#!/usr/bin/env python3
"""Migrate scorecards to the tier + full provenance schema without inventing evidence.

Historical parity booleans cannot be made qualified after the reference operand,
exact Reverie SHA, profile and run coverage have been discarded.  The migration
therefore moves them byte-for-byte into ``legacy_parity_unqualified`` and clears
the qualified observable.  This preserves the historical observation while
making it impossible for a consumer to count it as a current parity claim.
The independent ``comparison_tier`` is backfilled as ``legacy-unqualified``;
historical rows are never guessed to have met either strict comparison tier.

Idempotent, fail-closed on partial schemas, and serialized with ``flock``.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import shutil
import sys
from pathlib import Path

TIER_COLUMNS = ("bitwise_parity", "compared_log_messages", "tier")
COMPARISON_TIER_COLUMN = "comparison_tier"
LEGACY_COMPARISON_TIER = "legacy-unqualified"
E9PATCH_REACH_COLUMNS = ("candidate_sites", "mapped_sites", "reach_state")
REVERIE_COLUMNS = ("absence_reason",)
PROVENANCE_COLUMNS = (
    "legacy_parity_unqualified",
    "ref_output_hash",
    "parity_comparator",
    "parity_tier",
    "profile_flags",
    "population_id",
    "selected_count",
    "executed_count",
    "evidence_count",
)
ANCHOR = "verify_compare"
PARITY_NAMES = ("stdout_parity", "tool_count_parity", "parity")
PRESERVED = (
    "run_id", "run_utc", "hermit_sha", "reverie_sha", "dirty", "run_mode", "lane",
    "bucket", "test_id", "test_mode", "backend", "cell_state", "outcome",
    "output_hash", "duration_ms", "max_rss_kb", "reason",
)
RELAXATION_COLUMN = "relaxation_set"


def historical_relaxations(_row: dict[str, str], _parity_column: str) -> list[str]:
    """Fail closed when the old row did not carry its own relaxation set.

    `lane=portable` is not an exact-set authority: historical producers varied
    the CPUID flag per backend and test. The paired provenance migration may
    replace this marker only from immutable per-row producer evidence.
    """
    return ["UNKNOWN-RELAXATION"]


def present_or_none(header: list[str], columns: tuple[str, ...], label: str) -> bool:
    present = [column for column in columns if column in header]
    if present and len(present) != len(columns):
        raise ValueError(
            f"partial {label} schema ({', '.join(present)}); refusing partial migration"
        )
    return bool(present)


def migrate(path: Path, *, apply: bool) -> int:
    backup = path.with_suffix(path.suffix + ".pre-provenance-migration")
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            original = handle.read()
            if not original.strip():
                print(f"REFUSED: {path} is empty", file=sys.stderr)
                return 2
            lines = original.splitlines()
            header = next(csv.reader([lines[0]]))
            rows = list(csv.DictReader(lines))
            if any(None in row for row in rows):
                print(f"REFUSED: {path} has over-wide rows", file=sys.stderr)
                return 2
            parity_columns = [name for name in PARITY_NAMES if name in header]
            if len(parity_columns) != 1:
                print(
                    f"REFUSED: {path} must have exactly one parity observable; found {parity_columns}",
                    file=sys.stderr,
                )
                return 2
            parity_column = parity_columns[0]

            if ANCHOR not in header:
                if "reason" not in header:
                    print(
                        f"REFUSED: {path} has neither {ANCHOR!r} nor 'reason'",
                        file=sys.stderr,
                    )
                    return 2
                at = header.index("reason") + 1
                header.insert(at, ANCHOR)
                for row in rows:
                    row[ANCHOR] = ""

            try:
                has_tier = present_or_none(header, TIER_COLUMNS, "tier")
                has_provenance = present_or_none(
                    header, PROVENANCE_COLUMNS, "provenance"
                )
            except ValueError as error:
                print(f"REFUSED: {path} {error}", file=sys.stderr)
                return 2

            new_header = list(header)
            if not has_tier:
                at = new_header.index(ANCHOR) + 1
                new_header[at:at] = list(TIER_COLUMNS)
            if not has_provenance:
                new_header.extend(PROVENANCE_COLUMNS)
            if RELAXATION_COLUMN not in new_header:
                at = new_header.index("profile_flags") + 1
                new_header.insert(at, RELAXATION_COLUMN)
            if COMPARISON_TIER_COLUMN not in new_header:
                new_header.append(COMPARISON_TIER_COLUMN)
            if path.name == "e9patch-scorecard.csv":
                for column in E9PATCH_REACH_COLUMNS:
                    if column not in new_header:
                        new_header.append(column)
            if path.name == "reverie-scorecard.csv":
                for column in REVERIE_COLUMNS:
                    if column not in new_header:
                        new_header.append(column)
            comparison_labelled = 0
            for row in rows:
                for column in TIER_COLUMNS + PROVENANCE_COLUMNS + (RELAXATION_COLUMN,):
                    row.setdefault(column, "")
                for column in E9PATCH_REACH_COLUMNS:
                    if column in new_header:
                        row.setdefault(column, "")
                for column in REVERIE_COLUMNS:
                    if column in new_header:
                        row.setdefault(column, "")
                # The old rows do not carry INFO/stack/heap evidence.  Record
                # that absence explicitly; never guess either strict tier.
                if not (row.get(COMPARISON_TIER_COLUMN) or "").strip():
                    row[COMPARISON_TIER_COLUMN] = LEGACY_COMPARISON_TIER
                    comparison_labelled += 1

            labelled = 0
            unqualified = 0
            ineligible = 0
            dequalified = 0
            relaxation_filled = 0
            for row in rows:
                raw_relaxations = (row.get(RELAXATION_COLUMN) or "").strip()
                if raw_relaxations:
                    try:
                        relaxations = json.loads(raw_relaxations)
                    except json.JSONDecodeError as error:
                        print(f"REFUSED: malformed relaxation_set: {error}", file=sys.stderr)
                        return 2
                    if not isinstance(relaxations, list) or any(
                        not isinstance(item, str) or not item for item in relaxations
                    ) or len(relaxations) != len(set(relaxations)):
                        print(f"REFUSED: relaxation_set is not a string set: {raw_relaxations!r}", file=sys.stderr)
                        return 2
                else:
                    relaxations = historical_relaxations(row, parity_column)
                    row[RELAXATION_COLUMN] = json.dumps(relaxations, separators=(",", ":"))
                    relaxation_filled += 1
                if relaxations:
                    ineligible += 1
                    if row.get("deterministic") == "1":
                        row["deterministic"] = ""
                        dequalified += 1

                mode = (row.get("test_mode") or "").strip()
                if (
                    row.get("deterministic") == "1"
                    and not (row.get("tier") or "").strip()
                ):
                    if mode == "counter":
                        row["verify_compare"] = row.get("verify_compare") or "syscall-count-across-reps"
                        row["tier"] = "counter"
                        labelled += 1
                    elif mode == "verify" and (
                        (row.get("verify_compare") or "").strip() == "stripped"
                        or row.get("run_mode") in ("expansion", "e9patch")
                    ):
                        row["verify_compare"] = row.get("verify_compare") or "stripped"
                        row["tier"] = "stripped-uncounted"
                        labelled += 1

                verdict = (row.get(parity_column) or "").strip()
                if verdict in ("0", "1"):
                    # No historical file had the complete provenance columns.  Do not
                    # infer any missing value from a neighbouring row or checkout.
                    qualified = all((row.get(column) or "").strip() for column in (
                        "ref_output_hash", "parity_comparator", "parity_tier",
                        "profile_flags", "population_id", "selected_count",
                        "executed_count", "evidence_count",
                    ))
                    if not qualified:
                        prior = (row.get("legacy_parity_unqualified") or "").strip()
                        carried = f"{parity_column}:{verdict}"
                        if prior and prior != carried:
                            print(
                                f"REFUSED: conflicting legacy parity {prior!r} vs {carried!r}",
                                file=sys.stderr,
                            )
                            return 2
                        row["legacy_parity_unqualified"] = carried
                        row[parity_column] = ""
                        unqualified += 1

            changed = (
                new_header != header or labelled > 0 or unqualified > 0
                or relaxation_filled > 0 or dequalified > 0
                or comparison_labelled > 0
            )
            print(
                f"rows={len(rows)} header={len(header)}->{len(new_header)} "
                f"tier_labels={labelled} comparison_labels={comparison_labelled} "
                f"parity_moved_unqualified={unqualified} "
                f"non_strict_or_unknown={ineligible} "
                f"deterministic_dequalified={dequalified}"
            )
            if not changed:
                print(f"ALREADY MIGRATED: {path}")
                return 0
            if not apply:
                print("DRY RUN — pass --apply to write")
                return 0

            shutil.copyfile(path, backup)
            handle.seek(0)
            handle.truncate()
            writer = csv.DictWriter(handle, fieldnames=new_header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    before = list(csv.DictReader(backup.open(encoding="utf-8")))
    after = list(csv.DictReader(path.open(encoding="utf-8")))
    if len(before) != len(after):
        print(f"FAILED: row count {len(before)} -> {len(after)}", file=sys.stderr)
        return 1
    parity_column = next(name for name in PARITY_NAMES if name in after[0])
    moved = 0
    ineligible = 0
    dequalified = 0
    for index, (old, new) in enumerate(zip(before, after), start=2):
        for column in PRESERVED:
            if column in old and old[column] != new.get(column):
                print(
                    f"FAILED: line {index} column {column} changed "
                    f"{old[column]!r} -> {new.get(column)!r}",
                    file=sys.stderr,
                )
                return 1
        old_parity = (old.get(parity_column) or "").strip()
        if old_parity in ("0", "1") and not (new.get(parity_column) or "").strip():
            if new.get("legacy_parity_unqualified") != f"{parity_column}:{old_parity}":
                print(f"FAILED: line {index} lost legacy parity", file=sys.stderr)
                return 1
            moved += 1
        parsed_relaxations = json.loads(new[RELAXATION_COLUMN])
        if parsed_relaxations:
            ineligible += 1
            if old.get("deterministic") == "1":
                if new.get("deterministic"):
                    print(f"FAILED: line {index} retained relaxed deterministic=1", file=sys.stderr)
                    return 1
                dequalified += 1
        elif old.get("deterministic") != new.get("deterministic"):
            print(f"FAILED: line {index} changed strict deterministic verdict", file=sys.stderr)
            return 1
    print(
        f"OK: {len(after)} rows migrated; {ineligible} non-strict-or-unknown rows recorded; "
        f"{dequalified} relaxed deterministic positives dequalified; {moved} unbound "
        "parity values preserved as legacy-unqualified"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv_path",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent / "scorecard.csv",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.csv_path.exists():
        print(f"REFUSED: {args.csv_path} does not exist", file=sys.stderr)
        return 2
    return migrate(args.csv_path, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
