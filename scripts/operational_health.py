#!/usr/bin/env python3
"""Emit tick-hub gate fields for dev-hermit operational health checks."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

if __package__:
    from . import github_main_health, pr_status
else:
    import github_main_health
    import pr_status


BROKEN_AGENT_STATES = frozenset(
    ("crashed", "disconnected", "error", "failed", "stuck", "unresponsive")
)
ACTIVE_AGENT_STATES = frozenset(
    ("active", "busy", "in_progress", "running", "working")
)
DEFAULT_STUCK_AFTER_SECS = 60 * 60


def _field(value: object) -> str:
    return " ".join(str(value).split()) or "none"


def _emit(fields: Mapping[str, object]) -> None:
    for key, value in fields.items():
        print(f"{key}={_field(value)}")


def github_main_gate() -> int:
    try:
        health = [
            github_main_health.evaluate_repo(repo)
            for repo in github_main_health.DEFAULT_REPOS
        ]
        state = github_main_health.overall_state(health)
        summary = ",".join(f"{repo.repo}:{repo.state}" for repo in health)
    except RuntimeError as error:
        _emit({"state": "unknown", "summary": _field(error)})
        return 1

    _emit({"state": state, "summary": summary})
    return 1 if state in {"red", "none"} else 0


def pull_request_gate() -> int:
    try:
        pulls = [
            pull
            for repo in pr_status.DEFAULT_REPOS
            for pull in pr_status.fetch_open_prs(repo)
        ]
    except (RuntimeError, ValueError) as error:
        _emit(
            {
                "state": "unknown",
                "total": 0,
                "red": 0,
                "pending": 0,
                "summary": _field(error),
            }
        )
        return 1

    counts = {
        state: sum(pull.ci_status == state for pull in pulls)
        for state in ("green", "red", "pending", "none")
    }
    state = "red" if counts["red"] else "ok"
    _emit(
        {
            "state": state,
            "total": len(pulls),
            "red": counts["red"],
            "pending": counts["pending"],
            "green": counts["green"],
            "none": counts["none"],
            "summary": (
                f"open={len(pulls)},red={counts['red']},"
                f"pending={counts['pending']},none={counts['none']}"
            ),
        }
    )
    return 1 if counts["red"] else 0


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
    text = snapshot if snapshot is not None else os.environ.get("HERMIT_AGENT_SNAPSHOT_JSON")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args == ["github-main"]:
        return github_main_gate()
    if args == ["pull-requests"]:
        return pull_request_gate()
    if args == ["agents"]:
        return agent_gate()
    print(
        "usage: operational_health.py <github-main|pull-requests|agents>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
