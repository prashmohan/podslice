"""
Tests for the Gemini model fallback logic.
"""
from unittest import mock
from django.test import TestCase
from podcasts.tasks import AdManager
from podcasts.models import Podcast, Episode

class GeminiFallbackTest(TestCase):
    """
    Test cases for the Gemini model fallback logic.
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            rss_url="http://example.com/feed.xml",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            guid="guid-123",
            pub_date="2026-04-28T12:00:00Z",
            original_audio_url="http://example.com/audio.mp3"
        )
        self.ad_manager = AdManager(self.episode)

    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    def test_gemini_fallback_success(self, mock_gen_model, mock_upload):
        """
        Test that the fallback model is used if the primary model fails.
        """
        mock_primary_model = mock.Mock()
        mock_primary_model.generate_content.side_effect = Exception("Quota exceeded")
        
        mock_fallback_model = mock.Mock()
        mock_fallback_model.generate_content.return_value = mock.Mock(text='[{"start": 10, "end": 20}]')
        
        mock_gen_model.side_effect = [mock_primary_model, mock_fallback_model]
        mock_upload.return_value = "fake_file"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEY = "fake-key"
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            
            result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3")
            
            self.assertEqual(result, '[{"start": 10, "end": 20}]')
            self.assertEqual(mock_gen_model.call_count, 2)

    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    def test_gemini_all_fail(self, mock_gen_model, mock_upload):
        """
        Test that an empty list is returned if all models fail.
        """
        mock_model = mock.Mock()
        mock_model.generate_content.side_effect = Exception("General error")
        mock_gen_model.return_value = mock_model
        mock_upload.return_value = "fake_file"

        result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3")
        
        self.assertEqual(result, "[]")
        self.assertEqual(mock_gen_model.call_count, 2)
