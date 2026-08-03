#!/usr/bin/env python3
"""Summarize open-PR CI health, robustly, on the hosts we actually run ops from.

Two engines:

* ``gh`` (DEFAULT) — a single proxied ``gh pr list --json`` call per repo.
  ``gh`` returns ``mergeable`` and ``statusCheckRollup`` inline, so the whole
  open-PR picture comes back in ONE API round-trip with NO per-PR local
  ``git fetch``. This is the cheap, fast, robust path and it is what actually
  works on 3pai hosts.

* ``planner`` (opt-in, ``--engine planner``) — adapts the pinned
  agent-utils/pr-landing-planner, which additionally does per-PR local
  ``git fetch`` + ``merge-tree`` file-overlap conflict detection. That fan-out
  is slow (~1.35s/PR) and, on BpfJailer-enforced 3pai hosts, its network git
  fetches are intermittently blocked (FILE_OPEN denials) or hang, so it is no
  longer the default.

Why the ``gh`` default and the loudness discipline below matter: this is the
sanctioned ops tool, dispatched every PR-status tick. Its previous per-PR git
fan-out could hang or be silently blocked on 3pai, and *no output plus a zero
exit is indistinguishable from "there are no PRs"* — a monitoring tool that
reports emptiness when it is actually blind will eventually convince someone the
backlog is clear. So: a failed/blocked query is ALWAYS reported as UNAVAILABLE
with an actionable reason and a NON-ZERO exit, never as an empty-but-green
report. The report heading always prints, so stdout is never empty.

Boxing discipline (unchanged intent): per-repo subprocess timeout, an overall
command deadline, bounded retries with backoff on transient network errors, and
partial results plus an explicit statement of what could not be fetched, so the
tool ALWAYS terminates with a report instead of hanging.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
#   planner engine — measured 2026-08-03 on devbig014: the reverie planner
#   completed in 35.17s for 26 open PRs => ~1.35s per PR of sequential proxied
#   `git fetch`. Hermit had 128 open PRs => ~173s happy-path; combined ~208s.
#   gh engine — a single `gh pr list` API call per repo returns in a few
#   seconds regardless of PR count, so the same budgets are comfortably ample
#   headroom for network variance and leave a stalled call bounded.
DEFAULT_PER_REPO_TIMEOUT = float(os.environ.get("CI_HUB_PR_STATUS_TIMEOUT", "300"))
DEFAULT_OVERALL_DEADLINE = float(os.environ.get("CI_HUB_PR_STATUS_DEADLINE", "480"))
# Seconds to wait for the killed planner child to die before moving on.
_TERMINATE_GRACE = 10.0

# Default network wrapper. Bare `gh`/`git` reach GitHub directly, which fails on
# proxied hosts ("network is unreachable"); `with-proxy` is idempotent (wrapping
# an already-proxied command is a no-op), so we prefix it by default when it is
# on PATH. Override with --net-wrapper "" to disable, or --net-wrapper CMD.
DEFAULT_NET_WRAPPER = "with-proxy"

# Fields pulled in the single `gh pr list` call. mergeable + statusCheckRollup
# are what let us classify every open PR without any per-PR local git fetch.
GH_FIELDS = (
    "number",
    "title",
    "isDraft",
    "mergeable",
    "mergeStateStatus",
    "reviewDecision",
    "baseRefName",
    "headRefName",
    "updatedAt",
    "labels",
    "statusCheckRollup",
)

# GitHub check/status vocabularies.
_FAIL_STATES = {
    "FAILURE",
    "ERROR",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
_OK_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED", "COMPLETED"}
# Transient network/GH errors worth a bounded retry.
_RETRYABLE_MARKERS = (
    "stream error",
    "CANCEL",
    "504",
    "502",
    "timeout",
    "timed out",
    "connection reset",
    "temporarily unavailable",
    "changed during collection",
)


class RepoUnavailable(RuntimeError):
    """The status query could not complete within its time budget or was blocked."""


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
    undetermined_reds: int = 0
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


def resolve_net_wrapper(spec: str | None) -> list[str]:
    """Turn a --net-wrapper spec into a command prefix.

    ``None`` selects the default (``with-proxy`` if on PATH, else nothing, with a
    warning). An explicit empty string disables wrapping. A non-empty string is
    split shell-style. A named wrapper that is not on PATH is an error rather
    than a silent bare call that would fail with "network is unreachable".
    """
    if spec is None:
        if shutil.which(DEFAULT_NET_WRAPPER):
            return [DEFAULT_NET_WRAPPER]
        print(
            f"pr_status: note: default net wrapper {DEFAULT_NET_WRAPPER!r} not on "
            "PATH; issuing bare gh/git (may fail on proxied hosts). Pass "
            "--net-wrapper to override.",
            file=sys.stderr,
        )
        return []
    spec = spec.strip()
    if not spec:
        return []
    import shlex

    parts = shlex.split(spec)
    if parts and not shutil.which(parts[0]):
        raise RuntimeError(
            f"--net-wrapper {parts[0]!r} is not on PATH; refusing to run a bare "
            "network call that would fail on a proxied host"
        )
    return parts


# --------------------------------------------------------------------------- #
# gh engine (default)
# --------------------------------------------------------------------------- #


def _rollup_ci_state(rollup: object) -> str:
    """Reduce a statusCheckRollup list to one of red/pending/green.

    Empty rollup (no checks yet) is treated as pending, not green: a PR with no
    checks has not demonstrated health.
    """
    if not isinstance(rollup, list) or not rollup:
        return "pending"
    any_fail = False
    any_pending = False
    any_ok = False
    for check in rollup:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").upper()
        # CheckRun => conclusion; StatusContext => state.
        outcome = str(check.get("conclusion") or check.get("state") or "").upper()
        if status and status != "COMPLETED":
            any_pending = True
        if outcome in _FAIL_STATES:
            any_fail = True
        elif outcome in _OK_STATES:
            any_ok = True
        elif outcome:  # PENDING / EXPECTED / REQUESTED / unknown => not yet done
            any_pending = True
    if any_fail:
        return "red"
    if any_pending:
        return "pending"
    if any_ok:
        return "green"
    return "pending"


def _classify_gh_prs(repo: str, raw: list) -> RepoStatus:
    prs: list[dict[str, object]] = []
    green = red = pending = real_reds = undetermined_reds = 0
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("isDraft") is True:
            continue
        number = entry.get("number")
        ci = _rollup_ci_state(entry.get("statusCheckRollup"))
        mergeable = str(entry.get("mergeable") or "").upper()
        merge_state = str(entry.get("mergeStateStatus") or "").upper()
        # Stale-base vs real: a CONFLICTING/DIRTY red is red because its merge
        # ref is unbuildable (rebase fixes it), NOT a genuine product failure.
        # BUT GitHub computes mergeability lazily, so a cold query returns
        # mergeable=UNKNOWN / merge_state=UNKNOWN; classifying those reds as
        # "real" would falsely spike real_reds/outage and flap between runs.
        # Treat unknown-base reds as their own "undetermined" class so the health
        # signal is stable and honest — a warm re-run resolves them.
        base_known = mergeable in ("MERGEABLE", "CONFLICTING") or merge_state not in (
            "",
            "UNKNOWN",
        )
        stale_base = mergeable == "CONFLICTING" or merge_state == "DIRTY"
        red_class = ""
        if ci == "red":
            red += 1
            if stale_base:
                red_class = "stale-base"
            elif not base_known:
                red_class = "undetermined"
                undetermined_reds += 1
            else:
                red_class = "real-red"
                real_reds += 1
        elif ci == "green":
            green += 1
        else:
            pending += 1
        prs.append(
            {
                "pr": number,
                "ci": ci,
                "red_class": red_class,
                "mergeable": mergeable or "UNKNOWN",
                "merge_state": merge_state or "UNKNOWN",
                "title": entry.get("title", ""),
            }
        )
    open_count = len(prs)
    # Outage heuristic: a large simultaneous *known* real-red fraction smells
    # like infra, not N independent product breaks. Built only from resolved
    # real reds so a cold mergeability query cannot trigger a false outage.
    outage = open_count >= 4 and real_reds >= max(3, (open_count + 1) // 2)
    return RepoStatus(
        repo=repo,
        open=open_count,
        green=green,
        red=red,
        pending=pending,
        real_reds=real_reds,
        undetermined_reds=undetermined_reds,
        outage_suspected=outage,
        prs=tuple(prs),
    )


def fetch_repo_status(
    repo: str,
    warn_threshold: int = DEFAULT_WARN_THRESHOLD,
    *,
    timeout: float | None = None,
) -> RepoStatus:
    """Default single-repo query: the robust gh engine, self-proxied.

    Stable entry point for consumers (e.g. operational_health). ``warn_threshold``
    is accepted for signature compatibility but unused by the gh engine. Raises
    :class:`RepoUnavailable` (a ``RuntimeError``) on any blocked/failed query, so
    a caller catching ``RuntimeError`` still handles it and never mistakes a
    failure for zero open PRs.
    """
    return fetch_repo_status_gh(
        repo, net_wrapper=resolve_net_wrapper(None), timeout=timeout
    )


def fetch_repo_status_gh(
    repo: str,
    *,
    net_wrapper: Sequence[str],
    gh_cmd: str = "gh",
    timeout: float | None = None,
) -> RepoStatus:
    """Query one repo's open-PR health via a single proxied ``gh pr list`` call.

    Raises :class:`RepoUnavailable` on timeout, block, transient-exhaustion, or
    an unparseable/malformed response, so the caller records a partial result
    (UNAVAILABLE) instead of silently reporting zero open PRs.
    """
    command = [
        *net_wrapper,
        gh_cmd,
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        ",".join(GH_FIELDS),
    ]
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        if timeout is not None and timeout <= 0:
            raise RepoUnavailable(
                f"{repo}: time budget exhausted before gh attempt {attempt}"
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
            budget = "unbounded" if timeout is None else f"{timeout:.0f}s"
            raise RepoUnavailable(
                f"{repo}: `gh pr list` exceeded {budget} (proxy stall or gh hang)"
            ) from None
        if result.returncode == 0:
            break
        detail = (result.stderr.strip() or result.stdout.strip() or "").strip()
        # BpfJailer FILE_OPEN denial is not retryable by re-running the same call.
        if "security policy" in detail or "bpfjailer" in detail.lower():
            raise RepoUnavailable(
                f"{repo}: gh/git blocked by BpfJailer security policy "
                f"(FILE_OPEN); ensure the call is proxied. detail: {detail[:400]}"
            )
        retryable = any(m.lower() in detail.lower() for m in _RETRYABLE_MARKERS)
        if not retryable or attempt == MAX_FETCH_ATTEMPTS:
            break
        backoff = float(attempt)
        if timeout is not None:
            elapsed = time.monotonic() - started
            timeout = max(0.0, timeout - elapsed)
            backoff = min(backoff, timeout)
        time.sleep(backoff)
    assert result is not None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RepoUnavailable(f"{repo}: `gh pr list` failed: {detail[:400]}")
    text = result.stdout.strip()
    if not text:
        # gh exited 0 with empty stdout — never treat as "0 open PRs"; that is the
        # blind-but-green failure mode this tool must not produce.
        raise RepoUnavailable(
            f"{repo}: `gh pr list` returned exit 0 but EMPTY output; refusing to "
            "report 0 open PRs from an empty response"
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise RepoUnavailable(f"{repo}: gh returned non-JSON: {text[:200]}") from error
    if not isinstance(raw, list):
        raise RepoUnavailable(f"{repo}: gh returned an unexpected schema (not a list)")
    return _classify_gh_prs(repo, raw)


# --------------------------------------------------------------------------- #
# planner engine (opt-in)
# --------------------------------------------------------------------------- #


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


def fetch_repo_status_planner(
    repo: str,
    warn_threshold: int = DEFAULT_WARN_THRESHOLD,
    *,
    timeout: float | None = None,
) -> RepoStatus:
    """Query one repo's PR health via the planner, bounded by ``timeout`` seconds.

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
        if "security policy" in detail or "bpfjailer" in detail.lower():
            raise RepoUnavailable(
                f"{repo}: planner git fetch blocked by BpfJailer (FILE_OPEN); "
                f"use --engine gh on 3pai hosts. detail: {detail[:300]}"
            )
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


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def collect_statuses(
    repos: Sequence[str],
    warn_threshold: int,
    *,
    engine: str,
    net_wrapper: Sequence[str],
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
            if engine == "planner":
                statuses.append(
                    fetch_repo_status_planner(repo, warn_threshold, timeout=budget)
                )
            else:
                statuses.append(
                    fetch_repo_status_gh(
                        repo, net_wrapper=net_wrapper, timeout=budget
                    )
                )
        except RepoUnavailable as unavailable:
            statuses.append(_unavailable(repo, str(unavailable)))
        except RuntimeError as error:
            # A hard planner/schema error still yields a report line rather than
            # aborting the whole command with nothing printed.
            statuses.append(_unavailable(repo, f"query failed: {error}"))
    return statuses


def render_report(statuses: Sequence[RepoStatus], warn_threshold: int, engine: str) -> str:
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
    source = {
        "gh": "gh pr list --json (single proxied API call per repo)",
        "planner": "pinned agent-utils/pr-landing-planner status (per-PR git fetch)",
    }.get(engine, engine)
    lines = [heading, f"Source: {source}"]
    for status in statuses:
        if not status.available:
            lines.append(f"  {status.repo}: UNAVAILABLE — {status.reason}")
            continue
        undet = (
            f" undetermined_reds={status.undetermined_reds}"
            if status.undetermined_reds
            else ""
        )
        lines.append(
            f"  {status.repo}: open={status.open} green={status.green} "
            f"red={status.red} pending={status.pending} real_reds={status.real_reds}"
            f"{undet} outage={'yes' if status.outage_suspected else 'no'}"
        )
        if status.undetermined_reds:
            lines.append(
                f"    CAUTION: {status.undetermined_reds} red PR(s) have mergeability "
                "still computing (base state UNDETERMINED this query); real-red vs "
                "stale-base unresolved — re-run to classify."
            )
        for pr in status.prs:
            lines.append(
                f"    #{pr.get('pr', '?'):<5} ci={pr.get('ci', 'unknown'):<7} "
                f"class={pr.get('red_class') or '-':<23} {pr.get('title', '')}"
            )
    if unavailable:
        lines.append(
            f"PARTIAL RESULT: {len(unavailable)} of {len(statuses)} repo(s) "
            "could not be queried within the time budget; open-PR totals above "
            "cover only the repos that responded (NOT a claim that they have no "
            "PRs)."
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
    parser.add_argument(
        "--engine",
        choices=("gh", "planner"),
        default=os.environ.get("CI_HUB_PR_STATUS_ENGINE", "gh"),
        help=(
            "status backend: 'gh' (default; single proxied gh API call, no "
            "per-PR git fetch) or 'planner' (agent-utils per-PR fetch + "
            "file-overlap conflict detection)"
        ),
    )
    parser.add_argument(
        "--net-wrapper",
        dest="net_wrapper",
        default=None,
        help=(
            "command prefix for gh (gh engine); default 'with-proxy' if on PATH "
            "(idempotent). Pass '' to disable."
        ),
    )
    parser.add_argument("--warn-threshold", type=int, default=DEFAULT_WARN_THRESHOLD)
    parser.add_argument(
        "--per-repo-timeout",
        type=float,
        default=DEFAULT_PER_REPO_TIMEOUT,
        help=(
            "seconds to allow one repo's query before recording it "
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
    try:
        net_wrapper = resolve_net_wrapper(args.net_wrapper)
    except RuntimeError as error:
        # Configuration error: fail loudly, never silently issue a bare call.
        print(f"CI health: ERROR — {error}", file=sys.stderr)
        return 3
    statuses = collect_statuses(
        repos,
        args.warn_threshold,
        engine=args.engine,
        net_wrapper=net_wrapper,
        per_repo_timeout=args.per_repo_timeout,
        overall_deadline=args.overall_deadline,
    )
    if args.json:
        print(
            json.dumps(
                {"engine": args.engine, "repos": [asdict(s) for s in statuses]},
                sort_keys=True,
            )
        )
    else:
        print(render_report(statuses, args.warn_threshold, args.engine))
    if any(status.unhealthy for status in statuses):
        return 1
    if any(not status.available for status in statuses):
        # Degraded/partial: cannot fully verify PR health, but we terminated
        # with a report. Non-zero mirrors github_main_health's "cannot verify".
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("CI health: ERROR — interrupted", file=sys.stderr)
        raise SystemExit(130)
