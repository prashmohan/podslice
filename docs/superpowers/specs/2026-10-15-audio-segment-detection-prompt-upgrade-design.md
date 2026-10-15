# Design Specification: Audio Segment Detection Prompt Upgrade

**Date:** 2026-10-15  
**Status:** Approved  
**Target:** `podcasts/tasks.py` (`GEMINI_PROMPT`), `podcasts/services.py` (`categorize_segment_label`), `podcasts/tests/`

---

## 1. Executive Summary & Intent

Podslice utilizes Gemini (`gemini-3.8-flash` with fallback to `gemini-3.5-flash-lite`) to detect and remove non-essential audio segments from podcast episodes. In recent episodes, outros were frequently retained because:
1. `Intro/Outro` was grouped into a single prompt directive that explicitly instructed the model *not* to cut host speech over music, which inadvertently protected conversational outro sign-offs, production credits, and legal disclaimers.
2. The few-shot example in the prompt omitted an `outro` entry, failing to demonstrate outro tagging.
3. The prompt lacked a terminal boundary rule directing Gemini to extend outro cuts continuously to the end of the file (`{audio_duration_seconds}`).
4. Other common non-essential segments (network cross-promotional trailers, live show/merch housekeeping, "previously on" recaps, "next time on" previews, and long musical interludes) lacked explicit definitions.

This upgrade:
1. Redesigns `GEMINI_PROMPT` with separated, explicit categories and terminal outro anchoring.
2. Expands the taxonomy to cover network cross-promotions, housekeeping/merch/live tour plugs, serialized recaps/previews, and extended instrumental interludes.
3. Updates `categorize_segment_label` in `podcasts/services.py` so all newly emitted labels map accurately into Podslice's dashboard metrics categories (`ads`, `intros_outros`, `ctas`).
4. Adds automated unit tests verifying prompt formatting, schema compliance, and classification categorization.

---

## 2. Updated Category Taxonomy

| Category | Prompt Label | Description | Dashboard Bin |
|---|---|---|---|
| **Commercial Ads & Sponsorships** | `ad` / `sponsor` | Pre-roll, mid-roll, sponsor reads, native host-read brand stories, discount codes. | `ads` |
| **Network Cross-Promotions & Trailers** | `network-promo` / `promo` | Promotional audio trailers or host plugs for sister podcasts / network shows (e.g. Wondery, iHeart, NPR). | `ads` |
| **Intro & Cold Opens** | `intro` | Opening theme songs, jingles, repetitive branding/disclaimers with no active episode discussion. | `intros_outros` |
| **Outro & Closing Wrap-up** | `outro` | Spoken host goodbyes, production/sound design credits, legal disclaimers, network tags, and closing theme music. Once main discussion concludes, must extend continuously to `{audio_duration_seconds}`. | `intros_outros` |
| **Housekeeping & CTAs** | `housekeeping` / `cta` | Live tour dates, merchandise plugs, subscription/Patreon/membership pitches, review requests, social media follows. | `ctas` |
| **Recaps & Previews** | `recap` / `preview` | "Previously on..." recaps in serialized shows, and "Next time on..." teasers before/during outro. | `intros_outros` |
| **Silence & Instrumental Beds** | `silence` / `music` | Continuous dead air (≥3s) or extended non-vocal musical transitions/interludes between interview segments (≥10s). | `intros_outros` |

---

## 3. Detailed Prompt Enhancements (`GEMINI_PROMPT`)

### 3.1 Terminal Outro Anchoring Rule
- **Explicit Instruction:** "Once the core episode discussion or interview concludes, identify the start of the host wrap-up/sign-off and mark the segment as `outro`, extending its `end` timestamp all the way to `{audio_duration_seconds}`. Do not leave trailing silence, credits, legal disclaimers, or exit music behind."

### 3.2 Clear Separation of Spoken Content vs Editorial Content
- Differentiate between:
  - **Editorial Content:** The actual topic, story, or interview discussion of the episode.
  - **Wrap-up & Non-Essential Speech:** Credits ("Produced by...", "Sound engineered by..."), network announcements, legal disclaimers, and sign-offs ("Thanks for listening!").

### 3.3 Few-Shot Output Demonstration
The JSON example in the prompt will include:
```json
[
  {"start": 0.0, "end": 45.2, "label": "intro"},
  {"start": 45.2, "end": 95.0, "label": "recap"},
  {"start": 605.1, "end": 690.5, "label": "ad"},
  {"start": 1200.0, "end": 1215.0, "label": "music"},
  {"start": 1850.0, "end": 1920.0, "label": "network-promo"},
  {"start": 3200.0, "end": 3260.0, "label": "housekeeping"},
  {"start": 3260.0, "end": {audio_duration_seconds}, "label": "outro"}
]
```

---

## 4. Backend Service Categorization (`podcasts/services.py`)

`categorize_segment_label(label: Optional[str]) -> str` is updated:
- `ads`: `sponsor`, `ad`, `advertisement`, `network-promo`, `cross-promo`, `trailer`, `merch`.
- `intros_outros`: `intro`, `outro`, `music`, `theme`, `silence`, `interlude`, `recap`, `preview`, `teaser`.
- `ctas`: `cta`, `call to action`, `promo`, `promotion`, `housekeeping`, `live-show`, `tour`, `patreon`, `membership`, `off-topic`, `other`.

---

## 5. Verification & Testing Plan

1. **Prompt Structure & Formatting Tests (`test_tasks.py`)**:
   - Verify that `GEMINI_PROMPT` contains explicit instructions for `Outro`, `Network Promos`, `Housekeeping`, `Recaps`, and `Previews`.
   - Verify that formatting `GEMINI_PROMPT` with dynamic duration formats the example JSON and instructions without key errors.
2. **AdManager Parsing Tests (`test_tasks.py`)**:
   - Verify that `AdManager._parse_ad_segments` accurately parses JSON containing the new labels.
3. **Metrics Categorization Tests (`test_metrics.py`)**:
   - Verify that `categorize_segment_label` maps `network-promo`, `housekeeping`, `recap`, `preview`, and `interlude` into the correct dashboard bins.
   - Verify that `MetricsService.get_system_metrics()` handles episodes with these new labels seamlessly.
4. **Full Regression Test Suite**:
   - Run `/home/prmohan/projects/podslice/venv/bin/python manage.py test podcasts` (all 154+ tests pass).
   - Run pylint on modified files to preserve 10.00/10 quality.
