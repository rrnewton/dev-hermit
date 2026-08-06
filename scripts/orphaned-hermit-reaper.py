#!/usr/bin/env python3
"""Find and reap proven runaway Hermit or test orphans.

The default is report-only.  ``--apply`` additionally requires the exact
acknowledgement flag, and signals through a pidfd after re-reading the process
identity.  It never uses a name-, user-, process-group-, or cgroup-wide kill:
an agent scope can contain both a live coordinator and an orphaned Hermit.

A process is eligible only when all of these independently observable facts
hold:

* its real uid is the caller's uid and its current parent is PID 1;
* its cgroup is a 3pai per-agent ``run-p*.scope`` for that same uid;
* its executable resolves below this dev-hermit workspace;
* it is either ``hermit ... run ...`` or a Cargo test executable under
  ``target/{debug,release}/deps``;
* age, consumed CPU time, and lifetime CPU/age ratio all exceed their bounds.

This is cleanup containment.  New ad-hoc runs should instead use
``scripts/hermit-box-run``, whose cgroup owns and reaps the complete process
tree at the resource boundary.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
import re
import select
import signal
import sys
from collections.abc import Callable, Iterable


DEFAULT_MIN_AGE_SECONDS = 15 * 60.0
DEFAULT_MIN_CPU_SECONDS = 10 * 60.0
DEFAULT_MIN_CORE_RATIO = 0.80
DEFAULT_GRACE_SECONDS = 5.0
CONFIRMATION = "signal-only-the-reported-pids"


class ProcReadError(RuntimeError):
    """A process could not be read consistently."""


@dataclasses.dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    start_ticks: int
    ppid: int
    uid: int
    state: str
    cpu_seconds: float
    age_seconds: float
    exe: Path
    argv: tuple[str, ...]
    cgroup: str

    @property
    def core_ratio(self) -> float:
        return self.cpu_seconds / self.age_seconds if self.age_seconds > 0 else 0.0


@dataclasses.dataclass(frozen=True)
class Bounds:
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS
    min_cpu_seconds: float = DEFAULT_MIN_CPU_SECONDS
    min_core_ratio: float = DEFAULT_MIN_CORE_RATIO


@dataclasses.dataclass(frozen=True)
class Decision:
    snapshot: ProcessSnapshot
    eligible: bool
    reasons: tuple[str, ...]
    kind: str | None = None


@dataclasses.dataclass(frozen=True)
class ReapResult:
    pid: int
    outcome: str
    term_sent: bool
    kill_sent: bool


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, ProcessLookupError) as exc:
        raise ProcReadError(f"process disappeared while reading {path}") from exc
    except OSError as exc:
        raise ProcReadError(f"cannot read {path}: {exc}") from exc


def _parse_stat(raw: str) -> tuple[str, int, int, int, int]:
    """Return state, ppid, utime, stime, starttime from proc(5) stat."""
    right = raw.rfind(")")
    if right < 0:
        raise ProcReadError("malformed /proc stat: missing command terminator")
    fields = raw[right + 2 :].split()
    # fields[0] is field 3 (state); indices below follow proc(5).
    if len(fields) < 20:
        raise ProcReadError("malformed /proc stat: too few fields")
    try:
        return fields[0], int(fields[1]), int(fields[11]), int(fields[12]), int(fields[19])
    except (ValueError, IndexError) as exc:
        raise ProcReadError("malformed numeric /proc stat field") from exc


def _parse_uid(status: str) -> int:
    for line in status.splitlines():
        if line.startswith("Uid:"):
            fields = line.split()
            if len(fields) >= 2:
                try:
                    return int(fields[1])
                except ValueError as exc:
                    raise ProcReadError("malformed real uid in /proc status") from exc
    raise ProcReadError("missing Uid field in /proc status")


def _parse_cgroup(raw: str) -> str:
    unified = [line.split(":", 2)[2] for line in raw.splitlines() if line.startswith("0::")]
    if len(unified) != 1 or not unified[0].startswith("/"):
        raise ProcReadError("missing or ambiguous unified cgroup membership")
    return unified[0]


class ProcReader:
    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        *,
        clock_ticks: int | None = None,
        uptime_seconds: Callable[[], float] | None = None,
    ) -> None:
        self.proc_root = proc_root
        self.clock_ticks = clock_ticks or os.sysconf("SC_CLK_TCK")
        self._uptime_seconds = uptime_seconds or self._read_uptime

    def _read_uptime(self) -> float:
        try:
            return float(_read_text(self.proc_root / "uptime").split()[0])
        except (ValueError, IndexError) as exc:
            raise ProcReadError("malformed /proc/uptime") from exc

    def pids(self) -> Iterable[int]:
        try:
            entries = self.proc_root.iterdir()
        except OSError as exc:
            raise ProcReadError(f"cannot enumerate {self.proc_root}: {exc}") from exc
        for entry in entries:
            if entry.name.isdigit():
                yield int(entry.name)

    def read(self, pid: int) -> ProcessSnapshot:
        base = self.proc_root / str(pid)
        state, ppid, utime, stime, start_ticks = _parse_stat(_read_text(base / "stat"))
        uid = _parse_uid(_read_text(base / "status"))
        cgroup = _parse_cgroup(_read_text(base / "cgroup"))
        try:
            exe_raw = os.readlink(base / "exe")
            cmdline_raw = (base / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError) as exc:
            raise ProcReadError(f"process {pid} disappeared while reading identity") from exc
        except OSError as exc:
            raise ProcReadError(f"cannot read process {pid} identity: {exc}") from exc
        if exe_raw.endswith(" (deleted)"):
            raise ProcReadError("executable has been deleted")
        argv = tuple(
            part.decode("utf-8", errors="surrogateescape")
            for part in cmdline_raw.rstrip(b"\0").split(b"\0")
            if part
        )
        uptime = self._uptime_seconds()
        age = uptime - (start_ticks / self.clock_ticks)
        if age < 0:
            raise ProcReadError("process start time is newer than system uptime")
        return ProcessSnapshot(
            pid=pid,
            start_ticks=start_ticks,
            ppid=ppid,
            uid=uid,
            state=state,
            cpu_seconds=(utime + stime) / self.clock_ticks,
            age_seconds=age,
            exe=Path(exe_raw),
            argv=argv,
            cgroup=cgroup,
        )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _agent_scope(uid: int, cgroup: str) -> bool:
    prefix = (
        rf"/user\.slice/user-{uid}\.slice/user@{uid}\.service/"
        rf"3pai_sandbox\.slice/run-p[0-9]+(?:-i[A-Za-z0-9_.:-]+)?\.scope"
    )
    return re.fullmatch(prefix + r"(?:/.*)?", cgroup) is not None


def _process_kind(snapshot: ProcessSnapshot, workspace: Path) -> str | None:
    if not _inside(snapshot.exe, workspace):
        return None
    if snapshot.exe.name == "hermit":
        # The known leak is a Hermit run, not a long-lived coordinator/helper.
        return "hermit-run" if "run" in snapshot.argv[1:] else None
    try:
        relative = snapshot.exe.relative_to(workspace)
    except ValueError:
        return None
    parts = relative.parts
    for index, part in enumerate(parts[:-2]):
        if part == "target" and parts[index + 1] in {"debug", "release"} and parts[index + 2] == "deps":
            return "cargo-test"
    return None


def decide(
    snapshot: ProcessSnapshot,
    *,
    caller_uid: int,
    workspace: Path,
    bounds: Bounds,
) -> Decision:
    reasons: list[str] = []
    if snapshot.uid != caller_uid:
        reasons.append(f"uid {snapshot.uid} != caller uid {caller_uid}")
    if snapshot.ppid != 1:
        reasons.append(f"ppid {snapshot.ppid} != 1")
    if snapshot.state == "Z":
        reasons.append("zombie has no executable work to reap")
    if not _agent_scope(caller_uid, snapshot.cgroup):
        reasons.append("not in this uid's 3pai per-agent scope")
    kind = _process_kind(snapshot, workspace)
    if kind is None:
        reasons.append("not an allowed workspace Hermit run or Cargo test executable")
    if snapshot.age_seconds < bounds.min_age_seconds:
        reasons.append(
            f"age {snapshot.age_seconds:.1f}s < {bounds.min_age_seconds:.1f}s"
        )
    if snapshot.cpu_seconds < bounds.min_cpu_seconds:
        reasons.append(
            f"cpu {snapshot.cpu_seconds:.1f}s < {bounds.min_cpu_seconds:.1f}s"
        )
    if snapshot.core_ratio < bounds.min_core_ratio:
        reasons.append(
            f"cpu/age {snapshot.core_ratio:.3f} < {bounds.min_core_ratio:.3f}"
        )
    return Decision(snapshot=snapshot, eligible=not reasons, reasons=tuple(reasons), kind=kind)


def same_identity(left: ProcessSnapshot, right: ProcessSnapshot) -> bool:
    """Facts which must not change between report and pidfd signal."""
    return (
        left.pid,
        left.start_ticks,
        left.uid,
        left.ppid,
        left.exe,
        left.cgroup,
        left.argv,
    ) == (
        right.pid,
        right.start_ticks,
        right.uid,
        right.ppid,
        right.exe,
        right.cgroup,
        right.argv,
    )


def _wait_pidfd(pidfd: int, seconds: float) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return bool(poller.poll(max(0, round(seconds * 1000))))


def reap(
    candidate: Decision,
    *,
    reader: ProcReader,
    caller_uid: int,
    workspace: Path,
    bounds: Bounds,
    grace_seconds: float,
    pidfd_open: Callable[[int], int] = os.pidfd_open,
    pidfd_send: Callable[[int, signal.Signals], None] = signal.pidfd_send_signal,
    wait_pidfd: Callable[[int, float], bool] = _wait_pidfd,
) -> ReapResult:
    """TERM then, if necessary, KILL one revalidated process via pidfd."""
    if not candidate.eligible:
        return ReapResult(candidate.snapshot.pid, "refused-ineligible", False, False)
    pid = candidate.snapshot.pid
    try:
        pidfd = pidfd_open(pid)
    except (AttributeError, OSError) as exc:
        return ReapResult(pid, f"refused-pidfd-unavailable:{exc}", False, False)
    try:
        try:
            current = reader.read(pid)
        except ProcReadError:
            return ReapResult(pid, "already-exited", False, False)
        current_decision = decide(
            current, caller_uid=caller_uid, workspace=workspace, bounds=bounds
        )
        if not current_decision.eligible or not same_identity(candidate.snapshot, current):
            return ReapResult(pid, "refused-identity-changed-before-term", False, False)
        try:
            pidfd_send(pidfd, signal.SIGTERM)
        except ProcessLookupError:
            return ReapResult(pid, "already-exited", False, False)
        if wait_pidfd(pidfd, grace_seconds):
            return ReapResult(pid, "terminated", True, False)
        try:
            after_term = reader.read(pid)
        except ProcReadError:
            return ReapResult(pid, "terminated", True, False)
        after_decision = decide(
            after_term, caller_uid=caller_uid, workspace=workspace, bounds=bounds
        )
        if not after_decision.eligible or not same_identity(candidate.snapshot, after_term):
            return ReapResult(pid, "refused-identity-changed-before-kill", True, False)
        try:
            pidfd_send(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            return ReapResult(pid, "terminated", True, False)
        return ReapResult(pid, "killed-after-grace", True, True)
    finally:
        os.close(pidfd)


def _snapshot_record(decision: Decision) -> dict[str, object]:
    item = decision.snapshot
    return {
        "pid": item.pid,
        "eligible": decision.eligible,
        "kind": decision.kind,
        "reasons": decision.reasons,
        "uid": item.uid,
        "ppid": item.ppid,
        "state": item.state,
        "start_ticks": item.start_ticks,
        "age_seconds": round(item.age_seconds, 3),
        "cpu_seconds": round(item.cpu_seconds, 3),
        "cpu_age_ratio": round(item.core_ratio, 6),
        "exe": str(item.exe),
        "argv": item.argv,
        "cgroup": item.cgroup,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="signal eligible PIDs")
    parser.add_argument(
        "--confirm",
        metavar="TEXT",
        help=f"required with --apply; must equal {CONFIRMATION!r}",
    )
    parser.add_argument("--pid", action="append", type=int, help="inspect only this PID; repeatable")
    parser.add_argument("--min-age-seconds", type=float, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--min-cpu-seconds", type=float, default=DEFAULT_MIN_CPU_SECONDS)
    parser.add_argument("--min-core-ratio", type=float, default=DEFAULT_MIN_CORE_RATIO)
    parser.add_argument("--grace-seconds", type=float, default=DEFAULT_GRACE_SECONDS)
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    parser.add_argument(
        "--verbose-refusals", action="store_true", help="include inspected non-candidates"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    numeric = (
        args.min_age_seconds,
        args.min_cpu_seconds,
        args.min_core_ratio,
        args.grace_seconds,
    )
    if any(value < 0 for value in numeric) or args.min_core_ratio > 1.0:
        _parser().error("bounds must be nonnegative and --min-core-ratio must be <= 1")
    if args.apply and args.confirm != CONFIRMATION:
        _parser().error(f"--apply requires --confirm {CONFIRMATION}")
    if args.apply and not args.pid:
        _parser().error("--apply requires at least one explicit --pid from a prior dry-run")

    workspace = Path(__file__).resolve().parent.parent
    reader = ProcReader()
    caller_uid = os.getuid()
    bounds = Bounds(args.min_age_seconds, args.min_cpu_seconds, args.min_core_ratio)
    requested = sorted(set(args.pid)) if args.pid else sorted(reader.pids())
    decisions: list[Decision] = []
    scan_errors: list[dict[str, object]] = []
    for pid in requested:
        try:
            snapshot = reader.read(pid)
        except ProcReadError as exc:
            # Races are normal in a /proc scan.  An explicitly named PID is not.
            if args.pid:
                scan_errors.append({"pid": pid, "error": str(exc)})
            continue
        decision = decide(
            snapshot, caller_uid=caller_uid, workspace=workspace, bounds=bounds
        )
        # Explicit PID inspection always reports a refusal.  In apply mode an
        # unreadable or ineligible member of the requested set blocks the
        # entire operation instead of silently producing a partial sweep.
        if decision.eligible or args.verbose_refusals or args.pid:
            decisions.append(decision)

    candidates = [decision for decision in decisions if decision.eligible]
    explicit_refusals = [decision for decision in decisions if not decision.eligible]
    results: list[ReapResult] = []
    if args.apply and not scan_errors and not explicit_refusals:
        for candidate in candidates:
            results.append(
                reap(
                    candidate,
                    reader=reader,
                    caller_uid=caller_uid,
                    workspace=workspace,
                    bounds=bounds,
                    grace_seconds=args.grace_seconds,
                )
            )

    payload = {
        "schema": "orphaned-hermit-reaper/v1",
        "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "workspace": str(workspace),
        "caller_uid": caller_uid,
        "bounds": dataclasses.asdict(bounds),
        "candidate_count": len(candidates),
        "processes": [_snapshot_record(decision) for decision in decisions],
        "scan_errors": scan_errors,
        "results": [dataclasses.asdict(result) for result in results],
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"orphaned-hermit-reaper: mode={payload['mode']} "
            f"candidates={len(candidates)} bounds={dataclasses.asdict(bounds)}"
        )
        for decision in decisions:
            item = decision.snapshot
            status = "CANDIDATE" if decision.eligible else "REFUSED"
            print(
                f"{status} pid={item.pid} kind={decision.kind or '-'} "
                f"age={item.age_seconds:.1f}s cpu={item.cpu_seconds:.1f}s "
                f"ratio={item.core_ratio:.3f} exe={item.exe} cgroup={item.cgroup}"
            )
            if decision.reasons:
                print("  reasons: " + "; ".join(decision.reasons))
        for error in scan_errors:
            print(f"ERROR pid={error['pid']}: {error['error']}", file=sys.stderr)
        for result in results:
            print(f"RESULT pid={result.pid} outcome={result.outcome}")

    if scan_errors:
        return 3
    if args.apply and explicit_refusals:
        return 4
    if args.apply and any(
        result.outcome not in {"terminated", "killed-after-grace", "already-exited"}
        for result in results
    ):
        return 4
    return 1 if candidates and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
