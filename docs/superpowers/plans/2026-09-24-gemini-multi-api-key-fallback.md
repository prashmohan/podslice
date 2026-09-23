# Gemini Multi-API-Key Fallback, Response MIME Type & Retry Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement multi-API-key support for Gemini with primary-model-first rotation, intelligent 429 retry handling, strict `application/json` output, and resilient timestamp parsing.

**Architecture:** Parse `GEMINI_API_KEYS` from settings; rotate across all keys on `GEMINI_MODEL` first, sleeping once if all keys encounter 429; fall back to `FALLBACK_GEMINI_MODEL` across all keys if primary fails; isolate File API uploads per key; enforce `response_mime_type="application/json"` and sanitize timestamps.

**Tech Stack:** Python 3.12, Django 5.x, `google-generativeai`, Docker.

**Spec:** `docs/superpowers/specs/2026-09-24-gemini-multi-api-key-fallback-design.md`

## Global Constraints
- `GEMINI_API_KEYS` supports comma-delimited strings with fallback to `GEMINI_API_KEY`.
- `GEMINI_MAX_RETRY_DELAY_SEC` defaults to 30 seconds.
- Every API key maintains its own audio file upload handle via `uploaded_files: Dict[str, Any]`.
- All tests must pass via `docker exec podslice-app-1 python manage.py test podcasts`.

## Review Focus
1. Unquoted or string `MM:SS.sss` timestamps in model output must convert to float seconds without crashing `json.loads`.
2. Model preambles or chain-of-thought text outside JSON blocks must not break extraction.
3. Rapid 429 errors must rotate through all available keys before any sleep.
4. Uploading to Google File API must never be called twice for the same API key in a single analysis run.
5. If all models and keys fail, `"[]"` must be returned and original audio preserved as `COMPLETE`.

---

### Task 1: Settings & Environment Configuration for Multi-Key

**Files:**
- Modify: `podslice/settings.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `podcasts/tests/test_settings.py` (or create if absent)

**Interfaces:**
- Produces: `settings.GEMINI_API_KEYS: List[str]`, `settings.GEMINI_MAX_RETRY_DELAY_SEC: int`.

- [ ] **Step 1: Write the failing test for settings configuration**

Create `podcasts/tests/test_settings.py`:
```python
from unittest import mock
import os
from django.test import TestCase

class SettingsGeminiConfigTest(TestCase):
    def test_gemini_api_keys_parsing_comma_separated(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEYS": "key1, key2, key3 "}):
            # Re-evaluate logic or import helper
            from podslice import settings
            keys = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
            self.assertEqual(keys, ["key1", "key2", "key3"])

    def test_gemini_api_keys_fallback_to_single_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "legacy-key"}, clear=True):
            raw = os.environ.get("GEMINI_API_KEYS", "").strip()
            keys = [k.strip() for k in raw.split(",") if k.strip()] if raw else [os.environ.get("GEMINI_API_KEY")]
            self.assertEqual(keys, ["legacy-key"])
```

- [ ] **Step 2: Run test to verify it passes/fails**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_settings`

- [ ] **Step 3: Update `podslice/settings.py`, `.env.example`, and `README.md`**

In `podslice/settings.py`:
```python
_raw_gemini_keys = os.environ.get("GEMINI_API_KEYS", "").strip()
if _raw_gemini_keys:
    GEMINI_API_KEYS = [k.strip() for k in _raw_gemini_keys.split(",") if k.strip()]
elif os.environ.get("GEMINI_API_KEY"):
    GEMINI_API_KEYS = [os.environ.get("GEMINI_API_KEY").strip()]
else:
    GEMINI_API_KEYS = []

GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
FALLBACK_GEMINI_MODEL = os.environ.get("FALLBACK_GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_MAX_RETRY_DELAY_SEC = int(os.environ.get("GEMINI_MAX_RETRY_DELAY_SEC", 30))
```

Update `.env.example` and `README.md` to document `GEMINI_API_KEYS`.

- [ ] **Step 4: Run test suite**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_settings`

- [ ] **Step 5: Commit**

```bash
git add podslice/settings.py .env.example README.md podcasts/tests/test_settings.py
git commit -m "feat(settings): support comma-separated GEMINI_API_KEYS and retry delay"
```

---

### Task 2: Response MIME Type & Resilient JSON / Timestamp Parsing

**Files:**
- Modify: `podcasts/tasks.py:121-160`
- Test: `podcasts/tests/test_parsing.py`

**Interfaces:**
- Modifies: `AdManager._parse_ad_segments(gemini_response: str) -> List[Dict[str, float]]`
- Enforces: Output format `application/json`, extracts array from markdown/preambles, normalizes string timestamps to float.

- [ ] **Step 1: Write failing tests for robust JSON parsing**

Create `podcasts/tests/test_parsing.py`:
```python
from django.test import TestCase
from podcasts.models import Podcast, Episode
from podcasts.tasks import AdManager

class AdSegmentsParsingTest(TestCase):
    def setUp(self):
        podcast = Podcast.objects.create(title="P", rss_url="http://e.com/rss")
        self.episode = Episode.objects.create(
            podcast=podcast, title="E", guid="g", pub_date="2026-09-24T00:00:00Z", original_audio_url="http://e.com/a.mp3"
        )
        self.mgr = AdManager(self.episode)

    def test_parse_direct_json_array(self):
        res = '[{"start": 10.5, "end": 20.0, "label": "ad"}]'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 10.5)

    def test_parse_markdown_with_preamble(self):
        res = 'Here is the analysis:\n```json\n[{"start": 5.0, "end": 15.0, "label": "ad"}]\n```'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 5.0)

    def test_parse_string_timestamps(self):
        res = '[{"start": "01:30.500", "end": "02:00.000", "label": "ad"}]'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 90.5)
        self.assertEqual(parsed[0]["end"], 120.0)

    def test_invalid_segments_discarded(self):
        res = '[{"start": 50.0, "end": 40.0}]' # start >= end
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_parsing`

- [ ] **Step 3: Implement resilient parsing in `_parse_ad_segments`**

In `podcasts/tasks.py`:
- Helper to parse timestamp strings `MM:SS` or `HH:MM:SS` into float.
- Try `json.loads(gemini_response.strip())`.
- If error, try code block regex: `re.search(r"```(?:json)?\s*(\[.*?\])\s*```", gemini_response, re.DOTALL)`.
- If error, try outermost brackets: `re.search(r"(\[.*\])", gemini_response, re.DOTALL)`.
- Validate items have `start` and `end`, convert to float, filter out `start >= end`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_parsing`

- [ ] **Step 5: Commit**

```bash
git add podcasts/tasks.py podcasts/tests/test_parsing.py
git commit -m "feat(parsing): add resilient JSON extraction and timestamp normalization"
```

---

### Task 3: Multi-API-Key Rotation, Per-Key Uploads & Retry Strategy

**Files:**
- Modify: `podcasts/tasks.py:84-120`
- Test: `podcasts/tests/test_fallback.py`

**Interfaces:**
- Consumes: `settings.GEMINI_API_KEYS`, `settings.GEMINI_MODEL`, `settings.FALLBACK_GEMINI_MODEL`, `settings.GEMINI_MAX_RETRY_DELAY_SEC`.
- Modifies: `AdManager._get_ad_segments_from_gemini(audio_path: str, audio_duration_seconds: float) -> str`

- [ ] **Step 1: Write comprehensive failing tests in `test_fallback.py`**

Update `podcasts/tests/test_fallback.py` to test:
- Primary model rotates through multiple API keys on 429.
- Audio file upload is isolated per API key (cached in `uploaded_files`).
- All keys return 429 on primary model -> triggers sleep and retries primary model once.
- Primary failure after retry -> falls back to fallback model across keys.
- `generation_config={"response_mime_type": "application/json"}` is passed.

- [ ] **Step 2: Run tests to verify failure**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_fallback`

- [ ] **Step 3: Implement multi-key and retry logic in `_get_ad_segments_from_gemini`**

In `podcasts/tasks.py`:
- Extract keys from `settings.GEMINI_API_KEYS` (or single `settings.GEMINI_API_KEY`).
- Maintain `uploaded_files: Dict[str, Any] = {}`.
- Implement `_call_model_with_key(model_name, api_key, audio_path, prompt, uploaded_files)`.
- Enforce `generation_config={"response_mime_type": "application/json"}`.
- Rotate keys on primary model.
- If all 429, sleep `min(retry_delays)` up to `GEMINI_MAX_RETRY_DELAY_SEC` and retry primary.
- If primary exhausted, rotate keys on fallback model.
- Return `"[]"` if everything fails.

- [ ] **Step 4: Run tests to verify pass**

Run: `docker exec podslice-app-1 python manage.py test podcasts.tests.test_fallback`

- [ ] **Step 5: Commit**

```bash
git add podcasts/tasks.py podcasts/tests/test_fallback.py
git commit -m "feat(gemini): implement multi-API-key fallback, per-key upload isolation and 429 retry"
```

---

### Task 4: Full Regression Testing & Verification

**Files:**
- Test: All tests in `podcasts`

- [ ] **Step 1: Run full test suite in container**

Run: `docker exec podslice-app-1 python manage.py test podcasts`
Expected: 75+ tests PASS with 0 failures.

- [ ] **Step 2: Verify git status and clean working tree**

Run: `git status`

- [ ] **Step 3: Commit any remaining test artifacts and finalize**

```bash
git commit -m "chore: finalize multi-key fallback implementation and test suite"
```
