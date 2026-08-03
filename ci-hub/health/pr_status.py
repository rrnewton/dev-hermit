#!/usr/bin/env python3
"""Summarize open-PR CI by adapting the pinned agent-utils planner."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPOS = ("rrnewton/hermit", "rrnewton/reverie")
DEFAULT_WARN_THRESHOLD = 10
MAX_FETCH_ATTEMPTS = 3


@dataclass(frozen=True)
class RepoStatus:
    repo: str
    open: int
    green: int
    red: int
    pending: int
    real_reds: int
    outage_suspected: bool
    prs: tuple[dict[str, object], ...]

    @property
    def unhealthy(self) -> bool:
        return self.real_reds > 0 or self.outage_suspected


def _checkout_for(repo: str) -> Path:
    name = repo.rsplit("/", 1)[-1]
    if name == "dev-hermit":
        return ROOT
    checkout = ROOT / name
    if not checkout.is_dir():
        raise RuntimeError(f"{repo}: local checkout is missing: {checkout}")
    return checkout


def planner_command(repo: str, warn_threshold: int) -> list[str]:
    return [
        str(ROOT / "ci-hub/bin/agent-tool"),
        "pr-landing-planner",
        "status",
        "--repo",
        repo,
        "--base",
        "main",
        "--git-dir",
        str(_checkout_for(repo)),
        "--remote",
        "origin",
        "--net-wrapper",
        "with-proxy",
        "--gh-cmd",
        "gh",
        "--conflict-detector",
        "file-overlap",
        "--gate-check",
        "merge-gate" if repo == "rrnewton/hermit" else "Merge Gate",
        "--format",
        "json",
        "--warn-threshold",
        str(warn_threshold),
    ]


def fetch_repo_status(repo: str, warn_threshold: int = DEFAULT_WARN_THRESHOLD) -> RepoStatus:
    command = planner_command(repo, warn_threshold)
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            break
        detail = result.stderr.strip() or result.stdout.strip()
        retryable = "504" in detail or "changed during collection" in detail
        if not retryable or attempt == MAX_FETCH_ATTEMPTS:
            break
        time.sleep(attempt)
    assert result is not None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{repo}: agent-utils pr-landing-planner failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{repo}: planner returned invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise RuntimeError(f"{repo}: planner returned an unexpected schema")
    summary = payload["summary"]
    prs = payload.get("prs")
    if not isinstance(prs, list):
        raise RuntimeError(f"{repo}: planner result has no PR list")

    def count(key: str) -> int:
        value = summary.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return RepoStatus(
        repo=repo,
        open=count("open"),
        green=count("green"),
        red=count("red"),
        pending=count("pending"),
        real_reds=count("real_reds"),
        outage_suspected=summary.get("outage_suspected") is True,
        prs=tuple(pr for pr in prs if isinstance(pr, dict)),
    )


def render_report(statuses: Sequence[RepoStatus], warn_threshold: int) -> str:
    total = sum(status.open for status in statuses)
    heading = "CI health: UNHEALTHY" if any(status.unhealthy for status in statuses) else "CI health: HEALTHY"
    lines = [
        heading,
        "Source: pinned agent-utils/pr-landing-planner status (fresh GitHub query)",
    ]
    for status in statuses:
        lines.append(
            f"  {status.repo}: open={status.open} green={status.green} "
            f"red={status.red} pending={status.pending} real_reds={status.real_reds} "
            f"outage={'yes' if status.outage_suspected else 'no'}"
        )
        for pr in status.prs:
            lines.append(
                f"    #{pr.get('pr', '?'):<5} ci={pr.get('ci', 'unknown'):<7} "
                f"class={pr.get('red_class') or '-':<23} {pr.get('title', '')}"
            )
    if total > warn_threshold:
        lines.append(
            f"WARNING: {total} open PRs exceeds the {warn_threshold} PR threshold."
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        dest="repos",
        action="append",
        help="GitHub OWNER/REPO to query; repeat to override defaults",
    )
    parser.add_argument("--warn-threshold", type=int, default=DEFAULT_WARN_THRESHOLD)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.warn_threshold < 0:
        parser.error("--warn-threshold must be non-negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS
    try:
        statuses = [fetch_repo_status(repo, args.warn_threshold) for repo in repos]
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"repos": [asdict(status) for status in statuses]}, sort_keys=True))
    else:
        print(render_report(statuses, args.warn_threshold))
    return 1 if any(status.unhealthy for status in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
