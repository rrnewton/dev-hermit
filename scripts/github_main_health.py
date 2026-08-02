#!/usr/bin/env python3
"""Report live GitHub Actions health for the current main commit."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Sequence

DEFAULT_REPOS = (
    "rrnewton/dev-hermit",
    "rrnewton/hermit",
    "rrnewton/reverie",
)
DEFAULT_RUN_LIMIT = 100
RED_CONCLUSIONS = frozenset(
    (
        "failure",
        "timed_out",
        "cancelled",
        "error",
        "action_required",
        "startup_failure",
        "stale",
    )
)
SUCCESS_CONCLUSIONS = frozenset(("success", "neutral", "skipped"))


@dataclass(frozen=True)
class MainRun:
    workflow: str
    head_sha: str
    status: str
    conclusion: str
    url: str
    created_at: str


@dataclass(frozen=True)
class RepoMainHealth:
    repo: str
    main_sha: str
    state: str
    runs: tuple[MainRun, ...]


def _run_gh(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise RuntimeError(
            "with-proxy was not found; GitHub queries must use the proxy wrapper"
        ) from error

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result.stdout


def fetch_main_sha(repo: str) -> str:
    output = _run_gh(
        (
            "with-proxy",
            "gh",
            "api",
            f"repos/{repo}/commits/main",
            "--jq",
            ".sha",
        )
    )
    sha = output.strip()
    if len(sha) != 40:
        raise RuntimeError(f"{repo}: invalid main SHA from GitHub: {sha!r}")
    return sha


def fetch_main_runs(repo: str, limit: int = DEFAULT_RUN_LIMIT) -> list[MainRun]:
    output = _run_gh(
        (
            "with-proxy",
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--branch",
            "main",
            "--event",
            "push",
            "--limit",
            str(limit),
            "--json",
            "workflowName,headSha,status,conclusion,url,createdAt",
        )
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{repo}: gh run list returned invalid JSON") from error
    if not isinstance(payload, list):
        raise RuntimeError(f"{repo}: gh run list returned a non-list payload")

    runs: list[MainRun] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise RuntimeError(f"{repo}: malformed workflow run: {raw!r}")
        runs.append(
            MainRun(
                workflow=str(raw.get("workflowName") or "unknown-workflow"),
                head_sha=str(raw.get("headSha") or ""),
                status=str(raw.get("status") or "").lower(),
                conclusion=str(raw.get("conclusion") or "").lower(),
                url=str(raw.get("url") or ""),
                created_at=str(raw.get("createdAt") or ""),
            )
        )
    return runs


def classify_current_runs(runs: Sequence[MainRun]) -> str:
    if not runs:
        return "none"
    if any(run.conclusion in RED_CONCLUSIONS for run in runs):
        return "red"
    if any(
        run.status != "completed" or run.conclusion not in SUCCESS_CONCLUSIONS
        for run in runs
    ):
        return "pending"
    return "green"


def evaluate_repo(repo: str, limit: int = DEFAULT_RUN_LIMIT) -> RepoMainHealth:
    main_sha = fetch_main_sha(repo)
    candidates = [run for run in fetch_main_runs(repo, limit) if run.head_sha == main_sha]

    # gh returns newest first. Keep only the newest attempt for each workflow so
    # a successful rerun supersedes an older failed/cancelled attempt.
    latest_by_workflow: dict[str, MainRun] = {}
    for run in candidates:
        previous = latest_by_workflow.get(run.workflow)
        if previous is None or run.created_at > previous.created_at:
            latest_by_workflow[run.workflow] = run
    current_runs = tuple(
        sorted(latest_by_workflow.values(), key=lambda run: run.workflow.lower())
    )
    return RepoMainHealth(
        repo=repo,
        main_sha=main_sha,
        state=classify_current_runs(current_runs),
        runs=current_runs,
    )


def overall_state(health: Sequence[RepoMainHealth]) -> str:
    states = {repo.state for repo in health}
    if "red" in states:
        return "red"
    if "pending" in states:
        return "pending"
    if "none" in states:
        return "none"
    return "green"


def render_report(health: Sequence[RepoMainHealth]) -> str:
    state = overall_state(health)
    if state == "red":
        heading = "HARD WARNING: GITHUB MAIN IS RED"
    elif state == "green":
        heading = "GitHub main health: GREEN"
    elif state == "pending":
        heading = "GitHub main health: PENDING (do not claim green)"
    else:
        heading = "GitHub main health: NO CURRENT-TIP RUNS (do not claim green)"

    lines = [heading, "Ground truth: gh run list --branch main --event push"]
    for repo in health:
        lines.append(f"  {repo.repo} main={repo.main_sha[:12]} state={repo.state}")
        if not repo.runs:
            lines.append("    no push workflow runs found at the current main SHA")
            continue
        for run in repo.runs:
            marker = "RED" if run.conclusion in RED_CONCLUSIONS else run.status.upper()
            conclusion = run.conclusion or "none"
            lines.append(
                f"    {marker:<9} {run.workflow}: {run.status}/{conclusion} {run.url}"
            )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        dest="repos",
        action="append",
        help="GitHub OWNER/REPO to query; repeat to override the defaults",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_RUN_LIMIT,
        help=f"main-branch push runs to inspect per repo (default: {DEFAULT_RUN_LIMIT})",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS
    try:
        health = [evaluate_repo(repo, args.limit) for repo in repos]
    except RuntimeError as error:
        print(f"HARD WARNING: CANNOT VERIFY GITHUB MAIN HEALTH: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "state": overall_state(health),
                    "repos": [asdict(repo) for repo in health],
                },
                sort_keys=True,
            )
        )
    else:
        print(render_report(health))
    return 1 if overall_state(health) == "red" else 0


if __name__ == "__main__":
    raise SystemExit(main())
