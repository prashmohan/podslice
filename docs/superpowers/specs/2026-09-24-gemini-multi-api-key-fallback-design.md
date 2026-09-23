# Design Document: Gemini Multi-API-Key Fallback, Response MIME Type & Retry Handling

## Background & Motivation
In Podslice, podcast episodes are analyzed by Google Gemini to detect and slice advertisements. Currently:
1. `gemini-3.8-flash` frequently encounters HTTP 429 quota errors (5 RPM and 20 RPD on Google's free tier).
2. While multiple API keys could distribute quota across projects, Podslice currently supports only a single `GEMINI_API_KEY`.
3. When the primary model fails on a 429 rate limit, Podslice immediately falls back to `gemini-3.5-flash-lite` without attempting alternate API keys or honoring Google's retry delay.
4. The fallback model produces unquoted `MM:SS` timestamps and raw text preambles that cause JSON decoding and regex extraction failures, resulting in original audio being rehosted with advertisements intact.

This design introduces multi-API-key fallback on the primary model first, intelligent retry handling for 429 errors, strict JSON response MIME type configuration, and resilient timestamp parsing.

---

## Scope & Impact
- **Configuration:** Updates `podslice/settings.py`, `.env.example`, and `README.md` to support comma-separated `GEMINI_API_KEYS` with backward compatibility for `GEMINI_API_KEY`.
- **Task Logic:** Modifies `AdManager` in `podcasts/tasks.py` to manage per-key file uploads, fast key rotation, rate-limit backoff, structured output constraints, and robust JSON parsing.
- **Testing:** Updates `podcasts/tests/test_fallback.py` to verify multi-key fallback, retry delays, per-key file uploads, and JSON response sanitization.

---

## Detailed Design

### 1. Settings & Configuration Schema
In `podslice/settings.py`:
- Read `GEMINI_API_KEYS` as a comma-separated string from `os.environ`.
- Parse into a list of cleaned strings: `[k.strip() for k in raw_keys.split(",") if k.strip()]`.
- If `GEMINI_API_KEYS` is not provided or empty, fallback to `[os.environ.get("GEMINI_API_KEY").strip()]` if set, else `[]`.
- Retain `GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""` for legacy compatibility.
- Retain `GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")`.
- Retain `FALLBACK_GEMINI_MODEL = os.environ.get("FALLBACK_GEMINI_MODEL", "gemini-3.5-flash-lite")`.
- Add `GEMINI_MAX_RETRY_DELAY_SEC = int(os.environ.get("GEMINI_MAX_RETRY_DELAY_SEC", 30))`.

### 2. Multi-API-Key Execution & File Upload Lifecycle
In the Gemini File API, uploaded audio files are scoped to the API key used during upload. To ensure correctness:
- `AdManager._get_ad_segments_from_gemini` maintains a dictionary of uploaded files: `uploaded_files: Dict[str, Any] = {}`.
- Before invoking a model with a given `api_key`:
  ```python
  genai.configure(api_key=api_key)
  if api_key not in uploaded_files:
      uploaded_files[api_key] = genai.upload_file(path=audio_path)
  audio_file = uploaded_files[api_key]
  ```
- If an API key is used again (e.g. during a retry or when switching to the fallback model), the previously uploaded file handle for that key is reused without redundant network transfers.

### 3. Rate Limit & 429 Retry Strategy
The execution follows this priority order:
1. **Primary Model Pass (Keys 1..N):**
   - For each key in `GEMINI_API_KEYS`, attempt ad detection using `GEMINI_MODEL`.
   - If an HTTP 429 error occurs:
     - Extract `retry_delay` (via `e.retry_delay.seconds` or regex `r"retry in ([0-9.]+)s"` from the error message).
     - Record the delay.
     - Immediately attempt the next key without sleeping.
   - If any other error occurs (e.g. HTTP 5xx), log warning and immediately attempt the next key.
   - If successful, return `response.text`.
2. **Primary Model Retry (if all keys hit 429):**
   - If all keys failed on the primary model and at least one 429 was encountered:
     - Determine sleep duration: `sleep_sec = min(min(retry_delays), settings.GEMINI_MAX_RETRY_DELAY_SEC)`.
     - Log info and sleep for `sleep_sec`.
     - Re-attempt the primary model across the keys once more.
     - If successful, return `response.text`.
3. **Fallback Model Pass (Keys 1..N):**
   - If the primary model fails across all keys (and after any retry), switch to `FALLBACK_GEMINI_MODEL`.
   - Iterate through each key in `GEMINI_API_KEYS`.
   - If successful, return `response.text`.
4. **Exhaustion Handling:**
   - If all keys and all models fail:
     - Log error: `"All Gemini models and API keys failed."`
     - Return `"[]"`.
     - The calling pipeline maintains current behavior: gracefully rehosting the original audio and marking the episode `COMPLETE`.

### 4. Response MIME Type & Robust JSON Parsing
To prevent formatting anomalies from breaking downstream parsing:
1. **Model Configuration:**
   Instantiate `genai.GenerativeModel` with:
   ```python
   generation_config={"response_mime_type": "application/json"}
   ```
   This constrains the model to return valid JSON without conversational preambles or markdown code fences.
2. **Defensive Parsing in `_parse_ad_segments`:**
   - First attempt `json.loads(gemini_response.strip())`.
   - If that fails, extract content inside code fences: `re.search(r"```(?:json)?\s*(\[.*?\])\s*```", gemini_response, re.DOTALL)`.
   - If code fences are missing, locate the outermost array brackets: `re.search(r"(\[.*\])", gemini_response, re.DOTALL)`.
3. **Timestamp Sanitization:**
   - Normalize string timestamps: if `start` or `end` is formatted as `"MM:SS.sss"` or `"HH:MM:SS"`, parse it into total float seconds.
   - Validate numeric values: cast to `float`.
   - Sanity filter: discard segments where `start >= end` or `start >= audio_duration_seconds`.
   - If parsing fails completely, catch `json.JSONDecodeError` / `ValueError`, log warning, and return `[]`.

---

## Verification Plan

### Automated Unit Tests (`podcasts/tests/test_fallback.py`)
1. **Single Key Success:** Verify normal operation with 1 key on primary model.
2. **Key 1 Fails (429), Key 2 Succeeds:** Verify primary model succeeds on Key 2 without switching to fallback model.
3. **Per-Key Upload Isolation:** Verify `genai.upload_file` is called once per distinct API key when rotating keys.
4. **All Keys Fail (429) -> Retry Primary:** Verify that when all keys return 429, sleep is called and primary is retried once.
5. **All Keys Fail on Primary -> Fallback Model:** Verify that when primary completely fails across all keys, the fallback model is invoked with Key 1.
6. **All Keys & Models Fail:** Verify `"[]"` is returned.
7. **JSON Response MIME Type:** Verify `generation_config={"response_mime_type": "application/json"}` is passed to `GenerativeModel`.
8. **Robust Parsing & Timestamp Normalization:**
   - Plain JSON array `[{"start": 10.5, "end": 20.0}]`
   - Markdown code block ````json [...] ```` with preamble text
   - String `MM:SS.sss` timestamps `[{"start": "01:30.5", "end": "02:15.0"}]`

### Regression Testing
Run the complete Podslice test suite inside the container:
```bash
docker exec podslice-app-1 python manage.py test podcasts
```
Ensure all tests pass.
