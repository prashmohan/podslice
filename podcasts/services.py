"""
Services module for computing system metrics, duration formatting, and segment classifications.
"""
import json
import logging
from typing import Any, Dict
from podcasts.models import Episode

logger = logging.getLogger(__name__)


def format_duration_hm(seconds: float) -> str:
    """
    Formats seconds into human-readable duration:
      - >= 3600: "Xh Ym"
      - 60 <= seconds < 3600: "Xm Ys"
      - 0 < seconds < 60: "Xs"
      - <= 0 or None: "0m"
    """
    if seconds is None:
        return "0m"
    try:
        sec = float(seconds)
    except (ValueError, TypeError):
        return "0m"

    if sec <= 0:
        return "0m"

    if sec >= 3600:
        hours = int(sec // 3600)
        minutes = int((sec % 3600) // 60)
        return f"{hours}h {minutes}m"
    if sec >= 60:
        minutes = int(sec // 60)
        remaining_sec = int(sec % 60)
        return f"{minutes}m {remaining_sec}s"
    return f"{int(sec)}s"


def format_hours_decimal(seconds: float) -> str:
    """
    Formats seconds into decimal hours/minutes representation:
      - >= 3600: f"{round(seconds / 3600.0, 1)}h"
      - > 0: f"{round(seconds / 60.0, 1)}m"
      - <= 0 or None: "0h"
    """
    if seconds is None:
        return "0h"
    try:
        sec = float(seconds)
    except (ValueError, TypeError):
        return "0h"

    if sec <= 0:
        return "0h"

    if sec >= 3600:
        return f"{round(sec / 3600.0, 1)}h"
    return f"{round(sec / 60.0, 1)}m"


def categorize_segment_label(label: Any) -> str:
    """
    Normalizes segment labels into one of three categories:
      - intros_outros: 'intro', 'outro', 'music', 'theme'
      - ctas: 'cta', 'call to action', 'promo', 'promotion', 'off-topic', 'other'
      - ads: 'sponsor', 'ad', 'advertisement', empty, or any unrecognized category
    """
    if not label or not isinstance(label, str):
        return "ads"
    norm = label.strip().lower()
    if any(k in norm for k in ["intro", "outro", "music", "theme"]):
        return "intros_outros"
    if any(k in norm for k in ["cta", "call to action", "promo", "promotion", "off-topic", "other"]):
        return "ctas"
    return "ads"


class MetricsService:  # pylint: disable=too-few-public-methods
    """Service for calculating system-wide pipeline execution and AI resilience metrics."""

    @staticmethod
    def get_system_metrics() -> Dict[str, Any]:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks
        """
        Aggregates processing metrics across all episodes having telemetry data.

        Returns:
            Dict[str, Any] containing aggregated counts, percentages, stage failures,
            time saved telemetry, and API key distribution.
        """
        episodes = Episode.objects.exclude(processing_metrics=None)

        total_episodes_processed = 0
        completed_episodes = 0
        unresolved_failures = 0
        clean_runs_count = 0
        rate_limits_encountered = 0
        retries_recovered = 0
        fallback_model_used = 0
        total_with_gemini = 0

        stage_failures: Dict[str, int] = {
            "download": 0,
            "gemini": 0,
            "slicing": 0,
        }

        # Time saved & segment metrics
        total_time_saved_sec = 0.0
        total_segments_removed = 0
        segment_types: Dict[str, int] = {
            "ads": 0,
            "intros_outros": 0,
            "ctas": 0,
        }
        total_content_sec = 0.0
        total_audio_rehosted_sec = 0.0
        total_audio_analyzed_sec = 0.0

        # API key tracking: key_masked -> {total_calls, rate_limits, success_count}
        keys_data: Dict[str, Dict[str, Any]] = {}

        for ep in episodes:  # pylint: disable=too-many-nested-blocks
            metrics = ep.processing_metrics
            if not isinstance(metrics, dict) or not metrics:
                continue

            total_episodes_processed += 1

            status = ep.status or metrics.get("status")
            if status == Episode.Status.COMPLETE:
                completed_episodes += 1
            elif status == Episode.Status.FAILED:
                unresolved_failures += 1

            stages = metrics.get("stages") or {}
            download_stage = stages.get("download") or {}
            gemini_stage = stages.get("gemini") or {}
            slicing_stage = stages.get("slicing") or {}

            # Stage failures count
            download_failed = (
                download_stage.get("status") == "failed"
                or bool(download_stage.get("error"))
            )
            if download_failed:
                stage_failures["download"] += 1

            gemini_failed = gemini_stage.get("status") == "failed"
            if gemini_failed:
                stage_failures["gemini"] += 1

            slicing_failed = (
                slicing_stage.get("status") == "failed"
                or bool(slicing_stage.get("error"))
            )
            if slicing_failed:
                stage_failures["slicing"] += 1

            # AI processing tracking
            gemini_status = gemini_stage.get("status")
            has_gemini_data = (
                bool(gemini_stage.get("attempts"))
                or bool(gemini_stage.get("fallback_used"))
                or bool(gemini_stage.get("model_used"))
            )
            is_gemini_active = (
                bool(gemini_stage)
                and gemini_status not in ("skipped", "pending", None, "")
            ) or has_gemini_data

            if is_gemini_active:
                total_with_gemini += 1

            # 429 Rate limits encountered
            attempts = gemini_stage.get("attempts") or []
            had_429_in_attempts = any(
                str(att.get("status")) == "429" or att.get("status") == 429
                for att in attempts
                if isinstance(att, dict)
            )
            retry_attempts = gemini_stage.get("retry_attempts", 0) or 0
            is_retry_recovered = bool(gemini_stage.get("retry_recovered"))

            had_rate_limit = (
                retry_attempts > 0
                or is_retry_recovered
                or had_429_in_attempts
            )
            if had_rate_limit:
                rate_limits_encountered += 1

            # Retry recovery count
            if is_retry_recovered:
                retries_recovered += 1

            # Fallback model tracking
            is_fallback_used = bool(gemini_stage.get("fallback_used"))
            if is_fallback_used:
                fallback_model_used += 1

            # Clean run: completed without retries, errors, or fallback
            has_unresolved_error = bool(metrics.get("unresolved_error"))
            has_stage_error = (
                download_failed
                or gemini_failed
                or bool(gemini_stage.get("error"))
                or slicing_failed
            )
            is_clean = (
                status == Episode.Status.COMPLETE
                and not has_unresolved_error
                and not has_stage_error
                and not had_rate_limit
                and not is_fallback_used
                and retry_attempts == 0
            )
            if is_clean:
                clean_runs_count += 1

            # Time saved & ad segments (calculated for completed episodes)
            ep_time_saved = 0.0
            if status == Episode.Status.COMPLETE:
                raw_ad_segments = ep.ad_segments
                segments = []
                if isinstance(raw_ad_segments, str):
                    try:
                        parsed = json.loads(raw_ad_segments)
                        if isinstance(parsed, list):
                            segments = parsed
                    except (json.JSONDecodeError, ValueError, TypeError):
                        segments = []
                elif isinstance(raw_ad_segments, list):
                    segments = raw_ad_segments

                if not segments:
                    slicing_segs = slicing_stage.get("ad_segments")
                    if isinstance(slicing_segs, list):
                        segments = slicing_segs

                if slicing_stage.get("removed_duration_sec") is not None:
                    try:
                        ep_time_saved = float(slicing_stage.get("removed_duration_sec"))
                    except (ValueError, TypeError):
                        ep_time_saved = 0.0

                if ep_time_saved == 0.0 and segments:
                    for seg in segments:
                        if isinstance(seg, dict):
                            try:
                                start = float(seg.get("start", 0.0) or 0.0)
                                end = float(seg.get("end", 0.0) or 0.0)
                                if end > start:
                                    ep_time_saved += (end - start)
                            except (ValueError, TypeError):
                                pass

                total_time_saved_sec += ep_time_saved

                if segments:
                    for seg in segments:
                        if isinstance(seg, dict):
                            total_segments_removed += 1
                            cat = categorize_segment_label(seg.get("label"))
                            segment_types[cat] += 1
                elif slicing_stage.get("ads_detected"):
                    try:
                        detected_count = int(slicing_stage.get("ads_detected"))
                        if detected_count > 0:
                            total_segments_removed += detected_count
                            segment_types["ads"] += detected_count
                    except (ValueError, TypeError):
                        pass

                if ep.duration_seconds is not None:
                    try:
                        dur = float(ep.duration_seconds)
                        total_content_sec += dur
                        total_audio_rehosted_sec += dur
                    except (ValueError, TypeError):
                        pass

            # Audio analyzed duration
            ep_audio_analyzed = 0.0
            if is_gemini_active:
                if gemini_stage.get("audio_duration_sec") is not None:
                    try:
                        ep_audio_analyzed = float(gemini_stage.get("audio_duration_sec"))
                    except (ValueError, TypeError):
                        ep_audio_analyzed = 0.0
                elif ep.duration_seconds is not None or (status == Episode.Status.COMPLETE and ep_time_saved > 0):
                    completed_savings = ep_time_saved if status == Episode.Status.COMPLETE else 0.0
                    ep_audio_analyzed = float(ep.duration_seconds or 0.0) + completed_savings
            total_audio_analyzed_sec += ep_audio_analyzed

            # API Key Distribution tracking
            for att in attempts:
                if isinstance(att, dict):
                    key_masked = att.get("key_masked")
                    if key_masked:
                        if key_masked not in keys_data:
                            keys_data[key_masked] = {
                                "key_masked": key_masked,
                                "total_calls": 0,
                                "rate_limits": 0,
                                "success_count": 0,
                            }
                        keys_data[key_masked]["total_calls"] += 1
                        st = att.get("status")
                        if str(st) == "429" or st == 429:
                            keys_data[key_masked]["rate_limits"] += 1
                        elif st == "success":
                            keys_data[key_masked]["success_count"] += 1

        # Calculate percentages safely guarding against division by zero
        if total_episodes_processed > 0:
            clean_runs_pct = round((clean_runs_count / total_episodes_processed) * 100.0, 1)
        else:
            clean_runs_pct = 100.0

        if rate_limits_encountered > 0:
            retry_recovery_rate_pct = round((retries_recovered / rate_limits_encountered) * 100.0, 1)
        else:
            retry_recovery_rate_pct = 100.0

        if total_with_gemini > 0:
            fallback_model_pct = round((fallback_model_used / total_with_gemini) * 100.0, 1)
        else:
            fallback_model_pct = 0.0

        total_time_saved_sec = round(total_time_saved_sec, 2)
        total_time_saved_formatted = format_duration_hm(total_time_saved_sec)

        total_audio_analyzed_sec = round(total_audio_analyzed_sec, 2)
        total_audio_analyzed_formatted = format_hours_decimal(total_audio_analyzed_sec)

        total_audio_rehosted_sec = round(total_audio_rehosted_sec, 2)
        total_audio_rehosted_formatted = format_hours_decimal(total_audio_rehosted_sec)

        if total_segments_removed > 0:
            segment_percentages = {
                "ads": round((segment_types["ads"] / total_segments_removed) * 100.0, 1),
                "intros_outros": round((segment_types["intros_outros"] / total_segments_removed) * 100.0, 1),
                "ctas": round((segment_types["ctas"] / total_segments_removed) * 100.0, 1),
            }
        else:
            segment_percentages = {
                "ads": 0.0,
                "intros_outros": 0.0,
                "ctas": 0.0,
            }

        total_original_audio = total_content_sec + total_time_saved_sec
        if total_original_audio > 0:
            ad_load_pct = round((total_time_saved_sec / total_original_audio) * 100.0, 1)
        else:
            ad_load_pct = 0.0

        total_key_calls = sum(k["total_calls"] for k in keys_data.values())
        api_keys_list = []
        for key_info in sorted(keys_data.values(), key=lambda x: (-x["total_calls"], x["key_masked"])):
            call_pct = (
                round((key_info["total_calls"] / total_key_calls) * 100.0, 1)
                if total_key_calls > 0
                else 0.0
            )
            api_keys_list.append({
                "key_masked": key_info["key_masked"],
                "total_calls": key_info["total_calls"],
                "call_pct": call_pct,
                "rate_limits": key_info["rate_limits"],
                "success_count": key_info["success_count"],
            })
        active_keys_count = len(api_keys_list)

        return {
            "total_episodes_processed": total_episodes_processed,
            "completed_episodes": completed_episodes,
            "unresolved_failures": unresolved_failures,
            "clean_runs_count": clean_runs_count,
            "clean_runs_pct": clean_runs_pct,
            "rate_limits_encountered": rate_limits_encountered,
            "retries_recovered": retries_recovered,
            "retry_recovery_rate_pct": retry_recovery_rate_pct,
            "fallback_model_used": fallback_model_used,
            "fallback_model_pct": fallback_model_pct,
            "stage_failures": stage_failures,
            "total_time_saved_sec": total_time_saved_sec,
            "total_time_saved_formatted": total_time_saved_formatted,
            "total_segments_removed": total_segments_removed,
            "ad_load_pct": ad_load_pct,
            "segment_types": segment_types,
            "segment_percentages": segment_percentages,
            "api_keys": api_keys_list,
            "active_keys_count": active_keys_count,
            "total_audio_analyzed_sec": total_audio_analyzed_sec,
            "total_audio_analyzed_formatted": total_audio_analyzed_formatted,
            "total_audio_rehosted_sec": total_audio_rehosted_sec,
            "total_audio_rehosted_formatted": total_audio_rehosted_formatted,
        }
