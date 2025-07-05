import os
import uuid
from unittest import mock
import requests
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from ..models import Episode, Podcast, RehostedMedia
from ..tasks import AdManager, EpisodeProcessor, FeedManager, rehost_episode_audio, delete_podcast_data

@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, 'test_media'))
class TasksClassMethodsTest(TestCase):
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
        self.processor = EpisodeProcessor(self.episode)
        self.ad_manager = AdManager(self.episode)
        self.feed_manager = FeedManager(self.podcast)

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

        audio_path = self.processor._download_audio()

        self.assertIsNotNone(audio_path)
        self.assertTrue(os.path.exists(audio_path))
        with open(audio_path, 'rb') as f:
            self.assertEqual(f.read(), b"chunk1chunk2")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.DOWNLOADING)
        os.remove(audio_path)

    @mock.patch('podcasts.tasks.requests.get', side_effect=requests.exceptions.RequestException("Download failed"))
    def test_download_audio_failure(self, mock_requests_get):
        audio_path = self.processor._download_audio()
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

        response = self.ad_manager._get_ad_segments_from_gemini("dummy_path.mp3", 60.0)
        self.assertEqual(response, '[{"start": 10, "end": 20}]')
        mock_upload_file.assert_called_once_with(path="dummy_path.mp3")

    def test_parse_ad_segments_valid_json(self):
        segments = self.ad_manager._parse_ad_segments('[{"start": 10, "end": 20}]')
        self.assertEqual(segments, [{"start": 10, "end": 20}])

    @mock.patch('podcasts.tasks.EpisodeProcessor._get_audio_duration', return_value=60.0)
    @mock.patch('podcasts.tasks.EpisodeProcessor._download_audio')
    def test_fetch_and_prepare_audio_success(self, mock_download, mock_duration):
        mock_download.return_value = "/fake/path.mp3"
        result = self.processor._fetch_and_prepare_audio()
        self.assertEqual(result, ("/fake/path.mp3", 60.0))
        mock_download.assert_called_once()
        mock_duration.assert_called_once_with("/fake/path.mp3")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.ANALYZING)

    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks.EpisodeProcessor._get_audio_duration', return_value=None)
    @mock.patch('podcasts.tasks.EpisodeProcessor._download_audio', return_value="/fake/path.mp3")
    def test_fetch_and_prepare_audio_duration_fails(self, mock_download, mock_duration, mock_remove):
        result = self.processor._fetch_and_prepare_audio()
        self.assertIsNone(result)
        mock_remove.assert_called_once_with("/fake/path.mp3")

    @mock.patch('podcasts.tasks.AdManager._parse_ad_segments')
    @mock.patch('podcasts.tasks.AdManager._get_ad_segments_from_gemini')
    def test_analyze_audio_with_gemini(self, mock_get_segments, mock_parse_segments):
        mock_get_segments.return_value = 'gemini-response'
        mock_parse_segments.return_value = [{"start": 1, "end": 2}]
        
        result = self.ad_manager.analyze_audio("/fake/path.mp3", 60.0)

        self.assertEqual(result, [{"start": 1, "end": 2}])
        mock_get_segments.assert_called_once_with("/fake/path.mp3", 60.0)
        mock_parse_segments.assert_called_once_with('gemini-response')
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.ad_segments, 'gemini-response')

    @mock.patch('podcasts.tasks.EpisodeProcessor._save_processed_audio')
    @mock.patch('podcasts.tasks.EpisodeProcessor._run_ffmpeg_slicing')
    def test_slice_and_save_audio_with_segments(self, mock_run_ffmpeg, mock_save_processed):
        ad_segments = [{"start": 1, "end": 2}]
        mock_run_ffmpeg.return_value = ('/path/to/file.mp3', 12345)
        
        self.processor._slice_and_save_audio("/fake/path.mp3", ad_segments, 60.0)

        mock_run_ffmpeg.assert_called_once_with("/fake/path.mp3", mock.ANY)
        mock_save_processed.assert_called_once_with('/path/to/file.mp3', 12345)

    @mock.patch('podcasts.tasks.EpisodeProcessor._save_original_audio_as_rehosted')
    def test_slice_and_save_audio_no_segments(self, mock_save_original):
        self.processor._slice_and_save_audio("/fake/path.mp3", [], 60.0)
        mock_save_original.assert_called_once_with("/fake/path.mp3")

    @mock.patch('podcasts.tasks.EpisodeProcessor._save_empty_audio_as_rehosted')
    @mock.patch('podcasts.tasks.EpisodeProcessor._generate_ffmpeg_filter_complex', return_value="")
    def test_slice_and_save_audio_empty_filter(self, mock_filter, mock_save_empty):
        ad_segments = [{"start": 0, "end": 60}]
        self.processor._slice_and_save_audio("/fake/path.mp3", ad_segments, 60.0)
        mock_save_empty.assert_called_once()

    @mock.patch('podcasts.tasks.EpisodeProcessor._save_processed_audio')
    @mock.patch('podcasts.tasks.os.rename')
    @mock.patch('podcasts.tasks.os.path.getsize')
    def test_save_original_audio_as_rehosted(self, mock_getsize, mock_rename, mock_save_processed):
        mock_getsize.return_value = 12345
        self.processor._save_original_audio_as_rehosted("/fake/path.mp3")
        mock_rename.assert_called_once()
        mock_save_processed.assert_called_once_with(mock.ANY, 12345)

    @mock.patch('podcasts.tasks.subprocess.run')
    def test_run_ffmpeg_slicing(self, mock_run):
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

            path, size = self.processor._run_ffmpeg_slicing(input_path, "select_filter")

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
    @mock.patch('podcasts.tasks.EpisodeProcessor._slice_and_save_audio')
    @mock.patch('podcasts.tasks.AdManager.analyze_audio')
    @mock.patch('podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio')
    def test_rehost_episode_audio_orchestrator_success(
        self, mock_fetch, mock_analyze, mock_slice, mock_remove, mock_exists
    ):
        mock_fetch.return_value = ("/fake/path.mp3", 60.0)
        mock_analyze.return_value = [{"start": 10, "end": 20}]

        rehost_episode_audio(self.episode.id)

        mock_fetch.assert_called_once()
        mock_analyze.assert_called_once_with("/fake/path.mp3", 60.0)
        mock_slice.assert_called_once_with("/fake/path.mp3", [{"start": 10, "end": 20}], 60.0)
        mock_remove.assert_called_once_with("/fake/path.mp3")

    @mock.patch('podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio', return_value=None)
    def test_rehost_episode_audio_fetch_fails(self, mock_fetch):
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertNotEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch('podcasts.tasks.os.path.exists', return_value=True)
    @mock.patch('podcasts.tasks.os.remove')
    @mock.patch('podcasts.tasks.EpisodeProcessor._slice_and_save_audio', side_effect=Exception("Slicing failed"))
    @mock.patch('podcasts.tasks.AdManager.analyze_audio', return_value=[])
    @mock.patch('podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio', return_value=("/fake/path.mp3", 60.0))
    def test_rehost_episode_audio_slice_fails(self, mock_fetch, mock_analyze, mock_slice, mock_remove, mock_exists):
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)
        mock_remove.assert_called_once_with("/fake/path.mp3")


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
        delete_podcast_data(uuid.uuid4())

    def test_delete_podcast_data_file_already_deleted(self):
        os.remove(self.file_path)
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(RehostedMedia.objects.filter(media_guid=self.rehosted_media.media_guid).exists())
