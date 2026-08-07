#!/usr/bin/env python3
"""Read-only, bounded-memory analysis of w21's fixed-179 detlog sweep."""

from collections import Counter
from itertools import zip_longest
import json
from pathlib import Path
import re


ROOT = Path("ignored/detlog-parity/current-0041130/corpus/runs")
IDS = tuple(
    line.strip()
    for line in Path("/tmp/detlog-canonical-179.txt").read_text().splitlines()
    if line.strip()
)
ARMS = ("dbi", "kvm", "sabre", "liteinst", "e9patch")
EXPECTED_ENGAGEMENT = {
    "dbi": "dbt_engaged",
    "kvm": "detcore_engaged",
    "sabre": "rewrite_engaged_split_channel",
    "liteinst": "preload_engaged",
    "e9patch": "rewrite_engaged",
}
TIMESTAMP = re.compile(
    rb"^(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    rb"[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]+|"
    rb"[0-9]+-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}.[0-9]+Z) +"
)
LEVEL = re.compile(rb"^(?:ERROR|WARN|INFO|DEBUG|TRACE) ")


def read_field(cell: Path, name: str) -> str:
    path = cell / name
    return path.read_text(errors="replace").strip() if path.exists() else ""


def selected_messages(path: Path, signal: str, bare: bool):
    """Yield canonical messages, preserving everything except wall-clock prefix."""
    current = None
    if not path.exists():
        return
    with path.open("rb") as stream:
        for raw in stream:
            line = raw.rstrip(b"\r\n")
            match = TIMESTAMP.match(line)
            if match:
                if current is not None:
                    message = b"\n".join(current).strip()
                    if message.startswith(b"INFO ") and selected(message, signal):
                        yield message
                current = [line[match.end():]]
            elif bare and LEVEL.match(line):
                if current is not None:
                    message = b"\n".join(current).strip()
                    if message.startswith(b"INFO ") and selected(message, signal):
                        yield message
                current = [line]
            elif current is not None:
                current.append(line)
    if current is not None:
        message = b"\n".join(current).strip()
        if message.startswith(b"INFO ") and selected(message, signal):
            yield message


def selected(message: bytes, signal: str) -> bool:
    if signal == "info_all_on":
        return True
    return (b"[" + signal.encode() + b"]->") in message


def compare(left: Path, right: Path, signal: str, left_bare=False, right_bare=False):
    left_count = right_count = 0
    differs = False
    sentinel = object()
    for lhs, rhs in zip_longest(
        selected_messages(left, signal, left_bare),
        selected_messages(right, signal, right_bare),
        fillvalue=sentinel,
    ):
        if lhs is not sentinel:
            left_count += 1
        if rhs is not sentinel:
            right_count += 1
        if lhs is sentinel or rhs is sentinel or lhs != rhs:
            differs = True
    return left_count, right_count, differs


def source_is_bare(cell: Path) -> bool:
    return read_field(cell, "source") == "stderr_fallback"


def classify(ident: str, arm: str, signal: str) -> str:
    key = ident.replace("/", "__")
    ref = ROOT / key / "ptrace"
    control = ROOT / key / "ptrace-control"
    candidate = ROOT / key / arm
    if any(read_field(cell, "source") == "fixture_missing" for cell in (ref, control, candidate)):
        return "FIXTURE_MISSING"
    if not read_field(ref, "rc") or not read_field(control, "rc"):
        return "REF_INCOMPLETE"
    if read_field(ref, "rc") != "0" or read_field(control, "rc") != "0":
        return "REF_RUN_FAIL"
    if read_field(ref, "engagement") != "detcore_engaged" or read_field(control, "engagement") != "detcore_engaged":
        return "REF_NOT_ENGAGED"

    ref_count, control_count, control_differs = compare(
        ref / "events", control / "events", signal
    )
    if ref_count == 0 or control_count == 0:
        return "REF_CONTROL_NO_RESULT"
    if control_differs:
        return "REF_CONTROL_DIVERGE"

    if not read_field(candidate, "rc"):
        return "CAND_INCOMPLETE"
    if read_field(candidate, "rc") != "0":
        return "CAND_RUN_FAIL"
    if read_field(candidate, "engagement") != EXPECTED_ENGAGEMENT[arm]:
        return "CAND_NOT_ENGAGED"
    if (candidate / "stdout").read_bytes() != (ref / "stdout").read_bytes():
        return "STDOUT_DIVERGE"

    _, candidate_count, candidate_differs = compare(
        ref / "events",
        candidate / "events",
        signal,
        right_bare=source_is_bare(candidate),
    )
    if candidate_count == 0:
        return "CAND_NO_RESULT"
    if candidate_differs:
        return "DIVERGE"
    return "PASS"


def main():
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for signal in ("info_all_on", "stack", "heap"):
            result[arm][signal] = dict(
                sorted(Counter(classify(ident, arm, signal) for ident in IDS).items())
            )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
