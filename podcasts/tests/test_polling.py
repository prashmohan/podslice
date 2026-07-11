"""
Tests for the polling functionality in the podcasts app.
"""
import os
import threading
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from podcasts.tests.test_base import PodcastTestCase
from podcasts.apps import polling_loop
from podcasts.models import Episode
from podcasts.tasks import poll_feed, rehost_episode_audio, polling_locks


class PollingThreadTest(PodcastTestCase):
    """Tests for the polling thread."""

    def tearDown(self):
        """Clean up after each test."""
        super().tearDown()
        

    @mock.patch("podcasts.tasks.poll_feed")
    def test_polling_loop(self, mock_poll_feed):
        """Test that the polling loop works as expected."""
        shutdown_event = threading.Event()
        shutdown_event.set()
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_not_called()

        # self.podcast is already created in setUp
        shutdown_event.clear()

        def side_effect(*_args, **_kwargs):
            shutdown_event.set()
            return True

        mock_poll_feed.side_effect = side_effect
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_called_once()

    @mock.patch("podcasts.tasks.DOWNLOAD_POOL.submit")
    @mock.patch("podcasts.tasks.feedparser")
    def test_poll_feed_reprocesses_stuck_episodes(self, mock_feedparser, mock_submit):
        """Test that poll_feed reprocesses stuck episodes."""
        # Ensure a clean slate for episodes before this test
        Episode.objects.all().delete()

        podcast = self.podcast
        stuck_time = timezone.now() - timezone.timedelta(hours=2)

        episode = Episode.objects.create(
            podcast=podcast,
            title="Stuck Episode",
            guid="stuck-episode",
            original_audio_url="http://example.com/stuck.mp3",
            pub_date=stuck_time,
            status=Episode.Status.NEW,
        )
        Episode.objects.filter(id=episode.id).update(
            updated_at=stuck_time, status=Episode.Status.DOWNLOADING
        )
        episode.refresh_from_db()

        mock_feed = mock.Mock()
        mock_feed.bozo = False
        mock_feed.entries = []
        mock_feedparser.parse.return_value = mock_feed

        poll_feed(podcast.id)

        mock_submit.assert_not_called()
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)

    @mock.patch("podcasts.tasks.DOWNLOAD_POOL.submit")
    @mock.patch("podcasts.tasks.feedparser")
    def test_poll_feed_download_all(self, mock_feedparser, mock_submit):
        """Test that poll_feed with download_all=True processes all entries."""
        # Ensure a clean slate for episodes before this test
        Episode.objects.all().delete()

        podcast = self.podcast
        mock_feed = mock.Mock()
        mock_feed.bozo = False

        # Create 15 entries (greater than default limit of 5)
        entries = []
        for i in range(15):
            entry = {
                "id": f"guid-{i}",
                "title": f"Episode {i}",
                "enclosures": [{"href": f"http://example.com/audio-{i}.mp3", "type": "audio/mpeg"}],
                "published_parsed": timezone.now().timetuple(),
            }
            entries.append(entry)

        mock_feed.entries = entries
        mock_feedparser.parse.return_value = mock_feed

        # Run with download_all=True
        podcast.max_episodes = 0
        podcast.save()
        poll_feed(podcast.id, download_all=True)

        # Check that all 15 episodes were created
        self.assertEqual(Episode.objects.filter(podcast=podcast).count(), 15)
        mock_submit.assert_not_called()


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class RaceConditionPreventionTest(PodcastTestCase):
    """Tests for race condition prevention."""

    def tearDown(self):
        """Clean up after each test."""
        super().tearDown()
        polling_locks.clear()

    @mock.patch("podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio")
    def test_rehost_episode_audio_skips_if_in_progress(self, mock_fetch):
        """Test that rehost_episode_audio skips if the episode is already in progress."""
        self.episode.status = Episode.Status.DOWNLOADING
        self.episode.save()

        rehost_episode_audio(self.episode.id)
        mock_fetch.assert_not_called()

    @mock.patch("podcasts.tasks.threading.Lock")
    def test_poll_feed_locking(self, mock_lock):
        """Test that poll_feed uses a lock to prevent race conditions."""
        mock_lock.return_value.acquire.return_value = False
        poll_feed(self.podcast.id)
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.last_polled, None)


class AppConfigReadyTest(PodcastTestCase):
    """Tests for PodcastsConfig.ready hook and Gunicorn/management command bypass logic."""

    @mock.patch("podcasts.apps.start_polling_thread")
    def test_ready_bypass_for_management_commands(self, mock_start_polling):
        """Test that ready() returns early when a management command is running."""
        from django.apps import apps
        
        config = apps.get_app_config("podcasts")
        with mock.patch("sys.argv", ["manage.py", "migrate"]):
            config.ready()
        
        mock_start_polling.assert_not_called()

    @mock.patch("podcasts.apps.start_polling_thread")
    @mock.patch("os.environ.get")
    def test_ready_bypass_for_runserver_autoreload(self, mock_env_get, mock_start_polling):
        """Test that ready() returns early when runserver is starting the main thread reload."""
        from django.apps import apps
        
        config = apps.get_app_config("podcasts")
        mock_env_get.return_value = None  # RUN_MAIN not set
        with mock.patch("sys.argv", ["manage.py", "runserver"]):
            config.ready()
        
        mock_start_polling.assert_not_called()

    @mock.patch("podcasts.apps.start_polling_thread")
    def test_ready_resets_stuck_episodes_and_starts_thread(self, mock_start_polling):
        """Test that ready() resets in-progress/stuck episodes and starts the polling thread."""
        from django.apps import apps
        
        # Ensure we have a stuck episode
        self.episode.status = Episode.Status.DOWNLOADING
        self.episode.save()
        
        config = apps.get_app_config("podcasts")
        # Fake that we are inside the reloaded process (RUN_MAIN = true) or running production
        with mock.patch("sys.argv", ["manage.py", "runserver", "0.0.0.0:8000"]):
            with mock.patch("os.environ", {"RUN_MAIN": "true"}):
                config.ready()
            
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.NEW)
        mock_start_polling.assert_called_once()


