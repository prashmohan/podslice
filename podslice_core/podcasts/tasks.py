import logging
import time
from datetime import datetime

import feedparser
from celery import shared_task
from django.utils import timezone

from .models import Episode, Podcast

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
            _, created = Episode.objects.get_or_create(
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