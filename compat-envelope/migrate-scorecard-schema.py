#!/usr/bin/env python3
"""Migrate the canonical scorecard to carry tier evidence, without inventing any.

WHY THIS EXISTS. `deterministic=1` is ambiguous on its own: it cannot say which
comparison earned it. The producers now emit `verify_compare`, `bitwise_parity`,
`compared_log_messages` and `tier` -- but the canonical
`compat-envelope/scorecard.csv` is 20 columns wide and carries only
`verify_compare`, so the writer (correctly refusing to short-write rows) drops
the other three and the evidence never reaches the consumer. Widening the file is
what makes the new columns load-bearing rather than decorative.

WHAT IT DOES NOT DO, and this is the point. Historical rows get the three new
columns **EMPTY**. It does not derive `tier` from `verify_compare`, and it does
not touch `deterministic`, `parity`, `outcome` or any other existing value. A
tier is a claim about a comparison that a particular run performed; back-filling
it from a neighbouring column would manufacture measurements that no run made --
the same class of error the tier work exists to remove. Blank means unmeasured,
which is exactly true of every row written before the producers learned to record
this.

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
                print(
                    f"REFUSED: {path} has no {ANCHOR!r} column; its header is "
                    f"{len(header)} wide: {','.join(header)}",
                    file=sys.stderr,
                )
                return 2
            present = [c for c in NEW_COLUMNS if c in header]
            if len(present) == len(NEW_COLUMNS):
                print(f"ALREADY MIGRATED: {path} carries all {len(header)} columns")
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
            for row in rows:
                for column in NEW_COLUMNS:
                    row[column] = ""  # unmeasured, never derived

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
            if column in old and old[column] != new.get(column):
                print(
                    f"FAILED: line {index} column {column} changed "
                    f"{old[column]!r} -> {new.get(column)!r}",
                    file=sys.stderr,
                )
                return 1
        if any(new.get(c) != "" for c in NEW_COLUMNS):
            print(f"FAILED: line {index} has a non-blank derived value", file=sys.stderr)
            return 1
    print(f"OK: {len(after)} rows migrated; all {len(PRESERVED)} existing columns "
          f"byte-identical; {len(NEW_COLUMNS)} new columns blank on every row")
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
