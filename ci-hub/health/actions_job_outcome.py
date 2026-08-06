#!/usr/bin/env python3
"""Fail-closed authority for GitHub Actions setup-only job failures.

The PR rollup is a useful index, not enough evidence to distinguish a product
failure from a runner that never started the workflow.  This module dereferences
one exact Actions job and accepts the narrow setup-only shape.  Every mismatch is
an ordinary failed verification; callers must retain the original red verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import re
import subprocess
import time
from urllib.parse import urlsplit


MAX_JOB_DEREFERENCES = 32
MAX_JOB_RESPONSE_BYTES = 256 * 1024
MAX_JOB_STEPS = 128
DEFAULT_JOB_CALL_TIMEOUT = 5.0
DEFAULT_JOB_TOTAL_TIMEOUT = 30.0

_ACTIONS_JOB_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/actions/runs/(?P<run>[1-9][0-9]*)/job/(?P<job>[1-9][0-9]*)$"
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class JobReference:
    repo: str
    run_id: int
    job_id: int
    html_url: str


@dataclass(frozen=True)
class SetupOnlyVerification:
    """Result of dereferencing one exact failed check.

    ``accepted`` is the only value that changes a rollup classification.  The
    reason is retained for operator-visible diagnostics when evidence cannot be
    established.
    """

    accepted: bool
    reason: str
    run_id: int | None = None
    job_id: int | None = None


def _reject(
    reason: str, reference: JobReference | None = None
) -> SetupOnlyVerification:
    return SetupOnlyVerification(
        accepted=False,
        reason=reason,
        run_id=reference.run_id if reference else None,
        job_id=reference.job_id if reference else None,
    )


def _canonical_reference(
    repo: str, check: Mapping[str, object]
) -> tuple[JobReference | None, str]:
    if check.get("__typename") != "CheckRun":
        return None, "rollup entry is not a CheckRun"
    url = check.get("detailsUrl")
    if not isinstance(url, str) or not url:
        return None, "CheckRun has no detailsUrl"
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        return None, "detailsUrl is not a canonical github.com Actions job URL"
    match = _ACTIONS_JOB_PATH.fullmatch(parsed.path)
    if match is None:
        return None, "detailsUrl is not an exact Actions run/job URL"
    observed_repo = f"{match.group('owner')}/{match.group('repo')}"
    if observed_repo != repo:
        return None, f"detailsUrl repository mismatch: {observed_repo!r} != {repo!r}"
    return (
        JobReference(
            repo=repo,
            run_id=int(match.group("run")),
            job_id=int(match.group("job")),
            html_url=url,
        ),
        "",
    )


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def verify_setup_only_job_payload(
    *,
    repo: str,
    check: Mapping[str, object],
    head_sha: str,
    payload: object,
) -> SetupOnlyVerification:
    """Verify the complete identity and sole-step shape of one Actions job."""
    reference, reference_error = _canonical_reference(repo, check)
    if reference is None:
        return _reject(reference_error)
    if _FULL_SHA.fullmatch(head_sha) is None:
        return _reject("PR head is not an exact lowercase 40-hex SHA", reference)
    if not isinstance(payload, Mapping):
        return _reject("job API response is not an object", reference)
    if any(key in payload for key in ("jobs", "total_count", "next")):
        return _reject("job API response is a paginated collection", reference)

    expected_api_root = f"https://api.github.com/repos/{repo}"
    exact_fields: tuple[tuple[str, object], ...] = (
        ("id", reference.job_id),
        ("run_id", reference.run_id),
        ("head_sha", head_sha),
        ("url", f"{expected_api_root}/actions/jobs/{reference.job_id}"),
        ("html_url", reference.html_url),
        ("run_url", f"{expected_api_root}/actions/runs/{reference.run_id}"),
        ("check_run_url", f"{expected_api_root}/check-runs/{reference.job_id}"),
        ("status", "completed"),
        ("conclusion", "failure"),
        ("name", check.get("name")),
        ("workflow_name", check.get("workflowName")),
        ("started_at", check.get("startedAt")),
        ("completed_at", check.get("completedAt")),
    )
    if not isinstance(check.get("name"), str) or not check.get("name"):
        return _reject("CheckRun name is missing", reference)
    if not isinstance(check.get("workflowName"), str) or not check.get("workflowName"):
        return _reject("CheckRun workflowName is missing", reference)
    if check.get("status") != "COMPLETED" or check.get("conclusion") != "FAILURE":
        return _reject("CheckRun is not a completed failure", reference)
    if _positive_int(payload.get("id")) != reference.job_id:
        return _reject("job id is missing, non-integer, or mismatched", reference)
    if _positive_int(payload.get("run_id")) != reference.run_id:
        return _reject("job run_id is missing, non-integer, or mismatched", reference)
    for field, expected in exact_fields:
        observed = payload.get(field)
        if observed != expected:
            return _reject(
                f"job {field} mismatch: {observed!r} != {expected!r}", reference
            )

    created = _timestamp(payload.get("created_at"))
    started = _timestamp(payload.get("started_at"))
    completed = _timestamp(payload.get("completed_at"))
    if created is None or started is None or completed is None:
        return _reject("job timestamps are missing or malformed", reference)
    if not created <= started <= completed:
        return _reject("job timestamps are out of order", reference)

    steps = payload.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        return _reject("job steps are missing or malformed", reference)
    if len(steps) > MAX_JOB_STEPS:
        return _reject(
            f"job step bound exceeded: {len(steps)} > {MAX_JOB_STEPS}", reference
        )
    # This deliberately rejects even a later successful/skipped step with a
    # timestamp: the accepted authority says no workflow/product step started.
    if len(steps) != 1:
        return _reject(
            f"setup-only job must contain exactly one step, observed {len(steps)}",
            reference,
        )
    step = steps[0]
    if not isinstance(step, Mapping):
        return _reject("setup step is not an object", reference)
    expected_step: tuple[tuple[str, object], ...] = (
        ("name", "Set up job"),
        ("number", 1),
        ("status", "completed"),
        ("conclusion", "failure"),
    )
    for field, expected in expected_step:
        observed = step.get(field)
        if observed != expected or (
            field == "number" and _positive_int(observed) is None
        ):
            return _reject(
                f"setup step {field} mismatch: {observed!r} != {expected!r}",
                reference,
            )
    step_started = _timestamp(step.get("started_at"))
    step_completed = _timestamp(step.get("completed_at"))
    if step_started is None or step_completed is None:
        return _reject("setup step timestamps are missing or malformed", reference)
    if not started <= step_started <= step_completed <= completed:
        return _reject("setup step timestamps are outside the job interval", reference)
    return SetupOnlyVerification(
        accepted=True,
        reason="exact job executed only failed Set up job; zero workflow steps ran",
        run_id=reference.run_id,
        job_id=reference.job_id,
    )


@dataclass(frozen=True)
class _FetchResult:
    payload: object | None
    error: str


class GitHubActionsJobAuthority:
    """Bounded, cached exact-job dereferencer used by the gh PR-status engine."""

    def __init__(
        self,
        repo: str,
        *,
        net_wrapper: Sequence[str],
        gh_cmd: str = "gh",
        deadline: float | None = None,
        max_dereferences: int = MAX_JOB_DEREFERENCES,
        call_timeout: float = DEFAULT_JOB_CALL_TIMEOUT,
    ) -> None:
        self.repo = repo
        self.net_wrapper = tuple(net_wrapper)
        self.gh_cmd = gh_cmd
        self.deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + DEFAULT_JOB_TOTAL_TIMEOUT
        )
        self.max_dereferences = max(0, max_dereferences)
        self.call_timeout = max(0.0, call_timeout)
        self._dereferences = 0
        self._cache: dict[int, _FetchResult] = {}

    def __call__(
        self, repo: str, check: Mapping[str, object], head_sha: str
    ) -> SetupOnlyVerification:
        if repo != self.repo:
            return _reject(f"authority repository mismatch: {repo!r} != {self.repo!r}")
        reference, reference_error = _canonical_reference(repo, check)
        if reference is None:
            return _reject(reference_error)
        fetched = self._cache.get(reference.job_id)
        if fetched is None:
            if self._dereferences >= self.max_dereferences:
                return _reject("exact-job dereference budget exhausted", reference)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.call_timeout <= 0:
                return _reject("exact-job deadline exhausted", reference)
            self._dereferences += 1
            fetched = self._fetch(reference, timeout=min(self.call_timeout, remaining))
            self._cache[reference.job_id] = fetched
        if fetched.error:
            return _reject(fetched.error, reference)
        return verify_setup_only_job_payload(
            repo=repo,
            check=check,
            head_sha=head_sha,
            payload=fetched.payload,
        )

    def _fetch(self, reference: JobReference, *, timeout: float) -> _FetchResult:
        command = [
            *self.net_wrapper,
            self.gh_cmd,
            "api",
            "--method",
            "GET",
            f"repos/{self.repo}/actions/jobs/{reference.job_id}",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return _FetchResult(None, f"exact-job API exceeded {timeout:.1f}s")
        except OSError as error:
            return _FetchResult(None, f"exact-job API could not start: {error}")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            return _FetchResult(None, f"exact-job API failed: {detail[:300]}")
        if len(result.stdout.encode("utf-8")) > MAX_JOB_RESPONSE_BYTES:
            return _FetchResult(
                None,
                "exact-job API response exceeded "
                f"{MAX_JOB_RESPONSE_BYTES} byte bound",
            )
        if not result.stdout.strip():
            return _FetchResult(None, "exact-job API returned empty output")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return _FetchResult(None, "exact-job API returned malformed JSON")
        if not isinstance(payload, Mapping):
            return _FetchResult(None, "exact-job API returned a non-object schema")
        return _FetchResult(payload, "")
