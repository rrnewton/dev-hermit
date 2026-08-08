#!/usr/bin/env python3
"""Read-only Herdr observer for one ci-hub-owned validation service."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from run_registry import update_record


TERMINAL_STATES = frozenset(("failed", "inactive"))


def service_properties(unit: str) -> dict[str, str] | None:
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=ActiveState",
            "--property=SubState",
            "--property=ExecMainStatus",
            "--property=Result",
            "--property=MainPID",
            "--no-pager",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def proc_ppids(proc_root: Path = Path("/proc")) -> dict[int, int]:
    result: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text()
            rest = raw[raw.rfind(")") + 2 :].split()
            result[int(entry.name)] = int(rest[1])
        except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
            continue
    return result


def descendants(root_pid: int, relationships: dict[int, int]) -> set[int]:
    found = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in relationships.items():
            if ppid in found and pid not in found:
                found.add(pid)
                changed = True
    return found


def safe_ci_cgroups(root_pid: int, proc_root: Path = Path("/proc")) -> set[str]:
    groups: set[str] = set()
    for pid in descendants(root_pid, proc_ppids(proc_root)):
        try:
            for line in (proc_root / str(pid) / "cgroup").read_text().splitlines():
                path = line.rsplit(":", 1)[-1]
                if "safe-ci-" in path:
                    groups.add(path)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return groups


def process_cgroups(pid: int, proc_root: Path = Path("/proc")) -> set[str]:
    try:
        return {
            line.rsplit(":", 1)[-1]
            for line in (proc_root / str(pid) / "cgroup").read_text().splitlines()
        }
    except (FileNotFoundError, PermissionError, OSError):
        return set()


def live_cgroup_pids(
    candidates: dict[int, set[str]], proc_root: Path = Path("/proc")
) -> set[int]:
    """Return PIDs still alive in the exact cgroup observed for that PID."""

    live: set[int] = set()
    for pid, observed_groups in candidates.items():
        current_groups = process_cgroups(pid, proc_root)
        if observed_groups and current_groups == observed_groups:
            live.add(pid)
    return live


def emit_log(path: Path, offset: int) -> int:
    try:
        with path.open(errors="replace") as stream:
            stream.seek(offset)
            data = stream.read()
            offset = stream.tell()
    except (FileNotFoundError, OSError):
        return offset
    if data:
        print(data, end="", flush=True)
    return offset


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--unit", required=True)
    result.add_argument("--target", required=True)
    result.add_argument("--checkout", required=True, type=Path)
    result.add_argument("--log", required=True, type=Path)
    result.add_argument("--record", required=True, type=Path)
    result.add_argument("--pr", type=int)
    result.add_argument("--poll-seconds", type=float, default=1.0)
    result.add_argument("--appearance-seconds", type=float, default=30.0)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    identity = f"PR #{args.pr}" if args.pr is not None else args.unit
    print(
        f"CI-HUB VALIDATE PANE {identity}\n"
        f"HEAD {args.target}\nUNIT {args.unit}\nCHECKOUT {args.checkout}\nLOG {args.log}",
        flush=True,
    )
    offset = 0
    seen = False
    observed_pids: dict[int, set[str]] = {}
    observed_groups: set[str] = set()
    deadline = time.monotonic() + args.appearance_seconds
    final: dict[str, str] | None = None
    while True:
        previous_offset = offset
        offset = emit_log(args.log, offset)
        log_advanced = offset > previous_offset
        properties = service_properties(args.unit)
        if properties is None:
            if not seen and time.monotonic() < deadline:
                time.sleep(args.poll_seconds)
                continue
            # One failed systemctl query is only an observer visibility miss.
            # Re-query after a poll interval, then corroborate against process
            # cgroups and the append-only durable log before deciding whether
            # this observer should stop. None of those observations authorizes
            # the observer to publish terminal run state.
            time.sleep(args.poll_seconds)
            properties = service_properties(args.unit)
            if properties is None:
                after_retry = emit_log(args.log, offset)
                log_advanced = log_advanced or after_retry > offset
                offset = after_retry
                live_pids = live_cgroup_pids(observed_pids)
                if live_pids or log_advanced:
                    proof = ",".join(str(pid) for pid in sorted(live_pids)) or "none"
                    print(
                        "VISIBILITY-MISS: service query failed twice; "
                        f"live_cgroup_pids={proof} log_advanced={str(log_advanced).lower()}; "
                        "run remains nonterminal",
                        flush=True,
                    )
                    continue
                print(
                    "VISIBILITY-LOST: service query failed twice with no live observed "
                    "cgroup PID or advancing log; observer exits without publishing "
                    "terminal run state",
                    flush=True,
                )
                break
        seen = True
        try:
            main_pid = int(properties.get("MainPID", "0"))
        except ValueError:
            main_pid = 0
        if main_pid > 0:
            for pid in descendants(main_pid, proc_ppids()):
                groups = process_cgroups(pid)
                if groups:
                    observed_pids[pid] = groups
            new_groups = safe_ci_cgroups(main_pid) - observed_groups
            for group in sorted(new_groups):
                print(f"BOX-CGROUP {group}", flush=True)
            observed_groups.update(new_groups)
        if properties.get("ActiveState") in TERMINAL_STATES:
            final = properties
            break
        time.sleep(args.poll_seconds)

    offset = emit_log(args.log, offset)
    status: int | None = None
    observed_state = "visibility-lost"
    if final is not None:
        observed_state = final.get("ActiveState", "terminal")
        try:
            status = int(final.get("ExecMainStatus", ""))
        except ValueError:
            pass
    # This pane is an observer, not the systemd-owned producer. It may append
    # cgroup evidence, but it must never write state/result/exit_code/finished_at.
    # In particular, its own loss of systemctl visibility is not a run result.
    update_record(
        args.record,
        observed_safe_ci_cgroups=sorted(observed_groups),
    )
    proof = ",".join(sorted(observed_groups)) or "UNOBSERVED"
    print(
        f"\nCI-HUB VALIDATE OBSERVER FINISHED observed_state={observed_state} "
        f"observed_exit={status} box_cgroup={proof}",
        flush=True,
    )
    return status if isinstance(status, int) and 0 <= status <= 125 else 1


if __name__ == "__main__":
    raise SystemExit(main())
