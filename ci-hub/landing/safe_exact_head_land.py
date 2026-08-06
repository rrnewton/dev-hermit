#!/usr/bin/env python3
"""Crash-recoverable, no-rewrite exact-head landing for Hermit.

This first version accepts only ``rrnewton/hermit``.  Exact-head validation is
authorized interchangeably by either a counted local full-validation receipt
or the repository's complete authoritative GitHub job set.  Exact-head
role-tagged adversarial-review comments are independently dereferenced before
the merge mutation.  The tool never checks out, rebases, pushes, labels, or
otherwise rewrites the pull-request branch.

The append-only intent store is the recovery authority.  The synchronous
GitHub merge REST call carries ``sha=X`` as the cross-host atomic head guard;
the canonical fleet landing lock retains an exact-operation mutation barrier
until post-merge replay verification and remediation arming both finish.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn, Protocol
from urllib.parse import urlsplit


REMEDIATION_DIR = Path(__file__).resolve().parents[1] / "remediation"
if str(REMEDIATION_DIR) not in sys.path:
    sys.path.insert(0, str(REMEDIATION_DIR))
import protocol as obligation_protocol  # type: ignore[import-not-found]  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_REPO = "rrnewton/hermit"
TARGET_BRANCH = "main"
SCHEMA_VERSION = 1
RECORD_TYPE = "safe-exact-head-landing"
LAND_LOCK_OVERRIDE = "CI_HUB_LANDING_LOCK"
LAND_STORE_OVERRIDE = "CI_HUB_SAFE_EXACT_HEAD_LAND_STORE"
OBLIGATION_STORE_OVERRIDE = "CI_HUB_OBLIGATIONS_STORE"
CI_HUB_PARSE_ONLY = "CI_HUB_DOCS_PARSE_ONLY"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
GREEN_HARD = "hard_green"
GREEN_SOFT = "soft_green"
SOFT_ZERO_CONFLICT = "soft-green(zero-conflict)"
LOCAL_VALIDATION_AUTHORITY = "ci-hub-validate-status"
HOSTED_VALIDATION_AUTHORITY = "github-actions-exact-head-jobs-v1"
REVIEW_AUTHORITY = "github-role-tagged-exact-head-review-v1"
POST_FACTO_REVIEW_LABEL = "post-facto-human-review"
REVIEW_FAMILIES = frozenset({"codex", "claude"})

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 3
EXIT_PENDING = 4

EVENT_TYPES = frozenset(
    {
        "intent",
        "merge_requested",
        "merge_call_started",
        "merge_response",
        "merge_pending",
        "landing_quarantined",
        "landing_verified",
        "arm_failed",
        "obligation_armed",
        "failure",
    }
)
DEFINITIVE_NO_MUTATION_HTTP_STATUSES = frozenset({404, 405, 409, 422})
MAX_HTTP_ENVELOPE_BYTES = 64 * 1024
CANONICAL_LANDING_STORE = ROOT / "ignored/ci-hub/safe-exact-head-landings.jsonl"
CANONICAL_OBLIGATION_STORE = ROOT / "ignored/ci-hub/obligations.jsonl"


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def default_store() -> Path:
    return CANONICAL_LANDING_STORE


class LandingError(RuntimeError):
    """An environmental/tool failure prevented a trustworthy result."""

    exit_code = EXIT_ERROR


class Refused(LandingError):
    """A policy or identity predicate refused the landing."""

    exit_code = EXIT_REFUSED


class NoGreenValidation(Refused):
    """One well-formed validation source has no positive exact-head answer."""


class Pending(LandingError):
    """The atomic merge request has not produced a terminal GitHub state yet."""

    exit_code = EXIT_PENDING


class StoreError(LandingError):
    """The append-only recovery store is malformed or internally inconsistent."""


class ReplayMismatch(Refused):
    """The landed replay does not match the authorized exact-head composition."""

    def __init__(self, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class PullRequestSnapshot:
    number: int
    state: str
    is_draft: bool
    head: str
    base: str
    review_decision: str
    merge_commit: str | None


@dataclass(frozen=True)
class MergeHttpResult:
    http_status: int
    merged: bool | None
    merge_commit: str | None
    message: str
    definitive_no_mutation: bool


@dataclass(frozen=True)
class ReceiptEvidence:
    report: dict[str, Any]
    report_sha256: str
    command: tuple[str, ...]
    authority: str = LOCAL_VALIDATION_AUTHORITY

    def as_json(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "command": list(self.command),
            "report_sha256": self.report_sha256,
            "report": self.report,
        }


@dataclass(frozen=True)
class SourceProvenance:
    observed_base: str
    source_base: str
    source_commits: tuple[str, ...]
    source_tree: str
    observed_base_tree: str

    @property
    def source_commit_count(self) -> int:
        return len(self.source_commits)

    def as_json(self) -> dict[str, Any]:
        return {
            "observed_base": self.observed_base,
            "source_base": self.source_base,
            "source_commit_count": self.source_commit_count,
            "source_commits": list(self.source_commits),
            "source_tree": self.source_tree,
            "observed_base_tree": self.observed_base_tree,
        }


@dataclass(frozen=True)
class ReplayProvenance:
    expected_head: str
    observed_base: str
    source_base: str
    source_commit_count: int
    replay_base: str
    merge_commit: str
    fetched_main: str
    composition_merge_base: str
    expected_tree: str
    actual_tree: str
    replay_commits: tuple[str, ...]
    replay_base_is_ancestor_of_source: bool
    green_class: str
    soft_green: str | None
    source_receipt: dict[str, Any] | None = None
    base_receipt: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "expected_head": self.expected_head,
            "observed_base": self.observed_base,
            "source_base": self.source_base,
            "source_commit_count": self.source_commit_count,
            "replay_base": self.replay_base,
            "merge_commit": self.merge_commit,
            "fetched_main": self.fetched_main,
            "composition_merge_base": self.composition_merge_base,
            "expected_tree": self.expected_tree,
            "actual_tree": self.actual_tree,
            "replay_commits": list(self.replay_commits),
            "replay_base_is_ancestor_of_source": (
                self.replay_base_is_ancestor_of_source
            ),
            "green_class": self.green_class,
            "soft_green": self.soft_green,
            "source_receipt": self.source_receipt,
            "base_receipt": self.base_receipt,
        }


@dataclass(frozen=True)
class LandingResult:
    attempt_id: str
    repo: str
    pr: int
    expected_head: str
    replay_base: str
    merge_commit: str
    obligation_id: str
    recovered: bool
    green_class: str
    soft_green: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "verdict": "LANDED_AND_ARMED",
            "attempt_id": self.attempt_id,
            "repo": self.repo,
            "pr": self.pr,
            "expected_head": self.expected_head,
            "replay_base": self.replay_base,
            "merge_commit": self.merge_commit,
            "obligation_id": self.obligation_id,
            "recovered": self.recovered,
            "green_class": self.green_class,
            "soft_green": self.soft_green,
        }


@dataclass(frozen=True)
class MutationBarrierBinding:
    attempt_id: str
    call_count: int
    last_call_id: str | None


class Runner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]: ...


class MutationBarrier(Protocol):
    def arm(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None: ...

    def bind_call(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
        call_count: int,
        call_id: str,
    ) -> None: ...

    def clear(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None: ...


class SubprocessRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise LandingError(
                f"command timed out after {timeout:g}s: {' '.join(command)}"
            ) from error
        except OSError as error:
            raise LandingError(
                f"cannot execute {' '.join(command)}: {error}"
            ) from error


def _json_object(output: str, authority: str) -> dict[str, Any]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise LandingError(f"{authority} emitted invalid JSON") from error
    if not isinstance(payload, dict):
        raise LandingError(f"{authority} emitted a non-object")
    return payload


def _full_sha(value: object, label: str) -> str:
    text = str(value or "").lower()
    if not SHA_RE.fullmatch(text):
        raise LandingError(f"{label} is not a lowercase 40-hex commit: {value!r}")
    return text


def github_remote_repo(remote: str) -> str | None:
    """Return ``owner/repo`` for unambiguous canonical GitHub HTTPS/SSH URLs."""

    value = remote.strip()
    scp = re.fullmatch(r"git@github\.com:([^/:]+/[^/:]+?)(?:\.git)?", value)
    if scp:
        return scp.group(1)
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme == "https":
        if parsed.netloc != "github.com":
            return None
    elif parsed.scheme == "ssh":
        if (
            parsed.hostname != "github.com"
            or parsed.username != "git"
            or port is not None
            or parsed.netloc != "git@github.com"
        ):
            return None
    else:
        return None
    path = parsed.path
    if not path.startswith("/") or path.endswith("/"):
        return None
    identity = path[1:]
    if identity.endswith(".git"):
        identity = identity[:-4]
    if not re.fullmatch(r"[^/]+/[^/]+", identity):
        return None
    return identity


def _hosted_validation_problem(report: object, expected_head: str) -> str | None:
    if not isinstance(report, Mapping):
        return "hosted validation report is not an object"
    if report.get("schema_version") != 1:
        return "hosted validation report has the wrong schema"
    if report.get("repo") != SUPPORTED_REPO:
        return "hosted validation report is not repository-bound"
    if report.get("sha") != expected_head:
        return "hosted validation report is not exact-SHA-bound"
    if report.get("verdict") != "VALIDATED":
        return "hosted validation report is not green"
    policy = report.get("verification_policy")
    try:
        if not isinstance(policy, Mapping):
            return "hosted validation report has no policy"
        validated_policy = obligation_protocol.validate_verification_policy(policy)
    except obligation_protocol.ProtocolError as error:
        return f"hosted validation policy is invalid: {error}"
    if validated_policy.get("repo") != SUPPORTED_REPO:
        return "hosted validation policy is not repository-bound"
    github = report.get("github")
    if not isinstance(github, Mapping):
        return "hosted validation report has no GitHub result"
    required = validated_policy["github"]["required_jobs"]
    required_count = validated_policy["github"]["required_positive_count"]
    if (
        github.get("state") != "green"
        or github.get("required_positive_count") != required_count
        or github.get("positive_count") != required_count
        or type(required_count) is not int
        or required_count <= 0
    ):
        return "hosted validation report is not a complete positive result"
    jobs = github.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != required_count:
        return "hosted validation report has incomplete job coverage"
    observed: set[tuple[str, str, str]] = set()
    identities: set[tuple[int, int]] = set()
    for job in jobs:
        if not isinstance(job, Mapping) or job.get("state") != "green":
            return "hosted validation report contains a non-green job"
        descriptor = (
            str(job.get("workflow_file") or ""),
            str(job.get("workflow_name") or ""),
            str(job.get("job_name") or ""),
        )
        run_id = job.get("run_id")
        job_id = job.get("job_id")
        if (
            type(run_id) is not int
            or run_id <= 0
            or type(job_id) is not int
            or job_id <= 0
        ):
            return "hosted validation job has no dereferenced identity"
        identity = (run_id, job_id)
        if identity in identities:
            return "hosted validation report repeats a job identity"
        identities.add(identity)
        observed.add(descriptor)
    expected = {
        (job["workflow_file"], job["workflow_name"], job["job_name"])
        for job in required
    }
    if observed != expected:
        return "hosted validation jobs do not equal the registered policy"
    return None


def _receipt_problem(
    receipt: object, expected_head: str, *, require_envelope: bool
) -> str | None:
    """Explain a malformed persisted validation-evidence envelope, if any.

    The local receipt and hosted exact-job set are interchangeable authorities,
    but each envelope remains source-typed.  Crash recovery re-dereferences the
    same source instead of treating this persisted record as authority.
    """

    if not isinstance(receipt, Mapping):
        return "receipt is not an object"
    authority = (
        str(receipt.get("authority") or "")
        if require_envelope
        else LOCAL_VALIDATION_AUTHORITY
    )
    if authority not in {LOCAL_VALIDATION_AUTHORITY, HOSTED_VALIDATION_AUTHORITY}:
        return "receipt has the wrong authority"
    command = receipt.get("command")
    if require_envelope:
        if authority == LOCAL_VALIDATION_AUTHORITY:
            if not isinstance(command, list) or len(command) != 7:
                return "receipt command is malformed"
            if Path(str(command[0])).name != "ci-hub" or command[1:] != [
                "validate-status",
                "--repo",
                SUPPORTED_REPO,
                "--sha",
                expected_head,
                "--json",
            ]:
                return "receipt command is not the exact-repository/head canonical query"
        elif command != [
            "protocol.github_runs",
            "--repo",
            SUPPORTED_REPO,
            "--sha",
            expected_head,
        ]:
            return "hosted command is not the exact-repository/head canonical query"
    report = receipt.get("report") if require_envelope else receipt
    problem = (
        obligation_protocol._local_receipt_problem(
            report,
            repo=SUPPORTED_REPO,
            sha=expected_head,
            returncode=0,
        )
        if authority == LOCAL_VALIDATION_AUTHORITY
        else _hosted_validation_problem(report, expected_head)
    )
    if problem:
        return str(problem)
    if require_envelope:
        digest = receipt.get("report_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "receipt report digest is malformed"
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != digest:
            return "receipt report digest does not match"
    return None


def _review_evidence_problem(
    evidence: object, *, repo: str, pr: int, expected_head: str
) -> str | None:
    if not isinstance(evidence, Mapping):
        return "review evidence is not an object"
    if evidence.get("authority") != REVIEW_AUTHORITY:
        return "review evidence has the wrong authority"
    if evidence.get("repo") != repo or evidence.get("pr") != pr:
        return "review evidence is not bound to the PR"
    if evidence.get("head") != expected_head:
        return "review evidence is not exact-head-bound"
    post_facto = evidence.get("post_facto_human_review")
    if type(post_facto) is not bool:
        return "review evidence has no policy selector"
    required = evidence.get("required_families")
    expected_required = ["claude", "codex"] if post_facto else ["any"]
    if required != expected_required:
        return "review evidence has the wrong required families"
    approvals = evidence.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        return "review evidence has no exact-head approval"
    families: set[str] = set()
    ids: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, Mapping):
            return "review approval is not an object"
        family = str(approval.get("family") or "")
        comment_id = str(approval.get("comment_id") or "")
        if family not in REVIEW_FAMILIES or not comment_id or comment_id in ids:
            return "review approval identity is malformed"
        if approval.get("head") != expected_head or approval.get("verdict") != "PASS":
            return "review approval is stale or non-passing"
        digest = str(approval.get("body_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "review approval body digest is malformed"
        if not isinstance(approval.get("created_at"), str):
            return "review approval timestamp is malformed"
        ids.add(comment_id)
        families.add(family)
    if post_facto and families != REVIEW_FAMILIES:
        return "post-facto review evidence lacks dual Claude and Codex approval"
    return None


class GitHubClient:
    """The GitHub identity/review/merge adapter used by the executor."""

    _THREAD_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100){
        nodes{isResolved}
        pageInfo{hasNextPage}
      }
    }
  }
}
""".strip()

    _COMMITS_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      commits(first:100){
        nodes{commit{oid}}
        pageInfo{hasNextPage}
      }
    }
  }
}
""".strip()

    _REVIEW_EVIDENCE_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      labels(first:100){nodes{name} pageInfo{hasNextPage}}
      comments(last:100){
        nodes{id databaseId body createdAt url author{login}}
        pageInfo{hasPreviousPage}
      }
    }
  }
}
""".strip()

    _ROLE_TAG = re.compile(r"^\[adversarial-reviewer agent,\s*([^\]\n]+)\]$")
    _COORDINATOR_TAG = re.compile(r"^\[coordinator,\s*([^\]\n]+)\]$")
    _CODEX_MODEL = re.compile(
        r"^(?:gpt-[0-9]+(?:\.[0-9]+)*(?:-[a-z0-9]+)*|codex(?:-[a-z0-9]+)*)$"
    )
    _CLAUDE_MODEL = re.compile(
        r"^(?:claude-)?(?:opus|sonnet|haiku)(?:-[0-9]+(?:\.[0-9]+)*)?$"
    )
    _INSTANCE_ID = re.compile(r"^[a-z0-9][a-z0-9._/-]{2,127}$")
    _ASSIGNMENT_PREFIX = "Review-assignment: "
    _ATTESTATION_PREFIX = "Review-attestation: "
    _EXACT_HEAD_VERDICT = re.compile(
        r"^Exact-head verdict for `?([0-9a-f]{40})`?(?:[^:\n]*)?:\s*"
        r"(?:\*\*)?(PASS|FAIL)(?:\*\*)?(?=[\s.,;:—-]|$)",
        re.IGNORECASE,
    )

    def __init__(self, runner: Runner):
        self.runner = runner

    @staticmethod
    def _repo_parts(repo: str) -> tuple[str, str]:
        parts = repo.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise Refused(f"invalid repository identity {repo!r}")
        return parts[0], parts[1]

    def snapshot(self, repo: str, pr: int) -> PullRequestSnapshot:
        command = (
            "with-proxy",
            "gh",
            "pr",
            "view",
            str(pr),
            "-R",
            repo,
            "--json",
            "number,state,isDraft,headRefOid,baseRefName,reviewDecision,mergeCommit",
        )
        result = self.runner.run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"cannot read {repo}#{pr}: {detail}")
        payload = _json_object(result.stdout, "gh pr view")
        if payload.get("number") != pr:
            raise LandingError("GitHub PR response did not carry the requested number")
        merge = payload.get("mergeCommit")
        merge_commit: str | None = None
        if merge is not None:
            if not isinstance(merge, Mapping):
                raise LandingError("GitHub mergeCommit is not an object")
            oid = merge.get("oid")
            if oid:
                merge_commit = _full_sha(oid, "mergeCommit.oid")
        state = str(payload.get("state") or "").upper()
        if state not in {"OPEN", "MERGED", "CLOSED"}:
            raise LandingError(f"GitHub returned unknown PR state {state!r}")
        if type(payload.get("isDraft")) is not bool:
            raise LandingError("GitHub PR response has no boolean isDraft")
        return PullRequestSnapshot(
            number=pr,
            state=state,
            is_draft=payload["isDraft"],
            head=_full_sha(payload.get("headRefOid"), "headRefOid"),
            base=str(payload.get("baseRefName") or ""),
            review_decision=str(payload.get("reviewDecision") or "").upper(),
            merge_commit=merge_commit,
        )

    def unresolved_review_threads(self, repo: str, pr: int) -> int:
        owner, name = self._repo_parts(repo)
        command = (
            "with-proxy",
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={self._THREAD_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr}",
        )
        result = self.runner.run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"cannot dereference review threads: {detail}")
        payload = _json_object(result.stdout, "GitHub review-thread query")
        try:
            threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = threads["nodes"]
            has_next = threads["pageInfo"]["hasNextPage"]
        except (KeyError, TypeError) as error:
            raise LandingError("GitHub review-thread response is incomplete") from error
        if type(has_next) is not bool or not isinstance(nodes, list):
            raise LandingError("GitHub review-thread response has invalid types")
        if has_next:
            raise LandingError(
                "more than 100 review threads; pagination is unsupported"
            )
        unresolved = 0
        for node in nodes:
            if (
                not isinstance(node, Mapping)
                or type(node.get("isResolved")) is not bool
            ):
                raise LandingError("GitHub review-thread entry is malformed")
            if not node["isResolved"]:
                unresolved += 1
        return unresolved

    @staticmethod
    def _review_family(model: str) -> str | None:
        lowered = model.lower()
        codex = GitHubClient._CODEX_MODEL.fullmatch(lowered) is not None
        claude = GitHubClient._CLAUDE_MODEL.fullmatch(lowered) is not None
        if codex == claude:
            return None
        return "codex" if codex else "claude"

    def review_evidence(self, repo: str, pr: int, expected_head: str) -> dict[str, Any]:
        """Dereference role-tagged exact-head review comments and live policy label."""
        owner, name = self._repo_parts(repo)
        command = (
            "with-proxy",
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={self._REVIEW_EVIDENCE_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr}",
        )
        result = self.runner.run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"cannot dereference exact-head review evidence: {detail}")
        payload = _json_object(result.stdout, "GitHub exact-head review query")
        try:
            pull = payload["data"]["repository"]["pullRequest"]
            labels = pull["labels"]
            label_nodes = labels["nodes"]
            label_next = labels["pageInfo"]["hasNextPage"]
            comments = pull["comments"]
            comment_nodes = comments["nodes"]
            comment_previous = comments["pageInfo"]["hasPreviousPage"]
        except (KeyError, TypeError) as error:
            raise LandingError("GitHub exact-head review response is incomplete") from error
        if (
            type(label_next) is not bool
            or type(comment_previous) is not bool
            or not isinstance(label_nodes, list)
            or not isinstance(comment_nodes, list)
        ):
            raise LandingError("GitHub exact-head review response has invalid types")
        if label_next:
            raise LandingError("more than 100 PR labels; pagination is unsupported")
        if comment_previous:
            raise LandingError(
                "more than 100 PR comments; exact-head review pagination is unsupported"
            )
        label_names: set[str] = set()
        for node in label_nodes:
            if not isinstance(node, Mapping) or not isinstance(node.get("name"), str):
                raise LandingError("GitHub PR label entry is malformed")
            label_names.add(str(node["name"]))
        post_facto = POST_FACTO_REVIEW_LABEL in label_names

        latest: dict[str, dict[str, Any]] = {}
        for node in comment_nodes:
            if not isinstance(node, Mapping):
                raise LandingError("GitHub PR comment entry is malformed")
            body = node.get("body")
            comment_id = node.get("id")
            created_at = node.get("createdAt")
            if (
                not isinstance(body, str)
                or not isinstance(comment_id, str)
                or not comment_id
                or not isinstance(created_at, str)
            ):
                raise LandingError("GitHub PR comment identity is malformed")
            lines = body.splitlines()
            if not lines:
                continue
            role = self._ROLE_TAG.fullmatch(lines[0].strip())
            if role is None:
                continue
            verdicts = [
                match
                for line in lines[1:]
                if (match := self._EXACT_HEAD_VERDICT.match(line.strip())) is not None
            ]
            if not verdicts:
                continue
            if len(verdicts) != 1:
                raise Refused(
                    f"role-tagged review comment {comment_id} has ambiguous verdict lines"
                )
            verdict_head, verdict = verdicts[0].groups()
            if verdict_head.lower() != expected_head:
                continue
            model = role.group(1).strip()
            family = self._review_family(model)
            if family is None:
                raise Refused(
                    f"exact-head reviewer model {model!r} has no unique Claude/Codex family"
                )
            author = node.get("author")
            if author is not None and not isinstance(author, Mapping):
                raise LandingError("GitHub PR comment author is malformed")
            approval = {
                "comment_id": comment_id,
                "database_id": node.get("databaseId"),
                "url": str(node.get("url") or ""),
                "author": str((author or {}).get("login") or ""),
                "created_at": created_at,
                "model": model,
                "family": family,
                "head": verdict_head.lower(),
                "verdict": verdict.upper(),
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
            previous = latest.get(family)
            ordering = (created_at, comment_id)
            if previous is None or ordering > (
                str(previous["created_at"]),
                str(previous["comment_id"]),
            ):
                latest[family] = approval

        failures = [
            approval
            for approval in latest.values()
            if approval["verdict"] == "FAIL"
        ]
        if failures:
            families = ", ".join(sorted(str(item["family"]) for item in failures))
            raise Refused(f"latest exact-head adversarial verdict is FAIL for {families}")
        approvals = sorted(
            (
                approval
                for approval in latest.values()
                if approval["verdict"] == "PASS"
            ),
            key=lambda item: str(item["family"]),
        )
        if post_facto:
            missing = REVIEW_FAMILIES - {str(item["family"]) for item in approvals}
            if missing:
                raise Refused(
                    "post-facto-human-review requires exact-head Claude and Codex "
                    f"approvals; missing {', '.join(sorted(missing))}"
                )
        elif not approvals:
            raise Refused("no role-tagged exact-head adversarial-review PASS exists")
        evidence = {
            "authority": REVIEW_AUTHORITY,
            "repo": repo,
            "pr": pr,
            "head": expected_head,
            "post_facto_human_review": post_facto,
            "required_families": ["claude", "codex"] if post_facto else ["any"],
            "approvals": approvals,
        }
        problem = _review_evidence_problem(
            evidence, repo=repo, pr=pr, expected_head=expected_head
        )
        if problem:
            raise LandingError(f"fresh exact-head review evidence is malformed: {problem}")
        return evidence

    def pr_commits(self, repo: str, pr: int) -> tuple[str, ...]:
        """Return GitHub's complete, ordered PR commit identity list.

        The first version refuses pagination instead of silently binding a
        local count to GitHub's first page.
        """

        owner, name = self._repo_parts(repo)
        command = (
            "with-proxy",
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={self._COMMITS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr}",
        )
        result = self.runner.run(command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"cannot dereference GitHub PR commits: {detail}")
        payload = _json_object(result.stdout, "GitHub PR-commit query")
        try:
            commits = payload["data"]["repository"]["pullRequest"]["commits"]
            nodes = commits["nodes"]
            has_next = commits["pageInfo"]["hasNextPage"]
        except (KeyError, TypeError) as error:
            raise LandingError("GitHub PR-commit response is incomplete") from error
        if type(has_next) is not bool or not isinstance(nodes, list):
            raise LandingError("GitHub PR-commit response has invalid types")
        if has_next:
            raise Refused("PR has more than 100 commits; pagination is unsupported")
        result_commits: list[str] = []
        for node in nodes:
            try:
                oid = node["commit"]["oid"]
            except (KeyError, TypeError) as error:
                raise LandingError("GitHub PR-commit entry is malformed") from error
            result_commits.append(_full_sha(oid, "GitHub PR commit oid"))
        if not result_commits:
            raise Refused("GitHub reports a vacuous PR commit list")
        if len(set(result_commits)) != len(result_commits):
            raise LandingError("GitHub PR-commit list contains duplicate identities")
        return tuple(result_commits)

    def request_rebase_merge(
        self, repo: str, pr: int, expected_head: str
    ) -> subprocess.CompletedProcess[str]:
        # GitHub's REST merge endpoint is synchronous, and `sha` is its atomic
        # expected-head guard. A queue-capable CLI merge would leave an external
        # mutation active after this process's bounded wait expired.
        return self.runner.run(
            (
                "with-proxy",
                "gh",
                "api",
                "--include",
                "--method",
                "PUT",
                f"repos/{repo}/pulls/{pr}/merge",
                "-f",
                f"sha={expected_head}",
                "-f",
                "merge_method=rebase",
            )
        )


class CanonicalMutationBarrier:
    """Persist the exact pending mutation in the canonical landing lock."""

    def __init__(self, runner: Runner, ci_hub: Path | None = None):
        self.runner = runner
        self.ci_hub = ci_hub or ROOT / "ci-hub/ci-hub"

    def _set(
        self,
        action: str,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None:
        command = (
            str(self.ci_hub),
            "land-lock",
            action,
            "--agent",
            actor,
            "--repo",
            repo,
            "--pr",
            str(pr),
            "--operation",
            operation,
            "--attempt-id",
            attempt_id,
            "--child-pid",
            str(os.getpid()),
        )
        result = self.runner.run(command, cwd=ROOT, timeout=30.0)
        marker = (
            f"MUTATION_BARRIER_{'ARMED' if action == 'arm-mutation' else 'CLEARED'} "
            f"agent={actor} repo={repo} pr={pr} operation={operation} "
            f"attempt_id={attempt_id}"
        )
        if result.returncode != 0 or result.stdout.strip() != marker:
            detail = (result.stderr or result.stdout).strip()[:1024]
            raise LandingError(
                f"canonical landing-lock {action} failed"
                + (f": {detail}" if detail else "")
            )

    def arm(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None:
        self._set(
            "arm-mutation",
            actor=actor,
            repo=repo,
            pr=pr,
            operation=operation,
            attempt_id=attempt_id,
        )

    def bind_call(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
        call_count: int,
        call_id: str,
    ) -> None:
        command = (
            str(self.ci_hub),
            "land-lock",
            "bind-mutation-call",
            "--agent",
            actor,
            "--repo",
            repo,
            "--pr",
            str(pr),
            "--operation",
            operation,
            "--attempt-id",
            attempt_id,
            "--call-count",
            str(call_count),
            "--call-id",
            call_id,
            "--child-pid",
            str(os.getpid()),
        )
        result = self.runner.run(command, cwd=ROOT, timeout=30.0)
        marker = (
            f"MUTATION_CALL_BOUND agent={actor} repo={repo} pr={pr} "
            f"operation={operation} attempt_id={attempt_id} "
            f"call_count={call_count} call_id={call_id}"
        )
        if result.returncode != 0 or result.stdout.strip() != marker:
            detail = (result.stderr or result.stdout).strip()[:1024]
            raise LandingError(
                "canonical landing-lock bind-mutation-call failed"
                + (f": {detail}" if detail else "")
            )

    def clear(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None:
        self._set(
            "clear-mutation",
            actor=actor,
            repo=repo,
            pr=pr,
            operation=operation,
            attempt_id=attempt_id,
        )


class CanonicalValidationAuthority:
    """Dereference interchangeable exact-head local or hosted green authority."""

    def __init__(
        self,
        runner: Runner,
        ci_hub: Path | None = None,
        *,
        hosted_runs: Callable[..., list[dict[str, Any]]] | None = None,
    ):
        self.runner = runner
        self.ci_hub = ci_hub or ROOT / "ci-hub/ci-hub"
        self.hosted_runs = hosted_runs or obligation_protocol.github_runs

    def _verify_local(self, expected_head: str) -> ReceiptEvidence:
        command = (
            str(self.ci_hub),
            "validate-status",
            "--repo",
            SUPPORTED_REPO,
            "--sha",
            expected_head,
            "--json",
        )
        result = self.runner.run(command, timeout=60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise NoGreenValidation(
                f"canonical local-full receipt verifier refused {expected_head}: {detail}"
            )
        report = _json_object(result.stdout, "ci-hub validate-status")
        problem = _receipt_problem(report, expected_head, require_envelope=False)
        if problem:
            raise Refused(f"validate-status report refused: {problem}")
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        return ReceiptEvidence(
            report=dict(report),
            report_sha256=hashlib.sha256(canonical).hexdigest(),
            command=command,
            authority=LOCAL_VALIDATION_AUTHORITY,
        )

    def _verify_hosted(self, expected_head: str) -> ReceiptEvidence:
        policy = obligation_protocol.verification_policy_for_repo(SUPPORTED_REPO)
        try:
            runs = self.hosted_runs(
                SUPPORTED_REPO,
                expected_head,
                policy=policy,
            )
            patch = obligation_protocol._github_patch(runs, expected_head, policy)
        except (obligation_protocol.ProtocolError, subprocess.SubprocessError) as error:
            raise NoGreenValidation(
                f"canonical hosted exact-head verifier refused {expected_head}: {error}"
            ) from error
        github = patch.get("github")
        if not isinstance(github, Mapping) or github.get("state") != "green":
            state = github.get("state") if isinstance(github, Mapping) else "malformed"
            raise NoGreenValidation(
                f"canonical hosted exact-head verifier returned {state} for {expected_head}"
            )
        report = {
            "schema_version": 1,
            "repo": SUPPORTED_REPO,
            "sha": expected_head,
            "verdict": "VALIDATED",
            "verification_policy": policy,
            "github": dict(github),
        }
        problem = _hosted_validation_problem(report, expected_head)
        if problem:
            raise Refused(f"canonical hosted exact-head verifier refused: {problem}")
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        return ReceiptEvidence(
            report=report,
            report_sha256=hashlib.sha256(canonical).hexdigest(),
            command=(
                "protocol.github_runs",
                "--repo",
                SUPPORTED_REPO,
                "--sha",
                expected_head,
            ),
            authority=HOSTED_VALIDATION_AUTHORITY,
        )

    def verify_authority(
        self, expected_head: str, authority: str
    ) -> ReceiptEvidence:
        if authority == LOCAL_VALIDATION_AUTHORITY:
            return self._verify_local(expected_head)
        if authority == HOSTED_VALIDATION_AUTHORITY:
            return self._verify_hosted(expected_head)
        raise Refused(f"unknown persisted validation authority {authority!r}")

    def verify(self, expected_head: str) -> ReceiptEvidence:
        local_problem: str
        try:
            return self._verify_local(expected_head)
        except NoGreenValidation as error:
            local_problem = str(error)
        try:
            return self._verify_hosted(expected_head)
        except NoGreenValidation as error:
            raise Refused(
                f"no exact-head validation authority is green for {expected_head}; "
                f"local={local_problem}; hosted={error}"
            ) from error


# Compatibility name for existing imports; the implementation is no longer
# local-receipt-only.
CanonicalReceiptAuthority = CanonicalValidationAuthority


class GitRepository:
    """Read-only commit-graph and tree verifier in the assigned lander slot."""

    def __init__(self, runner: Runner, checkout: Path):
        self.runner = runner
        self.checkout = checkout

    def _run(
        self,
        args: Sequence[str],
        *,
        network: bool = False,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        prefix = ["with-proxy", "git"] if network else ["git"]
        return self.runner.run(
            (*prefix, "-C", str(self.checkout), *args), timeout=timeout
        )

    def _checked(self, args: Sequence[str], label: str) -> str:
        result = self._run(args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"{label} failed: {detail}")
        return result.stdout.strip()

    def ensure_checkout(self, expected_repo: str) -> None:
        if not self.checkout.is_dir():
            raise LandingError(f"checkout does not exist: {self.checkout}")
        value = self._checked(
            ("rev-parse", "--is-inside-work-tree"), "git checkout probe"
        )
        if value != "true":
            raise LandingError(f"not a Git worktree: {self.checkout}")
        remote = self._run(("remote", "get-url", "--all", "origin"))
        if remote.returncode != 0:
            raise Refused("checkout has no readable origin fetch URL")
        urls = tuple(
            line.strip() for line in remote.stdout.splitlines() if line.strip()
        )
        if len(urls) != 1:
            raise Refused(
                "checkout origin must have exactly one unambiguous fetch URL; "
                f"observed {len(urls)}"
            )
        if github_remote_repo(urls[0]) != expected_repo:
            raise Refused(
                "checkout origin identity mismatch: expected "
                f"{expected_repo}, observed {urls[0]!r}"
            )

    def fetch_base(self) -> str:
        result = self._run(
            (
                "fetch",
                "--no-tags",
                "origin",
                f"refs/heads/{TARGET_BRANCH}:refs/remotes/origin/{TARGET_BRANCH}",
            ),
            network=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"fresh origin/{TARGET_BRANCH} fetch failed: {detail}")
        return self.rev_parse(f"refs/remotes/origin/{TARGET_BRANCH}")

    def fetch_head(self, pr: int, expected_head: str) -> None:
        result = self._run(
            ("fetch", "--no-tags", "origin", f"refs/pull/{pr}/head"),
            network=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"immutable PR-head fetch failed: {detail}")
        observed = self.rev_parse("FETCH_HEAD")
        if observed != expected_head:
            raise Refused(
                f"fetched PR head drifted: expected {expected_head}, observed {observed}"
            )

    def rev_parse(self, expression: str) -> str:
        return _full_sha(
            self._checked(
                ("rev-parse", "--verify", expression), f"resolve {expression}"
            ),
            expression,
        )

    def _lines(self, args: Sequence[str], label: str) -> tuple[str, ...]:
        output = self._checked(args, label)
        if not output:
            return ()
        lines = tuple(
            line.strip().lower() for line in output.splitlines() if line.strip()
        )
        for value in lines:
            _full_sha(value, label)
        return lines

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._run(("merge-base", "--is-ancestor", ancestor, descendant))
        if result.returncode not in {0, 1}:
            detail = (result.stderr or result.stdout).strip()
            raise LandingError(f"cannot compare {ancestor}..{descendant}: {detail}")
        return result.returncode == 0

    def commit_exists(self, revision: str) -> bool:
        result = self._run(("cat-file", "-e", f"{revision}^{{commit}}"))
        if result.returncode == 0:
            return True
        if result.returncode in {1, 128}:
            return False
        detail = (result.stderr or result.stdout).strip()
        raise LandingError(f"cannot probe commit {revision}: {detail}")

    def source_provenance(
        self, expected_head: str, observed_base: str
    ) -> SourceProvenance:
        source_base = _full_sha(
            self._checked(
                ("merge-base", observed_base, expected_head), "derive source merge-base"
            ),
            "source merge-base",
        )
        if not self.is_ancestor(source_base, observed_base):
            raise Refused("source merge-base is not an ancestor of observed main")
        source_range = f"{source_base}..{expected_head}"
        commits = self._lines(
            ("rev-list", "--reverse", source_range), "enumerate source commits"
        )
        first_parent = self._lines(
            ("rev-list", "--reverse", "--first-parent", source_range),
            "enumerate source first-parent commits",
        )
        merges = self._lines(
            ("rev-list", "--merges", source_range), "detect source merge commits"
        )
        if not commits:
            raise Refused("source range is empty; a vacuous PR cannot be rebase-landed")
        if commits != first_parent or merges:
            raise Refused("source X is not a linear, merge-free commit range")
        return SourceProvenance(
            observed_base=observed_base,
            source_base=source_base,
            source_commits=commits,
            source_tree=self.rev_parse(f"{expected_head}^{{tree}}"),
            observed_base_tree=self.rev_parse(f"{observed_base}^{{tree}}"),
        )

    def verify_replay(
        self,
        *,
        expected_head: str,
        observed_base: str,
        source_base: str,
        source_commit_count: int,
        source_commits: Sequence[str],
        merge_commit: str,
        fetched_main: str,
    ) -> ReplayProvenance:
        details: dict[str, Any] = {
            "expected_head": expected_head,
            "observed_base": observed_base,
            "source_base": source_base,
            "source_commit_count": source_commit_count,
            "merge_commit": merge_commit,
            "fetched_main": fetched_main,
            "replay_base": None,
            "composition_merge_base": None,
            "expected_tree": None,
            "actual_tree": None,
            "replay_base_is_ancestor_of_source": None,
            "green_class": None,
            "soft_green": None,
        }
        try:
            current_source = self.source_provenance(expected_head, observed_base)
            if (
                current_source.source_base != source_base
                or current_source.source_commit_count != source_commit_count
                or current_source.source_commits != tuple(source_commits)
            ):
                raise ReplayMismatch(
                    "persisted source range no longer matches X", details
                )
            if not self.is_ancestor(merge_commit, fetched_main):
                raise ReplayMismatch(
                    "MC is not an ancestor of freshly fetched main", details
                )
            replay_base = self.rev_parse(f"{merge_commit}~{source_commit_count}")
            details["replay_base"] = replay_base
            if not self.is_ancestor(observed_base, replay_base):
                raise ReplayMismatch(
                    "actual replay base Y does not descend from observed main", details
                )
            replay_range = f"{replay_base}..{merge_commit}"
            replay_commits = self._lines(
                ("rev-list", "--reverse", replay_range), "enumerate replay commits"
            )
            replay_first_parent = self._lines(
                ("rev-list", "--reverse", "--first-parent", replay_range),
                "enumerate replay first-parent commits",
            )
            replay_merges = self._lines(
                ("rev-list", "--merges", replay_range), "detect replay merge commits"
            )
            if (
                len(replay_commits) != source_commit_count
                or replay_commits != replay_first_parent
                or replay_merges
            ):
                raise ReplayMismatch(
                    "Y..MC is not the expected linear source commit count", details
                )
            composition_merge_base = _full_sha(
                self._checked(
                    ("merge-base", replay_base, expected_head),
                    "derive composition merge-base",
                ),
                "composition merge-base",
            )
            details["composition_merge_base"] = composition_merge_base
            if composition_merge_base != source_base:
                raise ReplayMismatch(
                    "actual Y changes the authorized S..X composition base", details
                )
            composition = self._run(
                ("merge-tree", "--write-tree", replay_base, expected_head)
            )
            if composition.returncode != 0:
                details["merge_tree_output"] = (
                    composition.stdout + composition.stderr
                ).strip()[:4096]
                raise ReplayMismatch(
                    "X does not compose conflict-free over actual replay base Y",
                    details,
                )
            first_line = next(
                (
                    line.strip()
                    for line in composition.stdout.splitlines()
                    if line.strip()
                ),
                "",
            )
            expected_tree = _full_sha(first_line, "merge-tree result tree")
            actual_tree = self.rev_parse(f"{merge_commit}^{{tree}}")
            details["expected_tree"] = expected_tree
            details["actual_tree"] = actual_tree
            if actual_tree != expected_tree:
                raise ReplayMismatch(
                    "MC tree differs from conflict-free composition of X over Y",
                    details,
                )
            replay_base_is_ancestor = self.is_ancestor(replay_base, expected_head)
            green_class = GREEN_HARD if replay_base_is_ancestor else GREEN_SOFT
            soft_green = None if replay_base_is_ancestor else SOFT_ZERO_CONFLICT
            details["replay_base_is_ancestor_of_source"] = replay_base_is_ancestor
            details["green_class"] = green_class
            details["soft_green"] = soft_green
            return ReplayProvenance(
                expected_head=expected_head,
                observed_base=observed_base,
                source_base=source_base,
                source_commit_count=source_commit_count,
                replay_base=replay_base,
                merge_commit=merge_commit,
                fetched_main=fetched_main,
                composition_merge_base=composition_merge_base,
                expected_tree=expected_tree,
                actual_tree=actual_tree,
                replay_commits=replay_commits,
                replay_base_is_ancestor_of_source=replay_base_is_ancestor,
                green_class=green_class,
                soft_green=soft_green,
            )
        except ReplayMismatch:
            raise
        except LandingError as error:
            raise ReplayMismatch(str(error), details) from error


class CanonicalObligationArmer:
    """Arm through the tracked ci-hub command and dereference the result."""

    def __init__(
        self,
        runner: Runner,
        checkout: Path,
        *,
        ci_hub: Path | None = None,
        obligation_store: Path | None = None,
    ):
        self.runner = runner
        self.checkout = checkout
        self.ci_hub = ci_hub or ROOT / "ci-hub/ci-hub"
        self.obligation_store = obligation_store

    def _records(self) -> list[Mapping[str, Any]]:
        query = [str(self.ci_hub), "obligations", "--all", "--json"]
        if self.obligation_store is not None:
            query.extend(("--store", str(self.obligation_store)))
        observed = self.runner.run(query, timeout=60)
        # The canonical CLI uses 1 for open obligations and 2 for obligations
        # needing remediation.  Both are typed query results, not transport or
        # decoding failures; the JSON record checks below remain authoritative.
        if observed.returncode not in {0, 1, 2}:
            detail = (observed.stderr or observed.stdout).strip()
            raise LandingError(f"cannot dereference armed obligation store: {detail}")
        payload = _json_object(observed.stdout, "ci-hub obligations")
        records = payload.get("obligations")
        if not isinstance(records, list):
            raise LandingError("ci-hub obligations did not return a record list")
        return [record for record in records if isinstance(record, Mapping)]

    def verify(
        self, repo: str, merge_commit: str, obligation_id: str
    ) -> dict[str, Any]:
        matches = [
            record
            for record in self._records()
            if record.get("obligation_id") == obligation_id
        ]
        if len(matches) != 1:
            raise LandingError(
                "canonical obligation store does not contain exactly one claimed obligation"
            )
        record = matches[0]
        if record.get("repo") != repo or record.get("landed_sha") != merge_commit:
            raise LandingError(
                "claimed obligation does not bind the exact repository and merge commit"
            )
        if not obligation_protocol.obligation_launch_durable(record):
            raise LandingError(
                "exact-MC obligation exists but canonical launch durability is pending"
            )
        return {
            "obligation_id": obligation_id,
            "overall_state": record.get("overall_state"),
            "launch_durable": True,
        }

    def arm(self, repo: str, pr: int, merge_commit: str, actor: str) -> dict[str, Any]:
        command = [
            str(self.ci_hub),
            "arm-land",
            merge_commit,
            "--repo",
            repo,
            "--pr",
            str(pr),
            "--source",
            str(self.checkout),
            "--land-mode",
            "speculative",
            "--actor",
            actor,
        ]
        if self.obligation_store is not None:
            command.extend(("--store", str(self.obligation_store)))
        armed = self.runner.run(command, timeout=300)
        records = self._records()
        matches = [
            record
            for record in records
            if record.get("repo") == repo and record.get("landed_sha") == merge_commit
        ]
        if not matches:
            detail = (armed.stderr or armed.stdout).strip()
            raise LandingError(
                f"arm-land did not create or recover an exact-MC obligation: {detail}"
            )
        if armed.returncode != 0:
            detail = (armed.stderr or armed.stdout).strip()
            raise LandingError(
                "arm-land did not report a durable launch "
                f"(rc={armed.returncode}): {detail}"
            )
        record = matches[-1]
        obligation_id = record.get("obligation_id")
        if not isinstance(obligation_id, str) or not obligation_id:
            raise LandingError("exact-MC obligation has no identity")
        verified = self.verify(repo, merge_commit, obligation_id)
        verified.update(
            arm_returncode=armed.returncode,
            arm_stdout_sha256=hashlib.sha256(armed.stdout.encode()).hexdigest(),
        )
        return verified


class EventStore:
    """Strict append-only JSONL intent/recovery store."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @contextmanager
    def host_lock(self) -> Iterator[None]:
        self._ensure_parent()
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Refused(
                    "another host-local landing executor holds the lock"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise StoreError(
                            f"invalid landing JSONL at line {line_number}: {error}"
                        ) from error
                    self._validate_event(event, line_number)
                    events.append(event)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self._validate_sequences(events)
        return events

    @staticmethod
    def _validate_event(event: object, line_number: int) -> None:
        if not isinstance(event, dict):
            raise StoreError(f"landing event at line {line_number} is not an object")
        if event.get("schema_version") != SCHEMA_VERSION:
            raise StoreError(f"unsupported landing schema at line {line_number}")
        if event.get("record_type") != RECORD_TYPE:
            raise StoreError(f"wrong landing record type at line {line_number}")
        if event.get("event_type") not in EVENT_TYPES:
            raise StoreError(f"unknown landing event type at line {line_number}")
        if not ID_RE.fullmatch(str(event.get("event_id") or "")):
            raise StoreError(f"invalid event_id at line {line_number}")
        if not ID_RE.fullmatch(str(event.get("attempt_id") or "")):
            raise StoreError(f"invalid attempt_id at line {line_number}")
        if event.get("repo") != SUPPORTED_REPO:
            raise StoreError(f"unsupported event repository at line {line_number}")
        if type(event.get("pr")) is not int or event["pr"] <= 0:
            raise StoreError(f"invalid event PR at line {line_number}")
        if not SHA_RE.fullmatch(str(event.get("expected_head") or "")):
            raise StoreError(f"invalid event expected_head at line {line_number}")
        if not isinstance(event.get("recorded_at"), str):
            raise StoreError(f"missing event timestamp at line {line_number}")
        if event["event_type"] == "intent":
            for key in (
                "observed_base",
                "source_base",
                "source_tree",
                "observed_base_tree",
            ):
                if not SHA_RE.fullmatch(str(event.get(key) or "")):
                    raise StoreError(f"invalid intent {key} at line {line_number}")
            count = event.get("source_commit_count")
            commits = event.get("source_commits")
            if type(count) is not int or count <= 0 or not isinstance(commits, list):
                raise StoreError(f"invalid intent source count at line {line_number}")
            if len(commits) != count or not all(
                isinstance(value, str) and SHA_RE.fullmatch(value) for value in commits
            ):
                raise StoreError(f"invalid intent source commits at line {line_number}")
            github_count = event.get("github_pr_commit_count")
            github_commits = event.get("github_pr_commits")
            if github_count != count or github_commits != commits:
                raise StoreError(
                    f"GitHub PR commit list is not bound to source commits at line {line_number}"
                )
            review_problem = _review_evidence_problem(
                event.get("review_evidence"),
                repo=str(event["repo"]),
                pr=int(event["pr"]),
                expected_head=str(event["expected_head"]),
            )
            if review_problem:
                raise StoreError(
                    f"invalid intent review evidence at line {line_number}: "
                    f"{review_problem}"
                )
            relation = event.get("observed_base_is_ancestor_of_source")
            planned = event.get("planned_green_class")
            if type(relation) is not bool:
                raise StoreError(
                    f"invalid intent base/source relation at line {line_number}"
                )
            expected_planned = GREEN_HARD if relation else GREEN_SOFT
            if planned != expected_planned:
                raise StoreError(f"invalid planned green class at line {line_number}")
            source_problem = _receipt_problem(
                event.get("source_receipt"),
                str(event["expected_head"]),
                require_envelope=True,
            )
            if source_problem:
                raise StoreError(
                    f"invalid source receipt at line {line_number}: {source_problem}"
                )
            base_receipt = event.get("base_receipt")
            if relation and base_receipt is not None:
                raise StoreError(
                    f"hard-green intent unexpectedly carries base receipt at line {line_number}"
                )
            if not relation:
                base_problem = _receipt_problem(
                    base_receipt,
                    str(event["observed_base"]),
                    require_envelope=True,
                )
                if base_problem:
                    raise StoreError(
                        f"soft-green intent has no exact hard base at line {line_number}: "
                        f"{base_problem}"
                    )
        if event["event_type"] == "merge_requested":
            if event.get("merge_method") != "rebase":
                raise StoreError(f"unsupported merge method at line {line_number}")
            if event.get("request_semantics") != "synchronous-rest-v1":
                raise StoreError(
                    f"merge request is not synchronous REST at line {line_number}"
                )
            if event.get("expected_head_guard") != event["expected_head"]:
                raise StoreError(
                    f"merge request lacks exact-head guard at line {line_number}"
                )
            if not SHA_RE.fullmatch(str(event.get("observed_base") or "")):
                raise StoreError(
                    f"merge request lacks observed base at line {line_number}"
                )
        if event["event_type"] == "merge_call_started":
            if not ID_RE.fullmatch(str(event.get("call_id") or "")):
                raise StoreError(
                    f"merge call has invalid call_id at line {line_number}"
                )
            if event.get("request_semantics") != "synchronous-rest-v1":
                raise StoreError(
                    f"merge call is not synchronous REST at line {line_number}"
                )
            if event.get("expected_head_guard") != event["expected_head"]:
                raise StoreError(
                    f"merge call lacks exact-head guard at line {line_number}"
                )
        if event["event_type"] == "merge_response":
            if not ID_RE.fullmatch(str(event.get("call_id") or "")):
                raise StoreError(
                    f"merge response has invalid call_id at line {line_number}"
                )
            returncode = event.get("returncode")
            if returncode is not None and type(returncode) is not int:
                raise StoreError(
                    f"merge response has invalid return code at line {line_number}"
                )
            for key in ("stdout_sha256", "stderr_sha256"):
                value = event.get(key)
                if value is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value)):
                    raise StoreError(
                        f"merge response has invalid {key} at line {line_number}"
                    )
            envelope = event.get("http_envelope")
            if envelope is not None and (
                not isinstance(envelope, str)
                or len(envelope.encode()) > MAX_HTTP_ENVELOPE_BYTES
            ):
                raise StoreError(
                    f"merge response has invalid HTTP envelope at line {line_number}"
                )
            if (
                isinstance(envelope, str)
                and event.get("stdout_sha256")
                != hashlib.sha256(envelope.encode()).hexdigest()
            ):
                raise StoreError(
                    f"merge response HTTP envelope hash mismatch at line {line_number}"
                )
            http_status = event.get("http_status")
            if http_status is not None and (
                type(http_status) is not int or not 100 <= http_status <= 599
            ):
                raise StoreError(
                    f"merge response has invalid HTTP status at line {line_number}"
                )
            if type(event.get("definitive_no_mutation")) is not bool:
                raise StoreError(
                    f"merge response lacks definitive-negative disposition at line {line_number}"
                )
            if (
                http_status is not None
                or event.get("definitive_no_mutation") is True
                or event.get("merged") is not None
            ) and not isinstance(envelope, str):
                raise StoreError(
                    f"parsed merge response has no durable HTTP envelope at line {line_number}"
                )
            merged = event.get("merged")
            if merged is not None and type(merged) is not bool:
                raise StoreError(
                    f"merge response has invalid merged result at line {line_number}"
                )
            response_sha = event.get("response_merge_commit")
            if response_sha is not None and not SHA_RE.fullmatch(str(response_sha)):
                raise StoreError(
                    f"merge response has invalid merge commit at line {line_number}"
                )
            if merged is True and response_sha is None:
                raise StoreError(
                    f"successful merge response has no merge commit at line {line_number}"
                )
            if merged is False and response_sha is not None:
                raise StoreError(
                    f"refused merge response carries a merge commit at line {line_number}"
                )
            if event["definitive_no_mutation"]:
                if merged is not False or http_status not in {
                    200,
                    *DEFINITIVE_NO_MUTATION_HTTP_STATUSES,
                }:
                    raise StoreError(
                        f"merge response has invalid definitive negative at line {line_number}"
                    )
            if merged is True and event["definitive_no_mutation"]:
                raise StoreError(
                    f"successful merge response is marked negative at line {line_number}"
                )
        if event["event_type"] == "merge_pending" and not isinstance(
            event.get("message"), str
        ):
            raise StoreError(f"pending merge has no message at line {line_number}")
        if event["event_type"] == "landing_quarantined":
            if (
                not isinstance(event.get("reason_code"), str)
                or not event["reason_code"]
                or not isinstance(event.get("message"), str)
                or not event["message"]
            ):
                raise StoreError(
                    f"landing quarantine lacks reason/message at line {line_number}"
                )
            if event.get("github_state") != "MERGED":
                raise StoreError(
                    f"landing quarantine lacks MERGED observation at line {line_number}"
                )
            for key in ("merge_commit", "fetched_main"):
                if not SHA_RE.fullmatch(str(event.get(key) or "")):
                    raise StoreError(
                        f"landing quarantine has invalid {key} at line {line_number}"
                    )
            diagnostics = event.get("diagnostics")
            if not isinstance(diagnostics, Mapping) or not diagnostics:
                raise StoreError(
                    f"landing quarantine has no proof diagnostics at line {line_number}"
                )
        if event["event_type"] in {
            "landing_verified",
            "arm_failed",
            "obligation_armed",
        }:
            for key in (
                "observed_base",
                "source_base",
                "replay_base",
                "merge_commit",
                "fetched_main",
                "composition_merge_base",
                "expected_tree",
                "actual_tree",
            ):
                if not SHA_RE.fullmatch(str(event.get(key) or "")):
                    raise StoreError(f"invalid verified {key} at line {line_number}")
            if event["expected_tree"] != event["actual_tree"]:
                raise StoreError(f"verified tree mismatch at line {line_number}")
            if event["composition_merge_base"] != event["source_base"]:
                raise StoreError(
                    f"verified composition base differs from source base at line {line_number}"
                )
            count = event.get("source_commit_count")
            replay_commits = event.get("replay_commits")
            if (
                type(count) is not int
                or count <= 0
                or not isinstance(replay_commits, list)
                or len(replay_commits) != count
                or not all(
                    isinstance(value, str) and SHA_RE.fullmatch(value)
                    for value in replay_commits
                )
            ):
                raise StoreError(
                    f"invalid verified replay commits at line {line_number}"
                )
            relation = event.get("replay_base_is_ancestor_of_source")
            if type(relation) is not bool:
                raise StoreError(
                    f"invalid replay/source relation at line {line_number}"
                )
            expected_class = GREEN_HARD if relation else GREEN_SOFT
            expected_soft = None if relation else SOFT_ZERO_CONFLICT
            if (
                event.get("green_class") != expected_class
                or event.get("soft_green") != expected_soft
            ):
                raise StoreError(f"invalid verified green class at line {line_number}")
            source_problem = _receipt_problem(
                event.get("source_receipt"),
                str(event["expected_head"]),
                require_envelope=True,
            )
            if source_problem:
                raise StoreError(
                    f"invalid verified source receipt at line {line_number}: "
                    f"{source_problem}"
                )
            base_receipt = event.get("base_receipt")
            if relation and base_receipt is not None:
                raise StoreError(
                    f"hard replay unexpectedly carries base receipt at line {line_number}"
                )
            if not relation:
                base_problem = _receipt_problem(
                    base_receipt,
                    str(event["replay_base"]),
                    require_envelope=True,
                )
                if base_problem:
                    raise StoreError(
                        f"soft replay inherits from non-hard base at line {line_number}: "
                        f"{base_problem}"
                    )
        if event["event_type"] == "obligation_armed":
            obligation = event.get("obligation")
            if (
                not isinstance(obligation, Mapping)
                or not isinstance(obligation.get("obligation_id"), str)
                or obligation.get("launch_durable") is not True
            ):
                raise StoreError(f"malformed armed obligation at line {line_number}")
        if event["event_type"] == "failure":
            if (
                not isinstance(event.get("reason_code"), str)
                or not event["reason_code"]
            ):
                raise StoreError(f"failure has no reason code at line {line_number}")
            if not isinstance(event.get("message"), str) or not event["message"]:
                raise StoreError(f"failure has no message at line {line_number}")
            for key in ("observed_base", "source_base"):
                if not SHA_RE.fullmatch(str(event.get(key) or "")):
                    raise StoreError(f"failure has invalid {key} at line {line_number}")
            if (
                type(event.get("source_commit_count")) is not int
                or event["source_commit_count"] <= 0
            ):
                raise StoreError(
                    f"failure has invalid source count at line {line_number}"
                )
            for key in (
                "replay_base",
                "merge_commit",
                "fetched_main",
                "composition_merge_base",
                "expected_tree",
                "actual_tree",
            ):
                value = event.get(key)
                if value is not None and not SHA_RE.fullmatch(str(value)):
                    raise StoreError(f"failure has invalid {key} at line {line_number}")

    @staticmethod
    def _validate_sequences(events: Sequence[Mapping[str, Any]]) -> None:
        attempts: dict[str, list[Mapping[str, Any]]] = {}
        event_ids: set[str] = set()
        for event in events:
            event_id = str(event["event_id"])
            if event_id in event_ids:
                raise StoreError(f"duplicate landing event_id {event_id}")
            event_ids.add(event_id)
            attempts.setdefault(str(event["attempt_id"]), []).append(event)
        live_owners: dict[tuple[object, object], str] = {}
        for attempt_id, rows in attempts.items():
            if rows[-1]["event_type"] in {"failure", "obligation_armed"}:
                continue
            owner_key = (rows[0]["repo"], rows[0]["pr"])
            prior = live_owners.get(owner_key)
            if prior is not None:
                raise StoreError(
                    f"multiple nonterminal attempts own {owner_key[0]}#{owner_key[1]}: "
                    f"{prior}, {attempt_id}"
                )
            live_owners[owner_key] = attempt_id
        for attempt_id, rows in attempts.items():
            if rows[0]["event_type"] != "intent":
                raise StoreError(f"attempt {attempt_id} does not begin with intent")
            intent = rows[0]
            identity = tuple(intent[key] for key in ("repo", "pr", "expected_head"))
            terminal = False
            verified: Mapping[str, Any] | None = None
            requested = False
            calls: dict[str, Mapping[str, Any] | None] = {}
            merge_accepted = False
            definitive_negative_seen = False
            postmerge_commit: str | None = None
            for index, row in enumerate(rows):
                if (
                    tuple(row[key] for key in ("repo", "pr", "expected_head"))
                    != identity
                ):
                    raise StoreError(f"attempt {attempt_id} changes immutable identity")
                if index and row["event_type"] == "intent":
                    raise StoreError(f"attempt {attempt_id} has duplicate intent")
                if terminal:
                    raise StoreError(
                        f"attempt {attempt_id} has events after terminal state"
                    )
                event_type = row["event_type"]
                if event_type == "merge_requested":
                    if verified is not None:
                        raise StoreError(
                            f"attempt {attempt_id} requests merge after verification"
                        )
                    if requested:
                        raise StoreError(f"attempt {attempt_id} requests merge twice")
                    requested = True
                    if row.get("expected_head_guard") != intent["expected_head"]:
                        raise StoreError(
                            f"attempt {attempt_id} changes expected-head guard"
                        )
                    if row.get("observed_base") != intent.get("observed_base"):
                        raise StoreError(
                            f"attempt {attempt_id} changes requested base provenance"
                        )
                if event_type == "merge_call_started":
                    if not requested or verified is not None:
                        raise StoreError(
                            f"attempt {attempt_id} starts a call outside request state"
                        )
                    if merge_accepted or definitive_negative_seen:
                        raise StoreError(
                            f"attempt {attempt_id} starts a call after a definitive response"
                        )
                    call_id = str(row["call_id"])
                    if call_id in calls:
                        raise StoreError(
                            f"attempt {attempt_id} repeats merge call {call_id}"
                        )
                    calls[call_id] = None
                if event_type == "merge_response":
                    call_id = str(row["call_id"])
                    if call_id not in calls:
                        raise StoreError(
                            f"attempt {attempt_id} responds to unknown call {call_id}"
                        )
                    if calls[call_id] is not None:
                        raise StoreError(
                            f"attempt {attempt_id} responds twice to call {call_id}"
                        )
                    calls[call_id] = row
                    if row.get("merged") is True:
                        merge_accepted = True
                    if row.get("definitive_no_mutation") is True:
                        definitive_negative_seen = True
                if (
                    event_type
                    in {
                        "merge_call_started",
                        "merge_response",
                        "merge_pending",
                        "landing_quarantined",
                    }
                    and not requested
                ):
                    raise StoreError(
                        f"attempt {attempt_id} records merge state before request"
                    )
                if (
                    event_type
                    in {
                        "merge_call_started",
                        "merge_response",
                        "merge_pending",
                        "landing_quarantined",
                    }
                    and verified is not None
                ):
                    raise StoreError(
                        f"attempt {attempt_id} records merge state after verification"
                    )
                if event_type == "landing_quarantined" and not calls:
                    raise StoreError(
                        f"attempt {attempt_id} quarantines before a merge call"
                    )
                candidate_commit = (
                    row.get("response_merge_commit")
                    if event_type == "merge_response"
                    else row.get("merge_commit")
                )
                if candidate_commit is not None:
                    if postmerge_commit is None:
                        postmerge_commit = str(candidate_commit)
                    elif candidate_commit != postmerge_commit:
                        raise StoreError(
                            f"attempt {attempt_id} changes observed merge commit"
                        )
                if event_type == "landing_verified":
                    if not requested:
                        raise StoreError(
                            f"attempt {attempt_id} verifies an unrequested merge"
                        )
                    if not calls:
                        raise StoreError(
                            f"attempt {attempt_id} verifies before any merge call"
                        )
                    if verified is not None:
                        raise StoreError(f"attempt {attempt_id} verifies landing twice")
                    for key in (
                        "observed_base",
                        "source_base",
                        "source_commit_count",
                    ):
                        if row.get(key) != intent.get(key):
                            raise StoreError(
                                f"attempt {attempt_id} changes verified {key}"
                            )
                    verified = row
                if (
                    event_type in {"arm_failed", "obligation_armed"}
                    and verified is None
                ):
                    raise StoreError(
                        f"attempt {attempt_id} arms before replay verification"
                    )
                if event_type in {"arm_failed", "obligation_armed"}:
                    assert verified is not None
                    for key in (
                        "observed_base",
                        "source_base",
                        "source_commit_count",
                        "replay_base",
                        "merge_commit",
                        "fetched_main",
                        "composition_merge_base",
                        "expected_tree",
                        "actual_tree",
                        "replay_commits",
                        "replay_base_is_ancestor_of_source",
                        "green_class",
                        "soft_green",
                        "source_receipt",
                        "base_receipt",
                    ):
                        if row.get(key) != verified.get(key):
                            raise StoreError(
                                f"attempt {attempt_id} changes armed replay field {key}"
                            )
                if event_type in {"failure", "obligation_armed"}:
                    terminal = True

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_parent()
        record = dict(event)
        self._validate_event(record, 0)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing: list[dict[str, Any]] = []
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        prior = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise StoreError(
                            f"invalid landing JSONL at line {line_number}: {error}"
                        ) from error
                    self._validate_event(prior, line_number)
                    existing.append(prior)
                self._validate_sequences([*existing, record])
                handle.seek(0, os.SEEK_END)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return record


class LandingExecutor:
    def __init__(
        self,
        *,
        github: Any,
        repository: Any,
        receipt_authority: Any,
        armer: Any,
        mutation_barrier: MutationBarrier,
        store: EventStore,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        attempt_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        event_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        now: Callable[[], str] = utc_now,
    ):
        self.github = github
        self.repository = repository
        self.receipt_authority = receipt_authority
        self.armer = armer
        self.mutation_barrier = mutation_barrier
        self.store = store
        self.monotonic = monotonic
        self.sleep = sleep
        self.attempt_id = attempt_id
        self.event_id = event_id
        self.now = now

    @staticmethod
    def _validate_inputs(repo: str, pr: int, expected_head: str) -> None:
        if repo != SUPPORTED_REPO:
            raise Refused(
                f"unsupported repository {repo!r}; first version supports {SUPPORTED_REPO}"
            )
        if type(pr) is not int or pr <= 0:
            raise Refused("PR must be a positive integer")
        if not SHA_RE.fullmatch(expected_head):
            raise Refused("expected head X must be a lowercase 40-hex commit")

    @staticmethod
    def _attempt_rows(
        events: Sequence[Mapping[str, Any]], attempt_id: str
    ) -> list[dict[str, Any]]:
        return [dict(row) for row in events if row["attempt_id"] == attempt_id]

    @staticmethod
    def _latest_matching_attempt(
        events: Sequence[Mapping[str, Any]], repo: str, pr: int, expected_head: str
    ) -> list[dict[str, Any]] | None:
        order: list[str] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in events:
            attempt = str(row["attempt_id"])
            if attempt not in grouped:
                order.append(attempt)
                grouped[attempt] = []
            grouped[attempt].append(dict(row))
        nonterminal: list[list[dict[str, Any]]] = []
        for attempt in order:
            rows = grouped[attempt]
            intent = rows[0]
            if intent["repo"] != repo or intent["pr"] != pr:
                continue
            if rows[-1]["event_type"] not in {"failure", "obligation_armed"}:
                nonterminal.append(rows)
        if len(nonterminal) > 1:
            raise StoreError(f"multiple nonterminal attempts own {repo}#{pr}")
        if nonterminal:
            rows = nonterminal[0]
            if rows[0]["expected_head"] != expected_head:
                raise Refused(
                    "another nonterminal attempt owns this PR at a different head"
                )
            return rows
        for attempt in reversed(order):
            rows = grouped[attempt]
            intent = rows[0]
            if intent["repo"] != repo or intent["pr"] != pr:
                continue
            if intent["expected_head"] == expected_head:
                return rows
        return None

    def _event(
        self, intent: Mapping[str, Any], event_type: str, **payload: Any
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "event_id": self.event_id(),
            "event_type": event_type,
            "recorded_at": self.now(),
            "attempt_id": intent["attempt_id"],
            "repo": intent["repo"],
            "pr": intent["pr"],
            "expected_head": intent["expected_head"],
            **payload,
        }

    @staticmethod
    def _assert_open_snapshot(
        snapshot: PullRequestSnapshot, expected_head: str
    ) -> None:
        if snapshot.state != "OPEN":
            raise Refused(f"PR state is {snapshot.state}, not OPEN")
        if snapshot.merge_commit is not None:
            raise Refused("OPEN PR unexpectedly reports mergeCommit.oid")
        if snapshot.head != expected_head:
            raise Refused(
                f"PR head drift: expected {expected_head}, observed {snapshot.head}"
            )
        if snapshot.base != TARGET_BRANCH:
            raise Refused(
                f"PR targets forbidden base {snapshot.base!r}, expected {TARGET_BRANCH!r}"
            )
        if snapshot.is_draft:
            raise Refused("draft PRs are not landable")
        if snapshot.review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
            raise Refused(f"unresolved review decision: {snapshot.review_decision}")
        if snapshot.review_decision not in {"", "APPROVED"}:
            raise Refused(
                f"unknown review decision {snapshot.review_decision!r}; refusing"
            )

    def _assert_reviews_resolved(self, repo: str, pr: int) -> None:
        unresolved = self.github.unresolved_review_threads(repo, pr)
        if unresolved:
            raise Refused(f"{unresolved} unresolved review thread(s)")

    def _fresh_review_evidence(
        self, repo: str, pr: int, expected_head: str
    ) -> dict[str, Any]:
        evidence = self.github.review_evidence(repo, pr, expected_head)
        problem = _review_evidence_problem(
            evidence, repo=repo, pr=pr, expected_head=expected_head
        )
        if problem:
            raise Refused(f"fresh exact-head review evidence refused: {problem}")
        return dict(evidence)

    def _assert_review_evidence_still_authoritative(
        self, intent: Mapping[str, Any]
    ) -> None:
        repo = str(intent["repo"])
        pr = int(intent["pr"])
        expected_head = str(intent["expected_head"])
        persisted = intent.get("review_evidence")
        problem = _review_evidence_problem(
            persisted, repo=repo, pr=pr, expected_head=expected_head
        )
        if problem:
            raise StoreError(
                f"persisted exact-head review evidence is malformed: {problem}"
            )
        live = self._fresh_review_evidence(repo, pr, expected_head)
        if persisted != live:
            raise Refused(
                "persisted exact-head review evidence differs from the fresh "
                "role-tagged review authority"
            )

    @staticmethod
    def _decode_merge_response(output: str) -> tuple[bool, str | None, str]:
        payload = _json_object(output, "synchronous GitHub merge response")
        merged = payload.get("merged")
        if type(merged) is not bool:
            raise LandingError("GitHub merge response has no boolean merged field")
        error_message = payload.get("message")
        if not isinstance(error_message, str) or not error_message:
            raise LandingError("GitHub merge response has no message")
        raw_sha = payload.get("sha")
        merge_commit = None
        if merged:
            merge_commit = _full_sha(raw_sha, "synchronous merge response sha")
        elif raw_sha is not None and raw_sha != "":
            raise LandingError("unmerged GitHub response unexpectedly carries a sha")
        return merged, merge_commit, error_message

    @staticmethod
    def _decode_http_envelope(output: str) -> tuple[int, dict[str, Any]]:
        normalized = output.replace("\r\n", "\n")
        protocol = r"HTTP/(?:1\.0|1\.1|2|2\.0)"
        status_lines = re.findall(rf"(?m)^{protocol} [0-9]{{3}}(?: .*)?$", normalized)
        if len(status_lines) != 1:
            raise LandingError(
                "GitHub merge response must contain exactly one HTTP status line"
            )
        headers, separator, body = normalized.partition("\n\n")
        if not separator or not body.strip():
            raise LandingError("GitHub merge response has no complete HTTP envelope")
        first_line = headers.splitlines()[0] if headers.splitlines() else ""
        match = re.fullmatch(rf"{protocol} ([0-9]{{3}})(?: .*)?", first_line)
        if match is None or first_line != status_lines[0]:
            raise LandingError("GitHub merge response status line is malformed")
        status = int(match.group(1))
        payload = _json_object(body, "GitHub merge HTTP body")
        body_status = payload.get("status")
        if body_status is not None:
            if type(body_status) is int:
                parsed_body_status = body_status
            elif isinstance(body_status, str) and re.fullmatch(
                r"[0-9]{3}", body_status
            ):
                parsed_body_status = int(body_status)
            else:
                raise LandingError("GitHub merge JSON status field is malformed")
            if parsed_body_status != status:
                raise LandingError(
                    "GitHub merge JSON status contradicts the HTTP status line"
                )
        return status, payload

    @classmethod
    def _decode_merge_http_response(
        cls, response: subprocess.CompletedProcess[str]
    ) -> MergeHttpResult:
        status, payload = cls._decode_http_envelope(response.stdout)
        if status == 200:
            if response.returncode != 0:
                raise LandingError(
                    "GitHub merge returned HTTP 200 with a failing process status"
                )
            merged, merge_commit, message = cls._decode_merge_response(
                json.dumps(payload, sort_keys=True)
            )
            return MergeHttpResult(
                http_status=status,
                merged=merged,
                merge_commit=merge_commit,
                message=message,
                definitive_no_mutation=not merged,
            )

        error_message = payload.get("message")
        if not isinstance(error_message, str) or not error_message:
            raise LandingError("GitHub merge HTTP error has no message")
        if "merged" in payload and type(payload["merged"]) is not bool:
            raise LandingError("GitHub merge HTTP error has malformed merged field")
        if payload.get("merged") is True:
            raise LandingError(
                "GitHub merge HTTP error contradicts its no-success status"
            )
        raw_sha = payload.get("sha")
        if raw_sha is not None and raw_sha != "":
            raise LandingError(
                "GitHub merge HTTP error contradicts its no-success status"
            )
        if response.returncode == 0:
            raise LandingError(
                f"GitHub merge returned HTTP {status} with a successful process status"
            )
        return MergeHttpResult(
            http_status=status,
            merged=False if status in DEFINITIVE_NO_MUTATION_HTTP_STATUSES else None,
            merge_commit=None,
            message=error_message,
            definitive_no_mutation=(status in DEFINITIVE_NO_MUTATION_HTTP_STATUSES),
        )

    def _new_intent(
        self,
        repo: str,
        pr: int,
        expected_head: str,
        actor: str,
    ) -> dict[str, Any]:
        snapshot = self.github.snapshot(repo, pr)
        self._assert_open_snapshot(snapshot, expected_head)
        self._assert_reviews_resolved(repo, pr)
        review_evidence = self._fresh_review_evidence(repo, pr, expected_head)
        observed_base = self.repository.fetch_base()
        self.repository.fetch_head(pr, expected_head)
        source = self.repository.source_provenance(expected_head, observed_base)
        github_commits = self.github.pr_commits(repo, pr)
        if github_commits != source.source_commits:
            raise Refused(
                "GitHub PR commit list does not equal the freshly derived S..X graph"
            )
        source_receipt = self.receipt_authority.verify(expected_head)
        base_is_ancestor = self.repository.is_ancestor(observed_base, expected_head)
        planned_green_class = GREEN_HARD if base_is_ancestor else GREEN_SOFT
        # A divergent composition is one soft hop.  Requiring exact hard-green
        # evidence at both X and its observed base prevents inheriting atop an
        # already-soft, unverified main.
        base_receipt = (
            None
            if base_is_ancestor
            else self.receipt_authority.verify(observed_base).as_json()
        )
        attempt_id = self.attempt_id()
        if not ID_RE.fullmatch(attempt_id):
            raise LandingError("attempt ID generator did not return 32 lowercase hex")
        intent = {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "event_id": self.event_id(),
            "event_type": "intent",
            "recorded_at": self.now(),
            "attempt_id": attempt_id,
            "repo": repo,
            "pr": pr,
            "expected_head": expected_head,
            "target_branch": TARGET_BRANCH,
            "actor": actor,
            "review_decision": snapshot.review_decision,
            "unresolved_review_threads": 0,
            "review_evidence": review_evidence,
            "source_receipt": source_receipt.as_json(),
            "base_receipt": base_receipt,
            "github_pr_commit_count": len(github_commits),
            "github_pr_commits": list(github_commits),
            "observed_base_is_ancestor_of_source": base_is_ancestor,
            "planned_green_class": planned_green_class,
            **source.as_json(),
        }
        return self.store.append(intent)

    def _append_failure(
        self,
        intent: Mapping[str, Any],
        reason_code: str,
        message: str,
        **details: Any,
    ) -> None:
        details = dict(details)
        replay_fields: dict[str, Any] = {
            "observed_base": intent.get("observed_base"),
            "source_base": intent.get("source_base"),
            "source_commit_count": intent.get("source_commit_count"),
            "replay_base": details.pop("replay_base", None),
            "merge_commit": details.pop("merge_commit", None),
            "fetched_main": details.pop("fetched_main", None),
            "composition_merge_base": details.pop("composition_merge_base", None),
            "expected_tree": details.pop("expected_tree", None),
            "actual_tree": details.pop("actual_tree", None),
        }
        # ReplayMismatch carries a complete diagnostic map.  Immutable attempt
        # identity/provenance comes from the durable intent, never from that
        # secondary map, and one merged payload avoids duplicate **kwargs.
        for key in (
            "schema_version",
            "record_type",
            "event_id",
            "event_type",
            "recorded_at",
            "attempt_id",
            "repo",
            "pr",
            "expected_head",
            "observed_base",
            "source_base",
            "source_commit_count",
            "reason_code",
            "message",
        ):
            details.pop(key, None)
        replay_fields.update(details)
        self.store.append(
            self._event(
                intent,
                "failure",
                reason_code=reason_code,
                message=message,
                **replay_fields,
            )
        )

    def _refuse(
        self,
        intent: Mapping[str, Any],
        reason_code: str,
        message: str,
        **details: Any,
    ) -> None:
        self._append_failure(intent, reason_code, message, **details)
        raise Refused(message)

    def _retain_pending(
        self,
        intent: Mapping[str, Any],
        reason_code: str,
        message: str,
        **details: Any,
    ) -> NoReturn:
        self.store.append(
            self._event(
                intent,
                "merge_pending",
                reason_code=reason_code,
                message=message,
                **details,
            )
        )
        raise Pending(message)

    def _quarantine(
        self,
        intent: Mapping[str, Any],
        reason_code: str,
        message: str,
        *,
        merge_commit: str,
        fetched_main: str,
        diagnostics: Mapping[str, Any],
    ) -> NoReturn:
        self.store.append(
            self._event(
                intent,
                "landing_quarantined",
                reason_code=reason_code,
                message=message,
                github_state="MERGED",
                merge_commit=merge_commit,
                fetched_main=fetched_main,
                diagnostics=dict(diagnostics),
            )
        )
        raise Pending(
            f"merged exact operation is quarantined for fresh proof: {message}"
        )

    @staticmethod
    def _call_history(
        rows: Sequence[Mapping[str, Any]],
    ) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
        responses = {
            str(row["call_id"]): row
            for row in rows
            if row["event_type"] == "merge_response"
        }
        return [
            (row, responses.get(str(row["call_id"])))
            for row in rows
            if row["event_type"] == "merge_call_started"
        ]

    @classmethod
    def _has_ambiguous_call(cls, rows: Sequence[Mapping[str, Any]]) -> bool:
        return any(
            response is None
            or (
                response.get("merged") is not True
                and response.get("definitive_no_mutation") is not True
            )
            for _, response in cls._call_history(rows)
        )

    @classmethod
    def _dereference_persisted_merge_response(
        cls, response: Mapping[str, Any]
    ) -> MergeHttpResult:
        envelope = response.get("http_envelope")
        returncode = response.get("returncode")
        if not isinstance(envelope, str) or type(returncode) is not int:
            raise StoreError(
                "persisted merge response has no dereferenceable HTTP envelope"
            )
        decoded = cls._decode_merge_http_response(
            subprocess.CompletedProcess(
                ["persisted-gh-api-response"], returncode, envelope, ""
            )
        )
        copied = (
            response.get("http_status"),
            response.get("merged"),
            response.get("response_merge_commit"),
            response.get("response_message"),
            response.get("definitive_no_mutation"),
        )
        actual = (
            decoded.http_status,
            decoded.merged,
            decoded.merge_commit,
            decoded.message,
            decoded.definitive_no_mutation,
        )
        if copied != actual:
            raise StoreError(
                "persisted merge response disposition does not match its HTTP envelope"
            )
        return decoded

    def _verified_from_event(self, row: Mapping[str, Any]) -> ReplayProvenance:
        replay_commits = row.get("replay_commits")
        if not isinstance(replay_commits, list):
            raise StoreError("landing_verified event has no replay commit list")
        return ReplayProvenance(
            expected_head=str(row["expected_head"]),
            observed_base=str(row["observed_base"]),
            source_base=str(row["source_base"]),
            source_commit_count=int(row["source_commit_count"]),
            replay_base=str(row["replay_base"]),
            merge_commit=str(row["merge_commit"]),
            fetched_main=str(row["fetched_main"]),
            composition_merge_base=str(row["composition_merge_base"]),
            expected_tree=str(row["expected_tree"]),
            actual_tree=str(row["actual_tree"]),
            replay_commits=tuple(str(value) for value in replay_commits),
            replay_base_is_ancestor_of_source=bool(
                row["replay_base_is_ancestor_of_source"]
            ),
            green_class=str(row["green_class"]),
            soft_green=(
                str(row["soft_green"]) if row.get("soft_green") is not None else None
            ),
            source_receipt=dict(row["source_receipt"]),
            base_receipt=(
                dict(row["base_receipt"])
                if isinstance(row.get("base_receipt"), Mapping)
                else None
            ),
        )

    def _assert_receipt_still_authoritative(
        self, persisted: object, expected_head: str
    ) -> None:
        problem = _receipt_problem(persisted, expected_head, require_envelope=True)
        if problem:
            raise StoreError(f"persisted exact-head receipt is malformed: {problem}")
        assert isinstance(persisted, Mapping)
        persisted_report = persisted.get("report")
        assert isinstance(persisted_report, Mapping)
        authority = str(persisted.get("authority") or "")
        verify_authority = getattr(self.receipt_authority, "verify_authority", None)
        if callable(verify_authority):
            live = verify_authority(expected_head, authority).as_json()
        else:
            live = self.receipt_authority.verify(expected_head).as_json()
        live_problem = _receipt_problem(live, expected_head, require_envelope=True)
        if live_problem:
            raise Refused(
                "fresh canonical exact-head receipt is malformed: " f"{live_problem}"
            )
        live_report = live.get("report")
        assert isinstance(live_report, Mapping)
        if authority == LOCAL_VALIDATION_AUTHORITY:
            persisted_newest = persisted_report.get("newest_qualifying")
            live_newest = live_report.get("newest_qualifying")
            assert isinstance(persisted_newest, Mapping)
            assert isinstance(live_newest, Mapping)
            persisted_identity = persisted_newest.get("receipt_identity")
            live_identity = live_newest.get("receipt_identity")
            assert isinstance(persisted_identity, Mapping)
            assert isinstance(live_identity, Mapping)
            if persisted_identity.get("digest") != live_identity.get("digest"):
                raise Refused(
                    "persisted receipt identity digest differs from the fresh "
                    f"canonical receipt for {expected_head}"
                )
            # Store-wide counts may grow, but the selected receipt is stable.
            if dict(persisted_newest) != dict(live_newest):
                raise Refused(
                    "persisted receipt fields differ from the fresh canonical "
                    f"receipt for {expected_head}"
                )
        elif dict(persisted_report) != dict(live_report):
            raise Refused(
                "persisted hosted job evidence differs from the fresh canonical "
                f"job set for {expected_head}"
            )

    def _assert_intent_receipts_still_authoritative(
        self, intent: Mapping[str, Any]
    ) -> None:
        self._assert_receipt_still_authoritative(
            intent.get("source_receipt"), str(intent["expected_head"])
        )
        base_receipt = intent.get("base_receipt")
        if base_receipt is not None:
            self._assert_receipt_still_authoritative(
                base_receipt, str(intent["observed_base"])
            )

    def _assert_replay_receipts_still_authoritative(
        self, replay: ReplayProvenance
    ) -> None:
        self._assert_receipt_still_authoritative(
            replay.source_receipt, replay.expected_head
        )
        if replay.base_receipt is not None:
            self._assert_receipt_still_authoritative(
                replay.base_receipt, replay.replay_base
            )

    def _arm(
        self,
        intent: Mapping[str, Any],
        replay: ReplayProvenance,
        actor: str,
        recovered: bool,
    ) -> LandingResult:
        try:
            # A fsynced event is recovery provenance, never receipt authority.
            # Re-dereference every hard receipt immediately before arming.
            self._assert_replay_receipts_still_authoritative(replay)
            arm = self.armer.arm(
                str(intent["repo"]),
                int(intent["pr"]),
                replay.merge_commit,
                actor,
            )
            if not isinstance(arm, Mapping):
                raise LandingError("obligation armer returned a non-object")
            obligation_id = arm.get("obligation_id")
            if not isinstance(obligation_id, str) or not obligation_id:
                raise LandingError("obligation armer returned no obligation identity")
            if arm.get("launch_durable") is not True:
                raise LandingError(
                    "obligation armer did not prove canonical launch durability"
                )
        except LandingError as error:
            self.store.append(
                self._event(
                    intent,
                    "arm_failed",
                    message=str(error),
                    **replay.as_json(),
                )
            )
            raise
        self.store.append(
            self._event(
                intent,
                "obligation_armed",
                obligation=arm,
                **replay.as_json(),
            )
        )
        return LandingResult(
            attempt_id=str(intent["attempt_id"]),
            repo=str(intent["repo"]),
            pr=int(intent["pr"]),
            expected_head=str(intent["expected_head"]),
            replay_base=replay.replay_base,
            merge_commit=replay.merge_commit,
            obligation_id=obligation_id,
            recovered=recovered,
            green_class=replay.green_class,
            soft_green=replay.soft_green,
        )

    def _verify_and_arm(
        self,
        intent: Mapping[str, Any],
        merge_commit: str,
        actor: str,
        recovered: bool,
        visibility_timeout: float,
        poll_seconds: float,
    ) -> LandingResult:
        visibility_deadline = self.monotonic() + visibility_timeout
        while True:
            fetched_main = self.repository.fetch_base()
            visible = self.repository.commit_exists(merge_commit)
            on_main = (
                self.repository.is_ancestor(merge_commit, fetched_main)
                if visible
                else False
            )
            if visible and on_main:
                break
            if self.monotonic() >= visibility_deadline:
                self.store.append(
                    self._event(
                        intent,
                        "merge_pending",
                        message=(
                            "GitHub reports MERGED but mergeCommit.oid is not yet "
                            "visible on freshly fetched main"
                        ),
                        merge_commit=merge_commit,
                        fetched_main=fetched_main,
                        timeout_seconds=visibility_timeout,
                    )
                )
                raise Pending(
                    f"merged commit {merge_commit} is not visible on fresh main; "
                    f"resume attempt {intent['attempt_id']}"
                )
            self.sleep(
                min(
                    poll_seconds,
                    max(0.0, visibility_deadline - self.monotonic()),
                )
            )

        self.repository.fetch_head(int(intent["pr"]), str(intent["expected_head"]))
        github_commits = self.github.pr_commits(str(intent["repo"]), int(intent["pr"]))
        if list(github_commits) != intent["github_pr_commits"]:
            self._quarantine(
                intent,
                "postmerge_pr_commit_list_changed",
                "GitHub PR commit list no longer matches the authorized source list",
                merge_commit=merge_commit,
                fetched_main=fetched_main,
                diagnostics={
                    "authorized_github_pr_commits": intent["github_pr_commits"],
                    "observed_github_pr_commits": list(github_commits),
                },
            )
        try:
            replay = self.repository.verify_replay(
                expected_head=str(intent["expected_head"]),
                observed_base=str(intent["observed_base"]),
                source_base=str(intent["source_base"]),
                source_commit_count=int(intent["source_commit_count"]),
                source_commits=tuple(str(x) for x in intent["source_commits"]),
                merge_commit=merge_commit,
                fetched_main=fetched_main,
            )
        except ReplayMismatch as error:
            details = dict(error.details)
            details.setdefault("proof_error", str(error))
            details.setdefault("expected_head", str(intent["expected_head"]))
            self._quarantine(
                intent,
                "replay_mismatch",
                str(error),
                merge_commit=merge_commit,
                fetched_main=fetched_main,
                diagnostics=details,
            )
        try:
            source_receipt = self.receipt_authority.verify(
                str(intent["expected_head"])
            ).as_json()
            base_receipt = (
                None
                if replay.replay_base_is_ancestor_of_source
                else self.receipt_authority.verify(replay.replay_base).as_json()
            )
        except Refused as error:
            # GitHub has already merged, so this is not a terminal attempt: an
            # independently validated actual Y can make the same durable
            # attempt eligible on recovery.  It is still neither verified nor
            # armed until that exact authority answers positively.
            self.store.append(
                self._event(
                    intent,
                    "merge_pending",
                    reason_code="actual_source_or_base_not_hard_green",
                    message=str(error),
                    **replay.as_json(),
                )
            )
            raise Pending(
                "merged composition awaits exact hard-green source/base evidence: "
                f"{error}"
            ) from error
        replay = replace(
            replay,
            source_receipt=source_receipt,
            base_receipt=base_receipt,
        )
        self.store.append(self._event(intent, "landing_verified", **replay.as_json()))
        return self._arm(intent, replay, actor, recovered)

    def _poll_merged(
        self,
        intent: Mapping[str, Any],
        actor: str,
        timeout: float,
        poll_seconds: float,
        recovered: bool,
    ) -> LandingResult:
        deadline = self.monotonic() + timeout
        while True:
            snapshot = self.github.snapshot(str(intent["repo"]), int(intent["pr"]))
            if snapshot.head != intent["expected_head"]:
                self._retain_pending(
                    intent,
                    "postrequest_head_changed",
                    f"expected X {intent['expected_head']}, observed {snapshot.head}",
                    merge_commit=snapshot.merge_commit,
                )
            if snapshot.base != TARGET_BRANCH:
                self._retain_pending(
                    intent,
                    "postrequest_base_changed",
                    f"PR base changed to {snapshot.base!r}",
                    merge_commit=snapshot.merge_commit,
                )
            if snapshot.state == "MERGED":
                if snapshot.merge_commit is None:
                    if self.monotonic() >= deadline:
                        self.store.append(
                            self._event(
                                intent,
                                "merge_pending",
                                reason_code="merge_commit_oid_pending",
                                message=(
                                    "GitHub reports MERGED but mergeCommit.oid "
                                    "has not propagated"
                                ),
                                timeout_seconds=timeout,
                            )
                        )
                        raise Pending(
                            "merged PR still has no mergeCommit.oid after "
                            f"{timeout:g}s; resume attempt {intent['attempt_id']}"
                        )
                    self.sleep(
                        min(
                            poll_seconds,
                            max(0.0, deadline - self.monotonic()),
                        )
                    )
                    continue
                return self._verify_and_arm(
                    intent,
                    snapshot.merge_commit,
                    actor,
                    recovered,
                    visibility_timeout=timeout,
                    poll_seconds=poll_seconds,
                )
            if snapshot.state == "CLOSED":
                self._retain_pending(
                    intent,
                    "postrequest_closed_without_merge",
                    "PR closed while the exact merge mutation remains unresolved",
                )
            try:
                self._assert_open_snapshot(snapshot, str(intent["expected_head"]))
                self._assert_reviews_resolved(str(intent["repo"]), int(intent["pr"]))
                self._assert_review_evidence_still_authoritative(intent)
            except Refused as error:
                self._retain_pending(intent, "postrequest_identity_changed", str(error))
            if self.monotonic() >= deadline:
                self.store.append(
                    self._event(
                        intent,
                        "merge_pending",
                        message="bounded poll expired while PR remained OPEN",
                        timeout_seconds=timeout,
                    )
                )
                raise Pending(
                    f"synchronous merge response has not propagated after {timeout:g}s; resume attempt "
                    f"{intent['attempt_id']}"
                )
            self.sleep(min(poll_seconds, max(0.0, deadline - self.monotonic())))

    def _continue_attempt(
        self,
        rows: Sequence[Mapping[str, Any]],
        actor: str,
        timeout: float,
        poll_seconds: float,
        recovered: bool,
    ) -> LandingResult:
        intent = rows[0]
        requested = any(row["event_type"] == "merge_requested" for row in rows)
        armed = next(
            (row for row in reversed(rows) if row["event_type"] == "obligation_armed"),
            None,
        )
        if armed is not None:
            replay = self._verified_from_event(armed)
            obligation = armed.get("obligation")
            obligation_id = (
                obligation.get("obligation_id")
                if isinstance(obligation, Mapping)
                else None
            )
            if not isinstance(obligation_id, str) or not obligation_id:
                raise StoreError("terminal obligation_armed event is malformed")
            try:
                live_obligation = self.armer.verify(
                    str(intent["repo"]), replay.merge_commit, obligation_id
                )
            except LandingError as error:
                raise Pending(
                    "recorded obligation_armed is not live in the canonical "
                    f"obligation store: {error}"
                ) from error
            if live_obligation.get("obligation_id") != obligation_id or not bool(
                live_obligation.get("launch_durable")
            ):
                raise Pending(
                    "canonical obligation verifier returned a mismatched or "
                    "non-durable record"
                )
            return LandingResult(
                attempt_id=str(intent["attempt_id"]),
                repo=str(intent["repo"]),
                pr=int(intent["pr"]),
                expected_head=str(intent["expected_head"]),
                replay_base=replay.replay_base,
                merge_commit=replay.merge_commit,
                obligation_id=obligation_id,
                recovered=True,
                green_class=replay.green_class,
                soft_green=replay.soft_green,
            )
        verified = next(
            (row for row in reversed(rows) if row["event_type"] == "landing_verified"),
            None,
        )
        if verified is not None:
            return self._arm(
                intent, self._verified_from_event(verified), actor, recovered=True
            )

        if recovered:
            try:
                self._assert_intent_receipts_still_authoritative(intent)
            except Refused as error:
                if requested:
                    self.store.append(
                        self._event(
                            intent,
                            "merge_pending",
                            reason_code="persisted_receipt_not_live",
                            message=str(error),
                        )
                    )
                    raise Pending(
                        "recovered merge awaits exact persisted receipt identity: "
                        f"{error}"
                    ) from error
                self._refuse(
                    intent,
                    "persisted_receipt_not_live",
                    str(error),
                )

        snapshot = self.github.snapshot(str(intent["repo"]), int(intent["pr"]))
        if snapshot.state == "MERGED":
            if not requested:
                self._refuse(
                    intent,
                    "merged_without_durable_request",
                    "PR merged after intent but before this executor recorded a request",
                    merge_commit=snapshot.merge_commit,
                )
            if snapshot.head != intent["expected_head"]:
                self._retain_pending(
                    intent,
                    "postrequest_merged_different_head",
                    f"GitHub merged {snapshot.head}, not X {intent['expected_head']}",
                    merge_commit=snapshot.merge_commit,
                )
            if snapshot.base != TARGET_BRANCH:
                self._retain_pending(
                    intent,
                    "postrequest_merged_to_wrong_base",
                    f"GitHub merged PR to {snapshot.base!r}, not {TARGET_BRANCH!r}",
                    merge_commit=snapshot.merge_commit,
                )
            if snapshot.merge_commit is None:
                return self._poll_merged(
                    intent,
                    actor,
                    timeout,
                    poll_seconds,
                    recovered=True,
                )
            return self._verify_and_arm(
                intent,
                snapshot.merge_commit,
                actor,
                recovered=True,
                visibility_timeout=timeout,
                poll_seconds=poll_seconds,
            )
        try:
            self._assert_open_snapshot(snapshot, str(intent["expected_head"]))
            self._assert_reviews_resolved(str(intent["repo"]), int(intent["pr"]))
            self._assert_review_evidence_still_authoritative(intent)
        except Refused as error:
            if requested:
                self._retain_pending(intent, "postrequest_identity_changed", str(error))
            self._refuse(intent, "premerge_identity_changed", str(error))
        accepted = any(
            (row["event_type"] == "merge_response" and row.get("merged") is True)
            or row["event_type"] == "landing_quarantined"
            for row in rows
        )
        if accepted:
            # The synchronous endpoint reported a completed merge. GitHub's PR
            # view may lag that response, so recovery polls without issuing a
            # duplicate mutation while the exact-operation barrier remains.
            return self._poll_merged(
                intent,
                actor,
                timeout,
                poll_seconds,
                recovered=True,
            )
        call_history = self._call_history(rows)
        definitive_negative = next(
            (
                response
                for _, response in call_history
                if response is not None
                and response.get("definitive_no_mutation") is True
            ),
            None,
        )
        if definitive_negative is not None:
            if (
                call_history
                and call_history[0][1] is definitive_negative
                and not any(response is None for _, response in call_history[:1])
            ):
                decoded_negative = self._dereference_persisted_merge_response(
                    definitive_negative
                )
                if not decoded_negative.definitive_no_mutation:
                    raise StoreError(
                        "persisted response is not a definitive merge refusal"
                    )
                self.mutation_barrier.clear(
                    actor=actor,
                    repo=str(intent["repo"]),
                    pr=int(intent["pr"]),
                    operation=str(intent["expected_head"]),
                    attempt_id=str(intent["attempt_id"]),
                )
                self._refuse(
                    intent,
                    "synchronous_merge_refused",
                    decoded_negative.message,
                )
            self._retain_pending(
                intent,
                "negative_after_ambiguous_merge_call",
                "a later definitive refusal cannot disprove an earlier ambiguous merge call",
                http_status=definitive_negative.get("http_status"),
            )
        fresh_base = self.repository.fetch_base()
        if fresh_base != intent["observed_base"]:
            if requested:
                self._retain_pending(
                    intent,
                    "postrequest_main_advanced",
                    f"main advanced from {intent['observed_base']} to {fresh_base}",
                )
            self._refuse(
                intent,
                "main_advanced_before_merge",
                f"main advanced from {intent['observed_base']} to {fresh_base}",
            )
        self.repository.fetch_head(int(intent["pr"]), str(intent["expected_head"]))
        github_commits = self.github.pr_commits(str(intent["repo"]), int(intent["pr"]))
        if list(github_commits) != intent["github_pr_commits"]:
            if requested:
                self._retain_pending(
                    intent,
                    "postrequest_commit_list_changed",
                    "GitHub PR commit list changed after durable request",
                )
            self._refuse(
                intent,
                "pr_commit_list_changed",
                "GitHub PR commit list changed after durable intent",
            )
        refreshed_source = self.repository.source_provenance(
            str(intent["expected_head"]), fresh_base
        )
        if refreshed_source.as_json() != {
            key: intent[key]
            for key in (
                "observed_base",
                "source_base",
                "source_commit_count",
                "source_commits",
                "source_tree",
                "observed_base_tree",
            )
        }:
            if requested:
                self._retain_pending(
                    intent,
                    "postrequest_source_provenance_changed",
                    "freshly recomputed S..X provenance differs from durable request",
                )
            self._refuse(
                intent,
                "source_provenance_changed",
                "freshly recomputed S..X provenance differs from durable intent",
            )
        # Dereference the canonical authority again at the final pre-merge
        # boundary.  The persisted intent proves what authorized the attempt;
        # this fresh query proves that authority still answers for X now.
        self._assert_intent_receipts_still_authoritative(intent)
        final_snapshot = self.github.snapshot(str(intent["repo"]), int(intent["pr"]))
        try:
            self._assert_open_snapshot(final_snapshot, str(intent["expected_head"]))
            self._assert_reviews_resolved(str(intent["repo"]), int(intent["pr"]))
            self._assert_review_evidence_still_authoritative(intent)
        except Refused as error:
            if requested:
                self._retain_pending(
                    intent, "postrequest_final_identity_changed", str(error)
                )
            self._refuse(intent, "final_premerge_identity_changed", str(error))
        if not requested:
            self.store.append(
                self._event(
                    intent,
                    "merge_requested",
                    merge_method="rebase",
                    request_semantics="synchronous-rest-v1",
                    expected_head_guard=str(intent["expected_head"]),
                    observed_base=str(intent["observed_base"]),
                )
            )
        prior_ambiguous_call = self._has_ambiguous_call(rows)
        # The Rust barrier carries the durable attempt id. A crash immediately
        # after this fsync can resume the same attempt before any call exists;
        # a crash after call_started retains that exact call history.
        self.mutation_barrier.arm(
            actor=actor,
            repo=str(intent["repo"]),
            pr=int(intent["pr"]),
            operation=str(intent["expected_head"]),
            attempt_id=str(intent["attempt_id"]),
        )
        call_id = self.event_id()
        self.store.append(
            self._event(
                intent,
                "merge_call_started",
                call_id=call_id,
                request_semantics="synchronous-rest-v1",
                expected_head_guard=str(intent["expected_head"]),
            )
        )
        call_count = len(self._call_history(rows)) + 1
        self.mutation_barrier.bind_call(
            actor=actor,
            repo=str(intent["repo"]),
            pr=int(intent["pr"]),
            operation=str(intent["expected_head"]),
            attempt_id=str(intent["attempt_id"]),
            call_count=call_count,
            call_id=call_id,
        )
        merged: bool | None = None
        response_merge_commit: str | None = None
        response_message: str | None = None
        http_status: int | None = None
        definitive_no_mutation = False
        parse_error: str | None = None
        try:
            response = self.github.request_rebase_merge(
                str(intent["repo"]), int(intent["pr"]), str(intent["expected_head"])
            )
            response_payload = {
                "returncode": response.returncode,
                "stdout_sha256": hashlib.sha256(response.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(response.stderr.encode()).hexdigest(),
                "output_excerpt": (response.stderr or response.stdout).strip()[:4096],
                "http_envelope": response.stdout,
            }
            if len(response.stdout.encode()) > MAX_HTTP_ENVELOPE_BYTES:
                response_payload["http_envelope"] = None
                parse_error = "GitHub merge HTTP envelope exceeds durable size bound"
            else:
                try:
                    decoded = self._decode_merge_http_response(response)
                    http_status = decoded.http_status
                    merged = decoded.merged
                    response_merge_commit = decoded.merge_commit
                    response_message = decoded.message
                    definitive_no_mutation = decoded.definitive_no_mutation
                except LandingError as error:
                    parse_error = str(error)
        except LandingError as error:
            # The transport result cannot prove the merge did not happen. The
            # exact-operation barrier remains armed and recovery may safely
            # retry the idempotent sha-guarded synchronous request.
            response_payload = {
                "returncode": None,
                "output_excerpt": str(error),
                "stdout_sha256": None,
                "stderr_sha256": None,
                "http_envelope": None,
            }
            parse_error = str(error)
        response_payload.update(
            call_id=call_id,
            http_status=http_status,
            merged=merged,
            response_merge_commit=response_merge_commit,
            response_message=response_message,
            definitive_no_mutation=definitive_no_mutation,
            parse_error=parse_error,
        )
        self.store.append(self._event(intent, "merge_response", **response_payload))
        if definitive_no_mutation:
            if not prior_ambiguous_call and not self._call_history(rows):
                # Only the first fully paired call can prove that no earlier
                # ambiguous invocation merged. Clear before terminal refusal;
                # clear failure leaves the exact operation recoverable.
                self.mutation_barrier.clear(
                    actor=actor,
                    repo=str(intent["repo"]),
                    pr=int(intent["pr"]),
                    operation=str(intent["expected_head"]),
                    attempt_id=str(intent["attempt_id"]),
                )
                self._refuse(
                    intent,
                    "synchronous_merge_refused",
                    response_message or "GitHub synchronously refused the merge",
                )
            self._retain_pending(
                intent,
                "negative_after_ambiguous_merge_call",
                "definitive refusal followed an unresolved earlier merge call; barrier retained",
                http_status=http_status,
            )
        return self._poll_merged(
            intent, actor, timeout, poll_seconds, recovered=recovered
        )

    def run(
        self,
        *,
        repo: str,
        pr: int,
        expected_head: str,
        actor: str,
        timeout: float,
        poll_seconds: float,
        adopted_barrier: MutationBarrierBinding | None = None,
    ) -> LandingResult:
        self._validate_inputs(repo, pr, expected_head)
        if timeout <= 0 or poll_seconds <= 0:
            raise Refused("timeout and poll interval must be positive")
        if not actor:
            raise Refused("actor identity is required")
        with self.store.host_lock():
            # Bind both new and recovered attempts to the requested repository
            # before any fetch, ref lookup, ancestry query, or tree proof.
            self.repository.ensure_checkout(repo)
            events = self.store.load()
            rows: list[dict[str, Any]] | None
            if adopted_barrier is not None:
                rows = self._attempt_rows(events, adopted_barrier.attempt_id)
                if not rows:
                    raise Pending(
                        "retained exact-operation barrier has no matching durable "
                        f"landing attempt {adopted_barrier.attempt_id}; mutation remains quarantined"
                    )
                intent = rows[0]
                if (
                    intent.get("repo") != repo
                    or intent.get("pr") != pr
                    or intent.get("expected_head") != expected_head
                ):
                    raise Pending(
                        "retained exact-operation barrier attempt identity differs "
                        "from the requested repository/PR/head"
                    )
                if rows[-1]["event_type"] == "failure":
                    raise Pending(
                        "retained exact-operation barrier points at a terminally "
                        "refused attempt; mutation history is inconsistent"
                    )
                if not any(row["event_type"] == "merge_requested" for row in rows):
                    raise Pending(
                        "retained exact-operation barrier has no durable merge request; "
                        "mutation remains quarantined"
                    )
                call_history = self._call_history(rows)
                observed_last_call = (
                    None if not call_history else str(call_history[-1][0]["call_id"])
                )
                if (
                    len(call_history) != adopted_barrier.call_count
                    or observed_last_call != adopted_barrier.last_call_id
                ):
                    raise Pending(
                        "retained exact-operation barrier call high-water differs "
                        "from durable event history; mutation remains quarantined"
                    )
                recovered = True
            else:
                rows = self._latest_matching_attempt(events, repo, pr, expected_head)
                recovered = rows is not None
            if rows is None or rows[-1]["event_type"] == "failure":
                intent = self._new_intent(repo, pr, expected_head, actor)
                rows = [intent]
                recovered = False
            result = self._continue_attempt(
                rows,
                actor,
                timeout,
                poll_seconds,
                recovered=recovered,
            )
            # Success means both replay verification and durable obligation
            # launch are complete. Clear only at this final boundary; a crash
            # or any exception before it leaves Rust's exact-operation barrier
            # retained for recovery.
            self.mutation_barrier.clear(
                actor=actor,
                repo=repo,
                pr=pr,
                operation=expected_head,
                attempt_id=result.attempt_id,
            )
            return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-head", required=True, dest="expected_head")
    parser.add_argument(
        "--checkout", type=Path, default=ROOT / "worktrees/lander/hermit"
    )
    parser.add_argument("--actor", default="hermit-lander")
    parser.add_argument("--merge-timeout", type=float, default=180.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--json", action="store_true")
    # Control-flow only.  Possession of this flag grants nothing: the inner
    # process must still pass the canonical process-bound lock verifier.
    parser.add_argument("--lock-child", action="store_true", help=argparse.SUPPRESS)
    return parser


def assert_canonical_lock_child(
    runner: Runner, *, actor: str, repo: str, pr: int, operation: str
) -> MutationBarrierBinding | None:
    command = [
        str(ROOT / "ci-hub/ci-hub"),
        "land-lock",
        "assert-child",
        "--agent",
        actor,
        "--repo",
        repo,
        "--pr",
        str(pr),
        "--operation",
        operation,
        "--child-pid",
        str(os.getpid()),
    ]
    response = runner.run(command, cwd=ROOT, timeout=30.0)
    expected = (
        f"LOCK_CHILD_VERIFIED agent={actor} repo={repo} pr={pr} "
        f"operation={operation} "
    )
    line = response.stdout.strip()
    if response.returncode != 0 or not line.startswith(expected):
        detail = (response.stderr or response.stdout).strip()[:1024]
        raise Refused(
            "canonical landing-lock child assertion failed"
            + (f": {detail}" if detail else "")
        )
    try:
        fields = dict(token.split("=", 1) for token in line.split()[1:])
        pending_mutation = fields["pending_mutation"]
        pending_attempt = fields["pending_attempt"]
        pending_call_count = fields["pending_call_count"]
        pending_call_id = fields["pending_call_id"]
    except (KeyError, ValueError) as error:
        raise Refused(
            "canonical landing-lock child assertion omitted mutation-attempt binding"
        ) from error
    if (
        pending_mutation == "-"
        and pending_attempt == "-"
        and pending_call_count == "-"
        and pending_call_id == "-"
    ):
        return None
    if (
        pending_mutation != operation
        or re.fullmatch(r"[0-9a-f]{32}", pending_attempt) is None
    ):
        raise Refused(
            "canonical landing-lock child assertion carries a mismatched mutation attempt"
        )
    try:
        call_count = int(pending_call_count)
    except ValueError as error:
        raise Refused(
            "canonical landing-lock child assertion carries an invalid call count"
        ) from error
    if call_count < 0 or (
        (call_count == 0 and pending_call_id != "-")
        or (call_count > 0 and re.fullmatch(r"[0-9a-f]{32}", pending_call_id) is None)
    ):
        raise Refused(
            "canonical landing-lock child assertion carries an invalid call high-water"
        )
    return MutationBarrierBinding(
        attempt_id=pending_attempt,
        call_count=call_count,
        last_call_id=None if call_count == 0 else pending_call_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    original_argv = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(original_argv)
    if not args.lock_child:
        environment = dict(os.environ)
        # The safe executor always uses the fleet's canonical lock.  The
        # generic override remains available to land-lock's isolated tests but
        # cannot redirect this authorization to an attacker-chosen file.
        environment.pop(LAND_LOCK_OVERRIDE, None)
        environment.pop(LAND_STORE_OVERRIDE, None)
        environment.pop(OBLIGATION_STORE_OVERRIDE, None)
        environment.pop(CI_HUB_PARSE_ONLY, None)
        command = [
            str(ROOT / "ci-hub/ci-hub"),
            "land-lock",
            "run",
            "--agent",
            args.actor,
            "--repo",
            args.repo,
            "--pr",
            str(args.pr),
            "--operation",
            args.expected_head,
            "--",
            sys.executable,
            str(Path(__file__).resolve()),
            *original_argv,
            "--lock-child",
        ]
        try:
            os.execve(command[0], command, environment)
        except OSError as error:
            print(
                f"safe-exact-head-land: cannot enter land lock: {error}",
                file=sys.stderr,
            )
            return EXIT_ERROR
    # A forged hidden flag or a nested attacker-selected lock cannot authorize
    # the landing.  Re-resolve the canonical lock before asking its sole
    # process/identity verifier.
    os.environ.pop(LAND_LOCK_OVERRIDE, None)
    os.environ.pop(LAND_STORE_OVERRIDE, None)
    os.environ.pop(OBLIGATION_STORE_OVERRIDE, None)
    os.environ.pop(CI_HUB_PARSE_ONLY, None)
    runner = SubprocessRunner()
    try:
        adopted_barrier = assert_canonical_lock_child(
            runner,
            actor=args.actor,
            repo=args.repo,
            pr=args.pr,
            operation=args.expected_head,
        )
        repository = GitRepository(runner, args.checkout.expanduser().resolve())
        executor = LandingExecutor(
            github=GitHubClient(runner),
            repository=repository,
            receipt_authority=CanonicalValidationAuthority(runner),
            mutation_barrier=CanonicalMutationBarrier(runner),
            armer=CanonicalObligationArmer(
                runner,
                repository.checkout,
                obligation_store=CANONICAL_OBLIGATION_STORE,
            ),
            store=EventStore(CANONICAL_LANDING_STORE),
        )
        result = executor.run(
            repo=args.repo,
            pr=args.pr,
            expected_head=args.expected_head,
            actor=args.actor,
            timeout=args.merge_timeout,
            poll_seconds=args.poll_seconds,
            adopted_barrier=adopted_barrier,
        )
    except LandingError as error:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "verdict": error.__class__.__name__.upper(),
            "exit_code": error.exit_code,
            "reason": str(error),
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                f"safe-exact-head-land: {payload['verdict']}: {error}",
                file=sys.stderr,
            )
        return error.exit_code
    payload = result.as_json()
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            "LANDED_AND_ARMED "
            f"{result.repo}#{result.pr} X={result.expected_head} "
            f"Y={result.replay_base} MC={result.merge_commit} "
            f"green_class={result.green_class} "
            f"obligation={result.obligation_id} attempt={result.attempt_id}"
        )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
