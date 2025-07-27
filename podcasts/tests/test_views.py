"""
Tests for the podcast views.
"""
import os
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from podcasts.models import Episode, Podcast


@override_settings(REHOST_BASE_URL="http://localhost:12343")
class PodcastURLGenerationTest(TestCase):
    """
    Test cases for URL generation in views.
    """

    def setUp(self):
        """
        Set up test data.
        """
        self.podcast = Podcast.objects.create(
            title="Test Podcast", rss_url="http://example.com/feed.xml"
        )

    def test_opml_export_view(self):
        """
        Test that the OPMLExportView generates the correct rehosted_rss_url.
        """
        url = reverse("opml-export")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        expected_url = f"http://localhost:12343{reverse('podcast-rss-feed-api', args=[self.podcast.id])}"
        self.assertContains(response, f'xmlUrl="{expected_url}"')

    def test_podcast_rss_feed_view(self):
        """
        Test that the PodcastRSSFeedView generates the correct rehosted_rss_url in the context.
        """
        url = reverse("podcast-rss-feed-api", kwargs={"podcast_id": self.podcast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        expected_url = f"http://localhost:12343{reverse('podcast-rss-feed-api', args=[self.podcast.id])}"
        self.assertContains(response, f'<atom:link href="{expected_url}"')

    def test_podcast_status_ui_view(self):
        """
        Test that the PodcastStatusUIView generates the correct rehosted_rss_url in the context.
        """
        url = reverse("podcast-status-ui", kwargs={"podcast_id": self.podcast.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        expected_url = f"http://localhost:12343{reverse('podcast-rss-feed-api', args=[self.podcast.id])}"
        self.assertContains(response, f'<a href="{expected_url}" target="_blank"')


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class PodcastViewsTest(TestCase):
    """
    Test cases for the podcast views.
    """

    def setUp(self):
        """
        Set up test data.
        """
        self.client = APIClient()
        self.podcast = Podcast.objects.create(  # pylint: disable=no-member
            title="Test Podcast", rss_url="http://example.com/feed.xml"
        )

    def tearDown(self):
        """
        Clean up test media files.
        """
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            os.rmdir(media_root)

    @mock.patch("podcasts.views.create_podcast_from_url")
    def test_podcast_subscription_api_view_post_success(self, mock_create_podcast):
        """
        Test successful podcast subscription via API.
        """
        response = self.client.post(
            reverse("podcast-subscribe-api"),
            {"rss_url": "http://example.com/new.xml"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_podcast.assert_called_once()

    @mock.patch(
        "podcasts.views.create_podcast_from_url", side_effect=ValueError("Invalid Feed")
    )
    def test_podcast_subscription_api_view_post_invalid_feed(
        self, mock_create_podcast
    ):  # pylint: disable=W0613
        """
        Test podcast subscription with an invalid feed URL.
        """
        response = self.client.post(
            reverse("podcast-subscribe-api"),
            {"rss_url": "http://invalid.com/feed.xml"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # mock_create_podcast is used in the side_effect, so it's not unused.
        # The pylint warning W0613 is a false positive here.

    @mock.patch("podcasts.views.threading.Thread")
    def test_podcast_delete_api_view(self, mock_thread):
        """
        Test podcast deletion via API.
        """
        url = reverse("podcast-delete-api", kwargs={"podcast_id": self.podcast.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_thread.assert_called_once()
        self.assertTrue(
            Podcast.objects.filter(id=self.podcast.id).exists()
        )  # pylint: disable=no-member

    @mock.patch("podcasts.views.rehost_episode_audio")
    def test_episode_reprocess_view(self, mock_rehost_episode_audio):
        """
        Test reprocessing a single episode.
        """
        episode = self.podcast.episodes.create(
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.FAILED,
            pub_date=timezone.now(),
        )
        url = reverse("episode-reprocess", kwargs={"episode_id": episode.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)
        mock_rehost_episode_audio.assert_called_once_with(episode.id)
