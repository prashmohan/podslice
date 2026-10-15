# Audio Segment Detection Prompt Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `GEMINI_PROMPT` in `podcasts/tasks.py` to reliably detect and strip outros (with terminal anchoring to `{audio_duration_seconds}`), network cross-promotions/trailers, housekeeping/merch/live tour plugs, serialized recaps/previews, and extended instrumental interludes, while updating classification categorization in `podcasts/services.py`.

**Architecture:** Update the prompt structure in `podcasts/tasks.py` (`GEMINI_PROMPT`) to separate `Intro` and `Outro` into distinct directives, add explicit terminal boundary rules for outros, expand the category taxonomy, and update few-shot JSON examples. Update `categorize_segment_label` in `podcasts/services.py` so newly tagged segments map predictably into the dashboard's 3-bin breakdown (`ads`, `intros_outros`, `ctas`).

**Tech Stack:** Python 3.14, Django 5.2, google-genai SDK, Pydantic, host venv.

**Spec:** [`docs/superpowers/specs/2026-10-15-audio-segment-detection-prompt-upgrade-design.md`](file:///home/prmohan/projects/podslice/docs/superpowers/specs/2026-10-15-audio-segment-detection-prompt-upgrade-design.md)

## Global Constraints

- **STRICTLY NO DOCKER**: Do not run `docker` or `docker-compose` commands under any circumstances. All tests and commands run locally on host venv: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`.
- **Zero Schema Changes**: Pure prompt template and service mapping updates; schema remains `AdSegmentSchema(start=float, end=float, label=str)`.
- **Python String Formatting Safety**: In `GEMINI_PROMPT`, curly braces for literal JSON examples must be escaped as `{{` and `}}` while parameters `{episode_title}`, `{podcast_title}`, and `{audio_duration_seconds}` remain single-bracket template variables.
- **Defensive Label Mapping**: `categorize_segment_label` must safely handle `None`, empty strings, and unknown labels, defaulting to `"ads"`.

## Review Focus

1. **Prompt Template Formatting**: Ensuring `.format(...)` does not raise `KeyError` or `ValueError` due to unescaped JSON brackets in the example block.
2. **Terminal Outro Anchoring**: Clear and unambiguous phrasing in `GEMINI_PROMPT` instructing Gemini that the outro segment must run to `{audio_duration_seconds}`.
3. **AdSegmentSchema Compatibility**: Emitted labels remain clean strings that validate against Pydantic schema `AdSegmentSchema`.
4. **Dashboard Category Mapping**: Verify that all new labels (`network-promo`, `housekeeping`, `recap`, `preview`, `interlude`) map correctly into `ads`, `intros_outros`, or `ctas`.
5. **No Regressions on Ad Detection**: Preserving native host-read ad instructions and buffer/natural pause guidance.

---

### Task 1: Update Categorization Service & Classification Mapping

**Files:**
- Modify: `podcasts/services.py:65-80`
- Test: `podcasts/tests/test_metrics.py`

**Interfaces:**
- Consumes: Segment labels emitted by Gemini.
- Produces: `categorize_segment_label(label: Optional[str]) -> str` returning `"ads"`, `"intros_outros"`, or `"ctas"`.

- [ ] **Step 1: Write failing unit tests in `podcasts/tests/test_metrics.py`**

Add tests:
- `test_categorize_segment_label_new_categories`: verifies `network-promo`, `cross-promo`, `trailer`, `merch` map to `"ads"`; `recap`, `preview`, `teaser`, `interlude`, `silence` map to `"intros_outros"`; and `housekeeping`, `tour`, `live-show`, `patreon`, `membership` map to `"ctas"`.
- `test_get_system_metrics_aggregates_expanded_categories`: creates an episode with `network-promo`, `housekeeping`, `recap`, and `outro` segments, verifies counts and percentages in `MetricsService.get_system_metrics()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: FAIL on new label mappings.

- [ ] **Step 3: Update `categorize_segment_label` in `podcasts/services.py`**

Expand keyword matching in `categorize_segment_label`:
```python
def categorize_segment_label(label: Optional[str]) -> str:
    if not label or not isinstance(label, str):
        return "ads"
    norm = label.strip().lower()
    if any(k in norm for k in ["intro", "outro", "music", "theme", "silence", "interlude", "recap", "preview", "teaser"]):
        return "intros_outros"
    if any(k in norm for k in ["cta", "call to action", "promo", "promotion", "housekeeping", "tour", "live-show", "patreon", "membership", "off-topic", "other"]):
        if any(k in norm for k in ["network-promo", "cross-promo", "trailer"]):
            return "ads"
        return "ctas"
    if any(k in norm for k in ["ad", "sponsor", "advertisement", "cross-promo", "network-promo", "trailer", "merch"]):
        return "ads"
    return "ads"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_metrics`
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All 154+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add podcasts/services.py podcasts/tests/test_metrics.py
git commit -m "feat(metrics): expand segment categorization for network promos, housekeeping, and recaps"
```

---

### Task 2: Redesign GEMINI_PROMPT with Terminal Outro Anchoring & Expanded Taxonomy

**Files:**
- Modify: `podcasts/tasks.py:45-78`
- Test: `podcasts/tests/test_tasks.py`

**Interfaces:**
- Consumes: `{episode_title}`, `{podcast_title}`, `{audio_duration_seconds}`.
- Produces: Formatted prompt sent to Gemini with explicit directives for Outro (terminal anchoring), Network Promos, Housekeeping, Recaps/Previews, and Extended Music.

- [ ] **Step 1: Write unit tests in `podcasts/tests/test_tasks.py`**

Add tests:
- `test_gemini_prompt_contains_outro_terminal_and_expanded_categories`: verifies `GEMINI_PROMPT` contains directives for "Outro", "Network Promos", "Housekeeping", "Recaps & Previews", and terminal anchoring to `audio_duration_seconds`.
- `test_gemini_prompt_formats_cleanly`: verifies `GEMINI_PROMPT.format(episode_title="Test", podcast_title="Cast", audio_duration_seconds=1800.0)` does not raise `KeyError` or bracket errors, and includes `1800.0` in the output example.
- `test_parse_ad_segments_handles_expanded_taxonomy`: verifies `AdManager._parse_ad_segments` parses JSON containing `outro`, `network-promo`, `housekeeping`, `recap`, `preview`.

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`
Expected: FAIL due to missing prompt clauses.

- [ ] **Step 3: Update `GEMINI_PROMPT` in `podcasts/tasks.py`**

Restructure `GEMINI_PROMPT`:
1. Distinct `**Outro & Sign-Offs**` category:
   "Spoken host goodbyes ('that wraps it up', 'thanks for listening'), production credits, sound engineering credits, legal notices, network sign-offs, and closing theme music. Crucially: once the core episode discussion or interview concludes, identify the start of the wrap-up and extend this segment's end time all the way to {audio_duration_seconds}."
2. Distinct `**Network Cross-Promotions & Trailers**` category:
   "Promotional audio trailers or host plugs for other podcasts or network shows."
3. Distinct `**Housekeeping & CTAs**` category:
   "Tour dates, live shows, merch drops, subscription/Patreon/Discord pitches, reviews, social media."
4. Distinct `**Recaps & Previews**` category:
   "'Previously on...' recaps of past episodes, or 'Next time on...' teasers for upcoming episodes."
5. Distinct `**Silence & Instrumental Beds**` category:
   "Silence >= 3s, or extended non-vocal musical transitions/interludes >= 10s."
6. Guidance for Precision:
   - Specific rule on **Complete Outro Removal**: "The outro segment must extend all the way to {audio_duration_seconds} unless substantive bonus discussion occurs after."
7. Few-shot example containing `intro`, `recap`, `ad`, `music`, `network-promo`, `housekeeping`, and `outro` ending at `{audio_duration_seconds}`.

- [ ] **Step 4: Run task tests to verify they pass**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts.tests.test_tasks`
Expected: PASS

- [ ] **Step 5: Run full test suite regression**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All 154+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add podcasts/tasks.py podcasts/tests/test_tasks.py
git commit -m "feat(tasks): upgrade GEMINI_PROMPT with terminal outro anchoring and expanded segment taxonomy"
```

---

### Task 3: Pylint Linting, Whole-Branch Review & Upstream Push

**Files:**
- Inspect: `podcasts/tasks.py`, `podcasts/services.py`, `podcasts/tests/test_tasks.py`, `podcasts/tests/test_metrics.py`

- [ ] **Step 1: Run pylint on modified files**

Run: `DJANGO_SETTINGS_MODULE=podslice.settings REHOST_BASE_URL=http://localhost:8000 /home/prmohan/projects/podslice/venv/bin/pylint podcasts/tasks.py podcasts/services.py podcasts/tests/test_tasks.py podcasts/tests/test_metrics.py`
Expected: 10.00/10 rating (or no blocking errors).

- [ ] **Step 2: Run full regression test suite**

Run: `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts`
Expected: All tests pass.

- [ ] **Step 3: Dispatch Whole-Branch Final Reviewer**

Use `superpowers:requesting-code-review` on `pro` model tier.

- [ ] **Step 4: Push to `origin/main`**

Run: `git push origin main`
Expected: Fast-forward push succeeds.
