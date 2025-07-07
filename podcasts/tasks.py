"""
This module contains Celery tasks for the podcasts app.
"""
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import feedparser
import google.generativeai as genai
import requests
from django.conf import settings
from django.urls import reverse
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
GEMINI_MODEL = getattr(settings, "GEMINI_MODEL", "gemini-2.5-pro")
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=settings.DOWNLOAD_WORKER_COUNT)
EPISODES_PER_FEED = getattr(settings, "EPISODES_PER_FEED", 5)
EPISODE_LIMIT = getattr(settings, "EPISODE_LIMIT", 5)

# A thread-safe dictionary to hold locks for each podcast being polled.
polling_locks = {}

GEMINI_PROMPT = """
You are an audio editing assistant. Your sole task is to analyze a podcast audio file 
and identify all segments that are **not** the main content. The goal is to create a list 
of timestamps for segments that can be removed.
Identify the precise start and end times for any of the following non-essential audio segments:
* **Advertisements** (pre-produced ads, host-read sponsor messages)
* **Introduction Music/Jingles**
* **Outro Music/Jingles**
* **Host Banter** that is clearly separate from the main topic (e.g., initial greetings, off-topic chat before the core discussion begins).
* **Calls to Action** (e.g., "subscribe," "follow us on social media," "visit our website").
* **Extended periods of silence** (longer than 3 seconds).
**Instructions:**
1.  Focus exclusively on identifying the segments listed above.
2.  Do **not** identify or timestamp the main content of the podcast.
3.  Return a JSON array of objects, where each object has 'start' and 'end' keys representing the start and end times of the ad segment in seconds.
4.  If no ads are found, return an empty array.
5.  Only generate the JSON array and nothing other than the array.
6.  Audio duration is {audio_duration_seconds} seconds.
Example: [{{"start": 60.5, "end": 95.0}}]
"""


class AdManager:
    """Handles the analysis of audio files to detect ad segments using the Gemini API."""

    def __init__(self, episode: Episode):
        """Initializes the AdManager."""
        self.episode = episode

    def _get_ad_segments_from_gemini(
        self, audio_path: str, audio_duration_seconds: float
    ) -> str:
        """Sends audio to the Gemini API for ad detection."""
        logger.debug("Sending audio to Gemini for ad detection.")
        try:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)
            audio_file = genai.upload_file(path=audio_path)
            prompt = GEMINI_PROMPT.format(audio_duration_seconds=audio_duration_seconds)
            response = model.generate_content([prompt, audio_file])
            return response.text
        except Exception:
            logger.error("Gemini API call failed", exc_info=True)
            return "[]"

    def _parse_ad_segments(self, gemini_response: str) -> List[Dict[str, float]]:
        """Parses the JSON response from Gemini to extract ad segments."""
        logger.debug(
            "Parsing Gemini response for Episode '%s' (%s): %s",
            self.episode.title,
            self.episode.id,
            gemini_response,
        )
        match = re.search(
            r"```json\n(.*?)?\n```|(.*)",
            gemini_response, re.DOTALL
        )
        if not match:
            logger.warning(
                "No valid JSON array found in Gemini response for Episode '%s' (%s).",
                self.episode.title,
                self.episode.id,
            )
            return []

        json_string = next(g for g in match.groups() if g is not None)
        try:
            ad_segments_list = json.loads(json_string)
            if not isinstance(ad_segments_list, list) or not all(
                isinstance(item, dict) and "start" in item and "end" in item
                for item in ad_segments_list
            ):
                raise json.JSONDecodeError("Invalid structure", json_string, 0)
            ad_segments_list.sort(key=lambda x: x["start"])
            return ad_segments_list
        except json.JSONDecodeError:
            logger.warning(
                "Gemini API returned invalid JSON for ad segments for Episode '%s' (%s). Response: %s",
                self.episode.title,
                self.episode.id,
                json_string,
            )
            self.episode.status = Episode.Status.FAILED
            self.episode.save(update_fields=["status"])
            return []

    def analyze_audio(
        self, audio_path: str, audio_duration_seconds: float
    ) -> List[Dict[str, float]]:
        """Analyzes the audio, saves the raw response, and returns parsed segments."""
        gemini_response = self._get_ad_segments_from_gemini(
            audio_path, audio_duration_seconds
        )
        self.episode.ad_segments = gemini_response
        self.episode.save(update_fields=["ad_segments"])
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
            self.episode.save(update_fields=["status"])
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
        self, final_audio_path: str, file_size: int, output_mime_type: str = "audio/mpeg"
    ):
        """Creates a RehostedMedia record and updates the episode with the new URL."""
        logger.debug("Creating RehostedMedia entry in the database.")
        try:
            media_entry = RehostedMedia.objects.create(
                media_guid=uuid.uuid4(),
                file_path=final_audio_path,
                content_type=output_mime_type,
            )
            logger.info(
                "Successfully created RehostedMedia record for '%s' (GUID: %s)",
                self.episode.title,
                media_entry.media_guid,
            )
        except Exception as e:
            logger.error(
                "Failed to create RehostedMedia record for '%s'",
                self.episode.title,
                exc_info=True,
            )
            os.remove(final_audio_path)
            raise e

        relative_url = reverse(
            "serve_media_episode", kwargs={"media_guid": media_entry.media_guid}
        )
        self.episode.rehosted_audio_url = f"{settings.REHOST_BASE_URL}{relative_url}"
        self.episode.rehosted_audio_size = file_size
        self.episode.rehosted_media_id = media_entry.media_guid
        self._update_status(Episode.Status.COMPLETE, save=False)
        self.episode.save(
            update_fields=[
                "status",
                "rehosted_audio_url",
                "rehosted_audio_size",
                "rehosted_media_id",
            ]
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
                self.episode.original_audio_url, stream=True, 
                timeout=settings.DOWNLOAD_TIMEOUT_SEC
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
        self, audio_path: str, filter_complex: str
    ) -> Tuple[str, int]:
        """Runs the ffmpeg command to slice the audio."""
        final_audio_path = self._create_rehosted_media_path()
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
                ffmpeg_cmd, check=True, capture_output=True, text=True, 
                timeout=settings.AD_SPLICING_TIMEOUT_SEC
            )
            file_size = os.path.getsize(final_audio_path)
            return final_audio_path, file_size
        except subprocess.CalledProcessError as e:
            logger.error(
                "ffmpeg failed for '%s'. Stderr: %s", self.episode.title, e.stderr
            )
            raise Exception(
                f"ffmpeg processing failed for episode {self.episode.id}"
            ) from e

    def _save_original_audio_as_rehosted(self, audio_path: str):
        """Saves the original audio as re-hosted if no ads are found."""
        logger.info(
            "No ad segments to slice for '%s'. Saving original audio.",
            self.episode.title,
        )
        final_audio_path = self._create_rehosted_media_path()
        os.rename(audio_path, final_audio_path)
        file_size = os.path.getsize(final_audio_path)
        self._save_processed_audio(final_audio_path, file_size)

    def _save_empty_audio_as_rehosted(self):
        """Saves an empty audio file if ads cover the entire duration."""
        logger.warning(
            "Ad segments covered the entire audio for '%s'. Result will be an empty file.",
            self.episode.title,
        )
        final_audio_path = self._create_rehosted_media_path()
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
            self._save_processed_audio(final_audio_path, file_size)
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
            final_audio_path, file_size = self._run_ffmpeg_slicing(
                audio_path, filter_complex
            )
            self._save_processed_audio(final_audio_path, file_size)

    def rehost_audio(self):
        """Main method to process and re-host the audio for an episode."""
        audio_path = None
        try:
            if self.episode.status in [
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
            ad_segments_list = self.ad_manager.analyze_audio(
                audio_path, audio_duration_seconds
            )

            self._slice_and_save_audio(
                audio_path, ad_segments_list, audio_duration_seconds
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
        except Exception:
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
        except Exception:
            logger.error(
                "Failed to create episode with GUID %s for podcast '%s'.",
                guid,
                self.podcast.title,
                exc_info=True,
            )
            return None

    def _process_feed_entries(
        self, feed: feedparser.FeedParserDict, episodes_to_process: set
    ):
        """Processes the entries in the feed, creating new episodes as needed."""
        new_episodes_count = 0
        skipped_episodes_count = 0
        logger.info(
            "Processing the latest %d episodes from '%s'.",
            EPISODES_PER_FEED,
            self.podcast.title,
        )
        for entry in feed.entries[:EPISODES_PER_FEED]:
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
            except Exception:
                logger.error(
                    "Error deleting media for episode '%s'",
                    episode.title,
                    exc_info=True,
                )

    def _enforce_episode_limit(self):
        """Enforces the episode limit for the podcast."""
        all_episodes = self.podcast.episodes.all().order_by("-pub_date")
        if all_episodes.count() > EPISODE_LIMIT:
            episodes_to_delete = all_episodes[EPISODE_LIMIT:]
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
        """Dispatches re-hosting tasks for the given episode IDs."""
        for episode_id in episode_ids:
            logger.info("Dispatching re-hosting task for episode ID '%s'.", episode_id)
            DOWNLOAD_POOL.submit(rehost_episode_audio, episode_id)

    def poll(self):
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

            self._process_feed_entries(feed, episodes_to_process)
            self._dispatch_rehosting_tasks(episodes_to_process)
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
            episode.rehosted_media_id = None
            episode.rehosted_audio_url = None
            episode.rehosted_audio_size = 0
            episode.ad_segments = None
            episode.save()
            DOWNLOAD_POOL.submit(rehost_episode_audio, episode.id)
            logger.info("Dispatched re-hosting task for episode '%s'.", episode.title)
        logger.info(
            "Completed dispatching reprocessing tasks for all episodes of podcast '%s'.",
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


def rehost_episode_audio(episode_id: int):
    """Task to re-host the audio for a single episode."""
    try:
        episode = Episode.objects.select_related("podcast").get(id=episode_id)
        processor = EpisodeProcessor(episode)
        processor.rehost_audio()
    except Episode.DoesNotExist:
        logger.error("Episode with ID %s not found. Aborting rehost task.", episode_id)


def poll_feed(podcast_id: uuid.UUID):
    """Task to poll a podcast feed."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        manager = FeedManager(podcast)
        manager.poll()
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
    except Exception:
        logger.error(
            "An error occurred during podcast deletion for ID %s",
            podcast_id,
            exc_info=True,
        )
