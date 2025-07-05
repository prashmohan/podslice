import os
from unittest import mock
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from ..models import Podcast
from django.conf import settings

@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class PodcastViewsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/feed.xml")

    def tearDown(self):
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            os.rmdir(media_root)

    @mock.patch('podcasts.views.create_podcast_from_url')
    def test_podcast_subscription_api_view_post_success(self, mock_create_podcast):
        response = self.client.post(reverse('podcast-subscribe-api'), {'rss_url': 'http://example.com/new.xml'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_podcast.assert_called_once()

    @mock.patch('podcasts.views.create_podcast_from_url', side_effect=ValueError("Invalid Feed"))
    def test_podcast_subscription_api_view_post_invalid_feed(self, mock_create_podcast):
        response = self.client.post(reverse('podcast-subscribe-api'), {'rss_url': 'http://invalid.com/feed.xml'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch('podcasts.views.threading.Thread')
    def test_podcast_delete_api_view(self, mock_thread):
        url = reverse('podcast-delete-api', kwargs={'podcast_id': self.podcast.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_thread.assert_called_once()
        self.assertTrue(Podcast.objects.filter(id=self.podcast.id).exists())

    def test_opml_export_view(self):
        # Create a second podcast to ensure multiple podcasts are exported
        Podcast.objects.create(title="Another Podcast", rss_url="http://example.com/another_feed.xml")

        response = self.client.get(reverse('opml-export'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn(b'<?xml version="1.0" encoding="UTF-8"?>', response.content)
        self.assertIn(b'<opml version="2.0">', response.content)
        self.assertIn(b'<title>Podslice Subscriptions</title>', response.content)
        self.assertIn(b'<outline text="Test Podcast" title="Test Podcast" type="rss"', response.content)
        expected_url_podcast1 = b'xmlUrl="http://testserver/api/rss/' + str(self.podcast.id).encode() + b'/rss.xml"'
        self.assertIn(expected_url_podcast1, response.content)

        # For the second podcast, we'll find its ID and then construct the expected URL
        podcast2 = Podcast.objects.get(title="Another Podcast")
        expected_url_podcast2 = b'xmlUrl="http://testserver/api/rss/' + str(podcast2.id).encode() + b'/rss.xml"'
        self.assertIn(expected_url_podcast2, response.content)
