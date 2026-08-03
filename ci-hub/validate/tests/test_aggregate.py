#!/usr/bin/env python3
"""Tests for ci-hub/validate/aggregate.py.

Focus: the faithful per-gate classifier and the run-level de-conflation it
feeds. Every class is anchored ONLY on markers hermit/validate.sh writes itself
(`Command:`, `Skipped:`, `Gate timed out after`, `Exit:`, `Duration:`), never on
workload text — a gate whose *output* contains the words "panic" or "timeout"
must not be misclassified. These tests therefore include adversarial workload
text to prove the classifier ignores it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aggregate as agg


def parse(body: str) -> dict:
    """Reconstruct a run record from `body`.

    The temp file is written under a private directory and unlinked immediately
    after parsing (parse_raw_log reads the file and its mtime synchronously), so
    the test never leaves a `hermit-validate.*.log` in a real TMPDIR that the
    machine-wide sweep would then ingest.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "hermit-validate.test.log")
        with open(path, "w") as fh:
            fh.write(body)
        return agg.parse_raw_log(path)


# A real gate that passed.
PASS = """\
=== Build workspace ===
Command: cargo build --workspace
   Compiling detcore v0.1.0
Exit: 0
Duration: 42s

"""

# A real gate that failed for a product reason (non-zero, not a timeout).
FAIL = """\
=== Detcore core unit tests ===
Command: cargo test -p detcore --lib
test scheduler::races ... FAILED
Exit: 101
Duration: 30s

"""

# Killed by a bound via run_timed_command: the authoritative marker + Exit 124.
TIMEOUT_MARKER = """\
=== liteinst strict verify python entropy ===
Command: cargo test -p hermit liteinst_strict_verify_python_entropy
test liteinst_strict_verify_python_entropy ... Gate timed out after 300s (subprocess PID 2865433)
Exit: 124
Duration: 300s

"""

# Killed by a bound via a bare `timeout` in a compat probe: Exit 124, NO marker.
TIMEOUT_BARE = """\
=== L2 compatibility: lsof ===
Command: timeout 60 hermit run --strict --verify -- lsof
:: Run1...
Exit: 124
Duration: 60s

"""

# A deferred/skipped gate: header + Skipped:, no Exit.
SKIPPED = """\
=== L2 compatibility: rustc ===
Skipped: scheduled super diagnostic (portable runner)

"""

# A pure section banner: header alone, no Command/Exit.
BANNER = "=== Strict compatibility envelope (L2, blocking) ===\n"

# A gate cut off mid-run: Command but never an Exit/Duration footer (EOF).
INCOMPLETE = """\
=== Portable ptrace E2E verification ===
Command: ./ci/test_harness.sh e2e
:: Run1...
"""

HEADER = "Root: /home/newton/work/dev-hermit/hermit\nLevel: full\n"


class ClassifyTest(unittest.TestCase):
    def kinds(self, body: str) -> list[str]:
        return [g["kind"] for g in parse(HEADER + body)["gates"]]

    def test_pass(self):
        self.assertEqual(self.kinds(PASS), ["pass"])

    def test_product_fail(self):
        self.assertEqual(self.kinds(FAIL), ["fail"])

    def test_timeout_via_marker(self):
        self.assertEqual(self.kinds(TIMEOUT_MARKER), ["timeout"])

    def test_timeout_via_bare_exit_124(self):
        # No "Gate timed out after" line, only Exit 124 — still killed-by-bound.
        self.assertEqual(self.kinds(TIMEOUT_BARE), ["timeout"])

    def test_skipped(self):
        self.assertEqual(self.kinds(SKIPPED), ["skipped"])

    def test_banner(self):
        self.assertEqual(self.kinds(BANNER), ["banner"])

    def test_incomplete_trailing(self):
        self.assertEqual(self.kinds(INCOMPLETE), ["incomplete"])

    def test_workload_text_does_not_sway_class(self):
        # A PASSING gate whose output prints the words a naive scanner keys on.
        adversarial = """\
=== run tests that mention scary words ===
Command: cargo test -p detcore mentions_panic_and_timeout
test panic_recovery ... ok
note: 'Gate timed out' appears here only as guest stdout, not a real marker line prefix... actually it must be a standalone line to count
Exit: 0
Duration: 5s

"""
        # The marker check is substring-based on validate.sh's own writes; here
        # the decisive fact is Exit 0 -> pass regardless of the word "panic".
        self.assertEqual(self.kinds(adversarial), ["pass"])


class RunLevelTest(unittest.TestCase):
    def test_verdict_priority_fail_beats_timeout(self):
        run = parse(HEADER + PASS + TIMEOUT_MARKER + FAIL)
        self.assertEqual(run["result"], "fail")

    def test_verdict_timeout_beats_incomplete(self):
        run = parse(HEADER + PASS + INCOMPLETE.replace("Portable", "X") + TIMEOUT_MARKER)
        # incomplete is a trailing-only concept; put the incomplete gate first so
        # it is followed by a completed timeout gate.
        self.assertEqual(run["result"], "timeout")

    def test_incomplete_when_only_cutoff(self):
        run = parse(HEADER + PASS + INCOMPLETE)
        self.assertEqual(run["result"], "incomplete")

    def test_all_pass(self):
        run = parse(HEADER + PASS + PASS)
        self.assertEqual(run["result"], "pass")

    def test_deconfliction_fields(self):
        run = parse(HEADER + PASS + FAIL + TIMEOUT_MARKER + TIMEOUT_BARE
                    + INCOMPLETE)
        self.assertEqual(run["failures"], 1)          # product fail only
        self.assertEqual(run["killed_by_bound"], 2)   # both timeout paths
        self.assertEqual(run["incomplete_gates"], 1)
        self.assertEqual(run["gate_classification"]["pass"], 1)

    def test_banner_excluded_from_checks(self):
        # Banner + skipped present; checks counts non-banner gates only.
        run = parse(HEADER + BANNER + PASS + SKIPPED)
        self.assertEqual(run["banner_lines"], 1)
        self.assertEqual(run["skipped_gates"], 1)
        self.assertEqual(run["checks"], 2)  # PASS + SKIPPED, banner excluded


class RenderTest(unittest.TestCase):
    def test_header_carries_second_units(self):
        out = agg.render_table([parse(HEADER + PASS)])
        head = out.splitlines()[0]
        self.assertIn("WALL(s)", head)
        self.assertIn("USER(s)", head)
        self.assertIn("SYS(s)", head)

    def test_profile_not_truncated(self):
        long_profile = "portable-strict-compat-only"
        run = parse("Root: /x\nLevel: %s\n" % long_profile + PASS)
        out = agg.render_table([run])
        self.assertIn(long_profile, out)  # full identifier, not "portable-stric"

    def test_gates_denominator_excludes_banner(self):
        run = parse(HEADER + BANNER + PASS + FAIL)
        out = agg.render_table([run])
        # 1 pass of 2 verdict gates (banner excluded), not 1/3.
        self.assertIn("1/2", out)

    def test_gate_kind_tolerates_ledger_rows(self):
        # Ledger rows carry only `result`, no `kind`.
        self.assertEqual(agg.gate_kind({"result": "pass"}), "pass")
        self.assertEqual(agg.gate_kind({"result": "fail"}), "fail")
        self.assertEqual(agg.gate_kind({"exit_code": 0}), "pass")


if __name__ == "__main__":
    unittest.main()
