import json
import logging
import os
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from rehost_app.models import RehostedEpisode, RehostedPodcast

# The shared directory where the core service will drop JSON message files.
# We get the MEDIA_ROOT from settings, which is /media by default.
MESSAGE_DIR = Path(settings.MEDIA_ROOT) / "messages"

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Runs a watchdog to process new episode JSON files from a shared directory."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Starting file-based consumer..."))
        self.stdout.write(f"Watching for new message files in: {MESSAGE_DIR}")

        # Ensure the message directory exists.
        try:
            MESSAGE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.stderr.write(self.style.ERROR(f"Could not create or access message directory: {e}"))
            return # Exit if we can't access the dir

        # Main loop to run the consumer indefinitely
        while True:
            try:
                # Scan the directory for .json files
                json_files = list(MESSAGE_DIR.glob("*.json"))
                
                if not json_files:
                    # If no files, wait before scanning again to avoid busy-looping
                    time.sleep(5)
                    continue

                self.stdout.write(f"Found {len(json_files)} new message file(s). Processing...")
                for file_path in json_files:
                    self.process_message_file(file_path)

            except Exception as e:
                logger.error("An unexpected error occurred in the consumer loop.", exc_info=True)
                self.stderr.write(self.style.ERROR(f"An unexpected error occurred: {e}. Retrying in 5 seconds..."))
                time.sleep(5)

    def process_message_file(self, file_path: Path):
        """
        Reads a JSON file, saves the data to the database, and deletes the file.
        """
        try:
            self.stdout.write(f"\nProcessing file: {file_path.name}")
            with file_path.open('r') as f:
                data = json.load(f)

            # --- Data Validation (same as before) ---
            if 'podcast' not in data or 'episode' not in data:
                raise ValueError("Message is missing 'podcast' or 'episode' key.")
            
            podcast_data = data['podcast']
            episode_data = data['episode']

            if 'slug' not in podcast_data or 'guid' not in episode_data:
                raise ValueError("Message is missing 'slug' or 'guid' key.")

            # --- Database Operations (Idempotent) ---
            podcast_obj, created = RehostedPodcast.objects.get_or_create(
                slug=podcast_data['slug'],
                defaults={
                    'original_rss_url': podcast_data.get('original_rss_url'),
                    'title': podcast_data.get('title', 'Untitled Podcast'),
                    'author': podcast_data.get('author'),
                    'artwork_url': podcast_data.get('artwork_url'),
                    'description': podcast_data.get('description'),
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  -> Created new podcast: '{podcast_obj.title}'"))
            else:
                self.stdout.write(f"  -> Found existing podcast: '{podcast_obj.title}'")

            _ep_obj, created = RehostedEpisode.objects.get_or_create(
                guid=episode_data['guid'],
                defaults={
                    'podcast': podcast_obj,
                    'title': episode_data.get('title', 'Untitled Episode'),
                    'pub_date_iso': episode_data.get('pub_date_iso'),
                    'description': episode_data.get('description'),
                    'absolute_file_path': episode_data.get('absolute_file_path'),
                    'duration_seconds': episode_data.get('duration_seconds'),
                    'audio_file_size_bytes': episode_data.get('audio_file_size_bytes'),
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  -> Created new episode: '{episode_data.get('title')}'"))
            else:
                self.stdout.write(f"  -> Episode with GUID '{episode_data['guid']}' already exists. Skipped.")

            # --- Cleanup ---
            # If all database operations were successful, delete the file.
            file_path.unlink()
            self.stdout.write(self.style.SUCCESS(f"  -> Successfully processed and deleted {file_path.name}"))

        except json.JSONDecodeError:
            self.stderr.write(self.style.ERROR(f"Could not decode JSON from {file_path.name}. Moving to 'failed' folder."))
            self.move_to_failed(file_path)
        except Exception as e:
            logger.error(f"Failed to process message file {file_path.name}.", exc_info=True)
            self.stderr.write(self.style.ERROR(f"An error occurred with {file_path.name}: {e}. Moving to 'failed' folder."))
            self.move_to_failed(file_path)
    
    def move_to_failed(self, file_path: Path):
        """Moves a file that failed processing to a 'failed' subdirectory for inspection."""
        failed_dir = MESSAGE_DIR / "failed"
        failed_dir.mkdir(exist_ok=True)
        try:
            file_path.rename(failed_dir / file_path.name)
        except OSError as e:
            logger.error(f"Could not move failed file {file_path.name} to {failed_dir}. Error: {e}")