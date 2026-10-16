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

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_head_does_not_trigger_processing(self, mock_thread_class):
        """
        Verify that a HEAD request on a NEW episode does NOT trigger background rehosting
        or change episode status, but returns HTTP 200.
        """
        episode = self.podcast.episodes.create(
            title="Probe Episode",
            guid="probe-1",
            original_audio_url="http://example.com/probe.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
        )
        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.head(url)

        self.assertEqual(response.status_code, 200)  # pylint: disable=no-member
        self.assertEqual(response.headers.get("Content-Type"), "audio/mpeg")
        self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
        mock_thread_class.assert_not_called()

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_crawler_get_does_not_trigger_processing(self, mock_thread_class):
        """
        Verify that a GET request from a bot or crawler (e.g. WordPress.com - Audio/1.0
        or PocketCasts Feed Parser) on a NEW episode does NOT trigger background
        rehosting or change episode status, but returns HTTP 200 with audio headers.
        """
        episode = self.podcast.episodes.create(
            title="Crawler Probe Episode",
            guid="crawler-probe-1",
            original_audio_url="http://example.com/crawler-probe.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
            rehosted_audio_size=93491347,
        )
        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})

        # Test WordPress.com - Audio/1.0 crawler
        response = self.client.get(url, HTTP_USER_AGENT="WordPress.com - Audio/1.0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "audio/mpeg")
        self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(response.headers.get("Content-Length"), "93491347")
        mock_thread_class.assert_not_called()

        # Test Pocket Casts feed parser
        response_pc = self.client.get(
            url,
            HTTP_USER_AGENT="PocketCasts/1.0 (Pocket Casts Feed Parser; +http://pocketcasts.com/)"
        )
        self.assertEqual(response_pc.status_code, 200)
        self.assertEqual(response_pc.headers.get("Content-Length"), "93491347")
        mock_thread_class.assert_not_called()

        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)

    @mock.patch("podcasts.views.threading.Thread")
    def test_serve_rehosted_media_legitimate_player_triggers_processing(self, mock_thread_class):
        """
        Verify that a GET request from a real podcast player (e.g. Pocket Casts app)
        on a NEW episode DOES trigger background rehosting and returns HTTP 202.
        """
        episode = self.podcast.episodes.create(
            title="Mobile Player Episode",
            guid="mobile-player-1",
            original_audio_url="http://example.com/mobile-player.mp3",
            status=Episode.Status.NEW,
            pub_date=timezone.now(),
            rehosted_audio_size=93491347,
        )
        url = reverse("serve_media_episode", kwargs={"media_guid": episode.rehosted_media_id})
        response = self.client.get(url, HTTP_USER_AGENT="Pocket Casts")

        self.assertEqual(response.status_code, 202)
        mock_thread_class.assert_called_once()
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.DOWNLOADING)

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

    def test_episode_status_api_supports_time_window(self):
        """
        Verify EpisodeStatusAPIView returns completed count filtered by time_window.
        """
        now = timezone.now()
        # Ep 1: completed 2 hours ago (within 1d, 1w, 1m)
        _ep1 = Episode.objects.create(
            podcast=self.podcast,
            title="Ep 1d",
            guid="guid-1d",
            pub_date=now - timezone.timedelta(hours=2),
            original_audio_url="http://example.com/ep1.mp3",
            status=Episode.Status.COMPLETE,
        )
        # Ep 2: completed 3 days ago (within 1w, 1m; outside 1d)
        ep2 = Episode.objects.create(
            podcast=self.podcast,
            title="Ep 1w",
            guid="guid-1w",
            pub_date=now - timezone.timedelta(days=3),
            original_audio_url="http://example.com/ep2.mp3",
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.filter(id=ep2.id).update(
            created_at=now - timezone.timedelta(days=3),
            updated_at=now - timezone.timedelta(days=3),
        )
        # Ep 3: completed 15 days ago (within 1m; outside 1d, 1w)
        ep3 = Episode.objects.create(
            podcast=self.podcast,
            title="Ep 1m",
            guid="guid-1m",
            pub_date=now - timezone.timedelta(days=15),
            original_audio_url="http://example.com/ep3.mp3",
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.filter(id=ep3.id).update(
            created_at=now - timezone.timedelta(days=15),
            updated_at=now - timezone.timedelta(days=15),
        )
        # Ep 4: completed 45 days ago (outside 1m)
        ep4 = Episode.objects.create(
            podcast=self.podcast,
            title="Ep Old",
            guid="guid-old",
            pub_date=now - timezone.timedelta(days=45),
            original_audio_url="http://example.com/ep4.mp3",
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.filter(id=ep4.id).update(
            created_at=now - timezone.timedelta(days=45),
            updated_at=now - timezone.timedelta(days=45),
        )

        # Note: self.episode is NEW (pending) from setUp

        url = reverse("episode-status-api")

        # 1d window: only ep1
        resp_1d = self.client.get(url + "?time_window=1d")
        self.assertEqual(resp_1d.status_code, 200)
        self.assertEqual(resp_1d.json()["completed"], 1)
        self.assertEqual(resp_1d.json()["pending"], 1)

        # 1w window: ep1, ep2
        resp_1w = self.client.get(url + "?time_window=1w")
        self.assertEqual(resp_1w.status_code, 200)
        self.assertEqual(resp_1w.json()["completed"], 2)
        self.assertEqual(resp_1w.json()["pending"], 1)

        # 1m window: ep1, ep2, ep3
        resp_1m = self.client.get(url + "?time_window=1m")
        self.assertEqual(resp_1m.status_code, 200)
        self.assertEqual(resp_1m.json()["completed"], 3)
        self.assertEqual(resp_1m.json()["pending"], 1)

        # all window: ep1, ep2, ep3, ep4
        resp_all = self.client.get(url + "?time_window=all")
        self.assertEqual(resp_all.status_code, 200)
        self.assertEqual(resp_all.json()["completed"], 4)
        self.assertEqual(resp_all.json()["pending"], 1)

        # default (no param): ep1, ep2, ep3, ep4
        resp_default = self.client.get(url)
        self.assertEqual(resp_default.status_code, 200)
        self.assertEqual(resp_default.json()["completed"], 4)
        self.assertEqual(resp_default.json()["pending"], 1)

        # invalid param falls back to all
        resp_invalid = self.client.get(url + "?time_window=invalid_val")
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertEqual(resp_invalid.json()["completed"], 4)
        self.assertEqual(resp_invalid.json()["pending"], 1)


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
    Tests for PodcastSubscribeUIView rendering metrics and health dashboard,
    as well as pagination, sorting, and filtering of pending episodes.
    """

    def test_subscribe_ui_renders_health_dashboard(self):
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("metrics", response.context)
        self.assertContains(response, "Processing & AI Health")
        self.assertContains(response, "429 Retry Recovery")

    @override_settings(GEMINI_API_KEYS=[], GEMINI_API_KEY="")
    def test_subscribe_ui_renders_fallback_when_no_keys_configured(self):
        """Verify fallback badge '0 Keys Active' renders when no keys are configured and no telemetry."""
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0 Keys Active")

    def test_subscribe_view_renders_2x2_metrics_grid(self):
        """
        Verify that PodcastSubscribeUIView renders the 2x2 metrics grid cards
        and preserves live status IDs (completed-count and pending-count).
        """
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("metrics", response.context)
        # Check 4 card headers
        self.assertContains(response, "Listener Time Saved")
        self.assertContains(response, "Processing & AI Health")
        self.assertContains(response, "API Keys & Quota Balance")
        self.assertContains(response, "System & Queue Status")
        # Check preserved live status IDs
        self.assertContains(response, 'id="completed-count"')
        self.assertContains(response, 'id="pending-count"')
        self.assertContains(response, "Operational")

    @override_settings(GEMINI_API_KEYS=["...KEY_ALPHA", "...KEY_BETA"], GEMINI_API_KEY="")
    def test_subscribe_view_renders_key_distribution_and_segment_bars(self):
        """
        Verify that key distribution progress bars and ad segment breakdown
        render properly when telemetry data is present.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Telemetry Episode",
            guid="guid-telemetry-1",
            pub_date=timezone.now(),
            original_audio_url="http://example.com/telemetry.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=3600,
            ad_segments=[
                {"start": 0.0, "end": 60.0, "label": "intro"},
                {"start": 500.0, "end": 620.0, "label": "sponsor"},
                {"start": 1000.0, "end": 1030.0, "label": "cta"},
            ],
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "audio_duration_sec": 3810.0,
                        "attempts": [
                            {"model": "gemini-3.8-flash", "key_masked": "...KEY_ALPHA", "status": 429},
                            {"model": "gemini-3.8-flash", "key_masked": "...KEY_ALPHA", "status": "success"},
                            {"model": "gemini-3.8-flash", "key_masked": "...KEY_BETA", "status": "success"},
                        ],
                    },
                    "slicing": {"status": "success", "removed_duration_sec": 210.0},
                },
            },
        )
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        # Verify API keys telemetry rendered
        self.assertContains(response, "...KEY_ALPHA")
        self.assertContains(response, "...KEY_BETA")
        self.assertContains(response, "2 of 2 Keys Used")
        self.assertContains(response, "Total Audio Analyzed")
        # Verify listener time saved and segment breakdown rendered
        self.assertContains(response, "Ad Load")
        self.assertContains(response, "Total ads & filler removed")
        self.assertContains(response, "3 segments removed")

    @override_settings(
        GEMINI_API_KEYS=[
            "AIzaSyPrimary1111111111",
            "AIzaSyBackup2222222222",
            "AIzaSyStandby3333333333",
        ],
        GEMINI_API_KEY="",
    )
    def test_subscribe_view_renders_configured_and_standby_keys(self):
        """
        Verify that Card 3 displays 'X of Y Keys Used' header badge, percentage bars
        for active keys, and 'Standby' badges with 0 calls for standby keys.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Active Key Episode",
            guid="guid-active-key-1",
            pub_date=timezone.now(),
            original_audio_url="http://example.com/audio1.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=1800,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "gemini": {
                        "status": "success",
                        "attempts": [
                            {
                                "model": "gemini-3.8-flash",
                                "key_masked": "AIzaSy...1111",
                                "status": "success",
                            },
                        ],
                    },
                },
            },
        )
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)

        # Header badge: 1 of 3 Keys Used
        self.assertContains(response, "1 of 3 Keys Used")

        # Active key displays call percentage badge (100.0%) and calls
        self.assertContains(response, "AIzaSy...1111")
        self.assertContains(response, "100.0%")
        self.assertContains(response, "1 calls")

        # Standby keys display Standby badge and 0 calls (Standby)
        self.assertContains(response, "AIzaSy...2222")
        self.assertContains(response, "AIzaSy...3333")
        self.assertContains(response, '<span class="badge bg-secondary text-white">Standby</span>')
        self.assertContains(response, "0 calls (Standby)")

    def test_subscribe_ui_renders_time_window_filter(self):
        """
        Verify that the main UI page renders filter buttons for 'All Time', 'Last 1d',
        'Last 1w', and 'Last 1m' with correct links and active state.
        """
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("time_window", response.context)
        self.assertIn("time_window_urls", response.context)
        self.assertEqual(response.context["time_window"], "all")

        # Check button group header and buttons
        self.assertContains(response, "Service Metrics")
        self.assertContains(response, 'aria-label="Metrics Time Window Filter"')
        self.assertContains(response, "All Time")
        self.assertContains(response, "Last 1d")
        self.assertContains(response, "Last 1w")
        self.assertContains(response, "Last 1m")

        # Active state: "All Time" should be active, others outline-secondary
        self.assertContains(
            response,
            '<a href="?" class="btn btn-primary active fw-semibold">All Time</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="?time_window=1d" class="btn btn-outline-secondary">Last 1d</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="?time_window=1w" class="btn btn-outline-secondary">Last 1w</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="?time_window=1m" class="btn btn-outline-secondary">Last 1m</a>',
            html=True,
        )

    def test_subscribe_ui_filters_metrics_by_time_window(self):
        """
        Verify GET with ?time_window=1d passes '1d' to MetricsService and marks
        'Last 1d' button active, and that invalid time_window falls back to 'all'.
        """
        now = timezone.now()
        # Episode 1: within 1d
        Episode.objects.create(
            podcast=self.podcast,
            title="Recent Complete Episode",
            guid="recent-complete-1",
            pub_date=now - timezone.timedelta(hours=2),
            original_audio_url="http://example.com/recent.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=1200,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "gemini": {
                        "status": "success",
                        "audio_duration_sec": 1200.0,
                    },
                    "slicing": {"status": "success", "removed_duration_sec": 120.0},
                },
            },
        )
        # Episode 2: older than 1d (e.g. 3 days ago)
        old_ep = Episode.objects.create(
            podcast=self.podcast,
            title="Older Complete Episode",
            guid="older-complete-2",
            pub_date=now - timezone.timedelta(days=3),
            original_audio_url="http://example.com/older.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=1800,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "gemini": {
                        "status": "success",
                        "audio_duration_sec": 1800.0,
                    },
                    "slicing": {"status": "success", "removed_duration_sec": 180.0},
                },
            },
        )
        Episode.objects.filter(id=old_ep.id).update(
            created_at=now - timezone.timedelta(days=3),
            updated_at=now - timezone.timedelta(days=3),
        )

        # GET with ?time_window=1d
        response = self.client.get(reverse("podcast-subscribe-ui") + "?time_window=1d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["time_window"], "1d")
        # 1d metrics should only count the recent episode
        self.assertEqual(response.context["metrics"]["completed_episodes"], 1)

        # Button active state: Last 1d is active, All Time is outline
        self.assertContains(
            response,
            '<a href="?time_window=1d" class="btn btn-primary active fw-semibold">Last 1d</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a href="?" class="btn btn-outline-secondary">All Time</a>',
            html=True,
        )

        # GET with invalid time_window should fall back to 'all'
        response_invalid = self.client.get(reverse("podcast-subscribe-ui") + "?time_window=invalid_period")
        self.assertEqual(response_invalid.status_code, 200)
        self.assertEqual(response_invalid.context["time_window"], "all")
        # All time should count both episodes
        self.assertEqual(response_invalid.context["metrics"]["completed_episodes"], 2)
        self.assertContains(
            response_invalid,
            '<a href="?" class="btn btn-primary active fw-semibold">All Time</a>',
            html=True,
        )

    def test_subscribe_ui_preserves_query_params_on_time_window_change(self):
        """
        Verify that existing filters (status, podcast_id, q, sort, order) are preserved
        in time_window_urls while page is reset, and sort/pagination links retain time_window.
        """
        # Create enough pending episodes to have multiple pages
        base_time = timezone.now()
        for i in range(12):
            Episode.objects.create(
                podcast=self.podcast,
                title=f"Sample Match {i+1}",
                guid=f"sample-guid-{i+1}",
                original_audio_url=f"http://example.com/s{i+1}.mp3",
                pub_date=base_time + timezone.timedelta(minutes=i),
                status=Episode.Status.DOWNLOADING,
            )

        query = (
            f"?q=Sample&status=DOWNLOADING&podcast_id={self.podcast.id}"
            f"&sort=title&order=asc&page=2&time_window=1w"
        )
        response = self.client.get(reverse("podcast-subscribe-ui") + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["time_window"], "1w")

        urls = response.context["time_window_urls"]
        # Verify page is dropped in all time_window_urls
        for tw, url in urls.items():
            self.assertNotIn("page=", url)
            self.assertIn("q=Sample", url)
            self.assertIn("status=DOWNLOADING", url)
            self.assertIn(f"podcast_id={self.podcast.id}", url)
            self.assertIn("sort=title", url)
            self.assertIn("order=asc", url)
            if tw == "all":
                self.assertNotIn("time_window=", url)
            else:
                self.assertIn(f"time_window={tw}", url)

        # Check sort_urls preserve time_window=1w
        sort_urls = response.context["sort_urls"]
        for col_info in sort_urls.values():
            self.assertIn("time_window=1w", col_info["url"])

        # Check pagination links retain time_window=1w
        self.assertContains(response, "time_window=1w")

    def test_pending_episodes_pagination(self):
        """
        Verify that pending episodes are paginated (10 per page by default).
        """
        # Create 14 additional pending episodes (we already have 1 from setUp)
        base_time = timezone.now()
        for i in range(14):
            Episode.objects.create(
                podcast=self.podcast,
                title=f"Pending Episode {i+2:02d}",
                guid=f"guid-pending-{i+2}",
                original_audio_url=f"http://example.com/ep{i+2}.mp3",
                pub_date=base_time + timezone.timedelta(minutes=i + 1),
                status=Episode.Status.NEW,
            )

        # Page 1
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        pending_page = response.context["pending_episodes"]
        self.assertEqual(len(pending_page), 10)
        self.assertTrue(pending_page.has_next())
        self.assertFalse(pending_page.has_previous())
        self.assertEqual(pending_page.paginator.num_pages, 2)
        self.assertEqual(pending_page.paginator.count, 15)

        # Page 2
        response_p2 = self.client.get(reverse("podcast-subscribe-ui") + "?page=2")
        self.assertEqual(response_p2.status_code, 200)
        pending_page_2 = response_p2.context["pending_episodes"]
        self.assertEqual(len(pending_page_2), 5)
        self.assertFalse(pending_page_2.has_next())
        self.assertTrue(pending_page_2.has_previous())

        # Out-of-bounds page should return the last page
        response_invalid = self.client.get(reverse("podcast-subscribe-ui") + "?page=999")
        self.assertEqual(response_invalid.status_code, 200)
        pending_page_invalid = response_invalid.context["pending_episodes"]
        self.assertEqual(pending_page_invalid.number, 2)

    def test_pending_episodes_filter_by_status(self):
        """
        Verify filtering pending episodes by specific pending status.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Downloading Episode",
            guid="guid-dl",
            original_audio_url="http://example.com/dl.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.DOWNLOADING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Processing Episode",
            guid="guid-proc",
            original_audio_url="http://example.com/proc.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.PROCESSING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Completed Episode",
            guid="guid-done",
            original_audio_url="http://example.com/done.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.COMPLETE,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?status=DOWNLOADING")
        self.assertEqual(response.status_code, 200)
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].title, "Downloading Episode")

    def test_episodes_filter_default_shows_pending_statuses(self):
        """
        When no status is provided, only pending episodes are included;
        complete and failed episodes are excluded.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Downloading Ep",
            guid="guid-dl-default",
            original_audio_url="http://example.com/dl.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.DOWNLOADING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Complete Ep",
            guid="guid-complete-default",
            original_audio_url="http://example.com/complete.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Failed Ep",
            guid="guid-failed-default",
            original_audio_url="http://example.com/failed.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.FAILED,
        )

        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_status"], "")
        self.assertIn("all_statuses", response.context)
        self.assertIn("total_episodes_count", response.context)
        self.assertEqual(response.context["total_episodes_count"], 4)
        self.assertEqual(response.context["total_pending_count"], 2)
        episodes = list(response.context["pending_episodes"])
        titles = [e.title for e in episodes]
        self.assertIn("Test Episode", titles)
        self.assertIn("Downloading Ep", titles)
        self.assertNotIn("Complete Ep", titles)
        self.assertNotIn("Failed Ep", titles)
        self.assertIn("episodes", response.context)
        self.assertEqual(list(response.context["episodes"]), episodes)

    def test_episodes_filter_all_statuses(self):
        """
        When ?status=all is passed, episodes with status NEW, DOWNLOADING, COMPLETE,
        and FAILED are all returned in the page.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Downloading Ep",
            guid="guid-dl-all",
            original_audio_url="http://example.com/dl.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.DOWNLOADING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Complete Ep",
            guid="guid-complete-all",
            original_audio_url="http://example.com/complete.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Failed Ep",
            guid="guid-failed-all",
            original_audio_url="http://example.com/failed.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.FAILED,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?status=all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_status"], "all")
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 4)
        statuses = {e.status for e in episodes}
        self.assertEqual(
            statuses,
            {Episode.Status.NEW, Episode.Status.DOWNLOADING, Episode.Status.COMPLETE, Episode.Status.FAILED},
        )
        self.assertEqual(list(response.context["episodes"]), episodes)

    def test_episodes_filter_complete_status(self):
        """
        When ?status=COMPLETE is passed, only COMPLETE episodes are returned.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Downloading Ep",
            guid="guid-dl-comp",
            original_audio_url="http://example.com/dl.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.DOWNLOADING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Complete Ep",
            guid="guid-complete-comp",
            original_audio_url="http://example.com/complete.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Failed Ep",
            guid="guid-failed-comp",
            original_audio_url="http://example.com/failed.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.FAILED,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?status=COMPLETE")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_status"], "COMPLETE")
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].title, "Complete Ep")
        self.assertEqual(episodes[0].status, Episode.Status.COMPLETE)
        self.assertEqual(list(response.context["episodes"]), episodes)

    def test_episodes_filter_failed_status(self):
        """
        When ?status=FAILED is passed, only FAILED episodes are returned.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Downloading Ep",
            guid="guid-dl-fail",
            original_audio_url="http://example.com/dl.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.DOWNLOADING,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Complete Ep",
            guid="guid-complete-fail",
            original_audio_url="http://example.com/complete.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.COMPLETE,
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Failed Ep",
            guid="guid-failed-fail",
            original_audio_url="http://example.com/failed.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.FAILED,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?status=FAILED")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_status"], "FAILED")
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].title, "Failed Ep")
        self.assertEqual(episodes[0].status, Episode.Status.FAILED)
        self.assertEqual(list(response.context["episodes"]), episodes)

    def test_pending_episodes_filter_by_podcast(self):
        """
        Verify filtering pending episodes by podcast ID.
        """
        podcast2 = Podcast.objects.create(title="Second Podcast", rss_url="http://example.com/p2.xml")
        Episode.objects.create(
            podcast=podcast2,
            title="Second Podcast Pending Episode",
            guid="guid-p2-ep",
            original_audio_url="http://example.com/p2ep.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.NEW,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + f"?podcast_id={podcast2.id}")
        self.assertEqual(response.status_code, 200)
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].podcast.id, podcast2.id)

    def test_pending_episodes_filter_by_search(self):
        """
        Verify searching pending episodes by episode title.
        """
        Episode.objects.create(
            podcast=self.podcast,
            title="Kubernetes Deep Dive",
            guid="guid-k8s",
            original_audio_url="http://example.com/k8s.mp3",
            pub_date=timezone.now(),
            status=Episode.Status.NEW,
        )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?q=Kubernetes")
        self.assertEqual(response.status_code, 200)
        episodes = list(response.context["pending_episodes"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].title, "Kubernetes Deep Dive")

    def test_pending_episodes_sorting(self):
        """
        Verify sorting pending episodes by title and pub_date ascending/descending.
        """
        now = timezone.now()
        Episode.objects.all().delete()

        ep_a = Episode.objects.create(
            podcast=self.podcast,
            title="Alpha Episode",
            guid="guid-a",
            original_audio_url="http://example.com/a.mp3",
            pub_date=now - timezone.timedelta(days=2),
            status=Episode.Status.NEW,
        )
        ep_b = Episode.objects.create(
            podcast=self.podcast,
            title="Beta Episode",
            guid="guid-b",
            original_audio_url="http://example.com/b.mp3",
            pub_date=now - timezone.timedelta(days=1),
            status=Episode.Status.DOWNLOADING,
        )

        # Sort by title desc
        res_title_desc = self.client.get(reverse("podcast-subscribe-ui") + "?sort=title&order=desc")
        episodes = list(res_title_desc.context["pending_episodes"])
        self.assertEqual([e.id for e in episodes], [ep_b.id, ep_a.id])

        # Sort by title asc
        res_title_asc = self.client.get(reverse("podcast-subscribe-ui") + "?sort=title&order=asc")
        episodes = list(res_title_asc.context["pending_episodes"])
        self.assertEqual([e.id for e in episodes], [ep_a.id, ep_b.id])

        # Sort by pub_date desc
        res_date_desc = self.client.get(reverse("podcast-subscribe-ui") + "?sort=pub_date&order=desc")
        episodes = list(res_date_desc.context["pending_episodes"])
        self.assertEqual([e.id for e in episodes], [ep_b.id, ep_a.id])

        # Invalid sort key falls back to default
        res_invalid = self.client.get(reverse("podcast-subscribe-ui") + "?sort=hacked_column&order=asc")
        self.assertEqual(res_invalid.status_code, 200)
        episodes = list(res_invalid.context["pending_episodes"])
        self.assertEqual([e.id for e in episodes], [ep_a.id, ep_b.id])

    def test_pending_episodes_template_rendering(self):
        """
        Verify template renders the table, sort headers, filter toolbar, and pagination.
        """
        base_time = timezone.now()
        for i in range(14):
            Episode.objects.create(
                podcast=self.podcast,
                title=f"Pending Ep {i+2:02d}",
                guid=f"guid-render-{i+2}",
                original_audio_url=f"http://example.com/ep{i+2}.mp3",
                pub_date=base_time + timezone.timedelta(minutes=i + 1),
                status=Episode.Status.DOWNLOADING,
            )

        response = self.client.get(reverse("podcast-subscribe-ui") + "?q=Pending&sort=title&order=asc")
        self.assertEqual(response.status_code, 200)
        # Check toolbar presence
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'name="podcast_id"')
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'value="Pending"')
        # Check sort links and indicators
        self.assertContains(response, 'sort=title&amp;order=desc')
        # Check pagination links include query params
        self.assertContains(response, 'page=2')
        self.assertContains(response, 'q=Pending')
        self.assertContains(response, 'sort=title')
        # Check status badge
        self.assertContains(response, "Downloading")
        # Check podcast link
        self.assertContains(response, reverse("podcast-status-ui", args=[self.podcast.id]))

    def test_pending_episodes_empty_filter_state(self):
        """
        Verify empty message displayed when filters yield no matches.
        """
        response = self.client.get(reverse("podcast-subscribe-ui") + "?q=NonExistentEpisode123")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pending episodes match the selected filter criteria.")
        self.assertContains(response, "Reset Filters")

    def test_pending_episodes_card_hidden_when_no_pending_and_no_filter(self):
        """
        Verify pending episodes card is hidden when there are 0 pending episodes.
        """
        Episode.objects.all().delete()
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "pending-episodes-panel")


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

    def test_podcast_status_ui_audio_tag_only_rendered_for_complete_episodes(self):
        """
        Verify that <audio> player is only rendered when episode status is COMPLETE,
        and not rendered when status is NEW or FAILED.
        """
        # Status NEW: should NOT contain <audio controls
        self.episode.status = Episode.Status.NEW
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<audio controls")

        # Status COMPLETE: should contain <audio controls
        self.episode.status = Episode.Status.COMPLETE
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<audio controls")
