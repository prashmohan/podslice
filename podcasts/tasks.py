"""
This module contains Celery tasks for the podcasts app.
"""

import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import google.generativeai as genai
import requests
from django.conf import settings
from django.utils import timezone

from podcasts.models import Episode, Podcast, RehostedMedia

logger = logging.getLogger(__name__)

# --- Constants ---
BROWSER_USER_AGENT = getattr(
    settings,
    "BROWSER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36",
)
GEMINI_MODEL = getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash")
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=settings.DOWNLOAD_WORKER_COUNT)
EPISODES_PER_FEED = getattr(settings, "EPISODES_PER_FEED", 5)

# A thread-safe dictionary to hold locks for each podcast being polled.
polling_locks = {}

GEMINI_PROMPT = """
You are an expert audio editor. Your task is to identify non-essential segments in the podcast episode "{episode_title}" from the podcast "{podcast_title}".

The total duration of the audio is {audio_duration_seconds} seconds.

Identify the precise start and end times for the following categories:
* **Ads**: Pre-roll, mid-roll, sponsor reads, or promotional content. This includes native, host-read ads that are woven directly into the narrative of the episode without a change in voiceover or musical cue. Pay close attention to spoken content where the hosts transition to describing a sponsor's product, history, or services, even if it is connected to or framed around the episode's main topic (e.g., host-read sponsor segments in podcasts like "Acquired"). Identify the exact segment where the sponsor is being promoted.
* **Intro/Outro**: Music, jingles, or repetitive legal/branding announcements. Only identify these if there is no active episode content being delivered (do not cut sections where the host is actively introducing the topic over background music).
* **Off-Topic**: Tangents or banter completely unrelated to "{episode_title}" or "{podcast_title}". Be conservative here; only identify self-contained tangents that do not transition back into the main topic.
* **CTAs**: Requests to subscribe, leave reviews, follow social media, or visit sponsor URLs.
* **Silence**: Continuous silence lasting 3 seconds or longer.

**Guidelines for Precision & Clean Cuts:**
1. **Identify Native Ads**: Scan the spoken content for transitions into brand promotions or sponsor stories, even if the same hosts continue speaking in the same tone without music cues. Look for brand names, product descriptions, discount codes, or promotional calls to action.
2. **Natural Pauses**: Align the start and end times with natural pauses in speech or silence. Do not cut in the middle of a word or sentence. Leave a 0.5-second buffer of silence on either end if possible.
3. **Merge Proximity**: If consecutive non-essential segments are separated by less than 3 seconds of content, merge them into a single continuous segment to prevent fragmented, choppy audio cutting.
4. **No Hallucinations**: If there are no ads, CTAs, or other non-essential segments in this audio, return an empty array `[]`.

**Output Requirements:**
1. Return ONLY a valid JSON array within a markdown code block (```json ... ```).
2. Do not include any introductory text, explanation, notes, or concluding text outside of the code block.
3. Each object must have: "start" (float), "end" (float), and "label" (string).
4. Timestamps are in seconds from the start of the file.

Example Output:
```json
[
  {{"start": 0.0, "end": 45.2, "label": "intro"}},
  {{"start": 605.1, "end": 690.5, "label": "ad"}},
  {{"start": 1200.0, "end": 1205.0, "label": "silence"}}
]
```
"""


class AdManager:
    """Handles the analysis of audio files to detect ad segments using the Gemini API."""

    def __init__(self, episode: Episode):
        """Initializes the AdManager."""
        self.episode = episode

    def _get_ad_segments_from_gemini(
        self, audio_path: str, audio_duration_seconds: float
    ) -> str:
        """Sends audio to the Gemini API for ad detection with automatic fallback."""
        models_to_try = [
            getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash"),
            getattr(settings, "FALLBACK_GEMINI_MODEL", "gemini-3.5-flash-lite"),
        ]

        logger.debug("Attempting ad detection with Gemini.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        audio_file = None

        prompt = GEMINI_PROMPT.format(
            episode_title=self.episode.title,
            podcast_title=self.episode.podcast.title,
            audio_duration_seconds=audio_duration_seconds,
        )

        for model_name in models_to_try:
            try:
                logger.info("Attempting ad detection with model: %s", model_name)
                model = genai.GenerativeModel(model_name)
                if audio_file is None:
                    audio_file = genai.upload_file(path=audio_path)

                response = model.generate_content([prompt, audio_file])
                return response.text
            except Exception as e:
                logger.warning(
                    "Gemini API call failed for model %s: %s", model_name, str(e)
                )
                if model_name == models_to_try[-1]:
                    logger.error("All Gemini models failed.")

        return "[]"

    @staticmethod
    def _normalize_timestamp(val: Any) -> Optional[float]:
        """Normalizes a timestamp value (float, int, MM:SS, HH:MM:SS) to seconds as float."""
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            sec = float(val)
            return (
                sec
                if sec >= 0 and not math.isnan(sec) and not math.isinf(sec)
                else None
            )
        if isinstance(val, str):
            val = val.strip()
            try:
                sec = float(val)
                return (
                    sec
                    if sec >= 0 and not math.isnan(sec) and not math.isinf(sec)
                    else None
                )
            except ValueError:
                pass
            parts = val.split(":")
            try:
                if len(parts) == 2:  # MM:SS or MM:SS.mmm
                    minutes = float(parts[0])
                    seconds = float(parts[1])
                    if (
                        minutes < 0
                        or seconds < 0
                        or math.isnan(minutes)
                        or math.isnan(seconds)
                        or math.isinf(minutes)
                        or math.isinf(seconds)
                    ):
                        return None
                    return minutes * 60.0 + seconds
                if len(parts) == 3:  # HH:MM:SS or HH:MM:SS.mmm
                    hours = float(parts[0])
                    minutes = float(parts[1])
                    seconds = float(parts[2])
                    if (
                        hours < 0
                        or minutes < 0
                        or seconds < 0
                        or math.isnan(hours)
                        or math.isnan(minutes)
                        or math.isnan(seconds)
                        or math.isinf(hours)
                        or math.isinf(minutes)
                        or math.isinf(seconds)
                    ):
                        return None
                    return hours * 3600.0 + minutes * 60.0 + seconds
            except (ValueError, TypeError):
                return None
        return None

    def _parse_ad_segments(self, gemini_response: str) -> List[Dict[str, float]]:
        """Parses the JSON response from Gemini to extract ad segments."""
        logger.debug(
            "Parsing Gemini response for Episode '%s' (%s): %s",
            self.episode.title,
            self.episode.id,
            gemini_response,
        )
        if not gemini_response or not isinstance(gemini_response, str):
            logger.warning(
                "Gemini API returned empty or non-string response for Episode '%s' (%s).",
                self.episode.title,
                self.episode.id,
            )
            self.episode.status = Episode.Status.FAILED
            Episode.objects.filter(id=self.episode.id).update(
                status=Episode.Status.FAILED
            )
            return []

        raw_list = None

        # 1. Direct JSON parse
        try:
            parsed = json.loads(gemini_response.strip())
            if isinstance(parsed, list):
                raw_list = parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # 2. Markdown code fences (```json [...] ``` or ``` [...] ```)
        if raw_list is None:
            match = re.search(
                r"```(?:json)?\s*(\[.*?\])\s*```",
                gemini_response,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                try:
                    parsed = json.loads(match.group(1).strip())
                    if isinstance(parsed, list):
                        raw_list = parsed
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        # 3. Fallback search for outermost [...]
        if raw_list is None:
            match = re.search(r"(\[.*\])", gemini_response, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1).strip())
                    if isinstance(parsed, list):
                        raw_list = parsed
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        if raw_list is None or not all(
            isinstance(item, dict) and "start" in item and "end" in item
            for item in raw_list
        ):
            logger.warning(
                "Gemini API returned invalid JSON for ad segments for Episode '%s' (%s). Response: %s",
                self.episode.title,
                self.episode.id,
                gemini_response,
            )
            self.episode.status = Episode.Status.FAILED
            Episode.objects.filter(id=self.episode.id).update(
                status=Episode.Status.FAILED
            )
            return []

        valid_segments = []
        for item in raw_list:
            start = self._normalize_timestamp(item.get("start"))
            end = self._normalize_timestamp(item.get("end"))
            if start is None or end is None:
                continue
            if end <= start:
                continue
            segment = dict(item)
            segment["start"] = start
            segment["end"] = end
            valid_segments.append(segment)

        valid_segments.sort(key=lambda x: x["start"])
        return valid_segments

    def analyze_audio(
        self, audio_path: str, audio_duration_seconds: float
    ) -> List[Dict[str, float]]:
        """Analyzes the audio, saves the raw response, and returns parsed segments."""
        gemini_response = self._get_ad_segments_from_gemini(
            audio_path, audio_duration_seconds
        )
        self.episode.ad_segments = gemini_response
        Episode.objects.filter(id=self.episode.id).update(ad_segments=gemini_response)
        logger.info(
            "Gemini analysis complete for '%s'. Ad segments: %s",
            self.episode.title,
            gemini_response,
        )
        return self._parse_ad_segments(gemini_response)


class EpisodeProcessor:
    """Handles the processing of a single episode, from download to re-hosting."""

    def __init__(self, episode: Episode):
        """Initializes the EpisodeProcessor."""
        self.episode = episode
        self.ad_manager = AdManager(episode)

    def _update_status(self, status: Episode.Status, save: bool = True):
        """Updates the status of the episode and logs the change."""
        self.episode.status = status
        if save:
            Episode.objects.filter(id=self.episode.id).update(status=status)
        logger.info(
            "Episode '%s' (%s) status updated to %s.",
            self.episode.title,
            self.episode.id,
            status.label,
        )

    def _create_rehosted_media_path(self, output_suffix: str = ".mp3") -> str:
        """Generates a unique file path for the re-hosted media."""
        media_dir = settings.MEDIA_ROOT
        os.makedirs(media_dir, exist_ok=True)
        final_audio_filename = f"{uuid.uuid4()}{output_suffix}"
        return os.path.join(media_dir, final_audio_filename)

    def _save_processed_audio(
        self,
        media_guid: uuid.UUID,
        final_audio_path: str,
        file_size: int,
        output_mime_type: str = "audio/mpeg",
    ):
        """Creates a RehostedMedia record and updates the episode with the new URL."""
        logger.debug("Creating RehostedMedia entry in the database.")
        try:
            media_entry = RehostedMedia.objects.create(
                media_guid=media_guid,
                file_path=final_audio_path,
                content_type=output_mime_type,
            )
            logger.info(
                "Successfully created RehostedMedia record for '%s' (GUID: %s)",
                self.episode.title,
                media_entry.media_guid,
            )
        except (ValueError, IOError) as _:
            logger.error(
                "Failed to create RehostedMedia record for '%s'",
                self.episode.title,
                exc_info=True,
            )
            os.remove(final_audio_path)
            raise

        duration_sec = self._get_audio_duration(final_audio_path)
        duration_seconds = (
            int(round(duration_sec)) if duration_sec is not None else None
        )

        self.episode.rehosted_audio_size = file_size
        self.episode.rehosted_media_id = media_entry.media_guid
        self.episode.duration_seconds = duration_seconds
        self._update_status(Episode.Status.COMPLETE, save=False)

        Episode.objects.filter(id=self.episode.id).update(
            status=Episode.Status.COMPLETE,
            rehosted_audio_size=file_size,
            rehosted_media_id=media_entry.media_guid,
            duration_seconds=duration_seconds,
        )
        logger.info(
            "Rehost completed successfully for Episode '%s' (%s).",
            self.episode.title,
            self.episode.id,
        )

    def _download_audio(self) -> Optional[str]:
        """Downloads the audio file for the episode."""
        self._update_status(Episode.Status.DOWNLOADING)
        try:
            logger.info(
                "Downloading audio for '%s' from %s",
                self.episode.title,
                self.episode.original_audio_url,
            )
            response = requests.get(
                self.episode.original_audio_url,
                stream=True,
                timeout=settings.DOWNLOAD_TIMEOUT_SEC,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                },
            )
            response.raise_for_status()

            media_dir = settings.MEDIA_ROOT
            os.makedirs(media_dir, exist_ok=True)
            temp_file_path = os.path.join(media_dir, f"temp_{self.episode.id}.mp3")

            with open(temp_file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(
                "Audio for Episode '%s' downloaded to %s.",
                self.episode.title,
                temp_file_path,
            )
            return temp_file_path
        except requests.exceptions.RequestException:
            logger.error(
                "Failed to download audio for Episode '%s'",
                self.episode.title,
                exc_info=True,
            )
            self._update_status(Episode.Status.FAILED)
            return None

    def _get_audio_duration(self, audio_path: str) -> Optional[float]:
        """Gets the duration of the audio file using ffprobe."""
        try:
            ffprobe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ]
            result = subprocess.run(
                ffprobe_cmd, capture_output=True, text=True, check=True, timeout=60
            )
            duration_str = result.stdout.strip()

            if duration_str == "N/A" or not duration_str:
                logger.error(
                    "ffprobe could not determine duration for '%s'. Output: '%s'",
                    self.episode.title,
                    duration_str,
                )
                self._update_status(Episode.Status.FAILED)
                return None

            return float(duration_str)
        except (subprocess.CalledProcessError, ValueError):
            logger.error(
                "ffprobe failed for '%s'.",
                self.episode.title,
                exc_info=True,
            )
            self._update_status(Episode.Status.FAILED)
            return None

    def _fetch_and_prepare_audio(self) -> Optional[Tuple[str, float]]:
        """Downloads and prepares the audio file for processing."""
        audio_path = self._download_audio()
        if not audio_path:
            return None
        self._update_status(Episode.Status.ANALYZING)
        audio_duration_seconds = self._get_audio_duration(audio_path)
        if audio_duration_seconds is None:
            logger.error(
                "Did not get audio length for '%s'.",
                self.episode.title,
            )
            os.remove(audio_path)
            return None

        return audio_path, audio_duration_seconds

    def _generate_ffmpeg_filter_complex(
        self, ad_segments: List[Dict[str, float]], audio_duration_seconds: float
    ) -> str:
        """Generates the ffmpeg filter_complex string to remove ad segments."""
        content_segments = []
        last_segment_end = 0
        for segment in ad_segments:
            start_s = segment["start"]
            end_s = segment["end"]
            if start_s > last_segment_end:
                content_segments.append({"start": last_segment_end, "end": start_s})
            last_segment_end = max(last_segment_end, end_s)
        if last_segment_end < audio_duration_seconds:
            content_segments.append(
                {"start": last_segment_end, "end": audio_duration_seconds}
            )

        if not content_segments:
            return ""

        filter_parts = []
        stream_labels = []
        for i, segment in enumerate(content_segments):
            start = segment["start"]
            end = segment["end"]
            label = f"[a{i}]"
            filter_parts.append(f"[0:a]atrim={start}:{end},asetpts=PTS-STARTPTS{label}")
            stream_labels.append(label)

        concat_streams = "".join(stream_labels)
        concat_filter = f"{concat_streams}concat=n={len(stream_labels)}:v=0:a=1[out]"

        return "; ".join(filter_parts) + "; " + concat_filter

    def _run_ffmpeg_slicing(
        self, audio_path: str, filter_complex: str, final_audio_path: str
    ) -> int:
        """Runs the ffmpeg command to slice the audio."""
        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            audio_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "0",
            "-q:a",
            "4",
            "-write_xing",
            "1",
            final_audio_path,
        ]
        logger.info(
            "Executing ffmpeg command for '%s': %s",
            self.episode.title,
            " ".join(ffmpeg_cmd),
        )
        try:
            subprocess.run(
                ffmpeg_cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.AD_SPLICING_TIMEOUT_SEC,
            )
            file_size = os.path.getsize(final_audio_path)
            return file_size
        except subprocess.CalledProcessError as e:
            logger.error(
                "ffmpeg failed for '%s'. Stderr: %s", self.episode.title, e.stderr
            )
            raise ValueError(
                f"ffmpeg processing failed for episode {self.episode.id}"
            ) from e

    def _save_original_audio_as_rehosted(self, audio_path: str):
        """Saves the original audio as re-hosted if no ads are found."""
        logger.info(
            "No ad segments to slice for '%s'. Saving original audio.",
            self.episode.title,
        )
        media_guid = self.episode.rehosted_media_id or uuid.uuid4()
        media_dir = settings.MEDIA_ROOT
        final_audio_path = os.path.join(media_dir, f"{media_guid}.mp3")
        os.rename(audio_path, final_audio_path)
        file_size = os.path.getsize(final_audio_path)
        self._save_processed_audio(media_guid, final_audio_path, file_size)

    def _save_empty_audio_as_rehosted(self):
        """Saves an empty audio file if ads cover the entire duration."""
        logger.warning(
            "Ad segments covered the entire audio for '%s'. Result will be an empty file.",
            self.episode.title,
        )
        media_guid = self.episode.rehosted_media_id or uuid.uuid4()
        media_dir = settings.MEDIA_ROOT
        final_audio_path = os.path.join(media_dir, f"{media_guid}.mp3")
        ffmpeg_cmd = [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "1",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            final_audio_path,
        ]
        try:
            subprocess.run(
                ffmpeg_cmd, check=True, capture_output=True, text=True, timeout=60
            )
            file_size = os.path.getsize(final_audio_path)
            self._save_processed_audio(media_guid, final_audio_path, file_size)
        except (subprocess.CalledProcessError, ValueError):
            logger.error(
                "ffmpeg failed for '%s'.",
                self.episode.title,
                exc_info=True,
            )
            self._update_status(Episode.Status.FAILED)

    def _slice_and_save_audio(
        self,
        audio_path: str,
        ad_segments: List[Dict[str, float]],
        audio_duration_seconds: float,
    ):
        """Slices the audio based on ad segments and saves the result."""
        if not ad_segments:
            self._save_original_audio_as_rehosted(audio_path)
            return

        logger.debug(
            "Slicing audio for '%s' to remove %d ad segments.",
            self.episode.title,
            len(ad_segments),
        )
        filter_complex = self._generate_ffmpeg_filter_complex(
            ad_segments, audio_duration_seconds
        )

        if not filter_complex:
            logger.info(
                "Did not get any ad segment slices for '%s'", self.episode.title
            )
            self._save_empty_audio_as_rehosted()
        else:
            logger.info(
                "Using the following ad segment slices for '%s': %s",
                self.episode.title,
                filter_complex,
            )
            media_guid = self.episode.rehosted_media_id or uuid.uuid4()
            media_dir = settings.MEDIA_ROOT
            final_audio_path = os.path.join(media_dir, f"{media_guid}.mp3")
            file_size = self._run_ffmpeg_slicing(
                audio_path, filter_complex, final_audio_path
            )
            self._save_processed_audio(media_guid, final_audio_path, file_size)

    def rehost_audio(self, force=False):
        """Main method to process and re-host the audio for an episode."""
        audio_path = None
        try:
            if not force and self.episode.status in [
                Episode.Status.DOWNLOADING,
                Episode.Status.ANALYZING,
                Episode.Status.PROCESSING,
            ]:
                logger.warning(
                    "Skipping rehost task for '%s' because it is already in progress (status: %s).",
                    self.episode.title,
                    self.episode.get_status_display(),
                )
                return

            logger.info(
                "Starting rehost task for '%s' from podcast '%s'.",
                self.episode.title,
                self.episode.podcast.title,
            )

            prepared_audio = self._fetch_and_prepare_audio()
            if not prepared_audio:
                return
            audio_path, audio_duration_seconds = prepared_audio

            self._update_status(Episode.Status.PROCESSING)
            if self.episode.disable_ai_processing:
                logger.info(
                    "AI processing disabled for '%s'. Skipping analysis.",
                    self.episode.title,
                )
                ad_segments_list = []
            else:
                ad_segments_list = self.ad_manager.analyze_audio(
                    audio_path, audio_duration_seconds
                )
            logger.info(
                "Finished Ad analysis audio for '%s' from podcast '%s'.",
                self.episode.title,
                self.episode.podcast.title,
            )

            self._slice_and_save_audio(
                audio_path, ad_segments_list, audio_duration_seconds
            )
            logger.info(
                "Finished slicing audio for '%s' from podcast '%s'.",
                self.episode.title,
                self.episode.podcast.title,
            )

        except Exception:
            logger.error(
                "An unexpected error occurred during rehosting of '%s'",
                self.episode.title,
                exc_info=True,
            )
            self._update_status(Episode.Status.FAILED)
        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
            logger.info(
                "Finished rehost task for Episode '%s' (%s).",
                self.episode.title,
                self.episode.id,
            )


class FeedManager:
    """Manages the polling of a podcast feed and the processing of its episodes."""

    def __init__(self, podcast: Podcast):
        """Initializes the FeedManager."""
        self.podcast = podcast

    def _get_episodes_to_process(self) -> set:
        """
        Identifies episodes that need to be processed.
        This includes new, failed, and stuck episodes.
        """
        episodes_to_process = set(
            self.podcast.episodes.filter(
                status__in=[Episode.Status.NEW, Episode.Status.FAILED]
            ).values_list("id", flat=True)
        )
        if episodes_to_process:
            failed_episodes_count = self.podcast.episodes.filter(
                id__in=episodes_to_process, status=Episode.Status.FAILED
            ).update(status=Episode.Status.NEW)
            if failed_episodes_count > 0:
                logger.info(
                    "Reset status to NEW for %d failed episodes.",
                    failed_episodes_count,
                )

        stuck_threshold = timezone.now() - timezone.timedelta(hours=1)
        stuck_episodes = self.podcast.episodes.filter(
            status__in=[
                Episode.Status.DOWNLOADING,
                Episode.Status.ANALYZING,
                Episode.Status.PROCESSING,
            ],
            updated_at__lt=stuck_threshold,
        )
        for episode in stuck_episodes:
            logger.warning(
                "Episode '%s' is stuck. Resetting to NEW for reprocessing.",
                episode.title,
            )
            episode.status = Episode.Status.NEW
            episode.save(update_fields=["status"])
            episodes_to_process.add(episode.id)

        logger.info(
            "Found %d existing episodes to process for '%s'.",
            len(episodes_to_process),
            self.podcast.title,
        )
        return episodes_to_process

    def _fetch_and_parse_feed(self) -> Optional[feedparser.FeedParserDict]:
        """Fetches and parses the RSS feed for the podcast."""
        try:
            logger.debug("Fetching RSS feed from %s", self.podcast.rss_url)
            feed = feedparser.parse(self.podcast.rss_url, agent=BROWSER_USER_AGENT)
            if feed.bozo:
                logger.error(
                    "Malformed feed for '%s'. Reason: %s",
                    self.podcast.title,
                    feed.get("bozo_exception", "Unknown"),
                )
                return None
            return feed
        except (ValueError, IOError) as _:
            logger.error(
                "Failed to fetch or parse feed for '%s'",
                self.podcast.title,
                exc_info=True,
            )
            return None

    def _create_or_update_episode_from_entry(
        self, entry: feedparser.FeedParserDict
    ) -> Optional[Tuple[Episode, bool]]:
        """Creates or updates an episode from a feed entry."""
        guid = entry.get("id")
        if not guid:
            logger.warning(
                "Skipping entry in '%s' due to missing GUID. Title: '%s'",
                self.podcast.title,
                entry.get("title", "N/A"),
            )
            return None

        audio_url = next(
            (
                enclosure.get("href")
                for enclosure in entry.get("enclosures", [])
                if enclosure.get("type", "").startswith("audio")
            ),
            None,
        )
        if not audio_url:
            logger.warning(
                "Skipping entry '%s' (GUID: %s) in '%s' due to missing audio enclosure.",
                entry.get("title", "N/A"),
                guid,
                self.podcast.title,
            )
            return None

        pub_date_parsed = entry.get("published_parsed")
        if not pub_date_parsed:
            logger.warning(
                "Skipping entry '%s' (GUID: %s) in '%s' due to missing publication date.",
                entry.get("title", "N/A"),
                guid,
                self.podcast.title,
            )
            return None

        pub_date = timezone.make_aware(
            datetime.fromtimestamp(time.mktime(pub_date_parsed)),
            timezone.get_current_timezone(),
        )

        try:
            episode, created = Episode.objects.get_or_create(
                guid=guid,
                defaults={
                    "podcast": self.podcast,
                    "title": entry.get("title", "Untitled Episode"),
                    "pub_date": pub_date,
                    "original_audio_url": audio_url,
                },
            )
            return episode, created
        except (ValueError, IOError) as _:
            logger.error(
                "Failed to create episode with GUID %s for podcast '%s'.",
                guid,
                self.podcast.title,
                exc_info=True,
            )
            return None

    def _process_feed_entries(
        self,
        feed: feedparser.FeedParserDict,
        episodes_to_process: set,
        download_all: bool = False,
    ):
        """Processes the entries in the feed, creating new episodes as needed."""
        new_episodes_count = 0
        skipped_episodes_count = 0
        limit = None if download_all else EPISODES_PER_FEED
        logger.info(
            "Processing the latest %s episodes from '%s'.",
            "all" if limit is None else str(limit),
            self.podcast.title,
        )
        entries = feed.entries if limit is None else feed.entries[:limit]
        for entry in entries:
            result = self._create_or_update_episode_from_entry(entry)
            if result:
                episode, created = result
                if created:
                    new_episodes_count += 1
                    logger.info(
                        "New episode created: '%s' for podcast '%s'.",
                        episode.title,
                        self.podcast.title,
                    )
                    episodes_to_process.add(episode.id)
                else:
                    skipped_episodes_count += 1
        logger.info(
            "Polling complete for '%s'. Found %d new episodes. Skipped %d existing episodes.",
            self.podcast.title,
            new_episodes_count,
            skipped_episodes_count,
        )

    def _delete_episode_media(self, episode: Episode):
        """Deletes the media associated with an episode."""
        if episode.rehosted_media_id:
            try:
                media_item = RehostedMedia.objects.get(pk=episode.rehosted_media_id)
                if os.path.exists(media_item.file_path):
                    os.remove(media_item.file_path)
                    logger.debug(
                        "Deleted physical file for '%s': %s",
                        episode.title,
                        media_item.file_path,
                    )
                media_item.delete()
                logger.debug(
                    "Deleted RehostedMedia entry for '%s' (GUID: %s)",
                    episode.title,
                    media_item.media_guid,
                )
            except RehostedMedia.DoesNotExist:
                logger.warning(
                    "RehostedMedia record not found for '%s' (media ID: %s)",
                    episode.title,
                    episode.rehosted_media_id,
                )
            except (ValueError, IOError) as _:
                logger.error(
                    "Error deleting media for episode '%s'",
                    episode.title,
                    exc_info=True,
                )

    def _enforce_episode_limit(self):
        """Enforces the episode limit for the podcast."""
        if self.podcast.max_episodes == 0:
            logger.info(
                "Episode retention is unlimited for '%s'. Skipping deletion.",
                self.podcast.title,
            )
            return

        all_episodes = self.podcast.episodes.all().order_by("-pub_date")
        if all_episodes.count() > self.podcast.max_episodes:
            episodes_to_delete = all_episodes[self.podcast.max_episodes :]
            logger.info(
                "Enforcing episode limit. Deleting %d old episodes for '%s'.",
                len(episodes_to_delete),
                self.podcast.title,
            )
            for episode in episodes_to_delete:
                self._delete_episode_media(episode)
                episode.delete()
                logger.info("Deleted old episode: '%s'", episode.title)

    def _dispatch_rehosting_tasks(self, episode_ids: set):
        """
        Dispatches re-hosting tasks for the given episode IDs.

        NOTE: This method is intentionally NOT called at the end of the poll() loop.
        Instead of processing new episodes as soon as they are detected, they are processed
        strictly on-demand when the client plays them. This is a design choice to conserve
        Gemini AI tokens and credits, as the user may not listen to every episode that is
        published to the feed.
        """
        for episode_id in episode_ids:
            logger.info("Dispatching re-hosting task for episode ID '%s'.", episode_id)
            DOWNLOAD_POOL.submit(rehost_episode_audio, episode_id)

    def poll(self, download_all: bool = False):
        """
        Main method to poll the podcast feed.

        This method is locked to prevent multiple polling tasks for the same podcast
        from running at the same time.
        """
        lock = polling_locks.get(self.podcast.id)
        if lock is None:
            lock = threading.Lock()
            polling_locks[self.podcast.id] = lock

        if not lock.acquire(blocking=False):
            logger.warning(
                "Polling for podcast %s is already in progress. Skipping.",
                self.podcast.id,
            )
            return

        try:
            logger.info("Starting poll for Podcast ID: %s", self.podcast.id)
            self.podcast.last_polled = timezone.now()
            self.podcast.save(update_fields=["last_polled"])

            episodes_to_process = self._get_episodes_to_process()
            feed = self._fetch_and_parse_feed()
            if not feed:
                return

            self._process_feed_entries(
                feed, episodes_to_process, download_all=download_all
            )
            self._enforce_episode_limit()
        finally:
            lock.release()

    def reprocess_podcast(self):
        """Reprocesses all episodes for the podcast."""
        logger.info(
            "Starting reprocessing task for Podcast '%s' (%s).",
            self.podcast.title,
            self.podcast.id,
        )
        for episode in self.podcast.episodes.all():
            logger.info("Resetting episode '%s' for reprocessing.", episode.title)
            self._delete_episode_media(episode)
            episode.status = Episode.Status.NEW
            episode.rehosted_audio_size = 0
            episode.ad_segments = None
            episode.save()
        logger.info(
            "Completed resetting all episodes of podcast '%s' for reprocessing on-demand.",
            self.podcast.title,
        )

    def delete_podcast_data(self):
        """Deletes all data associated with the podcast."""
        logger.info("Starting deletion task for Podcast ID: %s", self.podcast.id)
        for episode in self.podcast.episodes.all():
            self._delete_episode_media(episode)
        podcast_title = self.podcast.title
        self.podcast.delete()
        logger.info(
            "Successfully deleted podcast '%s' (%s) and all its associated data.",
            podcast_title,
            self.podcast.id,
        )


def rehost_episode_audio(episode_id: int, force: bool = False):
    """Task to re-host the audio for a single episode."""
    try:
        episode = Episode.objects.select_related("podcast").get(id=episode_id)
        processor = EpisodeProcessor(episode)
        processor.rehost_audio(force=force)
    except Episode.DoesNotExist:
        logger.error(
            "Episode with ID %s not found. Aborting rehost task.",
            episode_id,
        )


def poll_feed(podcast_id: uuid.UUID, download_all: bool = False):
    """Task to poll a podcast feed."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        manager = FeedManager(podcast)
        manager.poll(download_all=download_all)
    except Podcast.DoesNotExist:
        logger.error("Podcast with ID %s not found. Aborting task.", podcast_id)


def reprocess_podcast(podcast_id: uuid.UUID):
    """Task to reprocess all episodes for a podcast."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        manager = FeedManager(podcast)
        manager.reprocess_podcast()
    except Podcast.DoesNotExist:
        logger.error(
            "Podcast with ID %s not found. Aborting reprocess task.", podcast_id
        )


def delete_podcast_data(podcast_id: uuid.UUID):
    """Task to delete all data for a podcast."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        manager = FeedManager(podcast)
        manager.delete_podcast_data()
    except Podcast.DoesNotExist:
        logger.error(
            "Podcast with ID %s not found. Aborting deletion task.", podcast_id
        )
    except (ValueError, IOError) as _:
        logger.error(
            "An error occurred during podcast deletion for ID %s",
            podcast_id,
            exc_info=True,
        )
