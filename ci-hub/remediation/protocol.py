#!/usr/bin/env python3
"""Arm and watch mandatory verification after a speculative/admin land."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci-hub"))
sys.path.insert(0, str(ROOT / "ci-hub/history"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import obligations
from check_outcome import CheckOutcome, classify_check, select_latest_workflow_attempts
from check_outcome import FAIL_CONCLUSIONS as _RED_CONCLUSIONS
from nonzero_result import is_zero_test_green

DEFAULT_REPO = "rrnewton/hermit"
DEFAULT_WORKFLOW = "CI (GitHub-managed portable)"
DEFAULT_WORKFLOW_FILE = "ci-portable.yml"
DEFAULT_POLL_SECONDS = 15
DEFAULT_GITHUB_WAIT_SECONDS = 120
DEFAULT_NETWORK_TIMEOUT = float(
    os.environ.get("CI_HUB_REMEDIATION_NETWORK_TIMEOUT", "120")
)
TERMINAL_VERIFICATION_STATES = frozenset(("green", "red", "error"))
# Only a genuine failing ANSWER remediates. A no_result (a cancelled hosted run,
# an OOM-killed local validate, a lost runner, a network error reaching GitHub)
# is the ABSENCE of an answer, never a failing one: it is a hole to RE-DISPATCH,
# never a red to revert on. Misreading a no_result as red once put an automated
# revert of a healthy main tip one step away (task
# obligation-path-must-consume-no-result-taxonomy).
REMEDIATION_STATES = frozenset(("red",))
# A local validate exit code is not a truth value. Exit 0 is the only passing
# answer; a validate process the ENVIRONMENT killed — OOM/SIGKILL (137),
# SIGTERM (143), or any signal (a negative subprocess returncode) — never
# delivered a verdict at all. That is a no_result, exactly like a cancelled
# hosted run.
_LOCAL_INFRA_EXITS = frozenset((137, 143))
DEFAULT_LOCAL_REDISPATCH_LIMIT = int(
    os.environ.get("CI_HUB_LOCAL_REDISPATCH_LIMIT", "2")
)

# A clean nonzero exit is NOT automatically a failing answer. Tonight three
# separate ENVIRONMENTAL failures — a sandbox EPERM on a re-validate, a
# BpfJailer denial of a `.o.d` write inside a DAG step, and a cold-build linker
# flake with zero tests run — each exited clean-nonzero and read as a product
# RED, and each was one automated step from reverting a healthy tip. The
# genuine failure was in the HARNESS, not the landed code.
#
# THE DERIVED DISCRIMINATOR (task cancellation_taxonomy_distinguish_self). The
# earlier attempt enumerated environmental root causes and defaulted every other
# nonzero exit to RED. That is precisely "a hardcoded list of a growing set":
# each NEW environmental wording we had not yet listed fell through to red and
# became one automated step from reverting a healthy tip — the exact failure
# mode that bit us five times tonight. So we DERIVE instead of enumerate, and we
# put the UNKNOWN on the safe side:
#
#   only a genuine, product-attributable failing TEST VERDICT makes the LOCAL
#   leg red. Every other nonzero exit — a build/link error, a sandbox denial, a
#   proxy drop, a disk-full, or an unrecognised failure we have never seen — is
#   a no_result: re-dispatch and (if it reproduces) fix the box, never revert
#   the tip on a local leg alone.
#
# This is sound, not merely cautious: a speculative land already COMPILED and
# TESTED the tip before arming, so a nonzero re-validate that is NOT a fresh
# failing test is overwhelmingly the environment (cold cache, sandbox, flake),
# not a regression in the landed code. And a real regression the environment did
# NOT cause surfaces on the GitHub leg too, which is authoritative and reverts on
# its own. The only "list" that remains is _TEST_FAILURE_MARKERS, and it lives
# in the SAFE direction: a marker we forget only downgrades a would-be red to a
# re-dispatch (recoverable), it can never manufacture a revert. _INFRA_SIGNATURE
# is retained ONLY to LABEL which box to fix; it no longer decides red vs
# no_result, so a missing category costs a precise log line, never a false red.
_TEST_FAILURE_MARKERS = (
    "test result: failed",  # cargo: printed only when >0 tests failed
    "error: test failed",  # cargo test-harness wrapper on a failing suite
    "failures:",  # cargo lists the failing test names under this header
    "tests failed",
    "assertion failed",
    "assertionerror",
    "panicked at",  # a rust test panic
)
_TEST_FAILURE_COUNT_RE = re.compile(r"\b([1-9]\d*)\s+failed\b")  # pytest "3 failed"
# A BUILD-PHASE failure — a cargo build-script panic or a "failed to run custom
# build command" — is NOT a product test verdict, even though the DAG runner
# renders it with the very same "N failed" / "panicked at" vocabulary a failing
# test uses. It is a hole to re-dispatch (a cold-toolchain build-script flake
# reproduces every cold run), never a tip to revert. It is recognised FIRST, ahead
# of the test-verdict markers below, so that shared vocabulary can never
# manufacture a false red. Incident: a reverie-dbi/build.rs:339 cold-build panic,
# rendered "0 passed, 1 failed ... panicked at .../build.rs", was read as a
# test-failure and armed a revert of a healthy tip (obligation
# 20260804-025419-0f891e43). A build.rs panic means the crate never built, so no
# product test in that node could have run; a genuine regression the build did not
# cause still reverts via the authoritative GitHub leg, which compiles cleanly.
_BUILD_SCRIPT_FAILURE_MARKERS = (
    "failed to run custom build command for",  # cargo build-script failure line
)
_BUILD_SCRIPT_PANIC_RE = re.compile(r"panicked at [^\n]*build\.rs")
# An inner-MemoryMax OOM inside a boxed DAG node — the safe-ci-dag-runner reaped a
# step because it crossed its cgroup memory.max — is an ENVIRONMENTAL cap breach,
# not a product test verdict, even when its victim happens to be a test process and
# the node then surfaces "N failed"/"panicked at". It reproduces every run at the
# same cap, so it is a hole to re-dispatch (and fix-forward by RAISING the cap),
# never a tip to revert. It is recognised BEFORE the build-script and test-verdict
# markers so neither shared vocabulary can manufacture a false red, and because
# "raise this node's cap" is the actionable label even when the OOM also panicked a
# build.rs. BLAST-RADIUS CAVEAT (see the task note): the runner does not set
# memory.oom.group, so the kernel may kill an innocent process INSIDE the breaching
# node rather than the allocator that crossed the cap — attribution is sound at
# DAG-NODE granularity, NOT per-process. Do not blame the named victim; the reason
# points at the node's cap, which is the correct fix. Exact runner strings:
# model.rs `OOM-KILLED (hit inner MemoryMax; N oom_kill event(s))` and scheduler.rs
# `MEMORY CAP HIT: OOM-killed at its inner cgroup MemoryMax`.
_INNER_MEMORYMAX_OOM_MARKERS = (
    "oom-killed (hit inner memorymax",  # model.rs step reason
    "oom-killed at its inner cgroup memorymax",  # scheduler.rs cap-hit banner
)
# Diagnostic-only labels for a no_result. Grouped by CAUSE, but NON-load-bearing:
# they name which box to fix in the log, they do NOT gate red vs no_result, so
# this set can be incomplete without ever producing a false red.
_INFRA_SIGNATURE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sandbox-denied",  # 3pai BpfJailer / seccomp / permission denial
        (
            "operation not permitted",
            "permission denied",
            "eperm",
            "eacces",
            "bpfjailer",
            "blocked by seccomp",
            "seccomp",
        ),
    ),
    (
        "network-proxy",  # the required proxy / network dropped mid-fetch
        (
            "could not resolve host",
            "connection reset by peer",
            "connection timed out",
            "failed to connect",
            "proxyconnect",
            "temporary failure in name resolution",
            "network is unreachable",
        ),
    ),
    (
        "disk-exhausted",  # ran out of disk / inodes / memory under load
        (
            "no space left on device",
            "disk quota exceeded",
            "cannot allocate memory",
        ),
    ),
    (
        "cold-build-flake",  # a cold-toolchain link/archive race, zero tests run
        (
            "collect2: error",
            "ld returned 1 exit status",
            "ld: cannot",
            "error adding symbols",
            "clang: error: unable to execute command",
        ),
    ),
)


def _has_test_failures(output: str) -> bool:
    """Whether the validate output shows at least one genuine test failure.

    Over-inclusive on purpose: a false positive here only forces a genuine RED
    (revertable), never swallows one, so unusual failure formatting fails safe.
    """
    lowered = output.lower()
    if any(marker in lowered for marker in _TEST_FAILURE_MARKERS):
        return True
    return _TEST_FAILURE_COUNT_RE.search(lowered) is not None


def _infra_signature(output: str) -> str | None:
    """Best-effort ROOT-CAUSE label for a no_result, or None if unrecognised.

    Diagnostic only: it decides nothing. A None just yields a generic
    "non-test-failure" label; it never turns a no_result into a red.
    """
    lowered = output.lower()
    for category, needles in _INFRA_SIGNATURE_CATEGORIES:
        if any(needle in lowered for needle in needles):
            return category
    return None


def _is_inner_memorymax_oom(output: str) -> bool:
    """Whether a boxed DAG node was OOM-reaped at its inner cgroup memory.max.

    A cap breach is environmental (raise the cap), not a product test verdict —
    even when the reaped victim is a test process and the node then renders
    "N failed"/"panicked at". Recognising it keeps that shared vocabulary from
    manufacturing a false red; attribution is sound only at DAG-NODE granularity.
    """
    lowered = output.lower()
    return any(marker in lowered for marker in _INNER_MEMORYMAX_OOM_MARKERS)


def _is_build_phase_failure(output: str) -> bool:
    """Whether the failure originates in the BUILD phase, not a product test.

    A cargo build-script panic (``panicked at .../build.rs``) or a cargo
    "failed to run custom build command" is a build-layer flake, not a test
    verdict — the DAG runner just renders it with the same vocabulary. Recognising
    it here keeps that shared vocabulary from ever manufacturing a false red.
    """
    lowered = output.lower()
    if any(marker in lowered for marker in _BUILD_SCRIPT_FAILURE_MARKERS):
        return True
    return _BUILD_SCRIPT_PANIC_RE.search(lowered) is not None


def _classify_local(exit_code: int, output: str = "") -> tuple[str, str]:
    """Map a local validate (exit_code, output) to (state, human reason).

    The discriminator is DERIVED, not enumerated: only a genuine failing test
    VERDICT is red; every other nonzero exit — build/link error, sandbox denial,
    proxy drop, disk-full, or an unrecognised failure — is a no_result to
    re-dispatch, so an unknown failure mode can never manufacture a revert.
    """
    if exit_code == 0:
        # A clean exit is a PROXY for "the tests passed"; the executed count the
        # run itself printed is the fact. A validate that exited 0 having run a
        # DEMONSTRABLY zero test count (every libtest banner reported 0 — the
        # `--features` gating shape that compiles the target but excludes its
        # tests) is a no-result wearing a success badge, not a pass. It is a hole
        # to RE-DISPATCH (never a red to revert): downgrading here can only turn a
        # provably inert green into a recoverable re-dispatch, never manufacture a
        # revert. Absence of any banner stays green — is_zero_test_green fires only
        # on positive evidence of zero-ness, so an unreadable/banner-less log is
        # never downgraded.
        if is_zero_test_green(output):
            return "no_result", "zero-test-green"
        return "green", "clean exit"
    if exit_code < 0 or exit_code in _LOCAL_INFRA_EXITS:
        return "no_result", f"environment-killed (exit={exit_code})"
    if _is_inner_memorymax_oom(output):
        # A boxed node reaped at its inner cgroup memory.max is an environmental
        # cap breach (fix-forward = raise the cap), NOT a failing test verdict —
        # even when the reaped victim is a test process and the node then rendered
        # "N failed"/"panicked at". Recognised BEFORE build-script/test markers so
        # neither shared vocabulary can manufacture a revert: re-dispatch, never revert.
        return "no_result", "non-test-failure:inner-memorymax-oom"
    if _is_build_phase_failure(output):
        # A build-script panic / "failed to run custom build command" surfaced
        # through the DAG runner's "N failed" / "panicked at" summary is a
        # build-layer flake, NOT a failing test verdict: re-dispatch, never revert.
        return "no_result", "non-test-failure:build-script"
    if _has_test_failures(output):
        return "red", "test-failure"
    # Nonzero, but no product test rendered a failing verdict: the failure came
    # from the build/harness/sandbox layer (or is simply unrecognised). Either
    # way it is a hole to re-dispatch, never a tip to revert. GitHub stays
    # authoritative for a genuine regression the environment did not cause.
    category = _infra_signature(output) or "unclassified"
    return "no_result", f"non-test-failure:{category}"


def _local_state(exit_code: int, output: str = "") -> str:
    return _classify_local(exit_code, output)[0]


def _read_local_output(log_path: Path, offset: int, *, cap: int = 64_000_000) -> str:
    """Best-effort read of this validate run's output for classification.

    The validate subprocess streams to the same log the watcher opened, so the
    output from `offset` (captured just before validate started) to end is this
    run's. A read failure yields "" (no test-failure verdict visible), which
    leaves a nonzero exit as a no_result to re-dispatch — the safe state that
    never manufactures a revert from an output we could not even read.
    """
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(max(0, offset))
            return handle.read(cap)
    except OSError:
        return ""


class ProtocolError(RuntimeError):
    """The dual-verification protocol could not be armed or polled."""


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def estimate_local_validate_cost(
    ledger_path: Path = ROOT / "ignored/validate-run-ledger.jsonl",
) -> dict[str, Any]:
    samples: list[tuple[float, float]] = []
    if ledger_path.exists():
        try:
            with ledger_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        wall = float(record.get("real_seconds") or 0)
                        cpu = float(record.get("user_seconds") or 0) + float(
                            record.get("sys_seconds") or 0
                        )
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if record.get("profile") == "full" and wall > 0 and cpu >= 0:
                        samples.append((wall, cpu))
        except OSError:
            samples = []
    samples = samples[-50:]
    if samples:
        wall = _percentile([x[0] for x in samples], 0.9)
        cpu = _percentile([x[1] for x in samples], 0.9)
        basis = (
            f"derived from p90 of the last {len(samples)} usable successful "
            "full-profile validate ledger row(s), window capped at 50, "
            "mixed host/cache states"
        )
        return {
            "kind": "derived",
            "wall_seconds": round(wall, 3),
            "cpu_seconds": round(cpu, 3),
            "basis": basis,
        }
    return {
        "kind": "unknown",
        "wall_seconds": None,
        "cpu_seconds": None,
        "basis": "not measured: no usable successful full-profile validate ledger rows",
    }


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    capture_output: bool = True,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=True,
            env=dict(env) if env is not None else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ProtocolError(
            f"command timed out after {timeout:.1f}s: {' '.join(command)}"
        ) from error
    except (OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
        else:
            detail = str(error)
        raise ProtocolError(f"command failed: {' '.join(command)}: {detail}") from error


def _fetch_target(source: Path, target: str) -> str:
    target_ref = f"origin/{target}"
    _run(
        (
            "with-proxy",
            "git",
            "-C",
            str(source),
            "fetch",
            "origin",
            f"refs/heads/{target}:refs/remotes/{target_ref}",
        ),
        check=True,
        timeout=DEFAULT_NETWORK_TIMEOUT,
    )
    return target_ref


def _is_target_ancestor(source: Path, sha: str, target_ref: str) -> bool:
    ancestry = _run(
        (
            "git",
            "-C",
            str(source),
            "merge-base",
            "--is-ancestor",
            sha,
            target_ref,
        ),
        check=False,
    )
    if ancestry.returncode not in (0, 1):
        detail = (ancestry.stderr or ancestry.stdout or "").strip()
        raise ProtocolError(f"cannot compare {sha} with fetched {target_ref}: {detail}")
    return ancestry.returncode == 0


def _is_main_ancestor(source: Path, sha: str) -> bool:
    return _is_target_ancestor(source, sha, "origin/main")


def _query_pr_landing(repo: str, pr: int) -> tuple[str, str, str]:
    result = _run(
        (
            "with-proxy",
            "gh",
            "pr",
            "view",
            str(pr),
            "-R",
            repo,
            "--json",
            "state,headRefOid,mergeCommit",
        ),
        check=True,
        timeout=DEFAULT_NETWORK_TIMEOUT,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProtocolError("gh pr view returned invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise ProtocolError("gh pr view returned a non-object")
    state = str(payload.get("state") or "").upper()
    head = str(payload.get("headRefOid") or "").lower()
    merge_commit = payload.get("mergeCommit")
    replay = (
        str(merge_commit.get("oid") or "").lower()
        if isinstance(merge_commit, Mapping)
        else ""
    )
    return state, head, replay


def _resolve_pr_replay_sha(source: Path, repo: str, pr: int) -> tuple[str, str]:
    state, head, replay = _query_pr_landing(repo, pr)
    if state != "MERGED" or not obligations.SHA_RE.fullmatch(replay):
        raise ProtocolError(
            f"{repo}#{pr} has no merged replay SHA (state={state or 'unknown'})"
        )
    if not _is_main_ancestor(source, replay):
        raise ProtocolError(
            f"{repo}#{pr} reports replay SHA {replay}, but it is not reachable "
            "from fetched origin/main (the landing may have been orphaned)"
        )
    return head, replay


def resolve_landed_sha(
    source: Path,
    requested: str,
    *,
    repo: str | None = None,
    pr: int | None = None,
) -> str:
    """Resolve the commit that actually landed, not a pre-rebase PR head.

    In PR-aware mode the PR identity and GitHub's replay SHA are canonical. The
    requested head may have been rewritten by rebase merge and need not exist in
    the local object database.
    """
    if not source.is_dir():
        raise ProtocolError(f"Hermit source checkout is missing: {source}")
    _run(
        ("with-proxy", "git", "-C", str(source), "fetch", "origin", "main"),
        check=True,
        timeout=DEFAULT_NETWORK_TIMEOUT,
    )
    if pr is not None:
        if not repo:
            raise ProtocolError("a repository is required with a PR number")
        _, replay = _resolve_pr_replay_sha(source, repo, pr)
        return replay
    resolved = (
        _run(
            (
                "git",
                "-C",
                str(source),
                "rev-parse",
                "--verify",
                f"{requested}^{{commit}}",
            ),
            check=True,
        )
        .stdout.strip()
        .lower()
    )
    if not obligations.SHA_RE.fullmatch(resolved):
        raise ProtocolError(f"cannot resolve a full commit SHA from {requested!r}")
    if not _is_main_ancestor(source, resolved):
        raise ProtocolError(
            f"{resolved} is not reachable from fetched origin/main. A PR head is "
            "normally rewritten by rebase-merge; pass --pr so the verifier can "
            "resolve and check GitHub's replay SHA instead"
        )
    return resolved


def _print_landing_verdict(
    *,
    state: str,
    rc: int,
    reference: str,
    target_ref: str,
    json_output: bool,
    **details: object,
) -> int:
    payload = {
        "state": state,
        "rc": rc,
        "input": reference,
        "target": target_ref,
        **details,
    }
    if json_output:
        print(json.dumps(payload, sort_keys=True))
    else:
        fields = " ".join(
            f"{key}={value}" for key, value in payload.items() if key != "state"
        )
        print(f"{state.upper().replace('-', '_')} {fields}")
    return rc


def _resolve_raw_sha(source: Path, reference: str) -> str:
    rev_parse = (
        "git",
        "-C",
        str(source),
        "rev-parse",
        "--verify",
        f"{reference}^{{commit}}",
    )
    result = _run(rev_parse, check=False)
    if result.returncode != 0:
        _run(
            (
                "with-proxy",
                "git",
                "-C",
                str(source),
                "fetch",
                "--no-tags",
                "origin",
                reference,
            ),
            check=True,
            timeout=DEFAULT_NETWORK_TIMEOUT,
        )
        result = _run(rev_parse, check=True)
    commit = result.stdout.strip().lower()
    if not obligations.SHA_RE.fullmatch(commit):
        raise ProtocolError(f"cannot resolve full commit SHA from {reference!r}")
    return commit


def verify_landing(args: argparse.Namespace) -> int:
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        return _print_landing_verdict(
            state="unverifiable",
            rc=2,
            reference=args.reference,
            target_ref=f"origin/{args.target}",
            json_output=args.json,
            reason=f"source checkout is missing: {source}",
        )

    try:
        target_ref = _fetch_target(source, args.target)
        reference = args.reference.lower()
        if obligations.SHA_RE.fullmatch(reference):
            commit = _resolve_raw_sha(source, reference)
            is_ancestor = _is_target_ancestor(source, commit, target_ref)
            return _print_landing_verdict(
                state="landed" if is_ancestor else "not-landed",
                rc=0 if is_ancestor else 1,
                reference=args.reference,
                target_ref=target_ref,
                json_output=args.json,
                input_kind="sha",
                resolved_sha=commit,
                ancestry="ancestor" if is_ancestor else "not-ancestor",
            )

        if not args.reference.isdecimal() or int(args.reference) <= 0:
            return _print_landing_verdict(
                state="unverifiable",
                rc=2,
                reference=args.reference,
                target_ref=target_ref,
                json_output=args.json,
                reason="input must be a positive PR number or full 40-character SHA",
            )

        pr = int(args.reference)
        pr_state, head, replay = _query_pr_landing(args.repo, pr)
        if pr_state != "MERGED" or not obligations.SHA_RE.fullmatch(replay):
            return _print_landing_verdict(
                state="unverifiable",
                rc=2,
                reference=args.reference,
                target_ref=target_ref,
                json_output=args.json,
                input_kind="pr",
                repo=args.repo,
                pr=pr,
                pr_state=pr_state or "unknown",
                pr_head_sha=head or "unknown",
                reason="no mergeCommit.oid",
            )
        is_ancestor = _is_target_ancestor(source, replay, target_ref)
        return _print_landing_verdict(
            state="landed" if is_ancestor else "not-landed",
            rc=0 if is_ancestor else 1,
            reference=args.reference,
            target_ref=target_ref,
            json_output=args.json,
            input_kind="pr",
            repo=args.repo,
            pr=pr,
            pr_state=pr_state,
            pr_head_sha=head or "unknown",
            resolved_sha=replay,
            ancestry="ancestor" if is_ancestor else "not-ancestor",
        )
    except ProtocolError as error:
        return _print_landing_verdict(
            state="unverifiable",
            rc=2,
            reference=args.reference,
            target_ref=f"origin/{args.target}",
            json_output=args.json,
            reason=str(error),
        )


def _parse_github_runs(output: str, sha: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise ProtocolError("gh run list returned invalid JSON") from error
    if not isinstance(payload, list):
        raise ProtocolError("gh run list returned a non-list payload")
    return select_latest_workflow_attempts(
        payload,
        head_sha=sha,
        workflows=(DEFAULT_WORKFLOW,),
    )


def github_runs(repo: str, sha: str) -> list[dict[str, Any]]:
    result = _run(
        (
            "with-proxy",
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--commit",
            sha,
            "--workflow",
            DEFAULT_WORKFLOW_FILE,
            "--limit",
            "20",
            "--json",
            "databaseId,status,conclusion,createdAt,startedAt,updatedAt,url,event,headSha,workflowName",
        ),
        check=True,
        timeout=DEFAULT_NETWORK_TIMEOUT,
    )
    return _parse_github_runs(result.stdout, sha)


def github_main_sha(repo: str) -> str:
    result = _run(
        ("with-proxy", "gh", "api", f"repos/{repo}/commits/main", "--jq", ".sha"),
        check=True,
        timeout=DEFAULT_NETWORK_TIMEOUT,
    )
    sha = result.stdout.strip().lower()
    if not obligations.SHA_RE.fullmatch(sha):
        raise ProtocolError(f"GitHub returned invalid main SHA {sha!r}")
    return sha


def _github_state(run: Mapping[str, Any]) -> str:
    outcome = classify_check(run.get("status"), run.get("conclusion"))
    if outcome is CheckOutcome.PASSED:
        return "green"
    if outcome is CheckOutcome.FAILED:
        return "red"
    return "no_result"


def _github_patch(run: Mapping[str, Any]) -> dict[str, Any]:
    state = _github_state(run)
    return {
        "github": {
            "state": state,
            "started_at": run.get("startedAt") or run.get("createdAt"),
            "finished_at": (
                run.get("updatedAt") if state in TERMINAL_VERIFICATION_STATES else None
            ),
            "run_ids": [int(run["databaseId"])],
            "urls": [str(run.get("url") or "")],
            "workflow_name": DEFAULT_WORKFLOW,
            "event": run.get("event"),
            "last_poll_error": None,
        }
    }


def _latest_resolved_github_run(
    runs: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the newest run only when that run produced a real verdict.

    ``github_runs`` is sorted newest-first at one exact head. Looking past a
    newest NO_RESULT would revive stale pass/fail evidence and make duplicate
    same-head runs order-dependent.
    """
    if not runs:
        return None
    latest = runs[0]
    return latest if _github_state(latest) in {"green", "red"} else None


def ensure_github_verification(
    obligation_id: str,
    *,
    store_path: Path,
    wait_seconds: int = DEFAULT_GITHUB_WAIT_SECONDS,
    poll_seconds: int = 5,
    allow_dispatch: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    repo, sha = record["repo"], record["landed_sha"]
    deadline = time.monotonic() + wait_seconds
    dispatched = False
    while True:
        runs = github_runs(repo, sha)
        # Classify only the newest exact-head run. A cancelled/skipped/stale run
        # is a HOLE, not permission to fall through to an older opposite answer.
        usable = _latest_resolved_github_run(runs)
        if usable is not None:
            obligations.transition(
                obligation_id, "github-observed", _github_patch(usable), store_path
            )
            return evaluate_obligation(obligation_id, store_path=store_path)
        if time.monotonic() >= deadline:
            break
        if (
            allow_dispatch
            and not dispatched
            and deadline - time.monotonic() <= max(0, wait_seconds - 30)
        ):
            if github_main_sha(repo) == sha:
                _run(
                    (
                        "with-proxy",
                        "gh",
                        "workflow",
                        "run",
                        DEFAULT_WORKFLOW_FILE,
                        "-R",
                        repo,
                        "--ref",
                        "main",
                    ),
                    check=True,
                    timeout=DEFAULT_NETWORK_TIMEOUT,
                )
                dispatched = True
        sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

    summary = (
        f"no resolved {DEFAULT_WORKFLOW!r} run appeared for exact SHA {sha} within "
        f"{wait_seconds}s (only cancelled/superseded/no-result runs, if any)"
    )
    # A missing or unresolved GitHub result is NOT a failure: it leaves the leg in
    # no_result so a locally-green land is never reverted purely because its hosted
    # run was throttled/cancelled. A later poll or the re-dispatched run completes
    # verification; a genuine local red still alarms via the local leg.
    obligations.transition(
        obligation_id,
        "github-no-result",
        {
            "github": {
                "state": "no_result",
                "finished_at": None,
                "last_poll_error": summary,
            }
        },
        store_path,
    )
    return evaluate_obligation(obligation_id, store_path=store_path)


def _failure_details(record: Mapping[str, Any]) -> tuple[str, str]:
    failed: list[tuple[str, Mapping[str, Any]]] = []
    for source in ("local", "github"):
        verification = record[source]
        if verification.get("state") in REMEDIATION_STATES:
            failed.append((source, verification))
    if not failed:
        raise ProtocolError("failure details requested for a non-failing obligation")
    failed.sort(key=lambda pair: str(pair[1].get("finished_at") or ""))
    source, verification = failed[0]
    if source == "local":
        summary = (
            f"local validate state={verification.get('state')} "
            f"exit={verification.get('exit_code')} log={verification.get('log_path')}"
        )
    else:
        summary = (
            f"GitHub {verification.get('workflow_name') or DEFAULT_WORKFLOW} "
            f"state={verification.get('state')} urls={','.join(verification.get('urls') or [])}"
        )
    return source, summary


def remediation_recommendation(
    record: Mapping[str, Any], main_sha: str | None
) -> dict[str, str]:
    if main_sha == record["landed_sha"] or main_sha is None:
        return {
            "action": "revert",
            "reason": (
                "the failing speculative land is still the main tip; revert immediately to "
                "restore the last green base, then fix forward on a reviewed branch"
                if main_sha is not None
                else "main-tip identity is unavailable; conservatively prepare an immediate revert"
            ),
        }
    return {
        "action": "fix-forward",
        "reason": (
            f"main has advanced to {main_sha}; repair the current tip immediately rather than "
            "blindly reverting through later lands"
        ),
    }


def _legs_disagree(record: Mapping[str, Any]) -> bool:
    """A local red beside a GREEN GitHub leg for the same SHA.

    This is a THIRD outcome, neither a confirmed regression nor a clean pass: the
    authoritative hosted verifier PASSED the exact SHA the local verifier failed.
    It is never an auto-revert (the local leg alone cannot overrule a green
    authoritative leg); it is surfaced for a human to investigate — a genuine
    local-only regression, or a local-environment artifact the hosted leg avoided.
    """
    return record["local"]["state"] == "red" and record["github"]["state"] == "green"


def _local_red_uncorroborated(record: Mapping[str, Any]) -> bool:
    """A local red whose authoritative GitHub leg NEVER reported (no_result).

    Under admission-limited hosted CI, github == "no_result" (never admitted,
    cancelled below the concurrency cap, or superseded) is the COMMON case, not a
    rare hole. A local red beside it — even after the whole re-dispatch budget is
    spent, since a load/environment-dependent flake reproduces on the same
    loaded/cold box — has NO authoritative corroboration. It is therefore surfaced
    for a human to investigate (a genuine local-only regression, or an
    environmental/load artifact the hosted leg would have avoided), NEVER an
    auto-revert of a landed tip. This is the actuator-side residual of the
    no_result taxonomy: the classifier already keeps no_result distinct from red,
    and here the revert PATH refuses to act on a lone local signal.

    Gated on the spent re-dispatch budget so a still-re-dispatching local red
    (redispatch_count < limit) keeps re-validating rather than immediately
    raising an investigation alert.
    """
    return (
        record["local"]["state"] == "red"
        and record["github"]["state"] == "no_result"
        and int(record["local"].get("redispatch_count") or 0)
        >= DEFAULT_LOCAL_REDISPATCH_LIMIT
    )


def _remediation_ready(record: Mapping[str, Any]) -> bool:
    """Whether a failing answer is CONFIRMED enough to revert a landed tip.

    Sequencing comes first: never arm a revert while EITHER verifier is still
    ``running``. A leg in flight may be the tool's OWN exoneration arriving — the
    incident that armed ``action=revert`` on a local red while the GitHub verify
    run (30873193855) was still executing (obligation 20260804-025419-0f891e43).
    A revert decision is never made on a partial picture. (``pending`` — a leg not
    yet dispatched — does not block the authoritative GitHub-red path below, which
    is the intended safe revert; only an in-flight ``running`` answer is waited on.)

    Once both legs have reported, ONLY an authoritative GitHub red arms a revert —
    hosted CI does not flake the way a loaded local box does, and cancelled/absent
    is already no_result — so it remediates at once. A local red is only
    PROVISIONAL and NEVER reverts alone: a single (or even re-dispatch-reproduced)
    local validate that failed on a loaded/cold box is a candidate flake, not an
    authoritative regression. Beside a GREEN hosted leg it is a DISAGREEMENT
    (see ``_legs_disagree``); beside a NO_RESULT hosted leg — the COMMON case
    under admission-limited hosted CI, where the authoritative run was never
    admitted / cancelled below the concurrency cap / superseded — it is an
    UNCORROBORATED local signal (see ``_local_red_uncorroborated``). Either way it
    is surfaced for a human to investigate, never an auto-revert of a landed tip.

    History: reverting a healthy tip on a lone local red is the exact hazard this
    guard forecloses — three such recommendations fired 2026-08-04 (e8a0d8d3,
    0f891e43 x2), all caught by humans reading evidence, not by the actuator. The
    classifiers (_classify_local, _github_state) were already conservative; this is
    the ACTUATOR refusing to act on a lone local signal at all. This is the
    post-mortem's own remedy — a local box cannot overrule the authoritative
    verifier — made mechanical.
    """
    local = record["local"]["state"]
    github = record["github"]["state"]
    if "running" in (local, github):
        return False
    if github == "red":
        return True
    # A local red is never sufficient to revert on its own. github == "green" is
    # a disagreement (_legs_disagree); github == "no_result" is uncorroborated
    # (_local_red_uncorroborated). Both route to investigation_required in
    # evaluate_obligation; neither arms an automated revert.
    return False


def evaluate_obligation(
    obligation_id: str,
    *,
    store_path: Path,
    main_sha: str | None = None,
) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    states = (record["local"]["state"], record["github"]["state"])
    now = obligations.utc_now()
    first_terminal_at = record.get("first_terminal_at")
    if first_terminal_at is None and any(
        state in TERMINAL_VERIFICATION_STATES for state in states
    ):
        first_terminal_at = now

    if _remediation_ready(record):
        source, summary = _failure_details(record)
        if main_sha is None:
            try:
                main_sha = github_main_sha(record["repo"])
            except ProtocolError:
                main_sha = None
        recommendation = remediation_recommendation(record, main_sha)
        raised_now = (
            record.get("overall_state") != "remediation_required"
            or record.get("failure_summary") != summary
        )
        if raised_now:
            record = obligations.transition(
                obligation_id,
                "remediation-required",
                {
                    "overall_state": "remediation_required",
                    "first_terminal_at": first_terminal_at,
                    "failure_source": source,
                    "failure_summary": summary,
                    "recommendation": recommendation,
                    "alert": {"state": "raised", "raised_at": now},
                    "remediation": {
                        "state": "recommended",
                        "kind": recommendation["action"],
                    },
                },
                store_path,
            )
        if raised_now:
            print(
                f"HARD WARNING: obligation {obligation_id} failed: {summary}; "
                f"recommendation={recommendation['action']} ({recommendation['reason']})",
                file=sys.stderr,
                flush=True,
            )
        return trigger_remediation(record, store_path=store_path)

    if states == ("green", "green"):
        if record.get("overall_state") != "satisfied":
            record = obligations.transition(
                obligation_id,
                "satisfied",
                {
                    "overall_state": "satisfied",
                    "first_terminal_at": first_terminal_at,
                    "satisfied_at": now,
                    "remediation": {"state": "not_required"},
                },
                store_path,
            )
        return record

    if _legs_disagree(record):
        summary = (
            "local validate red but authoritative GitHub leg green for the same "
            f"SHA {record['landed_sha']}; NOT reverting — investigate before any "
            "manual revert (genuine local-only regression, or a local-env artifact)"
        )
        if record.get("overall_state") != "investigation_required":
            record = obligations.transition(
                obligation_id,
                "verification-disagreement",
                {
                    "overall_state": "investigation_required",
                    "first_terminal_at": first_terminal_at,
                    "failure_source": "local",
                    "failure_summary": summary,
                    "recommendation": None,
                    "alert": {"state": "raised", "raised_at": now},
                    "remediation": {"state": "not_required"},
                },
                store_path,
            )
            print(
                f"HARD WARNING: obligation {obligation_id} legs DISAGREE: {summary}",
                file=sys.stderr,
                flush=True,
            )
        return record

    if _local_red_uncorroborated(record):
        summary = (
            "local validate red but the authoritative GitHub leg never reported "
            f"(no_result) for SHA {record['landed_sha']} after the re-dispatch "
            "budget was spent; NOT reverting on a lone local signal — investigate "
            "(genuine local-only regression, or a load/environment artifact the "
            "hosted leg was never admitted to corroborate under admission-limited CI)"
        )
        if record.get("overall_state") != "investigation_required":
            record = obligations.transition(
                obligation_id,
                "verification-uncorroborated-local-red",
                {
                    "overall_state": "investigation_required",
                    "first_terminal_at": first_terminal_at,
                    "failure_source": "local",
                    "failure_summary": summary,
                    "recommendation": None,
                    "alert": {"state": "raised", "raised_at": now},
                    "remediation": {"state": "not_required"},
                },
                store_path,
            )
            print(
                f"HARD WARNING: obligation {obligation_id} UNCORROBORATED local "
                f"red: {summary}",
                file=sys.stderr,
                flush=True,
            )
        return record

    if first_terminal_at != record.get("first_terminal_at"):
        record = obligations.transition(
            obligation_id,
            "verification-progress",
            {"first_terminal_at": first_terminal_at},
            store_path,
        )
    return record


def trigger_remediation(
    record: Mapping[str, Any], *, store_path: Path
) -> dict[str, Any]:
    """Persist an idempotent, executable remediation dispatch.

    The ORC heartbeat may send a best-effort wake, but the append-only dispatch
    is the authority. Every fresh lander can discover and acknowledge the same
    record without receiving that wake.
    """
    if record.get("overall_state") != "remediation_required":
        return dict(record)
    remediation = record.get("remediation")
    if isinstance(remediation, Mapping) and remediation.get("state") in {
        "triggered",
        "completed",
    }:
        return dict(record)
    recommendation = record.get("recommendation")
    action = (
        recommendation.get("action") if isinstance(recommendation, Mapping) else None
    )
    if action not in {"fix-forward", "revert"}:
        raise ProtocolError("cannot trigger remediation without a concrete action")
    now = obligations.utc_now()
    instruction = (
        f"Act immediately on obligation {record['obligation_id']}: {action} "
        f"{record['repo']}@{record['landed_sha']}. Failure: "
        f"{record.get('failure_summary') or 'see obligation record'}. "
        "After the repair lands, run resolve-obligation with its full SHA."
    )
    triggered = obligations.transition(
        str(record["obligation_id"]),
        "remediation-triggered",
        {
            "alert": {
                "state": "pending",
                "raised_at": now,
                "target": "hermit-lander",
            },
            "remediation": {
                "state": "triggered",
                "kind": action,
                "started_at": now,
                "dispatch": {
                    "state": "pending",
                    "target": "hermit-lander",
                    "requested_at": now,
                    "instruction": instruction,
                    "wake_attempt": 0,
                    "wake_id": None,
                    "wake_sent_at": None,
                    "acknowledged_at": None,
                    "acknowledged_by": None,
                    "acknowledged_session": None,
                },
            },
        },
        store_path,
    )
    print(
        f"REMEDIATION TRIGGERED: obligation={record['obligation_id']} "
        f"action={action} target=hermit-lander",
        file=sys.stderr,
        flush=True,
    )
    return triggered


def actionable_records(store_path: Path) -> list[dict[str, Any]]:
    """Return every remediation still owed, independent of notification state."""
    return [
        record
        for record in obligations.unresolved_records(store_path)
        if record.get("overall_state") == "remediation_required"
    ]


def record_wake_sent(
    *, store_path: Path, target: str, source: str
) -> list[dict[str, Any]]:
    """Record that notification was attempted, not that anybody handled it."""
    now = obligations.utc_now()
    wake_id = uuid.uuid4().hex
    updated: list[dict[str, Any]] = []
    for record in actionable_records(store_path):
        remediation = record.get("remediation") or {}
        dispatch = remediation.get("dispatch") or {}
        attempt = int(dispatch.get("wake_attempt") or 0) + 1
        already_acknowledged = dispatch.get("state") == "acknowledged"
        alert_patch = (
            {
                "state": "handled",
                "target": target,
                "wake_id": wake_id,
                "wake_sent_at": now,
            }
            if already_acknowledged
            else {
                "state": "sent_unacknowledged",
                "target": target,
                "wake_id": wake_id,
                "wake_sent_at": now,
            }
        )
        dispatch_patch: dict[str, Any] = {
            "state": "acknowledged" if already_acknowledged else "sent_unacknowledged",
            "target": target,
            "source": source,
            "wake_attempt": attempt,
            "wake_id": wake_id,
            "wake_sent_at": now,
        }
        if not already_acknowledged:
            dispatch_patch.update(
                acknowledged_at=None,
                acknowledged_by=None,
                acknowledged_session=None,
            )
        updated.append(
            obligations.transition(
                record["obligation_id"],
                "remediation-wake-sent",
                {
                    "alert": alert_patch,
                    "remediation": {"dispatch": dispatch_patch},
                },
                store_path,
            )
        )
    unacknowledged = sum(
        ((record.get("remediation") or {}).get("dispatch") or {}).get("state")
        == "sent_unacknowledged"
        for record in updated
    )
    print(
        f"WAKE RECORDED: wake_id={wake_id} target={target} count={len(updated)} "
        f"unacknowledged={unacknowledged} ids="
        + (",".join(record["obligation_id"] for record in updated) or "none")
    )
    return updated


def inherit_actionable(
    *, store_path: Path, agent: str, session: str
) -> list[dict[str, Any]]:
    """Let a fresh reader discover and acknowledge all inherited remediation."""
    now = obligations.utc_now()
    inherited: list[dict[str, Any]] = []
    for record in actionable_records(store_path):
        remediation = record.get("remediation") or {}
        dispatch = remediation.get("dispatch") or {}
        if (
            dispatch.get("state") == "acknowledged"
            and dispatch.get("acknowledged_by") == agent
            and dispatch.get("acknowledged_session") == session
        ):
            inherited.append(record)
            continue
        inherited.append(
            obligations.transition(
                record["obligation_id"],
                "remediation-inherited",
                {
                    "alert": {
                        "state": "handled",
                        "handled_at": now,
                        "handled_by": agent,
                        "handled_session": session,
                    },
                    "remediation": {
                        "dispatch": {
                            "state": "acknowledged",
                            "acknowledged_at": now,
                            "acknowledged_by": agent,
                            "acknowledged_session": session,
                            "acknowledged_wake_id": dispatch.get("wake_id"),
                        }
                    },
                },
                store_path,
            )
        )
    print(f"INHERITED REMEDIATION OBLIGATIONS: {len(inherited)}")
    for record in inherited:
        dispatch = (record.get("remediation") or {}).get("dispatch") or {}
        print(
            f"  {record['obligation_id']} {record['repo']}@{record['landed_sha']} "
            f"action={(record.get('recommendation') or {}).get('action', '-')}"
        )
        print(f"    {dispatch.get('instruction') or 'inspect the obligation record'}")
    return inherited


def _spawn_detached(arguments: Sequence[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as output:
        process = subprocess.Popen(
            [
                "nohup",
                "setsid",
                "--fork",
                "--wait",
                "--",
                sys.executable,
                str(Path(__file__).resolve()),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def _local_run(obligation_id: str, source: Path, store_path: Path) -> int:
    record = obligations.get_record(obligation_id, store_path)
    workspace = ROOT / "ignored/ci-hub/obligations" / obligation_id
    checkout = workspace / "hermit"
    log_path = Path(record["local"]["log_path"])
    cost = record["local"]["cost"]
    estimate = cost["estimate"]
    cost_path = Path(cost["record_path"])
    started = time.monotonic()
    exit_code = 2
    # A setup that never reaches validate.sh (clone/checkout/submodule failure)
    # is infrastructure, not a product verdict: leave the leg no_result so it is
    # re-dispatched, never reverted on.
    state = "no_result"
    print(
        f"ci-hub obligation={obligation_id} repo={record['repo']} sha={record['landed_sha']} "
        f"started_at={obligations.utc_now()}",
        flush=True,
    )
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # This workspace is obligation-scoped ignored scratch. A re-dispatched
        # local leg (a prior attempt that came back no_result) must be able to
        # reclaim its own checkout; a stale one is not another owner's work.
        if checkout.exists():
            shutil.rmtree(checkout, ignore_errors=True)
        _run(
            ("git", "clone", "--shared", "--no-checkout", str(source), str(checkout)),
            check=True,
            capture_output=False,
        )
        _run(
            ("git", "-C", str(checkout), "checkout", "--detach", record["landed_sha"]),
            check=True,
            capture_output=False,
        )
        _run(
            (
                "with-proxy",
                "git",
                "-C",
                str(checkout),
                "submodule",
                "update",
                "--init",
                "--recursive",
            ),
            check=True,
            capture_output=False,
            timeout=DEFAULT_NETWORK_TIMEOUT,
        )
        actual = _run(
            ("git", "-C", str(checkout), "rev-parse", "HEAD"), check=True
        ).stdout.strip()
        if actual != record["landed_sha"]:
            raise ProtocolError(
                f"isolated checkout is {actual}, expected {record['landed_sha']}"
            )
        environment = os.environ.copy()
        environment.update(
            {
                "CI_HUB_OBLIGATION_ID": obligation_id,
                "HERMIT_VALIDATE_LEDGER": str(
                    ROOT / "ignored/validate-run-ledger.jsonl"
                ),
                "VALIDATE_LABEL_PR": "0",
            }
        )
        estimate_arguments = (
            (
                "--estimate-wall-seconds",
                str(estimate["wall_seconds"]),
                "--estimate-cpu-seconds",
                str(estimate["cpu_seconds"]),
            )
            if estimate["kind"] == "derived"
            else ("--estimate-unknown",)
        )
        # Everything from here streams to the same log the watcher opened, so
        # capture the offset now: the bytes after it are exactly this validate
        # run's output, used to tell a product test failure from an
        # environmental (harness-caused) one.
        log_offset = log_path.stat().st_size if log_path.exists() else 0
        result = _run(
            (
                str(ROOT / "ci-hub/bin/tool-cost"),
                "--tool",
                "speculative-land/local-validate",
                *estimate_arguments,
                "--basis",
                str(estimate["basis"]),
                "--actual-json",
                str(cost_path),
                "--",
                str(checkout / "validate.sh"),
                "--no-label-pr",
            ),
            cwd=checkout,
            check=False,
            capture_output=False,
            env=environment,
        )
        exit_code = result.returncode
        output = _read_local_output(log_path, log_offset)
        state, reason = _classify_local(exit_code, output)
        # Log every classification so an environmental downgrade is auditable and
        # an unattributed zero-test-failure red surfaces a candidate missing
        # signature (task cancellation_taxonomy_distinguish_self).
        print(
            f"ci-hub obligation={obligation_id} local classification: "
            f"state={state} reason={reason} exit={exit_code}",
            flush=True,
        )
        try:
            measured_cost = json.loads(cost_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProtocolError(
                f"tool-cost result is unavailable: {cost_path}: {error}"
            ) from error
        measured_cost["record_path"] = str(cost_path)
    except ProtocolError as error:
        print(f"local verification setup failed: {error}", file=sys.stderr, flush=True)
        measured_cost = cost
    finished_at = obligations.utc_now()
    obligations.transition(
        obligation_id,
        "local-completed",
        {
            "local": {
                "state": state,
                "finished_at": finished_at,
                "exit_code": exit_code,
                "duration_seconds": round(time.monotonic() - started, 3),
                "log_path": str(log_path),
                "cost": measured_cost,
            }
        },
        store_path,
    )
    record = evaluate_obligation(obligation_id, store_path=store_path)
    return 0 if record["local"]["state"] == "green" else 1


def _pid_alive(raw_pid: object) -> bool:
    if not isinstance(raw_pid, int) or raw_pid <= 0:
        return False
    try:
        os.kill(raw_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def poll_obligation(obligation_id: str, store_path: Path) -> dict[str, Any]:
    record = obligations.get_record(obligation_id, store_path)
    if record["overall_state"] in obligations.CLOSED_STATES:
        return record
    if record["github"]["state"] not in TERMINAL_VERIFICATION_STATES:
        try:
            runs = github_runs(record["repo"], record["landed_sha"])
            if runs:
                record = obligations.transition(
                    obligation_id, "github-polled", _github_patch(runs[0]), store_path
                )
        except ProtocolError as error:
            record = obligations.transition(
                obligation_id,
                "github-poll-error",
                {"github": {"last_poll_error": str(error)}},
                store_path,
            )
    if record["local"]["state"] == "running" and not _pid_alive(
        record["local"].get("pid")
    ):
        record = obligations.get_record(obligation_id, store_path)
        if record["local"]["state"] == "running":
            # A runner that vanished never returned a verdict: it is a hole to
            # re-dispatch, never a red to revert on.
            record = obligations.transition(
                obligation_id,
                "local-runner-lost",
                {
                    "local": {
                        "state": "no_result",
                        "finished_at": obligations.utc_now(),
                        "exit_code": 2,
                    }
                },
                store_path,
            )
    record = _maybe_redispatch_local(obligation_id, record, store_path)
    return evaluate_obligation(obligation_id, store_path=store_path)


def _maybe_redispatch_local(
    obligation_id: str, record: Mapping[str, Any], store_path: Path
) -> dict[str, Any]:
    """Re-run the local leg to fill a hole or reproduce a provisional red.

    A no_result (OOM/SIGKILL, a lost runner, a setup failure) is a hole; a lone
    local red not yet corroborated by GitHub is provisional. Both are re-dispatched
    — never reverted on — until the leg resolves green, GitHub corroborates a red,
    or the bounded budget is spent (a spent red is then confirmed by
    ``_remediation_ready``; a spent no_result stays an unresolved hole, still never
    a revert).
    """
    local = record["local"]
    state = local.get("state")
    if state not in {"no_result", "red"}:
        return dict(record)
    if state == "red" and record["github"]["state"] == "red":
        return dict(record)  # GitHub already corroborates the red; let it remediate
    spent = int(local.get("redispatch_count") or 0)
    if spent >= DEFAULT_LOCAL_REDISPATCH_LIMIT:
        return dict(record)
    if _pid_alive(local.get("pid")):
        return dict(record)  # a runner is already producing the next answer
    source = local.get("source")
    if not source or not Path(source).is_dir():
        return dict(record)  # cannot re-run without the donor checkout
    log_path = Path(
        local.get("log_path")
        or ROOT / "ignored/ci-hub/obligations" / obligation_id / "local-validate.log"
    )
    pid = _spawn_detached(
        (
            "_local-run",
            obligation_id,
            "--source",
            str(source),
            "--store",
            str(store_path),
        ),
        log_path,
    )
    reason = (
        "reproduce a provisional local red"
        if state == "red"
        else "fill a local no_result hole"
    )
    print(
        f"LOCAL RE-DISPATCH: obligation={obligation_id} attempt={spent + 1} "
        f"reason={reason} pid={pid}",
        file=sys.stderr,
        flush=True,
    )
    return obligations.transition(
        obligation_id,
        "local-redispatched",
        {
            "local": {
                "state": "running",
                "pid": pid,
                "finished_at": None,
                "redispatch_count": spent + 1,
            }
        },
        store_path,
    )


def _watch_complete(record: Mapping[str, Any]) -> bool:
    if record["overall_state"] in obligations.CLOSED_STATES:
        return True
    return all(
        record[source]["state"] in TERMINAL_VERIFICATION_STATES
        for source in ("local", "github")
    )


def watch(
    *,
    store_path: Path,
    obligation_id: str | None,
    once: bool,
    poll_seconds: int,
) -> int:
    while True:
        records = (
            [obligations.get_record(obligation_id, store_path)]
            if obligation_id
            else obligations.unresolved_records(store_path)
        )
        updated = [
            poll_obligation(record["obligation_id"], store_path) for record in records
        ]
        if once or all(_watch_complete(record) for record in updated):
            remediation = sum(
                record["overall_state"] == "remediation_required" for record in updated
            )
            unresolved = sum(
                record["overall_state"] not in obligations.CLOSED_STATES
                for record in updated
            )
            print(
                f"WATCH OBLIGATIONS: checked={len(updated)} "
                f"unresolved={unresolved} remediation_required={remediation}"
            )
            for record in updated:
                print(f"  {_summary_line(record)}")
            if any(
                record["overall_state"] == "remediation_required" for record in updated
            ):
                return 2
            return (
                1
                if any(
                    record["overall_state"] not in obligations.CLOSED_STATES
                    for record in updated
                )
                else 0
            )
        time.sleep(poll_seconds)


def _summary_line(record: Mapping[str, Any]) -> str:
    recommendation = record.get("recommendation") or {}
    action = (
        recommendation.get("action", "-")
        if isinstance(recommendation, Mapping)
        else "-"
    )
    remediation = record.get("remediation") or {}
    remediation_state = (
        remediation.get("state", "-") if isinstance(remediation, Mapping) else "-"
    )
    dispatch = remediation.get("dispatch") if isinstance(remediation, Mapping) else None
    dispatch_state = (
        dispatch.get("state", "-") if isinstance(dispatch, Mapping) else "-"
    )
    return (
        f"{record['obligation_id']} {record['repo']}@{record['landed_sha'][:12]} "
        f"overall={record['overall_state']} local={record['local']['state']} "
        f"github={record['github']['state']} recommendation={action} "
        f"remediation={remediation_state} dispatch={dispatch_state}"
    )


def print_status(
    store_path: Path,
    *,
    include_closed: bool,
    json_output: bool,
    gate: bool,
    actionable_only: bool = False,
) -> int:
    records = list(obligations.latest_records(store_path).values())
    if actionable_only:
        records = [
            record
            for record in records
            if record.get("overall_state") == "remediation_required"
        ]
    if not include_closed:
        records = [
            record
            for record in records
            if record["overall_state"] not in obligations.CLOSED_STATES
        ]
    records.sort(key=lambda record: (record["opened_at"], record["obligation_id"]))
    unresolved = [
        record
        for record in records
        if record["overall_state"] not in obligations.CLOSED_STATES
    ]
    remediation = [
        record
        for record in unresolved
        if record["overall_state"] == "remediation_required"
    ]
    triggered = [
        record
        for record in remediation
        if (record.get("remediation") or {}).get("state") == "triggered"
    ]
    sent_unacknowledged = [
        record
        for record in remediation
        if ((record.get("remediation") or {}).get("dispatch") or {}).get("state")
        == "sent_unacknowledged"
    ]
    acknowledged = [
        record
        for record in remediation
        if ((record.get("remediation") or {}).get("dispatch") or {}).get("state")
        == "acknowledged"
    ]
    if json_output:
        print(json.dumps({"obligations": records}, sort_keys=True))
    elif gate:
        state = (
            "remediation-required" if remediation else "open" if unresolved else "clear"
        )
        print(f"state={state}")
        print(f"count={len(unresolved)}")
        print(f"remediation_count={len(remediation)}")
        print(f"triggered_count={len(triggered)}")
        print(f"sent_unacknowledged_count={len(sent_unacknowledged)}")
        print(f"acknowledged_count={len(acknowledged)}")
        print(
            "ids="
            + (",".join(record["obligation_id"] for record in unresolved) or "none")
        )
        print(
            "summary="
            + (
                ";".join(_summary_line(record) for record in unresolved)
                if unresolved
                else "no-open-speculative-land-obligations"
            )
        )
    else:
        heading = (
            "Speculative-land obligations: REMEDIATION REQUIRED"
            if remediation
            else (
                "Speculative-land obligations: OPEN"
                if unresolved
                else "Speculative-land obligations: CLEAR"
            )
        )
        print(heading)
        for record in records:
            print("  " + _summary_line(record))
            if record.get("failure_summary"):
                print(f"    failure: {record['failure_summary']}")
    return 2 if remediation else 1 if unresolved else 0


def arm(args: argparse.Namespace) -> int:
    store_path = args.store.expanduser().resolve()
    source = args.source.expanduser().resolve()
    sha = resolve_landed_sha(source, args.sha, repo=args.repo, pr=args.pr)
    try:
        record = obligations.create_obligation(
            repo=args.repo,
            landed_sha=sha,
            land_mode=args.land_mode,
            verification_scope="total",
            actor=args.actor,
            path=store_path,
        )
    except obligations.DuplicateOpenObligation as error:
        record = error.record
        print(str(error), file=sys.stderr)
        return print_status(
            store_path, include_closed=False, json_output=False, gate=False
        )

    obligation_id = record["obligation_id"]
    workspace = ROOT / "ignored/ci-hub/obligations" / obligation_id
    local_log = workspace / "local-validate.log"
    local_cost = workspace / "local-validate-cost.json"
    watcher_log = workspace / "watcher.log"
    cost_estimate = estimate_local_validate_cost()
    obligations.transition(
        obligation_id,
        "local-prepared",
        {
            "local": {
                "state": "starting",
                "started_at": obligations.utc_now(),
                "log_path": str(local_log),
                "workspace": str(workspace / "hermit"),
                # Persisted so a watcher poll can re-dispatch the local leg (fill a
                # no_result hole / reproduce a provisional red) without the donor
                # checkout being re-supplied. redispatch_count bounds those re-runs.
                "source": str(source),
                "redispatch_count": 0,
                "cost": {
                    "estimate": cost_estimate,
                    "actual": None,
                    "record_path": str(local_cost),
                },
            }
        },
        store_path,
    )
    local_pid = _spawn_detached(
        (
            "_local-run",
            obligation_id,
            "--source",
            str(source),
            "--store",
            str(store_path),
        ),
        local_log,
    )
    obligations.transition(
        obligation_id,
        "local-started",
        {
            "local": {
                "state": "running",
                "pid": local_pid,
            }
        },
        store_path,
    )
    github_error: ProtocolError | None = None
    try:
        ensure_github_verification(
            obligation_id,
            store_path=store_path,
            wait_seconds=args.github_wait_seconds,
            allow_dispatch=not args.no_dispatch,
        )
    except ProtocolError as error:
        github_error = error
        # A network/tooling error reaching GitHub is the ABSENCE of a hosted
        # verdict, not a failing one: leave the leg no_result so it is re-polled,
        # never reverted on.
        obligations.transition(
            obligation_id,
            "github-arm-error",
            {
                "github": {
                    "state": "no_result",
                    "finished_at": None,
                    "last_poll_error": str(error),
                }
            },
            store_path,
        )
        evaluate_obligation(obligation_id, store_path=store_path)

    watcher_pid = _spawn_detached(
        (
            "watch",
            "--id",
            obligation_id,
            "--poll-seconds",
            str(args.poll_seconds),
            "--store",
            str(store_path),
        ),
        watcher_log,
    )
    record = obligations.transition(
        obligation_id,
        "watcher-started",
        {
            "watcher": {
                "pid": watcher_pid,
                "log_path": str(watcher_log),
                "started_at": obligations.utc_now(),
            }
        },
        store_path,
    )
    print(f"OPEN OBLIGATION: {obligation_id} {args.repo}@{sha}")
    print(f"  local: pid={local_pid} log={local_log}")
    print(
        "  local estimate: "
        f"wall={cost_estimate['wall_seconds']:.0f}s cpu={cost_estimate['cpu_seconds']:.0f}s "
        f"basis={cost_estimate['basis']}"
    )
    print(
        "  github: "
        f"state={record['github']['state']} runs={','.join(map(str, record['github']['run_ids'])) or 'pending'}"
    )
    print(f"  watcher: pid={watcher_pid} log={watcher_log}")
    return 2 if github_error else 0


def resolve_obligation(args: argparse.Namespace) -> int:
    ref = args.ref.lower()
    if not obligations.SHA_RE.fullmatch(ref):
        raise ProtocolError("--ref must be a full 40-character commit SHA")
    now = obligations.utc_now()
    obligations.transition(
        args.id,
        "remediated",
        {
            "overall_state": "remediated",
            "remediation": {
                "state": "completed",
                "kind": args.kind,
                "ref": ref,
                "started_at": args.started_at or now,
                "completed_at": now,
            },
        },
        args.store,
    )
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    arm_parser = subparsers.add_parser(
        "arm", help="arm dual verification for a landed SHA"
    )
    arm_parser.add_argument("sha")
    arm_parser.add_argument("--repo", default=DEFAULT_REPO)
    arm_parser.add_argument(
        "--pr",
        type=int,
        help="resolve a rebase-merged PR head to GitHub's replay SHA",
    )
    arm_parser.add_argument("--source", type=Path, default=ROOT / "hermit")
    arm_parser.add_argument(
        "--land-mode", choices=("admin", "speculative"), default="speculative"
    )
    arm_parser.add_argument(
        "--actor", default=os.environ.get("AGENT", os.environ.get("USER", "unknown"))
    )
    arm_parser.add_argument(
        "--github-wait-seconds", type=int, default=DEFAULT_GITHUB_WAIT_SECONDS
    )
    arm_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    arm_parser.add_argument("--no-dispatch", action="store_true")
    arm_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    verify_landing_parser = subparsers.add_parser(
        "verify-landing",
        aliases=["verify-landed-pr"],
        help="verify a PR replay SHA or commit SHA against a freshly fetched target",
    )
    verify_landing_parser.add_argument(
        "reference", help="positive PR number or full 40-character commit SHA"
    )
    verify_landing_parser.add_argument("--repo", default=DEFAULT_REPO)
    verify_landing_parser.add_argument("--source", type=Path, default=ROOT / "hermit")
    verify_landing_parser.add_argument("--target", default="main")
    verify_landing_parser.add_argument("--json", action="store_true")

    watch_parser = subparsers.add_parser(
        "watch", help="poll open obligations and record transitions"
    )
    watch_parser.add_argument("--id")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--gate", action="store_true")
    watch_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    watch_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    status_parser = subparsers.add_parser("status", help="show unresolved obligations")
    status_parser.add_argument("--all", action="store_true")
    status_parser.add_argument(
        "--actionable", action="store_true", help="show only remediation still owed"
    )
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--gate", action="store_true")
    status_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    wake_parser = subparsers.add_parser(
        "wake-sent", help="record a best-effort wake as sent but unacknowledged"
    )
    wake_parser.add_argument("--target", required=True)
    wake_parser.add_argument("--source", default="unknown")
    wake_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    inherit_parser = subparsers.add_parser(
        "inherit", help="discover and acknowledge inherited remediation"
    )
    inherit_parser.add_argument("--agent", required=True)
    inherit_parser.add_argument(
        "--session", default=f"{socket.gethostname()}:{os.getpid()}"
    )
    inherit_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    resolve_parser = subparsers.add_parser(
        "resolve", help="record completed remediation"
    )
    resolve_parser.add_argument("id")
    resolve_parser.add_argument(
        "--kind", choices=("fix-forward", "revert"), required=True
    )
    resolve_parser.add_argument("--ref", required=True)
    resolve_parser.add_argument("--started-at")
    resolve_parser.add_argument(
        "--store", type=Path, default=obligations.default_store_path()
    )

    local_parser = subparsers.add_parser("_local-run", help=argparse.SUPPRESS)
    local_parser.add_argument("id")
    local_parser.add_argument("--source", type=Path, required=True)
    local_parser.add_argument("--store", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "arm":
            if args.github_wait_seconds < 0 or args.poll_seconds <= 0:
                raise ProtocolError(
                    "wait must be non-negative and poll interval must be positive"
                )
            return arm(args)
        if args.command in ("verify-landing", "verify-landed-pr"):
            return verify_landing(args)
        if args.command == "watch":
            if args.poll_seconds <= 0:
                raise ProtocolError("--poll-seconds must be positive")
            result = watch(
                store_path=args.store,
                obligation_id=args.id,
                once=args.once,
                poll_seconds=args.poll_seconds,
            )
            if args.gate:
                return print_status(
                    args.store, include_closed=False, json_output=False, gate=True
                )
            return result
        if args.command == "status":
            return print_status(
                args.store,
                include_closed=args.all,
                json_output=args.json,
                gate=args.gate,
                actionable_only=args.actionable,
            )
        if args.command == "wake-sent":
            record_wake_sent(
                store_path=args.store, target=args.target, source=args.source
            )
            return 0
        if args.command == "inherit":
            inherit_actionable(
                store_path=args.store, agent=args.agent, session=args.session
            )
            return 0
        if args.command == "resolve":
            return resolve_obligation(args)
        if args.command == "_local-run":
            return _local_run(args.id, args.source, args.store)
    except (ProtocolError, obligations.StoreError) as error:
        print(f"ci-hub speculative-land: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
