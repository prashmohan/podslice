"""
This module contains a management command to mark a single episode for reanalysis.
"""
from django.core.management.base import BaseCommand, CommandError

from podcasts.models import Episode


class Command(BaseCommand):
    """
    A management command to mark a single episode for reanalysis by setting its status to 'NEW'.
    """

    help = "Marks a single episode for reanalysis by setting its status to 'NEW'."

    def add_arguments(self, parser):
        """
        Adds the command-line arguments for the command.
        """
        parser.add_argument(
            "title",
            type=str,
            help="The title of the episode to mark for reanalysis.",
        )

    def handle(self, *args, **options):
        """
        The main logic for the command.
        """
        title = options["title"]
        try:
            episode = Episode.objects.get(title=title)
            episode.status = Episode.Status.NEW
            episode.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully marked episode '{title}' for reanalysis."
                )
            )
        except Episode.DoesNotExist:
            raise CommandError(f"Episode with title '{title}' does not exist.")
        except Episode.MultipleObjectsReturned:
            raise CommandError(
                f"Multiple episodes found with title '{title}'. Please use a more specific identifier."
            )
