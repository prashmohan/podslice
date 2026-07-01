"""
Tests for the podcast views.
"""
import os
import threading
import time
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import TestCase, override_settings, TransactionTestCase
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
        mock_create_podcast.assert_called_once_with("http://example.com/new.xml", download_all=False)

    @mock.patch("podcasts.views.create_podcast_from_url")
    def test_podcast_subscription_api_view_post_download_all(self, mock_create_podcast):
        """
        Test successful podcast subscription via API with download_all.
        """
        response = self.client.post(
            reverse("podcast-subscribe-api"),
            {"rss_url": "http://example.com/new.xml", "download_all": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_podcast.assert_called_once_with("http://example.com/new.xml", download_all=True)

    @mock.patch("podcasts.views.create_podcast_from_url")
    def test_podcast_subscription_ui_view_post_download_all(self, mock_create_podcast):
        """
        Test successful podcast subscription via UI with download_all.
        """
        mock_create_podcast.return_value = self.podcast
        response = self.client.post(
            reverse("podcast-subscribe-ui"),
            {"rss_url": "http://example.com/new.xml", "download_all": "on"},
        )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        mock_create_podcast.assert_called_once_with("http://example.com/new.xml", download_all=True)

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
        old_uuid = episode.rehosted_media_id
        url = reverse("episode-reprocess", kwargs={"episode_id": episode.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)
        self.assertEqual(episode.rehosted_media_id, old_uuid)
        mock_rehost_episode_audio.assert_not_called()

    def test_podcast_reprocess_view(self):
        """
        Test reprocessing a podcast.
        """
        episode = self.podcast.episodes.create(
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.FAILED,
            pub_date=timezone.now(),
        )
        old_uuid = episode.rehosted_media_id
        url = reverse("podcast-reprocess", kwargs={"podcast_id": self.podcast.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)
        self.assertEqual(episode.rehosted_media_id, old_uuid)

    @mock.patch("podcasts.tasks.EpisodeProcessor")
    def test_serve_rehosted_media_new_status_success(self, mock_processor_class):
        """
        Test that accessing a NEW status episode triggers rehost_audio synchronously.
        """
        episode = self.podcast.episodes.create(
            title="On Demand Episode",
            guid="ondemand-1",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
        )
        
        def mock_rehost():
            episode.status = Episode.Status.COMPLETE
            episode.save()
            from podcasts.models import RehostedMedia
            media_item = RehostedMedia.objects.create(
                media_guid=episode.rehosted_media_id,
                file_path=os.path.join(settings.MEDIA_ROOT, "test.mp3"),
                content_type="audio/mpeg"
            )
            os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
            with open(media_item.file_path, "wb") as f:
                f.write(b"fake audio data")

        mock_processor_instance = mock.Mock()
        mock_processor_instance.rehost_audio.side_effect = mock_rehost
        mock_processor_class.return_value = mock_processor_instance

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.getvalue(), b"fake audio data")
        
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.COMPLETE)
        mock_processor_class.assert_called_once_with(episode)

    def test_serve_rehosted_media_complete_status(self):
        """
        Test that accessing a COMPLETE status episode serves it directly without reprocessing.
        """
        episode = self.podcast.episodes.create(
            title="Complete Episode",
            guid="complete-1",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.COMPLETE,
            pub_date=timezone.now(),
        )
        from podcasts.models import RehostedMedia
        media_item = RehostedMedia.objects.create(
            media_guid=episode.rehosted_media_id,
            file_path=os.path.join(settings.MEDIA_ROOT, "complete.mp3"),
            content_type="audio/mpeg"
        )
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        with open(media_item.file_path, "wb") as f:
            f.write(b"already processed audio")

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.getvalue(), b"already processed audio")


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class PodcastViewsConcurrencyTest(TransactionTestCase):
    """
    Concurrency tests for serve_rehosted_media.
    """

    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            os.rmdir(media_root)

    @mock.patch("podcasts.tasks.EpisodeProcessor")
    def test_serve_rehosted_media_concurrency(self, mock_processor_class):
        """
        Test that concurrent requests to serve a NEW episode block and only trigger processing once.
        """
        podcast = Podcast.objects.create(
            title="Concurrent Podcast", rss_url="http://example.com/feed.xml"
        )
        episode = podcast.episodes.create(
            title="Concurrent Episode",
            guid="concurrent-1",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
        )

        import queue
        
        rehost_start_event = threading.Event()
        rehost_finish_event = threading.Event()
        call_count = 0
        call_count_lock = threading.Lock()

        def mock_rehost():
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            rehost_start_event.set()
            rehost_finish_event.wait(timeout=10)
            
            episode.status = Episode.Status.COMPLETE
            episode.save()
            from podcasts.models import RehostedMedia
            media_item = RehostedMedia.objects.create(
                media_guid=episode.rehosted_media_id,
                file_path=os.path.join(settings.MEDIA_ROOT, "concurrent.mp3"),
                content_type="audio/mpeg"
            )
            os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
            with open(media_item.file_path, "wb") as f:
                f.write(b"concurrent audio")

        mock_processor_instance = mock.Mock()
        mock_processor_instance.rehost_audio.side_effect = mock_rehost
        mock_processor_class.return_value = mock_processor_instance

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        
        results = queue.Queue()

        def make_request():
            connection.close()
            try:
                response = self.client.get(url)
                results.put((response.status_code, response.getvalue()))
            except Exception as e:
                results.put(e)
            finally:
                connection.close()

        # Start first thread (which will trigger rehost and wait)
        t1 = threading.Thread(target=make_request)
        t1.start()

        # Wait for the rehost to start
        rehost_start_event.wait(timeout=5)

        # Start second thread (which should enter the sleep-and-poll loop since status is DOWNLOADING)
        t2 = threading.Thread(target=make_request)
        t2.start()

        # Let some time pass to ensure the second thread has entered the loop and polled
        time.sleep(1)

        # Let the rehost finish
        rehost_finish_event.set()

        # Wait for both threads to finish
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Verify calls and results
        self.assertEqual(call_count, 1)
        res1 = results.get()
        res2 = results.get()
        
        self.assertEqual(res1, (200, b"concurrent audio"))
        self.assertEqual(res2, (200, b"concurrent audio"))
