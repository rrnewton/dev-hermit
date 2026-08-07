#!/usr/bin/env python3
"""Mutation testing for the detlog comparison producer.

BOTH HALVES ARE REQUIRED, and the second is the one that gets skipped. Planting
a wrong value proves the comparator FIRES. Confirming the legitimate stream still
passes proves it does not fire on EVERYTHING. A comparator that reports false
unconditionally satisfies every mutation below and is worthless -- which is not
hypothetical here: porting `capture_parity`'s cross-backend axis to detlog would
have produced exactly that, because the backends emit different record counts.

Every mutation must also fail for its OWN reason. A suite where several
mutations die at one shared earlier check reports a perfect detection rate while
leaving the checks it meant to exercise unexecuted -- observed three times in
this session, including once in my own first draft of a mutation harness.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detlog_compare as dc  # noqa: E402

# A realistic stream, shaped like the captured baselines: the tracing formatter
# puts a wall-clock and level in front of every record.
BASE = [
    "DETLOG SCHEDRAND: seeding scheduler runqueue with seed 0",
    "DETLOG USER RAND: seeding PRNG for root thread with seed 0",
    "DETLOG CHAOSRAND: seeding chaos scheduler with seed 0",
    "DETLOG SYSCALL: [1] brk(NULL) = 0x5bd000",
    "DETLOG TIME: virtual clock advanced to 1000000",
]


def raw(records, prefix="2026-08-07T00:00:00Z INFO "):
    """Wrap records in a formatter prefix, as hermit really emits them."""
    return "\n".join(prefix + r for r in records) + "\n"


class LegitimatePopulation(unittest.TestCase):
    """The half that proves the comparator is not a blanket rejecter."""

    def test_identical_runs_pass_with_a_nonzero_denominator(self):
        v = dc.self_determinism(raw(BASE), raw(BASE))
        self.assertEqual(v["verdict"], dc.PASS)
        self.assertEqual(v["denominator_run1"], 5)
        self.assertEqual(v["differing"], 0)

    def test_a_differing_wall_clock_prefix_does_not_fail_the_comparison(self):
        """The prefix is real time. Comparing it would make every run diverge for
        a reason that has nothing to do with determinism."""
        v = dc.self_determinism(
            raw(BASE, "2026-08-07T00:00:00Z INFO "),
            raw(BASE, "2026-08-07T09:59:59Z INFO "),
        )
        self.assertEqual(v["verdict"], dc.PASS)

    def test_interleaved_non_detlog_output_is_ignored(self):
        noisy = "some guest stdout\n" + raw(BASE) + "warning: unrelated\n"
        v = dc.self_determinism(noisy, raw(BASE))
        self.assertEqual(v["verdict"], dc.PASS)
        self.assertEqual(v["denominator_run1"], 5)

    def test_the_three_measured_baselines_would_pass(self):
        """Known-good inputs: the captured baselines were 0/141, 0/368, 0/1245.
        A stream compared against itself at those sizes must pass and must report
        the size, not just 'pass'."""
        for n in (141, 368, 1245):
            stream = [f"DETLOG SYSCALL: [{i}] read(3) = 64" for i in range(n)]
            v = dc.self_determinism(raw(stream), raw(stream))
            self.assertEqual(v["verdict"], dc.PASS, n)
            self.assertEqual(v["denominator_run1"], n)
            self.assertEqual(v["differing"], 0)


class PlantedMutations(unittest.TestCase):
    """The half that proves the comparator fires. Each must fail its OWN way."""

    def test_M1_one_record_changed_is_detected(self):
        m = list(BASE)
        m[3] = "DETLOG SYSCALL: [1] brk(NULL) = 0xDEADBEEF"
        v = dc.self_determinism(raw(BASE), raw(m))
        self.assertEqual(v["verdict"], dc.FAIL)
        self.assertEqual(v["differing"], 1)
        self.assertEqual(v["common_prefix"], 3)

    def test_M2_truncated_stream_is_detected_not_scored_clean(self):
        """Agrees everywhere it overlaps. Counting only positional mismatches
        would score this 0 and call it identical."""
        v = dc.self_determinism(raw(BASE), raw(BASE[:3]))
        self.assertEqual(v["verdict"], dc.FAIL)
        self.assertEqual(v["differing"], 2)
        self.assertEqual(v["common_prefix"], 3)

    def test_M3_appended_record_is_detected(self):
        v = dc.self_determinism(raw(BASE), raw(BASE + ["DETLOG EXTRA: unexpected"]))
        self.assertEqual(v["verdict"], dc.FAIL)
        self.assertEqual(v["differing"], 1)

    def test_M4_reordering_is_detected(self):
        m = list(BASE)
        m[0], m[1] = m[1], m[0]
        v = dc.self_determinism(raw(BASE), raw(m))
        self.assertEqual(v["verdict"], dc.FAIL)
        self.assertEqual(v["common_prefix"], 0)

    def test_M5_a_single_changed_byte_deep_in_a_record_is_detected(self):
        """No field inside a record is normalised away, so a one-character
        divergence in virtual time is still a divergence."""
        m = list(BASE)
        m[4] = "DETLOG TIME: virtual clock advanced to 1000001"
        v = dc.self_determinism(raw(BASE), raw(m))
        self.assertEqual(v["verdict"], dc.FAIL)
        self.assertEqual(v["differing"], 1)

    def test_M6_empty_stream_is_NOT_MEASURED_never_pass(self):
        v = dc.self_determinism(raw(BASE), "")
        self.assertEqual(v["verdict"], dc.NOT_MEASURED)
        # Assert the SEMANTIC, not the wording: the reason must name the
        # zero-record condition. Pinning exact prose makes the test brittle to a
        # refactor that changed nothing observable.
        self.assertIn("no detlog records", v["reason"])

    def test_M7_both_streams_empty_is_NOT_MEASURED_not_a_trivial_pass(self):
        """THE VACUOUS-GREEN TRAP. Two empty streams have an identical digest, so
        a bare `r == t` reports PASS for a run that measured nothing."""
        v = dc.self_determinism("", "")
        self.assertEqual(v["verdict"], dc.NOT_MEASURED)
        self.assertEqual(v["denominator_run1"], 0)

    def test_mutations_fail_for_DISTINCT_reasons(self):
        """Guard against a suite whose mutations all die at one shared check."""
        cases = {
            "changed": raw([*BASE[:3], "DETLOG X: y", BASE[4]]),
            "truncated": raw(BASE[:2]),
            "appended": raw(BASE + ["DETLOG Z: w"]),
            "empty": "",
        }
        seen = set()
        for name, mutant in cases.items():
            v = dc.self_determinism(raw(BASE), mutant)
            self.assertNotEqual(v["verdict"], dc.PASS, name)
            seen.add((v["verdict"], v["differing"], v["common_prefix"]))
        self.assertEqual(len(seen), len(cases), f"collapsed signatures: {seen}")


class CrossBackendRefusesAVerdict(unittest.TestCase):
    def test_cross_backend_returns_no_parity_boolean(self):
        a = [f"DETLOG A{i}" for i in range(141)]
        b = [f"DETLOG A{i}" for i in range(368)]
        r = dc.cross_backend_prefix(raw(a), raw(b))
        self.assertFalse(r["comparable"])
        self.assertNotIn("verdict", r)
        self.assertEqual(r["denominator_a"], 141)
        self.assertEqual(r["denominator_b"], 368)

    def test_both_denominators_are_reported_so_neither_can_be_quoted_alone(self):
        a = [f"DETLOG A{i}" for i in range(141)]
        b = [f"DETLOG A{i}" for i in range(100)] + [f"DETLOG B{i}" for i in range(1145)]
        r = dc.cross_backend_prefix(raw(a), raw(b))
        self.assertEqual(r["common_prefix"], 100)
        self.assertIsNotNone(r["prefix_over_a_pct"])
        self.assertIsNotNone(r["prefix_over_b_pct"])
        self.assertNotEqual(r["prefix_over_a_pct"], r["prefix_over_b_pct"])


class TierNeverOverclaims(unittest.TestCase):
    def test_a_subset_never_reports_strict(self):
        t = dc.tier_for({"stdout": dc.PASS, "info_log": "", "stack": "", "heap": ""})
        self.assertNotEqual(t, "strict")
        self.assertTrue(t.startswith("partial:"))

    def test_detlog_plus_stdout_is_still_partial(self):
        t = dc.tier_for({"stdout": dc.PASS, "stack": dc.PASS, "info_log": "", "heap": ""})
        self.assertTrue(t.startswith("partial:"))

    def test_all_four_is_strict(self):
        """Positive control: the tier is reachable, not permanently unreachable."""
        t = dc.tier_for(
            {"stdout": dc.PASS, "info_log": dc.PASS, "stack": dc.PASS, "heap": dc.PASS}
        )
        self.assertEqual(t, "strict")

    def test_nothing_compared_is_blank_not_a_tier(self):
        self.assertEqual(dc.tier_for({"stdout": "", "heap": ""}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
