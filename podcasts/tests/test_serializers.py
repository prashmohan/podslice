"""
Tests for the serializers in the podcasts app.
"""
from django.test import TestCase

from podcasts.models import Podcast
from podcasts.serializers import PodcastSerializer


class PodcastSerializerTest(TestCase):
    """Tests for the PodcastSerializer."""

    def test_podcast_serializer_valid_data(self):
        """Test that the PodcastSerializer can be validated with valid data."""
        serializer = PodcastSerializer(data={"rss_url": "http://example.com/new_feed.xml"})
        self.assertTrue(serializer.is_valid())

    def test_podcast_serializer_duplicate_rss_url(self):
        """Test that the PodcastSerializer raises a validation error for a duplicate RSS URL."""
        Podcast.objects.create(
            title="Existing", rss_url="http://example.com/existing.xml"
        )
        serializer = PodcastSerializer(data={"rss_url": "http://example.com/existing.xml"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("rss_url", serializer.errors)