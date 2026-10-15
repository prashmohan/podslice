# Dashboard Analytics & 2x2 Metrics Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Podslice main dashboard (`podcasts/templates/podcasts/subscribe.html`) into a responsive, harmonized 2x2 metrics grid showcasing listener time saved, processing health, Gemini API key usage distribution, and system queue status, driven by aggregated telemetry in `MetricsService`.

**Architecture:** Extend `MetricsService.get_system_metrics()` in `podcasts/services.py` to aggregate listener time saved (ad load %, segment category breakdown), API key rotation distribution (call share %, 429 rate limit counts, audio hours analyzed), and system rehosting totals from existing `Episode.processing_metrics` and `Episode.ad_segments`. Update `subscribe.html` to replace disparate legacy cards with four harmonized Bootstrap 5 cards arranged in a responsive 2-column grid (`row-cols-1 row-cols-md-2`).

**Tech Stack:** Django 5.2, Django REST Framework, Bootstrap 5, Python 3.14 (Host venv).

**Spec:** [`docs/superpowers/specs/2026-10-15-dashboard-analytics-grid-design.md`](file:///home/prmohan/projects/podslice/docs/superpowers/specs/2026-10-15-dashboard-analytics-grid-design.md)

## Global Constraints

- **STRICTLY NO DOCKER**: Do not run `docker` or `docker-compose` commands under any circumstances. All tests and commands run locally on host venv: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`.
- **Zero Schema Changes**: Pure aggregation over existing database fields (`Episode.processing_metrics`, `Episode.ad_segments`, `Episode.duration_seconds`, `Episode.status`).
- **Defensive Parsing & Zero-Division Safety**: Every ratio, percentage, and dictionary access must guard against zero denominators and missing/empty/None telemetry fields.
- **Dynamic Polling Continuity**: Retain DOM IDs `completed-count` and `pending-count` so background polling script `fetchEpisodeStatus()` continues functioning seamlessly.

## Review Focus

1. **JSON String & Label Parsing in `ad_segments`**: `Episode.ad_segments` stored as either raw JSON string or parsed list of dicts, with various labels (`sponsor`, `ad`, `intro`, `outro`, `cta`, `promo`) mapped accurately into `ads`, `intros_outros`, and `ctas`.
2. **Zero-Division Handling**: Zero total episodes, zero duration, or zero attempts must yield clean defaults (`0.0%`, `0m`, `"0h"`) without raising `ZeroDivisionError`.
3. **Missing or None Fields in Telemetry**: Handling legacy or failed episodes where `stages["slicing"]`, `stages["gemini"]`, or `attempts` is absent or malformed.
4. **Key Masking Consistency**: Grouping Gemini attempts by masked key even if attempts have missing keys or diverse string formats.
5. **Human Duration Formatting**: Clean formatting for both short (e.g. `25m 10s` / `45s`) and long durations (e.g. `4h 18m` / `32.4h`).

---

### Task 1: Extend MetricsService with Time Saved & API Key Distribution Aggregations

**Files:**
- Modify: `podcasts/services.py:8-169`
- Test: `podcasts/tests/test_metrics.py`

**Interfaces:**
- Consumes: `Episode.objects.exclude(processing_metrics=None)`, `Episode.ad_segments`, `Episode.duration_seconds`, `Episode.status`, `Episode.processing_metrics`.
- Produces: `MetricsService.get_system_metrics() -> Dict[str, Any]` returning:
  - Existing keys: `total_episodes_processed`, `completed_episodes`, `unresolved_failures`, `clean_runs_count`, `clean_runs_pct`, `rate_limits_encountered`, `retries_recovered`, `retry_recovery_rate_pct`, `fallback_model_used`, `fallback_model_pct`, `stage_failures`.
  - New Time Saved keys:
    - `total_time_saved_sec`: float
    - `total_time_saved_formatted`: str (e.g. `"4h 18m"` or `"25m 10s"` or `"0m"`)
    - `total_segments_removed`: int
    - `ad_load_pct`: float (e.g. `14.2`)
    - `segment_types`: `Dict[str, int]` (`{"ads": int, "intros_outros": int, "ctas": int}`)
    - `segment_percentages`: `Dict[str, float]` (`{"ads": float, "intros_outros": float, "ctas": float}`)
  - New API Key Distribution keys:
    - `api_keys`: `List[Dict[str, Any]]` sorted by total calls descending, each with:
      - `key_masked`: str
      - `total_calls`: int
      - `call_pct`: float
      - `rate_limits`: int
      - `success_count`: int
    - `active_keys_count`: int
    - `total_audio_analyzed_sec`: float
    - `total_audio_analyzed_formatted`: str (e.g. `"32.4h"` or `"0h"`)
    - `total_audio_rehosted_sec`: float
    - `total_audio_rehosted_formatted`: str (e.g. `"28.1h"` or `"0h"`)

- [ ] **Step 1: Write the failing tests in `podcasts/tests/test_metrics.py`**

Add tests:
- `test_get_system_metrics_empty_database_includes_new_keys`: verifies empty DB returns zeroed time saved and empty api_keys with safe defaults.
- `test_get_system_metrics_time_saved_and_segments_breakdown`: verifies time saved, ad load %, and segment category grouping (sponsor/ad, intro/outro, cta/promo) across completed episodes.
- `test_get_system_metrics_handles_json_string_ad_segments`: verifies parsing when `ad_segments` is a raw JSON string.
- `test_get_system_metrics_api_key_distribution_and_429s`: verifies multi-key calls count, usage share percentages, rate limit counts per key, and audio hours analyzed.
- `test_format_duration_helpers`: verifies duration formatting helper behavior (`format_duration_hm` and `format_hours_decimal`).

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: FAIL due to missing keys `total_time_saved_formatted`, `ad_load_pct`, `api_keys`, etc.

- [ ] **Step 3: Implement metric calculations and duration helpers in `podcasts/services.py`**

1. Helper functions:
   - `format_duration_hm(seconds: float) -> str`: Formats seconds into `"Xh Ym"` (if >= 3600), `"Xm Ys"` (if 60 <= seconds < 3600), or `"Xs"` / `"0m"` (if < 60).
   - `format_hours_decimal(seconds: float) -> str`: Formats seconds into `f"{round(seconds / 3600.0, 1)}h"` (if >= 3600), `f"{round(seconds / 60.0, 1)}m"` (if 60 <= seconds < 3600 and > 0), or `"0h"`.
2. Segment label normalizer:
   - Categorize segment labels into `ads` (`sponsor`, `ad`, `advertisement`, or empty/unknown), `intros_outros` (`intro`, `outro`, `music`, `theme`), `ctas` (`cta`, `call to action`, `promo`, `promotion`, `off-topic`, `other`).
3. Aggregations in `MetricsService.get_system_metrics()`:
   - Iterate over episodes, extract `slicing.removed_duration_sec` and `ad_segments`.
   - Calculate `total_time_saved_sec`, `total_segments_removed`, and segment category counts.
   - Guard `ad_load_pct`: `(total_time_saved_sec / (total_content_sec + total_time_saved_sec)) * 100` if denominator > 0 else `0.0`.
   - Parse `gemini.attempts`: tally per-key calls, status 429s, and successes. Compute `call_pct`.
   - Sum `total_audio_analyzed_sec` and `total_audio_rehosted_sec`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: 147+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add podcasts/services.py podcasts/tests/test_metrics.py
git commit -m "feat(metrics): add time saved, segment breakdown, and API key quota telemetry aggregation"
```

---

### Task 2: Implement Harmonized 2x2 Grid UI in `subscribe.html` & View Integration

**Files:**
- Modify: `podcasts/templates/podcasts/subscribe.html:35-127`
- Test: `podcasts/tests/test_views.py`

**Interfaces:**
- Consumes: `metrics` dictionary from `PodcastSubscribeUIView._get_context()` populated by `MetricsService.get_system_metrics()`.
- Produces: Responsive 2x2 Bootstrap grid with 4 unified cards:
  - Card 1: ⏱️ Listener Time Saved (Value, Ad Load % badge, segmented progress bar for ads/intros/ctas, legend)
  - Card 2: ⚡ Processing & AI Health (Processed badge, 429 recovery, clean runs, fallback usage, unresolved failures, stage error breakdown)
  - Card 3: 🔑 API Keys & Quota Balance (Active keys badge, per-key progress bars with call share and 429 counts, total audio analyzed)
  - Card 4: 📊 System & Queue Status (Operational badge, 3 stat pillars with `id="completed-count"`, `id="pending-count"`, and total rehosted audio, last polled footer)

- [ ] **Step 1: Write view tests in `podcasts/tests/test_views.py`**

Add tests:
- `test_subscribe_view_renders_2x2_metrics_grid`: checks that `PodcastSubscribeUIView` returns HTTP 200 and renders the four card headers ("Listener Time Saved", "Processing & AI Health", "API Keys & Quota Balance", "System & Queue Status") along with `id="completed-count"` and `id="pending-count"`.
- `test_subscribe_view_renders_key_distribution_and_segment_bars`: creates test episode with processing metrics and ad segments, verifies badges and percentage bars appear in rendered HTML.

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views`
Expected: FAIL (missing 2x2 grid elements).

- [ ] **Step 3: Update `podcasts/templates/podcasts/subscribe.html`**

Replace lines 35-127 with the responsive 2x2 grid structure:
```html
<div class="row row-cols-1 row-cols-md-2 g-3 mb-4">
    <!-- Card 1: Listener Time Saved -->
    <div class="col">
        <div class="card h-100 shadow-sm">
            <div class="card-header bg-light d-flex justify-content-between align-items-center">
                <h5 class="card-title mb-0 fs-6 fw-bold">⏱️ Listener Time Saved</h5>
                <span class="badge bg-success">{{ metrics.ad_load_pct|default:"0.0" }}% Ad Load</span>
            </div>
            ...
        </div>
    </div>
    <!-- Card 2: Processing & AI Health -->
    <div class="col">
        ...
    </div>
    <!-- Card 3: API Keys & Quota Balance -->
    <div class="col">
        ...
    </div>
    <!-- Card 4: System & Queue Status -->
    <div class="col">
        ...
    </div>
</div>
```
Ensure `id="completed-count"` and `id="pending-count"` are preserved inside Card 4.

- [ ] **Step 4: Run view tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views`
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add podcasts/templates/podcasts/subscribe.html podcasts/tests/test_views.py
git commit -m "feat(ui): implement harmonized 2x2 analytics and quota grid on dashboard"
```

---

### Task 3: Linting, End-to-End Regression & Push

**Files:**
- Inspect: `podcasts/services.py`, `podcasts/views.py`, `podcasts/tests/test_metrics.py`, `podcasts/tests/test_views.py`

- [ ] **Step 1: Run pylint on modified files**

Run: `/home/prmohan/projects/podslice/venv/bin/pylint podcasts/services.py podcasts/views.py podcasts/tests/test_metrics.py podcasts/tests/test_views.py`
Expected: Code rated 10.00/10 (or no blocking errors).

- [ ] **Step 2: Run full regression test suite**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests pass.

- [ ] **Step 3: Push commits upstream to `origin/main`**

Run: `git push origin main`
Expected: Fast-forward push succeeds.
