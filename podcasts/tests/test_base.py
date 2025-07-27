"""
Base test case for the podcasts app.
"""
import os
from django.test import TestCase, override_settings
from django.utils import timezone
from django.conf import settings
from podcasts.models import Podcast, Episode


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class PodcastTestCase(TestCase):
    """
    Base test case for podcast-related tests.
    """

    def setUp(self):
        """Set up the test case."""
        self.podcast = Podcast.objects.create(
            title="Test Podcast", rss_url="http://example.com/rss"
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )
        self.test_media_dir = settings.MEDIA_ROOT
        os.makedirs(self.test_media_dir, exist_ok=True)

    def tearDown(self):
        """Clean up after each test."""
        if os.path.exists(self.test_media_dir):
            for f in os.listdir(self.test_media_dir):
                os.remove(os.path.join(self.test_media_dir, f))
            if os.path.exists(self.test_media_dir) and not os.listdir(
                self.test_media_dir
            ):
                os.rmdir(self.test_media_dir)
