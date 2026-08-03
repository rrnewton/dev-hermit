#!/usr/bin/env python3
"""Arm and watch mandatory verification after a speculative/admin land."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci-hub/history"))

import obligations

DEFAULT_REPO = "rrnewton/hermit"
DEFAULT_WORKFLOW = "CI (GitHub-managed portable)"
DEFAULT_WORKFLOW_FILE = "ci-portable.yml"
DEFAULT_POLL_SECONDS = 15
DEFAULT_GITHUB_WAIT_SECONDS = 120
TERMINAL_VERIFICATION_STATES = frozenset(("green", "red", "error"))


class ProtocolError(RuntimeError):
    """The dual-verification protocol could not be armed or polled."""


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def estimate_local_validate_cost(
    ledger_path: Path = ROOT / "ignored/validate-run-ledger.jsonl",
) -> dict[str, Any]:
    samples: list[tuple[float, float]] = []
    if ledger_path.exists():
        try:
            with ledger_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        wall = float(record.get("real_seconds") or 0)
                        cpu = float(record.get("user_seconds") or 0) + float(
                            record.get("sys_seconds") or 0
                        )
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if record.get("profile") == "full" and wall > 0 and cpu >= 0:
                        samples.append((wall, cpu))
        except OSError:
            samples = []
    samples = samples[-50:]
    if samples:
        wall = _percentile([x[0] for x in samples], 0.9)
        cpu = _percentile([x[1] for x in samples], 0.9)
        basis = (
            f"derived from p90 of the last {len(samples)} usable successful "
            "full-profile validate ledger row(s), window capped at 50, "
            "mixed host/cache states"
        )
        return {
            "kind": "derived",
            "wall_seconds": round(wall, 3),
            "cpu_seconds": round(cpu, 3),
            "basis": basis,
        }
    return {
        "kind": "unknown",
        "wall_seconds": None,
        "cpu_seconds": None,
        "basis": "not measured: no usable successful full-profile validate ledger rows",
    }


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    capture_output: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=True,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
        else:
            detail = str(error)
        raise ProtocolError(f"command failed: {' '.join(command)}: {detail}") from error


def resolve_landed_sha(source: Path, requested: str) -> str:
    if not source.is_dir():
        raise ProtocolError(f"Hermit source checkout is missing: {source}")
    _run(("with-proxy", "git", "-C", str(source), "fetch", "origin", "main"), check=True)
    resolved = _run(
        ("git", "-C", str(source), "rev-parse", "--verify", f"{requested}^{{commit}}"),
        check=True,
    ).stdout.strip().lower()
    if not obligations.SHA_RE.fullmatch(resolved):
        raise ProtocolError(f"cannot resolve a full commit SHA from {requested!r}")
    ancestry = _run(
        (
            "git",
            "-C",
            str(source),
            "merge-base",
            "--is-ancestor",
            resolved,
            "origin/main",
        ),
        check=False,
    )
    if ancestry.returncode != 0:
        raise ProtocolError(f"{resolved} is not reachable from fetched origin/main")
    return resolved


def _parse_github_runs(output: str, sha: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise ProtocolError("gh run list returned invalid JSON") from error
    if not isinstance(payload, list):
        raise ProtocolError("gh run list returned a non-list payload")
    runs = [
        run
        for run in payload
        if isinstance(run, dict)
        and str(run.get("headSha", "")).lower() == sha
        and run.get("workflowName") == DEFAULT_WORKFLOW
    ]
    return sorted(
        runs,
        key=lambda run: (str(run.get("createdAt", "")), int(run.get("databaseId") or 0)),
        reverse=True,
    )


def github_runs(repo: str, sha: str) -> list[dict[str, Any]]:
    result = _run(
        (
            "with-proxy",
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--commit",
            sha,
            "--workflow",
            DEFAULT_WORKFLOW_FILE,
            "--limit",
            "20",
            "--json",
            "databaseId,status,conclusion,createdAt,startedAt,updatedAt,url,event,headSha,workflowName",
        ),
        check=True,
    )
    return _parse_github_runs(result.stdout, sha)


def github_main_sha(repo: str) -> str:
    result = _run(
        ("with-proxy", "gh", "api", f"repos/{repo}/commits/main", "--jq", ".sha"),
        check=True,
    )
    sha = result.stdout.strip().lower()
    if not obligations.SHA_RE.fullmatch(sha):
        raise ProtocolError(f"GitHub returned invalid main SHA {sha!r}")
    return sha


def _github_state(run: Mapping[str, Any]) -> str:
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status != "completed":
        return "running"
    return "green" if conclusion in {"success", "neutral"} else "red"


def _github_patch(run: Mapping[str, Any]) -> dict[str, Any]:
    state = _github_state(run)
    return {
        "github": {
            "state": state,
            "started_at": run.get("startedAt") or run.get("createdAt"),
            "finished_at": run.get("updatedAt") if state in TERMINAL_VERIFICATION_STATES else None,
            "run_ids": [int(run["databaseId"])],
            "urls": [str(run.get("url") or "")],
            "workflow_name": DEFAULT_WORKFLOW,
            "event": run.get("event"),
            "last_poll_error": None,
        }
    }


def ensure_github_verification(
    obligation_id: str,
    *,
    store_path: Path,
    wait_seconds: int = DEFAULT_GITHUB_WAIT_SECONDS,
    poll_seconds: int = 5,
    allow_dispatch: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    repo, sha = record["repo"], record["landed_sha"]
    deadline = time.monotonic() + wait_seconds
    dispatched = False
    while True:
        runs = github_runs(repo, sha)
        if runs:
            updated = obligations.transition(
                obligation_id, "github-observed", _github_patch(runs[0]), store_path
            )
            return evaluate_obligation(obligation_id, store_path=store_path)
        if time.monotonic() >= deadline:
            break
        if allow_dispatch and not dispatched and deadline - time.monotonic() <= max(0, wait_seconds - 30):
            if github_main_sha(repo) == sha:
                _run(
                    (
                        "with-proxy",
                        "gh",
                        "workflow",
                        "run",
                        DEFAULT_WORKFLOW_FILE,
                        "-R",
                        repo,
                        "--ref",
                        "main",
                    ),
                    check=True,
                )
                dispatched = True
        sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

    summary = f"no {DEFAULT_WORKFLOW!r} run appeared for exact SHA {sha} within {wait_seconds}s"
    obligations.transition(
        obligation_id,
        "github-arm-failed",
        {
            "github": {
                "state": "error",
                "finished_at": obligations.utc_now(),
                "last_poll_error": summary,
            }
        },
        store_path,
    )
    return evaluate_obligation(obligation_id, store_path=store_path)


def _failure_details(record: Mapping[str, Any]) -> tuple[str, str]:
    failed: list[tuple[str, Mapping[str, Any]]] = []
    for source in ("local", "github"):
        verification = record[source]
        if verification.get("state") in {"red", "error"}:
            failed.append((source, verification))
    if not failed:
        raise ProtocolError("failure details requested for a non-failing obligation")
    failed.sort(key=lambda pair: str(pair[1].get("finished_at") or ""))
    source, verification = failed[0]
    if source == "local":
        summary = (
            f"local validate state={verification.get('state')} "
            f"exit={verification.get('exit_code')} log={verification.get('log_path')}"
        )
    else:
        summary = (
            f"GitHub {verification.get('workflow_name') or DEFAULT_WORKFLOW} "
            f"state={verification.get('state')} urls={','.join(verification.get('urls') or [])}"
        )
    return source, summary


def remediation_recommendation(record: Mapping[str, Any], main_sha: str | None) -> dict[str, str]:
    if main_sha == record["landed_sha"] or main_sha is None:
        return {
            "action": "revert",
            "reason": (
                "the failing speculative land is still the main tip; revert immediately to "
                "restore the last green base, then fix forward on a reviewed branch"
                if main_sha is not None
                else "main-tip identity is unavailable; conservatively prepare an immediate revert"
            ),
        }
    return {
        "action": "fix-forward",
        "reason": (
            f"main has advanced to {main_sha}; repair the current tip immediately rather than "
            "blindly reverting through later lands"
        ),
    }


def evaluate_obligation(
    obligation_id: str,
    *,
    store_path: Path,
    main_sha: str | None = None,
) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    states = (record["local"]["state"], record["github"]["state"])
    now = obligations.utc_now()
    first_terminal_at = record.get("first_terminal_at")
    if first_terminal_at is None and any(state in TERMINAL_VERIFICATION_STATES for state in states):
        first_terminal_at = now

    if any(state in {"red", "error"} for state in states):
        source, summary = _failure_details(record)
        if main_sha is None:
            try:
                main_sha = github_main_sha(record["repo"])
            except ProtocolError:
                main_sha = None
        recommendation = remediation_recommendation(record, main_sha)
        raised_now = (
            record.get("overall_state") != "remediation_required"
            or record.get("failure_summary") != summary
        )
        if raised_now:
            record = obligations.transition(
                obligation_id,
                "remediation-required",
                {
                    "overall_state": "remediation_required",
                    "first_terminal_at": first_terminal_at,
                    "failure_source": source,
                    "failure_summary": summary,
                    "recommendation": recommendation,
                    "alert": {"state": "raised", "raised_at": now},
                    "remediation": {"state": "recommended", "kind": recommendation["action"]},
                },
                store_path,
            )
        if raised_now:
            print(
                f"HARD WARNING: obligation {obligation_id} failed: {summary}; "
                f"recommendation={recommendation['action']} ({recommendation['reason']})",
                file=sys.stderr,
                flush=True,
            )
        return record

    if states == ("green", "green"):
        if record.get("overall_state") != "satisfied":
            record = obligations.transition(
                obligation_id,
                "satisfied",
                {
                    "overall_state": "satisfied",
                    "first_terminal_at": first_terminal_at,
                    "satisfied_at": now,
                    "remediation": {"state": "not_required"},
                },
                store_path,
            )
        return record

    if first_terminal_at != record.get("first_terminal_at"):
        record = obligations.transition(
            obligation_id,
            "verification-progress",
            {"first_terminal_at": first_terminal_at},
            store_path,
        )
    return record


def _spawn_detached(arguments: Sequence[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as output:
        process = subprocess.Popen(
            [
                "nohup",
                "setsid",
                "--fork",
                "--wait",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def _local_run(obligation_id: str, source: Path, store_path: Path) -> int:
    record = obligations.get_record(obligation_id, store_path)
    workspace = ROOT / "ignored/ci-hub/obligations" / obligation_id
    checkout = workspace / "hermit"
    log_path = Path(record["local"]["log_path"])
    cost = record["local"]["cost"]
    estimate = cost["estimate"]
    cost_path = Path(cost["record_path"])
    started = time.monotonic()
    exit_code = 2
    state = "error"
    print(
        f"ci-hub obligation={obligation_id} repo={record['repo']} sha={record['landed_sha']} "
        f"started_at={obligations.utc_now()}",
        flush=True,
    )
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        if checkout.exists():
            raise ProtocolError(f"isolated validation checkout already exists: {checkout}")
        _run(("git", "clone", "--shared", "--no-checkout", str(source), str(checkout)), check=True, capture_output=False)
        _run(
            ("git", "-C", str(checkout), "checkout", "--detach", record["landed_sha"]),
            check=True,
            capture_output=False,
        )
        _run(
            (
                "with-proxy",
                "git",
                "-C",
                str(checkout),
                "submodule",
                "update",
                "--init",
                "--recursive",
            ),
            check=True,
            capture_output=False,
        )
        actual = _run(("git", "-C", str(checkout), "rev-parse", "HEAD"), check=True).stdout.strip()
        if actual != record["landed_sha"]:
            raise ProtocolError(f"isolated checkout is {actual}, expected {record['landed_sha']}")
        environment = os.environ.copy()
        environment.update(
            {
                "CI_HUB_OBLIGATION_ID": obligation_id,
                "HERMIT_VALIDATE_LEDGER": str(ROOT / "ignored/validate-run-ledger.jsonl"),
                "VALIDATE_LABEL_PR": "0",
            }
        )
        estimate_arguments = (
            (
                "--estimate-wall-seconds",
                str(estimate["wall_seconds"]),
                "--estimate-cpu-seconds",
                str(estimate["cpu_seconds"]),
            )
            if estimate["kind"] == "derived"
            else ("--estimate-unknown",)
        )
        result = _run(
            (
                str(ROOT / "ci-hub/bin/tool-cost"),
                "--tool",
                "speculative-land/local-validate",
                *estimate_arguments,
                "--basis",
                str(estimate["basis"]),
                "--actual-json",
                str(cost_path),
                "--",
                str(checkout / "validate.sh"),
                "--no-label-pr",
            ),
            cwd=checkout,
            check=False,
            capture_output=False,
            env=environment,
        )
        exit_code = result.returncode
        state = "green" if exit_code == 0 else "red"
        try:
            measured_cost = json.loads(cost_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProtocolError(f"tool-cost result is unavailable: {cost_path}: {error}") from error
        measured_cost["record_path"] = str(cost_path)
    except ProtocolError as error:
        print(f"local verification setup failed: {error}", file=sys.stderr, flush=True)
        measured_cost = cost
    finished_at = obligations.utc_now()
    obligations.transition(
        obligation_id,
        "local-completed",
        {
            "local": {
                "state": state,
                "finished_at": finished_at,
                "exit_code": exit_code,
                "duration_seconds": round(time.monotonic() - started, 3),
                "log_path": str(log_path),
                "cost": measured_cost,
            }
        },
        store_path,
    )
    record = evaluate_obligation(obligation_id, store_path=store_path)
    return 0 if record["local"]["state"] == "green" else 1


def _pid_alive(raw_pid: object) -> bool:
    if not isinstance(raw_pid, int) or raw_pid <= 0:
        return False
    try:
        os.kill(raw_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def poll_obligation(obligation_id: str, store_path: Path) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    if record["overall_state"] in obligations.CLOSED_STATES:
        return record
    if record["github"]["state"] not in TERMINAL_VERIFICATION_STATES:
        try:
            runs = github_runs(record["repo"], record["landed_sha"])
            if runs:
                record = obligations.transition(
                    obligation_id, "github-polled", _github_patch(runs[0]), store_path
                )
        except ProtocolError as error:
            record = obligations.transition(
                obligation_id,
                "github-poll-error",
                {"github": {"last_poll_error": str(error)}},
                store_path,
            )
    if record["local"]["state"] == "running" and not _pid_alive(record["local"].get("pid")):
        record = obligations.get_record(obligation_id, store_path)
        if record["local"]["state"] == "running":
            record = obligations.transition(
                obligation_id,
                "local-runner-lost",
                {
                    "local": {
                        "state": "error",
                        "finished_at": obligations.utc_now(),
                        "exit_code": 2,
                    }
                },
                store_path,
            )
    return evaluate_obligation(obligation_id, store_path=store_path)


def _watch_complete(record: Mapping[str, Any]) -> bool:
    if record["overall_state"] in obligations.CLOSED_STATES:
        return True
    return all(record[source]["state"] in TERMINAL_VERIFICATION_STATES for source in ("local", "github"))


def watch(
    *,
    store_path: Path,
    obligation_id: str | None,
    once: bool,
    poll_seconds: int,
) -> int:
    while True:
        records = (
            [obligations.get_record(obligation_id, store_path)]
            if obligation_id
            else obligations.unresolved_records(store_path)
        )
        updated = [poll_obligation(record["obligation_id"], store_path) for record in records]
        if once or all(_watch_complete(record) for record in updated):
            if any(record["overall_state"] == "remediation_required" for record in updated):
                return 2
            return 1 if any(record["overall_state"] not in obligations.CLOSED_STATES for record in updated) else 0
        time.sleep(poll_seconds)


def _summary_line(record: Mapping[str, Any]) -> str:
    recommendation = record.get("recommendation") or {}
    action = recommendation.get("action", "-") if isinstance(recommendation, Mapping) else "-"
    return (
        f"{record['obligation_id']} {record['repo']}@{record['landed_sha'][:12]} "
        f"overall={record['overall_state']} local={record['local']['state']} "
        f"github={record['github']['state']} recommendation={action}"
    )


def print_status(store_path: Path, *, include_closed: bool, json_output: bool, gate: bool) -> int:
    records = list(obligations.latest_records(store_path).values())
    if not include_closed:
        records = [record for record in records if record["overall_state"] not in obligations.CLOSED_STATES]
    records.sort(key=lambda record: (record["opened_at"], record["obligation_id"]))
    unresolved = [record for record in records if record["overall_state"] not in obligations.CLOSED_STATES]
    remediation = [record for record in unresolved if record["overall_state"] == "remediation_required"]
    if json_output:
        print(json.dumps({"obligations": records}, sort_keys=True))
    elif gate:
        state = "remediation-required" if remediation else "open" if unresolved else "clear"
        print(f"state={state}")
        print(f"count={len(unresolved)}")
        print(f"remediation_count={len(remediation)}")
        print("ids=" + (",".join(record["obligation_id"] for record in unresolved) or "none"))
        print(
            "summary="
            + (
                ";".join(_summary_line(record) for record in unresolved)
                if unresolved
                else "no-open-speculative-land-obligations"
            )
        )
    else:
        heading = (
            "Speculative-land obligations: REMEDIATION REQUIRED"
            if remediation
            else "Speculative-land obligations: OPEN"
            if unresolved
            else "Speculative-land obligations: CLEAR"
        )
        print(heading)
        for record in records:
            print("  " + _summary_line(record))
            if record.get("failure_summary"):
                print(f"    failure: {record['failure_summary']}")
    return 2 if remediation else 1 if unresolved else 0


def arm(args: argparse.Namespace) -> int:
    store_path = args.store.expanduser().resolve()
    source = args.source.expanduser().resolve()
    sha = resolve_landed_sha(source, args.sha)
    try:
        record = obligations.create_obligation(
            repo=args.repo,
            landed_sha=sha,
            land_mode=args.land_mode,
            verification_scope="total",
            actor=args.actor,
            path=store_path,
        )
    except obligations.DuplicateOpenObligation as error:
        record = error.record
        print(str(error), file=sys.stderr)
        return print_status(store_path, include_closed=False, json_output=False, gate=False)

    obligation_id = record["obligation_id"]
    workspace = ROOT / "ignored/ci-hub/obligations" / obligation_id
    local_log = workspace / "local-validate.log"
    local_cost = workspace / "local-validate-cost.json"
    watcher_log = workspace / "watcher.log"
    cost_estimate = estimate_local_validate_cost()
    obligations.transition(
        obligation_id,
        "local-prepared",
        {
            "local": {
                "state": "starting",
                "started_at": obligations.utc_now(),
                "log_path": str(local_log),
                "workspace": str(workspace / "hermit"),
                "cost": {
                    "estimate": cost_estimate,
                    "actual": None,
                    "record_path": str(local_cost),
                },
            }
        },
        store_path,
    )
    local_pid = _spawn_detached(
        ("_local-run", obligation_id, "--source", str(source), "--store", str(store_path)),
        local_log,
    )
    obligations.transition(
        obligation_id,
        "local-started",
        {
            "local": {
                "state": "running",
                "pid": local_pid,
            }
        },
        store_path,
    )
    github_error: ProtocolError | None = None
    try:
        ensure_github_verification(
            obligation_id,
            store_path=store_path,
            wait_seconds=args.github_wait_seconds,
            allow_dispatch=not args.no_dispatch,
        )
    except ProtocolError as error:
        github_error = error
        obligations.transition(
            obligation_id,
            "github-arm-error",
            {
                "github": {
                    "state": "error",
                    "finished_at": obligations.utc_now(),
                    "last_poll_error": str(error),
                }
            },
            store_path,
        )
        evaluate_obligation(obligation_id, store_path=store_path)

    watcher_pid = _spawn_detached(
        (
            "watch",
            "--id",
            obligation_id,
            "--poll-seconds",
            str(args.poll_seconds),
            "--store",
            str(store_path),
        ),
        watcher_log,
    )
    record = obligations.transition(
        obligation_id,
        "watcher-started",
        {
            "watcher": {
                "pid": watcher_pid,
                "log_path": str(watcher_log),
                "started_at": obligations.utc_now(),
            }
        },
        store_path,
    )
    print(f"OPEN OBLIGATION: {obligation_id} {args.repo}@{sha}")
    print(f"  local: pid={local_pid} log={local_log}")
    print(
        "  local estimate: "
        f"wall={cost_estimate['wall_seconds']:.0f}s cpu={cost_estimate['cpu_seconds']:.0f}s "
        f"basis={cost_estimate['basis']}"
    )
    print(
        "  github: "
        f"state={record['github']['state']} runs={','.join(map(str, record['github']['run_ids'])) or 'pending'}"
    )
    print(f"  watcher: pid={watcher_pid} log={watcher_log}")
    return 2 if github_error else 0


def resolve_obligation(args: argparse.Namespace) -> int:
    ref = args.ref.lower()
    if not obligations.SHA_RE.fullmatch(ref):
        raise ProtocolError("--ref must be a full 40-character commit SHA")
    now = obligations.utc_now()
    obligations.transition(
        args.id,
        "remediated",
        {
            "overall_state": "remediated",
            "remediation": {
                "state": "completed",
                "kind": args.kind,
                "ref": ref,
                "started_at": args.started_at or now,
                "completed_at": now,
            },
        },
        args.store,
    )
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    arm_parser = subparsers.add_parser("arm", help="arm dual verification for a landed SHA")
    arm_parser.add_argument("sha")
    arm_parser.add_argument("--repo", default=DEFAULT_REPO)
    arm_parser.add_argument("--source", type=Path, default=ROOT / "hermit")
    arm_parser.add_argument("--land-mode", choices=("admin", "speculative"), default="speculative")
    arm_parser.add_argument("--actor", default=os.environ.get("AGENT", os.environ.get("USER", "unknown")))
    arm_parser.add_argument("--github-wait-seconds", type=int, default=DEFAULT_GITHUB_WAIT_SECONDS)
    arm_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    arm_parser.add_argument("--no-dispatch", action="store_true")
    arm_parser.add_argument("--store", type=Path, default=obligations.default_store_path())

    watch_parser = subparsers.add_parser("watch", help="poll open obligations and record transitions")
    watch_parser.add_argument("--id")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--gate", action="store_true")
    watch_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    watch_parser.add_argument("--store", type=Path, default=obligations.default_store_path())

    status_parser = subparsers.add_parser("status", help="show unresolved obligations")
    status_parser.add_argument("--all", action="store_true")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--gate", action="store_true")
    status_parser.add_argument("--store", type=Path, default=obligations.default_store_path())

    resolve_parser = subparsers.add_parser("resolve", help="record completed remediation")
    resolve_parser.add_argument("id")
    resolve_parser.add_argument("--kind", choices=("fix-forward", "revert"), required=True)
    resolve_parser.add_argument("--ref", required=True)
    resolve_parser.add_argument("--started-at")
    resolve_parser.add_argument("--store", type=Path, default=obligations.default_store_path())

    local_parser = subparsers.add_parser("_local-run", help=argparse.SUPPRESS)
    local_parser.add_argument("id")
    local_parser.add_argument("--source", type=Path, required=True)
    local_parser.add_argument("--store", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "arm":
            if args.github_wait_seconds < 0 or args.poll_seconds <= 0:
                raise ProtocolError("wait must be non-negative and poll interval must be positive")
            return arm(args)
        if args.command == "watch":
            if args.poll_seconds <= 0:
                raise ProtocolError("--poll-seconds must be positive")
            result = watch(
                store_path=args.store,
                obligation_id=args.id,
                once=args.once,
                poll_seconds=args.poll_seconds,
            )
            if args.gate:
                return print_status(args.store, include_closed=False, json_output=False, gate=True)
            return result
        if args.command == "status":
            return print_status(args.store, include_closed=args.all, json_output=args.json, gate=args.gate)
        if args.command == "resolve":
            return resolve_obligation(args)
        if args.command == "_local-run":
            return _local_run(args.id, args.source, args.store)
    except (ProtocolError, obligations.StoreError) as error:
        print(f"ci-hub speculative-land: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
