# Extend Pending Episodes Table to All Episodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the "Pending Episodes" table in the Podslice subscription UI (`/`) to display all episodes across all statuses (including COMPLETE and FAILED), while retaining and using by default an "All Pending Statuses" filter to preserve the existing pending-episodes workflow.

**Architecture:** Update `PodcastSubscribeUIView._get_context` to support a tri-state status filter: default (no `status` or empty) filters to pending statuses (`NEW`, `QUEUED`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`); `status=all` displays all episodes without status restriction; explicit status (e.g. `COMPLETE`, `FAILED`, `DOWNLOADING`) filters by that specific status. Update `subscribe.html` to add "All Statuses" and all episode statuses in the select dropdown, style `COMPLETE` (`bg-success`) and `FAILED` (`bg-danger`) badges, update the card title to "Episodes", and ensure card visibility and empty states gracefully guide the user.

**Tech Stack:** Python 3.12+, Django 5+, Bootstrap 5, Django Test Client.

**Spec:** User request: "Can we extend the Pending Episodes table in the UI to not include just pending episodes but all episodes. We can retain (and use by default) a 'All pending Statuses' filter to reflect the current workflow. But this would also allow us to show all of the recently processed episodes as well from the same table." / "The list of all episodes and its current status"

## Global Constraints
- Preserve backward compatibility for existing query parameters (`page`, `sort`, `order`, `podcast_id`, `q`, `time_window`).
- Default view without a `status` query parameter MUST retain current behavior: showing only pending statuses (`NEW`, `QUEUED`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`).
- The reset filter button MUST reset back to the default view (clearing `status`, `podcast_id`, `q`, while keeping `time_window` if active).
- Keep `id="pending-episodes-panel"` on the episodes card container for DOM consistency and test compatibility.
- Ensure all tests in `podcasts/tests/` continue to pass without regression.

## Review Focus
- Default view regression: Visiting `/` without `status` parameter must NEVER show `COMPLETE` or `FAILED` episodes.
- Status `all`: Visiting `/?status=all` must return episodes with any status including `COMPLETE` and `FAILED`.
- Specific completed/failed filtering: `/?status=COMPLETE` and `/?status=FAILED` must correctly return matching episodes.
- Card visibility: Card must be hidden when total episodes count is 0 and no filters are set, but must remain visible when completed episodes exist so users can switch the dropdown.
- Empty state: When pending filter produces 0 results but episodes exist in the system, display helpful guidance to switch filter to "All Statuses" or "Complete".

---

## Tasks

### Task 1: Extend `PodcastSubscribeUIView` Status Filtering Logic
**Files:**
- Modify: `podcasts/views.py:280-410`
- Test: `podcasts/tests/test_views.py`

**Interfaces:**
- Consumes: `Episode.Status` choices (`NEW`, `QUEUED`, `DOWNLOADING`, `ANALYZING`, `PROCESSING`, `COMPLETE`, `FAILED`).
- Produces: `PodcastSubscribeUIView.ALL_STATUSES`, context variables `all_statuses`, `total_episodes_count`, and updated `pending_episodes` queryset filtering logic.

- [ ] **Step 1: Write failing tests in `podcasts/tests/test_views.py`**
Add unit tests verifying:
  - `test_episodes_filter_default_shows_pending_statuses`: When no `status` is provided, only pending episodes are included; complete and failed episodes are excluded.
  - `test_episodes_filter_all_statuses`: When `?status=all` is passed, episodes with status `NEW`, `DOWNLOADING`, `COMPLETE`, and `FAILED` are all returned in the page.
  - `test_episodes_filter_complete_status`: When `?status=COMPLETE` is passed, only `COMPLETE` episodes are returned.
  - `test_episodes_filter_failed_status`: When `?status=FAILED` is passed, only `FAILED` episodes are returned.

- [ ] **Step 2: Run tests to verify they fail**
Run: `./venv/bin/python manage.py test podcasts.tests.test_views.PodcastSubscribeUIViewTest.test_episodes_filter_all_statuses`
Expected: FAIL (returns only pending episodes because base queryset currently hardcodes `status__in=self.PENDING_STATUSES`).

- [ ] **Step 3: Implement status filtering logic in `PodcastSubscribeUIView`**
In `podcasts/views.py`:
  - Define `ALL_STATUSES = [(s.value, s.label) for s in Episode.Status]`.
  - In `_get_context(self, request, extra_context=None)`:
    - Base queryset: `episodes_qs = Episode.objects.select_related('podcast')`.
    - Check `status_filter = request.GET.get("status", "").strip() if request.method == "GET" else ""`.
    - If `status_filter.lower() == "all"`: do not apply status filter on `episodes_qs`.
    - Else if `status_filter in Episode.Status.values`: `episodes_qs = episodes_qs.filter(status=status_filter)`.
    - Else (default, when `status_filter == ""` or `status_filter == "pending"` or unrecognized): `episodes_qs = episodes_qs.filter(status__in=self.PENDING_STATUSES)`, and normalize `status_filter = ""`.
    - Calculate counts:
      - `total_episodes_count = Episode.objects.count()`
      - `total_pending_count = Episode.objects.filter(status__in=self.PENDING_STATUSES).count()`
    - Add to `context`:
      - `'pending_episodes': pending_page`
      - `'episodes': pending_page`
      - `'total_episodes_count': total_episodes_count`
      - `'total_pending_count': total_pending_count`
      - `'all_statuses': self.ALL_STATUSES`
      - `'pending_statuses': [(s.value, s.label) for s in self.PENDING_STATUSES]`
      - `'selected_status': status_filter`

- [ ] **Step 4: Run tests to verify they pass**
Run: `./venv/bin/python manage.py test podcasts.tests.test_views.PodcastSubscribeUIViewTest`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add podcasts/views.py podcasts/tests/test_views.py
git commit -m "feat(views): support all statuses and all-episodes filtering in PodcastSubscribeUIView"
```

---

### Task 2: Update UI Template `subscribe.html` with All Episodes & Badges
**Files:**
- Modify: `podcasts/templates/podcasts/subscribe.html:236-405`
- Test: `podcasts/tests/test_views.py`

**Interfaces:**
- Consumes: `all_statuses`, `total_episodes_count`, `total_pending_count`, `selected_status`, `pending_episodes`.
- Produces: Rendered episodes table supporting all statuses, updated card heading "Episodes", dropdown with "All Pending Statuses" and "All Statuses", badge styling for `COMPLETE` and `FAILED`, and contextual empty state.

- [ ] **Step 1: Write failing template tests in `podcasts/tests/test_views.py`**
Add tests verifying:
  - `test_episodes_template_dropdown_options`: Verify dropdown contains `<option value="">All Pending Statuses</option>`, `<option value="all">All Statuses</option>`, and individual choices like `Complete` and `Failed`.
  - `test_episodes_template_complete_and_failed_badges`: Verify `COMPLETE` episodes render `<span class="badge bg-success">Complete</span>` and `FAILED` episodes render `<span class="badge bg-danger">Failed</span>`.
  - `test_episodes_panel_visible_when_completed_episodes_exist`: Verify that when there are completed episodes but 0 pending episodes, the episodes panel is still visible (allowing user to switch filter to All/Complete).
  - `test_episodes_empty_state_guidance`: Verify empty state guides users to switch filter if other episodes exist in the system.

- [ ] **Step 2: Run tests to verify they fail**
Run: `./venv/bin/python manage.py test podcasts.tests.test_views.PodcastSubscribeUIViewTest.test_episodes_template_dropdown_options`
Expected: FAIL.

- [ ] **Step 3: Update `podcasts/templates/podcasts/subscribe.html`**
  - Panel visibility:
    `{% if total_episodes_count > 0 or selected_status or selected_podcast or search_query %}`
  - Card header title:
    `<h3 class="card-title mb-0">Episodes</h3>`
  - Select dropdown:
    ```html
    <select name="status" class="form-select form-select-sm">
        <option value="">All Pending Statuses</option>
        <option value="all" {% if selected_status == 'all' %}selected{% endif %}>All Statuses</option>
        {% for s_val, s_label in all_statuses %}
        <option value="{{ s_val }}" {% if selected_status == s_val %}selected{% endif %}>{{ s_label }}</option>
        {% endfor %}
    </select>
    ```
  - Badges in table body:
    ```html
    {% if episode.status == 'NEW' %}
        <span class="badge bg-info text-dark">New</span>
    {% elif episode.status == 'QUEUED' %}
        <span class="badge bg-secondary">Queued</span>
    {% elif episode.status == 'DOWNLOADING' %}
        <span class="badge bg-primary">Downloading</span>
    {% elif episode.status == 'ANALYZING' %}
        <span class="badge bg-warning text-dark">Analyzing</span>
    {% elif episode.status == 'PROCESSING' %}
        <span class="badge bg-primary">Processing</span>
    {% elif episode.status == 'COMPLETE' %}
        <span class="badge bg-success">Complete</span>
    {% elif episode.status == 'FAILED' %}
        <span class="badge bg-danger">Failed</span>
    {% else %}
        <span class="badge bg-secondary">{{ episode.get_status_display }}</span>
    {% endif %}
    ```
  - Empty state message:
    ```html
    <div class="text-center py-4 text-muted">
        {% if selected_status == "" or selected_status == "pending" %}
            <p class="mb-2">No pending episodes match the selected filter criteria.</p>
            {% if total_episodes_count > 0 %}
            <p class="small text-muted mb-2">Switch the status filter to <strong>All Statuses</strong> or <strong>Complete</strong> to view processed episodes.</p>
            {% endif %}
        {% else %}
            <p class="mb-2">No episodes match the selected filter criteria.</p>
        {% endif %}
        <a href="{% if time_window and time_window != 'all' %}?time_window={{ time_window }}{% else %}{% url 'podcast-subscribe-ui' %}{% endif %}" class="btn btn-sm btn-outline-primary">Reset Filters</a>
    </div>
    ```

- [ ] **Step 4: Run tests to verify they pass**
Run: `./venv/bin/python manage.py test podcasts.tests.test_views.PodcastSubscribeUIViewTest`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add podcasts/templates/podcasts/subscribe.html podcasts/tests/test_views.py
git commit -m "feat(ui): update episodes table to support all statuses, complete/failed badges, and clear empty state"
```

---

### Task 3: Quality Verification, Pylint & Regression Suite
**Files:**
- Audit: `podcasts/views.py`, `podcasts/templates/podcasts/subscribe.html`, `podcasts/tests/test_views.py`

- [ ] **Step 1: Run code quality linters**
Run pylint on modified Python files to ensure code quality and no lint warnings:
`./venv/bin/pylint podcasts/views.py podcasts/tests/test_views.py`

- [ ] **Step 2: Run complete test suite**
Run: `./venv/bin/python manage.py test podcasts`
Expected: All 186+ tests pass with 0 errors and 0 failures.

- [ ] **Step 3: Dispatch whole-branch code reviewer subagent**
Verify adherence to requirements, no regressions, clean code formatting.

- [ ] **Step 4: Commit any final polish if needed**
```bash
git commit -m "chore: code quality polish and test verification for all-episodes table"
```
