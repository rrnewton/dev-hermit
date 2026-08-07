#!/usr/bin/env python3
"""Derive the PRE-TIGHTENING baseline projection from a raw fullcorpus sweep.

The raw CSV that `collect-fullcorpus.sh` writes carries per-run timing
(`duration_ms`, `max_rss_kb`, `run_utc`) that cannot repeat byte-for-byte, so
the raw file is evidence but is NOT the reproducible artifact. This tool
projects it onto the timing-free per-cell verdict, which IS reproducible: run
`derive.py --check` against the same raw input and the diff is empty.

It also states, per row, what the verdict was actually established BY. Two
fields exist for that and neither may be summarised away:

  determinism_contract  stripped-verify-l2  -- `hermit run --strict --verify`
                        exited 0. A STRIPPED comparison: it normalises before
                        comparing and has been measured missing planted
                        DETLOG / address / path divergence. NOT bitwise.
  parity_contract       stdout-sha256       -- sha256 of piped guest stdout vs
                        the ptrace reference. Compares one channel of four.

  observed_tier         TIER-1-AT-BEST | TIER-0-FAIL | NO-RESULT-*
                        Tier vocabulary from hermit PR #1778:
                        TIER-1 exit+stdout, TIER-2 +stderr, TIER-3 +INFO.
                        stdout-sha256 cannot establish better than TIER-1, so
                        every green here is TIER-1-AT-BEST -- never asserted as
                        the tier actually achieved, which is unmeasured.
  bitwise_axis          CONTRACT-UNAVAILABLE on every row. #1595 wired
                        BitwiseInfoV1 into same-backend paths only, and the
                        cross-backend comparator (parity_mutation.py, PR #1778)
                        is out-of-tree. There is no in-product way to ask "does
                        this backend match ptrace, bitwise?", so the field
                        records the absence instead of implying a zero.

Usage:
  derive.py --raw raw-scorecard.csv --out results.csv [--totals totals.md]
  derive.py --raw raw-scorecard.csv --out results.csv --check
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

# A cell the sweep never ran is not a zero. Keep the three states apart.
NO_RESULT = "NO-RESULT-NOT-RUN"
UNMEASURED = "NO-RESULT-UNMEASURED"

OUT_COLUMNS = [
    "hermit_sha",
    "reverie_sha",
    "dirty",
    "lane",
    "bucket",
    "test_id",
    "test_mode",
    "backend",
    "cell_state",
    "outcome",
    "determinism_result",
    "determinism_contract",
    "stdout_parity_result",
    "parity_contract",
    "observed_tier",
    "bitwise_axis",
]


def tri(value: str) -> str:
    """1/0/blank -> pass/fail/unmeasured. A blank is NOT a zero."""
    value = (value or "").strip()
    if value == "1":
        return "pass"
    if value == "0":
        return "fail"
    return UNMEASURED


def observed_tier(parity: str, determinism: str) -> str:
    if parity == "pass":
        # stdout-sha256 agreement bounds the claim at TIER-1. stderr (TIER-2)
        # and the unstripped INFO log (TIER-3) were never compared, so a higher
        # tier is unmeasured rather than unreached.
        return "TIER-1-AT-BEST"
    if parity == "fail":
        return "TIER-0-FAIL"
    if determinism in ("pass", "fail"):
        return UNMEASURED
    return NO_RESULT


def derive(raw_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for row in raw_rows:
        determinism = tri(row.get("deterministic", ""))
        parity = tri(row.get("stdout_parity", row.get("parity", "")))
        out.append(
            {
                "hermit_sha": row.get("hermit_sha", ""),
                "reverie_sha": row.get("reverie_sha", ""),
                "dirty": row.get("dirty", ""),
                "lane": row.get("lane", ""),
                "bucket": row.get("bucket", ""),
                "test_id": row.get("test_id", ""),
                "test_mode": row.get("test_mode", ""),
                "backend": row.get("backend", ""),
                "cell_state": row.get("cell_state", ""),
                "outcome": row.get("outcome", ""),
                "determinism_result": determinism,
                "determinism_contract": "stripped-verify-l2",
                "stdout_parity_result": parity,
                "parity_contract": "stdout-sha256",
                "observed_tier": observed_tier(parity, determinism),
                "bitwise_axis": "CONTRACT-UNAVAILABLE",
            }
        )
    # Deterministic order so a rerun is byte-identical regardless of the raw
    # file's row order (the collector writes backends as they finish).
    out.sort(key=lambda r: (r["backend"], r["bucket"], r["test_id"], r["test_mode"]))
    return out


def render(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=OUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def totals(rows: list[dict[str, str]]) -> str:
    by_backend: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        counter = by_backend[row["backend"]]
        counter["ran"] += 1
        counter["det_" + row["determinism_result"]] += 1
        counter["par_" + row["stdout_parity_result"]] += 1
        counter["tier_" + row["observed_tier"]] += 1

    lines = [
        "| backend | ran | det pass | det fail | det unmeasured | parity pass | parity fail "
        "| parity unmeasured | TIER-1-AT-BEST | bitwise-qualified |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for backend in sorted(by_backend):
        c = by_backend[backend]
        lines.append(
            f"| {backend} | {c['ran']} | {c['det_pass']} | {c['det_fail']} "
            f"| {c['det_' + UNMEASURED]} | {c['par_pass']} | {c['par_fail']} "
            f"| {c['par_' + UNMEASURED]} | {c['tier_TIER-1-AT-BEST']} "
            f"| 0 (contract unavailable) |"
        )
    grand = Counter()
    for counter in by_backend.values():
        grand.update(counter)
    lines.append(
        f"| **TOTAL** | **{grand['ran']}** | **{grand['det_pass']}** | **{grand['det_fail']}** "
        f"| **{grand['det_' + UNMEASURED]}** | **{grand['par_pass']}** | **{grand['par_fail']}** "
        f"| **{grand['par_' + UNMEASURED]}** | **{grand['tier_TIER-1-AT-BEST']}** "
        f"| **0 (contract unavailable)** |"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--totals", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-derive and diff against --out instead of writing it",
    )
    args = parser.parse_args(argv)

    with args.raw.open(newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    if not raw_rows:
        print(f"REFUSED: {args.raw} has 0 data rows, which is a no-result", file=sys.stderr)
        return 3

    rendered = render(derive(raw_rows))

    if args.check:
        if not args.out.is_file():
            print(f"REFUSED: {args.out} does not exist to check against", file=sys.stderr)
            return 3
        existing = args.out.read_text()
        if existing == rendered:
            print(f"REPRODUCIBLE: re-derived {args.out.name} is byte-identical ({len(raw_rows)} raw rows)")
            return 0
        print(f"DRIFT: re-derived {args.out.name} differs from the committed copy", file=sys.stderr)
        return 1

    args.out.write_text(rendered)
    if args.totals:
        args.totals.write_text(totals(derive(raw_rows)))
    print(f"wrote {args.out} ({len(raw_rows)} raw rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
