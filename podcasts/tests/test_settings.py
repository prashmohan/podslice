"""
Unit tests for Gemini configuration parsing in settings.
"""
import importlib
import os
from unittest import mock
from django.test import TestCase
from podslice import settings


class SettingsGeminiConfigTest(TestCase):
    """Test suite for environment variable parsing in settings.py."""

    def tearDown(self):
        super().tearDown()
        importlib.reload(settings)

    def test_gemini_api_keys_parsing_comma_separated(self):
        """Verify comma-separated keys are parsed into a list."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "key1, key2, key3 "}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2", "key3"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_empty_elements_stripped(self):
        """Verify empty and whitespace elements are stripped from GEMINI_API_KEYS."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": " key1 , , key2 ,   "}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_fallback_to_single_key(self):
        """Verify GEMINI_API_KEYS falls back to GEMINI_API_KEY when unset."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "legacy-key"}):
            os.environ.pop("GEMINI_API_KEYS", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["legacy-key"])
            self.assertEqual(settings.GEMINI_API_KEY, "legacy-key")

    def test_gemini_api_keys_fallback_when_keys_is_whitespace(self):
        """Verify GEMINI_API_KEYS falls back to single key when keys string is whitespace."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "   ", "GEMINI_API_KEY": "legacy-key"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["legacy-key"])
            self.assertEqual(settings.GEMINI_API_KEY, "legacy-key")

    def test_gemini_api_keys_empty_when_neither_set(self):
        """Verify GEMINI_API_KEYS is empty list when neither environment variable is set."""
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_API_KEYS", None)
            os.environ.pop("GEMINI_API_KEY", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, [])
            self.assertEqual(settings.GEMINI_API_KEY, "")

    def test_gemini_max_retry_delay_sec_default(self):
        """Verify GEMINI_MAX_RETRY_DELAY_SEC defaults to 30."""
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_MAX_RETRY_DELAY_SEC", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MAX_RETRY_DELAY_SEC, 30)

    def test_gemini_max_retry_delay_sec_custom(self):
        """Verify custom GEMINI_MAX_RETRY_DELAY_SEC is parsed as integer."""
        with mock.patch.dict(os.environ, {"GEMINI_MAX_RETRY_DELAY_SEC": "45"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MAX_RETRY_DELAY_SEC, 45)

    def test_gemini_models_default(self):
        """Verify default GEMINI_MODEL and FALLBACK_GEMINI_MODEL settings."""
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_MODEL", None)
            os.environ.pop("FALLBACK_GEMINI_MODEL", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MODEL, "gemini-3.8-flash")
            self.assertEqual(settings.FALLBACK_GEMINI_MODEL, "gemini-3.5-flash-lite")

    def test_gemini_api_keys_double_quotes_stripped(self):
        """Verify double quotes surrounding GEMINI_API_KEYS string are stripped."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": '"key1, key2, key3"'}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2", "key3"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_single_quotes_stripped(self):
        """Verify single quotes surrounding GEMINI_API_KEYS string are stripped."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "'key1, key2'"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_individual_token_quotes_stripped(self):
        """Verify individual quoted key tokens have their quotes stripped."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": '"key1", \'key2\', "key3"'}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2", "key3"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_key_single_fallback_quotes_stripped(self):
        """Verify quotes are stripped from singular GEMINI_API_KEY fallback."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": '"legacy-key"'}):
            os.environ.pop("GEMINI_API_KEYS", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["legacy-key"])
            self.assertEqual(settings.GEMINI_API_KEY, "legacy-key")

    def test_gemini_api_keys_empty_quotes_treated_as_empty(self):
        """Verify empty quotes are cleanly parsed as empty lists and empty strings."""
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": '""', "GEMINI_API_KEY": "''"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, [])
            self.assertEqual(settings.GEMINI_API_KEY, "")
