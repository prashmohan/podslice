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
from .tasks import (_download_audio, _get_ad_segments_from_gemini,
                    _parse_ad_segments, _save_processed_audio, _slice_audio,
                    delete_podcast_data, poll_feed, rehost_episode_audio)

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
        episode.updated_at = stuck_time
        episode.save()


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

    @mock.patch('podcasts.tasks.requests.get')
    def test_download_audio_success(self, mock_requests_get):
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b"audio_data"
        mock_requests_get.return_value = mock_response

        audio_content = _download_audio(self.episode)

        self.assertEqual(audio_content, b"audio_data")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.DOWNLOADING)

    @mock.patch('podcasts.tasks.requests.get', side_effect=requests.exceptions.RequestException("Download failed"))
    def test_download_audio_failure(self, mock_requests_get):
        audio_content = _download_audio(self.episode)
        self.assertIsNone(audio_content)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch('podcasts.tasks.genai.GenerativeModel')
    def test_get_ad_segments_from_gemini(self, mock_generative_model):
        mock_model_instance = mock.Mock()
        mock_model_instance.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        mock_generative_model.return_value = mock_model_instance

        response = _get_ad_segments_from_gemini(b"dummy_audio", 60.0)
        self.assertEqual(response, '[{"start": 10, "end": 20}]')

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

    def test_slice_audio_with_ads(self):
        audio = AudioSegment.silent(duration=60000)  # 60 seconds
        ad_segments = [{"start": 10, "end": 20}, {"start": 40, "end": 50}]
        processed_audio = _slice_audio(audio, ad_segments, self.episode)
        self.assertEqual(len(processed_audio), 40000) # 60s - 10s - 10s = 40s

    def test_slice_audio_no_ads(self):
        audio = AudioSegment.silent(duration=60000)
        processed_audio = _slice_audio(audio, [], self.episode)
        self.assertEqual(len(processed_audio), 60000)

    @override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
    @mock.patch('podcasts.tasks.os.path.getsize', return_value=12345)
    @mock.patch('pydub.AudioSegment.export')
    def test_save_processed_audio(self, mock_export, mock_getsize):
        audio = AudioSegment.silent(duration=1000)
        _save_processed_audio(self.episode, audio)
        self.episode.refresh_from_db()

        self.assertEqual(self.episode.status, Episode.Status.COMPLETE)
        self.assertIsNotNone(self.episode.rehosted_audio_url)
        self.assertEqual(self.episode.rehosted_audio_size, 12345)
        self.assertTrue(RehostedMedia.objects.filter(media_guid=self.episode.rehosted_media_id).exists())
        # Clean up the created file
        media = RehostedMedia.objects.get(media_guid=self.episode.rehosted_media_id)
        if os.path.exists(media.file_path):
            os.remove(media.file_path)


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class RehostEpisodeAudioOrchestratorTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="Test Podcast", rss_url="http://example.com/rss")
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="12345",
            original_audio_url="http://example.com/episode.mp3",
            pub_date=timezone.now(),
        )

    def tearDown(self):
        # Clean up test media files
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for f in os.listdir(media_root):
                os.remove(os.path.join(media_root, f))
            os.rmdir(media_root)

    @mock.patch('podcasts.tasks._download_audio')
    @mock.patch('podcasts.tasks.AudioSegment.from_file')
    @mock.patch('podcasts.tasks._get_ad_segments_from_gemini')
    @mock.patch('podcasts.tasks._parse_ad_segments')
    @mock.patch('podcasts.tasks._slice_audio')
    @mock.patch('podcasts.tasks.os.path.getsize', return_value=12345)
    @mock.patch('podcasts.tasks.RehostedMedia.objects.create')
    @mock.patch('pydub.AudioSegment.export')
    def test_rehost_episode_audio_orchestrator_success(
        self, mock_export, mock_rehosted_media_create, mock_getsize, mock_slice, mock_parse, mock_gemini, mock_from_file, mock_download
    ):
        mock_download.return_value = b"audio_data"
        mock_from_file.return_value = AudioSegment.silent(duration=60000)
        mock_gemini.return_value = '[{"start": 10, "end": 20}]'
        mock_parse.return_value = [{"start": 10, "end": 20}]
        mock_slice.return_value = AudioSegment.silent(duration=50000)
        mock_rehosted_media_create.return_value = RehostedMedia(media_guid=uuid.uuid4(), file_path="/tmp/fake.mp3")

        rehost_episode_audio(self.episode.id)

        mock_download.assert_called_once_with(self.episode)
        mock_gemini.assert_called_once()
        mock_parse.assert_called_once()
        mock_slice.assert_called_once()
        mock_export.assert_called_once()

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.COMPLETE)


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
        self.episode.rehosted_media_id = self.media_guid
        self.episode.save()

    def tearDown(self):
        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        if os.path.exists(settings.MEDIA_ROOT):
            os.rmdir(settings.MEDIA_ROOT)

    def test_delete_podcast_data_success(self):
        self.assertTrue(os.path.exists(self.file_path))
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(Episode.objects.filter(id=self.episode.id).exists())
        self.assertFalse(RehostedMedia.objects.filter(media_guid=self.media_guid).exists())
        self.assertFalse(os.path.exists(self.file_path))

    def test_delete_podcast_data_podcast_not_found(self):
        # Should not raise an error
        delete_podcast_data(uuid.uuid4())

    def test_delete_podcast_data_file_already_deleted(self):
        os.remove(self.file_path)
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(RehostedMedia.objects.filter(media_guid=self.media_guid).exists())