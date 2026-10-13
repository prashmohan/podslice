# Google GenAI SDK Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Podslice from deprecated `google-generativeai` to the unified `google-genai` SDK (`from google import genai`) while preserving multi-key rotation, fallback models, rate limit detection, telemetry tracking, and adding remote file cleanup.

**Architecture:** Initialize `genai.Client(api_key=key)` per key; use `client.files.upload(file=path)` for audio files; execute ad segment detection via `client.models.generate_content(model=..., contents=[prompt, file], config=...)` with structured JSON output schema; ensure remote uploaded files are cleaned up via `client.files.delete(name=file.name)` in a `finally` block; adapt rate-limit detection to `google.genai.errors.APIError`.

**Tech Stack:** Python 3.14 / 3.12, Django 5.2, `google-genai==2.30.0`, Pydantic 2.14, unittest / mock.

**Spec:** Direct user request to modernize `google-generativeai` to `google-genai` across `requirements.txt`, `podcasts/tasks.py`, `podcasts/tests/test_tasks.py`, and `podcasts/tests/test_fallback.py`.

## Global Constraints

- Never touch Docker or run docker/docker-compose commands; run all tests in the host virtualenv (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`).
- Strictly preserve multi-key rotation, 429 backoff retry, fallback model (`FALLBACK_GEMINI_MODEL`), and telemetry payload in `Episode.processing_metrics`.
- Use TDD: update test mocks/assertions first, verify failure, implement code, verify pass.
- Maintain high code quality and satisfy pylint checks.

## Review Focus

1. **Remote file cleanup failure does not abort processing:** If `client.files.delete` throws an error (e.g. network glitch or file already deleted), the exception is logged as a warning and suppressed so valid ad segments are still returned.
2. **File caching per key during retries/fallback:** An audio file must only be uploaded once per API key across primary model attempts, retries, and fallback model attempts; cleanup deletes each uploaded key's remote file once at the conclusion.
3. **APIError code extraction:** `google.genai.errors.APIError` provides `.code` (e.g. 429) and `.status` (e.g. "RESOURCE_EXHAUSTED"); `_is_rate_limit_error` accurately detects 429 across `APIError`, `ClientError`, and message strings.
4. **Structured JSON output format:** `client.models.generate_content` is passed `config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=...)`, and `response.text` yields valid JSON that `_parse_ad_segments` parses without schema disruption.
5. **Masked API key telemetry preservation:** `gemini_telemetry["attempts"]` continues to record `{ "model": model_name, "key_masked": masked, "status": "success" | 429 | "error", ... }`.

---

### Task 1: Update Dependencies in `requirements.txt`

**Files:**
- Modify: `requirements.txt:11`
- Test: Local virtual environment verification

**Interfaces:**
- Consumes: `google-genai==2.30.0`
- Produces: Updated `requirements.txt` without deprecated `google-generativeai`

- [ ] **Step 1: Check dependency status in requirements.txt**
Verify that line 11 specifies `google-generativeai==0.8.6`.

- [ ] **Step 2: Update `requirements.txt` to replace `google-generativeai` with `google-genai`**
Replace `google-generativeai==0.8.6` with `google-genai==2.30.0`.

- [ ] **Step 3: Verify package importability in venv**
Run: `/home/prmohan/projects/podslice/venv/bin/python -c "import google.genai; print(google.genai.__name__)"`
Expected: Output `google.genai`

- [ ] **Step 4: Commit**
```bash
git add requirements.txt
git commit -m "deps: replace deprecated google-generativeai with google-genai"
```

---

### Task 2: Pydantic Schema and Error Handling Adaptations in `podcasts/tasks.py`

**Files:**
- Modify: `podcasts/tasks.py:18-20, 86-155`
- Test: `podcasts/tests/test_fallback.py:319-389`

**Interfaces:**
- Consumes: `google.genai.errors.APIError`, `google.genai.types`
- Produces: `AdSegmentSchema`, enhanced `AdManager._is_rate_limit_error(exc)`, enhanced `AdManager._extract_retry_delay(exc)`

- [ ] **Step 1: Write failing tests for `APIError` in `test_fallback.py`**
Add tests to `GeminiFallbackTest` testing `_is_rate_limit_error` and `_extract_retry_delay` with `google.genai.errors.APIError` instances (code 429, details with `retryDelay`).

```python
from google.genai import errors as genai_errors

def test_is_rate_limit_error_with_genai_api_error(self):
    err_429 = genai_errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Resource exhausted"}})
    self.assertTrue(AdManager._is_rate_limit_error(err_429))
    err_500 = genai_errors.APIError(500, {"error": {"code": 500, "status": "INTERNAL", "message": "Server error"}})
    self.assertFalse(AdManager._is_rate_limit_error(err_500))

def test_extract_retry_delay_from_genai_api_error_details(self):
    err = genai_errors.APIError(429, {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17.5s"}]}})
    self.assertEqual(AdManager._extract_retry_delay(err), 17.5)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_fallback.GeminiFallbackTest.test_extract_retry_delay_from_genai_api_error_details`
Expected: FAIL (or returns default 10.0 instead of 17.5s)

- [ ] **Step 3: Implement `AdSegmentSchema`, update imports and error handlers in `podcasts/tasks.py`**
- Import `from google import genai` and `from google.genai import errors, types`.
- Define `AdSegmentSchema(BaseModel)` with fields `start: float`, `end: float`, `label: str`.
- Enhance `_is_rate_limit_error` to check `exc.code`, `exc.status`, and `errors.APIError`.
- Enhance `_extract_retry_delay` to parse `exc.details` containing `retryDelay`.

- [ ] **Step 4: Run test to verify it passes**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_fallback.GeminiFallbackTest.test_is_rate_limit_error_with_genai_api_error podcasts.tests.test_fallback.GeminiFallbackTest.test_extract_retry_delay_from_genai_api_error_details`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add podcasts/tasks.py podcasts/tests/test_fallback.py
git commit -m "feat(tasks): add AdSegmentSchema and enhance APIError handling"
```

---

### Task 3: Migrate `AdManager._get_ad_segments_from_gemini` and Add Remote Cleanup

**Files:**
- Modify: `podcasts/tasks.py:156-328`
- Test: `podcasts/tests/test_tasks.py`

**Interfaces:**
- Consumes: `genai.Client`, `client.files.upload(file=...)`, `client.files.delete(name=...)`, `client.models.generate_content(...)`
- Produces: `_get_ad_segments_from_gemini(audio_path, audio_duration_seconds) -> str`

- [ ] **Step 1: Write failing test for remote file deletion on success and error in `test_tasks.py`**
Add `test_remote_file_cleanup_after_analysis` in `podcasts/tests/test_tasks.py` asserting that `mock_client.files.delete(name=...)` is invoked for uploaded files.

```python
@mock.patch("podcasts.tasks.genai.Client")
def test_remote_file_cleanup_after_analysis(self, mock_client_cls):
    mock_client = mock.Mock()
    mock_client_cls.return_value = mock_client
    fake_file = mock.Mock()
    fake_file.name = "files/test-remote-audio-123"
    mock_client.files.upload.return_value = fake_file
    mock_client.models.generate_content.return_value = mock.Mock(text='[{"start": 0, "end": 10, "label": "intro"}]')

    with mock.patch("podcasts.tasks.settings") as mock_settings:
        mock_settings.GEMINI_API_KEYS = ["key-cleanup"]
        mock_settings.GEMINI_MODEL = "gemini-3.8-flash"
        mock_settings.FALLBACK_GEMINI_MODEL = "gemini-3.5-flash-lite"
        res = self.ad_manager._get_ad_segments_from_gemini("dummy.mp3", 30.0)

    self.assertEqual(res, '[{"start": 0, "end": 10, "label": "intro"}]')
    mock_client.files.delete.assert_called_once_with(name="files/test-remote-audio-123")
```

- [ ] **Step 2: Run test to verify it fails**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks.EpisodeProcessorTest.test_remote_file_cleanup_after_analysis`
Expected: FAIL (AttributeError or Client not called yet)

- [ ] **Step 3: Implement client initialization, file upload, model call, and cleanup in `_get_ad_segments_from_gemini`**
In `podcasts/tasks.py`:
- Replace `genai.configure(api_key=api_key)` with client dictionary `clients[api_key] = genai.Client(api_key=api_key)`.
- Use `client.files.upload(file=audio_path)` and cache in `uploaded_files[api_key]`.
- Call `client.models.generate_content(model=model_name, contents=[prompt, uploaded_files[api_key]], config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=list[AdSegmentSchema]))`.
- Wrap the entire Phase 1-4 execution in `try ... finally` calling `_cleanup_remote_files()` to execute `client.files.delete(name=file_name)` safely.

- [ ] **Step 4: Run test to verify it passes**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks.EpisodeProcessorTest.test_remote_file_cleanup_after_analysis`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add podcasts/tasks.py podcasts/tests/test_tasks.py
git commit -m "feat(tasks): migrate _get_ad_segments_from_gemini to google-genai Client and add remote file cleanup"
```

---

### Task 4: Update Unit Tests in `podcasts/tests/test_tasks.py`

**Files:**
- Modify: `podcasts/tests/test_tasks.py`
- Test: `podcasts/tests/test_tasks.py`

**Interfaces:**
- Consumes: Updated `AdManager` using `google-genai`
- Produces: Updated test suite in `test_tasks.py` mocking `genai.Client`

- [ ] **Step 1: Identify all deprecated mock patches in `test_tasks.py`**
Lines 78-96, lines 726-756, lines 763-795 still mock `podcasts.tasks.genai.upload_file` and `GenerativeModel`.

- [ ] **Step 2: Run existing tests to verify failures/warnings**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks.EpisodeProcessorTest`

- [ ] **Step 3: Update `test_tasks.py` to mock `genai.Client`**
- In `test_get_ad_segments_from_gemini`: patch `podcasts.tasks.genai.Client`, configure `mock_client.files.upload` and `mock_client.models.generate_content`, assert `upload(file="dummy_path.mp3")` and `delete(name=...)`.
- In `test_gemini_telemetry_retry_and_recovery`: patch `podcasts.tasks.genai.Client`, simulate 429 on first generate_content call and success on retry, verify telemetry.
- In `test_gemini_telemetry_fallback_model_used`: patch `podcasts.tasks.genai.Client`, simulate primary model failure and fallback success, verify telemetry.

- [ ] **Step 4: Run test to verify all tests in `test_tasks.py` pass**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`
Expected: PASS with 0 errors

- [ ] **Step 5: Commit**
```bash
git add podcasts/tests/test_tasks.py
git commit -m "test(tasks): update test_tasks mock objects for google-genai Client"
```

---

### Task 5: Update Unit Tests in `podcasts/tests/test_fallback.py`

**Files:**
- Modify: `podcasts/tests/test_fallback.py`
- Test: `podcasts/tests/test_fallback.py`

**Interfaces:**
- Consumes: Updated `AdManager` using `google-genai`
- Produces: Updated test suite in `test_fallback.py` mocking `genai.Client`

- [ ] **Step 1: Check existing test failures in `test_fallback.py`**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_fallback`
Expected: Failures on tests that mock `genai.configure`, `genai.GenerativeModel`, and `genai.upload_file`.

- [ ] **Step 2: Update all tests in `GeminiFallbackTest` to patch `genai.Client`**
Update:
- `test_single_key_success_primary_model`
- `test_key1_fails_429_key2_succeeds_primary_model`
- `test_per_key_upload_isolation_and_caching`
- `test_all_keys_fail_429_sleeps_and_retries_primary`
- `test_primary_fails_after_retry_falls_back_to_fallback_model`
- `test_all_keys_and_models_fail_returns_empty_list`
- `test_retry_delay_capped_at_max`
- `test_no_api_keys_configured`
- `test_fallback_to_legacy_gemini_api_key_when_keys_empty`
- `test_generation_config_response_mime_type`
- `test_non_429_error_does_not_sleep_before_fallback`
Update mocks to `@mock.patch("podcasts.tasks.genai.Client")` and assert client calls (`files.upload(file=...)`, `models.generate_content(...)`, `files.delete(name=...)`).

- [ ] **Step 3: Run `test_fallback.py` to verify all tests pass**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_fallback`
Expected: All tests PASS.

- [ ] **Step 4: Commit**
```bash
git add podcasts/tests/test_fallback.py
git commit -m "test(fallback): update test_fallback to mock google-genai Client"
```

---

### Task 6: Documentation, Linting, and Full Test Suite Verification

**Files:**
- Modify: `GEMINI.md`
- Verify: Entire test suite & pylint

**Interfaces:**
- Consumes: Clean implementation & test suite
- Produces: Passing full test run, zero pylint regressions, updated project docs

- [ ] **Step 1: Update `GEMINI.md` documentation**
Replace `google-generativeai` with `google-genai` in `GEMINI.md`.

- [ ] **Step 2: Run full test suite in local virtual environment**
Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All 134+ tests PASS with 0 failures, 0 errors, and NO deprecation warnings from `google.generativeai`.

- [ ] **Step 3: Run pylint on `podcasts/tasks.py` and test modules**
Run: `REHOST_BASE_URL=http://localhost:8000 DJANGO_SETTINGS_MODULE=podslice.settings /home/prmohan/projects/podslice/venv/bin/pylint podcasts/tasks.py podcasts/tests/test_tasks.py podcasts/tests/test_fallback.py`
Expected: High quality score (>= baseline 9.63/10) with no syntax/import errors.

- [ ] **Step 4: Commit**
```bash
git add GEMINI.md
git commit -m "docs: update GEMINI.md to reference google-genai"
```
