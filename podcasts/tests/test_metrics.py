"""
Unit tests for MetricsService and duration helper functions.
"""
import json
from django.test import TestCase, override_settings
from podcasts.models import Podcast, Episode
from podcasts.services import (
    MetricsService,
    format_duration_hm,
    format_hours_decimal,
    categorize_segment_label,
    mask_api_key,
)


class MetricsServiceTest(TestCase):
    """Test suite for MetricsService aggregation and formatting helpers."""

    def setUp(self):
        self.podcast = Podcast.objects.create(title="P", rss_url="http://e.com/rss")

    def test_get_system_metrics_empty_database(self):
        """Verify get_system_metrics returns default zeros when database is empty."""
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 0)
        self.assertEqual(metrics["completed_episodes"], 0)
        self.assertEqual(metrics["unresolved_failures"], 0)
        self.assertEqual(metrics["clean_runs_count"], 0)
        self.assertEqual(metrics["clean_runs_pct"], 100.0)
        self.assertEqual(metrics["rate_limits_encountered"], 0)
        self.assertEqual(metrics["retries_recovered"], 0)
        self.assertEqual(metrics["retry_recovery_rate_pct"], 100.0)
        self.assertEqual(metrics["fallback_model_used"], 0)
        self.assertEqual(metrics["fallback_model_pct"], 0.0)
        self.assertEqual(
            metrics["stage_failures"],
            {"download": 0, "gemini": 0, "slicing": 0},
        )

    def test_get_system_metrics_with_telemetry_data(self):
        """Verify get_system_metrics correctly aggregates metrics across episodes."""
        # Episode 1: Clean run on primary model
        Episode.objects.create(
            podcast=self.podcast,
            title="E1",
            guid="g1",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/1.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "retry_attempts": 0,
                        "fallback_used": False,
                    },
                    "slicing": {"status": "success"},
                },
            },
        )
        # Episode 2: 429 Recovered
        Episode.objects.create(
            podcast=self.podcast,
            title="E2",
            guid="g2",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/2.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "retry_attempts": 1,
                        "retry_recovered": True,
                        "fallback_used": False,
                    },
                    "slicing": {"status": "success"},
                },
            },
        )
        # Episode 3: Fallback used
        Episode.objects.create(
            podcast=self.podcast,
            title="E3",
            guid="g3",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/3.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "fallback_success",
                        "retry_attempts": 1,
                        "retry_recovered": False,
                        "fallback_used": True,
                    },
                    "slicing": {"status": "success"},
                },
            },
        )
        # Episode 4: Failed at download
        Episode.objects.create(
            podcast=self.podcast,
            title="E4",
            guid="g4",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/4.mp3",
            status=Episode.Status.FAILED,
            processing_metrics={
                "status": "FAILED",
                "stages": {
                    "download": {"status": "failed", "error": "HTTP 404"},
                    "gemini": {"status": "skipped"},
                    "slicing": {"status": "skipped"},
                },
                "unresolved_error": "HTTP 404",
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 4)
        self.assertEqual(metrics["completed_episodes"], 3)
        self.assertEqual(metrics["unresolved_failures"], 1)
        self.assertEqual(metrics["clean_runs_count"], 1)
        self.assertEqual(metrics["clean_runs_pct"], 25.0)
        self.assertEqual(metrics["rate_limits_encountered"], 2)
        self.assertEqual(metrics["retries_recovered"], 1)
        self.assertEqual(metrics["retry_recovery_rate_pct"], 50.0)
        self.assertEqual(metrics["fallback_model_used"], 1)
        self.assertAlmostEqual(metrics["fallback_model_pct"], 33.3, places=1)
        self.assertEqual(metrics["stage_failures"]["download"], 1)
        self.assertEqual(metrics["stage_failures"]["gemini"], 0)
        self.assertEqual(metrics["stage_failures"]["slicing"], 0)

    def test_get_system_metrics_gemini_and_slicing_failures(self):
        """Verify stage failure counters for gemini and slicing errors."""
        # Episode failed at gemini stage
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Gemini_Fail",
            guid="g_gemini_fail",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/fail1.mp3",
            status=Episode.Status.FAILED,
            processing_metrics={
                "status": "FAILED",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "failed", "error": "All API keys quota exhausted"},
                    "slicing": {"status": "pending"},
                },
                "unresolved_error": "All API keys quota exhausted",
            },
        )
        # Episode failed at slicing stage
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Slicing_Fail",
            guid="g_slicing_fail",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/fail2.mp3",
            status=Episode.Status.FAILED,
            processing_metrics={
                "status": "FAILED",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success", "retry_attempts": 0, "fallback_used": False},
                    "slicing": {"status": "failed", "error": "ffmpeg non-zero exit code 1"},
                },
                "unresolved_error": "ffmpeg non-zero exit code 1",
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 2)
        self.assertEqual(metrics["unresolved_failures"], 2)
        self.assertEqual(metrics["stage_failures"]["gemini"], 1)
        self.assertEqual(metrics["stage_failures"]["slicing"], 1)
        self.assertEqual(metrics["stage_failures"]["download"], 0)
        self.assertEqual(metrics["clean_runs_count"], 0)
        self.assertEqual(metrics["clean_runs_pct"], 0.0)

    def test_get_system_metrics_ignores_episodes_without_processing_metrics(self):
        """Verify episodes without processing metrics are ignored by aggregator."""
        # Episode without processing metrics
        Episode.objects.create(
            podcast=self.podcast,
            title="Unprocessed Episode",
            guid="g_unprocessed",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/unprocessed.mp3",
            status=Episode.Status.NEW,
            processing_metrics=None,
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 0)

    def test_get_system_metrics_rate_limit_from_attempts_log(self):
        """Verify rate limits are detected from gemini attempts telemetry."""
        # Episode where retry was logged in attempts array
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Attempt_429",
            guid="g_attempt_429",
            pub_date="2026-09-25T00:00:00Z",
            original_audio_url="http://e.com/att429.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "retry_attempts": 0,
                        "retry_recovered": True,
                        "fallback_used": False,
                        "attempts": [
                            {"model": "gemini-3.8-flash", "status": 429, "error": "ResourceExhausted"},
                            {"model": "gemini-3.8-flash", "status": "success"},
                        ],
                    },
                    "slicing": {"status": "original_preserved"},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_episodes_processed"], 1)
        self.assertEqual(metrics["rate_limits_encountered"], 1)
        self.assertEqual(metrics["retries_recovered"], 1)
        self.assertEqual(metrics["retry_recovery_rate_pct"], 100.0)
        self.assertEqual(metrics["clean_runs_count"], 0)  # Had a rate limit retry, not clean

    @override_settings(GEMINI_API_KEYS=[], GEMINI_API_KEY="")
    def test_get_system_metrics_empty_database_includes_new_keys(self):
        """Verify empty database returns initialized new time saved and api keys telemetry."""
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_time_saved_sec"], 0.0)
        self.assertEqual(metrics["total_time_saved_formatted"], "0m")
        self.assertEqual(metrics["total_segments_removed"], 0)
        self.assertEqual(metrics["ad_load_pct"], 0.0)
        self.assertEqual(metrics["segment_types"], {"ads": 0, "intros_outros": 0, "ctas": 0})
        self.assertEqual(metrics["segment_percentages"], {"ads": 0.0, "intros_outros": 0.0, "ctas": 0.0})
        self.assertEqual(metrics["api_keys"], [])
        self.assertEqual(metrics["active_keys_count"], 0)
        self.assertEqual(metrics["configured_keys_count"], 0)
        self.assertEqual(metrics["total_audio_analyzed_sec"], 0.0)
        self.assertEqual(metrics["total_audio_analyzed_formatted"], "0h")
        self.assertEqual(metrics["total_audio_rehosted_sec"], 0.0)
        self.assertEqual(metrics["total_audio_rehosted_formatted"], "0h")

    def test_get_system_metrics_time_saved_and_segments_breakdown(self):
        """Verify time saved, segment category counters, and ad load percentage."""
        # Episode 1: Completed with sponsor ad and intro
        Episode.objects.create(
            podcast=self.podcast,
            title="E_TimeSaved_1",
            guid="g_ts1",
            pub_date="2026-10-01T00:00:00Z",
            original_audio_url="http://e.com/ts1.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=3000,
            ad_segments=[
                {"start": 0.0, "end": 30.0, "label": "intro"},
                {"start": 600.0, "end": 690.0, "label": "sponsor"},
            ],
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success"},
                    "slicing": {"status": "success", "removed_duration_sec": 120.0},
                },
            },
        )
        # Episode 2: Completed with ad and cta
        Episode.objects.create(
            podcast=self.podcast,
            title="E_TimeSaved_2",
            guid="g_ts2",
            pub_date="2026-10-02T00:00:00Z",
            original_audio_url="http://e.com/ts2.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=1800,
            ad_segments=[
                {"start": 500.0, "end": 560.0, "label": "ad"},
                {"start": 1200.0, "end": 1220.0, "label": "cta"},
            ],
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success"},
                    "slicing": {"status": "success", "removed_duration_sec": 80.0},
                },
            },
        )
        # Episode 3: Failed episode (must NOT be counted in time saved or removed segments)
        Episode.objects.create(
            podcast=self.podcast,
            title="E_TimeSaved_Failed",
            guid="g_ts_fail",
            pub_date="2026-10-03T00:00:00Z",
            original_audio_url="http://e.com/tsfail.mp3",
            status=Episode.Status.FAILED,
            duration_seconds=None,
            ad_segments=[
                {"start": 0.0, "end": 50.0, "label": "sponsor"},
            ],
            processing_metrics={
                "status": "FAILED",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "failed", "error": "rate limit"},
                    "slicing": {"status": "pending"},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_time_saved_sec"], 200.0)
        self.assertEqual(metrics["total_time_saved_formatted"], "3m 20s")
        self.assertEqual(metrics["total_segments_removed"], 4)
        self.assertEqual(metrics["segment_types"], {"ads": 2, "intros_outros": 1, "ctas": 1})
        self.assertEqual(metrics["segment_percentages"], {"ads": 50.0, "intros_outros": 25.0, "ctas": 25.0})
        # ad_load_pct: (200 / (4800 + 200)) * 100 = 4.0%
        self.assertEqual(metrics["ad_load_pct"], 4.0)
        self.assertEqual(metrics["total_audio_rehosted_sec"], 4800.0)
        self.assertEqual(metrics["total_audio_rehosted_formatted"], "1.3h")

    def test_get_system_metrics_handles_json_string_ad_segments(self):
        """Verify get_system_metrics parses ad_segments when stored as raw JSON string."""
        Episode.objects.create(
            podcast=self.podcast,
            title="E_JSON_String",
            guid="g_json_str",
            pub_date="2026-10-01T00:00:00Z",
            original_audio_url="http://e.com/json.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=560,
            ad_segments=json.dumps([{"start": 10.0, "end": 40.0, "label": "promo"}]),
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success"},
                    "slicing": {"status": "success", "removed_duration_sec": 30.0},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_segments_removed"], 1)
        self.assertEqual(metrics["total_time_saved_sec"], 30.0)
        self.assertEqual(metrics["total_time_saved_formatted"], "30s")
        self.assertEqual(metrics["segment_types"]["ctas"], 1)
        self.assertEqual(metrics["segment_percentages"]["ctas"], 100.0)
        # 30 / (560 + 30) * 100 = 5.0847 -> 5.1%
        self.assertEqual(metrics["ad_load_pct"], 5.1)

    def test_get_system_metrics_api_key_distribution_and_429s(self):
        """Verify API key quota distribution, per-key 429 rate limits, and audio analyzed totals."""
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Keys_1",
            guid="g_keys_1",
            pub_date="2026-10-01T00:00:00Z",
            original_audio_url="http://e.com/k1.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=3600,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "audio_duration_sec": 3900.0,
                        "attempts": [
                            {
                                "model": "gemini-3.8-flash",
                                "key_masked": "AIzaSy...AAA",
                                "status": 429,
                                "error": "ResourceExhausted",
                            },
                            {
                                "model": "gemini-3.8-flash",
                                "key_masked": "AIzaSy...BBB",
                                "status": "success",
                            },
                        ],
                    },
                    "slicing": {"status": "success", "removed_duration_sec": 300.0},
                },
            },
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Keys_2",
            guid="g_keys_2",
            pub_date="2026-10-02T00:00:00Z",
            original_audio_url="http://e.com/k2.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=7200,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "audio_duration_sec": 7500.0,
                        "attempts": [
                            {"model": "gemini-3.8-flash", "key_masked": "AIzaSy...BBB", "status": "success"},
                            {"model": "gemini-3.8-flash", "key_masked": "AIzaSy...BBB", "status": "success"},
                        ],
                    },
                    "slicing": {"status": "success", "removed_duration_sec": 300.0},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["active_keys_count"], 2)
        # Sorted descending by total_calls: BBB (3 calls), AAA (1 call)
        self.assertEqual(metrics["api_keys"][0]["key_masked"], "AIzaSy...BBB")
        self.assertEqual(metrics["api_keys"][0]["total_calls"], 3)
        self.assertEqual(metrics["api_keys"][0]["call_pct"], 75.0)
        self.assertEqual(metrics["api_keys"][0]["rate_limits"], 0)
        self.assertEqual(metrics["api_keys"][0]["success_count"], 3)

        self.assertEqual(metrics["api_keys"][1]["key_masked"], "AIzaSy...AAA")
        self.assertEqual(metrics["api_keys"][1]["total_calls"], 1)
        self.assertEqual(metrics["api_keys"][1]["call_pct"], 25.0)
        self.assertEqual(metrics["api_keys"][1]["rate_limits"], 1)
        self.assertEqual(metrics["api_keys"][1]["success_count"], 0)

        self.assertEqual(metrics["total_audio_analyzed_sec"], 11400.0)
        self.assertEqual(metrics["total_audio_analyzed_formatted"], "3.2h")

    def test_format_duration_helpers(self):
        """Verify format_duration_hm, format_hours_decimal, and categorize_segment_label."""
        self.assertEqual(format_duration_hm(0), "0m")
        self.assertEqual(format_duration_hm(None), "0m")
        self.assertEqual(format_duration_hm(-10), "0m")
        self.assertEqual(format_duration_hm(45), "45s")
        self.assertEqual(format_duration_hm(1510), "25m 10s")
        self.assertEqual(format_duration_hm(15480), "4h 18m")

        self.assertEqual(format_hours_decimal(0), "0h")
        self.assertEqual(format_hours_decimal(None), "0h")
        self.assertEqual(format_hours_decimal(-5), "0h")
        self.assertEqual(format_hours_decimal(150), "2.5m")
        self.assertEqual(format_hours_decimal(116640), "32.4h")
        self.assertEqual(format_hours_decimal(101160), "28.1h")

        self.assertEqual(categorize_segment_label("sponsor"), "ads")
        self.assertEqual(categorize_segment_label("ad"), "ads")
        self.assertEqual(categorize_segment_label("advertisement"), "ads")
        self.assertEqual(categorize_segment_label("intro"), "intros_outros")
        self.assertEqual(categorize_segment_label("outro"), "intros_outros")
        self.assertEqual(categorize_segment_label("theme"), "intros_outros")
        self.assertEqual(categorize_segment_label("music"), "intros_outros")
        self.assertEqual(categorize_segment_label("cta"), "ctas")
        self.assertEqual(categorize_segment_label("call to action"), "ctas")
        self.assertEqual(categorize_segment_label("promo"), "ctas")
        self.assertEqual(categorize_segment_label("promotion"), "ctas")
        self.assertEqual(categorize_segment_label("off-topic"), "ctas")
        self.assertEqual(categorize_segment_label("other"), "ctas")
        self.assertEqual(categorize_segment_label(""), "ads")
        self.assertEqual(categorize_segment_label(None), "ads")

    def test_categorize_segment_label_new_categories(self):
        """Verify expanded segment labels map to correct categories."""
        # ads
        for label in ["network-promo", "network promo", "cross-promo", "cross promo", "trailer", "merch"]:
            self.assertEqual(categorize_segment_label(label), "ads")
        # intros_outros
        for label in ["recap", "preview", "teaser", "interlude", "silence"]:
            self.assertEqual(categorize_segment_label(label), "intros_outros")
        # ctas
        for label in [
            "housekeeping", "tour", "live-show", "live show", "patreon", "membership", "off-topic", "off topic"
        ]:
            self.assertEqual(categorize_segment_label(label), "ctas")

    def test_get_system_metrics_aggregates_expanded_categories(self):
        """Verify get_system_metrics aggregates counts and percentages for expanded categories."""
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Expanded_Categories",
            guid="g_expanded_categories",
            pub_date="2026-10-15T00:00:00Z",
            original_audio_url="http://e.com/expanded.mp3",
            status=Episode.Status.COMPLETE,
            duration_seconds=3600,
            ad_segments=[
                {"start": 0.0, "end": 60.0, "label": "recap"},
                {"start": 600.0, "end": 660.0, "label": "network-promo"},
                {"start": 1800.0, "end": 1860.0, "label": "housekeeping"},
                {"start": 3500.0, "end": 3600.0, "label": "outro"},
            ],
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {"status": "success"},
                    "slicing": {"status": "success", "removed_duration_sec": 280.0},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["total_segments_removed"], 4)
        self.assertEqual(
            metrics["segment_types"],
            {"ads": 1, "intros_outros": 2, "ctas": 1},
        )
        self.assertEqual(
            metrics["segment_percentages"],
            {"ads": 25.0, "intros_outros": 50.0, "ctas": 25.0},
        )

    def test_mask_api_key_helper(self):
        """Verify mask_api_key handles None, empty, already masked, short, and long keys."""
        self.assertEqual(mask_api_key(None), "")
        self.assertEqual(mask_api_key(""), "")
        self.assertEqual(mask_api_key("   "), "")
        self.assertEqual(mask_api_key("key1"), "key1")
        self.assertEqual(mask_api_key("abcdef"), "abcdef")
        self.assertEqual(mask_api_key("abcdefg"), "abcdef...")
        self.assertEqual(mask_api_key("1234567890"), "123456...")
        self.assertEqual(
            mask_api_key("AIzaSyBmFRaUdx8RodHMc4D03DYlsImXapS93Ds"),
            "AIzaSy...93Ds",
        )
        self.assertEqual(mask_api_key("AIzaSy...AAA"), "AIzaSy...AAA")
        self.assertEqual(mask_api_key("...KEY_ALPHA"), "...KEY_ALPHA")

    @override_settings(GEMINI_API_KEYS=["key1", "key2", "key3"], GEMINI_API_KEY="")
    def test_get_system_metrics_with_configured_standby_keys(self):
        """Verify configured keys appear as standby keys with 0 calls when no telemetry exists."""
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["configured_keys_count"], 3)
        self.assertEqual(metrics["active_keys_count"], 0)
        self.assertEqual(len(metrics["api_keys"]), 3)
        self.assertEqual(
            [k["key_masked"] for k in metrics["api_keys"]],
            ["key1", "key2", "key3"],
        )
        for key_info in metrics["api_keys"]:
            self.assertEqual(key_info["total_calls"], 0)
            self.assertEqual(key_info["call_pct"], 0.0)
            self.assertEqual(key_info["rate_limits"], 0)
            self.assertEqual(key_info["success_count"], 0)
            self.assertFalse(key_info["is_active"])

    @override_settings(GEMINI_API_KEYS=["key1", "key2", "key3"], GEMINI_API_KEY="")
    def test_get_system_metrics_active_and_standby_keys_sorting(self):
        """Verify active keys sort first by total calls, followed by standby keys in config order."""
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Active_Key1",
            guid="g_act_k1",
            pub_date="2026-10-16T00:00:00Z",
            original_audio_url="http://e.com/act1.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "attempts": [
                            {"model": "gemini-3.8-flash", "key_masked": "key1", "status": "success"},
                            {"model": "gemini-3.8-flash", "key_masked": "key1", "status": "success"},
                        ],
                    },
                    "slicing": {"status": "success"},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["configured_keys_count"], 3)
        self.assertEqual(metrics["active_keys_count"], 1)
        self.assertEqual(len(metrics["api_keys"]), 3)

        # Active key first
        self.assertEqual(metrics["api_keys"][0]["key_masked"], "key1")
        self.assertTrue(metrics["api_keys"][0]["is_active"])
        self.assertEqual(metrics["api_keys"][0]["total_calls"], 2)
        self.assertEqual(metrics["api_keys"][0]["call_pct"], 100.0)
        self.assertEqual(metrics["api_keys"][0]["success_count"], 2)

        # Standby keys next in configured order
        self.assertEqual(metrics["api_keys"][1]["key_masked"], "key2")
        self.assertFalse(metrics["api_keys"][1]["is_active"])
        self.assertEqual(metrics["api_keys"][1]["total_calls"], 0)
        self.assertEqual(metrics["api_keys"][1]["call_pct"], 0.0)

        self.assertEqual(metrics["api_keys"][2]["key_masked"], "key3")
        self.assertFalse(metrics["api_keys"][2]["is_active"])
        self.assertEqual(metrics["api_keys"][2]["total_calls"], 0)
        self.assertEqual(metrics["api_keys"][2]["call_pct"], 0.0)

    @override_settings(GEMINI_API_KEYS=[], GEMINI_API_KEY="legacy-single-key")
    def test_get_system_metrics_fallback_to_single_gemini_api_key(self):
        """Verify fallback to GEMINI_API_KEY when GEMINI_API_KEYS is empty."""
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["configured_keys_count"], 1)
        self.assertEqual(metrics["active_keys_count"], 0)
        self.assertEqual(len(metrics["api_keys"]), 1)
        self.assertEqual(metrics["api_keys"][0]["key_masked"], "legacy...-key")
        self.assertFalse(metrics["api_keys"][0]["is_active"])

    @override_settings(GEMINI_API_KEYS=[], GEMINI_API_KEY="")
    def test_get_system_metrics_unconfigured_active_keys_fallback(self):
        """Verify unconfigured historical keys are active and configured_keys_count falls back."""
        Episode.objects.create(
            podcast=self.podcast,
            title="E_Historical",
            guid="g_hist",
            pub_date="2026-10-16T00:00:00Z",
            original_audio_url="http://e.com/hist.mp3",
            status=Episode.Status.COMPLETE,
            processing_metrics={
                "status": "COMPLETE",
                "stages": {
                    "download": {"status": "success"},
                    "gemini": {
                        "status": "success",
                        "attempts": [
                            {"model": "gemini-3.8-flash", "key_masked": "historical-key", "status": "success"},
                        ],
                    },
                    "slicing": {"status": "success"},
                },
            },
        )

        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["active_keys_count"], 1)
        self.assertEqual(metrics["configured_keys_count"], 1)
        self.assertEqual(len(metrics["api_keys"]), 1)
        self.assertEqual(metrics["api_keys"][0]["key_masked"], "historical-key")
        self.assertTrue(metrics["api_keys"][0]["is_active"])

    @override_settings(GEMINI_API_KEYS=["key1", "key1", "key2"], GEMINI_API_KEY="")
    def test_get_system_metrics_duplicate_configured_keys_deduplicated(self):
        """Verify duplicate configured keys in settings are deduplicated."""
        metrics = MetricsService.get_system_metrics()
        self.assertEqual(metrics["configured_keys_count"], 2)
        self.assertEqual(len(metrics["api_keys"]), 2)
        self.assertEqual([k["key_masked"] for k in metrics["api_keys"]], ["key1", "key2"])
