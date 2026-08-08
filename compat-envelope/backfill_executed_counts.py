#!/usr/bin/env python3
"""Backfill run-level executed counts onto scorecards the old emitter produced.

Fixing an emitter does not fix the records it already produced. Those records
stay quotable and read as complete measurements. This tool closes that residue
for the `selected_count` / `executed_count` / `evidence_count` triple, which the
current collector writes and every previously-published scorecard leaves blank.

Measured state on `origin/main` at 2026-08-07, before this tool runs:

    3309 published scorecard rows
       0 with `executed_count` populated      <- the column exists and nothing writes it
    1671 with `legacy_parity_unqualified`     <- the UNQUALIFIED half, already done

So the column is present but inert -- the "unpopulated column" case, which is
worse than an absent one because a reader sees the field and infers it was
considered.

Semantics are taken from the collector, not invented
(`collect-envelope.rs`, which computes these after every row is known):

    selected_count  rows in the run
    executed_count  rows in the run that actually ran
    evidence_count  rows carrying *qualified* parity evidence

They are **run-level** values denormalised onto every row of that run, so this
tool groups by `run_id` and never derives a per-row count.

Two rules keep the backfill honest:

* **A derived value is never silently substituted for a recorded one.** An
  existing non-blank count is left exactly as it is.
* **Where the run data does not support a derivation, the cell is written
  `UNQUALIFIED`, not left blank.** A blank is indistinguishable from "nobody
  looked"; `UNQUALIFIED` says someone looked and could not tell.

Default is a dry run. `--apply` writes.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from dataclasses import dataclass
from pathlib import Path

#: Outcomes that mean the cell did not run. Anything else counts as executed.
NON_EXECUTED = frozenset({"", "unavailable", "no-result", "skipped"})

UNQUALIFIED = "UNQUALIFIED"

COUNT_FIELDS = ("selected_count", "executed_count", "evidence_count")


@dataclass
class RunBackfill:
    run_id: str
    selected: int
    executed: int
    evidence: int
    derivable: bool
    reason: str = ""

    def value(self, field: str) -> str:
        if not self.derivable:
            return UNQUALIFIED
        return str({"selected_count": self.selected,
                    "executed_count": self.executed,
                    "evidence_count": self.evidence}[field])


def parity_field(header) -> str | None:
    for candidate in ("stdout_parity", "parity"):
        if candidate in header:
            return candidate
    return None


def plan(rows, header) -> dict[str, RunBackfill]:
    """Derive one backfill per run_id."""
    pk = parity_field(header)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("run_id", ""), []).append(row)

    out: dict[str, RunBackfill] = {}
    for run_id, group in groups.items():
        if "outcome" not in header:
            out[run_id] = RunBackfill(run_id, len(group), 0, 0, False,
                                      "no outcome column; executed cannot be derived")
            continue
        executed = [r for r in group if (r.get("outcome") or "").strip() not in NON_EXECUTED]
        evidence = [r for r in executed if pk and (r.get(pk) or "").strip() != ""]
        out[run_id] = RunBackfill(run_id, len(group), len(executed), len(evidence), True)
    return out


def apply_to_text(text: str) -> tuple[str, dict[str, RunBackfill], dict[str, int]]:
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    rows = list(reader)
    if not rows:
        raise ValueError("zero data rows; refusing to backfill an empty scorecard")

    missing = [f for f in COUNT_FIELDS if f not in header]
    plans = plan(rows, header)

    stats = {"rows": len(rows), "written": 0, "preserved": 0, "unqualified": 0}
    if missing:
        # The column does not exist; we do not invent schema here.
        return text, plans, {**stats, "missing_columns": len(missing)}

    for row in rows:
        rb = plans[row.get("run_id", "")]
        for field in COUNT_FIELDS:
            if (row.get(field) or "").strip() != "":
                stats["preserved"] += 1        # never overwrite a recorded value
                continue
            row[field] = rb.value(field)
            if row[field] == UNQUALIFIED:
                stats["unqualified"] += 1
            else:
                stats["written"] += 1

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue(), plans, {**stats, "missing_columns": 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csvs", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)

    total = {"rows": 0, "written": 0, "preserved": 0, "unqualified": 0}
    files_backfilled = files_unqualified = files_no_column = 0

    for path in args.csvs:
        try:
            new_text, plans, stats = apply_to_text(path.read_text())
        except (OSError, ValueError) as error:
            print(f"  REFUSED {path}: {error}", file=sys.stderr)
            continue
        if stats["missing_columns"]:
            files_no_column += 1
            print(f"  NO-COLUMN {path}: lacks {', '.join(COUNT_FIELDS)} — schema change needed "
                  f"before backfill ({stats['rows']} rows)")
            continue
        any_unqual = any(not p.derivable for p in plans.values())
        files_unqualified += any_unqual
        files_backfilled += not any_unqual
        for rb in sorted(plans.values(), key=lambda p: p.run_id):
            mark = "UNQUALIFIED" if not rb.derivable else ""
            print(f"  {path.name} / {rb.run_id or '(no run_id)'}: "
                  f"selected={rb.value('selected_count')} executed={rb.value('executed_count')} "
                  f"evidence={rb.value('evidence_count')} {mark}")
        for k in total:
            total[k] += stats[k]
        if args.apply:
            path.write_text(new_text)

    print(f"\n  files: backfilled {files_backfilled} | with UNQUALIFIED runs {files_unqualified} "
          f"| lacking the columns {files_no_column}")
    print(f"  cells: written {total['written']} | UNQUALIFIED {total['unqualified']} "
          f"| preserved(existing) {total['preserved']} | rows {total['rows']}")
    print("  DRY RUN — nothing written (pass --apply to write)" if not args.apply
          else "  APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
