from django.test import TestCase
from ..models import Podcast
from ..serializers import PodcastSerializer

class PodcastSerializerTest(TestCase):
    def test_podcast_serializer_valid_data(self):
        serializer = PodcastSerializer(data={'rss_url': 'http://example.com/new_feed.xml'})
        self.assertTrue(serializer.is_valid())

    def test_podcast_serializer_duplicate_rss_url(self):
        Podcast.objects.create(title="Existing", rss_url="http://example.com/existing.xml")
        serializer = PodcastSerializer(data={'rss_url': 'http://example.com/existing.xml'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('rss_url', serializer.errors)
