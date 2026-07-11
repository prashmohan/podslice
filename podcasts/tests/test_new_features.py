"""
Tests for the newly added features: Duration ingestion, iTunes RSS metadata compliance, and HTTP conditional caching.
"""
import os
from unittest import mock
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from podcasts.tests.test_base import PodcastTestCase
from podcasts.models import Episode
from podcasts.views import create_podcast_from_url


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class NewFeaturesTest(PodcastTestCase):
    """
    Test cases for duration ingestion, iTunes metadata rendering, and HTTP caching.
    """

    @mock.patch("podcasts.views.feedparser.parse")
    def test_create_podcast_from_url_ingests_metadata(self, mock_parse):
        """
        Test that create_podcast_from_url parses and saves author and description.
        """
        # Mock feed parser return value
        mock_feed = mock.Mock()
        mock_feed.bozo = False
        mock_feed.feed = {
            "title": "New Podcast Feed",
            "image": {"href": "http://example.com/artwork.jpg"},
            "author": "John Doe & Jane Smith",
            "description": "A very informative podcast description.",
        }
        mock_parse.return_value = mock_feed

        # Run creation
        with mock.patch("podcasts.views.poll_feed"):
            podcast = create_podcast_from_url("http://example.com/feed-new.xml")

        self.assertEqual(podcast.title, "New Podcast Feed")
        self.assertEqual(podcast.author, "John Doe & Jane Smith")
        self.assertEqual(podcast.description, "A very informative podcast description.")

    @mock.patch("podcasts.tasks.EpisodeProcessor._get_audio_duration")
    @mock.patch("podcasts.tasks.RehostedMedia.objects.create")
    def test_episode_processor_ingests_duration(self, mock_media_create, mock_duration):
        """
        Test that _save_processed_audio fetches the audio duration and saves it.
        """
        mock_duration.return_value = 185.4
        mock_media_entry = mock.Mock()
        mock_media_entry.media_guid = self.episode.rehosted_media_id
        mock_media_create.return_value = mock_media_entry

        from podcasts.tasks import EpisodeProcessor
        processor = EpisodeProcessor(self.episode)

        # Call the private save audio method
        processor._save_processed_audio(
            media_guid=self.episode.rehosted_media_id,
            final_audio_path="/fake/path.mp3",
            file_size=5000
        )

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.duration_seconds, 185)

    def test_rss_feed_renders_itunes_metadata(self):
        """
        Test that the RSS XML feed outputs iTunes compliant author, description, and duration.
        """
        # Set metadata on the models
        self.podcast.author = "Test Author"
        self.podcast.description = "Test Description"
        self.podcast.save()

        self.episode.duration_seconds = 600
        self.episode.save()

        url = reverse("podcast-rss-feed-api", kwargs={"podcast_id": self.podcast.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Verify namespaces and metadata tags exist
        self.assertIn('xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"', content)
        self.assertIn("<itunes:author>Test Author</itunes:author>", content)
        self.assertIn("<itunes:summary>Test Description</itunes:summary>", content)
        self.assertIn("<itunes:subtitle>Ad-free version of Test Podcast</itunes:subtitle>", content)

        # Verify episode specific fields
        self.assertIn("<itunes:duration>600</itunes:duration>", content)
        self.assertIn("<itunes:summary>Ad-free version of Test Episode</itunes:summary>", content)

    def test_rss_feed_http_conditional_caching(self):
        """
        Test that the RSS feed handles ETag and Last-Modified conditional requests correctly.
        """
        self.episode.status = Episode.Status.COMPLETE
        # Set explicit update time
        stuck_time = timezone.now() - timezone.timedelta(days=1)
        Episode.objects.filter(id=self.episode.id).update(updated_at=stuck_time)
        self.episode.refresh_from_db()

        url = reverse("podcast-rss-feed-api", kwargs={"podcast_id": self.podcast.id})
        
        # 1. First request should return 200 with cache headers
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        etag = response.get("ETag")
        last_modified = response.get("Last-Modified")
        
        self.assertIsNotNone(etag)
        self.assertIsNotNone(last_modified)

        # 2. Request with matching If-None-Match should return 304
        response_304_etag = self.client.get(url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(response_304_etag.status_code, 304)

        # 3. Request with matching If-Modified-Since should return 304
        response_304_lm = self.client.get(url, HTTP_IF_MODIFIED_SINCE=last_modified)
        self.assertEqual(response_304_lm.status_code, 304)
