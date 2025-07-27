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
from podcasts.apps import polling_loop, start_polling_thread
from podcasts.models import Episode, Podcast
from podcasts.tasks import poll_feed, rehost_episode_audio, polling_locks


class PollingThreadTest(PodcastTestCase):
    """Tests for the polling thread."""

    def tearDown(self):
        """Clean up after each test."""
        super().tearDown()
        Podcast.objects.all().delete()

    @mock.patch("podcasts.tasks.poll_feed")
    def test_polling_loop(self, mock_poll_feed):
        """Test that the polling loop works as expected."""
        shutdown_event = threading.Event()
        shutdown_event.set()
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_not_called()

        Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        shutdown_event.clear()

        def side_effect(*_args, **_kwargs):
            shutdown_event.set()
            return True

        mock_poll_feed.side_effect = side_effect
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_called_once()

    @mock.patch("podcasts.apps.threading.Thread")
    def test_start_polling_thread(self, mock_thread):
        """Test that the polling thread can be started."""
        mock_thread_instance = mock.Mock()
        mock_thread.return_value = mock_thread_instance

        thread, _ = start_polling_thread()

        mock_thread.assert_called_once()
        self.assertEqual(thread, mock_thread_instance)
        mock_thread_instance.start.assert_called_once()

    @mock.patch("podcasts.tasks.DOWNLOAD_POOL.submit")
    @mock.patch("podcasts.tasks.feedparser")
    def test_poll_feed_reprocesses_stuck_episodes(self, mock_feedparser, mock_submit):
        """Test that poll_feed reprocesses stuck episodes."""
        podcast = Podcast.objects.create(
            title="Test Podcast", rss_url="http://example.com/rss"
        )
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

        mock_submit.assert_called_once_with(rehost_episode_audio, episode.id)
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)


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
