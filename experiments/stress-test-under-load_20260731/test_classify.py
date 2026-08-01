#!/usr/bin/env python3
"""Self-test for the guardrail's P0 detector (harness._classify) and fingerprint
parser (harness._count_fp).

The whole guardrail is only as trustworthy as this classifier: a live GREEN run
proves it does not FALSE-alarm, but not that it actually FIRES on divergence.
These asserts drive synthetic rep records through every branch of the taxonomy,
check branch PRECEDENCE (output divergence outranks a verify flip), and pin the
"timeouts are never a determinism P0" rule. Dependency-free: run `python3
test_classify.py`; exits 0 on success, 1 on the first failure. No hermit, no load.
"""

from __future__ import annotations

import sys

import harness


def rep(
    *,
    test: str = "t",
    output_hash: str | None = "h0",
    count_fp: str | None = "fp0",
    verify_pass: bool = True,
    strict_timed_out: bool = False,
    verify_timed_out: bool = False,
) -> dict:
    """A minimal rep record with exactly the keys _classify reads."""
    return {
        "test": test,
        "output_hash": output_hash,
        "count_fp": count_fp,
        "verify_pass": verify_pass,
        "strict_timed_out": strict_timed_out,
        "verify_timed_out": verify_timed_out,
    }


_failures: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
        _failures.append(name)


def main() -> int:
    print("== _classify taxonomy ==")

    # GREEN: all reps agree (1 hash, verify pass, 1 fp).
    v = harness._classify([rep(), rep(), rep()])
    check("green.verdict", v["verdict"], "GREEN")
    check("green.p0", v["p0"], False)
    check("green.scored", v["reps_scored"], 3)

    # P0: output bytes diverged across reps.
    v = harness._classify([rep(output_hash="h0"), rep(output_hash="h1")])
    check("output_div.verdict", v["verdict"], "P0_OUTPUT_DIVERGENCE")
    check("output_div.p0", v["p0"], True)

    # P0: --verify passed in some reps, failed in others (hash held constant).
    v = harness._classify([rep(verify_pass=True), rep(verify_pass=False)])
    check("verify_flip.verdict", v["verdict"], "P0_VERIFY_FLIP")
    check("verify_flip.p0", v["p0"], True)

    # P0: schedule/event-count fingerprint moved (hash + verify held constant).
    v = harness._classify([rep(count_fp="fpA"), rep(count_fp="fpB")])
    check("sched_fp.verdict", v["verdict"], "P0_SCHEDULE_FP_DIVERGENCE")
    check("sched_fp.p0", v["p0"], True)

    # Consistent verify failure == determinism defect, NOT load-induced (not P0 here).
    v = harness._classify([rep(verify_pass=False), rep(verify_pass=False)])
    check("preexisting.verdict", v["verdict"], "PREEXISTING_VERIFY_FAIL")
    check("preexisting.p0", v["p0"], False)

    # All reps timed out -> nothing scored -> INCONCLUSIVE, never P0.
    v = harness._classify(
        [rep(strict_timed_out=True, verify_timed_out=True, output_hash=None, count_fp=None)] * 3
    )
    check("all_timeout.verdict", v["verdict"], "INCONCLUSIVE")
    check("all_timeout.p0", v["p0"], False)
    check("all_timeout.scored", v["reps_scored"], 0)
    check("all_timeout.timeouts", v["timeouts"], 3)

    print("== precedence + timeout-tolerance ==")

    # PRECEDENCE: output divergence must win even when verify also flips.
    v = harness._classify(
        [rep(output_hash="h0", verify_pass=True), rep(output_hash="h1", verify_pass=False)]
    )
    check("precedence.output_over_flip", v["verdict"], "P0_OUTPUT_DIVERGENCE")

    # A timed-out rep among GREEN reps must NOT create a false P0 (it's dropped, not divergent).
    v = harness._classify(
        [
            rep(),
            rep(),
            rep(strict_timed_out=True, verify_timed_out=True, output_hash=None, count_fp=None),
        ]
    )
    check("timeout_mixed.verdict", v["verdict"], "GREEN")
    check("timeout_mixed.p0", v["p0"], False)
    check("timeout_mixed.timeouts", v["timeouts"], 1)
    check("timeout_mixed.scored", v["reps_scored"], 2)

    # A None hash from a strict-only timeout shouldn't inflate the distinct-hash set.
    v = harness._classify(
        [rep(output_hash="h0"), rep(output_hash=None, strict_timed_out=True)]
    )
    check("none_hash_ignored.verdict", v["verdict"], "GREEN")

    print("== _count_fp parsing ==")

    stderr_a = (
        "some noise\n"
        "Logs contain 1677 | 1677 DETLOG & scheduler COMMIT messages\n"
        "Logs contain 2439 | 2439 messages total\n"
        "trailing\n"
    )
    # Same numbers, different LINE ORDER -> identical fingerprint (sorted-normalized).
    stderr_a_reordered = (
        "Logs contain 2439 | 2439 messages total\n"
        "Logs contain 1677 | 1677 DETLOG & scheduler COMMIT messages\n"
    )
    fp_a = harness._count_fp(stderr_a)
    fp_a2 = harness._count_fp(stderr_a_reordered)
    check("count_fp.order_invariant", fp_a, fp_a2)
    check("count_fp.nonempty", bool(fp_a), True)

    # A moved count -> different fingerprint (this is what SCHEDULE_FP_DIVERGENCE keys on).
    stderr_b = (
        "Logs contain 1678 | 1678 DETLOG & scheduler COMMIT messages\n"
        "Logs contain 2439 | 2439 messages total\n"
    )
    check("count_fp.detects_move", harness._count_fp(stderr_b) != fp_a, True)

    # An A|B mismatch (the two runs disagreed) is preserved verbatim, not collapsed.
    stderr_mismatch = "Logs contain 100 | 101 messages total\n"
    check("count_fp.keeps_mismatch", harness._count_fp(stderr_mismatch), "messages total=100:101")

    # No count lines -> empty fingerprint (caller treats as no-signal).
    check("count_fp.empty_when_absent", harness._count_fp("no counts here"), "")

    print()
    if _failures:
        print(f"FAILED {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
