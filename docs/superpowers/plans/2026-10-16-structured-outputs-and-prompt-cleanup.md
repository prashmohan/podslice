# Structured Outputs & Prompt Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Overview
Adopt Google GenAI SDK best practices for structured JSON outputs:
1. Clean up `GEMINI_PROMPT` to remove duplicate few-shot JSON markdown code blocks so Gemini relies purely on schema-constrained generation via `response_schema=list[AdSegmentSchema]`. Enrich `AdSegmentSchema` field descriptions to provide schema-level classification guidance.
2. In `AdManager`, capture and leverage native `response.parsed` (a typed list of `AdSegmentSchema` instances provided by `google-genai`) while preserving a robust, graceful fallback to `_parse_ad_segments(gemini_response)` if SDK parsing is absent or fails.
3. Verify with unit tests, pylint, and full regression testing with zero test regressions.

## Proposed Changes

### `podcasts/tasks.py`
- In `GEMINI_PROMPT`: Remove `**Example Output:**` markdown JSON code block. Keep all category instructions, duration variables, and boundary/outro rules.
- In `AdSegmentSchema`: Enrich `label` field description to specify the classification category taxonomy (e.g. `ad`, `intro`, `outro`, `network-promo`, `housekeeping`, `recap`, `preview`, `music`, `off-topic`).
- In `AdManager`:
  - Initialize `self.last_parsed_response: Any = None`.
  - In `_call_model_with_key`: store `self.last_parsed_response = getattr(response, "parsed", None)`.
  - Implement `_extract_from_parsed(self, parsed_obj: Any) -> Optional[List[Dict[str, Any]]]`:
    Defensively inspect items in `parsed_obj`. Handle both `AdSegmentSchema` instances and dictionary objects, run `_normalize_timestamp`, ensure `end > start`, and sort by `start`.
  - In `analyze_audio`: Check `self.last_parsed_response` first via `_extract_from_parsed()`. If valid segments are returned, use them and persist `self.episode.ad_segments = gemini_response` (or json representation). If `self.last_parsed_response` is None, empty, or invalid, gracefully fall back to `_parse_ad_segments(gemini_response)`.
  - In `_parse_ad_segments`: Defensively handle cases where input is already a list or iterable of objects/dicts.

### `podcasts/tests/test_tasks.py`
- Update `test_gemini_prompt_formats_cleanly` to reflect the removal of the duplicate JSON code block from `GEMINI_PROMPT` while verifying proper string formatting with `{episode_title}`, `{podcast_title}`, `{audio_duration_seconds}`.
- Add unit tests for `response.parsed` handling:
  - Test `analyze_audio` with `response.parsed` populated with `[AdSegmentSchema(...)]`.
  - Test graceful fallback when `response.parsed` is `None` (ensuring mock tests setting `text` still pass seamlessly).
  - Test graceful fallback when `response.parsed` contains invalid timestamps (`end <= start` or malformed data).
  - Test `_parse_ad_segments` handling both string JSON and pre-parsed structures.

---

## Tasks

### Task 1: Prompt Cleanup & Schema Enrichment
- [ ] Update `test_gemini_prompt_formats_cleanly` in `podcasts/tests/test_tasks.py` to expect clean formatted prompt without duplicate JSON markdown code blocks.
- [ ] Run test to verify failure (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks.TasksTestCase.test_gemini_prompt_formats_cleanly`).
- [ ] In `podcasts/tasks.py`:
  - Remove `**Example Output:**` block from `GEMINI_PROMPT`.
  - Enrich `AdSegmentSchema.label` Field description with taxonomy examples.
- [ ] Run tests to verify passing (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`).
- [ ] Commit: `refactor(tasks): clean up GEMINI_PROMPT and enrich AdSegmentSchema`

### Task 2: Native `response.parsed` Support with Graceful Fallback
- [ ] Add unit tests in `podcasts/tests/test_tasks.py` for `response.parsed` extraction and fallback scenarios.
- [ ] Run tests to verify failure.
- [ ] In `podcasts/tasks.py`:
  - Update `AdManager.__init__` to initialize `self.last_parsed_response = None`.
  - In `_call_model_with_key`, capture `self.last_parsed_response = getattr(response, "parsed", None)`.
  - Implement `_extract_from_parsed(self, parsed_obj: Any) -> Optional[List[Dict[str, Any]]]`.
  - In `analyze_audio`, try `_extract_from_parsed(self.last_parsed_response)` before falling back to `_parse_ad_segments(gemini_response)`.
  - In `_parse_ad_segments`, handle both string input and list input defensively.
- [ ] Run tests to verify passing (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`).
- [ ] Verify full test suite passes (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`).
- [ ] Commit: `feat(tasks): leverage response.parsed from google-genai with graceful fallback`

### Task 3: Pylint, Regression Suite & Whole-Branch Review
- [ ] Run pylint on modified files (`podcasts/tasks.py`, `podcasts/tests/test_tasks.py`).
- [ ] Run complete test suite (`/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`).
- [ ] Dispatch Whole-Branch Code Reviewer subagent (`modelTier="pro"`).
- [ ] Address any reviewer recommendations.
- [ ] Push all commits to `origin/main`.
