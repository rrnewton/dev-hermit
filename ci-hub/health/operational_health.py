#!/usr/bin/env python3
"""Emit tick-hub gate fields for dev-hermit operational health checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runners"))

import github_main_health
import pr_status
import primary_checkout
import queue_health


BROKEN_AGENT_STATES = frozenset(
    (
        "crashed",
        "disconnected",
        "error",
        "failed",
        "stuck",
        "unreachable",
        "unresponsive",
    )
)
ACTIVE_AGENT_STATES = frozenset(("active", "busy", "in_progress", "running", "working"))
DEFAULT_STUCK_AFTER_SECS = 60 * 60
DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECS = 10 * 60
# Per-repo wall-clock budget for the auto-invoked PR-health gate. tick-hub's
# SubprocessGateRunner hard-kills any gate at 30s, so the gate MUST resolve well
# under that: two repos queried serially at 12s each is ~24s worst case. Staying
# under the guillotine is what lets pr_status emit its own structured
# degraded/unavailable result (distinguishing "GitHub was slow" from "ci-hub is
# broken") instead of the gate timing out into a bare, undifferentiated failure.
DEFAULT_PR_GATE_TIMEOUT_SECS = float(
    os.environ.get("CI_HUB_PR_GATE_TIMEOUT", "12")
)
# Per-gh-call bound for the auto-invoked CI queue-health gate, same rationale as
# the PR gate above: the gate makes at most two gh calls (run-list + runners API)
# per repo, so 12s each keeps a single default-repo tick to ~24s worst case,
# under tick-hub's 30s guillotine. On expiry queue_health classifies an UPSTREAM
# timeout ("GitHub slow") rather than being hard-killed into a bare failure that
# reads as "ci-hub broken".
DEFAULT_QUEUE_GATE_TIMEOUT_SECS = float(
    os.environ.get("CI_HUB_QUEUE_GATE_TIMEOUT", "12")
)
DEFAULT_AGENT_SNAPSHOT = ROOT / "ignored" / "ci-hub" / "agent-snapshot.json"
TERMINAL_AGENT_STATES = frozenset(
    (
        "closed",
        "crashed",
        "dead",
        "disconnected",
        "error",
        "exited",
        "failed",
        "retired",
        "stuck",
        "terminated",
        "unreachable",
        "unresponsive",
    )
)


@dataclass(frozen=True)
class TaskRecord:
    id: str
    title: str
    owner: str
    tags: tuple[str, ...]

    @property
    def implemented(self) -> bool:
        return "implemented" in self.tags


@dataclass(frozen=True)
class AgentRecord:
    name: str
    status: str
    current_task: str | None

    @property
    def live(self) -> bool:
        return self.status not in TERMINAL_AGENT_STATES

    @property
    def busy(self) -> bool:
        return self.status in ACTIVE_AGENT_STATES


@dataclass(frozen=True)
class Misroute:
    agent: str
    task: str
    reason: str


@dataclass(frozen=True)
class ActiveWorkReport:
    in_progress: tuple[TaskRecord, ...]
    awaiting_land: tuple[TaskRecord, ...]
    stale: tuple[TaskRecord, ...]
    owned_active: tuple[TaskRecord, ...]
    actually_active: tuple[TaskRecord, ...]
    orphaned: tuple[TaskRecord, ...]
    off_book: tuple[AgentRecord, ...]
    misrouted: tuple[Misroute, ...]
    live_agents: tuple[AgentRecord, ...]
    busy_agents: tuple[AgentRecord, ...]

    @property
    def actionable_count(self) -> int:
        return (
            len(self.orphaned)
            + len(self.stale)
            + len(self.off_book)
            + len(self.misrouted)
        )

    def counts(self) -> dict[str, int]:
        return {
            "in_progress": len(self.in_progress),
            "awaiting_land": len(self.awaiting_land),
            "stale": len(self.stale),
            "owned_active": len(self.owned_active),
            "actually_active": len(self.actually_active),
            "orphaned": len(self.orphaned),
            "off_book": len(self.off_book),
            "misrouted": len(self.misrouted),
            "live_agents": len(self.live_agents),
            "busy_agents": len(self.busy_agents),
        }


def _field(value: object) -> str:
    return " ".join(str(value).split()) or "none"


def _emit(fields: Mapping[str, object]) -> None:
    for key, value in fields.items():
        print(f"{key}={_field(value)}")


def github_main_gate() -> int:
    try:
        health = github_main_health.collect_health(
            github_main_health.DEFAULT_REPOS,
            github_main_health.DEFAULT_RUN_LIMIT,
            per_call_timeout=github_main_health.DEFAULT_CALL_TIMEOUT,
            overall_deadline=github_main_health.DEFAULT_OVERALL_DEADLINE,
        )
        state = github_main_health.overall_state(health)
        summary = ",".join(
            f"{repo.repo}:{repo.state if repo.available else 'unavailable'}"
            for repo in health
        )
    except RuntimeError as error:
        _emit({"state": "unknown", "summary": _field(error)})
        return 1

    _emit({"state": state, "summary": summary})
    return 1 if state in {"red", "none", "degraded"} else 0


def pull_request_gate() -> int:
    # Query each repo under a bounded budget and keep whatever succeeds: one slow
    # or blocked repo must not discard the other's real reds, and the gate must
    # resolve before tick-hub's 30s guillotine so the failure is reported (with a
    # reason) rather than silently timed out. See DEFAULT_PR_GATE_TIMEOUT_SECS.
    statuses: list[pr_status.RepoStatus] = []
    unavailable: list[tuple[str, str]] = []
    for repo in pr_status.DEFAULT_REPOS:
        try:
            statuses.append(
                pr_status.fetch_repo_status(
                    repo, timeout=DEFAULT_PR_GATE_TIMEOUT_SECS
                )
            )
        except RuntimeError as error:
            unavailable.append((repo, _field(error)))

    if not statuses:
        # Every repo failed: ci-hub/GitHub is unavailable. This is NOT "no open
        # PRs" and NOT "all green" — report it loudly and fire the reminder.
        reasons = "; ".join(f"{repo}:{why}" for repo, why in unavailable)
        _emit(
            {
                "state": "unavailable",
                "total": 0,
                "red": 0,
                "pending": 0,
                "green": 0,
                "real_reds": 0,
                "outage": "no",
                "degraded": "yes",
                "summary": f"all repos unavailable ({reasons})",
            }
        )
        return 1

    counts = {
        state: sum(getattr(status, state) for status in statuses)
        for state in ("open", "green", "red", "pending", "real_reds")
    }
    outage = any(status.outage_suspected for status in statuses)
    unhealthy = counts["real_reds"] > 0 or outage
    degraded = bool(unavailable)
    if unhealthy:
        state = "red"
    elif degraded:
        # Real reds vs partial coverage are different alarms: "degraded" tells the
        # coordinator the answer is incomplete (a repo was slow/blocked), not that
        # PRs are failing.
        state = "degraded"
    else:
        state = "ok"
    summary = (
        f"open={counts['open']},red={counts['red']},"
        f"pending={counts['pending']},real={counts['real_reds']},"
        f"outage={'yes' if outage else 'no'}"
    )
    if degraded:
        summary += ",unavailable=" + "|".join(repo for repo, _ in unavailable)
    _emit(
        {
            "state": state,
            "total": counts["open"],
            "red": counts["red"],
            "pending": counts["pending"],
            "green": counts["green"],
            "real_reds": counts["real_reds"],
            "outage": "yes" if outage else "no",
            "degraded": "yes" if degraded else "no",
            "summary": summary,
        }
    )
    return 1 if (unhealthy or degraded) else 0


def primary_snapshot_gate() -> int:
    output, errors = StringIO(), StringIO()
    result = primary_checkout.checkout_fresh(
        primary_checkout.default_root(),
        publish_parent=True,
        strict=True,
        out=output,
        err=errors,
    )
    report = errors.getvalue().strip() or output.getvalue().strip()
    _emit(
        {
            "state": "ok" if result == 0 else "blocked",
            "summary": report or "primary-snapshot-produced-no-output",
        }
    )
    return result


def queue_health_gate() -> int:
    """Fail-loud tick gate for CI queue depth, wait times, and staleness of the
    last green run. Delegates to the shared queue_health analysis so the tick and
    the human `runner-health` report never diverge."""
    import os

    gh_cmd = os.environ.get("GH", "with-proxy gh")
    limit = int(os.environ.get("CI_QUEUE_HEALTH_LIMIT", "100"))
    repos = [os.environ.get("CI_QUEUE_HEALTH_REPO", "rrnewton/hermit")]
    try:
        # Bound each gh call so the whole gate resolves under tick-hub's 30s
        # guillotine and reports a CLASSIFIED result (upstream-slow vs
        # ci-hub-broken) instead of being hard-killed into a bare timeout.
        return queue_health.gate(repos, gh_cmd, limit,
                                 per_call_timeout=DEFAULT_QUEUE_GATE_TIMEOUT_SECS)
    except Exception as error:  # never let a probe crash the tick silently
        _emit({"state": "unknown", "summary": _field(f"queue-health-error:{error}")})
        return 1


def _last_activity_seconds(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value > 10_000_000_000:
        value /= 1000.0
    return value


def classify_stuck_agents(
    agents: Sequence[object],
    *,
    now: float,
    stuck_after_secs: int = DEFAULT_STUCK_AFTER_SECS,
) -> list[tuple[str, str]]:
    stuck: list[tuple[str, str]] = []
    for raw in agents:
        if not isinstance(raw, Mapping):
            continue
        name = _field(raw.get("name", "unnamed"))
        status = str(raw.get("status") or "unknown").strip().lower()
        if status in BROKEN_AGENT_STATES:
            stuck.append((name, status))
            continue
        last_activity = _last_activity_seconds(raw.get("last_activity"))
        if (
            status in ACTIVE_AGENT_STATES
            and last_activity is not None
            and now - last_activity >= stuck_after_secs
        ):
            age_minutes = int((now - last_activity) // 60)
            stuck.append((name, f"{status}-silent-{age_minutes}m"))
    return sorted(stuck)


def agent_gate(
    snapshot: str | None = None,
    *,
    now: float | None = None,
    stuck_after_secs: int = DEFAULT_STUCK_AFTER_SECS,
) -> int:
    text = (
        snapshot
        if snapshot is not None
        else os.environ.get("HERMIT_AGENT_SNAPSHOT_JSON")
    )
    if text is None:
        _emit(
            {
                "state": "unknown",
                "count": 0,
                "names": "none",
                "summary": "ORC-agent-snapshot-missing",
            }
        )
        return 1
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as error:
        _emit(
            {
                "state": "unknown",
                "count": 0,
                "names": "none",
                "summary": f"invalid-agent-snapshot:{error.msg}",
            }
        )
        return 1
    if not isinstance(payload, list):
        _emit(
            {
                "state": "unknown",
                "count": 0,
                "names": "none",
                "summary": "agent-snapshot-is-not-a-list",
            }
        )
        return 1

    stuck = classify_stuck_agents(
        payload,
        now=time.time() if now is None else now,
        stuck_after_secs=stuck_after_secs,
    )
    _emit(
        {
            "state": "stuck" if stuck else "ok",
            "count": len(stuck),
            "names": ",".join(name for name, _reason in stuck) or "none",
            "summary": ",".join(f"{name}:{reason}" for name, reason in stuck)
            or "no-stuck-agents",
        }
    )
    return 1 if stuck else 0


def _parse_agents(payload: object) -> tuple[AgentRecord, ...]:
    if not isinstance(payload, list):
        raise RuntimeError("agent-snapshot-is-not-a-list")
    agents: list[AgentRecord] = []
    names: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"agent-snapshot-entry-{index}-is-not-an-object")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"agent-snapshot-entry-{index}-has-no-name")
        if name in names:
            raise RuntimeError(f"agent-snapshot-has-duplicate-name:{name}")
        names.add(name)
        status = str(raw.get("status") or "unknown").strip().lower()
        current_task_raw = raw.get("current_task")
        current_task = (
            str(current_task_raw).strip() if current_task_raw is not None else ""
        )
        agents.append(
            AgentRecord(
                name=name,
                status=status,
                current_task=current_task or None,
            )
        )
    return tuple(sorted(agents, key=lambda agent: agent.name))


def _persist_agent_snapshot(
    path: Path,
    payload: object,
    *,
    captured_at: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_at": captured_at,
                "agents": payload,
            },
            sort_keys=True,
        )
        + "\n"
    )
    os.replace(temporary, path)


def load_agent_snapshot(
    snapshot: str | None,
    *,
    snapshot_file: Path = DEFAULT_AGENT_SNAPSHOT,
    max_age_secs: int = DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECS,
    now: float | None = None,
) -> tuple[tuple[AgentRecord, ...], float]:
    observed_at = time.time() if now is None else now
    text = (
        snapshot
        if snapshot is not None
        else os.environ.get("HERMIT_AGENT_SNAPSHOT_JSON")
    )
    if text is not None:
        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid-agent-snapshot:{error.msg}") from error
        agents = _parse_agents(payload)
        _persist_agent_snapshot(snapshot_file, payload, captured_at=observed_at)
        return agents, observed_at

    try:
        envelope = json.loads(snapshot_file.read_text())
    except FileNotFoundError as error:
        raise RuntimeError(
            "ORC-agent-snapshot-missing; wait for the operational tick or pass --agent-snapshot"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot-read-agent-snapshot:{error}") from error
    if not isinstance(envelope, Mapping) or envelope.get("schema_version") != 1:
        raise RuntimeError("agent-snapshot-envelope-has-unsupported-schema")
    captured_at = envelope.get("captured_at")
    if isinstance(captured_at, bool) or not isinstance(captured_at, (int, float)):
        raise RuntimeError("agent-snapshot-envelope-has-invalid-captured-at")
    age = observed_at - float(captured_at)
    if age < 0 or age > max_age_secs:
        raise RuntimeError(
            f"agent-snapshot-stale:age={max(0, int(age))}s,max={max_age_secs}s"
        )
    return _parse_agents(envelope.get("agents")), float(captured_at)


def cache_agent_snapshot(snapshot: str | None = None) -> int:
    text = (
        snapshot
        if snapshot is not None
        else os.environ.get("HERMIT_AGENT_SNAPSHOT_JSON")
    )
    if text is None:
        _emit({"state": "unknown", "summary": "ORC-agent-snapshot-missing"})
        return 1
    try:
        payload: object = json.loads(text)
        agents = _parse_agents(payload)
        _persist_agent_snapshot(
            DEFAULT_AGENT_SNAPSHOT, payload, captured_at=time.time()
        )
    except (json.JSONDecodeError, OSError, RuntimeError) as error:
        _emit({"state": "unknown", "summary": f"cannot-cache-agent-snapshot:{error}"})
        return 1
    _emit({"state": "ok", "count": len(agents), "summary": "agent-snapshot-cached"})
    return 0


def _taskgraph_in_progress() -> tuple[TaskRecord, ...]:
    sql = """
SELECT json_object(
  'id', local_id,
  'title', title,
  'owner', COALESCE(owner, ''),
  'tags', json(tags)
) AS task_json
FROM tasks
WHERE status = 'IN_PROGRESS'
ORDER BY local_id
""".strip()
    last_error = "unknown failure"
    for attempt in range(3):
        try:
            process = subprocess.run(
                ["tg", "sql", sql],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"taskgraph-query-unavailable:{error}") from error
        if process.returncode == 0:
            break
        last_error = (process.stderr or process.stdout).strip() or "no diagnostic"
        if not any(
            marker in last_error.lower()
            for marker in (
                "database is locked",
                "database schema changed",
                "error code 17",
            )
        ):
            raise RuntimeError(f"taskgraph-query-failed:{last_error}")
        time.sleep(0.1 * (attempt + 1))
    else:
        raise RuntimeError(f"taskgraph-query-failed-after-retry:{last_error}")

    tasks: list[TaskRecord] = []
    for line in process.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"taskgraph-returned-invalid-json:{error.msg}"
            ) from error
        tags = raw.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise RuntimeError(f"taskgraph-task-has-invalid-tags:{raw.get('id')}")
        tasks.append(
            TaskRecord(
                id=str(raw.get("id") or ""),
                title=str(raw.get("title") or ""),
                owner=str(raw.get("owner") or "").strip(),
                tags=tuple(tags),
            )
        )
    count_match = re.search(r"\((\d+) rows\)", process.stdout)
    if count_match is None or int(count_match.group(1)) != len(tasks):
        raise RuntimeError(
            f"taskgraph-row-count-mismatch:parsed={len(tasks)},reported="
            f"{count_match.group(1) if count_match else 'missing'}"
        )
    return tuple(tasks)


def reconcile_active_work(
    tasks: Sequence[TaskRecord],
    agents: Sequence[AgentRecord],
) -> ActiveWorkReport:
    in_progress = tuple(sorted(tasks, key=lambda task: task.id))
    awaiting_land = tuple(task for task in in_progress if task.implemented)
    active_candidates = tuple(task for task in in_progress if not task.implemented)
    stale = tuple(task for task in active_candidates if not task.owner)
    owned_active = tuple(task for task in active_candidates if task.owner)
    live_agents = tuple(
        sorted((agent for agent in agents if agent.live), key=lambda a: a.name)
    )
    busy_agents = tuple(agent for agent in live_agents if agent.busy)
    live_by_name = {agent.name: agent for agent in live_agents}
    task_by_id = {task.id: task for task in in_progress}

    orphaned = tuple(task for task in owned_active if task.owner not in live_by_name)
    actually_active: list[TaskRecord] = []
    misrouted: set[Misroute] = set()
    for task in owned_active:
        agent = live_by_name.get(task.owner)
        if agent is None:
            continue
        if agent.busy and agent.current_task == task.id:
            actually_active.append(task)
            continue
        misrouted.add(
            Misroute(
                agent=agent.name,
                task=task.id,
                reason=(
                    f"owner-status={agent.status},"
                    f"owner-current-task={agent.current_task or 'none'}"
                ),
            )
        )

    off_book = tuple(agent for agent in busy_agents if not agent.current_task)
    for agent in busy_agents:
        if not agent.current_task:
            continue
        task = task_by_id.get(agent.current_task)
        if task is None:
            misrouted.add(
                Misroute(
                    agent=agent.name,
                    task=agent.current_task,
                    reason="current-task-is-not-in-progress",
                )
            )
        elif task.implemented:
            misrouted.add(
                Misroute(
                    agent=agent.name,
                    task=task.id,
                    reason="current-task-is-tagged-implemented",
                )
            )
        elif task.owner != agent.name:
            misrouted.add(
                Misroute(
                    agent=agent.name,
                    task=task.id,
                    reason=f"task-owner={task.owner or 'none'}",
                )
            )

    return ActiveWorkReport(
        in_progress=in_progress,
        awaiting_land=awaiting_land,
        stale=stale,
        owned_active=owned_active,
        actually_active=tuple(sorted(actually_active, key=lambda task: task.id)),
        orphaned=tuple(sorted(orphaned, key=lambda task: task.id)),
        off_book=tuple(sorted(off_book, key=lambda agent: agent.name)),
        misrouted=tuple(
            sorted(misrouted, key=lambda item: (item.agent, item.task, item.reason))
        ),
        live_agents=live_agents,
        busy_agents=busy_agents,
    )


def _active_work_detail(report: ActiveWorkReport) -> list[str]:
    detail: list[str] = []
    detail.extend(f"ORPHANED {task.id} owner={task.owner}" for task in report.orphaned)
    detail.extend(f"STALE {task.id}" for task in report.stale)
    detail.extend(
        f"AWAITING-LAND {task.id} owner={task.owner or 'none'}"
        for task in report.awaiting_land
    )
    detail.extend(
        f"OFF-BOOK {agent.name} status={agent.status}" for agent in report.off_book
    )
    detail.extend(
        f"MISROUTED {item.agent} task={item.task} {item.reason}"
        for item in report.misrouted
    )
    return detail


def _active_work_json(
    report: ActiveWorkReport, captured_at: float
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_snapshot_captured_at": captured_at,
        "state": "drift" if report.actionable_count else "ok",
        "counts": report.counts(),
        "orphaned": [asdict(task) for task in report.orphaned],
        "stale": [asdict(task) for task in report.stale],
        "awaiting_land": [asdict(task) for task in report.awaiting_land],
        "off_book": [asdict(agent) for agent in report.off_book],
        "misrouted": [asdict(item) for item in report.misrouted],
        "actually_active": [asdict(task) for task in report.actually_active],
    }


def active_work_gate(
    *,
    snapshot: str | None = None,
    snapshot_file: Path = DEFAULT_AGENT_SNAPSHOT,
    max_age_secs: int = DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECS,
    json_output: bool = False,
    gate_output: bool = False,
    now: float | None = None,
) -> int:
    try:
        agents, captured_at = load_agent_snapshot(
            snapshot,
            snapshot_file=snapshot_file,
            max_age_secs=max_age_secs,
            now=now,
        )
        report = reconcile_active_work(_taskgraph_in_progress(), agents)
    except RuntimeError as error:
        if json_output:
            print(
                json.dumps(
                    {"schema_version": 1, "state": "unknown", "error": str(error)}
                )
            )
        else:
            _emit({"state": "unknown", "summary": error})
        return 1

    counts = report.counts()
    state = "drift" if report.actionable_count else "ok"
    if json_output:
        print(json.dumps(_active_work_json(report, captured_at), sort_keys=True))
    elif gate_output:
        fields: dict[str, object] = {
            "state": state,
            **counts,
            "summary": (
                f"in-progress={counts['in_progress']},"
                f"actually-active={counts['actually_active']},"
                f"awaiting-land={counts['awaiting_land']},"
                f"stale={counts['stale']},orphaned={counts['orphaned']},"
                f"off-book={counts['off_book']},misrouted={counts['misrouted']}"
            ),
        }
        detail = _active_work_detail(report)
        if detail:
            fields["detail"] = " | ".join(detail)
        _emit(fields)
    else:
        print("ACTIVE-WORK RECONCILIATION")
        print(" ".join(f"{key}={value}" for key, value in counts.items()))
        detail = _active_work_detail(report)
        if detail:
            print("\n".join(detail))
        else:
            print("NO DIVERGENCES")
    return 1 if report.actionable_count else 0


def active_work_command(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="operational_health.py active-work",
        description="Reconcile TaskGraph state, task ownership, and live ORC agents.",
    )
    parser.add_argument("--agent-snapshot", type=Path)
    parser.add_argument(
        "--max-snapshot-age",
        type=int,
        default=DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECS,
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parsed = parser.parse_args(args)
    if parsed.max_snapshot_age < 0:
        parser.error("--max-snapshot-age must be non-negative")
    snapshot = None
    if parsed.agent_snapshot is not None:
        try:
            snapshot = parsed.agent_snapshot.read_text()
        except OSError as error:
            parser.error(f"cannot read --agent-snapshot: {error}")
    return active_work_gate(
        snapshot=snapshot,
        max_age_secs=parsed.max_snapshot_age,
        json_output=parsed.json,
        gate_output=parsed.gate,
    )


MEMORY_SKILL_LINTER = ROOT / "scripts" / "lint-memory-skill-sync.rs"
MEMORY_SKILL_SCANNER = ROOT / "scripts" / "memory-skill-contradiction-scan.rs"


def _run_tool(cmd: Sequence[str]) -> tuple[int, str, str]:
    """Run a checker; return (returncode, stdout, stderr). Non-runnable -> rc 127."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as error:
        return 127, "", str(error)
    except OSError as error:  # e.g. not executable, rust-script missing
        return 127, "", str(error)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after 300s"
    return proc.returncode, proc.stdout, proc.stderr


def _scan_fields(stdout: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition("=")
            key = key.strip()
            if key in {"state", "summary", "contradictions", "drift"}:
                fields.setdefault(key, value.strip())
    return fields


def memory_skill_sync_gate() -> int:
    """Check authoritative repository skills; local memories are advisory mirrors.

    The structural linter validates versioned skills. The scanner gates only
    contradictions in those skills; local-memory absence or drift never makes
    machine-local state authoritative. Both tools are report-only.
    """
    lint_rc, lint_out, lint_err = _run_tool([str(MEMORY_SKILL_LINTER), "--quiet"])
    scan_rc, scan_out, scan_err = _run_tool([str(MEMORY_SKILL_SCANNER), "--gate"])

    # A checker that cannot even run means drift detection is DOWN -> alert.
    if lint_rc in (124, 127) or scan_rc in (124, 127):
        which = "linter" if lint_rc in (124, 127) else "scanner"
        detail = (lint_err if which == "linter" else scan_err).strip() or "unavailable"
        print(lint_out or scan_out, end="")
        _emit(
            {
                "state": "error",
                "problems": 0,
                "contradictions": 0,
                "summary": f"memory-skill-{which}-unrunnable: {detail} (is rust-script installed?)",
            }
        )
        return 1

    problems = 0
    match = re.search(r"problems:\s*(\d+)", lint_out)
    if match:
        problems = int(match.group(1))
    elif lint_rc != 0:
        problems = 1  # linter failed but count unparsed

    scan = _scan_fields(scan_out)
    contradictions = int(scan.get("contradictions", "0") or "0")

    healthy = lint_rc == 0 and scan_rc == 0 and problems == 0 and contradictions == 0
    if healthy:
        state = "ok"
    elif contradictions and problems:
        state = "both"
    elif contradictions:
        state = "contradiction"
    else:
        state = "drift"

    summary = (
        f"repository structural problems={problems}, "
        f"repository contradictions={contradictions}; "
        "versioned skills are authoritative and optional local-memory drift is advisory"
    )

    # Build a single-line `detail` proposal from the tools' human output. It must
    # be ONE captured field: the tick-hub gate parser (parse_kv_lines) treats EVERY
    # stdout line containing '=' as a key=value pair, so emitting multi-line detail
    # would leak phantom fields and clobber state/summary. Packing it into one
    # collapsed value keeps embedded '=' safe (partition splits on the first only).
    detail_lines: list[str] = []
    if lint_rc != 0:
        for line in lint_out.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("OK "):
                detail_lines.append(f"[lint] {stripped}")
    for line in scan_out.splitlines():
        stripped = line.strip()
        # keep the human report; drop the scanner's own key=value gate fields.
        if not stripped:
            continue
        if stripped.split("=", 1)[0] in {"state", "summary", "contradictions", "drift"}:
            continue
        detail_lines.append(stripped)

    fields: dict[str, object] = {
        "state": state,
        "problems": problems,
        "contradictions": contradictions,
        "summary": summary,
    }
    if not healthy and detail_lines:
        fields["detail"] = " | ".join(detail_lines)
    _emit(fields)
    return 0 if healthy else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "active-work":
        return active_work_command(args[1:])
    if args == ["cache-agents"]:
        return cache_agent_snapshot()
    if args == ["github-main"]:
        return github_main_gate()
    if args == ["pull-requests"]:
        return pull_request_gate()
    if args == ["primary-snapshot"]:
        return primary_snapshot_gate()
    if args == ["agents"]:
        return agent_gate()
    if args == ["queue-health"]:
        return queue_health_gate()
    if args == ["memory-skill-sync"]:
        return memory_skill_sync_gate()
    print(
        "usage: operational_health.py "
        "<active-work|cache-agents|github-main|pull-requests|primary-snapshot|agents|queue-health|"
        "memory-skill-sync>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
