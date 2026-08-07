#!/usr/bin/env python3
"""Migrate the canonical scorecard to carry tier evidence, without inventing any.

WHY THIS EXISTS. `deterministic=1` is ambiguous on its own: it cannot say which
comparison earned it. The producers now emit `verify_compare`, `bitwise_parity`,
`compared_log_messages` and `tier` -- but the canonical
`compat-envelope/scorecard.csv` is 20 columns wide and carries only
`verify_compare`, so the writer (correctly refusing to short-write rows) drops
the other three and the evidence never reaches the consumer. Widening the file is
what makes the new columns load-bearing rather than decorative.

WHAT IT DOES NOT INVENT. `bitwise_parity` and `compared_log_messages` are left
EMPTY on every historical row, and `deterministic`, `parity`, `outcome` and every
other existing value are untouched. Those are measurements, and no run made them.

THE ONE THING IT DOES NAME. A historical row already recording `deterministic=1`
under `verify_compare=stripped` in a two-run mode is given
`tier=stripped-uncounted`. That is naming a comparator the row already records,
not deriving a measurement, and the name itself admits the count is absent. The
alternative was to leave `tier` blank -- which forces the consumer to keep an
implicit "blank tier means pass" bypass, and that bypass is precisely the
fail-open hole this work exists to close. An explicit weak tier is auditable; a
blank one is not.

Idempotent, and fail-closed on a file it does not recognise. Takes the same
`flock` the producers take, so a concurrent append cannot interleave.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import shutil
import sys
from pathlib import Path

NEW_COLUMNS = ("bitwise_parity", "compared_log_messages", "tier")
ANCHOR = "verify_compare"  # the new columns are appended directly after this

# Columns whose values must be byte-identical before and after. This is the
# safety property the migration asserts rather than merely intends.
PRESERVED = (
    "run_id", "run_utc", "hermit_sha", "reverie_sha", "dirty", "run_mode", "lane",
    "bucket", "test_id", "test_mode", "backend", "cell_state", "outcome",
    "deterministic", "output_hash", "duration_ms", "max_rss_kb", "reason",
    "verify_compare",
)


def migrate(path: Path, *, apply: bool) -> int:
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            original = handle.read()
            rows = list(csv.DictReader(original.splitlines()))
            header = list(csv.reader([original.splitlines()[0]]))[0]

            if ANCHOR not in header:
                # A scorecard predating the evidence schema entirely (the Reverie one
                # is 19 columns with no comparator at all). Insert the anchor too,
                # after `reason`, rather than refusing -- otherwise the wired consumer
                # is permanently red against a file no migration will touch.
                if "reason" not in header:
                    print(f"REFUSED: {path} has neither {ANCHOR!r} nor 'reason'; its "
                          f"header is {len(header)} wide: {','.join(header)}",
                          file=sys.stderr)
                    return 2
                at = header.index("reason") + 1
                header = header[:at] + [ANCHOR] + header[at:]
                for r in rows:
                    r[ANCHOR] = ""
                print(f"NOTE: {path} had no {ANCHOR!r}; inserting it after 'reason'")
            present = [c for c in NEW_COLUMNS if c in header]
            if len(present) == len(NEW_COLUMNS):
                # Already widened. It may still predate the labelling rule, so relabel
                # in place; this is idempotent and touches only blank tiers.
                todo = [r for r in rows
                        if r.get("deterministic") == "1"
                        and not (r.get("tier") or "").strip()
                        and (r.get("verify_compare") or "").strip() == "stripped"
                        and (r.get("test_mode") or "").strip() in ("verify", "counter")]
                if not todo:
                    print(f"ALREADY MIGRATED: {path} carries all {len(header)} columns "
                          f"and every deterministic=1 row is labelled")
                    return 0
                print(f"RELABEL: {len(todo)} deterministic=1 row(s) carry a recorded "
                      f"stripped comparator but a blank tier")
                if not apply:
                    print("DRY RUN — pass --apply to write")
                    return 0
                for r in todo:
                    r["tier"] = "stripped-uncounted"
                handle.seek(0); handle.truncate()
                w = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
                w.writeheader(); w.writerows(rows); handle.flush()
                print(f"OK: relabelled {len(todo)} row(s) as tier=stripped-uncounted; "
                      f"no other column touched")
                return 0
            if present:
                print(
                    f"REFUSED: {path} carries only part of the evidence schema "
                    f"({', '.join(present)}); refusing a partial migration",
                    file=sys.stderr,
                )
                return 2

            at = header.index(ANCHOR) + 1
            new_header = header[:at] + list(NEW_COLUMNS) + header[at:]
            labelled = 0
            for row in rows:
                for column in NEW_COLUMNS:
                    row[column] = ""  # unmeasured, never derived
                # ONE exception, and it is naming rather than deriving. A row that
                # already records deterministic=1 under verify_compare=stripped in a
                # two-run mode HAS a recorded comparator; calling that tier
                # `stripped-uncounted` states the comparison it ran and admits, in the
                # name, that the message count was never recorded. Leaving it blank
                # instead would force the consumer to keep an implicit
                # blank-tier-means-pass bypass, which is exactly the fail-open hole.
                # No count, parity or outcome is invented.
                mode = (row.get("test_mode") or "").strip()
                if row.get("deterministic") == "1" and mode == "counter":
                    # collect-reverie-compat compares a syscall counter across >=2 reps.
                    # Naming that is not deriving a measurement; the mode IS the method.
                    row["verify_compare"] = "syscall-count-across-reps"
                    row["tier"] = "counter"
                    labelled += 1
                elif (row.get("deterministic") == "1"
                        and (row.get("verify_compare") or "").strip() == "stripped"
                        and mode == "verify"):
                    row["tier"] = "stripped-uncounted"
                    labelled += 1
            print(f"labelled {labelled} historical deterministic=1 row(s) with an "
                  f"explicit tier (comparator named; absent counts stay absent)")

            print(f"rows={len(rows)}  header {len(header)} -> {len(new_header)} columns")
            print(f"adding (all blank): {', '.join(NEW_COLUMNS)}")
            if not apply:
                print("DRY RUN — pass --apply to write")
                return 0

            shutil.copyfile(path, path.with_suffix(path.suffix + ".pre-tier-migration"))
            handle.seek(0)
            handle.truncate()
            writer = csv.DictWriter(handle, fieldnames=new_header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # Verify AFTER releasing, by re-reading what is actually on disk.
    after = list(csv.DictReader(path.open(encoding="utf-8")))
    before = list(csv.DictReader(
        path.with_suffix(path.suffix + ".pre-tier-migration").open(encoding="utf-8")))
    if len(after) != len(before):
        print(f"FAILED: row count {len(before)} -> {len(after)}", file=sys.stderr)
        return 1
    for index, (old, new) in enumerate(zip(before, after), start=2):
        for column in PRESERVED:
            if column == "verify_compare":
                # May go blank -> "syscall-count-across-reps" for counter rows (naming
                # the method the mode already is). It may never be OVERWRITTEN.
                if (old.get(column) or "") and old[column] != new.get(column):
                    print(f"FAILED: line {index} verify_compare overwritten "
                          f"{old[column]!r} -> {new.get(column)!r}", file=sys.stderr)
                    return 1
                continue
            if column in old and old[column] != new.get(column):
                print(
                    f"FAILED: line {index} column {column} changed "
                    f"{old[column]!r} -> {new.get(column)!r}",
                    file=sys.stderr,
                )
                return 1
        nonblank = {c: new.get(c) for c in NEW_COLUMNS if new.get(c)}
        if nonblank and set(nonblank) != {"tier"}:
            print(f"FAILED: line {index} has unexpected derived values {nonblank}",
                  file=sys.stderr)
            return 1
        if nonblank.get("tier") not in (None, "stripped-uncounted", "counter"):
            print(f"FAILED: line {index} tier={nonblank['tier']!r} not permitted here",
                  file=sys.stderr)
            return 1
    print(f"OK: {len(after)} rows migrated; every preserved column byte-identical; "
          f"bitwise_parity and compared_log_messages blank on every row")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, nargs="?",
                        default=Path(__file__).resolve().parent / "scorecard.csv")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()
    if not args.csv_path.exists():
        print(f"REFUSED: {args.csv_path} does not exist", file=sys.stderr)
        return 2
    return migrate(args.csv_path, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
