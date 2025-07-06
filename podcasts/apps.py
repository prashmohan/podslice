import sys
import threading
import logging
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
    from .models import Podcast
    from .tasks import poll_feed

    while not shutdown_event.is_set():
        logger.info("Polling for new episodes...")
        for podcast in Podcast.objects.all():
            if shutdown_event.is_set():
                break
            logger.info(f"Checking podcast: {podcast.title}")
            try:
                poll_feed(podcast.id)
            except Exception:
                logger.error(f"Error polling {podcast.title}", exc_info=True)

        if not shutdown_event.is_set():
            logger.info(f"Finished polling. Waiting for {polling_interval} seconds...")
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
    default_auto_field = "django.db.models.BigAutoField"
    name = "podcasts"

    def ready(self):
        """
        Starts the polling thread when the app is ready.
        """
        # Avoid running in management commands like 'migrate'
        if "runserver" in sys.argv:
            start_polling_thread()
