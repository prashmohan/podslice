"""
Tests for the podcast views.
"""
import os
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings, TransactionTestCase
from podcasts.tests.test_base import PodcastTestCase
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

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_new_status_success(self, mock_thread_class):
        """
        Test that accessing a NEW status episode triggers background rehosting and returns 202.
        """
        episode = self.podcast.episodes.create(
            title="On Demand Episode",
            guid="ondemand-1",
            original_audio_url="http://example.com/episode.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
        )

        mock_thread_instance = mock.Mock()
        mock_thread_class.return_value = mock_thread_instance

        # Capture the old updated_at
        old_updated_at = episode.updated_at

        # Sleep briefly to guarantee timestamp difference on extremely fast execution
        import time
        time.sleep(0.001)

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get("Retry-After"), "30")
        self.assertIn(b"Episode processing has been initiated", response.content)

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.DOWNLOADING)
        self.assertGreater(episode.updated_at, old_updated_at)
        mock_thread_class.assert_called_once_with(
            target=mock.ANY,
            args=(episode.id,),
            kwargs={"force": True}
        )

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
    Non-blocking / Concurrency tests for serve_rehosted_media.
    """

    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            os.rmdir(media_root)

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_concurrency(self, mock_thread_class):
        """
        Test that accessing a NEW episode triggers processing and returns 202,
        and subsequent requests during processing also return 202 immediately.
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

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        
        # Request 1: should initiate processing and return 202
        response1 = self.client.get(url)
        self.assertEqual(response1.status_code, 202)
        mock_thread_class.assert_called_once()

        # Request 2: status is now DOWNLOADING. Should return 202 directly without starting another thread
        mock_thread_class.reset_mock()
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, 202)
        mock_thread_class.assert_not_called()


class AdditionalPodcastViewsTest(PodcastTestCase):
    """
    Test cases for previously untested views and edge cases.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_podcast_subscribe_ui_view_get(self):
        """Test GET request to subscribe UI."""
        url = reverse("podcast-subscribe-ui")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Podcast")

    def test_opml_import_view_get(self):
        """Test GET request to OPML import view."""
        url = reverse("opml-import")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @mock.patch("podcasts.views.create_podcast_from_url")
    def test_opml_import_view_post_success(self, mock_create):
        """Test uploading a valid OPML file."""
        opml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <opml version="1.0">
            <head><title>Test OPML</title></head>
            <body>
                <outline text="My Podcasts">
                    <outline type="rss" xmlUrl="http://example.com/rss1.xml" />
                    <outline type="rss" xmlUrl="http://example.com/rss2.xml" />
                </outline>
            </body>
        </opml>"""
        
        from django.core.files.uploadedfile import SimpleUploadedFile
        opml_file = SimpleUploadedFile("test.opml", opml_content.encode("utf-8"), content_type="text/xml")
        
        url = reverse("opml-import")
        response = self.client.post(url, {"opml_file": opml_file})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_create.call_count, 2)
        mock_create.assert_any_call("http://example.com/rss1.xml")
        mock_create.assert_any_call("http://example.com/rss2.xml")

    def test_opml_import_view_post_invalid(self):
        """Test uploading an invalid OPML file."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        invalid_file = SimpleUploadedFile("test.opml", b"invalid xml", content_type="text/xml")
        
        url = reverse("opml-import")
        response = self.client.post(url, {"opml_file": invalid_file})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid OPML file")

    def test_opml_backup_view(self):
        """Test OPML backup view renders original RSS URLs."""
        url = reverse("opml-backup")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "http://example.com/rss")

    def test_podcast_update_settings_view(self):
        """Test updating podcast settings."""
        url = reverse("podcast-update-settings", kwargs={"podcast_id": self.podcast.id})
        response = self.client.post(url, {"title": "Updated Title", "rss_url": self.podcast.rss_url, "max_episodes": 25})
        self.assertEqual(response.status_code, 302)
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.max_episodes, 25)

    @mock.patch("podcasts.views.threading.Thread")
    def test_podcast_refresh_view(self, mock_thread):
        """Test triggering podcast refresh."""
        url = reverse("podcast-refresh", kwargs={"podcast_id": self.podcast.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        mock_thread.assert_called_once()

    def test_episode_status_api_view(self):
        """Test episode status counts API."""
        # 1 pending (NEW), 0 completed
        url = reverse("episode-status-api")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["completed"], 0)
        self.assertEqual(data["pending"], 1)

        # change to COMPLETE
        self.episode.status = Episode.Status.COMPLETE
        self.episode.save()
        response = self.client.get(url)
        data = response.json()
        self.assertEqual(data["completed"], 1)
        self.assertEqual(data["pending"], 0)

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_stuck_reset(self, mock_thread_class):
        """
        Test that requesting media for a stuck episode resets it to NEW and triggers rehosting in background.
        """
        episode = self.episode
        stuck_time = timezone.now() - timezone.timedelta(minutes=25)
        Episode.objects.filter(id=episode.id).update(
            status=Episode.Status.DOWNLOADING,
            updated_at=stuck_time
        )
        episode.refresh_from_db()

        mock_thread_instance = mock.Mock()
        mock_thread_class.return_value = mock_thread_instance

        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get("Retry-After"), "30")

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.DOWNLOADING)
        mock_thread_class.assert_called_once()

    def test_serve_rehosted_media_not_found_raises_404(self):
        """
        Test that accessing a non-existent media GUID raises a 404.
        """
        import uuid
        url = reverse("serve_media_episode", kwargs={"media_guid": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class PodcastSubscribeUIViewTest(PodcastTestCase):
    """
    Tests for PodcastSubscribeUIView rendering metrics and health dashboard.
    """

    def test_subscribe_ui_renders_health_dashboard(self):
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("metrics", response.context)
        self.assertContains(response, "Processing & AI Health")
        self.assertContains(response, "429 Retry Recovery")


class PodcastStatusUIViewTest(PodcastTestCase):
    """
    Tests for PodcastStatusUIView rendering episode telemetry badges.
    """

    def test_podcast_status_ui_renders_episode_telemetry(self):
        self.episode.processing_metrics = {
            "status": "COMPLETE",
            "total_duration_sec": 7.5,
            "stages": {
                "gemini": {
                    "model_used": "gemini-3.8-flash",
                    "retry_recovered": True,
                }
            },
        }
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gemini-3.8-flash")
        self.assertContains(response, "429 Recovered")
        self.assertContains(response, "Processed in 7.5s")

    def test_podcast_status_ui_renders_fallback_model_badge(self):
        self.episode.processing_metrics = {
            "status": "COMPLETE",
            "total_duration_sec": 5.2,
            "stages": {
                "gemini": {
                    "model_used": "gemini-3.5-flash-lite",
                    "fallback_used": True,
                }
            },
        }
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gemini-3.5-flash-lite")
        self.assertContains(response, "bg-warning")

    def test_podcast_status_ui_renders_failed_stage_badge(self):
        self.episode.status = Episode.Status.FAILED
        self.episode.processing_metrics = {
            "status": "FAILED",
            "stages": {
                "download": {"status": "failed", "error": "HTTP 404"},
            },
        }
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed at Download")

    def test_podcast_status_ui_renders_without_processing_metrics(self):
        self.episode.processing_metrics = None
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "429 Recovered")

