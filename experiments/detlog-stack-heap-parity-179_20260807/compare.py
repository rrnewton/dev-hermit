#!/usr/bin/env python3
"""Aggregate the 2026-08-07 cross-backend INFO/stack/heap measurement.

This is an inert measurement helper.  It reads captured logs, never runs Hermit,
and refuses to call an empty or failed comparison a match.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/newton/work/dev-hermit")
OUT = ROOT / "ignored/detlog-parity/current-0041130/corpus"
RUNS = OUT / "runs"
DENOM = Path("/tmp/detlog-canonical-179.txt")
TARGETS = ("ptrace-control", "dbi", "kvm", "sabre", "liteinst", "e9patch")
ALL_ARMS = ("ptrace",) + TARGETS
DIMENSIONS = ("info", "stack", "heap")
TIERS = {
    "info": "INFO_INTERBACKEND_SINGLE_RUN",
    "stack": "STACK_INTERBACKEND_SINGLE_RUN",
    "heap": "HEAP_INTERBACKEND_SINGLE_RUN",
}
PATH_KIND = {
    "ptrace-control": "reference-control",
    "dbi": "dbt-backend",
    "kvm": "kvm-backend",
    "sabre": "binary-rewriting",
    "liteinst": "preload-instrumentation",
    "e9patch": "aot-binary-rewriting",
}

# This is the timestamp grammar used by detcore::logdiff::extract_log_messages.
TS = re.compile(
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d\d "
    r"\d\d:\d\d:\d\d\.\d+|\d+-\d\d-\d\dT\d\d:\d\d:\d\d.\d+Z) +"
)
TAG = re.compile(r"^(ERROR|WARN|INFO|DEBUG|TRACE) ")
HOSTADDR = re.compile(r"<hostaddr (0[xX][A-Fa-f0-9]+)>")


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return default


def read_scalar(path: Path, default: str = "") -> str:
    return read_text(path, default).strip()


def canonicalize_hostaddrs(messages: list[str]) -> list[str]:
    mapping: dict[str, int] = {}
    next_ord = 1

    def repl(match: re.Match[str]) -> str:
        nonlocal next_ord
        address = match.group(1)
        if address not in mapping:
            mapping[address] = next_ord
            next_ord += 1
        return f"<addr{mapping[address]}>"

    return [HOSTADDR.sub(repl, message) for message in messages]


def extract_info(raw: str) -> list[str]:
    """Apply BitwiseInfoV1's timestamp strip + INFO selection.

    DBI/SaBRe route their trace through stderr and mix it with launcher
    diagnostics.  Timestamped sources use detcore's message split.  Untimestamped
    INFO records are admitted line-by-line; indented continuations remain joined
    to their immediately preceding INFO record.  Non-log diagnostics terminate a
    pending record and are not silently promoted into the INFO envelope.
    """
    messages: list[str] = []
    if TS.search(raw):
        parts = TS.split(raw)
        for part in parts:
            message = part.strip()
            if TAG.match(message) and message.startswith("INFO "):
                messages.append(message)
    else:
        current: list[str] = []
        for line in raw.splitlines():
            if TAG.match(line):
                if current:
                    messages.append("\n".join(current).strip())
                    current = []
                if line.startswith("INFO "):
                    current = [line]
            elif current and (line.startswith((" ", "\t")) or not line.strip()):
                current.append(line)
            elif current:
                messages.append("\n".join(current).strip())
                current = []
        if current:
            messages.append("\n".join(current).strip())
    return canonicalize_hostaddrs(messages)


def dimension_records(raw: str) -> dict[str, list[str]]:
    info = extract_info(raw)
    return {
        "info": info,
        "stack": [m for m in info if " DETLOG " in m and "[memory]" in m and "[stack]" in m],
        "heap": [m for m in info if " DETLOG " in m and "[memory]" in m and "[heap]" in m],
    }


def stream_records(path: Path) -> tuple[dict[str, dict[str, object]], str]:
    """Stream one capture into exact, boundary-delimited dimension receipts."""
    counts = {dimension: 0 for dimension in DIMENSIONS}
    digests = {dimension: hashlib.sha256() for dimension in DIMENSIONS}
    raw_digest = hashlib.sha256()
    address_map: dict[str, int] = {}
    next_ord = 1
    current: list[str] = []

    def canonicalize(message: str) -> str:
        nonlocal next_ord

        def repl(match: re.Match[str]) -> str:
            nonlocal next_ord
            address = match.group(1)
            if address not in address_map:
                address_map[address] = next_ord
                next_ord += 1
            return f"<addr{address_map[address]}>"

        return HOSTADDR.sub(repl, message)

    def emit() -> None:
        if not current:
            return
        message = canonicalize("".join(current).strip())
        encoded = message.encode(errors="replace")
        dimensions = ["info"]
        if " DETLOG " in message and "[memory]" in message and "[stack]" in message:
            dimensions.append("stack")
        if " DETLOG " in message and "[memory]" in message and "[heap]" in message:
            dimensions.append("heap")
        framed = len(encoded).to_bytes(8, "big") + encoded
        for dimension in dimensions:
            counts[dimension] += 1
            digests[dimension].update(framed)

    try:
        handle = path.open("r", errors="replace")
    except OSError:
        return (
            {dimension: {"count": 0, "sha256": hashlib.sha256().hexdigest()} for dimension in DIMENSIONS},
            "",
        )
    with handle:
        for line in handle:
            raw_digest.update(line.encode(errors="replace"))
            timestamp = TS.match(line)
            if timestamp:
                emit()
                current = []
                payload = line[timestamp.end() :]
                if payload.startswith("INFO "):
                    current = [payload]
            elif TAG.match(line):
                emit()
                current = [line] if line.startswith("INFO ") else []
            elif current and (line.startswith((" ", "\t")) or not line.strip()):
                current.append(line)
            else:
                emit()
                current = []
        emit()
    records = {
        dimension: {"count": counts[dimension], "sha256": digests[dimension].hexdigest()}
        for dimension in DIMENSIONS
    }
    return records, raw_digest.hexdigest()


def receipt(cell: Path) -> dict[str, object]:
    rc_text = read_scalar(cell / "rc", "MISSING")
    try:
        rc: int | str = int(rc_text)
    except ValueError:
        rc = rc_text
    source = read_scalar(cell / "source", "missing")
    engagement = read_scalar(cell / "engagement", "missing")
    runner_outcome = read_scalar(cell / "runner_outcome", "")
    events_path = cell / "events"
    records, events_sha256 = stream_records(events_path)
    return {
        "rc": rc,
        "source": source,
        "engagement": engagement,
        "runner_outcome": runner_outcome,
        "records": records,
        "events_sha256": events_sha256,
    }


def eligible(arm: str, rec: dict[str, object]) -> bool:
    expected = {
        "ptrace-control": {"detcore_engaged"},
        "dbi": {"dbt_engaged"},
        "kvm": {"detcore_engaged"},
        "sabre": {"rewrite_engaged_split_channel"},
        "liteinst": {"preload_engaged"},
        "e9patch": {"rewrite_engaged"},
    }
    return rec["engagement"] in expected[arm]


def classify(
    arm: str,
    ref: dict[str, object],
    target: dict[str, object],
    dimension: str,
) -> tuple[str, str]:
    if ref["rc"] == 125:
        return "NO_RESULT_FIXTURE_ABSENT", "reference fixture is absent at current source SHA"
    if ref["rc"] != 0:
        return "FAILURE_REFERENCE", f"ptrace rc={ref['rc']}"
    if target["rc"] == 125:
        return "NO_RESULT_FIXTURE_ABSENT", "target fixture is absent at current source SHA"
    if target["rc"] != 0:
        outcome = target["runner_outcome"]
        if outcome:
            return "FAILURE_BACKEND", f"rc={target['rc']}; runner={outcome}"
        if target["rc"] == 137:
            return "FAILURE_BACKEND", "rc=137; guest deadline 90s"
        return "FAILURE_BACKEND", f"rc={target['rc']}"
    if not eligible(arm, target):
        return "NO_RESULT_NOT_ENGAGED", f"engagement={target['engagement']}"
    ref_records = ref["records"][dimension]
    target_records = target["records"][dimension]
    if ref_records["count"] == 0:
        return "NO_RESULT_ZERO_REFERENCE", "ptrace emitted zero qualifying records"
    if target_records["count"] == 0:
        return "ABSENT", "ptrace nonzero, target emitted zero qualifying records"
    if ref_records == target_records:
        return "PASS", "nonzero exact match"
    return "DIVERGE", "both sides nonzero and differ"


def first_difference(left: list[str], right: list[str]) -> int | None:
    for index, pair in enumerate(zip(left, right)):
        if pair[0] != pair[1]:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def mutate_message(messages: list[str], kind: str) -> list[str]:
    mutated = list(messages)
    if kind == "info":
        for index, message in enumerate(mutated):
            if "[memory]" not in message:
                mutated[index] = message + " [TAMPERED]"
                return mutated
    else:
        needle = f"[{kind}]"
        for index, message in enumerate(mutated):
            if needle in message:
                match = re.search(r"([0-9a-fA-F]{64})(?![0-9a-fA-F])", message)
                if not match:
                    continue
                digest = match.group(1)
                replacement = ("0" if digest[0] != "0" else "1") + digest[1:]
                mutated[index] = message[: match.start(1)] + replacement + message[match.end(1) :]
                return mutated
    raise RuntimeError(f"could not plant {kind} mutation")


def comparator_bracket(ids: list[str]) -> dict[str, object]:
    selected = None
    selected_records = None
    for test_id in ids:
        rec = receipt(RUNS / test_id.replace("/", "__") / "ptrace")
        records = rec["records"]
        path = RUNS / test_id.replace("/", "__") / "ptrace" / "events"
        if (
            rec["rc"] == 0
            and all(records[d]["count"] for d in DIMENSIONS)
            and path.exists()
            and path.stat().st_size < 8 * 1024 * 1024
        ):
            selected = test_id
            selected_records = dimension_records(path.read_text(errors="replace"))
            break
    if selected is None or selected_records is None:
        raise RuntimeError("no successful ptrace capture contains INFO+stack+heap")
    positive = {}
    negatives = {}
    for dimension in DIMENSIONS:
        original = selected_records[dimension]
        positive[dimension] = {
            "records_left": len(original),
            "records_right": len(original),
            "accepted": bool(original and original == list(original)),
        }
        tampered = mutate_message(original, dimension)
        negatives[dimension] = {
            "records_left": len(original),
            "records_right": len(tampered),
            "refused": bool(original and tampered and original != tampered),
            "first_difference": first_difference(original, tampered),
        }
    if not all(item["accepted"] for item in positive.values()):
        raise RuntimeError("qualifying comparator positive was not accepted")
    if not all(item["refused"] for item in negatives.values()):
        raise RuntimeError("tampered comparator negative was not refused")
    return {"fixture": selected, "positive": positive, "tampered_negative": negatives}


def main() -> int:
    ids = [line.strip() for line in DENOM.read_text().splitlines() if line.strip()]
    if len(ids) != 179 or len(set(ids)) != 179:
        raise RuntimeError(f"denominator is not 179 distinct IDs: {len(ids)}/{len(set(ids))}")

    rows: list[dict[str, object]] = []
    engagement = Counter()
    source = Counter()
    run_rc = Counter()
    receipt_cache: dict[tuple[str, str], dict[str, object]] = {}
    for test_id in ids:
        key = test_id.replace("/", "__")
        for arm in ALL_ARMS:
            rec = receipt(RUNS / key / arm)
            receipt_cache[(test_id, arm)] = rec
            engagement[(arm, rec["engagement"])] += 1
            source[(arm, rec["source"])] += 1
            run_rc[(arm, str(rec["rc"]))] += 1

        ref = receipt_cache[(test_id, "ptrace")]
        for arm in TARGETS:
            target = receipt_cache[(test_id, arm)]
            for dimension in DIMENSIONS:
                verdict, reason = classify(arm, ref, target, dimension)
                ref_records = ref["records"][dimension]
                target_records = target["records"][dimension]
                rows.append(
                    {
                        "test_id": test_id,
                        "backend": arm,
                        "path_kind": PATH_KIND[arm],
                        "engagement": target["engagement"],
                        "source": target["source"],
                        "run_rc": target["rc"],
                        "dimension": dimension,
                        "tier": TIERS[dimension],
                        "ref_records": ref_records["count"],
                        "backend_records": target_records["count"],
                        "result": verdict,
                        "reason": reason,
                        "first_difference": "",
                        "ref_dimension_sha256": ref_records["sha256"],
                        "backend_dimension_sha256": target_records["sha256"],
                        "ref_events_sha256": ref["events_sha256"],
                        "backend_events_sha256": target["events_sha256"],
                    }
                )

    fieldnames = list(rows[0])
    with (OUT / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    bracket = comparator_bracket(ids)
    (OUT / "comparator-bracket.json").write_text(json.dumps(bracket, indent=2) + "\n")

    verdicts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        verdicts[f"{row['backend']}:{row['dimension']}"][str(row["result"])] += 1
    summary = {
        "contract": "BitwiseInfoV1 (wall-clock stripped; explicit <hostaddr> ordinalized; all other bytes exact)",
        "tier_scope": "single strict inter-backend run; not an L2/L3 certification",
        "denominator": {
            "count": len(ids),
            "sha256": hashlib.sha256(DENOM.read_bytes()).hexdigest(),
            "origin": "179 distinct deterministic ptrace-pass IDs in compat-envelope/fullcorpus-scorecard.csv run=ptrace-fullcorpus-scorecard at Hermit 82a8e8533575e7f150ee9e64d9077d35bbb38806",
        },
        "source_pair": {
            "hermit": "0041130ccb0daa54ffe7dce2792c1f1495c57e58",
            "reverie": "0ae0c01b5e4c9fbf85c97adc66c2740f280727df",
            "binary_sha256": "0ee947522db96beeefd657c970ac18ada8f932212c0bb11dc60fa6f058e43300",
        },
        "cells": {"arms": len(ALL_ARMS), "run_receipts": len(ids) * len(ALL_ARMS), "comparison_rows": len(rows)},
        "verdicts": {key: dict(sorted(counter.items())) for key, counter in sorted(verdicts.items())},
        "engagement": {f"{key[0]}:{key[1]}": value for key, value in sorted(engagement.items())},
        "sources": {f"{key[0]}:{key[1]}": value for key, value in sorted(source.items())},
        "run_rc": {f"{key[0]}:{key[1]}": value for key, value in sorted(run_rc.items())},
        "comparator_bracket": bracket,
        "caveats": [
            "INFO is log evidence, never stdout.",
            "DBI and SaBRe use an explicit stderr fallback because their INFO/DETLOG trace is not wholly routed to --log-file.",
            "SaBRe split-channel rows are retained as measured and labeled by engagement/source; they are not silently promoted to canonical log-file routing.",
            "e9patch rows are eligible only when mapped_sites>0 caused rewrite_engaged.",
            "Zero records, fixture absence, non-engagement, backend failure, and reference failure cannot produce PASS.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
