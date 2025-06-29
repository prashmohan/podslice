import logging
import os
import tempfile
import time
from datetime import datetime
import uuid
import json
import io

import feedparser
import requests
from celery import shared_task
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


@shared_task
def poll_feed(podcast_id):
    """
    Fetches and parses a podcast's RSS feed to find and save new episodes.
    This task is idempotent: running it multiple times will not create
    duplicate episodes.
    """
    # 1. Retrieve the Podcast object
    try:
        podcast = Podcast.objects.get(id=podcast_id)
        logger.info(f"Starting poll for Podcast '{podcast.title}' (ID: {podcast_id})")
    except Podcast.DoesNotExist:
        logger.error(f"Podcast with ID {podcast_id} not found. Aborting task.")
        return f"Error: Podcast with ID {podcast_id} does not exist."

    # 2. Fetch and Parse the RSS Feed
    try:
        feed = feedparser.parse(podcast.rss_url, agent=BROWSER_USER_AGENT)
        if feed.bozo:
            # The 'bozo' bit is set if the feed is malformed.
            raise ValueError(
                f"Feed is malformed. Reason: {feed.get('bozo_exception', 'Unknown')}"
            )
    except (IOError, ValueError) as e:
        logger.error(f"Failed to fetch or parse feed for '{podcast.title}': {e}")
        return f"Error processing feed for Podcast ID {podcast_id}: {e}"

    # 3. Process Feed Entries and Create Episodes
    new_episodes_count = 0
    skipped_episodes_count = 0

    for entry in feed.entries:
        # --- A. Extract GUID (unique identifier for an episode) ---
        guid = entry.get("id")
        if not guid:
            logger.warning(
                f"Skipping entry in '{podcast.title}' due to missing GUID. "
                f"Title: '{entry.get('title', 'N/A')}'"
            )
            continue

        # --- B. Extract Audio URL from enclosures ---
        audio_url = None
        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("audio"):
                audio_url = enclosure.get("href")
                break  # Found an audio enclosure, stop looking

        if not audio_url:
            logger.warning(
                f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) "
                f"in '{podcast.title}' due to missing audio enclosure."
            )
            continue

        # --- C. Extract and parse publication date ---
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            # Convert time.struct_time to a naive datetime object
            dt_naive = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            # Make it timezone-aware using Django's current timezone setting
            pub_date = timezone.make_aware(dt_naive, timezone.get_current_timezone())
        else:
            logger.warning(
                f"Skipping entry '{entry.get('title', 'N/A')}' (GUID: {guid}) "
                f"in '{podcast.title}' due to missing publication date."
            )
            continue

        # --- D. Atomically create the episode if it doesn't exist ---
        # The 'guid' field has a unique constraint in the database, so this
        # prevents duplicates across all podcasts.
        try:
            episode_obj, created = Episode.objects.get_or_create(
                guid=guid,
                defaults={
                    "podcast": podcast,
                    "title": entry.get("title", "Untitled Episode"),
                    "pub_date": pub_date,
                    "original_audio_url": audio_url,
                    # The 'status' field defaults to 'NEW' as defined in the model
                },
            )
            if created:
                new_episodes_count += 1
                logger.info(
                    f"Created new episode for '{podcast.title}': '{entry.get('title')}'"
                )
                rehost_episode_audio.delay(episode_obj.id)
            else:
                skipped_episodes_count += 1
        except Exception as e:
            # Catch potential integrity errors or other DB issues for a single episode
            logger.error(
                f"Failed to create episode with GUID {guid} for podcast '{podcast.title}'. Error: {e}",
                exc_info=True,
            )

    summary = (
        f"Polling complete for '{podcast.title}'. "
        f"Found {new_episodes_count} new episodes. "
        f"Skipped {skipped_episodes_count} existing episodes."
    )
    logger.info(summary)
    return summary


@shared_task
def rehost_episode_audio(episode_id):
    """
    Downloads an episode's audio, processes it to remove ads using Gemini,
    and re-hosts the ad-free audio.
    """
    try:
        episode = Episode.objects.get(id=episode_id)
        logger.info(f"Starting rehost for Episode '{episode.title}' (ID: {episode_id})")
        episode.status = Episode.Status.DOWNLOADING
        episode.save()
    except Episode.DoesNotExist:
        logger.error(f"Episode with ID {episode_id} not found. Aborting rehost task.")
        return f"Error: Episode with ID {episode_id} does not exist."

    temp_audio_path = None
    try:
        # 1. Download the audio file from original_audio_url
        response = requests.get(episode.original_audio_url, stream=True)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio_file:
            audio_content = b""
            for chunk in response.iter_content(chunk_size=8192):
                temp_audio_file.write(chunk)
                audio_content += chunk
            temp_audio_path = temp_audio_file.name
        
        logger.info(f"Downloaded audio for Episode {episode.id} to {temp_audio_path}")
        episode.status = Episode.Status.ANALYZING
        episode.save()

        # 2. Send audio to Google Gemini API -> gets ad timestamps
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
        episode.save()
        logger.info(f"Gemini analysis complete for Episode {episode.id}. Ad segments: {ad_segments}")

        # 3. Uses pydub to slice audio, removes ad segments
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

        output_suffix = ".mp3"
        output_mime_type = "audio/mpeg"
        media_dir = settings.MEDIA_ROOT
        os.makedirs(media_dir, exist_ok=True)
        final_audio_filename = f"{uuid.uuid4()}{output_suffix}"
        final_audio_path = os.path.join(media_dir, final_audio_filename)

        processed_audio.export(final_audio_path, format="mp3")
        logger.info(f"Saved processed audio for Episode {episode.id} to {final_audio_path}")

        # 6. Updates rehost SQLite DB with file info & URL
        media_guid = uuid.uuid4()
        RehostedMedia.objects.using('rehost_db').create(
            media_guid=media_guid,
            file_path=final_audio_path,
            content_type=output_mime_type,
        )
        episode.rehosted_audio_url = f"{settings.REHOST_BASE_URL}/audio/{media_guid}/"
        episode.status = Episode.Status.COMPLETE
        episode.save()

        return f"Rehost completed for Episode ID {episode_id}."

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download audio for Episode {episode.id}: {e}")
        episode.status = Episode.Status.FAILED
        episode.save()
        return f"Error: Failed to download audio for Episode {episode.id}."
    except Exception as e:
        logger.error(f"An error occurred during rehosting of Episode {episode.id}: {e}", exc_info=True)
        episode.status = Episode.Status.FAILED
        episode.save()
        return f"Error: An error occurred during rehosting of Episode {episode.id}."
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)