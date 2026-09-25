from django.test import TestCase
from podcasts.models import Podcast, Episode
from podcasts.services import MetricsService


class MetricsServiceTest(TestCase):
    def setUp(self):
        self.podcast = Podcast.objects.create(title="P", rss_url="http://e.com/rss")

    def test_get_system_metrics_empty_database(self):
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
