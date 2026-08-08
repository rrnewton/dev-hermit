#!/usr/bin/env python3
"""Bracket the incremental validate-run importer, with the absent/null/zero
distinction as the property under test.

THE FAILURE THIS GUARDS AGAINST is not "the import did not run". It is an import
that runs and quietly makes the record WORSE: coercing a never-measured field
into a zero turns a visible gap into an invisible false record, and
`concurrent_validates` is the field certification condition 4 turns on. So every
test below states which of the three states it plants and which it expects back:

    key absent    -> the field was never measured
    explicit null -> recorded, explicitly unknown
    explicit 0    -> measured, and genuinely zero

Both collapses are lossy. Asserting only "absent does not become zero" would let
the opposite bug through, so the real explicit zero is asserted to SURVIVE as a
zero too -- it is the only row in the live corpus that proves an uncontended
validate was ever observed, and losing it would be as bad as inventing one.

The other three properties are the ones whose failure would be silent rather than
loud: id continuation (a restarted id REPLACES published history, because
`union_events` deduplicates by `event_id`), append-only, and idempotence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER_DIR = HERE.parent
REPO = LEDGER_DIR.parents[1]
IMPORTER = LEDGER_DIR / "import_validate_runs.py"

sys.path.insert(0, str(LEDGER_DIR))
from ledger import read_shard, replay_legacy  # noqa: E402

# check-portable-paths.sh exempts the generic /home/test fixture home;
# any other literal owner home would fail the portability gate.
WORKSPACE = "/home/test/work/dev-hermit"


def row(commit: str, finished: str, **extra) -> dict:
    """A minimal legacy validate row. `concurrent_validates` is supplied ONLY via
    **extra, so a test that omits it plants a genuinely absent key rather than a
    None that would beg the question."""
    base = {
        # A generic host, NOT this box's real one: `check-portability` rejects a
        # machine-specific hostname even inside a fixture and even inside a
        # comment, so do not name the real one here either. It must stay in step
        # with the shard path below, per the host-path-mismatch note there.
        "host": "testhost001",
        "commit": commit,
        "started_at": finished.replace("T0", "T0"),
        "finished_at": finished,
        "result": "pass",
        "cwd": f"{WORKSPACE}/hermit",
    }
    base.update(extra)
    return base


class ImportValidateRuns(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ledger-import-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # The linter validates team/host against the SHARD PATH itself
        # (`host-path-mismatch`), so the fixture must use the real
        # ledger/<team>/<short-host>/<YYYY>-<MM>.jsonl shape. A bare filename
        # fails for a reason unrelated to anything under test.
        self.shard = self.tmp / "ledger" / "hermit" / "testhost001" / "2026-08.jsonl"
        self.shard.parent.mkdir(parents=True, exist_ok=True)
        self.live = self.tmp / "live.jsonl"

    def write_live(self, rows: list[dict]) -> None:
        self.live.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def run_import(self, *flags: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(IMPORTER),
             "--shard", str(self.shard), "--live", str(self.live),
             "--workspace-root", WORKSPACE, *flags],
            capture_output=True, text=True,
        )

    def legacy_rows(self) -> list[dict]:
        return replay_legacy(read_shard(self.shard))

    # ------------------------------------------- the three-state distinction --
    def test_absent_null_and_zero_all_survive_distinctly(self) -> None:
        """THE CORE PROPERTY, asserted in all three directions at once.

        One import, three rows, three different states of the same field. If the
        importer normalised in either direction this fails, and the failure names
        which row lost which state.
        """
        self.write_live([
            row("a" * 40, "2026-08-08T01:00:00Z"),                            # absent
            row("b" * 40, "2026-08-08T02:00:00Z", concurrent_validates=None),  # null
            row("c" * 40, "2026-08-08T03:00:00Z", concurrent_validates=0),     # zero
            row("d" * 40, "2026-08-08T04:00:00Z", concurrent_validates=18),    # real
        ])
        proc = self.run_import()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        got = {r["commit"]: r for r in self.legacy_rows()}
        self.assertEqual(len(got), 4, got)

        self.assertNotIn("concurrent_validates", got["a" * 40],
                         "a never-measured field must stay ABSENT, not become 0 or null")
        self.assertIn("concurrent_validates", got["b" * 40])
        self.assertIsNone(got["b" * 40]["concurrent_validates"],
                          "an explicit null must stay null, not become 0 or absent")
        self.assertEqual(got["c" * 40]["concurrent_validates"], 0,
                         "a MEASURED ZERO must survive as 0, not be dropped to absent")
        self.assertEqual(got["d" * 40]["concurrent_validates"], 18)

    def test_round_trip_is_byte_exact(self) -> None:
        """Absent-preservation is structural, not a rule: the source row is
        carried verbatim, so replay reconstructs it exactly. Asserted on the
        awkward states rather than a tidy one."""
        rows = [
            row("a" * 40, "2026-08-08T01:00:00Z"),
            row("b" * 40, "2026-08-08T02:00:00Z", concurrent_validates=None),
            row("c" * 40, "2026-08-08T03:00:00Z", concurrent_validates=0),
        ]
        self.write_live(rows)
        self.assertEqual(self.run_import().returncode, 0)
        expected = [dict(r, cwd="{{WORKSPACE_ROOT}}/hermit") for r in rows]
        self.assertEqual(self.legacy_rows(), expected)

    # ------------------------------------------------------------ redaction --
    def test_workspace_paths_are_redacted_and_no_owner_path_is_written(self) -> None:
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        text = self.shard.read_text()
        self.assertIn("{{WORKSPACE_ROOT}}/hermit", text)
        self.assertNotIn(WORKSPACE, text)
        self.assertNotIn("/" + "home" + "/", text,
                         "a tracked shard must carry no owner path at any depth")

    def test_unredactable_owner_path_is_quarantined_not_dropped(self) -> None:
        """NEGATIVE: a /home/ path the single defined token cannot cover must be
        held back, NAMED, and still pending afterwards -- never silently skipped
        and never guessed at."""
        good = row("a" * 40, "2026-08-08T01:00:00Z", concurrent_validates=3)
        bad = row("b" * 40, "2026-08-08T02:00:00Z",
                  first_error_line="/home/test/.cargo/git/checkouts/x/y.c:1: fatal")
        self.write_live([good, bad])

        proc = self.run_import()
        self.assertEqual(proc.returncode, 6, proc.stdout + proc.stderr)
        self.assertIn("QUARANTINED 1", proc.stdout)
        self.assertIn("b" * 12, proc.stdout, "the quarantined row must be NAMED")
        self.assertIn("first_error_line", proc.stdout, "and the offending field named")

        commits = {r["commit"] for r in self.legacy_rows()}
        self.assertEqual(commits, {"a" * 40}, "only the clean row is published")
        self.assertNotIn("/" + "home" + "/", self.shard.read_text())

        # NOT DROPPED: it is still pending on the next run, so the gap stays visible.
        again = self.run_import("--dry-run")
        self.assertIn("QUARANTINED 1", again.stdout,
                      "a quarantined row must be re-reported, not forgotten")

    # ------------------------------------------- silent-failure protections --
    def test_ids_continue_and_never_collide(self) -> None:
        """A restarted id would not collide loudly -- union_events dedups BY
        event_id, so it would REPLACE published events. Assert continuation."""
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        first = read_shard(self.shard)

        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z"),
                         row("b" * 40, "2026-08-08T02:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        events = read_shard(self.shard)

        ids = [e["event_id"] for e in events]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate event_id: {ids}")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_id"], first[0]["event_id"])
        indices = [e["legacy_index"] for e in events]
        self.assertEqual(indices, sorted(set(indices)), "legacy_index must stay unique")

    def test_second_run_is_idempotent(self) -> None:
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z", concurrent_validates=2)])
        self.assertEqual(self.run_import().returncode, 0)
        before = self.shard.read_text()

        proc = self.run_import()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("shard is current", proc.stdout)
        self.assertEqual(self.shard.read_text(), before, "a no-op run must write nothing")

    def test_existing_bytes_are_never_rewritten(self) -> None:
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        before = self.shard.read_text()

        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z"),
                         row("b" * 40, "2026-08-08T02:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        after = self.shard.read_text()
        self.assertTrue(after.startswith(before),
                        "the import must be a pure append; old bytes are immutable")

    # --------------------------------------------------------- check-only ----
    def test_check_only_reports_lag_without_writing(self) -> None:
        """POSITIVE for the scheduler hook: a behind shard is a nonzero exit, and
        nothing is written."""
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z")])
        proc = self.run_import("--check-only")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("CHECK FAILED", proc.stderr)
        self.assertFalse(self.shard.exists(), "--check-only must not create the shard")

    def test_check_only_passes_when_current(self) -> None:
        """ADMIT CONTROL: without this, a checker that always failed would pass
        the test above."""
        self.write_live([row("a" * 40, "2026-08-08T01:00:00Z")])
        self.assertEqual(self.run_import().returncode, 0)
        proc = self.run_import("--check-only")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("shard is current", proc.stdout)


if __name__ == "__main__":
    unittest.main()
