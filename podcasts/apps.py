"""
This module contains the app configuration for the podcasts app.
"""
import logging
import os
import threading

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


def polling_loop(shutdown_event: threading.Event, polling_interval: int):
    """
    The main polling loop that checks for new episodes.

    Args:
        shutdown_event: Event to signal thread shutdown.
        polling_interval: The interval in seconds between polling runs.
    """
    from podcasts.models import Podcast, Episode
    from podcasts.tasks import poll_feed

    # Reset any stuck/in-progress episodes to NEW on startup/reload
    try:
        stuck_count = Episode.objects.filter(
            status__in=[
                Episode.Status.DOWNLOADING,
                Episode.Status.ANALYZING,
                Episode.Status.PROCESSING,
            ]
        ).update(status=Episode.Status.NEW)
        if stuck_count > 0:
            logger.info("Reset %d in-progress/stuck episodes to NEW on startup.", stuck_count)
    except Exception as e:
        logger.error("Failed to reset stuck episodes on startup: %s", e)

    while not shutdown_event.is_set():
        logger.info("Polling for new episodes...")
        for podcast in Podcast.objects.all():
            if shutdown_event.is_set():
                break
            logger.info("Checking podcast: %s", podcast.title)
            try:
                poll_feed(podcast.id)
            except Podcast.DoesNotExist:
                logger.error("Error polling %s", podcast.title, exc_info=True)

        if not shutdown_event.is_set():
            logger.info(
                "Finished polling. Waiting for %d seconds...", polling_interval
            )
            shutdown_event.wait(polling_interval)


def start_polling_thread():
    """
    Starts the background polling thread.
    """
    shutdown_event = threading.Event()
    polling_interval = settings.PODCAST_POLLING_INTERVAL

    polling_thread = threading.Thread(
        target=polling_loop,
        args=(shutdown_event, polling_interval),
        daemon=True,
    )
    polling_thread.start()
    logger.info("Started background thread for podcast polling.")
    return polling_thread, shutdown_event


class PodcastsConfig(AppConfig):
    """
    App configuration for the podcasts app.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "podcasts"

    def ready(self):
        """
        Starts the polling thread when the app is ready.
        """
        import sys

        # Avoid running in management commands like 'migrate', 'makemigrations', 'collectstatic', 'test'
        if any(
            cmd in sys.argv for cmd in ["migrate", "makemigrations", "collectstatic", "test", "inspect_uuids", "lint", "mark_episode_for_reanalysis"]
        ):
            return

        # Also avoid starting multiple threads when using runserver with auto-reload
        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        start_polling_thread()
