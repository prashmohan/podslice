from django.core.management.base import BaseCommand
from django.db import connection
import uuid

class Command(BaseCommand):
    help = 'Inspects raw Podcast data to find invalid UUIDs without using the ORM.'

    def handle(self, *args, **options):
        self.stdout.write("Starting raw inspection of Podcast UUIDs...")
        invalid_podcasts = []
        
        with connection.cursor() as cursor:
            # Using a raw query to bypass the ORM's UUID conversion
            cursor.execute("SELECT id, title FROM podcasts_podcast")
            rows = cursor.fetchall()

        for podcast_id, podcast_title in rows:
            try:
                # We attempt the same validation that Django does
                uuid.UUID(str(podcast_id))
            except (ValueError, TypeError):
                invalid_podcasts.append({'id': podcast_id, 'title': podcast_title})

        if invalid_podcasts:
            self.stdout.write(self.style.WARNING(f"Found {len(invalid_podcasts)} podcasts with invalid UUIDs:"))
            for podcast in invalid_podcasts:
                self.stdout.write(f"  - Title: {podcast['title']}, Invalid ID: {podcast['id']}")
            self.stdout.write(self.style.NOTICE("\nTo fix this, you will need to manually inspect the database and either delete these rows or assign them valid UUIDs."))
            self.stdout.write(self.style.NOTICE("You can use 'python manage.py dbshell' to access the database directly."))
        else:
            self.stdout.write(self.style.SUCCESS("No invalid UUIDs found in the Podcast table."))