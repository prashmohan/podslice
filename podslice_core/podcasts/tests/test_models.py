from django.test import TestCase
from ..models import Podcast, RehostedMedia

class PodcastModelTest(TestCase):
    def test_podcast_creation(self):
        podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.assertIsInstance(podcast, Podcast)
        self.assertEqual(str(podcast), "Test Podcast")


class RehostedMediaModelTest(TestCase):
    def test_rehosted_media_creation(self):
        media = RehostedMedia.objects.create(
            file_path="/path/to/file.mp3",
            content_type="audio/mpeg"
        )
        self.assertIsInstance(media, RehostedMedia)
        self.assertEqual(str(media), f"Media {media.media_guid}")
