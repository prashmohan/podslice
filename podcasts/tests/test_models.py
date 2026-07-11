"""
Tests for the models in the podcasts app.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from podcasts.models import Episode, Podcast


@override_settings(REHOST_BASE_URL="http://localhost:12343")
class EpisodeModelTest(TestCase):
    """Tests for the Episode model."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
        )

    def test_rehosted_audio_url_property(self):
        """Test the rehosted_audio_url property."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date="2025-07-20T12:00:00Z",
        )
        self.assertIsNotNone(episode.rehosted_audio_url)
        self.assertIsNotNone(episode.rehosted_media_id)

        expected_url = f"http://localhost:12343{reverse('serve_media_episode', kwargs={'media_guid': episode.rehosted_media_id})}"
        self.assertEqual(episode.rehosted_audio_url, expected_url)


class PodcastModelTest(TestCase):
    """Tests for the Podcast model."""

    def test_podcast_creation(self):
        """Test that a Podcast can be created."""
        podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg",
        )
        self.assertIsInstance(podcast, Podcast)
        self.assertEqual(str(podcast), "Test Podcast")

