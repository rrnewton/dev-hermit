#!/usr/bin/env python3
"""Validate and report the per-cell strict comparison tier.

WHY THIS DERIVES THE SET INSTEAD OF LISTING IT. Two scorecards were found still
publishing untiered rows, and the cause was that
`migrate-scorecard-schema.py` defaults to a single filename
(`csv_path ... default=.../scorecard.csv`, line ~194) -- so whoever ran the
migration migrated one file and the others silently kept the old schema. A guard
that hardcoded "check these four" would have exactly the same defect one
scorecard later: the set GROWS, and a stale list is how the class stays open.
So the set is globbed from disk on every run, and the count is printed so a
reader can see the denominator rather than trust it.

`.pre-tier-migration` backups are excluded on purpose: they are pre-migration
snapshots kept deliberately, and failing on them would train people to ignore
this check -- which is the failure mode that lets a real untiered scorecard
through.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = "comparison_tier"
FULL = "full-stdout-info-stack-heap"
SPOT_CHECK = "stdout-info-stack-heap-spot-check"
QUALIFYING = frozenset((FULL, SPOT_CHECK))

# These are explicit NON-GREEN classifications.  They let a historical or
# weaker measurement say what it actually established without either leaving
# the field blank or being silently promoted to one of the strict tiers.
UNQUALIFIED = frozenset((
    "legacy-unqualified",
    "unqualified-no-comparison",
    "unqualified-stdout-only",
    "unqualified-self-verify-only",
    "unqualified-tool-count-only",
))
KNOWN = QUALIFYING | UNQUALIFIED
EXCLUDE_SUFFIXES = (".pre-tier-migration",)


def scorecards(root: Path) -> list[Path]:
    return sorted(
        p for p in root.glob("*scorecard*.csv")
        if not any(str(p).endswith(s) for s in EXCLUDE_SUFFIXES)
    )


def header(path: Path) -> list[str]:
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            return [c.strip() for c in row]
    return []


def green_column(columns: list[str]) -> str | None:
    """Return the raw execution-result column; never guess beyond known names."""
    for name in ("outcome", "result"):
        if name in columns:
            return name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    found = scorecards(a.root)
    if not found:
        print(f"UNVERIFIABLE: no *scorecard*.csv under {a.root}", file=sys.stderr)
        return 2

    offenders = []
    total_rows = 0
    total_raw_green = 0
    total_qualified_green = 0
    total_distribution: dict[str, int] = {}
    for p in found:
        cols = header(p)
        if REQUIRED not in cols:
            offenders.append(f"{p.name}: missing column {REQUIRED!r}")
            continue
        result_column = green_column(cols)
        if result_column is None:
            offenders.append(f"{p.name}: missing outcome/result column")
            continue
        with p.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            offenders.append(f"{p.name}: no data rows")
            continue

        distribution: dict[str, int] = {}
        raw_green = 0
        qualified_green = 0
        for line, row in enumerate(rows, start=2):
            tier = (row.get(REQUIRED) or "").strip()
            label = tier or "<blank>"
            distribution[label] = distribution.get(label, 0) + 1
            total_distribution[label] = total_distribution.get(label, 0) + 1
            if not tier:
                offenders.append(f"{p.name}:{line}: blank {REQUIRED}")
            elif tier not in KNOWN:
                offenders.append(f"{p.name}:{line}: unknown {REQUIRED}={tier!r}")

            is_raw_green = (row.get(result_column) or "").strip() == "pass"
            if is_raw_green:
                raw_green += 1
                if tier in QUALIFYING:
                    qualified_green += 1

        total_rows += len(rows)
        total_raw_green += raw_green
        total_qualified_green += qualified_green
        if not a.quiet:
            print(
                f"  {p.name}: rows={len(rows)} tier_distribution={distribution} "
                f"qualified_green={qualified_green}/{raw_green} raw_passes"
            )

    if not a.quiet:
        print(f"scorecards enumerated: {len(found)} (derived by glob, not a list)")
        print(
            f"tier distribution: {total_distribution} (denominator {total_rows} rows); "
            f"qualified green={total_qualified_green}/{total_raw_green} raw passes"
        )

    if offenders:
        print(
            f"\nREFUSED: {len(offenders)} comparison-tier schema/value violation(s) "
            f"across {len(found)} scorecard(s):",
            file=sys.stderr,
        )
        for offender in offenders[:20]:
            print(f"  {offender}", file=sys.stderr)
        if len(offenders) > 20:
            print(f"  ... {len(offenders) - 20} more", file=sys.stderr)
        print(
            f"Every row must carry one known {REQUIRED}. Only {FULL!r} and "
            f"{SPOT_CHECK!r} qualify a raw pass as green; unqualified values are "
            "explicit non-green history, never defaults.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
