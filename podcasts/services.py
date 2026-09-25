import logging
from typing import Any, Dict
from podcasts.models import Episode

logger = logging.getLogger(__name__)


class MetricsService:
    """Service for calculating system-wide pipeline execution and AI resilience metrics."""

    @staticmethod
    def get_system_metrics() -> Dict[str, Any]:
        """
        Aggregates processing metrics across all episodes having telemetry data.

        Returns:
            Dict[str, Any] containing aggregated counts, percentages, and stage failures:
                - total_episodes_processed (int)
                - completed_episodes (int)
                - unresolved_failures (int)
                - clean_runs_count (int)
                - clean_runs_pct (float)
                - rate_limits_encountered (int)
                - retries_recovered (int)
                - retry_recovery_rate_pct (float)
                - fallback_model_used (int)
                - fallback_model_pct (float)
                - stage_failures (Dict[str, int]) with 'download', 'gemini', 'slicing'
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

        for ep in episodes:
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
            is_gemini_active = (
                bool(gemini_stage)
                and gemini_status not in ("skipped", "pending", None, "")
            ) or bool(gemini_stage.get("attempts")) or bool(gemini_stage.get("fallback_used")) or bool(gemini_stage.get("model_used"))

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
        }
