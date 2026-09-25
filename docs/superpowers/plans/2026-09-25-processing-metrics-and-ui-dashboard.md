# Processing Metrics & UI Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect structured processing telemetry across the audio download, Gemini AI ad detection, and audio slicing pipeline, and display system health metrics (429 retry recovery rate, fallback usage, error breakdowns) on the UI.

**Architecture:** Add `processing_metrics` JSONField on `Episode` populated by `EpisodeProcessor` in `podcasts/tasks.py`. Aggregate pipeline statistics on-demand via `MetricsService.get_system_metrics()` in `podcasts/services.py`. Render aggregate health cards on the System Status home page (`subscribe.html`) and per-episode telemetry badges on the podcast view (`status.html`).

**Tech Stack:** Django 4.2+, SQLite, Python 3.12, Bootstrap 5.

**Spec:** `docs/superpowers/specs/2026-09-25-processing-metrics-and-ui-dashboard-design.md`

## Global Constraints
- All tests and database operations must be executed strictly locally in the host virtual environment via `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`.
- Zero Docker commands (`docker`, `docker-compose`) must be executed.
- Reprocessing an episode must reset its `processing_metrics` to `None`.
- Telemetry must be persisted even when processing terminates with an unexpected fatal error in `rehost_audio`.
- API keys in telemetry must always be masked (`key[:6] + "..."`).

## Review Focus
1. Empty database or zero-processed episodes must not cause zero-division errors in `MetricsService` (e.g. `0 / 0`).
2. Legacy episodes with `processing_metrics = None` must not cause `TypeError` or `KeyError` during dashboard rendering or podcast status viewing.
3. Boolean values or missing keys inside `processing_metrics` sub-dictionaries must be handled defensively without unhandled exceptions.
4. Reprocessing an episode from any view (`EpisodeReprocessView`, `PodcastReprocessView`, `EpisodeToggleAIView`) must clear old metrics to prevent stale telemetry.
5. Telemetry recording in `tasks.py` must never itself raise an uncaught exception that aborts episode processing.

---

### Task 1: Model Schema & Database Migration

**Files:**
- Modify: `podcasts/models.py:90-115`
- Create: Migration file `podcasts/migrations/0014_episode_processing_metrics.py` (via makemigrations)
- Test: `podcasts/tests/test_models.py`

**Interfaces:**
- Produces: `Episode.processing_metrics: models.JSONField(null=True, blank=True)`

- [ ] **Step 1: Write unit test for `processing_metrics` field**

In `podcasts/tests/test_models.py`:
```python
from django.test import TestCase
from podcasts.models import Podcast, Episode

class EpisodeProcessingMetricsModelTest(TestCase):
    def test_processing_metrics_field_persists_json(self):
        podcast = Podcast.objects.create(title="P", rss_url="http://example.com/rss")
        payload = {
            "status": "COMPLETE",
            "total_duration_sec": 10.5,
            "stages": {
                "download": {"status": "success"},
                "gemini": {"status": "success", "model_used": "gemini-3.8-flash"},
                "slicing": {"status": "success"}
            }
        }
        episode = Episode.objects.create(
            podcast=podcast,
            title="E",
            guid="g-test",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://example.com/a.mp3",
            processing_metrics=payload
        )
        episode.refresh_from_db()
        self.assertEqual(episode.processing_metrics["status"], "COMPLETE")
        self.assertEqual(episode.processing_metrics["stages"]["gemini"]["model_used"], "gemini-3.8-flash")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_models`
Expected: FAIL with `FieldError` or unknown keyword argument `processing_metrics`.

- [ ] **Step 3: Add `processing_metrics` field and generate migration**

In `podcasts/models.py`:
```python
    processing_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Telemetry and execution metrics recorded during episode processing.",
    )
```

Run migration creation and apply locally:
```bash
/home/prmohan/projects/podslice/venv/bin/python manage.py makemigrations podcasts
/home/prmohan/projects/podslice/venv/bin/python manage.py migrate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_models`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add podcasts/models.py podcasts/migrations/ podcasts/tests/test_models.py
git commit -m "feat(models): add processing_metrics JSONField to Episode model"
```

---

### Task 2: Pipeline Telemetry Instrumentation in Tasks

**Files:**
- Modify: `podcasts/tasks.py:80-220`, `podcasts/tasks.py:640-750`
- Modify: `podcasts/views.py:40-95`
- Test: `podcasts/tests/test_tasks.py`

**Interfaces:**
- Modifies: `EpisodeProcessor.rehost_audio()` to record telemetry in `self.episode.processing_metrics`
- Modifies: `AdManager._get_ad_segments_from_gemini()` to return or capture `gemini_telemetry: Dict[str, Any]`
- Modifies: `EpisodeReprocessView`, `PodcastReprocessView`, `EpisodeToggleAIView` to set `episode.processing_metrics = None`

- [ ] **Step 1: Write failing tests for telemetry collection in `test_tasks.py`**

In `podcasts/tests/test_tasks.py`:
```python
    @mock.patch("podcasts.tasks.EpisodeProcessor._slice_and_save_audio")
    @mock.patch("podcasts.tasks.AdManager.analyze_audio", return_value=[{"start": 10.0, "end": 20.0}])
    @mock.patch("podcasts.tasks.EpisodeProcessor._get_audio_duration", return_value=60.0)
    @mock.patch("podcasts.tasks.EpisodeProcessor._download_audio")
    def test_rehost_audio_records_processing_metrics(
        self, mock_download, mock_duration, mock_analyze, mock_slice
    ):
        self.processor.rehost_audio()
        self.episode.refresh_from_db()
        self.assertIsNotNone(self.episode.processing_metrics)
        self.assertEqual(self.episode.processing_metrics["status"], "COMPLETE")
        self.assertIn("stages", self.episode.processing_metrics)
        self.assertEqual(self.episode.processing_metrics["stages"]["download"]["status"], "success")
        self.assertEqual(self.episode.processing_metrics["stages"]["gemini"]["status"], "success")
        self.assertEqual(self.episode.processing_metrics["stages"]["slicing"]["status"], "success")

    @mock.patch("podcasts.tasks.EpisodeProcessor._download_audio", side_effect=Exception("Network failure"))
    def test_rehost_audio_records_error_metrics_on_failure(self, mock_download):
        self.processor.rehost_audio()
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, Episode.Status.FAILED)
        self.assertIsNotNone(self.episode.processing_metrics)
        self.assertEqual(self.episode.processing_metrics["status"], "FAILED")
        self.assertEqual(self.episode.processing_metrics["stages"]["download"]["status"], "failed")
        self.assertIn("Network failure", self.episode.processing_metrics["unresolved_error"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks.EpisodeProcessorTestCase.test_rehost_audio_records_processing_metrics`
Expected: FAIL (assertion error that `processing_metrics` is None).

- [ ] **Step 3: Implement telemetry capture in `tasks.py` and reset in `views.py`**

In `podcasts/tasks.py`:
- In `AdManager`:
  - Inside `_get_ad_segments_from_gemini`, maintain `gemini_telemetry = {"attempts": [], "retry_attempts": 0, "retry_recovered": False, "fallback_used": False, "model_used": None, "error": None}`.
  - Expose `self.last_telemetry = gemini_telemetry`.
  - In `_call_model_with_key`, log attempt and record status/masked key.
  - On 429 and retry, record retry attempts and if recovered set `retry_recovered = True`.
  - On fallback model call, set `fallback_used = True`.
  - On success, set `model_used = model_name`.
- In `EpisodeProcessor.rehost_audio`:
  - Track `start_time = time.time()`.
  - Wrap stages with timing and stage status dictionary.
  - If `disable_ai_processing=True`, set `stages["gemini"] = {"status": "skipped", "error": None, "duration_sec": 0.0}`.
  - In `except Exception as e`:
    - Record `unresolved_error = str(e)`, `status = "FAILED"`.
    - Save `self.episode.processing_metrics`.
    - Save episode.
  - On success:
    - Set `status = "COMPLETE"`.
    - Save `self.episode.processing_metrics`.
- In `podcasts/views.py`:
  - In `EpisodeReprocessView`, `PodcastReprocessView`, and `EpisodeToggleAIView`, ensure `episode.processing_metrics = None` when resetting episodes to `NEW`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add podcasts/tasks.py podcasts/views.py podcasts/tests/test_tasks.py
git commit -m "feat(tasks): instrument pipeline stages with structured telemetry and error capture"
```

---

### Task 3: Domain Metrics Aggregation Service

**Files:**
- Create: `podcasts/services.py`
- Test: `podcasts/tests/test_metrics.py`

**Interfaces:**
- Produces: `MetricsService.get_system_metrics() -> Dict[str, Any]`

- [ ] **Step 1: Write failing unit tests for `MetricsService`**

Create `podcasts/tests/test_metrics.py`:
```python
from django.test import TestCase
from podcasts.models import Podcast, Episode
from podcasts.services import MetricsService

class MetricsServiceTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="P", rss_url="http://e.com/rss")

    def test_get_system_metrics_empty_database(self):
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 0)
        self.assertEqual(metrics["retry_recovery_rate_pct"], 100.0)
        self.assertEqual(metrics["fallback_model_pct"], 0.0)
        self.assertEqual(metrics["clean_runs_pct"], 100.0)
        self.assertEqual(metrics["unresolved_failures"], 0)

    def test_get_system_metrics_with_telemetry_data(self):
        # Episode 1: Clean run on primary model
        Episode.objects.create(
            podcast=self.podcast, title="E1", guid="g1", pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/1.mp3", status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success", "retry_attempts": 0, "fallback_used": False},
                    "slicing": {"status": "success"}
                }
            }
        )
        # Episode 2: 429 Recovered
        Episode.objects.create(
            podcast=self.podcast, title="E2", guid="g2", pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/2.mp3", status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success", "retry_attempts": 1, "retry_recovered": True, "fallback_used": False},
                    "slicing": {"status": "success"}
                }
            }
        )
        # Episode 3: Fallback used
        Episode.objects.create(
            podcast=self.podcast, title="E3", guid="g3", pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/3.mp3", status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "fallback_success", "retry_attempts": 1, "retry_recovered": False, "fallback_used": True},
                    "slicing": {"status": "success"}
                }
            }
        )
        # Episode 4: Failed at download
        Episode.objects.create(
            podcast=self.podcast, title="E4", guid="g4", pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/4.mp3", status=Episode.Status.FAILED,
            processing_metrics={
                "status": "FAILED",
                "stages": {
                    "download": {"status": "failed", "error": "HTTP 404"},
                    "gemini": {"status": "skipped"},
                    "slicing": {"status": "skipped"}
                },
                "unresolved_error": "HTTP 404"
            }
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 4)
        self.assertEqual(metrics["completed_episodes"], 3)
        self.assertEqual(metrics["unresolved_failures"], 1)
        self.assertEqual(metrics["rate_limits_encountered"], 2)
        self.assertEqual(metrics["retries_recovered"], 1)
        self.assertEqual(metrics["retry_recovery_rate_pct"], 50.0)
        self.assertEqual(metrics["fallback_model_used"], 1)
        self.assertEqual(metrics["stage_failures"]["download"], 1)
        self.assertEqual(metrics["stage_failures"]["gemini"], 0)
        self.assertEqual(metrics["stage_failures"]["slicing"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: FAIL (ModuleNotFoundError: No module named 'podcasts.services').

- [ ] **Step 3: Implement `MetricsService` in `podcasts/services.py`**

Create `podcasts/services.py`:
- Query `Episode.objects.exclude(processing_metrics=None)`.
- Aggregate total processed, status counts, clean runs, 429 encounters, recovered counts, fallback model counts, and stage error counts.
- Guard against division by zero using `if total > 0` checks, defaulting percentages to `100.0` or `0.0` appropriately.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add podcasts/services.py podcasts/tests/test_metrics.py
git commit -m "feat(services): implement MetricsService for system pipeline aggregation"
```

---

### Task 4: UI Health Dashboard & Episode Telemetry Badges

**Files:**
- Modify: `podcasts/views.py:280-380`
- Modify: `podcasts/templates/podcasts/subscribe.html:35-65`
- Modify: `podcasts/templates/podcasts/status.html:60-120`
- Test: `podcasts/tests/test_views.py`

**Interfaces:**
- Consumes: `MetricsService.get_system_metrics()`
- Modifies: `PodcastSubscribeUIView` template context
- Modifies: `PodcastStatusUIView` episode rendering

- [ ] **Step 1: Write failing tests for UI views rendering metrics**

In `podcasts/tests/test_views.py`:
```python
    def test_subscribe_ui_renders_health_dashboard(self):
        response = self.client.get(reverse("podcast-subscribe-ui"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("metrics", response.context)
        self.assertContains(response, "Processing & AI Health")
        self.assertContains(response, "429 Retry Recovery")

    def test_podcast_status_ui_renders_episode_telemetry(self):
        self.episode.processing_metrics = {
            "status": "COMPLETE",
            "total_duration_sec": 7.5,
            "stages": {
                "gemini": {
                    "model_used": "gemini-3.8-flash",
                    "retry_recovered": True
                }
            }
        }
        self.episode.save()
        response = self.client.get(reverse("podcast-status-ui", args=[self.podcast.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gemini-3.8-flash")
        self.assertContains(response, "429 Recovered")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views.PodcastSubscribeUIViewTest podcasts.tests.test_views.PodcastStatusUIViewTest`
Expected: FAIL (elements not found in HTML response).

- [ ] **Step 3: Update views and templates**

In `podcasts/views.py`:
- In `PodcastSubscribeUIView.get()`: import `MetricsService` and pass `metrics = MetricsService.get_system_metrics()` in `context`.
- In `PodcastStatusUIView.get()`: unpack telemetry details on each episode (model used, retry badge, failure stage, duration).

In `podcasts/templates/podcasts/subscribe.html`:
- Under "System Status", render the "Processing & AI Health" card with:
  - 429 Recovery Rate tile
  - Fallback Model Usage tile
  - Clean Runs percentage
  - Stage Error Breakdown (Download, Gemini, Slicing)
  - Unresolved Failures count

In `podcasts/templates/podcasts/status.html`:
- In the episode list, display badges for model name, 429 recovery indicator, failed stage indicator, and execution time.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add podcasts/views.py podcasts/templates/podcasts/subscribe.html podcasts/templates/podcasts/status.html podcasts/tests/test_views.py
git commit -m "feat(ui): add Processing & AI Health dashboard and episode telemetry badges"
```

---

### Task 5: Full Regression Testing & Verification

**Files:**
- Test: All tests in `podcasts`

- [ ] **Step 1: Run full test suite locally**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests PASS with 0 failures, 0 errors.

- [ ] **Step 2: Verify git status and working tree**

Run: `git status`
Expected: Clean working tree.

- [ ] **Step 3: Final commit / push if required**
