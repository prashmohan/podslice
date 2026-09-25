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


class EpisodeProcessingMetricsModelTest(TestCase):
    def test_processing_metrics_field_persists_json(self):
        podcast = Podcast.objects.create(title="P", rss_url="http://example.com/rss")
        payload = {
            "status": "COMPLETE",
            "total_duration_sec": 10.5,
            "stages": {
                "download": {"status": "success"},
                "gemini": {"status": "success", "model_used": "gemini-3.8-flash"},
                "slicing": {"status": "success"}
            }
        }
        episode = Episode.objects.create(
            podcast=podcast,
            title="E",
            guid="g-test",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://example.com/a.mp3",
            processing_metrics=payload
        )
        episode.refresh_from_db()
        self.assertEqual(episode.processing_metrics["status"], "COMPLETE")
        self.assertEqual(episode.processing_metrics["stages"]["gemini"]["model_used"], "gemini-3.8-flash")

