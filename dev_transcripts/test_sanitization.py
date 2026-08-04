#!/usr/bin/env python3
"""Mutation tests for transcript hostname sanitization."""

from __future__ import annotations

import unittest

from lib_transcript import scrub_internal_fqdns, scrub_internal_fqdns_tree
from render import render_day


class TranscriptSanitizationTest(unittest.TestCase):
    def test_internal_fqdns_become_short_names(self) -> None:
        text = (
            "run on devbig014.atn7.facebook.com and fetch from "
            "git.vip.facebook.com; keep github.com unchanged"
        )
        self.assertEqual(
            "run on devbig014 and fetch from git; keep github.com unchanged",
            scrub_internal_fqdns(text),
        )

    def test_nested_cache_and_legacy_render_are_scrubbed(self) -> None:
        doc = {
            "date": "2026-08-01",
            "weekday": "Sat",
            "meta": {
                "day_summary": "work moved to devbig030.atn3.facebook.com",
                "titles": {"main": "host migration"},
            },
            "turns": [
                {
                    "thread_key": "main",
                    "first_ms": 1785556800000,
                    "channel": "Web",
                    "prompt": "inspect devbig014.atn7.facebook.com",
                    "bucket": "paragraph",
                    "summary": "Measured devbig014.atn7.facebook.com.",
                }
            ],
        }

        scrubbed = scrub_internal_fqdns_tree(doc)
        self.assertNotIn(".facebook.com", str(scrubbed))
        self.assertIn("devbig030", scrubbed["meta"]["day_summary"])

        rendered = render_day(doc)
        self.assertNotIn(".facebook.com", rendered)
        self.assertIn("inspect devbig014", rendered)
        self.assertIn("Measured devbig014.", rendered)


if __name__ == "__main__":
    unittest.main()
