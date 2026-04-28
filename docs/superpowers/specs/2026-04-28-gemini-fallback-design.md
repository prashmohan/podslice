# Design Document: Automatic Gemini Model Fallback

## Background & Motivation
The primary Gemini model (`GEMINI_MODEL`) sometimes fails due to quota limits or other transient errors. To ensure robust processing, the system should automatically fallback to a lighter-weight model (`FALLBACK_GEMINI_MODEL`) when the primary model fails.

## Scope & Impact
- Updates `podslice/settings.py` for model configuration.
- Modifies `podcasts/tasks.py` to implement the retry/fallback logic.
- Updates documentation (`.env.example`, `README.md`) and container configuration (`Dockerfile`).

## Proposed Solution

### 1. Configuration Updates
Modify `podslice/settings.py` to provide defaults for models and include the fallback model.

```python
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
FALLBACK_GEMINI_MODEL = os.environ.get("FALLBACK_GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
```

### 2. Task Logic Updates
Update `AdManager._get_ad_segments_from_gemini` in `podcasts/tasks.py` to handle the fallback.

```python
def _get_ad_segments_from_gemini(self, audio_path: str) -> str:
    """Sends audio to the Gemini API with automatic fallback."""
    models_to_try = [
        getattr(settings, "GEMINI_MODEL", "gemini-3-flash-preview"),
        getattr(settings, "FALLBACK_GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    ]
    
    genai.configure(api_key=settings.GEMINI_API_KEY)
    audio_file = None # Upload once if possible
    
    for model_name in models_to_try:
        try:
            logger.info("Attempting ad detection with model: %s", model_name)
            model = genai.GenerativeModel(model_name)
            if audio_file is None:
                audio_file = genai.upload_file(path=audio_path)
            
            response = model.generate_content([GEMINI_PROMPT, audio_file])
            return response.text
        except Exception as e:
            logger.warning("Gemini API call failed for model %s: %s", model_name, str(e))
            if model_name == models_to_try[-1]:
                logger.error("All Gemini models failed.")
    
    return "[]"
```

### 3. Documentation Updates
Update `.env.example` and `README.md` to reflect the new defaults and the fallback variable.

## Alternatives Considered
- **Exponential Backoff only**: While useful, it doesn't help if the quota for a specific model is completely exhausted for the window.
- **Random Model Selection**: Less deterministic and might use more expensive models when not necessary.

## Verification Plan
### Automated Tests
- Mock the Gemini API to fail on the first call and succeed on the second. Verify `FALLBACK_GEMINI_MODEL` is used.
- Mock both calls to fail and verify empty result.

### Manual Verification
- Temporarily set `GEMINI_MODEL` to a non-existent model name and verify it falls back to the flash model.
