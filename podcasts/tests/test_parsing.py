from django.test import TestCase
from podcasts.models import Episode, Podcast
from podcasts.tasks import AdManager


class AdSegmentsParsingTest(TestCase):
    """
    Test suite for resilient JSON extraction and timestamp normalization
    in AdManager._parse_ad_segments.
    """

    def setUp(self):
        podcast = Podcast.objects.create(title="P", rss_url="http://e.com/rss")
        self.episode = Episode.objects.create(
            podcast=podcast,
            title="E",
            guid="g",
            pub_date="2026-09-24T00:00:00Z",
            original_audio_url="http://e.com/a.mp3",
        )
        self.mgr = AdManager(self.episode)

    def test_parse_direct_json_array(self):
        res = '[{"start": 10.5, "end": 20.0, "label": "ad"}]'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 10.5)

    def test_parse_markdown_with_preamble(self):
        res = 'Here is the analysis:\n```json\n[{"start": 5.0, "end": 15.0, "label": "ad"}]\n```'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 5.0)

    def test_parse_string_timestamps(self):
        res = '[{"start": "01:30.500", "end": "02:00.000", "label": "ad"}]'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 90.5)
        self.assertEqual(parsed[0]["end"], 120.0)

    def test_invalid_segments_discarded(self):
        res = '[{"start": 50.0, "end": 40.0}]'  # start >= end
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 0)

    def test_parse_hh_mm_ss_timestamps(self):
        res = '[{"start": "01:00:00", "end": "01:02:30.500", "label": "ad"}]'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 3600.0)
        self.assertEqual(parsed[0]["end"], 3750.5)

    def test_parse_code_block_without_json_tag(self):
        res = '```\n[{"start": 12.0, "end": 24.0, "label": "ad"}]\n```'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 12.0)
        self.assertEqual(parsed[0]["end"], 24.0)

    def test_parse_outermost_brackets_fallback(self):
        res = 'Preamble text [{"start": 15.0, "end": 30.0, "label": "sponsor"}] Postscript notes.'
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["start"], 15.0)
        self.assertEqual(parsed[0]["end"], 30.0)

    def test_mixed_valid_and_invalid_segments(self):
        res = (
            "[\n"
            '  {"start": 10.0, "end": 20.0, "label": "valid1"},\n'
            '  {"start": 50.0, "end": 40.0, "label": "inverted"},\n'
            '  {"start": "invalid", "end": 60.0, "label": "bad_time"},\n'
            '  {"start": 30.0, "end": 40.0, "label": "valid2"}\n'
            "]"
        )
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["start"], 10.0)
        self.assertEqual(parsed[0]["label"], "valid1")
        self.assertEqual(parsed[1]["start"], 30.0)
        self.assertEqual(parsed[1]["label"], "valid2")

    def test_empty_json_array(self):
        res = "[]"
        parsed = self.mgr._parse_ad_segments(res)
        self.assertEqual(parsed, [])
        self.episode.refresh_from_db()
        self.assertNotEqual(self.episode.status, Episode.Status.FAILED)
