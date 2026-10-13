"""
Tests for Gemini multi-API-key fallback, per-key upload isolation, and 429 retry handling.
"""
from unittest import mock
from django.test import TestCase
from google.genai import errors as genai_errors
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
    @mock.patch("podcasts.tasks.genai.Client")
    def test_single_key_success_primary_model(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify normal operation with a single API key succeeding on the primary model.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "fake_file_handle"
        mock_client.files.upload.return_value = fake_file
        mock_client.models.generate_content.return_value = mock.Mock(
            text='[{"start": 10, "end": 20}]'
        )

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-alpha"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3", 60.0)

            self.assertEqual(result, '[{"start": 10, "end": 20}]')
            mock_client_cls.assert_called_once_with(api_key="key-alpha")
            mock_client.files.upload.assert_called_once_with(file="fake_path.mp3")
            mock_client.models.generate_content.assert_called_once()
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            self.assertEqual(call_kwargs["model"], "primary-model")
            self.assertEqual(call_kwargs["config"].response_mime_type, "application/json")
            mock_client.files.delete.assert_called_once_with(name="fake_file_handle")
            mock_sleep.assert_not_called()

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_key1_fails_429_key2_succeeds_primary_model(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify that if Key 1 hits 429, Key 2 is tried immediately on the primary model without sleeping.
        """
        client_k1 = mock.Mock()
        file_k1 = mock.Mock()
        file_k1.name = "file_k1"
        client_k1.files.upload.return_value = file_k1
        client_k1.models.generate_content.side_effect = Exception("429 ResourceExhausted: quota exceeded")

        client_k2 = mock.Mock()
        file_k2 = mock.Mock()
        file_k2.name = "file_k2"
        client_k2.files.upload.return_value = file_k2
        client_k2.models.generate_content.return_value = mock.Mock(text='[{"start": 30, "end": 45}]')

        def client_side_effect(api_key):
            if api_key == "key-1":
                return client_k1
            return client_k2

        mock_client_cls.side_effect = client_side_effect

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("fake_path.mp3", 60.0)

            self.assertEqual(result, '[{"start": 30, "end": 45}]')
            self.assertEqual(mock_client_cls.call_count, 2)
            mock_client_cls.assert_has_calls([
                mock.call(api_key="key-1"),
                mock.call(api_key="key-2"),
            ])
            # Sleep should NOT be called on fast key rotation
            mock_sleep.assert_not_called()
            # Primary model used for both attempts
            self.assertEqual(client_k1.models.generate_content.call_count, 1)
            self.assertEqual(client_k2.models.generate_content.call_count, 1)
            self.assertEqual(client_k1.models.generate_content.call_args.kwargs["model"], "primary-model")
            self.assertEqual(client_k2.models.generate_content.call_args.kwargs["model"], "primary-model")
            # Files cleaned up
            client_k1.files.delete.assert_called_once_with(name="file_k1")
            client_k2.files.delete.assert_called_once_with(name="file_k2")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_per_key_upload_isolation_and_caching(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify genai.upload_file is called once per distinct API key, and cached file handle is reused.
        """
        # Scenario:
        # Pass 1: Key 1 fails (429), Key 2 fails (429) -> uploaded once for each
        # Pass 2 (Retry): Key 1 succeeds -> should reuse Key 1 file handle, no new upload
        client_k1 = mock.Mock()
        file_k1 = mock.Mock()
        file_k1.name = "file_handle_key1"
        client_k1.files.upload.return_value = file_k1
        client_k1.models.generate_content.side_effect = [
            Exception("429 ResourceExhausted: retry in 5s"),
            mock.Mock(text='[{"start": 5, "end": 15}]'),
        ]

        client_k2 = mock.Mock()
        file_k2 = mock.Mock()
        file_k2.name = "file_handle_key2"
        client_k2.files.upload.return_value = file_k2
        client_k2.models.generate_content.side_effect = [
            Exception("429 ResourceExhausted: retry in 5s"),
        ]

        mock_client_cls.side_effect = lambda api_key: client_k1 if api_key == "key-1" else client_k2

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 100.0)

            self.assertEqual(result, '[{"start": 5, "end": 15}]')
            # upload should be called exactly once for each client
            self.assertEqual(client_k1.files.upload.call_count, 1)
            self.assertEqual(client_k2.files.upload.call_count, 1)
            client_k1.files.upload.assert_called_once_with(file="audio.mp3")
            client_k2.files.upload.assert_called_once_with(file="audio.mp3")
            # Client creation should be once per key
            self.assertEqual(mock_client_cls.call_count, 2)
            # Key 1 retry must have used the cached file_k1
            retry_contents = client_k1.models.generate_content.call_args_list[1].kwargs["contents"]
            self.assertIn(file_k1, retry_contents)
            # Cleanup should delete files for both keys
            client_k1.files.delete.assert_called_once_with(name="file_handle_key1")
            client_k2.files.delete.assert_called_once_with(name="file_handle_key2")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_all_keys_fail_429_sleeps_and_retries_primary(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify that when all keys fail with 429, sleep(min(delays)) is executed and primary is retried.
        """
        client_k1 = mock.Mock()
        file_1 = mock.Mock()
        file_1.name = "file_1"
        client_k1.files.upload.return_value = file_1
        client_k1.models.generate_content.side_effect = [
            Exception("ResourceExhausted: retry in 15s"),
            mock.Mock(text='[{"start": 0, "end": 10}]'),
        ]

        client_k2 = mock.Mock()
        file_2 = mock.Mock()
        file_2.name = "file_2"
        client_k2.files.upload.return_value = file_2
        client_k2.models.generate_content.side_effect = [
            Exception("429 rate limit exceeded. seconds: 6"),
        ]

        mock_client_cls.side_effect = lambda api_key: client_k1 if api_key == "key-1" else client_k2

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1", "key-2"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 50.0)

            self.assertEqual(result, '[{"start": 0, "end": 10}]')
            # min(15, 6) = 6.0
            mock_sleep.assert_called_once_with(6.0)
            client_k1.files.delete.assert_called_once_with(name="file_1")
            client_k2.files.delete.assert_called_once_with(name="file_2")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_primary_fails_after_retry_falls_back_to_fallback_model(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify that if primary fails across all keys (even after retry), fallback model is tried.
        """
        # 1 key:
        # 1. Primary try 1 -> 429
        # 2. Primary retry -> 429
        # 3. Fallback try 1 -> success
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "file_handle"
        mock_client.files.upload.return_value = fake_file

        mock_client.models.generate_content.side_effect = [
            Exception("429 ResourceExhausted"),
            Exception("429 ResourceExhausted"),
            mock.Mock(text='[{"start": 12, "end": 24}]'),
        ]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 30

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 12, "end": 24}]')
            mock_sleep.assert_called_once_with(10.0)  # default delay when not specified in 429 msg
            self.assertEqual(mock_client.models.generate_content.call_count, 3)
            # Verify third call was with fallback-model
            models_called = [
                call.kwargs["model"]
                for call in mock_client.models.generate_content.call_args_list
            ]
            self.assertEqual(models_called, ["primary-model", "primary-model", "fallback-model"])
            mock_client.files.delete.assert_called_once_with(name="file_handle")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_all_keys_and_models_fail_returns_empty_list(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify that if primary (pass 1 + retry) and fallback fail on all keys, '[]' is returned.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "fake_file"
        mock_client.files.upload.return_value = fake_file
        mock_client.models.generate_content.side_effect = Exception("500 Internal Server Error")

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, "[]")
            mock_sleep.assert_not_called()  # No 429 encountered, no sleep
            mock_client.files.delete.assert_called_once_with(name="fake_file")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_retry_delay_capped_at_max(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify retry delay is capped at GEMINI_MAX_RETRY_DELAY_SEC.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "file_handle"
        mock_client.files.upload.return_value = fake_file

        mock_client.models.generate_content.side_effect = [
            Exception("429 ResourceExhausted: retry in 60s"),
            mock.Mock(text='[{"start": 1, "end": 2}]'),
        ]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"
            mock_settings.GEMINI_MAX_RETRY_DELAY_SEC = 25

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 1, "end": 2}]')
            mock_sleep.assert_called_once_with(25.0)
            mock_client.files.delete.assert_called_once_with(name="file_handle")

    @mock.patch("podcasts.tasks.genai.Client")
    def test_no_api_keys_configured(
        self, mock_client_cls
    ):
        """
        Verify that if no API keys are configured, '[]' is returned immediately.
        """
        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = []
            mock_settings.GEMINI_API_KEY = ""

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, "[]")
            mock_client_cls.assert_not_called()

    @mock.patch("podcasts.tasks.genai.Client")
    def test_fallback_to_legacy_gemini_api_key_when_keys_empty(
        self, mock_client_cls
    ):
        """
        Verify backward compatibility: if GEMINI_API_KEYS is empty, GEMINI_API_KEY is used.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "file_handle"
        mock_client.files.upload.return_value = fake_file
        mock_client.models.generate_content.return_value = mock.Mock(
            text='[{"start": 0, "end": 5}]'
        )

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = []
            mock_settings.GEMINI_API_KEY = "legacy-single-key"
            mock_settings.GEMINI_MODEL = "primary-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 0, "end": 5}]')
            mock_client_cls.assert_called_once_with(api_key="legacy-single-key")
            mock_client.files.delete.assert_called_once_with(name="file_handle")

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

    def test_is_rate_limit_error_with_genai_api_error(self):
        """
        Verify that google.genai.errors.APIError instances with 429 are recognized as rate limit errors.
        """
        err_429 = genai_errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Resource exhausted"}})
        self.assertTrue(AdManager._is_rate_limit_error(err_429))
        err_500 = genai_errors.APIError(500, {"error": {"code": 500, "status": "INTERNAL", "message": "Server error"}})
        self.assertFalse(AdManager._is_rate_limit_error(err_500))

    def test_extract_retry_delay_from_genai_api_error_details(self):
        """
        Verify that retry delay is correctly extracted from google.genai.errors.APIError details.
        """
        err = genai_errors.APIError(429, {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17.5s"}]}})
        self.assertEqual(AdManager._extract_retry_delay(err), 17.5)

    @mock.patch("podcasts.tasks.genai.Client")
    def test_generation_config_response_mime_type(
        self, mock_client_cls
    ):
        """
        Verify that GenerateContentConfig with response_mime_type="application/json" is always passed.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "file_handle"
        mock_client.files.upload.return_value = fake_file
        mock_client.models.generate_content.return_value = mock.Mock(text='[]')

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "gemini-3.8-flash"

            self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            mock_client.models.generate_content.assert_called_once()
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            self.assertEqual(call_kwargs["model"], "gemini-3.8-flash")
            self.assertEqual(call_kwargs["config"].response_mime_type, "application/json")
            mock_client.files.delete.assert_called_once_with(name="file_handle")

    @mock.patch("podcasts.tasks.time.sleep")
    @mock.patch("podcasts.tasks.genai.Client")
    def test_non_429_error_does_not_sleep_before_fallback(
        self, mock_client_cls, mock_sleep
    ):
        """
        Verify that non-429 error on primary model falls back immediately without sleeping.
        """
        mock_client = mock.Mock()
        mock_client_cls.return_value = mock_client
        fake_file = mock.Mock()
        fake_file.name = "file_handle"
        mock_client.files.upload.return_value = fake_file

        mock_client.models.generate_content.side_effect = [
            Exception("500 Internal Server Error"),
            mock.Mock(text='[{"start": 1, "end": 2}]'),
        ]

        with mock.patch("podcasts.tasks.settings") as mock_settings:
            mock_settings.GEMINI_API_KEYS = ["key-1"]
            mock_settings.GEMINI_MODEL = "primary-model"
            mock_settings.FALLBACK_GEMINI_MODEL = "fallback-model"

            result = self.ad_manager._get_ad_segments_from_gemini("audio.mp3", 60.0)

            self.assertEqual(result, '[{"start": 1, "end": 2}]')
            mock_sleep.assert_not_called()
            self.assertEqual(mock_client.models.generate_content.call_count, 2)
            mock_client.files.delete.assert_called_once_with(name="file_handle")

