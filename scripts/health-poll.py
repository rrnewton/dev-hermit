#!/usr/bin/env python3
"""Read-only, multi-frequency health poller for the dev-hermit workspace.

This phase is intentionally dry-run only. It does not persist cadence state,
install a scheduler, send terminal input, or mutate GitHub/repository state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_FILE = Path.home() / ".dev-hermit-health-poll-state"
HERMIT_REPO = "rrnewton/hermit"
REVERIE_REPO = "rrnewton/reverie"
AUTHORITATIVE_WORKFLOW = "CI (GitHub-managed portable)"
TERMINAL_FAILURES = {
    "action_required",
    "cancelled",
    "failure",
    "startup_failure",
    "stale",
    "timed_out",
}


@dataclass(frozen=True)
class Action:
    kind: str
    priority: str
    message: str


@dataclass(frozen=True)
class Result:
    status: str
    detail: str
    actions: tuple[Action, ...] = ()
    events: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class Check:
    name: str
    cadence_secs: int
    run: Callable[["Context"], Result]


@dataclass
class Context:
    root: Path
    now: int
    api: "GitHubAPI"
    recent_actions: int
    window_hours: int
    cache: dict[str, object] = field(default_factory=dict)


def quote(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def emit_check(name: str, cadence: int, result: Result) -> None:
    print(
        f"CHECK: {name} {result.status} cadence_secs={cadence} "
        f"detail={quote(result.detail)}"
    )
    for event in result.events:
        fields = " ".join(f"{key}={quote(value)}" for key, value in event.items())
        print(f"EVENT: {name} {fields}")
    for action in result.actions:
        print(
            f"ACTION: {action.kind} check={name} priority={action.priority} "
            f"message={quote(action.message)}"
        )


def parse_iso8601(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def compact_age(now: int, then: int | None) -> str:
    if then is None:
        return "unknown"
    seconds = max(0, now - then)
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def load_fired_state(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    fired: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        try:
            fired[name.strip()] = int(value.strip())
        except ValueError:
            continue
    return fired


def is_due(name: str, cadence_secs: int, now: int, fired: Mapping[str, int]) -> bool:
    if cadence_secs <= 0 or name not in fired:
        return True
    return now - fired[name] >= cadence_secs


class GitHubAPI:
    def __init__(self, base_url: str = "https://api.github.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.cache: dict[str, object] = {}

    def get(self, path: str, params: Mapping[str, object] | None = None) -> object:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        if url in self.cache:
            return self.cache[url]

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "dev-hermit-health-poll/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.load(response)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            raise RuntimeError(f"GET {url} failed: {error}") from error
        self.cache[url] = payload
        return payload


def _object_list(payload: object, key: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise RuntimeError(f"GitHub response is missing list field {key!r}")
    return [item for item in payload[key] if isinstance(item, dict)]


def github_runs(ctx: Context) -> list[dict[str, object]]:
    cached = ctx.cache.get("github_runs")
    if isinstance(cached, list):
        return cached
    runs: list[dict[str, object]] = []
    for page in range(1, 6):
        payload = ctx.api.get(
            f"repos/{HERMIT_REPO}/actions/workflows/ci-portable.yml/runs",
            {"branch": "main", "event": "push", "per_page": 100, "page": page},
        )
        batch = _object_list(payload, "workflow_runs")
        runs.extend(batch)
        if len(batch) < 100:
            break
    ctx.cache["github_runs"] = runs
    return runs


def github_all_runs(ctx: Context) -> list[dict[str, object]]:
    cached = ctx.cache.get("github_all_runs")
    if isinstance(cached, list):
        return cached
    payload = ctx.api.get(
        f"repos/{HERMIT_REPO}/actions/runs",
        {"branch": "main", "per_page": 100},
    )
    runs = _object_list(payload, "workflow_runs")
    ctx.cache["github_all_runs"] = runs
    return runs


def github_main_sha(ctx: Context) -> str:
    cached = ctx.cache.get("github_main_sha")
    if isinstance(cached, str):
        return cached
    payload = ctx.api.get(f"repos/{HERMIT_REPO}/commits/main")
    if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
        raise RuntimeError("GitHub main response is missing sha")
    sha_value = payload["sha"]
    assert isinstance(sha_value, str)
    sha = sha_value
    ctx.cache["github_main_sha"] = sha
    return sha


def authoritative_runs(
    runs: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [
        run
        for run in runs
        if run.get("name") == AUTHORITATIVE_WORKFLOW
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
    ]


def classify_current_ci(
    runs: Sequence[Mapping[str, object]], main_sha: str
) -> tuple[str, str, tuple[Action, ...]]:
    current = next(
        (run for run in authoritative_runs(runs) if run.get("head_sha") == main_sha),
        None,
    )
    short = main_sha[:12]
    if current is None:
        action = Action(
            "investigate-main-ci",
            "P0",
            f"No {AUTHORITATIVE_WORKFLOW} push run is attached to current main {short}.",
        )
        return "WARN", f"main={short} authoritative_run=missing", (action,)

    status = str(current.get("status") or "unknown").lower()
    conclusion = str(current.get("conclusion") or "").lower()
    url = str(current.get("html_url") or "")
    if status != "completed":
        action = Action(
            "monitor-main-ci",
            "P1",
            f"Current main {short} authoritative CI is {status}; follow {url} until terminal.",
        )
        return (
            "WARN",
            f"main={short} status={status} conclusion=pending url={url}",
            (action,),
        )
    if conclusion == "success":
        return "OK", f"main={short} status=completed conclusion=success url={url}", ()

    action = Action(
        "repair-main-ci",
        "P0",
        f"Current main {short} authoritative CI concluded {conclusion or 'unknown'}; triage {url}.",
    )
    return (
        "CRIT",
        f"main={short} status=completed conclusion={conclusion or 'unknown'} url={url}",
        (action,),
    )


def check_ci(ctx: Context) -> Result:
    try:
        status, detail, actions = classify_current_ci(
            github_runs(ctx), github_main_sha(ctx)
        )
        return Result(status, detail, actions)
    except RuntimeError as error:
        return Result(
            "UNKNOWN",
            str(error),
            (
                Action(
                    "restore-ci-observability",
                    "P0",
                    f"CI health could not be read: {error}",
                ),
            ),
        )


def _open_pr_count(ctx: Context, repo: str) -> int:
    payload = ctx.api.get(
        "search/issues",
        {"q": f"repo:{repo} is:pr is:open", "per_page": 1},
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("total_count"), int):
        raise RuntimeError(f"GitHub PR search for {repo} is missing total_count")
    total_count = payload["total_count"]
    assert isinstance(total_count, int)
    return total_count


def check_pr_count(ctx: Context) -> Result:
    try:
        counts = {
            repo: _open_pr_count(ctx, repo) for repo in (HERMIT_REPO, REVERIE_REPO)
        }
    except RuntimeError as error:
        return Result(
            "UNKNOWN",
            str(error),
            (
                Action(
                    "restore-pr-observability",
                    "P0",
                    f"Open PR count could not be read: {error}",
                ),
            ),
        )

    total = sum(counts.values())
    events = tuple({"repo": repo, "open": str(count)} for repo, count in counts.items())
    detail = f"total={total} hermit={counts[HERMIT_REPO]} reverie={counts[REVERIE_REPO]} warn=5 critical=10"
    if total >= 10:
        return Result(
            "CRIT",
            detail,
            (
                Action(
                    "reduce-pr-backlog",
                    "P0",
                    f"{total} PRs are open across Hermit and Reverie; stop opening avoidable PRs and prioritize review, CI repair, and landing.",
                ),
            ),
            events,
        )
    if total >= 5:
        return Result(
            "WARN",
            detail,
            (
                Action(
                    "reduce-pr-backlog",
                    "P1",
                    f"{total} PRs are open across Hermit and Reverie; shift available capacity toward landing.",
                ),
            ),
            events,
        )
    return Result("OK", detail, (), events)


def _run_git(
    repo: Path, args: Sequence[str], timeout: int = 20
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _git_output(repo: Path, *args: str) -> str | None:
    try:
        result = _run_git(repo, args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _status_entries(repo: Path) -> list[str] | None:
    try:
        result = _run_git(
            repo, ("status", "--porcelain=v1", "--untracked-files=normal")
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines() if result.stdout.strip() else []


def _gitlink(root: Path, name: str) -> str | None:
    output = _git_output(root, "ls-tree", "HEAD", name)
    if not output:
        return None
    fields = output.split()
    return fields[2] if len(fields) >= 3 else None


def check_repo_hygiene(ctx: Context) -> Result:
    repos = {
        "parent": ctx.root,
        "hermit": ctx.root / "hermit",
        "reverie": ctx.root / "reverie",
        "liteinst2": ctx.root / "liteinst2",
    }
    findings: list[str] = []
    actions: list[Action] = []
    events: list[Mapping[str, str]] = []

    for label, repo in repos.items():
        if not (repo / ".git").exists():
            findings.append(f"{label}:missing-checkout")
            continue
        entries = _status_entries(repo)
        branch = _git_output(repo, "branch", "--show-current") or "detached"
        head = _git_output(repo, "rev-parse", "HEAD") or "unknown"
        if entries is None:
            findings.append(f"{label}:status-unavailable")
            actions.append(
                Action(
                    "restore-repository-observability",
                    "P0",
                    f"git status failed for {label}; do not treat that checkout as clean until status is readable.",
                )
            )
            entries = []
        events.append(
            {
                "repo": label,
                "branch": branch,
                "head": head[:12],
                "dirty": str(len(entries)),
            }
        )
        if entries:
            samples = ", ".join(entry[:120] for entry in entries[:3])
            findings.append(f"{label}:dirty={len(entries)}[{samples}]")

    expected_main = ("parent", "hermit", "reverie")
    for label in expected_main:
        repo = repos[label]
        branch = _git_output(repo, "branch", "--show-current") or "detached"
        if branch != "main":
            findings.append(f"{label}:branch={branch},expected=main")
            actions.append(
                Action(
                    "repair-primary-branch",
                    "P0",
                    f"{label} primary is on {branch}, not main; preserve any work and restore the primary-checkout invariant.",
                )
            )
        primary_head = _git_output(repo, "rev-parse", "HEAD")
        origin_main = _git_output(repo, "rev-parse", "origin/main")
        if primary_head and origin_main and primary_head != origin_main:
            if label == "parent":
                counts = _git_output(
                    repo, "rev-list", "--left-right", "--count", "HEAD...origin/main"
                )
                try:
                    ahead, behind = (int(value) for value in (counts or "").split())
                except ValueError:
                    findings.append("parent:origin-divergence-unavailable")
                else:
                    if behind:
                        findings.append(
                            f"parent:ahead={ahead},behind={behind},origin-main={origin_main[:12]}"
                        )
            else:
                findings.append(
                    f"{label}:head={primary_head[:12]},origin-main={origin_main[:12]}"
                )

    pin_findings: list[str] = []
    for label in ("hermit", "reverie", "liteinst2"):
        repo = repos[label]
        recorded = _gitlink(ctx.root, label)
        checkout = _git_output(repo, "rev-parse", "HEAD")
        origin_main = _git_output(repo, "rev-parse", "origin/main")
        if recorded and checkout and recorded != checkout:
            pin_findings.append(
                f"{label}:gitlink={recorded[:12]},checkout={checkout[:12]}"
            )
        if recorded and origin_main and recorded != origin_main:
            pin_findings.append(
                f"{label}:gitlink={recorded[:12]},cached-origin-main={origin_main[:12]}"
            )
    findings.extend(pin_findings)

    if any(":dirty=" in finding for finding in findings):
        actions.append(
            Action(
                "attribute-dirty-state",
                "P0",
                "Dirty checkout state exists; identify its owner and preserve it before any cleanup, integration, or pin update.",
            )
        )
    if pin_findings:
        actions.append(
            Action(
                "review-submodule-pins",
                "P1",
                "Parent gitlinks differ from a primary checkout and/or cached origin/main; confirm intentional pins and commit only reviewed pointer advances.",
            )
        )

    if findings:
        return Result("WARN", "; ".join(findings), tuple(actions), tuple(events))
    return Result(
        "OK",
        "parent and primaries clean; main invariants and cached gitlinks agree",
        (),
        tuple(events),
    )


def _run_lint(name: str, command: Sequence[str], cwd: Path) -> tuple[str, str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return "error", f"{name}: {error}"
    output = " ".join((result.stdout + "\n" + result.stderr).split())
    return (
        "pass" if result.returncode == 0 else "fail"
    ), f"{name}:rc={result.returncode}:{output[:260]}"


def check_repository_lints(ctx: Context) -> Result:
    lint_commands = (
        ("portable-paths", (str(ctx.root / "scripts" / "check-portable-paths.sh"),)),
        ("whitespace", ("git", "diff", "--check", "HEAD")),
    )
    outcomes = [_run_lint(name, command, ctx.root) for name, command in lint_commands]
    failures = [detail for state, detail in outcomes if state != "pass"]
    detail = "; ".join(detail for _, detail in outcomes)
    if failures:
        return Result(
            "WARN",
            detail,
            (
                Action(
                    "repair-repository-lints",
                    "P1",
                    "One or more read-only repository lint checks failed: "
                    + "; ".join(failures),
                ),
            ),
        )
    return Result("OK", detail)


def _run_state(run: Mapping[str, object]) -> str:
    status = str(run.get("status") or "unknown").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    return conclusion if status == "completed" and conclusion else status


def check_recent_actions(ctx: Context) -> Result:
    try:
        runs = github_all_runs(ctx)[: ctx.recent_actions]
    except RuntimeError as error:
        return Result("UNKNOWN", str(error))
    events: list[Mapping[str, str]] = []
    failures = 0
    for index, run in enumerate(runs, 1):
        state = _run_state(run)
        if state in TERMINAL_FAILURES:
            failures += 1
        created = str(run.get("created_at") or "")
        events.append(
            {
                "index": str(index),
                "age": compact_age(ctx.now, parse_iso8601(created)),
                "workflow": str(run.get("name") or "unknown"),
                "state": state,
                "sha": str(run.get("head_sha") or "")[:12],
                "url": str(run.get("html_url") or ""),
            }
        )
    status = "WARN" if failures else "OK"
    actions: tuple[Action, ...] = ()
    if failures:
        actions = (
            Action(
                "review-recent-actions",
                "P1",
                f"{failures} of the {len(runs)} most recent main-branch Actions runs have terminal failure states.",
            ),
        )
    return Result(
        status,
        f"shown={len(runs)} requested={ctx.recent_actions} terminal_failures={failures}",
        actions,
        tuple(events),
    )


def summarize_main_window(
    commits: Sequence[Mapping[str, object]],
    runs: Sequence[Mapping[str, object]],
    now: int,
    window_hours: int,
) -> dict[str, int]:
    cutoff = now - window_hours * 3600
    selected: list[Mapping[str, object]] = []
    for commit in commits:
        nested = commit.get("commit")
        if not isinstance(nested, dict):
            continue
        committer = nested.get("committer")
        date = committer.get("date") if isinstance(committer, dict) else None
        stamp = parse_iso8601(str(date or ""))
        if stamp is not None and stamp >= cutoff:
            selected.append(commit)

    latest_by_sha: dict[str, Mapping[str, object]] = {}
    for run in authoritative_runs(runs):
        sha = str(run.get("head_sha") or "")
        if sha and sha not in latest_by_sha:
            latest_by_sha[sha] = run

    counts = {
        "commits": len(selected),
        "green": 0,
        "red": 0,
        "pending": 0,
        "missing": 0,
    }
    for commit in selected:
        sha = str(commit.get("sha") or "")
        matched_run = latest_by_sha.get(sha)
        if matched_run is None:
            counts["missing"] += 1
            continue
        state = _run_state(matched_run)
        if state == "success":
            counts["green"] += 1
        elif state in TERMINAL_FAILURES:
            counts["red"] += 1
        else:
            counts["pending"] += 1
    return counts


def check_main_history(ctx: Context) -> Result:
    try:
        cutoff = ctx.now - ctx.window_hours * 3600
        commits: list[dict[str, object]] = []
        truncated = True
        for page in range(1, 6):
            payload = ctx.api.get(
                f"repos/{HERMIT_REPO}/commits",
                {"sha": "main", "per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                raise RuntimeError("GitHub commit history response is not a list")
            batch = [item for item in payload if isinstance(item, dict)]
            commits.extend(batch)
            if len(batch) < 100:
                truncated = False
                break
            oldest = batch[-1].get("commit") if batch else None
            committer = oldest.get("committer") if isinstance(oldest, dict) else None
            oldest_date = committer.get("date") if isinstance(committer, dict) else None
            oldest_stamp = parse_iso8601(str(oldest_date or ""))
            if oldest_stamp is not None and oldest_stamp < cutoff:
                truncated = False
                break
        counts = summarize_main_window(
            commits, github_runs(ctx), ctx.now, ctx.window_hours
        )
    except RuntimeError as error:
        return Result("UNKNOWN", str(error))

    detail = (
        " ".join(f"{key}={value}" for key, value in counts.items())
        + f" window_hours={ctx.window_hours} truncated={str(truncated).lower()}"
    )
    if counts["red"] or counts["missing"] or truncated:
        action = Action(
            "review-main-health-window",
            "P1",
            f"The {ctx.window_hours}h main window has red={counts['red']}, missing={counts['missing']}, and truncated={str(truncated).lower()} authoritative CI observations; distinguish current regressions from recovered history.",
        )
        return Result("WARN", detail, (action,))
    return Result("OK", detail)


def _latest_match(root: Path, pattern: str) -> Path | None:
    matches = [path for path in root.glob(pattern) if path.is_file()]
    return (
        max(matches, key=lambda path: (path.stat().st_mtime, path.name))
        if matches
        else None
    )


def _artifact_stub(
    ctx: Context,
    name: str,
    pattern: str,
    threshold_secs: int,
    producer_hint: str,
) -> Result:
    latest = _latest_match(ctx.root, pattern)
    if latest is None:
        detail = f"spot_check_only pattern={pattern} candidate=missing threshold_secs={threshold_secs} canonical_marker_contract=TODO"
        message = f"No candidate {name} result matched {pattern}; {producer_hint} must eventually publish a canonical timestamp/status/SHA marker."
    else:
        age = max(0, ctx.now - int(latest.stat().st_mtime))
        detail = f"spot_check_only pattern={pattern} candidate={latest.relative_to(ctx.root)} mtime_age_secs={age} threshold_secs={threshold_secs} canonical_marker_contract=TODO"
        message = f"{name} spot-check found {latest.relative_to(ctx.root)} (mtime age {compact_age(ctx.now, int(latest.stat().st_mtime))}); result semantics are not authoritative until the marker contract is defined."
    return Result("STUB", detail, (Action("define-result-marker", "P2", message),))


def check_stress_result(ctx: Context) -> Result:
    pattern = os.environ.get(
        "HERMIT_HEALTH_STRESS_RESULT_GLOB",
        "experiments/stress-test-under-load_*/results/summary_*.json",
    )
    return _artifact_stub(ctx, "stress-run", pattern, 48 * 3600, "the stress harness")


def check_super_validate_result(ctx: Context) -> Result:
    pattern = os.environ.get(
        "HERMIT_HEALTH_SUPER_VALIDATE_GLOB",
        "health-results/super-validate/*.json",
    )
    return _artifact_stub(
        ctx, "super-validate", pattern, 24 * 3600, "scripts/super-validate.sh"
    )


CHECKS = (
    Check("repo-hygiene", 5 * 60, check_repo_hygiene),
    Check("ci-health", 10 * 60, check_ci),
    Check("outstanding-prs", 15 * 60, check_pr_count),
    Check("recent-actions", 30 * 60, check_recent_actions),
    Check("recent-main-history", 60 * 60, check_main_history),
    Check("repository-lints", 60 * 60, check_repository_lints),
    Check("stress-result-freshness", 6 * 60 * 60, check_stress_result),
    Check("super-validate-freshness", 6 * 60 * 60, check_super_validate_result),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="required: run read-only checks and never persist cadence state",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run every check regardless of read-only cadence state",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"read-only key=epoch cadence state (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument("--now", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--recent-actions",
        type=int,
        default=8,
        metavar="K",
        help="number of recent GitHub Actions runs to display (default: 8)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="sliding main-history window in hours (default: 24)",
    )
    parser.add_argument(
        "--github-api-base", default="https://api.github.com", help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("this phase is dry-run only; pass --dry-run explicitly")
    if args.recent_actions <= 0:
        parser.error("--recent-actions must be positive")
    if args.window_hours <= 0:
        parser.error("--window-hours must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    fired = load_fired_state(args.state_file)
    ctx = Context(
        root=ROOT,
        now=now,
        api=GitHubAPI(args.github_api_base),
        recent_actions=args.recent_actions,
        window_hours=args.window_hours,
    )
    print(
        f"NOTE: health-poll mode=dry-run now={now} state_file={quote(args.state_file)} "
        "state_writes=false scheduler=false prompt_injection=false"
    )
    due: list[str] = []
    skipped: list[str] = []
    for check in CHECKS:
        if not args.force and not is_due(check.name, check.cadence_secs, now, fired):
            remaining = check.cadence_secs - (now - fired[check.name])
            print(
                f"NOTE: skipped check={check.name} reason={quote(f'not due for {remaining}s')}"
            )
            skipped.append(check.name)
            continue
        due.append(check.name)
        try:
            result = check.run(ctx)
        except Exception as error:  # Keep independent checks from hiding one another.
            result = Result(
                "UNKNOWN",
                f"unexpected {type(error).__name__}: {error}",
                (
                    Action(
                        "repair-health-check",
                        "P0",
                        f"{check.name} crashed: {type(error).__name__}: {error}",
                    ),
                ),
            )
        emit_check(check.name, check.cadence_secs, result)

    would_mark = ",".join(due) if due else "none"
    print(
        f"NOTE: summary due={len(due)} skipped={len(skipped)} "
        f"would_mark_fired={quote(would_mark)} state_persisted=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
