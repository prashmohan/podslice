# Gemini Model Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automatic fallback to a lighter-weight Gemini model when the primary model fails.

**Architecture:** Update settings to include a fallback model variable and update the `AdManager` task logic to attempt a secondary call upon primary failure.

**Tech Stack:** Django, Python, Google Generative AI SDK.

---

### Task 1: Update Settings and Environment

**Files:**
- Modify: `podslice/settings.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `Dockerfile`

- [ ] **Step 1: Update `podslice/settings.py` with new defaults and fallback variable**
Modify `podslice/settings.py` around line 189:
```python
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
FALLBACK_GEMINI_MODEL = os.environ.get("FALLBACK_GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
```

- [ ] **Step 2: Update `.env.example`**
```text
GEMINI_MODEL=gemini-3-flash-preview
FALLBACK_GEMINI_MODEL=gemini-3.1-flash-lite-preview
```

- [ ] **Step 3: Update `README.md`**
Update the table of environment variables with new defaults and the fallback model.

- [ ] **Step 4: Update `Dockerfile`**
Update the `collectstatic` run command with the new dummy keys if necessary.

- [ ] **Step 5: Commit**
```bash
git add podslice/settings.py .env.example README.md Dockerfile
git commit -m "chore: update gemini model settings and documentation"
```

---

### Task 2: Implement Fallback Logic in Tasks

**Files:**
- Modify: `podcasts/tasks.py`

- [ ] **Step 1: Update `AdManager._get_ad_segments_from_gemini` to implement retry logic**
Modify `podcasts/tasks.py` (around line 78):
```python
    def _get_ad_segments_from_gemini(self, audio_path: str) -> str:
        """Sends audio to the Gemini API for ad detection with automatic fallback."""
        models_to_try = [
            getattr(settings, "GEMINI_MODEL", "gemini-3-flash-preview"),
            getattr(settings, "FALLBACK_GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
        ]
        
        logger.debug("Attempting ad detection with Gemini.")
        genai.configure(api_key=settings.GEMINI_API_KEY)
        audio_file = None

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

- [ ] **Step 2: Commit**
```bash
git add podcasts/tasks.py
git commit -m "feat: implement automatic Gemini model fallback"
```

---

### Task 3: Verification

**Files:**
- Create: `podcasts/tests/test_fallback.py`

- [ ] **Step 1: Write failing test for fallback**
Create a test where the first model call fails and verify the second model is called.

- [ ] **Step 2: Run tests**
Run: `python manage.py test podcasts.tests.test_fallback`

- [ ] **Step 3: Commit**
```bash
git add podcasts/tests/test_fallback.py
git commit -m "test: add gemini fallback verification tests"
```
