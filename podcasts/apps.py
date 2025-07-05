import time
import threading
import logging
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

def polling_loop(shutdown_event: threading.Event, polling_interval: int):
    """
    The main polling loop that checks for new episodes.
    """
    from .models import Podcast
    from .tasks import poll_feed

    while not shutdown_event.is_set():
        logger.info('Polling for new episodes...')
        podcasts = Podcast.objects.all()
        for podcast in podcasts:
            if shutdown_event.is_set():
                break
            logger.info(f'Checking podcast: {podcast.title}')
            try:
                poll_feed(podcast.id)
            except Exception as e:
                logger.error(f'Error polling {podcast.title}: {e}')
        
        if not shutdown_event.is_set():
            logger.info(f'Finished polling. Waiting for {polling_interval} seconds...')
            shutdown_event.wait(polling_interval)

def start_polling_thread():
    """
    Starts the background polling thread.
    """
    shutdown_event = threading.Event()
    polling_interval = 60 * 60  # Poll every hour
    
    polling_thread = threading.Thread(
        target=polling_loop,
        args=(shutdown_event, polling_interval),
        daemon=True
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
        import sys
        if 'runserver' in sys.argv:
            start_polling_thread()
