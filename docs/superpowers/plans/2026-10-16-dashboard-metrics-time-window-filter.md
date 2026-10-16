# Dashboard Metrics Time-Window Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a responsive time-window filter (`All Time`, `Last 1d`, `Last 1w`, `Last 1m`) at the top of the metrics section on the main Podslice dashboard (`podcasts/templates/podcasts/subscribe.html`), updating all 4 analytics cards dynamically while preserving URL parameters and auto-polling continuity.

**Architecture:** 
- In `MetricsService.get_system_metrics(time_window: Optional[str] = "all")`, compute cutoff timestamps (`1d` -> 24 hours ago, `1w` -> 7 days ago, `1m` -> 30 days ago) using `timezone.now() - timedelta(...)` and filter `Episode.objects.exclude(processing_metrics=None)` on `Q(updated_at__gte=cutoff) | Q(created_at__gte=cutoff)`.
- In `podcasts/tasks.py`, ensure `Episode.objects.filter(id=self.episode.id).update(...)` explicitly includes `updated_at=timezone.now()` so timestamps accurately reflect completion time without relying on `save()`.
- In `podcasts/views.py`, parse and sanitize `time_window` query parameter in `PodcastSubscribeUIView` and `EpisodeStatusAPIView`, generate preserved `time_window_urls`, and pass `time_window` state to template context.
- In `podcasts/templates/podcasts/subscribe.html`, render a clean Bootstrap pill/button group directly above the 2x2 grid, display initial counts without `--` flicker, and pass the active `time_window` to `fetchEpisodeStatus()`.

**Tech Stack:** Python 3.14, Django 5.x, Django ORM, Bootstrap 5.

**Spec:** User request: "It seems that the metrics shown in the UI is for the entire duration of the service. Can you have a filter on the top of the page that filters the metrics here to the last 1d, 1w and 1m?"

## Global Constraints

- **STRICTLY NO DOCKER:** Do not run `docker` or `docker-compose`. All verification commands run strictly on the local host venv: `/home/prmohan/projects/podslice/venv/bin/python manage.py ...`.
- **Zero Schema Migrations:** Pure ORM query filtering over existing fields (`updated_at`, `created_at`, `processing_metrics`). No new database migrations or schema alterations.
- **Defensive Parsing & Zero-Division Safety:** When a filter yields zero processed episodes in the selected time window, all percentages, formatted strings, and counts must default cleanly to `0.0%`, `0h`, `0`, and 100% clean runs without raising `ZeroDivisionError` or exceptions.
- **Dynamic Polling Continuity:** Preserve DOM IDs `completed-count` and `pending-count` so client-side auto-refresh continues functioning seamlessly, and update `fetchEpisodeStatus()` to pass `?time_window=...`.
- **Query Parameter Preservation:** Switching time windows must preserve table filters, pagination resets, search query `q`, and sort parameters (`sort`, `order`).

## Review Focus

1. **Empty Time-Window Boundary:** Selecting `1d` when no episodes have been processed in the last 24 hours must render all 4 cards gracefully without crash or NaN/division error.
2. **Timezone Accuracy:** Cutoff timestamps must use Django's timezone-aware `timezone.now()` to prevent timezone offset bugs with PostgreSQL/SQLite datetimes.
3. **ORM `auto_now` Update Reliability:** `filter().update()` calls in `tasks.py` must pass `updated_at=timezone.now()` explicitly to ensure subsequent time queries find newly processed episodes.
4. **Invalid Filter Fallback:** Submitting an arbitrary or malicious `?time_window=invalid_string` must safely fall back to `"all"` without raising an error.
5. **URL Preservation:** Clicking time filter links must preserve search (`q`), podcast filter (`podcast_id`), status filter (`status`), and sort parameters while dropping `page` so results start from page 1.

---

### Task 1: Backend Filter in `MetricsService` & `tasks.py` Explicit Timestamping

**Files:**
- Modify: `podcasts/tasks.py:1189-1350`
- Modify: `podcasts/services.py:159-535`
- Test: `podcasts/tests/test_metrics.py`

**Interfaces:**
- Consumes: `Episode.updated_at`, `Episode.created_at`, `Episode.processing_metrics`
- Produces: `MetricsService.get_time_window_cutoff(time_window: Optional[str]) -> Optional[datetime.datetime]`
- Produces: `MetricsService.get_system_metrics(time_window: Optional[str] = "all") -> Dict[str, Any]` (includes `"time_window": time_window`)

- [ ] **Step 1: Write the failing unit tests for time window filtering**

In `podcasts/tests/test_metrics.py`, add tests:
- `test_get_time_window_cutoff_boundaries`: checks `get_time_window_cutoff` for `"1d"` (~24h), `"1w"` (~7d), `"1m"` (~30d), `"all"` (None), and invalid string (None).
- `test_get_system_metrics_with_time_window`: creates episodes with different `updated_at` timestamps (e.g., 2 hours ago, 3 days ago, 15 days ago, 45 days ago) and verifies that filtering by `"1d"`, `"1w"`, `"1m"`, and `"all"` returns the exact matching subset of counts, time saved, and API key metrics.
- `test_get_system_metrics_empty_window_safety`: verifies that when a time window has 0 episodes, all metrics return default values without `ZeroDivisionError`.
- `test_get_system_metrics_default_is_all`: verifies that calling `get_system_metrics()` without arguments returns all episodes (100% backward compatibility).

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics -v 2`
Expected: FAIL (`get_system_metrics() got an unexpected keyword argument 'time_window'` or `get_time_window_cutoff` not found).

- [ ] **Step 3: Implement explicit timestamping in `podcasts/tasks.py` and filtering in `podcasts/services.py`**

1. In `podcasts/tasks.py`:
   - In `_rehost_audio()`: update all 3 `Episode.objects.filter(id=self.episode.id).update(...)` calls to include `updated_at=timezone.now()`.
   - In `metrics` dict, set `metrics["completed_at"] = timezone.now().isoformat()`.

2. In `podcasts/services.py`:
   - Implement `MetricsService.get_time_window_cutoff(time_window: Optional[str]) -> Optional[datetime.datetime]`:
     - Clean `time_window` (strip, lower).
     - `"1d"`: `timezone.now() - datetime.timedelta(days=1)`
     - `"1w"`: `timezone.now() - datetime.timedelta(days=7)`
     - `"1m"`: `timezone.now() - datetime.timedelta(days=30)`
     - Any other value (`"all"`, `None`, unknown): return `None`.
   - In `MetricsService.get_system_metrics(time_window: Optional[str] = "all") -> Dict[str, Any]`:
     - Sanitize input: `clean_window = (time_window or "all").strip().lower()`. If `clean_window not in ("1d", "1w", "1m", "all")`: `clean_window = "all"`.
     - Filter queryset:
       ```python
       cutoff = MetricsService.get_time_window_cutoff(clean_window)
       if cutoff:
           episodes = episodes.filter(
               Q(updated_at__gte=cutoff) | Q(created_at__gte=cutoff)
           )
       ```
     - Return `"time_window": clean_window` in the returned dictionary.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics -v 2`
Expected: PASS

- [ ] **Step 5: Run full podcast test suite to verify no regressions**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add podcasts/tasks.py podcasts/services.py podcasts/tests/test_metrics.py
git commit -m "feat(metrics): add time-window filtering (1d, 1w, 1m, all) to MetricsService"
```

---

### Task 2: UI Top Bar Filter Button Group, View Query Parameter Handling & API Integration

**Files:**
- Modify: `podcasts/views.py:279-400, 847-866`
- Modify: `podcasts/templates/podcasts/subscribe.html:34-45, 190-222, 430-450`
- Test: `podcasts/tests/test_views.py`

**Interfaces:**
- Consumes: `MetricsService.get_system_metrics(time_window=...)`, `MetricsService.get_time_window_cutoff(...)`
- Produces: `context['time_window']`, `context['time_window_urls']`, `EpisodeStatusAPIView` `?time_window=` support.

- [ ] **Step 1: Write failing view tests for time filter UI and query handling**

In `podcasts/tests/test_views.py`, add tests:
- `test_subscribe_ui_renders_time_window_filter`: verifies that the main UI page renders filter buttons for "All Time", "Last 1d", "Last 1w", and "Last 1m" with correct links and active state.
- `test_subscribe_ui_filters_metrics_by_time_window`: verifies GET with `?time_window=1d` passes `1d` to `MetricsService` and marks the `Last 1d` button active.
- `test_subscribe_ui_preserves_query_params_on_time_window_change`: verifies that existing filters (`status`, `podcast_id`, `q`, `sort`, `order`) are preserved in `time_window_urls` while `page` is reset.
- `test_episode_status_api_supports_time_window`: verifies `EpisodeStatusAPIView` returns completed count filtered by `time_window` when requested.

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views -v 2`
Expected: FAIL (filter elements or attributes not found).

- [ ] **Step 3: Implement view logic in `podcasts/views.py` and template in `subscribe.html`**

1. In `podcasts/views.py`:
   - In `PodcastSubscribeUIView._get_context()`:
     - Read `time_window = request.GET.get("time_window", "all").strip().lower() if request.method == "GET" else "all"`.
     - Validate: `if time_window not in ["1d", "1w", "1m", "all"]: time_window = "all"`.
     - Pass `time_window` to `MetricsService.get_system_metrics(time_window=time_window)`.
     - Build `time_window_urls`:
       ```python
       time_window_urls = {}
       for tw in ["all", "1d", "1w", "1m"]:
           tw_params = query_params.copy()
           if tw == "all":
               if "time_window" in tw_params:
                   del tw_params["time_window"]
           else:
               tw_params["time_window"] = tw
           tw_querystring = tw_params.urlencode() if hasattr(tw_params, "urlencode") else ""
           time_window_urls[tw] = f"?{tw_querystring}" if tw_querystring else "?"
       ```
     - Add `time_window` and `time_window_urls` to context.
     - In `sort_urls` generation and pagination links, ensure `time_window` is retained when not "all".
   - In `EpisodeStatusAPIView.get()`:
     - Read `time_window = request.GET.get("time_window", "all").strip().lower()`.
     - If `time_window not in ["1d", "1w", "1m", "all"]`: `time_window = "all"`.
     - Filter `completed_qs = Episode.objects.filter(status=Episode.Status.COMPLETE)`.
     - `cutoff = MetricsService.get_time_window_cutoff(time_window)`.
     - If `cutoff`: `completed_qs = completed_qs.filter(Q(updated_at__gte=cutoff) | Q(created_at__gte=cutoff))`.
     - Return count of `completed_qs`.

2. In `podcasts/templates/podcasts/subscribe.html`:
   - Right above the 2x2 grid (`row row-cols-1 row-cols-md-2 g-3 mb-4`):
     Add header toolbar:
     ```html
     <div class="d-flex flex-wrap justify-content-between align-items-center mb-3 gap-2">
         <h4 class="mb-0 fs-5 fw-bold text-dark">📊 Service Metrics</h4>
         <div class="btn-group btn-group-sm" role="group" aria-label="Metrics Time Window Filter">
             <a href="{{ time_window_urls.all }}" class="btn {% if time_window == 'all' %}btn-primary active fw-semibold{% else %}btn-outline-secondary{% endif %}">All Time</a>
             <a href="{{ time_window_urls.1d }}" class="btn {% if time_window == '1d' %}btn-primary active fw-semibold{% else %}btn-outline-secondary{% endif %}">Last 1d</a>
             <a href="{{ time_window_urls.1w }}" class="btn {% if time_window == '1w' %}btn-primary active fw-semibold{% else %}btn-outline-secondary{% endif %}">Last 1w</a>
             <a href="{{ time_window_urls.1m }}" class="btn {% if time_window == '1m' %}btn-primary active fw-semibold{% else %}btn-outline-secondary{% endif %}">Last 1m</a>
         </div>
     </div>
     ```
   - In Card 4:
     - Set initial `completed-count` text to `{{ metrics.completed_episodes|default:0 }}`.
     - Set initial `pending-count` text to `{{ total_pending_count|default:0 }}`.
   - In `fetchEpisodeStatus()`:
     - Pass `?time_window={{ time_window }}` if `time_window != 'all'`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views -v 2`
Expected: PASS

- [ ] **Step 5: Run full podcast test suite to verify no regressions**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add podcasts/views.py podcasts/templates/podcasts/subscribe.html podcasts/tests/test_views.py
git commit -m "feat(ui): add dashboard metrics time-window filter (1d, 1w, 1m, all) with active state"
```

---

### Task 3: Full Regression, Code Quality & Whole-Branch Review

**Files:**
- Modified files from Tasks 1 & 2

- [ ] **Step 1: Run pylint on all modified files**

Run: `/home/prmohan/projects/podslice/venv/bin/pylint podcasts/services.py podcasts/views.py podcasts/tasks.py podcasts/tests/test_metrics.py podcasts/tests/test_views.py`
Expected: No fatal errors or syntax violations. Address any warnings.

- [ ] **Step 2: Run complete test suite**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests pass.

- [ ] **Step 3: Dispatch Whole-Branch Final Reviewer**

Invoke subagent on `pro` tier to perform a whole-branch code review checking spec alignment, zero-division resilience, timezone safety, dynamic polling continuity, and code quality.

- [ ] **Step 4: Push to origin/main**

Run: `git push origin main`
Expected: Successfully pushed to remote.
