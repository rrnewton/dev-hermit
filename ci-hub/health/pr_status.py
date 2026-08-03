#!/usr/bin/env python3
"""Summarize open-PR CI by adapting the pinned agent-utils planner.

Boxing note: the planner shells out to a *per-PR* network ``git fetch`` for
every open PR (128 hermit + 26 reverie at time of writing), so an un-bounded
invocation takes minutes on the happy path and hangs forever if any single
proxied fetch stalls. We therefore apply the same boxing discipline ci-hub
enforces on everything else: a per-repo subprocess timeout, an overall command
deadline, bounded retries with backoff, and -- critically -- *partial results*
plus an explicit statement of what could not be fetched, so the tool ALWAYS
terminates with a report instead of hanging.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
AGENT_TOOL = Path(os.environ.get("CI_HUB_AGENT_TOOL", ROOT / "ci-hub/bin/agent-tool"))
DEFAULT_REPOS = ("rrnewton/hermit", "rrnewton/reverie")
DEFAULT_WARN_THRESHOLD = 10
MAX_FETCH_ATTEMPTS = 3

# Timeout basis (derived, not a plausible constant):
#   Measured 2026-08-03 on devbig014: the reverie planner completed in 35.17s
#   for 26 open PRs => ~1.35s per PR of sequential proxied `git fetch`. Hermit
#   had 128 open PRs => ~173s happy-path; reverie+hermit combined ~208s.
#   Per-repo default 300s ~= 1.7x the 173s hermit happy-path (headroom for
#   network variance and PR-count growth); overall default 480s ~= 2.3x the
#   ~208s combined happy-path so both repos normally complete while a stalled
#   fetch is still bounded. Override with the flags or the env vars below.
DEFAULT_PER_REPO_TIMEOUT = float(os.environ.get("CI_HUB_PR_STATUS_TIMEOUT", "300"))
DEFAULT_OVERALL_DEADLINE = float(os.environ.get("CI_HUB_PR_STATUS_DEADLINE", "480"))
# Seconds to wait for the killed planner child to die before moving on.
_TERMINATE_GRACE = 10.0


class RepoUnavailable(RuntimeError):
    """The planner could not be queried within its time budget."""


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
    available: bool = True
    reason: str = ""

    @property
    def unhealthy(self) -> bool:
        # An unavailable repo is UNKNOWN, not unhealthy: we must not synthesize a
        # red from a query we never completed.
        return self.available and (self.real_reds > 0 or self.outage_suspected)


def _unavailable(repo: str, reason: str) -> RepoStatus:
    return RepoStatus(
        repo=repo,
        open=0,
        green=0,
        red=0,
        pending=0,
        real_reds=0,
        outage_suspected=False,
        prs=(),
        available=False,
        reason=reason,
    )


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
        str(AGENT_TOOL),
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


def _parse_planner_payload(repo: str, stdout: str) -> RepoStatus:
    try:
        payload = json.loads(stdout)
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


def fetch_repo_status(
    repo: str,
    warn_threshold: int = DEFAULT_WARN_THRESHOLD,
    *,
    timeout: float | None = None,
) -> RepoStatus:
    """Query one repo's PR health, bounded by ``timeout`` seconds.

    Raises :class:`RepoUnavailable` if the (possibly-retried) planner cannot
    complete within the budget, so the caller can record a partial result and
    keep going instead of hanging.
    """
    command = planner_command(repo, warn_threshold)
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        if timeout is not None and timeout <= 0:
            raise RepoUnavailable(
                f"{repo}: time budget exhausted before planner attempt {attempt}"
            )
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already SIGKILLed the direct planner child; a
            # single in-flight `git fetch` grandchild (if any) exits on its own.
            budget = "unbounded" if timeout is None else f"{timeout:.0f}s"
            raise RepoUnavailable(
                f"{repo}: planner exceeded {budget} "
                f"(per-PR proxied git fetch fan-out; ~1.35s/PR measured)"
            ) from None
        if result.returncode == 0:
            break
        detail = result.stderr.strip() or result.stdout.strip()
        retryable = "504" in detail or "changed during collection" in detail
        if not retryable or attempt == MAX_FETCH_ATTEMPTS:
            break
        # Bounded backoff, but never sleep past the remaining budget.
        backoff = float(attempt)
        if timeout is not None:
            elapsed = time.monotonic() - started
            timeout = max(0.0, timeout - elapsed)
            backoff = min(backoff, timeout)
        time.sleep(backoff)
    assert result is not None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{repo}: agent-utils pr-landing-planner failed: {detail}")
    return _parse_planner_payload(repo, result.stdout)


def collect_statuses(
    repos: Sequence[str],
    warn_threshold: int,
    *,
    per_repo_timeout: float,
    overall_deadline: float,
) -> list[RepoStatus]:
    """Query every repo, always returning one status each (partial on failure)."""
    deadline = time.monotonic() + overall_deadline
    statuses: list[RepoStatus] = []
    for repo in repos:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            statuses.append(
                _unavailable(
                    repo,
                    f"overall deadline {overall_deadline:.0f}s exhausted "
                    f"before querying {repo}",
                )
            )
            continue
        budget = min(per_repo_timeout, remaining)
        try:
            statuses.append(fetch_repo_status(repo, warn_threshold, timeout=budget))
        except RepoUnavailable as unavailable:
            statuses.append(_unavailable(repo, str(unavailable)))
        except RuntimeError as error:
            # A hard planner/schema error still yields a report line rather than
            # aborting the whole command with nothing printed.
            statuses.append(_unavailable(repo, f"query failed: {error}"))
    return statuses


def render_report(statuses: Sequence[RepoStatus], warn_threshold: int) -> str:
    total = sum(status.open for status in statuses if status.available)
    unavailable = [status for status in statuses if not status.available]
    if any(status.unhealthy for status in statuses):
        heading = "CI health: UNHEALTHY"
    elif unavailable:
        heading = (
            f"CI health: DEGRADED (partial: {len(unavailable)} of "
            f"{len(statuses)} repos unavailable)"
        )
    else:
        heading = "CI health: HEALTHY"
    lines = [
        heading,
        "Source: pinned agent-utils/pr-landing-planner status (fresh GitHub query)",
    ]
    for status in statuses:
        if not status.available:
            lines.append(f"  {status.repo}: UNAVAILABLE — {status.reason}")
            continue
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
    if unavailable:
        lines.append(
            f"PARTIAL RESULT: {len(unavailable)} of {len(statuses)} repo(s) "
            "could not be fetched within the time budget; open-PR totals above "
            "cover only the repos that responded."
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
    parser.add_argument(
        "--per-repo-timeout",
        type=float,
        default=DEFAULT_PER_REPO_TIMEOUT,
        help=(
            "seconds to allow one repo's planner query before recording it "
            f"unavailable (default: {DEFAULT_PER_REPO_TIMEOUT:.0f}; "
            "env CI_HUB_PR_STATUS_TIMEOUT)"
        ),
    )
    parser.add_argument(
        "--overall-deadline",
        type=float,
        default=DEFAULT_OVERALL_DEADLINE,
        help=(
            "total seconds across all repos before remaining repos are marked "
            f"unavailable (default: {DEFAULT_OVERALL_DEADLINE:.0f}; "
            "env CI_HUB_PR_STATUS_DEADLINE)"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.warn_threshold < 0:
        parser.error("--warn-threshold must be non-negative")
    if args.per_repo_timeout <= 0:
        parser.error("--per-repo-timeout must be positive")
    if args.overall_deadline <= 0:
        parser.error("--overall-deadline must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS
    statuses = collect_statuses(
        repos,
        args.warn_threshold,
        per_repo_timeout=args.per_repo_timeout,
        overall_deadline=args.overall_deadline,
    )
    if args.json:
        print(
            json.dumps(
                {"repos": [asdict(status) for status in statuses]}, sort_keys=True
            )
        )
    else:
        print(render_report(statuses, args.warn_threshold))
    if any(status.unhealthy for status in statuses):
        return 1
    if any(not status.available for status in statuses):
        # Degraded/partial: cannot fully verify PR health, but we terminated
        # with a report. Non-zero mirrors github_main_health's "cannot verify".
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
