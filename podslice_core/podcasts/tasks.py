import logging
import os
import tempfile
import time
from datetime import datetime
import uuid
import json
import io
import threading
import re
from typing import List, Dict, Optional, Tuple

import feedparser
import requests
from django.conf import settings
from django.utils import timezone
import google.generativeai as genai
from pydub import AudioSegment

from django.urls import reverse
from .models import Episode, Podcast, RehostedMedia

# Define constants
logger = logging.getLogger(__name__)
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
GEMINI_MODEL = "gemini-2.5-flash"


def _update_episode_status(episode: Episode, status: Episode.Status, save: bool = True):
    """Helper to update and log episode status."""
    episode.status = status
    if save:
        episode.save(update_fields=['status'])
    logger.info(f"Episode {episode.id} status updated to {status.label}.")


def _download_audio(episode: Episode) -> Tuple[Optional[bytes], Optional[str]]:
    """Downloads audio content and returns it along with a temporary file path."""
    _update_episode_status(episode, Episode.Status.DOWNLOADING)
    try:
        logger.debug(f"Downloading audio from {episode.original_audio_url}")
        response = requests.get(episode.original_audio_url, stream=True)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio_file:
            audio_content = b""
            for chunk in response.iter_content(chunk_size=8192):
                temp_audio_file.write(chunk)
                audio_content += chunk
            temp_audio_path = temp_audio_file.name
        
        logger.info(f"Audio for Episode {episode.id} downloaded to {temp_audio_path}")
        return audio_content, temp_audio_path
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download audio for Episode {episode.id}: {e}", exc_info=True)
        _update_episode_status(episode, Episode.Status.FAILED)
        return None, None


def _get_ad_segments_from_gemini(audio_content: bytes, audio_duration_seconds: float) -> str:
    """Sends audio to Gemini for ad detection and returns the raw response."""
    logger.debug("Sending audio to Gemini for ad detection.")
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)

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
        # prompt = f"""Analyze the provided audio and identify segments that sound like advertisements.
        #              Return a JSON array of objects, where each object has 'start' and 'end' keys
        #              representing the start and end times of the ad segment in seconds.
        #              If no ads are found, return an empty array. Only generate the JSON array and
        #              nothing other than the array. Audio duration is {audio_duration_seconds} seconds.
        #              Example: [{{"start": 60.5, "end": 95.0}}]"""

        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}", exc_info=True)
        return "[]" # Return empty list on failure to avoid breaking the pipeline


def _parse_ad_segments(gemini_response: str, episode_id: uuid.UUID) -> List[Dict[str, float]]:
    """Parses the JSON response from Gemini to get ad segments."""
    logger.debug(f"Parsing Gemini response for Episode {episode_id}: {gemini_response}")
    # Use regex to find the JSON array within ```json ... ``` blocks or just the array itself
    match = re.search(r'```json\n(\[.*?\])\n```|(\[.*?\])', gemini_response, re.DOTALL)
    if not match:
        logger.warning(f"No valid JSON array found in Gemini response for Episode {episode_id}.")
        return []

    json_string = next(g for g in match.groups() if g is not None)
    
    try:
        ad_segments_list = json.loads(json_string)
        # Basic validation
        if not isinstance(ad_segments_list, list) or not all(
            isinstance(item, dict) and 'start' in item and 'end' in item for item in ad_segments_list
        ):
            raise json.JSONDecodeError("Invalid structure", json_string, 0)
        
        ad_segments_list.sort(key=lambda x: x['start'])
        return ad_segments_list
    except json.JSONDecodeError:
        logger.warning(f"Gemini API returned invalid JSON for ad segments for Episode {episode.id}. Response: {json_string}")
        return []


def _slice_audio(audio: AudioSegment, ad_segments: List[Dict[str, float]]) -> AudioSegment:
    """Removes ad segments from the audio."""
    if not ad_segments:
        return audio

    logger.debug(f"Slicing audio to remove {len(ad_segments)} ad segments.")
    processed_audio = AudioSegment.empty()
    last_segment_end = 0

    for segment in ad_segments:
        start_ms = int(segment['start'] * 1000)
        end_ms = int(segment['end'] * 1000)
        
        # Ensure segments are valid and ordered
        if start_ms < last_segment_end:
            logger.warning(f"Skipping overlapping or out-of-order ad segment: {segment}")
            continue

        if start_ms > last_segment_end:
            processed_audio += audio[last_segment_end:start_ms]
        
        last_segment_end = max(last_segment_end, end_ms)

    if last_segment_end < len(audio):
        processed_audio += audio[last_segment_end:]

    logger.debug("Audio slicing complete.")
    return processed_audio


def _save_processed_audio(episode: Episode, processed_audio: AudioSegment):
    """Exports the processed audio, creates a RehostedMedia entry, and updates the episode."""
    output_suffix = ".mp3"
    output_mime_type = "audio/mpeg"
    media_dir = settings.MEDIA_ROOT
    os.makedirs(media_dir, exist_ok=True)
    
    final_audio_filename = f"{uuid.uuid4()}{output_suffix}"
    final_audio_path = os.path.join(media_dir, final_audio_filename)

    logger.debug(f"Exporting processed audio to {final_audio_path}")
    processed_audio.export(final_audio_path, format="mp3")
    
    file_size = os.path.getsize(final_audio_path)

    logger.debug("Creating RehostedMedia entry in the database.")
    try:
        media_entry = RehostedMedia.objects.create(
            media_guid=uuid.uuid4(),
            file_path=final_audio_path,
            content_type=output_mime_type,
        )
        logger.info(f"Successfully created RehostedMedia record for GUID: {media_entry.media_guid}")
    except Exception as e:
        logger.error(f"Failed to create RehostedMedia record: {e}", exc_info=True)
        os.remove(final_audio_path) # Clean up orphaned file
        raise

    relative_url = reverse('serve_media_episode', kwargs={'media_guid': media_entry.media_guid})
    episode.rehosted_audio_url = f"{settings.REHOST_BASE_URL}{relative_url}"
    episode.rehosted_audio_size = file_size
    episode.rehosted_media_id = media_entry.media_guid
    _update_episode_status(episode, Episode.Status.COMPLETE, save=False)
    episode.save(update_fields=['status', 'rehosted_audio_url', 'rehosted_audio_size', 'rehosted_media_id'])
    logger.info(f"Rehost completed successfully for Episode {episode.id}.")


def rehost_episode_audio(episode_id: uuid.UUID):
    """
    Downloads, processes, and re-hosts an episode's audio.
    This is the main orchestrator for the re-hosting pipeline.
    """
    logger.info(f"Starting rehost task for Episode ID: {episode_id}")
    try:
        episode = Episode.objects.get(id=episode_id)
    except Episode.DoesNotExist:
        logger.error(f"Episode with ID {episode_id} not found. Aborting rehost task.")
        return

    temp_audio_path = None
    try:
        audio_content, temp_audio_path = _download_audio(episode)
        if not audio_content:
            return # Download failed, status already updated

        _update_episode_status(episode, Episode.Status.ANALYZING)
        audio = AudioSegment.from_file(io.BytesIO(audio_content), format="mp3")
        
        gemini_response = _get_ad_segments_from_gemini(audio_content, audio.duration_seconds)
        episode.ad_segments = gemini_response
        _update_episode_status(episode, Episode.Status.PROCESSING, save=False)
        episode.save(update_fields=['status', 'ad_segments'])
        logger.info(f"Gemini analysis complete for Episode {episode.id}. Ad segments: {gemini_response}")

        ad_segments_list = _parse_ad_segments(gemini_response, episode.id)
        processed_audio = _slice_audio(audio, ad_segments_list)
        _save_processed_audio(episode, processed_audio)

    except Exception as e:
        logger.error(f"An unexpected error occurred during rehosting of Episode {episode.id}: {e}", exc_info=True)
        _update_episode_status(episode, Episode.Status.FAILED)
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            logger.debug(f"Cleaning up temporary file: {temp_audio_path}")
            os.remove(temp_audio_path)
        logger.info(f"Finished rehost task for Episode ID: {episode.id}")


def reprocess_podcast(podcast_id: uuid.UUID):
    """
    Clears all downloaded/processed data for a podcast's episodes and
    triggers a re-processing of each episode.
    """
    logger.info(f"Starting reprocessing task for Podcast ID: {podcast_id}")
    try:
        podcast = Podcast.objects.get(id=podcast_id)
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting reprocess task.")
        return

    for episode in podcast.episodes.all():
        logger.info(f"Resetting episode '{episode.title}' for reprocessing.")
        
        # Delete associated media file if it exists
        if episode.rehosted_media_id:
            try:
                media_item = RehostedMedia.objects.get(pk=episode.rehosted_media_id)
                if os.path.exists(media_item.file_path):
                    os.remove(media_item.file_path)
                    logger.debug(f"Deleted physical file: {media_item.file_path}")
                media_item.delete()
                logger.debug(f"Deleted RehostedMedia entry for GUID: {media_item.media_guid}")
            except RehostedMedia.DoesNotExist:
                logger.warning(f"RehostedMedia record not found for media ID: {episode.rehosted_media_id}")
            except Exception as e:
                logger.error(f"Error deleting media for episode {episode.id}: {e}", exc_info=True)

        # Reset episode fields
        episode.status = Episode.Status.NEW
        episode.rehosted_media_id = None
        episode.rehosted_audio_url = None
        episode.rehosted_audio_size = 0
        episode.ad_segments = None
        episode.save()

        # Trigger re-hosting task
        rehost_thread = threading.Thread(target=rehost_episode_audio, args=(episode.id,))
        rehost_thread.start()
        logger.info(f"Dispatched re-hosting task for episode '{episode.title}'.")

    logger.info(f"Completed dispatching reprocessing tasks for all episodes of podcast '{podcast.title}'.")


def poll_feed(podcast_id: uuid.UUID):
    """
    Fetches and parses a podcast's RSS feed to find and save new episodes.
    """
    logger.info(f"Starting poll for Podcast ID: {podcast_id}")
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Polling feed for podcast: '{podcast.title}'")
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting task.")
        return

    podcast.last_polled = timezone.now()
    podcast.save(update_fields=['last_polled'])

    # Reset the status of any episodes that are not complete
    stuck_episodes = podcast.episodes.exclude(status=Episode.Status.COMPLETE)
    for episode in stuck_episodes:
        episode.status = Episode.Status.NEW
        episode.save(update_fields=['status'])
        logger.info(f"Reset status for episode '{episode.title}' to NEW.")
        rehost_thread = threading.Thread(target=rehost_episode_audio, args=(episode.id,))
        rehost_thread.start()

    try:
        logger.debug(f"Fetching RSS feed from {podcast.rss_url}")
        feed = feedparser.parse(podcast.rss_url, agent=BROWSER_USER_AGENT)
        if feed.bozo:
            logger.error(f"Malformed feed for '{podcast.title}'. Reason: {feed.get('bozo_exception', 'Unknown')}")
            return
    except Exception as e:
        logger.error(f"Failed to fetch or parse feed for '{podcast.title}': {e}", exc_info=True)
        return

    new_episodes_count = 0
    skipped_episodes_count = 0
    # Limit processing to the latest 10 episodes to avoid overwhelming the system
    # on the first poll of a large feed.
    logger.info(f"Processing the latest {10} episodes from '{podcast.title}'.")

    for entry in feed.entries[:10]:
        guid = entry.get("id")
        if not guid:
            logger.warning(f"Skipping entry in '{podcast.title}' due to missing GUID. Title: '{entry.get('title', 'N/A')}'")
            continue

        audio_url = next((enclosure.get("href") for enclosure in entry.get("enclosures", []) if enclosure.get("type", "").startswith("audio")), None)
        if not audio_url:
            logger.warning(f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) in '{podcast.title}' due to missing audio enclosure.")
            continue

        pub_date_parsed = entry.get("published_parsed")
        if not pub_date_parsed:
            logger.warning(f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) in '{podcast.title}' due to missing publication date.")
            continue
        
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
            if created:
                new_episodes_count += 1
                logger.info(f"New episode created: '{episode.title}' for podcast '{podcast.title}'. Dispatching rehost task.")
                rehost_thread = threading.Thread(target=rehost_episode_audio, args=(episode.id,))
                rehost_thread.start()
            else:
                skipped_episodes_count += 1
        except Exception as e:
            logger.error(f"Failed to create episode with GUID {guid} for podcast '{podcast.title}'. Error: {e}", exc_info=True)

    logger.info(f"Polling complete for '{podcast.title}'. Found {new_episodes_count} new episodes. Skipped {skipped_episodes_count} existing episodes.")


def delete_podcast_data(podcast_id: uuid.UUID):
    """
    Deletes a podcast and all associated episodes, rehosted media entries, and audio files.
    """
    logger.info(f"Starting deletion task for Podcast ID: {podcast_id}")
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Found podcast '{podcast.title}' ({podcast.id}) for deletion.")

        # Collect all media IDs to delete
        media_ids_to_delete = list(podcast.episodes.values_list('rehosted_media_id', flat=True).exclude(rehosted_media_id__isnull=True))

        # Delete RehostedMedia entries and their corresponding files
        if media_ids_to_delete:
            rehosted_media_entries = RehostedMedia.objects.filter(media_guid__in=media_ids_to_delete)
            for media_item in rehosted_media_entries:
                try:
                    if os.path.exists(media_item.file_path):
                        os.remove(media_item.file_path)
                        logger.info(f"Deleted physical file: {media_item.file_path}")
                    else:
                        logger.warning(f"Physical file not found for deletion: {media_item.file_path}")
                    media_item.delete()
                    logger.info(f"Deleted RehostedMedia entry for GUID: {media_item.media_guid}")
                except Exception as e:
                    logger.error(f"Error deleting rehosted media for GUID {media_item.media_guid}: {e}", exc_info=True)

        # Finally, delete the podcast object, which will cascade-delete its episodes
        podcast_title = podcast.title
        podcast.delete()
        logger.info(f"Successfully deleted podcast '{podcast_title}' ({podcast_id}) and all its episodes.")

    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting deletion task.")
    except Exception as e:
        logger.error(f"An error occurred during podcast deletion for ID {podcast_id}: {e}", exc_info=True)
