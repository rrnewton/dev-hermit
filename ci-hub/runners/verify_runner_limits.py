#!/usr/bin/env python3
"""Fail closed unless a runner container has the requested cgroup-v2 limits.

Run from inside the container (start-runner.sh execs this before ./run.sh,
and start-runner.sh --verify-only execs it against an already-running
container). It checks four properties before the runner is allowed to start
polling GitHub for jobs:

  - cpu.max matches the requested CPU count exactly;
  - memory.max matches the requested RAM cap (within one page);
  - memory.swap.max is 0, so a runaway is OOM-killed at the RAM cap instead
    of pushed into host swap;
  - memory.high is "max", so there is no hidden soft-cap throttle below the
    advertised hard cap.

A container without a real, enforced resource cap can take down the whole
host if a job runs away (this matters especially for hermit: chaos-mode /
fuzzing-style runs are exactly the kind of workload that can spin CPU or
allocate unboundedly).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


_SIZE_RE = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<suffix>[kmgtpe]?i?b?)?$", re.I)
_SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "ki": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mi": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gi": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "ti": 1024**4,
    "tib": 1024**4,
    "p": 1024**5,
    "pb": 1024**5,
    "pi": 1024**5,
    "pib": 1024**5,
    "e": 1024**6,
    "eb": 1024**6,
    "ei": 1024**6,
    "eib": 1024**6,
}


@dataclass(frozen=True)
class AuditResult:
    passed: bool
    lines: tuple[str, ...]


def parse_memory_spec(spec: str) -> int:
    """Parse the binary-size spellings accepted by the runner launcher."""
    match = _SIZE_RE.fullmatch(spec.strip())
    if match is None:
        raise ValueError(f"invalid memory size: {spec!r}")
    suffix = (match.group("suffix") or "").lower()
    multiplier = _SIZE_MULTIPLIERS.get(suffix)
    if multiplier is None:
        raise ValueError(f"unsupported memory suffix in {spec!r}")
    value = Decimal(match.group("number")) * multiplier
    if value != value.to_integral_value() or value <= 0:
        raise ValueError(f"memory size must resolve to positive whole bytes: {spec!r}")
    return int(value)


def parse_cpu_count(spec: str) -> Decimal:
    try:
        value = Decimal(spec.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid CPU count: {spec!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"CPU count must be positive and finite: {spec!r}")
    return value


def current_cgroup_dir(cgroup_root: Path, proc_self_cgroup: Path) -> Path:
    """Resolve this process's unified cgroup without assuming namespace depth."""
    for line in proc_self_cgroup.read_text().splitlines():
        if line.startswith("0::"):
            relative = line[3:].lstrip("/")
            parts = Path(relative).parts
            if any(part == ".." for part in parts):
                raise ValueError(f"unsafe cgroup path in {proc_self_cgroup}: {line!r}")
            return cgroup_root.joinpath(*parts)
    raise ValueError(f"unified cgroup-v2 entry missing from {proc_self_cgroup}")


def _read(group: Path, name: str) -> str | None:
    try:
        return (group / name).read_text().strip()
    except OSError:
        return None


def audit_limits(group: Path, expected_cpus: Decimal, expected_memory: int) -> AuditResult:
    """Audit exact CPU/RAM caps plus the swapless, hard-cap-only policy."""
    lines: list[str] = []
    passed = True

    cpu_max = _read(group, "cpu.max")
    cpu_ok = False
    try:
        quota_text, period_text = (cpu_max or "").split()
        quota = Decimal(quota_text)
        period = Decimal(period_text)
        cpu_ok = quota > 0 and period > 0 and quota / period == expected_cpus
    except (InvalidOperation, ValueError):
        cpu_ok = False
    lines.append(
        f"cpu.max={cpu_max or 'UNREADABLE'} "
        f"({'PASS' if cpu_ok else 'FAIL'}; expected {expected_cpus} CPU(s))"
    )
    passed = passed and cpu_ok

    memory_max = _read(group, "memory.max")
    memory_ok = False
    try:
        actual_memory = int(memory_max or "")
        page_size = os.sysconf("SC_PAGE_SIZE")
        memory_ok = (
            actual_memory <= expected_memory
            and expected_memory - actual_memory < page_size
        )
    except (OSError, ValueError):
        memory_ok = False
    lines.append(
        f"memory.max={memory_max or 'UNREADABLE'} "
        f"({'PASS' if memory_ok else 'FAIL'}; expected {expected_memory} bytes)"
    )
    passed = passed and memory_ok

    memory_swap_max = _read(group, "memory.swap.max")
    swap_ok = memory_swap_max == "0"
    lines.append(
        f"memory.swap.max={memory_swap_max or 'UNREADABLE'} "
        f"({'PASS' if swap_ok else 'FAIL'}; expected 0)"
    )
    passed = passed and swap_ok

    memory_high = _read(group, "memory.high")
    hard_cap_ok = memory_high == "max"
    lines.append(
        f"memory.high={memory_high or 'UNREADABLE'} "
        f"({'PASS' if hard_cap_ok else 'FAIL'}; expected max)"
    )
    passed = passed and hard_cap_ok

    return AuditResult(passed=passed, lines=tuple(lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-cpus", required=True)
    parser.add_argument("--expected-memory", required=True)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    parser.add_argument("--proc-self-cgroup", type=Path, default=Path("/proc/self/cgroup"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        expected_cpus = parse_cpu_count(args.expected_cpus)
        expected_memory = parse_memory_spec(args.expected_memory)
        group = current_cgroup_dir(args.cgroup_root, args.proc_self_cgroup)
        result = audit_limits(group, expected_cpus, expected_memory)
    except (OSError, ValueError) as exc:
        print(f"CI runner cgroup audit: FAIL ({exc})", file=sys.stderr)
        return 1

    print(f"CI runner cgroup audit for {group}:")
    for line in result.lines:
        print(f"  {line}")
    if result.passed:
        print("CI runner cgroup audit: PASS — CPU/RAM hard caps are swapless and enforced.")
        return 0
    print(
        "CI runner cgroup audit: FAIL — refusing to poll GitHub with advisory-only limits.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
