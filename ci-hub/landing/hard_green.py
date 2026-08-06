#!/usr/bin/env python3
"""Exact-SHA hard-green authority for Hermit landing.

A commit is hard green when either authoritative execution path passed at that
exact SHA:

* a counted, clean, full local receipt accepted by ``ci-hub validate-status``;
* both GitHub product lanes (portable and privileged) passed at that SHA.

The sources are interchangeable positive evidence.  A genuine contradiction
(one source passed while the other returned a failing answer) is deliberately
reported as DISAGREEMENT rather than silently choosing a winner.  Missing,
queued, cancelled, or unavailable evidence is NO_RESULT and never becomes red
or green by inference.

Exit codes: 0 HARD_GREEN, 3 HARD_RED/DISAGREEMENT, 4 NO_RESULT, 2 ERROR.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci-hub"))
from check_outcome import (  # noqa: E402
    CheckOutcome,
    classify_check,
    select_latest_checks,
    select_latest_workflow_run,
)


EXIT_GREEN = 0
EXIT_ERROR = 2
EXIT_RED = 3
EXIT_NO_RESULT = 4

STATE_PASSED = "passed"
STATE_FAILED = "failed"
STATE_NO_RESULT = "no_result"
STATE_ERROR = "error"

DEFAULT_REPO = "rrnewton/hermit"
LOCAL_AUTHORITY = ROOT / "ci-hub" / "ci-hub"

LANES = (
    {
        "name": "portable",
        "workflow": "ci-portable.yml",
        "job": "Regular tests (GitHub-managed portable)",
        "events": ("workflow_dispatch", "pull_request", "push", "merge_group"),
    },
    {
        "name": "privileged",
        "workflow": "ci-privileged.yml",
        "job": "Privileged capability and E2E tests",
        "events": ("workflow_dispatch", "pull_request", "push"),
    },
)


class AuthorityError(RuntimeError):
    pass


def _run(command: Sequence[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout, check=False
    )


def _valid_sha(sha: str) -> bool:
    return len(sha) == 40 and all(char in "0123456789abcdef" for char in sha)


def local_status(sha: str, *, authority: Path = LOCAL_AUTHORITY) -> dict[str, Any]:
    """Dereference the one local-receipt verifier for ``sha``."""
    try:
        result = _run([str(authority), "validate-status", "--sha", sha, "--json"])
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "state": STATE_ERROR,
            "authority": "local-full-validate",
            "sha": sha,
            "reason": f"validate-status unavailable: {error}",
        }
    try:
        report = json.loads(result.stdout)
    except ValueError:
        return {
            "state": STATE_ERROR,
            "authority": "local-full-validate",
            "sha": sha,
            "reason": f"validate-status emitted unparseable output (rc={result.returncode})",
        }

    verdict = str(report.get("verdict") or "")
    qualifying = report.get("newest_qualifying")
    if verdict == "VALIDATED" and int(report.get("qualifying_count") or 0) > 0 and qualifying:
        return {
            "state": STATE_PASSED,
            "authority": "local-full-validate",
            "sha": sha,
            "receipt": qualifying,
            "qualifying_count": report.get("qualifying_count"),
            "ledger": report.get("ledger"),
        }
    if verdict == "FAILED":
        return {
            "state": STATE_FAILED,
            "authority": "local-full-validate",
            "sha": sha,
            "verdict": verdict,
            "disqualified_count": report.get("disqualified_count"),
        }
    return {
        "state": STATE_NO_RESULT,
        "authority": "local-full-validate",
        "sha": sha,
        "verdict": verdict or "UNAVAILABLE",
        "reason": "no qualifying exact-SHA full local receipt",
    }


def _gh_command() -> list[str]:
    command = ["gh"]
    if shutil.which("with-proxy"):
        command.insert(0, "with-proxy")
    return command


def _gh_json(endpoint: str) -> Any:
    try:
        result = _run([*_gh_command(), "api", endpoint])
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityError(f"GitHub authority unavailable for {endpoint}: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise AuthorityError(f"GitHub authority failed for {endpoint}: {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise AuthorityError(f"GitHub authority emitted invalid JSON for {endpoint}") from error


def _outcome_state(status: object, conclusion: object) -> str:
    outcome = classify_check(status, conclusion)
    if outcome is CheckOutcome.PASSED:
        return STATE_PASSED
    if outcome is CheckOutcome.FAILED:
        return STATE_FAILED
    return STATE_NO_RESULT


def github_lane_status(repo: str, sha: str, lane: Mapping[str, Any]) -> dict[str, Any]:
    """Mirror merge-gate's exact-run + exact-job selection for one hosted lane."""
    runs = _gh_json(
        f"repos/{repo}/actions/workflows/{lane['workflow']}/runs?head_sha={sha}&per_page=100"
    )
    run = select_latest_workflow_run(
        runs, head_sha=sha, events=tuple(lane["events"])
    )
    run_state = _outcome_state(run.get("status"), run.get("conclusion")) if run else STATE_NO_RESULT
    run_id = run.get("id") if run else None
    job: dict[str, Any] = {}
    if run_id:
        jobs = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
        selected = select_latest_checks(jobs.get("jobs", []), head_sha=sha)
        job = next((item for item in selected if item.get("name") == lane["job"]), {})

    state = _outcome_state(job.get("status"), job.get("conclusion")) if job else STATE_NO_RESULT
    # A workflow-level genuine failure still counts when the named job never
    # materialized (checkout/configuration failures are real hosted answers).
    if not job and run_state == STATE_FAILED:
        state = STATE_FAILED
    return {
        "state": state,
        "authority": f"github-{lane['name']}",
        "sha": sha,
        "workflow": lane["workflow"],
        "job_name": lane["job"],
        "run_id": run_id,
        "run_attempt": run.get("run_attempt") if run else None,
        "event": run.get("event") if run else None,
        "workflow_path": run.get("path") if run else None,
        "run_status": run.get("status") if run else None,
        "run_conclusion": run.get("conclusion") if run else None,
        "run_url": run.get("html_url") if run else None,
        "job_id": job.get("id"),
        "job_status": job.get("status"),
        "job_conclusion": job.get("conclusion"),
        "job_url": job.get("html_url"),
    }


def github_status(repo: str, sha: str) -> dict[str, Any]:
    lanes = []
    for lane in LANES:
        try:
            lanes.append(github_lane_status(repo, sha, lane))
        except AuthorityError as error:
            lanes.append({
                "state": STATE_ERROR,
                "authority": f"github-{lane['name']}",
                "sha": sha,
                "workflow": lane["workflow"],
                "job_name": lane["job"],
                "reason": str(error),
            })
    states = {lane["state"] for lane in lanes}
    if STATE_FAILED in states:
        state = STATE_FAILED
    elif all(lane["state"] == STATE_PASSED for lane in lanes):
        state = STATE_PASSED
    elif STATE_ERROR in states:
        state = STATE_ERROR
    else:
        state = STATE_NO_RESULT
    return {
        "state": state,
        "authority": "github-portable+privileged",
        "sha": sha,
        "lanes": lanes,
    }


def combine(local: Mapping[str, Any], github: Mapping[str, Any]) -> dict[str, Any]:
    """Apply OR-for-green while keeping genuine source contradictions visible."""
    sources = [dict(local), dict(github)]
    passed = [source for source in sources if source.get("state") == STATE_PASSED]
    failed = [source for source in sources if source.get("state") == STATE_FAILED]
    if passed and failed:
        verdict = "DISAGREEMENT"
        exit_code = EXIT_RED
        reason = "one exact-SHA authority passed while another returned a genuine failure"
    elif passed:
        verdict = "HARD_GREEN"
        exit_code = EXIT_GREEN
        reason = "at least one interchangeable exact-SHA validation authority passed"
    elif failed:
        verdict = "HARD_RED"
        exit_code = EXIT_RED
        reason = "no authority passed and at least one returned a genuine failure"
    else:
        verdict = "NO_RESULT"
        exit_code = EXIT_NO_RESULT
        reason = "neither exact-SHA authority has a passing or failing answer"
    return {
        "schema_version": 1,
        "sha": local.get("sha") or github.get("sha"),
        "verdict": verdict,
        "exit_code": exit_code,
        "reason": reason,
        "passing_authorities": [source.get("authority") for source in passed],
        "sources": {"local": dict(local), "github": dict(github)},
    }


def status(sha: str, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    return combine(local_status(sha), github_status(repo, sha))


def _render(report: Mapping[str, Any]) -> str:
    authorities = ",".join(report.get("passing_authorities", [])) or "none"
    return (
        f"{report['verdict']} {report['sha']} authorities={authorities} -- "
        f"{report['reason']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _valid_sha(args.sha):
        print("hard-green: --sha must be a lowercase 40-hex commit", file=sys.stderr)
        return EXIT_ERROR
    report = status(args.sha, repo=args.repo)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
