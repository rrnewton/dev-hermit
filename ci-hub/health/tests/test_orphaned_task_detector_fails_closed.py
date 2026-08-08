#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""`scripts/orphaned-task-detector.sh` must refuse an unreadable task graph.

The regression these lock: run bare, the detector used to read `tg`'s implicit
empty `tasks` default and print "scanned 0 ... ORPHANED: 0" at exit 0 — the same
verdict word and the same exit code as a full census that found nothing. A
detector that reports health because it could not see anything is worse than one
that does not run.

The database guard deliberately runs BEFORE the live-fleet read, so these cases
are exercisable without an orc socket: the refusal is decided before anything
report-shaped is printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DETECTOR = ROOT / "scripts" / "orphaned-task-detector.sh"

UNAVAILABLE_RC = 2


def run(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [str(DETECTOR)], cwd=ROOT, env=env, text=True, capture_output=True
    )


class OrphanDetectorFailsClosed(unittest.TestCase):
    def assert_refused(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, UNAVAILABLE_RC, result.stderr)
        self.assertIn("COULD NOT MEASURE", result.stderr)
        # The load-bearing half: it must not emit a clean-looking census.
        self.assertNotIn("ORPHANED", result.stdout)
        self.assertNotIn("scanned", result.stdout)

    def test_unbound_database_is_could_not_measure_not_zero_orphans(self) -> None:
        self.assert_refused(run({"TG_DB_PATH": None}))

    def test_blank_database_value_is_could_not_measure(self) -> None:
        self.assert_refused(run({"TG_DB_PATH": ""}))

    def test_missing_database_file_is_could_not_measure(self) -> None:
        missing = ROOT / "ignored" / "no-such-taskgraph-should-not-exist.db"
        self.assertFalse(missing.exists())
        self.assert_refused(run({"TG_DB_PATH": str(missing)}))

    def test_non_sqlite_bytes_are_could_not_measure(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            handle.write(b"not a database")
            junk = handle.name
        try:
            self.assert_refused(run({"TG_DB_PATH": junk}))
        finally:
            os.unlink(junk)

    def test_readable_but_ownerless_graph_stays_a_measured_zero(self) -> None:
        """Empty must NOT be swept into unverifiable — the whole point of the
        resolver validating SHAPE rather than contents. Without this the fix
        would over-correct and a genuinely quiet fleet would read as broken."""
        import tempfile

        source = os.environ.get("TG_DB_PATH")
        if not source or not Path(source).exists():
            self.skipTest("no bound TaskGraph to derive an ownerless copy from")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            empty = handle.name
        try:
            shutil.copyfile(source, empty)
            with sqlite3.connect(empty) as conn:
                conn.execute("UPDATE tasks SET owner=NULL")
            result = run({"TG_DB_PATH": empty})
            if result.returncode == 3:
                self.skipTest("live fleet unreadable here; guard order untested")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("scanned 0 owned non-terminal task(s)", result.stdout)
            self.assertIn("ORPHANED (owner not in live fleet): 0", result.stdout)
        finally:
            os.unlink(empty)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
