#!/usr/bin/env python3
"""Classify every published compat-envelope cell by WHAT PRODUCED IT.

A scorecard is auditable only if each cell says how it was obtained. This walks the
published scorecards and assigns one provenance class per row, plus the evidence
fields each row is missing. Reproduce: python3 classify.py > results.csv
"""
import csv, os, sys, collections

BASE = "/home/newton/work/dev-hermit/compat-envelope"
FILES = ["scorecard.csv", "fullcorpus-scorecard.csv", "e9patch-scorecard.csv", "reverie-scorecard.csv"]


def provenance(row: dict) -> str:
    """One class per cell, from the fields the row itself carries."""
    state = row.get("cell_state", "")
    if state == "disabled":
        # From `audit-gaps`: NOT in the CI plan. Executed only to see if it would pass.
        return "gap-probe-not-in-ci"
    if state == "expansion":
        return "expansion-candidate"
    if state == "enabled":
        return "ci-enforced-measured" if row.get("output_hash", "").strip() else "ci-enforced-unhashed"
    return f"unclassified({state or 'blank'})"


def main() -> int:
    out = csv.writer(sys.stdout)
    out.writerow(["source", "backend", "test_id", "test_mode", "outcome", "parity",
                  "provenance", "has_output_hash", "has_ref_output_hash",
                  "reverie_sha_known", "hermit_sha"])
    tally: collections.Counter[tuple[str, str]] = collections.Counter()
    for fname in FILES:
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path)):
            cls = provenance(r)
            tally[(fname, cls)] += 1
            out.writerow([
                fname, r.get("backend", ""), r.get("test_id", ""), r.get("test_mode", ""),
                r.get("outcome", ""), r.get("parity", ""), cls,
                bool(r.get("output_hash", "").strip()),
                # Absent from the published SCHEMA entirely, not merely blank.
                "ref_output_hash" in r and bool((r.get("ref_output_hash") or "").strip()),
                r.get("reverie_sha", "") not in ("", "unknown"),
                r.get("hermit_sha", ""),
            ])
    for (f, c), n in sorted(tally.items()):
        print(f"# {f:28s} {c:24s} {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
