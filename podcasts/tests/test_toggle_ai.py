"""
Tests for the EpisodeToggleAIView view.
"""
import os
from unittest import mock
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from podcasts.tests.test_base import PodcastTestCase
from podcasts.models import Episode, RehostedMedia


@override_settings(MEDIA_ROOT=os.path.join(settings.BASE_DIR, "test_media"))
class EpisodeToggleAITest(PodcastTestCase):
    """
    Test cases for toggling AI processing on an episode.
    """

    def setUp(self):
        super().setUp()
        self.media_path = os.path.join(settings.MEDIA_ROOT, "toggle_test.mp3")
        with open(self.media_path, "wb") as f:
            f.write(b"fake data")

        self.rehosted_media = RehostedMedia.objects.create(
            media_guid=self.episode.rehosted_media_id,
            file_path=self.media_path,
            content_type="audio/mpeg"
        )
        self.episode.status = Episode.Status.COMPLETE
        self.episode.rehosted_audio_size = 1234
        self.episode.ad_segments = '{"test": 1}'
        self.episode.save()

    @mock.patch("podcasts.views.threading.Thread")
    def test_toggle_ai_disable_ai_processing(self, mock_thread):
        """
        Test toggling AI from enabled (False) to disabled (True).
        This should delete media files, delete RehostedMedia records,
        reset fields, and spawn a reprocessing thread.
        """
        self.episode.disable_ai_processing = False
        self.episode.save()

        url = reverse("episode-toggle-ai", kwargs={"episode_id": self.episode.id})
        response = self.client.post(url)

        # Should redirect back to the podcast status UI
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("podcast-status-ui", kwargs={"podcast_id": self.podcast.id})
        )

        self.episode.refresh_from_db()
        self.assertTrue(self.episode.disable_ai_processing)
        self.assertEqual(self.episode.status, Episode.Status.NEW)
        self.assertIsNone(self.episode.rehosted_media_id)
        self.assertEqual(self.episode.rehosted_audio_size, 0)
        self.assertIsNone(self.episode.ad_segments)

        # File and DB record should be deleted
        self.assertFalse(os.path.exists(self.media_path))
        self.assertFalse(RehostedMedia.objects.filter(pk=self.rehosted_media.pk).exists())

        # Background thread should be started
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

    @mock.patch("podcasts.views.threading.Thread")
    def test_toggle_ai_enable_ai_processing(self, mock_thread):
        """
        Test toggling AI from disabled (True) to enabled (False).
        This should update the field but NOT delete files or spawn a thread.
        """
        self.episode.disable_ai_processing = True
        self.episode.save()

        url = reverse("episode-toggle-ai", kwargs={"episode_id": self.episode.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)

        self.episode.refresh_from_db()
        self.assertFalse(self.episode.disable_ai_processing)
        # Status and properties should remain unchanged
        self.assertEqual(self.episode.status, Episode.Status.COMPLETE)
        self.assertEqual(self.episode.rehosted_media_id, self.rehosted_media.media_guid)
        self.assertEqual(self.episode.rehosted_audio_size, 1234)

        # File and DB record should still exist
        self.assertTrue(os.path.exists(self.media_path))
        self.assertTrue(RehostedMedia.objects.filter(pk=self.rehosted_media.pk).exists())

        # Thread should NOT be started
        mock_thread.assert_not_called()
