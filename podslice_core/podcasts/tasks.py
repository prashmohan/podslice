import logging
import os
import time
from datetime import datetime
import uuid
import json
import io
import threading
import re
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import subprocess

import feedparser
import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
import google.generativeai as genai
from pydub import AudioSegment

from .models import Episode, Podcast, RehostedMedia

# Define constants
logger = logging.getLogger(__name__)
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
GEMINI_MODEL = "gemini-2.5-flash"
DOWNLOAD_POOL = ThreadPoolExecutor(max_workers=1)


# =============================================================================
# Episode Status and Media File Handling
# =============================================================================

def _update_episode_status(episode: Episode, status: Episode.Status, save: bool = True):
    """Helper to update and log episode status."""
    episode.status = status
    if save:
        episode.save(update_fields=['status'])
    logger.info(f"Episode '{episode.title}' ({episode.id}) status updated to {status.label}.")


def _create_rehosted_media_path(output_suffix: str = ".mp3") -> str:
    """Generates a unique file path for rehosted media."""
    media_dir = settings.MEDIA_ROOT
    os.makedirs(media_dir, exist_ok=True)
    final_audio_filename = f"{uuid.uuid4()}{output_suffix}"
    return os.path.join(media_dir, final_audio_filename)


def _save_processed_audio(episode: Episode, final_audio_path: str, file_size: int, output_mime_type: str = "audio/mpeg"):
    """Creates RehostedMedia entry and updates the episode."""
    logger.debug("Creating RehostedMedia entry in the database.")
    try:
        media_entry = RehostedMedia.objects.create(
            media_guid=uuid.uuid4(),
            file_path=final_audio_path,
            content_type=output_mime_type,
        )
        logger.info(f"Successfully created RehostedMedia record for '{episode.title}' (GUID: {media_entry.media_guid})")
    except Exception as e:
        logger.error(f"Failed to create RehostedMedia record for '{episode.title}': {e}", exc_info=True)
        os.remove(final_audio_path)
        raise
    relative_url = reverse('serve_media_episode', kwargs={'media_guid': media_entry.media_guid})
    episode.rehosted_audio_url = f"{settings.REHOST_BASE_URL}{relative_url}"
    episode.rehosted_audio_size = file_size
    episode.rehosted_media_id = media_entry.media_guid
    _update_episode_status(episode, Episode.Status.COMPLETE, save=False)
    episode.save(update_fields=['status', 'rehosted_audio_url', 'rehosted_audio_size', 'rehosted_media_id'])
    logger.info(f"Rehost completed successfully for Episode '{episode.title}' ({episode.id}).")


# =============================================================================
# Audio Downloading and Preparation
# =============================================================================

def _download_audio(episode: Episode) -> Optional[str]:
    """Downloads audio to a temporary file and returns the path."""
    _update_episode_status(episode, Episode.Status.DOWNLOADING)
    try:
        logger.info(f"Downloading audio for '{episode.title}' from {episode.original_audio_url}")
        response = requests.get(episode.original_audio_url, stream=True)
        response.raise_for_status()

        media_dir = settings.MEDIA_ROOT
        os.makedirs(media_dir, exist_ok=True)
        temp_file_path = os.path.join(media_dir, f"temp_{episode.id}.mp3")

        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.info(f"Audio for Episode '{episode.title}' downloaded to {temp_file_path}.")
        return temp_file_path
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download audio for Episode '{episode.title}': {e}", exc_info=True)
        _update_episode_status(episode, Episode.Status.FAILED)
        return None


def _get_audio_duration(audio_path: str, episode: Episode) -> Optional[float]:
    """Gets audio duration using ffprobe from a file."""
    try:
        ffprobe_cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', audio_path
        ]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        duration_str = result.stdout.strip()
        
        if duration_str == 'N/A' or not duration_str:
            logger.error(f"ffprobe could not determine duration for '{episode.title}'. Output: '{duration_str}'")
            _update_episode_status(episode, Episode.Status.FAILED)
            return None
            
        return float(duration_str)
    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for '{episode.title}'. Stderr: {e.stderr}")
        _update_episode_status(episode, Episode.Status.FAILED)
        return None
    except ValueError as e:
        logger.error(f"Could not convert ffprobe duration to float for '{episode.title}'. Output: '{result.stdout.strip()}'. Error: {e}")
        _update_episode_status(episode, Episode.Status.FAILED)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while getting audio duration for '{episode.title}': {e}", exc_info=True)
        _update_episode_status(episode, Episode.Status.FAILED)
        return None


def _fetch_and_prepare_audio(episode: Episode) -> Optional[Tuple[str, float]]:
    """Downloads audio to a file and gets its duration."""
    audio_path = _download_audio(episode)
    if not audio_path:
        return None

    _update_episode_status(episode, Episode.Status.ANALYZING)
    audio_duration_seconds = _get_audio_duration(audio_path, episode)
    if audio_duration_seconds is None:
        os.remove(audio_path)
        return None
    
    return audio_path, audio_duration_seconds


# =============================================================================
# Ad Segment Processing (Gemini)
# =============================================================================

def _get_ad_segments_from_gemini(audio_path: str, audio_duration_seconds: float) -> str:
    """Sends audio file to Gemini for ad detection and returns the raw response."""
    logger.debug("Sending audio to Gemini for ad detection.")
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        audio_file = genai.upload_file(path=audio_path)
        
        prompt = f"""
You are an audio editing assistant. Your sole task is to analyze a podcast audio file and identify all segments that are **not** the main content. The goal is to create a list of timestamps for segments that can be removed.
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
Example: [{{"start": 60.5, "end": 95.0}}]"""
        response = model.generate_content([prompt, audio_file])
        return response.text
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}", exc_info=True)
        return "[]"


def _parse_ad_segments(gemini_response: str, episode: Episode) -> List[Dict[str, float]]:
    """Parses the JSON response from Gemini to get ad segments."""
    logger.debug(f"Parsing Gemini response for Episode '{episode.title}' ({episode.id}): {gemini_response}")
    match = re.search(r'```json\n(\[.*?\])\n```|(\[.*?\])', gemini_response, re.DOTALL)
    if not match:
        logger.warning(f"No valid JSON array found in Gemini response for Episode '{episode.title}' ({episode.id}).")
        return []
    json_string = next(g for g in match.groups() if g is not None)
    try:
        ad_segments_list = json.loads(json_string)
        if not isinstance(ad_segments_list, list) or not all(
            isinstance(item, dict) and 'start' in item and 'end' in item for item in ad_segments_list
        ):
            raise json.JSONDecodeError("Invalid structure", json_string, 0)
        ad_segments_list.sort(key=lambda x: x['start'])
        return ad_segments_list
    except json.JSONDecodeError:
        logger.warning(f"Gemini API returned invalid JSON for ad segments for Episode '{episode.title}' ({episode.id}). Response: {json_string}")
        return []


def _analyze_audio_with_gemini(episode: Episode, audio_path: str, audio_duration_seconds: float) -> List[Dict[str, float]]:
    """Analyzes audio with Gemini to find ad segments."""
    gemini_response = _get_ad_segments_from_gemini(audio_path, audio_duration_seconds)
    episode.ad_segments = gemini_response
    _update_episode_status(episode, Episode.Status.PROCESSING, save=False)
    episode.save(update_fields=['status', 'ad_segments'])
    logger.info(f"Gemini analysis complete for '{episode.title}'. Ad segments: {gemini_response}")
    return _parse_ad_segments(gemini_response, episode)


# =============================================================================
# Audio Slicing and Saving (FFMPEG)
# =============================================================================

def _generate_ffmpeg_select_filter(ad_segments: List[Dict[str, float]], audio_duration_seconds: float) -> str:
    """Generates the ffmpeg select filter string to remove ad segments."""
    select_filter_parts = []
    last_segment_end = 0
    for segment in ad_segments:
        start_s = segment['start']
        end_s = segment['end']
        if start_s > last_segment_end:
            select_filter_parts.append(f"between(t,{last_segment_end},{start_s})")
        last_segment_end = max(last_segment_end, end_s)
    if last_segment_end < audio_duration_seconds:
        select_filter_parts.append(f"between(t,{last_segment_end},{audio_duration_seconds})")
    if not select_filter_parts:
        return ""
    return "select='" + "+".join(select_filter_parts) + "',asetpts=N/SR/TB"


def _run_ffmpeg_slicing(audio_path: str, select_filter: str, episode: Episode) -> Tuple[str, int]:
    """Runs ffmpeg to slice the audio and returns the output path and size."""
    final_audio_path = _create_rehosted_media_path()
    ffmpeg_cmd = [
        'ffmpeg', '-i', audio_path, '-vf', select_filter,
        '-c:a', 'libmp3lame', '-q:a', '2', final_audio_path
    ]
    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
        file_size = os.path.getsize(final_audio_path)
        return final_audio_path, file_size
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg failed for '{episode.title}'. Stderr: {e.stderr}")
        raise Exception(f"ffmpeg processing failed for episode {episode.id}")


def _save_original_audio_as_rehosted(audio_path: str, episode: Episode):
    """Saves the original audio content as the rehosted file."""
    logger.info(f"No ad segments to slice for '{episode.title}'. Saving original audio.")
    final_audio_path = _create_rehosted_media_path()
    os.rename(audio_path, final_audio_path)
    file_size = os.path.getsize(final_audio_path)
    _save_processed_audio(episode, final_audio_path, file_size)


def _save_empty_audio_as_rehosted(episode: Episode):
    """Saves an empty audio file as the rehosted file."""
    logger.warning(f"Ad segments covered the entire audio for '{episode.title}'. Result will be an empty file.")
    final_audio_path = _create_rehosted_media_path()
    AudioSegment.empty().export(final_audio_path, format="mp3")
    file_size = os.path.getsize(final_audio_path)
    _save_processed_audio(episode, final_audio_path, file_size)


def _slice_and_save_audio(audio_path: str, ad_segments: List[Dict[str, float]], episode: Episode, audio_duration_seconds: float):
    """Removes ad segments from the audio and saves the result."""
    if not ad_segments:
        _save_original_audio_as_rehosted(audio_path, episode)
        return

    logger.debug(f"Slicing audio for '{episode.title}' to remove {len(ad_segments)} ad segments.")
    select_filter = _generate_ffmpeg_select_filter(ad_segments, audio_duration_seconds)
    
    if not select_filter:
        _save_empty_audio_as_rehosted(episode)
    else:
        final_audio_path, file_size = _run_ffmpeg_slicing(audio_path, select_filter, episode)
        _save_processed_audio(episode, final_audio_path, file_size)


# =============================================================================
# Main Episode Rehosting Orchestration
# =============================================================================

polling_locks = {}

def rehost_episode_audio(episode_id: uuid.UUID):
    """Downloads, processes, and re-hosts an episode's audio."""
    audio_path = None
    try:
        episode = Episode.objects.select_related('podcast').get(id=episode_id)
        
        # Check if the episode is already being processed
        if episode.status in [Episode.Status.DOWNLOADING, Episode.Status.ANALYZING, Episode.Status.PROCESSING]:
            logger.warning(f"Skipping rehost task for '{episode.title}' because it is already in progress (status: {episode.get_status_display()}).")
            return
            
        logger.info(f"Starting rehost task for '{episode.title}' from podcast '{episode.podcast.title}'.")
    except Episode.DoesNotExist:
        logger.error(f"Episode with ID {episode_id} not found. Aborting rehost task.")
        return

    try:
        prepared_audio = _fetch_and_prepare_audio(episode)
        if not prepared_audio:
            return
        audio_path, audio_duration_seconds = prepared_audio

        ad_segments_list = _analyze_audio_with_gemini(episode, audio_path, audio_duration_seconds)
        
        _slice_and_save_audio(audio_path, ad_segments_list, episode, audio_duration_seconds)

    except Exception as e:
        logger.error(f"An unexpected error occurred during rehosting of '{episode.title}': {e}", exc_info=True)
        _update_episode_status(episode, Episode.Status.FAILED)
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
        logger.info(f"Finished rehost task for Episode '{episode.title}' ({episode.id}).")


# =============================================================================
# Podcast and Feed Management
# =============================================================================

def _get_episodes_to_process(podcast: Podcast) -> set:
    """Identifies episodes that need processing (NEW, FAILED, or STUCK)."""
    episodes_to_process = set(
        podcast.episodes.filter(status__in=[Episode.Status.NEW, Episode.Status.FAILED])
        .values_list('id', flat=True)
    )
    if episodes_to_process:
        failed_episodes_count = podcast.episodes.filter(
            id__in=episodes_to_process, status=Episode.Status.FAILED
        ).update(status=Episode.Status.NEW)
        if failed_episodes_count > 0:
            logger.info(f"Reset status to NEW for {failed_episodes_count} failed episodes.")
    stuck_threshold = timezone.now() - timezone.timedelta(hours=1)
    stuck_episodes = podcast.episodes.filter(
        status__in=[Episode.Status.DOWNLOADING, Episode.Status.ANALYZING, Episode.Status.PROCESSING],
        updated_at__lt=stuck_threshold
    )
    for episode in stuck_episodes:
        logger.warning(f"Episode '{episode.title}' is stuck. Resetting to NEW for reprocessing.")
        episode.status = Episode.Status.NEW
        episode.save(update_fields=['status'])
        episodes_to_process.add(episode.id)
    logger.info(f"Found {len(episodes_to_process)} existing episodes to process for '{podcast.title}'.")
    return episodes_to_process


def _fetch_and_parse_feed(podcast: Podcast) -> Optional[feedparser.FeedParserDict]:
    """Fetches and parses the RSS feed for a podcast."""
    try:
        logger.debug(f"Fetching RSS feed from {podcast.rss_url}")
        feed = feedparser.parse(podcast.rss_url, agent=BROWSER_USER_AGENT)
        if feed.bozo:
            logger.error(f"Malformed feed for '{podcast.title}'. Reason: {feed.get('bozo_exception', 'Unknown')}")
            return None
        return feed
    except Exception as e:
        logger.error(f"Failed to fetch or parse feed for '{podcast.title}': {e}", exc_info=True)
        return None


def _create_or_update_episode_from_entry(entry: feedparser.FeedParserDict, podcast: Podcast) -> Optional[Tuple[Episode, bool]]:
    """Creates or updates an episode from a feed entry."""
    guid = entry.get("id")
    if not guid:
        logger.warning(f"Skipping entry in '{podcast.title}' due to missing GUID. Title: '{entry.get('title', 'N/A')}'")
        return None

    audio_url = next((enclosure.get("href") for enclosure in entry.get("enclosures", []) if enclosure.get("type", "").startswith("audio")), None)
    if not audio_url:
        logger.warning(f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) in '{podcast.title}' due to missing audio enclosure.")
        return None

    pub_date_parsed = entry.get("published_parsed")
    if not pub_date_parsed:
        logger.warning(f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) in '{podcast.title}' due to missing publication date.")
        return None
    
    pub_date = timezone.make_aware(datetime.fromtimestamp(time.mktime(pub_date_parsed)), timezone.get_current_timezone())

    try:
        episode, created = Episode.objects.get_or_create(
            guid=guid,
            defaults={
                "podcast": podcast,
                "title": entry.get("title", "Untitled Episode"),
                "pub_date": pub_date,
                "original_audio_url": audio_url,
            },
        )
        return episode, created
    except Exception as e:
        logger.error(f"Failed to create episode with GUID {guid} for podcast '{podcast.title}'. Error: {e}", exc_info=True)
        return None


def _process_feed_entries(feed: feedparser.FeedParserDict, podcast: Podcast, episodes_to_process: set):
    """Processes feed entries and creates new episodes."""
    new_episodes_count = 0
    skipped_episodes_count = 0
    logger.info(f"Processing the latest {5} episodes from '{podcast.title}'.")
    for entry in feed.entries[:5]:
        result = _create_or_update_episode_from_entry(entry, podcast)
        if result:
            episode, created = result
            if created:
                new_episodes_count += 1
                logger.info(f"New episode created: '{episode.title}' for podcast '{podcast.title}'.")
                episodes_to_process.add(episode.id)
            else:
                skipped_episodes_count += 1
    logger.info(f"Polling complete for '{podcast.title}'. Found {new_episodes_count} new episodes. Skipped {skipped_episodes_count} existing episodes.")


def _delete_episode_media(episode: Episode):
    """Deletes the physical media file and the RehostedMedia entry for an episode."""
    if episode.rehosted_media_id:
        try:
            media_item = RehostedMedia.objects.get(pk=episode.rehosted_media_id)
            if os.path.exists(media_item.file_path):
                os.remove(media_item.file_path)
                logger.debug(f"Deleted physical file for '{episode.title}': {media_item.file_path}")
            media_item.delete()
            logger.debug(f"Deleted RehostedMedia entry for '{episode.title}' (GUID: {media_item.media_guid})")
        except RehostedMedia.DoesNotExist:
            logger.warning(f"RehostedMedia record not found for '{episode.title}' (media ID: {episode.rehosted_media_id})")
        except Exception as e:
            logger.error(f"Error deleting media for episode '{episode.title}': {e}", exc_info=True)


def _enforce_episode_limit(podcast: Podcast, limit: int = 5):
    """Deletes the oldest episodes if the total number of episodes exceeds the limit."""
    all_episodes = podcast.episodes.all().order_by('-pub_date')
    if all_episodes.count() > limit:
        episodes_to_delete = all_episodes[limit:]
        logger.info(f"Enforcing episode limit. Deleting {len(episodes_to_delete)} old episodes for '{podcast.title}'.")
        for episode in episodes_to_delete:
            _delete_episode_media(episode)
            episode.delete()
            logger.info(f"Deleted old episode: '{episode.title}'")


# =============================================================================
# Main Podcast Polling Orchestration
# =============================================================================

def _get_podcast_for_polling(podcast_id: uuid.UUID) -> Optional[Podcast]:
    """Fetches a podcast for polling and updates its last_polled time."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Polling feed for podcast: '{podcast.title}'")
        podcast.last_polled = timezone.now()
        podcast.save(update_fields=['last_polled'])
        return podcast
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting task.")
        return None


def _dispatch_rehosting_tasks(episode_ids: set):
    """Submits rehosting tasks to the download pool for a set of episode IDs."""
    for episode_id in episode_ids:
        logger.info(f"Dispatching re-hosting task for episode ID '{episode_id}'.")
        DOWNLOAD_POOL.submit(rehost_episode_audio, episode_id)


def poll_feed(podcast_id: uuid.UUID):
    """Fetches and parses a podcast's RSS feed to find and save new episodes."""
    lock = polling_locks.get(podcast_id)
    if lock is None:
        lock = threading.Lock()
        polling_locks[podcast_id] = lock

    if not lock.acquire(blocking=False):
        logger.warning(f"Polling for podcast {podcast_id} is already in progress. Skipping.")
        return

    try:
        logger.info(f"Starting poll for Podcast ID: {podcast_id}")
        podcast = _get_podcast_for_polling(podcast_id)
        if not podcast:
            return

        episodes_to_process = _get_episodes_to_process(podcast)
        feed = _fetch_and_parse_feed(podcast)
        if not feed:
            return
        
        _process_feed_entries(feed, podcast, episodes_to_process)
        _dispatch_rehosting_tasks(episodes_to_process)
        _enforce_episode_limit(podcast)
    finally:
        lock.release()


# =============================================================================
# Podcast-level Actions
# =============================================================================

def reprocess_podcast(podcast_id: uuid.UUID):
    """Clears all downloaded/processed data for a podcast's episodes and triggers a re-processing."""
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Starting reprocessing task for Podcast '{podcast.title}' ({podcast_id}).")
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting reprocess task.")
        return

    for episode in podcast.episodes.all():
        logger.info(f"Resetting episode '{episode.title}' for reprocessing.")
        _delete_episode_media(episode)
        episode.status = Episode.Status.NEW
        episode.rehosted_media_id = None
        episode.rehosted_audio_url = None
        episode.rehosted_audio_size = 0
        episode.ad_segments = None
        episode.save()
        DOWNLOAD_POOL.submit(rehost_episode_audio, episode.id)
        logger.info(f"Dispatched re-hosting task for episode '{episode.title}'.")
    logger.info(f"Completed dispatching reprocessing tasks for all episodes of podcast '{podcast.title}'.")


def delete_podcast_data(podcast_id: uuid.UUID):
    """Deletes a podcast and all associated episodes, rehosted media entries, and audio files."""
    logger.info(f"Starting deletion task for Podcast ID: {podcast_id}")
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Found podcast '{podcast.title}' ({podcast.id}) for deletion.")
        for episode in podcast.episodes.all():
            _delete_episode_media(episode)
        podcast_title = podcast.title
        podcast.delete()
        logger.info(f"Successfully deleted podcast '{podcast_title}' ({podcast_id}) and all its associated data.")
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting deletion task.")
    except Exception as e:
        logger.error(f"An error occurred during podcast deletion for ID {podcast_id}: {e}", exc_info=True)
