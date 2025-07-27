
"""
Tests for the management commands in the podcasts app.
"""
import io

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from podcasts.models import Episode, Podcast


class MarkEpisodeForReanalysisTest(TestCase):
    """Tests for the mark_episode_for_reanalysis management command."""

    def setUp(self):
        """Set up the test case."""
        self.podcast = Podcast.objects.create(
            title="Test Podcast", rss_url="https://example.com/rss"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            status=Episode.Status.COMPLETE,
            pub_date=timezone.now(),
        )

    def test_mark_episode_for_reanalysis_success(self):
        """Test that an episode can be marked for reanalysis."""
        out = io.StringIO()
        call_command("mark_episode_for_reanalysis", self.episode.title, stdout=out)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.NEW)
        self.assertIn("Successfully marked episode", out.getvalue())

    def test_mark_episode_for_reanalysis_not_found(self):
        """Test that an error is raised when the episode is not found."""
        out = io.StringIO()
        with self.assertRaises(Exception):
            call_command(
                "mark_episode_for_reanalysis", "Non-existent episode", stdout=out
            )

    def test_mark_episode_for_reanalysis_multiple_found(self):
        """Test that an error is raised when multiple episodes are found."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="67890",
            status=Episode.Status.COMPLETE,
            pub_date=timezone.now(),
        )
        out = io.StringIO()
        with self.assertRaises(Exception):
            call_command("mark_episode_for_reanalysis", self.episode.title, stdout=out)
