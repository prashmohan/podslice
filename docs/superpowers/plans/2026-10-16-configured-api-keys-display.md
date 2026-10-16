# Configured API Keys & Standby Status Display Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Overview
Currently, the "🔑 API Keys & Quota Balance" widget in `subscribe.html` only displays keys that have historical call records in `Episode.processing_metrics`. Because Podslice uses a sequential failover strategy (Key 1 -> Key 2 -> Key 3), Key 2 and Key 3 remain in warm standby as long as Key 1 does not hit rate limits, causing them to record 0 calls and disappear from the UI, giving the impression that only 1 key is configured.

This feature updates:
1. `podslice/settings.py`: Harden `GEMINI_API_KEYS` parsing to strip single and double quotes defensively.
2. `podcasts/services.py`: In `MetricsService.get_system_metrics()`, cross-reference `settings.GEMINI_API_KEYS`. Include configured standby keys (with `0 calls`, `0.0%` share, `is_active=False`) alongside active keys, and supply `configured_keys_count` and `active_keys_count`.
3. `podcasts/templates/podcasts/subscribe.html`: Update Card 3 header to display `X of Y Keys Used` (e.g. `1 of 3 Keys Used`), and render standby keys clearly with a `Standby` badge.
4. Comprehensive unit & view tests in `podcasts/tests/test_metrics.py`, `podcasts/tests/test_settings.py`, and `podcasts/tests/test_views.py`.

---

## Tasks

### Task 1: Harden Settings Parsing & Extend MetricsService with Configured Standby Keys
- [ ] Write failing unit tests in `podcasts/tests/test_settings.py` for quoted `GEMINI_API_KEYS` environment variables.
- [ ] Write failing unit tests in `podcasts/tests/test_metrics.py` asserting that `MetricsService.get_system_metrics()` includes configured standby keys when `settings.GEMINI_API_KEYS` has keys not yet present in telemetry.
- [ ] Run tests to verify failure.
- [ ] In `podslice/settings.py`: Strip quotes in `_raw_gemini_keys` and individual key tokens.
- [ ] In `podcasts/services.py`:
  - Add helper `mask_api_key(key: str) -> str`.
  - In `MetricsService.get_system_metrics()`: cross-reference `settings.GEMINI_API_KEYS`, append uncalled keys as standby (`is_active=False`, `0 calls`, `0 rate limits`), calculate `configured_keys_count`, `active_keys_count`, and sort active keys before standby keys.
- [ ] Run tests to verify passing (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics podcasts.tests.test_settings`).
- [ ] Commit: `feat(metrics): cross-reference configured API keys and track standby keys`

### Task 2: Update UI Dashboard Card in `subscribe.html` & View Integration
- [ ] Add view test in `podcasts/tests/test_views.py` checking that `subscribe.html` displays `1 of 3 Keys Used` and `Standby` badges when multiple keys are configured.
- [ ] Run test to verify failure.
- [ ] In `podcasts/templates/podcasts/subscribe.html`:
  - Update Card 3 header badge: display `{{ metrics.active_keys_count|default:0 }} of {{ metrics.configured_keys_count }} Keys Used` when `metrics.configured_keys_count` is present, else fallback to `{{ metrics.active_keys_count }} Keys Active`.
  - In the key loop: if `key_item.is_active` is True, show call percentage and primary progress bar; if False, show `Standby` badge and `0 calls (Standby)`.
- [ ] Run view tests to verify passing (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_views`).
- [ ] Verify full test suite passes (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`).
- [ ] Commit: `feat(ui): display configured API key count and standby badges in quota balance card`

### Task 3: Pylint, Regression Suite & Whole-Branch Review
- [ ] Run pylint on modified files (`podcasts/services.py`, `podslice/settings.py`, `podcasts/tests/test_metrics.py`, `podcasts/tests/test_views.py`).
- [ ] Run complete test suite (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`).
- [ ] Dispatch Whole-Branch Code Reviewer subagent (`pro` tier).
- [ ] Commit review polish and push to `origin/main`.
