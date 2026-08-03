#!/usr/bin/env python3
"""Emit tick-hub gate fields for dev-hermit operational health checks."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import github_main_health
import pr_status
import primary_checkout


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
        statuses = [
            pr_status.fetch_repo_status(repo)
            for repo in pr_status.DEFAULT_REPOS
        ]
    except RuntimeError as error:
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
        state: sum(getattr(status, state) for status in statuses)
        for state in ("open", "green", "red", "pending", "real_reds")
    }
    outage = any(status.outage_suspected for status in statuses)
    unhealthy = counts["real_reds"] > 0 or outage
    state = "red" if unhealthy else "ok"
    _emit(
        {
            "state": state,
            "total": counts["open"],
            "red": counts["red"],
            "pending": counts["pending"],
            "green": counts["green"],
            "real_reds": counts["real_reds"],
            "outage": "yes" if outage else "no",
            "summary": (
                f"open={counts['open']},red={counts['red']},"
                f"pending={counts['pending']},real={counts['real_reds']},"
                f"outage={'yes' if outage else 'no'}"
            ),
        }
    )
    return 1 if unhealthy else 0


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
    if args == ["primary-snapshot"]:
        return primary_snapshot_gate()
    if args == ["agents"]:
        return agent_gate()
    print(
        "usage: operational_health.py "
        "<github-main|pull-requests|primary-snapshot|agents>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
