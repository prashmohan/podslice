import logging
import os
import tempfile
import time
from datetime import datetime
import uuid
import json
import io
import threading

import feedparser
import requests
from django.conf import settings
from django.utils import timezone
import google.generativeai as genai
from pydub import AudioSegment
from pydub.silence import split_on_silence

from .models import Episode, Podcast
from rehost_app.models import RehostedMedia

# Define constants
logger = logging.getLogger(__name__)
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"


def poll_feed(podcast_id):
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
    logger.info(f"Processing the latest {2} episodes from '{podcast.title}'.")

    for entry in feed.entries[:2]:
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


def rehost_episode_audio(episode_id):
    """
    Downloads an episode's audio, processes it to remove ads using Gemini,
    and re-hosts the ad-free audio.
    """
    logger.info(f"Starting rehost task for Episode ID: {episode_id}")
    try:
        episode = Episode.objects.get(id=episode_id)
        logger.info(f"Processing episode: '{episode.title}' from podcast '{episode.podcast.title}'")
        episode.status = Episode.Status.DOWNLOADING
        episode.save(update_fields=['status'])
    except Episode.DoesNotExist:
        logger.error(f"Episode with ID {episode_id} not found. Aborting rehost task.")
        return

    temp_audio_path = None
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
        episode.status = Episode.Status.ANALYZING
        episode.save(update_fields=['status'])

        logger.debug("Sending audio to Gemini for ad detection.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-pro')
        
        audio = AudioSegment.from_file(io.BytesIO(audio_content), format="mp3")
        
        prompt = f"""Analyze the provided audio and identify segments that sound like advertisements. 
                     Return a JSON array of objects, where each object has 'start' and 'end' keys 
                     representing the start and end times of the ad segment in seconds. 
                     If no ads are found, return an empty array. 
                     Audio duration is {audio.duration_seconds} seconds.
                     Example: [{{"start": 60.5, "end": 95.0}}]"""

        response = model.generate_content(prompt)
        ad_segments = response.text
        episode.ad_segments = ad_segments
        episode.status = Episode.Status.PROCESSING
        episode.save(update_fields=['status', 'ad_segments'])
        logger.info(f"Gemini analysis complete for Episode {episode.id}. Ad segments: {ad_segments}")

        logger.debug("Slicing audio to remove ad segments.")
        ad_segments_list = json.loads(episode.ad_segments) if episode.ad_segments else []
        ad_segments_list.sort(key=lambda x: x['start'])

        processed_audio = AudioSegment.empty()
        last_segment_end = 0

        for segment in ad_segments_list:
            start_ms = int(segment['start'] * 1000)
            end_ms = int(segment['end'] * 1000)
            if start_ms > last_segment_end:
                processed_audio += audio[last_segment_end:start_ms]
            last_segment_end = max(last_segment_end, end_ms)

        if last_segment_end < len(audio):
            processed_audio += audio[last_segment_end:]

        if not ad_segments_list:
            processed_audio = audio
        logger.debug("Audio slicing complete.")

        output_suffix = ".mp3"
        output_mime_type = "audio/mpeg"
        media_dir = settings.MEDIA_ROOT
        os.makedirs(media_dir, exist_ok=True)
        final_audio_filename = f"{uuid.uuid4()}{output_suffix}"
        final_audio_path = os.path.join(media_dir, final_audio_filename)

        logger.debug(f"Exporting processed audio to {final_audio_path}")
        processed_audio.export(final_audio_path, format="mp3")
        
        logger.debug("Creating RehostedMedia entry in rehost_db.")
        media_guid = uuid.uuid4()
        RehostedMedia.objects.using('rehost_db').create(
            media_guid=media_guid,
            file_path=final_audio_path,
            content_type=output_mime_type,
        )
        episode.rehosted_audio_url = f"{settings.REHOST_BASE_URL}/audio/{media_guid}/"
        episode.status = Episode.Status.COMPLETE
        episode.save(update_fields=['status', 'rehosted_audio_url'])
        logger.info(f"Rehost completed successfully for Episode {episode.id}.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download audio for Episode {episode.id}: {e}", exc_info=True)
        episode.status = Episode.Status.FAILED
        episode.save(update_fields=['status'])
    except Exception as e:
        logger.error(f"An error occurred during rehosting of Episode {episode.id}: {e}", exc_info=True)
        episode.status = Episode.Status.FAILED
        episode.save(update_fields=['status'])
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            logger.debug(f"Cleaning up temporary file: {temp_audio_path}")
            os.remove(temp_audio_path)
