# Design Document: Processing Metrics & UI Health Dashboard

## Background & Motivation
In Podslice, episodes pass through a multi-stage background pipeline:
1. Audio file download from external RSS feeds.
2. AI-powered advertisement detection via Google Gemini (with multi-API-key rotation, 429 backoff, and fallback to `gemini-3.5-flash-lite`).
3. Audio slicing and concatenation via FFmpeg.

While Podslice now handles rate limits and model fallback reliably, there is currently no visibility into pipeline health. Users and administrators cannot see:
- How often HTTP 429 quota errors are encountered and whether retry/rotation attempts succeed.
- How frequently the fallback model (`gemini-3.5-flash-lite`) is invoked versus clean primary runs on `gemini-3.8-flash`.
- Which stages fail (download vs. AI analysis vs. FFmpeg slicing).
- Which specific attempts, keys, and models were used for a given episode.

This feature introduces structured telemetry collection on `Episode`, a lightweight aggregation service (`MetricsService`), and an intuitive UI dashboard on the System Status home page (`subscribe.html`) along with per-episode badges on the podcast view (`status.html`).

---

## Scope & Impact
- **Database Schema:** Adds `processing_metrics = models.JSONField(null=True, blank=True)` to `podcasts.models.Episode`.
- **Pipeline Telemetry:** Modifies `EpisodeProcessor` and `AdManager` in `podcasts/tasks.py` to record durations, attempt histories, 429 retry outcomes, and error details into `episode.processing_metrics`.
- **Metrics Service:** Adds `podcasts/services.py` with `MetricsService.get_system_metrics()` to compute aggregate statistics across episodes.
- **Views & UI:** Updates `PodcastSubscribeUIView` to render an "AI & Processing Health" dashboard card in `podcasts/templates/podcasts/subscribe.html`, and updates `PodcastStatusUIView` and `podcasts/templates/podcasts/status.html` to display episode-level telemetry badges.
- **Testing:** Adds unit and integration test suites in `podcasts/tests/test_metrics.py`, `podcasts/tests/test_tasks.py`, and `podcasts/tests/test_views.py`. All tests executed strictly in the host local virtualenv.

---

## Detailed Architecture & Design

### 1. Data Schema & Telemetry Structure

#### `Episode.processing_metrics` (`podcasts/models.py`)
```python
class Episode(models.Model):
    ...
    processing_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Telemetry and execution metrics recorded during episode processing."
    )
```

#### JSON Telemetry Payload Format
```json
{
  "status": "COMPLETE",
  "total_duration_sec": 12.4,
  "stages": {
    "download": {
      "status": "success",
      "error": null,
      "duration_sec": 2.1
    },
    "gemini": {
      "status": "success",
      "model_used": "gemini-3.8-flash",
      "attempts_count": 2,
      "retry_attempts": 1,
      "retry_recovered": true,
      "fallback_used": false,
      "attempts": [
        {
          "model": "gemini-3.8-flash",
          "key_masked": "AIzaSy...",
          "status": 429,
          "error": "ResourceExhausted",
          "retry_delay": 15.0
        },
        {
          "model": "gemini-3.8-flash",
          "key_masked": "AIzaSy...",
          "status": "success"
        }
      ],
      "error": null,
      "duration_sec": 7.8
    },
    "slicing": {
      "status": "success",
      "ads_detected": 2,
      "removed_duration_sec": 65.5,
      "error": null,
      "duration_sec": 2.5
    }
  },
  "unresolved_error": null
}
```

When an episode is reprocessed or reset, `episode.processing_metrics` is set to `None`, keeping the lifecycle strictly tied to the latest processing execution.

---

### 2. Pipeline Instrumentation (`podcasts/tasks.py`)

#### `EpisodeProcessor.rehost_audio()`
Tracks wall-clock start time and initializes the telemetry structure:
1. **Download Stage (`_download_audio`)**:
   - Records start and end time.
   - On error: logs error message and sets `stages.download.status = "failed"`.
2. **Gemini AI Stage (`AdManager.analyze_audio` / `_get_ad_segments_from_gemini`)**:
   - `_get_ad_segments_from_gemini` populates an `audit_trail` dictionary:
     - `attempts`: list of `{model, key_masked, status, error, retry_delay}`.
     - `retry_attempts`: count of 429 retry sleep/retry passes.
     - `retry_recovered`: boolean, True if 429 occurred but subsequent call succeeded.
     - `fallback_used`: boolean, True if fallback model was invoked.
     - `model_used`: string model identifier that successfully returned segments (or None).
   - If `disable_ai_processing=True`, sets `stages.gemini.status = "skipped"`.
3. **Audio Slicing Stage (`_slice_and_save_audio`)**:
   - Records start and end time, number of ad segments detected, and total cut duration.
   - If no ad segments detected, records `status = "original_preserved"`.
   - On error: sets `stages.slicing.status = "failed"`.
4. **Error & Completion Handling**:
   - In `except Exception as e`: records `unresolved_error = str(e)`, persists `self.episode.processing_metrics`, and saves episode status as `FAILED`.
   - In normal completion: persists `self.episode.processing_metrics` and saves episode status as `COMPLETE`.

---

### 3. Metrics Service (`podcasts/services.py`)

A clean domain service `MetricsService.get_system_metrics()` provides aggregate statistics:
```python
class MetricsService:
    @staticmethod
    def get_system_metrics() -> Dict[str, Any]:
        """
        Aggregates processing metrics across all episodes having telemetry.
        """
        ...
```

#### Calculated Summary Fields:
- `total_episodes_processed`: Total episodes with `processing_metrics` populated.
- `completed_episodes`: Episodes with `status == "COMPLETE"`.
- `unresolved_failures`: Episodes with `status == "FAILED"`.
- `clean_runs_count`: Episodes completed without any retries or errors.
- `clean_runs_pct`: Clean runs divided by total processed (formatted as percentage float).
- `rate_limits_encountered`: Episodes that encountered at least one HTTP 429.
- `retries_recovered`: Episodes that encountered a 429 and subsequently recovered.
- `retry_recovery_rate_pct`: `(retries_recovered / rate_limits_encountered * 100)` or `100.0` if 0 rate limits.
- `fallback_model_used`: Episodes where `fallback_used == True`.
- `fallback_model_pct`: Fallback episodes divided by total AI-processed episodes.
- `stage_failures`:
  - `download`: Failures occurring during audio download.
  - `gemini`: Unrecoverable errors during Gemini analysis.
  - `slicing`: Failures occurring during FFmpeg slicing.

---

### 4. UI Dashboard & Presentation

#### Main Subscription Page (`podcasts/templates/podcasts/subscribe.html`)
Inside or adjacent to the existing "System Status" card, render an "AI & Processing Health" dashboard containing:
- **Rate Limit Resilience:** Displays `429 Recovery Rate` (e.g. `85% (17 of 20 recovered)`), with badge coloring (green for >=80%, yellow for 50-79%, red for <50%).
- **Fallback Invocations:** Displays how many episodes used `gemini-3.5-flash-lite` vs clean `gemini-3.8-flash`.
- **Pipeline Error Breakdown:** Displays counts for Download errors, Gemini errors, and Slicing errors.
- **Unresolved Failures:** Prominently highlights total permanent failures.

#### Podcast Detail Page (`podcasts/templates/podcasts/status.html`)
For each episode in the episode list:
- Display a badge for the model used (`gemini-3.8-flash` in secondary/blue, `gemini-3.5-flash-lite` in warning/amber).
- If recovered from 429: `<span class="badge bg-info text-dark">429 Recovered</span>`.
- If failed: `<span class="badge bg-danger">Failed at [Stage]</span>`.
- Processing duration: `<small class="text-muted">Processed in Xs</small>`.

---

## Verification & Testing Plan

### Automated Unit & Integration Tests
1. **`podcasts/tests/test_metrics.py`**:
   - Zero-state handling (empty DB, zero division safety).
   - Recovery rate calculation (100% when no 429s, accurate ratios when 429s occur).
   - Fallback model count and percentage calculation.
   - Stage error breakdown accuracy.
2. **`podcasts/tests/test_tasks.py`**:
   - Successful processing generates valid `processing_metrics` dictionary on `Episode`.
   - Download failure generates `stages.download.status = "failed"` and sets `unresolved_error`.
   - Gemini 429 retry populates `attempts`, `retry_attempts`, and `retry_recovered`.
   - Slicing failure generates `stages.slicing.status = "failed"`.
   - Reprocess action clears `processing_metrics`.
3. **`podcasts/tests/test_views.py`**:
   - `PodcastSubscribeUIView` supplies `metrics` in template context and renders health card.
   - `PodcastStatusUIView` renders episode telemetry badges.

### Test Execution Environment
All tests executed strictly in local virtualenv on host:
```bash
/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts
```
Zero Docker interaction.
