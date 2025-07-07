"""
This module contains the app configuration for the podcasts app.
"""
import logging
import sys
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
    from podcasts.models import Podcast
    from podcasts.tasks import poll_feed

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
    polling_interval = getattr(settings, "PODCAST_POLLING_INTERVAL", 60 * 60)

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
        # Avoid running in management commands like 'migrate'
        if "runserver" in sys.argv:
            start_polling_thread()
