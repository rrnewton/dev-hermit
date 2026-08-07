#!/usr/bin/env python3
"""Bind every historical scorecard row to its exact relaxation set.

The historical CSV schema did not carry this condition.  Recovery is therefore
allowlisted by immutable run identity plus the producer revision recorded by the
row.  A lane name is never sufficient evidence: several portable backend-parity
runs intentionally omitted CPUID virtualization, while one portable record/replay
row used neither portable relaxation.

An unrecognized row is written as ``["UNKNOWN-RELAXATION"]``.  It is never
defaulted to ``[]`` because absence of provenance is not evidence of strictness.
Rows carrying any relaxation (including UNKNOWN) lose ``deterministic=1``; the
original outcome and every other measurement remain untouched.
"""

from __future__ import annotations

import argparse
import collections
import csv
import fcntl
import json
import os
import tempfile
from pathlib import Path


COLUMN = "relaxation_set"
UNKNOWN = ("UNKNOWN-RELAXATION",)
PORTABLE = ("no-virtualize-cpuid", "max-timeslice=disabled")
MAX_TMP = ("max-timeslice=disabled", "tmp=/tmp")
PORTABLE_TMP = (*PORTABLE, "tmp=/tmp")

FULLCORPUS_RUNS = {
    "ptrace-fullcorpus-scorecard",
    "kvm-fullcorpus-scorecard",
    "liteinst-fullcorpus-scorecard",
    "dbi-fullcorpus-scorecard",
    "sabre-fullcorpus-scorecard",
    "e9patch-fullcorpus-scorecard",
}
FULLCORPUS_SHA = "82a8e853357584a3a567fd80812e015572a607c7"

SCORECARD_FULLCORPUS_RUNS = {
    "kvm-fullcorpus-scorecard": FULLCORPUS_SHA,
    "liteinst-fullcorpus-1785621912":
        "464cbd9f9bb43d5505c914783819e1d349630283",
}
BACKEND_PARITY_RUNS = {
    "backend-parity-09d7bd0c6f98-1785720885-1561902":
        "09d7bd0c6f9833a51e4681357c552d24b71b6cf1",
    "backend-parity-09d7bd0c6f98-1785720918-1586797":
        "09d7bd0c6f9833a51e4681357c552d24b71b6cf1",
    "backend-parity-75edd7455dc9-1785909047-3802619":
        "75edd7455dc99f26953c06d8b2c8fb757c580c04",
    "backend-parity-52d56e5ceb38-1785912310-972152":
        "52d56e5ceb386d24ec809edbfdb6920e8484271e",
    "backend-parity-fc49593ac21c-1785914664-639593":
        "fc49593ac21c7655e841a3de825ef86692ad117c",
}


def lane_profile(row: dict[str, str]) -> tuple[str, ...]:
    if row.get("lane") == "portable":
        return PORTABLE
    if row.get("lane") == "privileged":
        return ()
    return UNKNOWN


def backend_parity_profile(row: dict[str, str]) -> tuple[str, ...]:
    """Recover the exact run_matrix.py command, not the portable label.

    These producers always passed max-timeslice=disabled and tmp=/tmp.  Only
    ptrace passed no-virtualize-cpuid, and its cpuid_policy case deliberately
    omitted that flag so the test could exercise CPUID virtualization.
    """
    base = MAX_TMP
    if row.get("backend") == "ptrace" and not row.get("test_id", "").endswith(
        "/cpuid_policy"
    ):
        return PORTABLE_TMP
    return base


def classify(dataset: str, row: dict[str, str]) -> tuple[str, ...]:
    run_id = row.get("run_id", "")
    sha = row.get("hermit_sha", "")

    if dataset == "reverie-scorecard.csv":
        if (
            run_id == "reverie-20260801"
            and sha == "2f3689bd8830ab6b59dacea6cb72951f4d0d899e"
            and row.get("run_mode") == "reverie"
        ):
            # The collector invokes Reverie launchers directly, not Hermit.
            return ()
        return UNKNOWN

    if dataset == "e9patch-scorecard.csv":
        if (
            run_id == "e9patch-20260801"
            and sha == "b1fdeaf6d7bcda0799a7a5c4f116bbe1ed55a43d"
            and row.get("run_mode") == "e9patch"
        ):
            return ("tmp=/tmp",)
        return UNKNOWN

    if dataset == "fullcorpus-scorecard.csv":
        if run_id in FULLCORPUS_RUNS and sha == FULLCORPUS_SHA:
            return lane_profile(row)
        return UNKNOWN

    if dataset != "scorecard.csv":
        return UNKNOWN

    if (
        run_id == "canonical-release-ptrace-dbi"
        and sha == "9429005ca04b6ae0b3d0aedbdc18969f3b770603"
    ):
        if (
            row.get("test_id") == "system-utils/record-getpid"
            and row.get("test_mode") == "replay"
            and row.get("backend") == "ptrace"
        ):
            # test_harness.sh's record command did not splice in the portable
            # profile; its JSON producer incorrectly inferred it from the lane.
            return ()
        if row.get("test_mode") in {"verify", "chaos", "custom"}:
            return PORTABLE
        return UNKNOWN

    expected_sha = SCORECARD_FULLCORPUS_RUNS.get(run_id)
    if expected_sha is not None and sha == expected_sha:
        return lane_profile(row)

    if (
        run_id == "liteinst-spst-1785620995"
        and sha == "464cbd9f9bb43d5505c914783819e1d349630283"
    ):
        return backend_parity_profile(row)

    expected_sha = BACKEND_PARITY_RUNS.get(run_id)
    if expected_sha is not None and sha == expected_sha:
        return backend_parity_profile(row)

    return UNKNOWN


def parse_existing(raw: str, *, path: Path, line: int) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:{line}: malformed {COLUMN}: {error}") from error
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{path}:{line}: {COLUMN} must be a JSON string set")
    return tuple(value)


def rewrite(path: Path, *, apply: bool) -> tuple[int, collections.Counter[tuple[str, ...]], int]:
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            rows = list(reader)
            if not header or not rows:
                raise ValueError(f"{path}: empty scorecard")
            if any(None in row for row in rows):
                raise ValueError(f"{path}: over-wide row")

            new_header = list(header)
            if COLUMN not in new_header:
                if "profile_flags" in new_header:
                    new_header.insert(new_header.index("profile_flags") + 1, COLUMN)
                else:
                    new_header.append(COLUMN)

            counts: collections.Counter[tuple[str, ...]] = collections.Counter()
            dequalified = 0
            changed = new_header != header
            for line, row in enumerate(rows, start=2):
                raw = (row.get(COLUMN) or "").strip()
                profile = parse_existing(raw, path=path, line=line) if raw else classify(path.name, row)
                encoded = json.dumps(profile, separators=(",", ":"))
                if raw != encoded:
                    row[COLUMN] = encoded
                    changed = True
                counts[profile] += 1
                if profile and row.get("deterministic") == "1":
                    row["deterministic"] = ""
                    dequalified += 1
                    changed = True

            if apply and changed:
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
                )
                try:
                    with os.fdopen(fd, "w", newline="", encoding="utf-8") as out:
                        writer = csv.DictWriter(out, fieldnames=new_header, lineterminator="\n")
                        writer.writeheader()
                        writer.writerows(rows)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(tmp_name, path)
                finally:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return len(rows), counts, dequalified


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scorecards",
        type=Path,
        nargs="*",
        default=[
            root / "scorecard.csv",
            root / "fullcorpus-scorecard.csv",
            root / "e9patch-scorecard.csv",
            root / "reverie-scorecard.csv",
        ],
    )
    parser.add_argument("--apply", action="store_true", help="write (default: audit only)")
    args = parser.parse_args()

    total = 0
    total_counts: collections.Counter[tuple[str, ...]] = collections.Counter()
    total_dequalified = 0
    try:
        for path in args.scorecards:
            rows, counts, dequalified = rewrite(path, apply=args.apply)
            total += rows
            total_counts.update(counts)
            total_dequalified += dequalified
            rendered = {json.dumps(k, separators=(",", ":")): v for k, v in sorted(counts.items())}
            print(f"{path}: rows={rows}/{rows} sets={rendered} dequalified={dequalified}/{rows}")
    except (OSError, ValueError) as error:
        print(f"REFUSED: {error}")
        return 2

    unknown = total_counts[UNKNOWN]
    print(
        f"TOTAL rows={total}/{total} known={total - unknown}/{total} "
        f"unknown={unknown}/{total} dequalified={total_dequalified}/{total}"
    )
    if not args.apply:
        print("AUDIT ONLY -- pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
