"""
Tests for the podcast tasks.
"""

import os
import uuid
from unittest import mock

import requests
from django.conf import settings
from django.test import override_settings
from django.utils import timezone


from podcasts.tests.test_base import PodcastTestCase
from podcasts.models import Episode, Podcast, RehostedMedia
from podcasts.tasks import (
    AdManager,
    EpisodeProcessor,
    FeedManager,
    delete_podcast_data,
    rehost_episode_audio,
)


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class TasksClassMethodsTest(PodcastTestCase):
    """
    Test cases for the methods within the tasks classes.
    """

    def setUp(self):
        """
        Set up test data for tasks class methods.
        """
        super().setUp()
        self.processor = EpisodeProcessor(self.episode)
        self.ad_manager = AdManager(self.episode)
        self.feed_manager = FeedManager(self.podcast)

    @mock.patch("podcasts.tasks.requests.get")
    def test_download_audio_success(self, mock_requests_get):
        """
        Test successful audio download.
        """
        mock_response = mock.Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_requests_get.return_value = mock_response

        audio_path = (
            self.processor._download_audio()
        )  # pylint: disable=protected-access

        self.assertIsNotNone(audio_path)
        self.assertTrue(os.path.exists(audio_path))
        with open(audio_path, "rb") as f:
            self.assertEqual(f.read(), b"chunk1chunk2")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.DOWNLOADING)
        os.remove(audio_path)

    @mock.patch(
        "podcasts.tasks.requests.get",
        side_effect=requests.exceptions.RequestException("Download failed"),
    )
    def test_download_audio_failure(self, mock_requests_get):  # pylint: disable=W0613
        """
        Test audio download failure.
        """
        audio_path = (
            self.processor._download_audio()
        )  # pylint: disable=protected-access
        self.assertIsNone(audio_path)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    def test_get_ad_segments_from_gemini(self, mock_generative_model, mock_upload_file):
        """
        Test fetching ad segments from Gemini.
        """
        mock_model_instance = mock.Mock()
        mock_model_instance.generate_content.return_value = mock.Mock(
            text='[{"start": 10, "end": 20}]'
        )
        mock_generative_model.return_value = mock_model_instance
        mock_upload_file.return_value = "fake_file_id"

        response = self.ad_manager._get_ad_segments_from_gemini(  # pylint: disable=protected-access
            "dummy_path.mp3"
        )
        self.assertEqual(response, '[{"start": 10, "end": 20}]')
        mock_upload_file.assert_called_once_with(path="dummy_path.mp3")

    def test_parse_ad_segments_valid_json(self):
        """
        Test parsing valid ad segments JSON.
        """
        segments = (
            self.ad_manager._parse_ad_segments(  # pylint: disable=protected-access
                '[{"start": 10, "end": 20}]'
            )
        )
        self.assertEqual(segments, [{"start": 10, "end": 20}])

    def test_parse_ad_segments_malformed_json(self):
        """
        Test parsing malformed ad segments JSON.
        """
        segments = (
            self.ad_manager._parse_ad_segments(  # pylint: disable=protected-access
                '[{"start": 10, "end": 20'
            )
        )
        self.assertEqual(segments, [])
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)

    def test_parse_ad_segments_invalid_structure(self):
        """
        Test parsing ad segments JSON with invalid structure.
        """
        segments = (
            self.ad_manager._parse_ad_segments(  # pylint: disable=protected-access
                '[{"start": 10}]'
            )
        )
        self.assertEqual(segments, [])
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._get_audio_duration", return_value=60.0
    )
    @mock.patch("podcasts.tasks.EpisodeProcessor._download_audio")
    def test_fetch_and_prepare_audio_success(
        self, mock_download, mock_duration
    ):  # pylint: disable=W0613
        """
        Test successful audio fetching and preparation.
        """
        mock_download.return_value = "/fake/path.mp3"
        result = (
            self.processor._fetch_and_prepare_audio()
        )  # pylint: disable=protected-access
        self.assertEqual(result, ("/fake/path.mp3", 60.0))
        mock_download.assert_called_once()
        mock_duration.assert_called_once_with("/fake/path.mp3")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.ANALYZING)

    @mock.patch("podcasts.tasks.os.remove")
    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._get_audio_duration", return_value=None
    )
    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._download_audio", return_value="/fake/path.mp3"
    )
    def test_fetch_and_prepare_audio_duration_fails(
        self, mock_download, mock_duration, mock_remove
    ):  # pylint: disable=W0613
        """
        Test audio fetching and preparation when duration fails.
        """
        result = (
            self.processor._fetch_and_prepare_audio()
        )  # pylint: disable=protected-access
        self.assertIsNone(result)
        mock_remove.assert_called_once_with("/fake/path.mp3")

    @mock.patch("podcasts.tasks.AdManager._parse_ad_segments")
    @mock.patch("podcasts.tasks.AdManager._get_ad_segments_from_gemini")
    def test_analyze_audio_with_gemini(self, mock_get_segments, mock_parse_segments):
        """
        Test audio analysis with Gemini.
        """
        mock_get_segments.return_value = "gemini-response"
        mock_parse_segments.return_value = [{"start": 1, "end": 2}]

        result = self.ad_manager.analyze_audio("/fake/path.mp3")

        self.assertEqual(result, [{"start": 1, "end": 2}])
        mock_get_segments.assert_called_once_with("/fake/path.mp3")
        mock_parse_segments.assert_called_once_with("gemini-response")
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.ad_segments, "gemini-response")

    @mock.patch("podcasts.tasks.EpisodeProcessor._save_processed_audio")
    @mock.patch("podcasts.tasks.EpisodeProcessor._run_ffmpeg_slicing")
    def test_slice_and_save_audio_with_segments(
        self, mock_run_ffmpeg, mock_save_processed
    ):
        """
        Test slicing and saving audio with ad segments.
        """
        ad_segments = [{"start": 1, "end": 2}]
        mock_run_ffmpeg.return_value = 12345

        self.processor._slice_and_save_audio(  # pylint: disable=protected-access
            "/fake/path.mp3", ad_segments, 60.0
        )

        mock_run_ffmpeg.assert_called_once_with("/fake/path.mp3", mock.ANY, mock.ANY)
        mock_save_processed.assert_called_once_with(mock.ANY, mock.ANY, 12345)

    @mock.patch("podcasts.tasks.EpisodeProcessor._save_original_audio_as_rehosted")
    def test_slice_and_save_audio_no_segments(self, mock_save_original):
        """
        Test slicing and saving audio with no ad segments.
        """
        self.processor._slice_and_save_audio(  # pylint: disable=protected-access
            "/fake/path.mp3", [], 60.0
        )
        mock_save_original.assert_called_once_with("/fake/path.mp3")

    @mock.patch("podcasts.tasks.EpisodeProcessor._save_empty_audio_as_rehosted")
    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._generate_ffmpeg_filter_complex",
        return_value="",
    )
    def test_slice_and_save_audio_empty_filter(
        self, mock_filter, mock_save_empty
    ):  # pylint: disable=W0613
        """
        Test slicing and saving audio with an empty FFmpeg filter.
        """
        ad_segments = [{"start": 0, "end": 60}]
        self.processor._slice_and_save_audio(  # pylint: disable=protected-access
            "/fake/path.mp3", ad_segments, 60.0
        )
        mock_save_empty.assert_called_once()

    @mock.patch("podcasts.tasks.EpisodeProcessor._save_processed_audio")
    @mock.patch("podcasts.tasks.os.rename")
    @mock.patch("podcasts.tasks.os.path.getsize")
    def test_save_original_audio_as_rehosted(
        self, mock_getsize, mock_rename, mock_save_processed
    ):
        """
        Test saving original audio as rehosted.
        """
        mock_getsize.return_value = 12345
        self.processor._save_original_audio_as_rehosted(  # pylint: disable=protected-access
            "/fake/path.mp3"
        )
        mock_rename.assert_called_once()
        mock_save_processed.assert_called_once_with(mock.ANY, mock.ANY, 12345)

    @mock.patch("podcasts.tasks.subprocess.run")
    def test_run_ffmpeg_slicing(self, mock_run):
        """
        Test running FFmpeg slicing command.
        """

        def side_effect(*args, **kwargs):
            cmd = args[0]
            output_path = cmd[-1]
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("dummy output")
            return mock.Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        with mock.patch("podcasts.tasks.os.path.getsize", return_value=54321):
            input_path = os.path.join(self.test_media_dir, "input.mp3")
            output_path = os.path.join(self.test_media_dir, "output.mp3")
            with open(input_path, "w", encoding="utf-8") as f:
                f.write("dummy input")

            size = (
                self.processor._run_ffmpeg_slicing(  # pylint: disable=protected-access
                    input_path, "select_filter", output_path
                )
            )

        self.assertEqual(size, 54321)
        self.assertTrue(os.path.exists(output_path))
        os.remove(output_path)


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class RehostEpisodeAudioTest(PodcastTestCase):
    """
    Test cases for the rehost_episode_audio task.
    """

    @mock.patch("podcasts.tasks.EpisodeProcessor._slice_and_save_audio")
    @mock.patch("podcasts.tasks.AdManager.analyze_audio")
    @mock.patch("podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio")
    def test_rehost_episode_audio_orchestrator_success(
        self, mock_fetch, mock_analyze, mock_slice
    ):
        """
        Test successful rehosting of episode audio.
        """
        mock_fetch.return_value = ("/fake/path.mp3", 60.0)
        mock_analyze.return_value = [{"start": 10, "end": 20}]

        rehost_episode_audio(self.episode.id)

        mock_fetch.assert_called_once()
        mock_analyze.assert_called_once_with("/fake/path.mp3")
        mock_slice.assert_called_once_with(
            "/fake/path.mp3", [{"start": 10, "end": 20}], 60.0
        )

    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio", return_value=None
    )
    def test_rehost_episode_audio_fetch_fails(
        self, mock_fetch
    ):  # pylint: disable=W0613
        """
        Test rehosting episode audio when fetch fails.
        """
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertNotEqual(self.episode.status, Episode.Status.FAILED)

    @mock.patch("podcasts.tasks.os.path.exists", return_value=True)
    @mock.patch("podcasts.tasks.os.remove")
    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._slice_and_save_audio",
        side_effect=ValueError("Slicing failed"),
    )
    @mock.patch("podcasts.tasks.AdManager.analyze_audio", return_value=[])
    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio",
        return_value=("/fake/path.mp3", 60.0),
    )
    def test_rehost_episode_audio_slice_fails(
        self, mock_fetch, mock_analyze, mock_slice, mock_remove, mock_exists
    ):  # pylint: disable=too-many-arguments  # pylint: disable=too-many-arguments
        """
        Test rehosting episode audio when slicing fails.
        """
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)
        mock_remove.assert_called_once_with("/fake/path.mp3")
        mock_fetch.assert_called_once()
        mock_analyze.assert_called_once()
        mock_slice.assert_called_once()
        mock_exists.assert_called()

    @mock.patch(
        "podcasts.tasks.EpisodeProcessor._fetch_and_prepare_audio",
        side_effect=ValueError("Random failure"),
    )
    def test_rehost_episode_audio_generic_exception(self, mock_fetch):
        """
        Test rehosting episode audio with a generic exception.
        """
        rehost_episode_audio(self.episode.id)
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)
        mock_fetch.assert_called_once()


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class DeletePodcastDataTest(PodcastTestCase):
    """
    Test cases for the delete_podcast_data task.
    """

    def setUp(self):
        """
        Set up test data for delete_podcast_data task.
        """
        super().setUp()
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

    def test_delete_podcast_data_success(self):
        """
        Test successful deletion of podcast data.
        """
        self.assertTrue(os.path.exists(self.file_path))
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(Episode.objects.filter(id=self.episode.id).exists())
        self.assertFalse(
            RehostedMedia.objects.filter(
                media_guid=self.rehosted_media.media_guid
            ).exists()
        )
        self.assertFalse(os.path.exists(self.file_path))

    def test_delete_podcast_data_podcast_not_found(self):
        """
        Test deletion when podcast is not found.
        """
        delete_podcast_data(uuid.uuid4())

    def test_delete_podcast_data_file_already_deleted(self):
        """
        Test deletion when file is already deleted.
        """
        os.remove(self.file_path)
        delete_podcast_data(self.podcast.id)
        self.assertFalse(Podcast.objects.filter(id=self.podcast.id).exists())
        self.assertFalse(
            RehostedMedia.objects.filter(
                media_guid=self.rehosted_media.media_guid
            ).exists()
        )


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class EpisodeRetentionTest(PodcastTestCase):
    """
    Test cases for the _enforce_episode_limit method in FeedManager.
    """

    def setUp(self):
        super().setUp()
        self.feed_manager = FeedManager(self.podcast)
        # Create multiple episodes with different publication dates
        Episode.objects.all().delete()
        self.episodes = []
        for i in range(10):
            episode = Episode.objects.create(
                podcast=self.podcast,
                title=f"Episode {i}",
                guid=f"guid-{i}",
                pub_date=timezone.now() - timezone.timedelta(days=i),
                original_audio_url=f"http://example.com/{i}.mp3",
            )
            self.episodes.append(episode)

    def test_enforce_episode_limit_custom_limit(self):
        """
        Test that _enforce_episode_limit respects a custom per-podcast limit.
        """
        self.podcast.max_episodes = 3
        self.podcast.save()

        self.feed_manager._enforce_episode_limit()  # pylint: disable=protected-access

        self.assertEqual(self.podcast.episodes.count(), 3)
        # Newest episodes (0, 1, 2) should remain
        remaining_guids = self.podcast.episodes.values_list('guid', flat=True)
        self.assertIn('guid-0', remaining_guids)
        self.assertIn('guid-1', remaining_guids)
        self.assertIn('guid-2', remaining_guids)

    def test_enforce_episode_limit_unlimited(self):
        """
        Test that _enforce_episode_limit skips deletion when max_episodes is 0.
        """
        self.podcast.max_episodes = 0
        self.podcast.save()

        self.feed_manager._enforce_episode_limit()  # pylint: disable=protected-access

        self.assertEqual(self.podcast.episodes.count(), 10)

    @mock.patch("podcasts.tasks.FeedManager._delete_episode_media")
    def test_enforce_episode_limit_deletes_media(self, mock_delete_media):
        """
        Test that _enforce_episode_limit calls _delete_episode_media for deleted episodes.
        """
        self.podcast.max_episodes = 5
        self.podcast.save()

        self.feed_manager._enforce_episode_limit()  # pylint: disable=protected-access

        self.assertEqual(self.podcast.episodes.count(), 5)
        self.assertEqual(mock_delete_media.call_count, 5)
