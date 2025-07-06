"""
Tests for the models in the podcasts app.
"""
from django.test import TestCase

from podcasts.models import Podcast, RehostedMedia


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


class RehostedMediaModelTest(TestCase):
    """Tests for the RehostedMedia model."""

    def test_rehosted_media_creation(self):
        """Test that a RehostedMedia object can be created."""
        media = RehostedMedia.objects.create(
            file_path="/path/to/file.mp3", content_type="audio/mpeg"
        )
        self.assertIsInstance(media, RehostedMedia)
        self.assertEqual(str(media), f"Media {media.media_guid}")