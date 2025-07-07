
import io
from django.core.management import call_command
from django.test import TestCase
from podcasts.models import Episode, Podcast

from django.utils import timezone
class MarkEpisodeForReanalysisTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="https://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            status=Episode.Status.COMPLETE,
            pub_date=timezone.now(),
        )

    def test_mark_episode_for_reanalysis_success(self):
        out = io.StringIO()
        call_command("mark_episode_for_reanalysis", self.episode.title, stdout=out)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.NEW)
        self.assertIn("Successfully marked episode", out.getvalue())

    def test_mark_episode_for_reanalysis_not_found(self):
        out = io.StringIO()
        with self.assertRaises(Exception):
            call_command("mark_episode_for_reanalysis", "Non-existent episode", stdout=out)

    def test_mark_episode_for_reanalysis_multiple_found(self):
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
