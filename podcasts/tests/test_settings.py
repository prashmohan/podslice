import importlib
import os
from unittest import mock
from django.test import TestCase
from podslice import settings


class SettingsGeminiConfigTest(TestCase):
    def tearDown(self):
        super().tearDown()
        importlib.reload(settings)

    def test_gemini_api_keys_parsing_comma_separated(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "key1, key2, key3 "}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2", "key3"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_empty_elements_stripped(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": " key1 , , key2 ,   "}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["key1", "key2"])
            self.assertEqual(settings.GEMINI_API_KEY, "key1")

    def test_gemini_api_keys_fallback_to_single_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "legacy-key"}):
            os.environ.pop("GEMINI_API_KEYS", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["legacy-key"])
            self.assertEqual(settings.GEMINI_API_KEY, "legacy-key")

    def test_gemini_api_keys_fallback_when_keys_is_whitespace(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "   ", "GEMINI_API_KEY": "legacy-key"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, ["legacy-key"])
            self.assertEqual(settings.GEMINI_API_KEY, "legacy-key")

    def test_gemini_api_keys_empty_when_neither_set(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_API_KEYS", None)
            os.environ.pop("GEMINI_API_KEY", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_API_KEYS, [])
            self.assertEqual(settings.GEMINI_API_KEY, "")

    def test_gemini_max_retry_delay_sec_default(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_MAX_RETRY_DELAY_SEC", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MAX_RETRY_DELAY_SEC, 30)

    def test_gemini_max_retry_delay_sec_custom(self):
        with mock.patch.dict(os.environ, {"GEMINI_MAX_RETRY_DELAY_SEC": "45"}):
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MAX_RETRY_DELAY_SEC, 45)

    def test_gemini_models_default(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("GEMINI_MODEL", None)
            os.environ.pop("FALLBACK_GEMINI_MODEL", None)
            importlib.reload(settings)
            self.assertEqual(settings.GEMINI_MODEL, "gemini-3.8-flash")
            self.assertEqual(settings.FALLBACK_GEMINI_MODEL, "gemini-3.5-flash-lite")
