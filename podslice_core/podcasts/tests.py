import os
import threading
import time
import uuid
from datetime import datetime
from unittest import mock

import requests
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pydub import AudioSegment
from rest_framework import status
from rest_framework.test import APIClient

from .apps import polling_loop, start_polling_thread
from .models import Episode, Podcast, RehostedMedia
from .serializers import PodcastSerializer
from .tasks import (
    _download_audio, _get_ad_segments_from_gemini,
    _parse_ad_segments, _slice_and_save_audio,
    delete_podcast_data, poll_feed, rehost_episode_audio,
    _fetch_and_prepare_audio, _analyze_audio_with_gemini,
    _save_original_audio_as_rehosted, _save_empty_audio_as_rehosted,
    _run_ffmpeg_slicing, _save_processed_audio,
    _create_or_update_episode_from_entry
)

class PollingThreadTest(TestCase):
    def tearDown(self):
        Podcast.objects.all().delete()

    @mock.patch('podcasts.tasks.poll_feed')
    def test_polling_loop(self, mock_poll_feed):
        # Test that the loop doesn't run if the shutdown event is set
        shutdown_event = threading.Event()
        shutdown_event.set() # Immediately stop the loop
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_not_called()

        # Test that the loop runs once
        Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        shutdown_event.clear()
        def side_effect(podcast_id):
            shutdown_event.set()
            return True
        
        mock_poll_feed.side_effect = side_effect
        polling_loop(shutdown_event, 1)
        mock_poll_feed.assert_called_once()

    @mock.patch('podcasts.apps.threading.Thread')
    def test_start_polling_thread(self, mock_thread):
        thread, _ = start_polling_thread()
        mock_thread.assert_called_once()
        thread.start.assert_called_once()

    @mock.patch('podcasts.tasks.DOWNLOAD_POOL.submit')
    @mock.patch('podcasts.tasks.feedparser')
    def test_poll_feed_reprocesses_stuck_episodes(self, mock_feedparser, mock_submit):
        # Create a podcast and a "stuck" episode
        podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        stuck_time = timezone.now() - timezone.timedelta(hours=2)
        
        episode = Episode.objects.create(
            podcast=podcast,
            title="Stuck Episode",
            guid="stuck-episode",
            original_audio_url="http://example.com/stuck.mp3",
            pub_date=stuck_time,
            status=Episode.Status.NEW,
        )
        # Manually set the updated_at to the past to simulate a stuck episode
        Episode.objects.filter(id=episode.id).update(updated_at=stuck_time, status=Episode.Status.DOWNLOADING)
        episode.refresh_from_db()

        # Mock the feedparser to return a valid, empty feed
        mock_feed = mock.Mock()
        mock_feed.bozo = False
        mock_feed.entries = []
        mock_feedparser.parse.return_value = mock_feed

        # Call the poll_feed task
        poll_feed(podcast.id)

        # Check that the rehost_episode_audio task was called for the stuck episode
        mock_submit.assert_called_once_with(rehost_episode_audio, episode.id)

        # Check that the episode status was reset to NEW
        episode.refresh_from_db()
        self.assertEqual(episode.status, Episode.Status.NEW)


class PodcastModelTest(TestCase):
    def test_podcast_creation(self):
        podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
            artwork_url="http://example.com/artwork.jpg"
        )
        self.assertIsInstance(podcast, Podcast)
        self.assertEqual(str(podcast), "Test Podcast")


class RehostedMediaModelTest(TestCase):
    def test_rehosted_media_creation(self):
        media = RehostedMedia.objects.create(
            file_path="/path/to/file.mp3",
            content_type="audio/mpeg"
        )
        self.assertIsInstance(media, RehostedMedia)
        self.assertEqual(str(media), f"Media {media.media_guid}")


class PodcastSerializerTest(TestCase):
    def test_podcast_serializer_valid_data(self):
        serializer = PodcastSerializer(data={'rss_url': 'http://example.com/new_feed.xml'})
        self.assertTrue(serializer.is_valid())

    def test_podcast_serializer_duplicate_rss_url(self):
        Podcast.objects.create(title="Existing", rss_url="http://example.com/existing.xml")
        serializer = PodcastSerializer(data={'rss_url': 'http://example.com/existing.xml'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('rss_url', serializer.errors)


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class PodcastViewsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/feed.xml")

    def tearDown(self):
        # Clean up test media files
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


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class TasksHelperFunctionsTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )
        self.test_media_dir = settings.MEDIA_ROOT
        os.makedirs(self.test_media_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_media_dir):
            for f in os.listdir(self.test_media_dir):
                os.remove(os.path.join(self.test_media_dir, f))
            os.rmdir(self.test_media_dir)

    @mock.patch('podcasts.tasks.requests.get')
    def test_download_audio_success(self, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_requests_get.return_value = mock_response

        audio_path = _download_audio(self.episode)

        self.assertIsNotNone(audio_path)
        self.assertTrue(os.path.exists(audio_path))
        with open(audio_path, 'rb') as f:
            self.assertEqual(f.read(), b"chunk1chunk2")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.DOWNLOADING)
        os.remove(audio_path)

    @mock.patch('podcasts.tasks.requests.get', side_effect=requests.exceptions.RequestException("Download failed"))
    def test_download_audio_failure(self, mock_requests_get):
        audio_path = _download_audio(self.episode)
        self.assertIsNone(audio_path)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch('podcasts.tasks.genai.upload_file')
    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    def test_get_ad_segments_from_gemini(self, mock_generative_model, mock_upload_file):
        mock_model_instance = mock.Mock()
        mock_model_instance.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        mock_generative_model.return_value = mock_model_instance
        mock_upload_file.return_value = "fake_file_id"

        response = _get_ad_segments_from_gemini("dummy_path.mp3", 60.0)
        self.assertEqual(response, '[{"start": 10, "end": 20}]')
        mock_upload_file.assert_called_once_with(path="dummy_path.mp3")

    def test_parse_ad_segments_valid_json(self):
        segments = _parse_ad_segments('[{"start": 10, "end": 20}]', self.episode)
        self.assertEqual(segments, [{"start": 10, "end": 20}])

    def test_parse_ad_segments_valid_json_with_markdown(self):
        response = """```json
[{"start": 30.5, "end": 45.0}]
```"""
        segments = _parse_ad_segments(response, self.episode)
        self.assertEqual(segments, [{"start": 30.5, "end": 45.0}])

    def test_parse_ad_segments_invalid_json(self):
        segments = _parse_ad_segments('invalid json', self.episode)
        self.assertEqual(segments, [])

    def test_parse_ad_segments_empty_response(self):
        segments = _parse_ad_segments('', self.episode)
        self.assertEqual(segments, [])

    @mock.patch('podcasts.tasks._get_audio_duration', return_value=60.0)
    @mock.patch('podcasts.tasks._download_audio')
    def test_fetch_and_prepare_audio_success(self, mock_download, mock_duration):
        mock_download.return_value = "/fake/path.mp3"
        result = _fetch_and_prepare_audio(self.episode)
        self.assertEqual(result, ("/fake/path.mp3", 60.0))
        mock_download.assert_called_once_with(self.episode)
        mock_duration.assert_called_once_with("/fake/path.mp3", self.episode)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.ANALYZING)

    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks._get_audio_duration', return_value=None)
    @mock.patch('podcasts.tasks._download_audio', return_value="/fake/path.mp3")
    def test_fetch_and_prepare_audio_duration_fails(self, mock_download, mock_duration, mock_remove):
        result = _fetch_and_prepare_audio(self.episode)
        self.assertIsNone(result)
        mock_remove.assert_called_once_with("/fake/path.mp3")

    @mock.patch('podcasts.tasks._parse_ad_segments')
    @mock.patch('podcasts.tasks._get_ad_segments_from_gemini')
    def test_analyze_audio_with_gemini(self, mock_get_segments, mock_parse_segments):
        mock_get_segments.return_value = 'gemini-response'
        mock_parse_segments.return_value = [{"start": 1, "end": 2}]
        
        result = _analyze_audio_with_gemini(self.episode, "/fake/path.mp3", 60.0)

        self.assertEqual(result, [{"start": 1, "end": 2}])
        mock_get_segments.assert_called_once_with("/fake/path.mp3", 60.0)
        mock_parse_segments.assert_called_once_with('gemini-response', self.episode)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.ad_segments, 'gemini-response')
        self.assertEqual(self.episode.status, Episode.Status.PROCESSING)

    @mock.patch('podcasts.tasks._save_processed_audio')
    @mock.patch('podcasts.tasks._run_ffmpeg_slicing')
    def test_slice_and_save_audio_with_segments(self, mock_run_ffmpeg, mock_save_processed):
        ad_segments = [{"start": 1, "end": 2}]
        mock_run_ffmpeg.return_value = ('/path/to/file.mp3', 12345)
        
        _slice_and_save_audio("/fake/path.mp3", ad_segments, self.episode, 60.0)

        mock_run_ffmpeg.assert_called_once_with("/fake/path.mp3", mock.ANY, self.episode)
        mock_save_processed.assert_called_once_with(self.episode, '/path/to/file.mp3', 12345)

    @mock.patch('podcasts.tasks._save_original_audio_as_rehosted')
    def test_slice_and_save_audio_no_segments(self, mock_save_original):
        _slice_and_save_audio("/fake/path.mp3", [], self.episode, 60.0)
        mock_save_original.assert_called_once_with("/fake/path.mp3", self.episode)

    @mock.patch('podcasts.tasks._save_empty_audio_as_rehosted')
    @mock.patch('podcasts.tasks._generate_ffmpeg_filter_complex', return_value="")
    def test_slice_and_save_audio_empty_filter(self, mock_filter, mock_save_empty):
        ad_segments = [{"start": 0, "end": 60}]
        _slice_and_save_audio("/fake/path.mp3", ad_segments, self.episode, 60.0)
        mock_save_empty.assert_called_once_with(self.episode)

    @mock.patch('podcasts.tasks._save_processed_audio')
    @mock.patch('podcasts.tasks.os.rename')
    @mock.patch('podcasts.tasks.os.path.getsize')
    def test_save_original_audio_as_rehosted(self, mock_getsize, mock_rename, mock_save_processed):
        mock_getsize.return_value = 12345
        _save_original_audio_as_rehosted("/fake/path.mp3", self.episode)
        mock_rename.assert_called_once()
        mock_save_processed.assert_called_once_with(self.episode, mock.ANY, 12345)

    @mock.patch('podcasts.tasks.subprocess.run')
    def test_run_ffmpeg_slicing(self, mock_run):
        # Simulate ffmpeg creating the output file
        def side_effect(*args, **kwargs):
            cmd = args[0]
            output_path = cmd[-1]
            with open(output_path, "w") as f:
                f.write("dummy output")
            return mock.Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        
        with mock.patch('podcasts.tasks.os.path.getsize', return_value=54321):
            input_path = os.path.join(self.test_media_dir, "input.mp3")
            with open(input_path, "w") as f:
                f.write("dummy input")

            path, size = _run_ffmpeg_slicing(input_path, "select_filter", self.episode)

        self.assertEqual(size, 54321)
        self.assertTrue(path.endswith(".mp3"))
        self.assertTrue(os.path.exists(path))
        os.remove(path)


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class RehostEpisodeAudioTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )
        self.test_media_dir = settings.MEDIA_ROOT
        os.makedirs(self.test_media_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_media_dir):
            for f in os.listdir(self.test_media_dir):
                os.remove(os.path.join(self.test_media_dir, f))
            os.rmdir(self.test_media_dir)

    @mock.patch('podcasts.tasks.os.path.exists', return_value=True)
    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks._slice_and_save_audio')
    @mock.patch('podcasts.tasks._analyze_audio_with_gemini')
    @mock.patch('podcasts.tasks._fetch_and_prepare_audio')
    def test_rehost_episode_audio_orchestrator_success(
        self, mock_fetch, mock_analyze, mock_slice, mock_remove, mock_exists
    ):
        mock_fetch.return_value = ("/fake/path.mp3", 60.0)
        mock_analyze.return_value = [{"start": 10, "end": 20}]

        rehost_episode_audio(self.episode.id)

        mock_fetch.assert_called_once_with(self.episode)
        mock_analyze.assert_called_once_with(self.episode, "/fake/path.mp3", 60.0)
        mock_slice.assert_called_once_with("/fake/path.mp3", [{"start": 10, "end": 20}], self.episode, 60.0)
        mock_remove.assert_called_once_with("/fake/path.mp3")

    @mock.patch('podcasts.tasks._fetch_and_prepare_audio', return_value=None)
    def test_rehost_episode_audio_fetch_fails(self, mock_fetch):
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertNotEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch('podcasts.tasks.os.path.exists', return_value=True)
    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks._slice_and_save_audio', side_effect=Exception("Slicing failed"))
    @mock.patch('podcasts.tasks._analyze_audio_with_gemini', return_value=[])
    @mock.patch('podcasts.tasks._fetch_and_prepare_audio', return_value=("/fake/path.mp3", 60.0))
    def test_rehost_episode_audio_slice_fails(self, mock_fetch, mock_analyze, mock_slice, mock_remove, mock_exists):
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)
        mock_remove.assert_called_once_with("/fake/path.mp3")


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class RaceConditionPreventionTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )
        self.test_media_dir = settings.MEDIA_ROOT
        os.makedirs(self.test_media_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_media_dir):
            for f in os.listdir(self.test_media_dir):
                os.remove(os.path.join(self.test_media_dir, f))
            os.rmdir(self.test_media_dir)
        # Clear the polling locks after each test
        from .tasks import polling_locks
        polling_locks.clear()

    @mock.patch('podcasts.tasks._fetch_and_prepare_audio', return_value=None)
    def test_rehost_episode_audio_skips_if_in_progress(self, mock_fetch):
        # Set the episode status to an in-progress state
        self.episode.status = Episode.Status.DOWNLOADING
        self.episode.save()

        rehost_episode_audio(self.episode.id)

        # Assert that the processing pipeline was not started
        mock_fetch.assert_not_called()

    @mock.patch('podcasts.tasks._get_podcast_for_polling')
    def test_poll_feed_locking(self, mock_get_podcast):
        # Mock the lock to be acquired
        lock = mock.Mock()
        lock.acquire.return_value = False
        
        with mock.patch('podcasts.tasks.polling_locks', {self.podcast.id: lock}):
            poll_feed(self.podcast.id)
            # Assert that the polling logic was not executed
            mock_get_podcast.assert_not_called()


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class DeletePodcastDataTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )
        # Create a dummy file and RehostedMedia entry
        self.media_guid = uuid.uuid4()
        self.file_path = os.path.join(settings.MEDIA_ROOT, f"{self.media_guid}.mp3")
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        with open(self.file_path, "wb") as f:
            f.write(b"test")
        self.rehosted_media = RehostedMedia.objects.create(
            media_guid=self.media_guid,
            file_path=self.file_path,
        )
        self.episode.rehosted_media_id = self.rehosted_media.media_guid
        self.episode.save()

    def tearDown(self):
        # Clean up test media files
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            if os.path.exists(media_root) and not os.listdir(media_root):
                os.rmdir(media_root)

    def test_delete_podcast_data_success(self):
        self.assertTrue(os.path.exists(self.file_path))
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(Episode.objects.filter(id=self.episode.id).exists())
        self.assertFalse(RehostedMedia.objects.filter(media_guid=self.rehosted_media.media_guid).exists())
        self.assertFalse(os.path.exists(self.file_path))

    def test_delete_podcast_data_podcast_not_found(self):
        # Should not raise an error
        delete_podcast_data(uuid.uuid4())

    def test_delete_podcast_data_file_already_deleted(self):
        os.remove(self.file_path)
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(RehostedMedia.objects.filter(media_guid=self.rehosted_media.media_guid).exists())
