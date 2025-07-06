"""
Tests for the podcast views.
"""
import os
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from podcasts.models import Podcast


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class PodcastViewsTest(TestCase):
    """
    Test cases for the podcast views.
    """
    def setUp(self):
        """
        Set up test data.
        """
        self.client = APIClient()
        self.podcast = Podcast.objects.create( # pylint: disable=no-member
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

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_podcast_subscription_api_view_post_success(self, mock_create_podcast):
        """
        Test successful podcast subscription via API.
        """
        response = self.client.post(
            reverse('podcast-subscribe-api'),
            {'rss_url': 'http://example.com/new.xml'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_podcast.assert_called_once()

    @mock.patch('podcasts.views.create_podcast_from_url',
                side_effect=ValueError("Invalid Feed"))
    def test_podcast_subscription_api_view_post_invalid_feed(self, mock_create_podcast): # pylint: disable=W0613
        """
        Test podcast subscription with an invalid feed URL.
        """
        response = self.client.post(
            reverse('podcast-subscribe-api'),
            {'rss_url': 'http://invalid.com/feed.xml'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # mock_create_podcast is used in the side_effect, so it's not unused.
        # The pylint warning W0613 is a false positive here.

    @mock.patch('podcasts.views.threading.Thread')
    def test_podcast_delete_api_view(self, mock_thread):
        """
        Test podcast deletion via API.
        """
        url = reverse('podcast-delete-api', kwargs={'podcast_id': self.podcast.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_thread.assert_called_once()
        self.assertTrue(Podcast.objects.filter(id=self.podcast.id).exists()) # pylint: disable=no-member
