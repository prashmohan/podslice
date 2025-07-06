"""
This module contains the lint command for the podcasts app.
"""
from django.core.management.base import BaseCommand
from pylint.lint import Run


class Command(BaseCommand):
    """A command to run pylint on the project."""

    help = "Runs pylint on the project."

    def handle(self, *args, **options):
        """Handles the command."""
        Run(["--load-plugins=pylint_django", "--django-settings-module=podslice.settings", "podcasts", "podslice"])
