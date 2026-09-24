"""
Tests for Gemini multi-API-key fallback, per-key upload isolation, and 429 retry handling.
"""
from unittest import mock
from django.test import TestCase
from podcasts.tasks import AdManager
from podcasts.models import Podcast, Episode


class GeminiFallbackTest(TestCase):
    """
    Test cases for Gemini multi-API-key fallback, upload caching, and retry logic.
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
            original_audio_url="http://example.com/audio.mp3",
        )
        self.ad_manager = AdManager(self.episode)

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_single_key_success_primary_model(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify normal operation with a single API key succeeding on the primary model.
        """
        mock_model = mock.Mock()
        mock_model.generate_content.return_value = mock.Mock(
            text='[{"start": 10, "end": 20}]'
        )
        mock_gen_model.return_value = mock_model
        mock_upload.return_value = "fake_file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-alpha"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3", 60.0)

            self.assertEqual(result, '[{"start": 10, "end": 20}]')
            mock_configure.assert_called_once_with(api_key="key-alpha")
            mock_upload.assert_called_once_with(path="fake_path.mp3")
            mock_gen_model.assert_called_once_with(
                "primary-model",
                generation_config={"response_mime_type": "application/json"},
            )
            mock_sleep.assert_not_called()

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_key1_fails_429_key2_succeeds_primary_model(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that if Key 1 hits 429, Key 2 is tried immediately on the primary model without sleeping.
        """
        model_key1 = mock.Mock()
        model_key1.generate_content.side_effect = Exception("429 ResourceExhausted: quota exceeded")

        model_key2 = mock.Mock()
        model_key2.generate_content.return_value = mock.Mock(text='[{"start": 30, "end": 45}]')

        mock_gen_model.side_effect = [model_key1, model_key2]
        mock_upload.side_effect = ["file_k1", "file_k2"]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3", 60.0)

            self.assertEqual(result, '[{"start": 30, "end": 45}]')
            self.assertEqual(mock_configure.call_count, 2)
            mock_configure.assert_has_calls([
                mock.call(api_key="key-1"),
                mock.call(api_key="key-2"),
            ])
            # Sleep should NOT be called on fast key rotation
            mock_sleep.assert_not_called()
            # Primary model used for both attempts
            self.assertEqual(mock_gen_model.call_count, 2)
            mock_gen_model.assert_has_calls([
                mock.call("primary-model", generation_config={"response_mime_type": "application/json"}),
                mock.call("primary-model", generation_config={"response_mime_type": "application/json"}),
            ])

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_per_key_upload_isolation_and_caching(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify genai.upload_file is called once per distinct API key, and cached file handle is reused.
        """
        # Scenario:
        # Pass 1: Key 1 fails (429), Key 2 fails (429) -> uploaded once for each
        # Pass 2 (Retry): Key 1 succeeds -> should reuse Key 1 file handle, no new upload
        model_p1_k1 = mock.Mock()
        model_p1_k1.generate_content.side_effect = Exception("429 ResourceExhausted: retry in 5s")
        model_p1_k2 = mock.Mock()
        model_p1_k2.generate_content.side_effect = Exception("429 ResourceExhausted: retry in 5s")
        model_p2_k1 = mock.Mock()
        model_p2_k1.generate_content.return_value = mock.Mock(text='[{"start": 5, "end": 15}]')

        mock_gen_model.side_effect = [model_p1_k1, model_p1_k2, model_p2_k1]
        mock_upload.side_effect = ["file_handle_key1", "file_handle_key2"]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 100.0)

            self.assertEqual(result, '[{"start": 5, "end": 15}]')
            # upload_file should be called exactly twice: once for key-1, once for key-2
            self.assertEqual(mock_upload.call_count, 2)
            mock_upload.assert_has_calls([
                mock.call(path="audio.mp3"),
                mock.call(path="audio.mp3"),
            ])
            # Key 1 retry must have used the cached file_handle_key1
            generate_content_args = model_p2_k1.generate_content.call_args[0][0]
            self.assertIn("file_handle_key1", generate_content_args)

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_all_keys_fail_429_sleeps_and_retries_primary(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that when all keys fail with 429, sleep(min(delays)) is executed and primary is retried.
        """
        model_k1_try1 = mock.Mock()
        model_k1_try1.generate_content.side_effect = Exception("ResourceExhausted: retry in 15s")
        model_k2_try1 = mock.Mock()
        model_k2_try1.generate_content.side_effect = Exception("429 rate limit exceeded. seconds: 6")

        model_k1_retry = mock.Mock()
        model_k1_retry.generate_content.return_value = mock.Mock(text='[{"start": 0, "end": 10}]')

        mock_gen_model.side_effect = [model_k1_try1, model_k2_try1, model_k1_retry]
        mock_upload.side_effect = ["file_1", "file_2"]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 50.0)

            self.assertEqual(result, '[{"start": 0, "end": 10}]')
            # min(15, 6) = 6.0
            mock_sleep.assert_called_once_with(6.0)

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_primary_fails_after_retry_falls_back_to_fallback_model(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that if primary fails across all keys (even after retry), fallback model is tried.
        """
        # 1 key:
        # 1. Primary try 1 -> 429
        # 2. Primary retry -> 429
        # 3. Fallback try 1 -> success
        m_primary_1 = mock.Mock()
        m_primary_1.generate_content.side_effect = Exception("429 ResourceExhausted")
        m_primary_retry = mock.Mock()
        m_primary_retry.generate_content.side_effect = Exception("429 ResourceExhausted")
        m_fallback = mock.Mock()
        m_fallback.generate_content.return_value = mock.Mock(text='[{"start": 12, "end": 24}]')

        mock_gen_model.side_effect = [m_primary_1, m_primary_retry, m_fallback]
        mock_upload.return_value = "file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 12, "end": 24}]')
            mock_sleep.assert_called_once_with(10.0)  # default delay when not specified in 429 msg
            self.assertEqual(mock_gen_model.call_count, 3)
            # Verify third call was with fallback-model
            mock_gen_model.assert_has_calls([
                mock.call("primary-model", generation_config={"response_mime_type": "application/json"}),
                mock.call("primary-model", generation_config={"response_mime_type": "application/json"}),
                mock.call("fallback-model", generation_config={"response_mime_type": "application/json"}),
            ])

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_all_keys_and_models_fail_returns_empty_list(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that if primary (pass 1 + retry) and fallback fail on all keys, '[]' is returned.
        """
        fail_model = mock.Mock()
        fail_model.generate_content.side_effect = Exception("500 Internal Server Error")
        mock_gen_model.return_value = fail_model
        mock_upload.return_value = "fake_file"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, "[]")
            mock_sleep.assert_not_called()  # No 429 encountered, no sleep

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_retry_delay_capped_at_max(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify retry delay is capped at GEMINI_MAX_RETRY_DELAY_SEC.
        """
        m_primary_1 = mock.Mock()
        m_primary_1.generate_content.side_effect = Exception("429 ResourceExhausted: retry in 60s")
        m_primary_retry = mock.Mock()
        m_primary_retry.generate_content.return_value = mock.Mock(text='[{"start": 1, "end": 2}]')

        mock_gen_model.side_effect = [m_primary_1, m_primary_retry]
        mock_upload.return_value = "file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 25

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 1, "end": 2}]')
            mock_sleep.assert_called_once_with(25.0)

    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_no_api_keys_configured(
        self, mock_configure, mock_gen_model, mock_upload
    ):
        """
        Verify that if no API keys are configured, '[]' is returned immediately.
        """
        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = []
            mock_settings.GEMINI_API_KEY = ""

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, "[]")
            mock_configure.assert_not_called()
            mock_upload.assert_not_called()
            mock_gen_model.assert_not_called()

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_fallback_to_legacy_gemini_api_key_when_keys_empty(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify backward compatibility: if GEMINI_API_KEYS is empty, GEMINI_API_KEY is used.
        """
        mock_model = mock.Mock()
        mock_model.generate_content.return_value = mock.Mock(text='[{"start": 0, "end": 5}]')
        mock_gen_model.return_value = mock_model
        mock_upload.return_value = "file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = []
            mock_settings.GEMINI_API_KEY = "legacy-single-key"
            mock_settings.GEMINI_MODEL = "primary-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 0, "end": 5}]')
            mock_configure.assert_called_once_with(api_key="legacy-single-key")

    def test_is_rate_limit_error_detection(self):
        """
        Verify rate limit error detection for status codes, exception types, and messages.
        """
        # Code attribute
        class CodeError(Exception):
            code = 429
        self.assertTrue(AdManager._is_rate_limit_error(CodeError("error")))

        # Status code attribute
        class StatusCodeError(Exception):
            status_code = 429
        self.assertTrue(AdManager._is_rate_limit_error(StatusCodeError("error")))

        # Class name ResourceExhausted
        class ResourceExhausted(Exception):
            pass
        self.assertTrue(AdManager._is_rate_limit_error(ResourceExhausted("some message")))

        # Messages
        self.assertTrue(AdManager._is_rate_limit_error(Exception("429 Too Many Requests")))
        self.assertTrue(AdManager._is_rate_limit_error(Exception("Rate limit exceeded")))
        self.assertTrue(AdManager._is_rate_limit_error(Exception("Quota exceeded for project")))
        self.assertTrue(AdManager._is_rate_limit_error(Exception("RESOURCE_EXHAUSTED")))

        # Non-rate-limit errors
        self.assertFalse(AdManager._is_rate_limit_error(Exception("500 Internal Server Error")))
        self.assertFalse(AdManager._is_rate_limit_error(ValueError("Invalid argument")))

    def test_extract_retry_delay(self):
        """
        Verify extraction of retry delays from error attributes, regex messages, and defaults.
        """
        # Attribute with total_seconds()
        class DelayObj:
            def total_seconds(self):
                return 18.5
        class TimedeltaError(Exception):
            retry_delay = DelayObj()
        self.assertEqual(AdManager._extract_retry_delay(TimedeltaError()), 18.5)

        # Attribute with seconds
        class SecondsObj:
            seconds = 22
        class SecondsError(Exception):
            retry_delay = SecondsObj()
        self.assertEqual(AdManager._extract_retry_delay(SecondsError()), 22.0)

        # Direct float attribute
        class DirectError(Exception):
            retry_delay = 14.0
        self.assertEqual(AdManager._extract_retry_delay(DirectError()), 14.0)

        # Regex "retry in 12.5s"
        self.assertEqual(
            AdManager._extract_retry_delay(Exception("Resource exhausted: please retry in 12.5s.")),
            12.5,
        )

        # Regex "seconds: 7"
        self.assertEqual(
            AdManager._extract_retry_delay(Exception("ResourceExhausted: error { seconds: 7 }")),
            7.0,
        )

        # Default fallback
        self.assertEqual(
            AdManager._extract_retry_delay(Exception("429 ResourceExhausted without delay")),
            10.0,
        )

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_generation_config_response_mime_type(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that generation_config={"response_mime_type": "application/json"} is always passed.
        """
        mock_model = mock.Mock()
        mock_model.generate_content.return_value = mock.Mock(text='[]')
        mock_gen_model.return_value = mock_model
        mock_upload.return_value = "file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "gemini-3.8-flash"

            self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            mock_gen_model.assert_called_once_with(
                "gemini-3.8-flash",
                generation_config={"response_mime_type": "application/json"},
            )

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.upload_file")
    @mock.patch("podcasts.tasks.genai.GenerativeModel")
    @mock.patch("podcasts.tasks.genai.configure")
    def test_non_429_error_does_not_sleep_before_fallback(
        self, mock_configure, mock_gen_model, mock_upload, mock_sleep
    ):
        """
        Verify that non-429 error on primary model falls back immediately without sleeping.
        """
        mock_primary = mock.Mock()
        mock_primary.generate_content.side_effect = Exception("500 Internal Server Error")

        mock_fallback = mock.Mock()
        mock_fallback.generate_content.return_value = mock.Mock(text='[{"start": 1, "end": 2}]')

        mock_gen_model.side_effect = [mock_primary, mock_fallback]
        mock_upload.return_value = "file_handle"

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 1, "end": 2}]')
            mock_sleep.assert_not_called()
            self.assertEqual(mock_gen_model.call_count, 2)

