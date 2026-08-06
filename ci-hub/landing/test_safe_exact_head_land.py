#!/usr/bin/env python3
"""Behavioral tests for the no-rewrite exact-head landing executor."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import safe_exact_head_land as land


def sha(number: int) -> str:
    return f"{number:040x}"


S = sha(1)
O = sha(2)
X = sha(3)
W = sha(4)
Y = sha(5)
MC = sha(6)
TREE = sha(7)
C1 = sha(8)
C2 = sha(9)
R1 = sha(10)
R2 = sha(11)


def receipt(sha_value: str, *, count: int = 1) -> land.ReceiptEvidence:
    tree = sha(12)
    finished_at = "2026-08-05T00:00:00Z"
    host = "fixture"
    slot = "lander"
    log_file = "/durable/fixture-full.log"
    report = {
        "schema_version": 1,
        "repo": land.SUPPORTED_REPO,
        "sha": sha_value,
        "verdict": "VALIDATED",
        "exit_code": 0,
        "qualifying_count": count,
        "disqualified_count": 0,
        "ledger": "/durable/validate-run-ledger.jsonl",
        "newest_qualifying": {
            "schema_version": 4,
            "repo": land.SUPPORTED_REPO,
            "sha": sha_value,
            "commit": sha_value,
            "tree": tree,
            "commit_anchored": True,
            "tree_dirty": False,
            "finished_at": finished_at,
            "host": host,
            "profile": "full",
            "selection_mode": "full",
            "result": "pass",
            "raw_result": "pass",
            "exit_code": 0,
            "checks": 1,
            "failures": 0,
            "gates_run": 1,
            "gates_expected": 1,
            "gates": [
                {
                    "name": "fixture full gate",
                    "result": "pass",
                    "exit_code": 0,
                }
            ],
            "executed_tests": 1,
            "filtered_tests": 0,
            "selected_tests": 1,
            "discovered_tests": 1,
            "count_derivation": (
                "selected_tests=executed_tests;"
                "discovered_tests=executed_tests+filtered_tests"
            ),
            "coverage": None,
            "coverage_satisfied": True,
            "coverage_basis": "legacy-schema4-full-gates-and-aggregate-counts",
            "slot": slot,
            "log_file": log_file,
            "receipt_identity": {
                "digest_algorithm": "sha256",
                "canonicalization": "serde_json::to_vec(HistoryRow)-v1",
                "digest": "d" * 64,
                "tuple": {
                    "repo": land.SUPPORTED_REPO,
                    "sha": sha_value,
                    "tree": tree,
                    "finished_at": finished_at,
                    "host": host,
                    "slot": slot,
                    "log_file": log_file,
                },
            },
        },
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    return land.ReceiptEvidence(
        report=report,
        report_sha256=hashlib.sha256(canonical).hexdigest(),
        command=(
            "/fixture/ci-hub",
            "validate-status",
            "--repo",
            land.SUPPORTED_REPO,
            "--sha",
            sha_value,
            "--json",
        ),
    )


class FakeReceiptAuthority:
    def __init__(self, allowed: set[str]):
        self.allowed = allowed
        self.calls: list[str] = []

    def verify(self, expected_head: str) -> land.ReceiptEvidence:
        self.calls.append(expected_head)
        if expected_head not in self.allowed:
            raise land.Refused(f"no exact hard-green receipt for {expected_head}")
        return receipt(expected_head)


class FakeGitHub:
    def __init__(
        self,
        snapshots: list[land.PullRequestSnapshot],
        *,
        commits: tuple[str, ...] = (C1, C2),
        unresolved: int = 0,
        merge_returncode: int = 0,
        merge_payload: dict[str, object] | None = None,
        merge_responses: (
            list[subprocess.CompletedProcess[str] | land.LandingError] | None
        ) = None,
    ):
        self.snapshots = list(snapshots)
        self.last_snapshot = snapshots[-1]
        self.commits = commits
        self.unresolved = unresolved
        self.merge_returncode = merge_returncode
        self.merge_payload = merge_payload or {
            "sha": MC,
            "merged": True,
            "message": "merged",
        }
        self.merge_responses = list(merge_responses or [])
        self.merge_calls = 0

    @staticmethod
    def http_envelope(status: int, payload: dict[str, object]) -> str:
        reason = "OK" if status == 200 else "Unprocessable Entity"
        return (
            f"HTTP/2.0 {status} {reason}\n"
            "content-type: application/json\n\n"
            f"{json.dumps(payload)}\n"
        )

    def snapshot(self, repo: str, pr: int) -> land.PullRequestSnapshot:
        if self.snapshots:
            self.last_snapshot = self.snapshots.pop(0)
        return self.last_snapshot

    def unresolved_review_threads(self, repo: str, pr: int) -> int:
        return self.unresolved

    def pr_commits(self, repo: str, pr: int) -> tuple[str, ...]:
        return self.commits

    def request_rebase_merge(
        self, repo: str, pr: int, expected_head: str
    ) -> subprocess.CompletedProcess[str]:
        self.merge_calls += 1
        if self.merge_responses:
            response = self.merge_responses.pop(0)
            if isinstance(response, land.LandingError):
                raise response
            return response
        return subprocess.CompletedProcess(
            ["gh", "api"],
            self.merge_returncode,
            self.http_envelope(200, self.merge_payload),
            "",
        )


def included_merge_response(
    status: int,
    payload: dict[str, object],
    *,
    returncode: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["gh", "api", "--include"],
        (0 if status == 200 else 1) if returncode is None else returncode,
        FakeGitHub.http_envelope(status, payload),
        "" if status == 200 else f"gh: HTTP {status}",
    )


class FakeRepository:
    def __init__(
        self,
        bases: list[str],
        *,
        observed_base: str = O,
        source_base: str = S,
        base_is_ancestor: bool = False,
        replay_base: str | None = None,
        replay_base_is_ancestor: bool | None = None,
        commit_visibility: list[bool] | None = None,
        replay_error: land.ReplayMismatch | None = None,
    ):
        self.bases = list(bases)
        self.last_base = bases[-1]
        self.observed_base = observed_base
        self.source_base = source_base
        self.base_is_ancestor = base_is_ancestor
        self.replay_base = replay_base or observed_base
        self.replay_base_is_ancestor = (
            base_is_ancestor
            if replay_base_is_ancestor is None
            else replay_base_is_ancestor
        )
        self.commit_visibility = list(commit_visibility or [True])
        self.replay_error = replay_error
        self.fetch_head_calls: list[tuple[int, str]] = []
        self.ensure_calls: list[str] = []

    def ensure_checkout(self, expected_repo: str) -> None:
        self.ensure_calls.append(expected_repo)

    def fetch_base(self) -> str:
        if self.bases:
            self.last_base = self.bases.pop(0)
        return self.last_base

    def fetch_head(self, pr: int, expected_head: str) -> None:
        self.fetch_head_calls.append((pr, expected_head))

    def source_provenance(
        self, expected_head: str, observed_base: str
    ) -> land.SourceProvenance:
        return land.SourceProvenance(
            observed_base=observed_base,
            source_base=self.source_base,
            source_commits=(C1, C2),
            source_tree=TREE,
            observed_base_tree=sha(12),
        )

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        if (ancestor, descendant) == (self.observed_base, X):
            return self.base_is_ancestor
        if ancestor == MC and descendant in {MC, self.last_base}:
            return True
        return True

    def commit_exists(self, revision: str) -> bool:
        if self.commit_visibility:
            value = self.commit_visibility.pop(0)
            if not self.commit_visibility:
                self.commit_visibility.append(value)
            return value
        return True

    def verify_replay(self, **kwargs: object) -> land.ReplayProvenance:
        if self.replay_error is not None:
            raise self.replay_error
        soft = not self.replay_base_is_ancestor
        return land.ReplayProvenance(
            expected_head=X,
            observed_base=O,
            source_base=self.source_base,
            source_commit_count=2,
            replay_base=self.replay_base,
            merge_commit=MC,
            fetched_main=str(kwargs["fetched_main"]),
            composition_merge_base=self.source_base,
            expected_tree=TREE,
            actual_tree=TREE,
            replay_commits=(R1, R2),
            replay_base_is_ancestor_of_source=not soft,
            green_class=land.GREEN_SOFT if soft else land.GREEN_HARD,
            soft_green=land.SOFT_ZERO_CONFLICT if soft else None,
        )


class FakeArmer:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls: list[tuple[str, int, str, str]] = []
        self.verify_calls: list[tuple[str, str, str]] = []
        self.obligations: dict[str, dict[str, object]] = {}

    def arm(
        self, repo: str, pr: int, merge_commit: str, actor: str
    ) -> dict[str, object]:
        self.calls.append((repo, pr, merge_commit, actor))
        if self.failures:
            self.failures -= 1
            raise land.LandingError("planted crash-window arm failure")
        obligation = {
            "obligation_id": "obligation-fixture",
            "overall_state": "OPEN",
            "launch_durable": True,
        }
        self.obligations["obligation-fixture"] = {
            **obligation,
            "repo": repo,
            "landed_sha": merge_commit,
        }
        return obligation

    def verify(
        self, repo: str, merge_commit: str, obligation_id: str
    ) -> dict[str, object]:
        self.verify_calls.append((repo, merge_commit, obligation_id))
        record = self.obligations.get(obligation_id)
        if (
            record is None
            or record.get("repo") != repo
            or record.get("landed_sha") != merge_commit
            or record.get("launch_durable") is not True
        ):
            raise land.LandingError("planted missing or non-durable obligation")
        return dict(record)


class FakeMutationBarrier:
    def __init__(self) -> None:
        self.arms: list[tuple[str, str, int, str, str]] = []
        self.call_bindings: list[tuple[str, str, int, str, str, int, str]] = []
        self.clears: list[tuple[str, str, int, str, str]] = []

    def arm(
        self,
        *,
        actor: str,
        repo: str,
        pr: int,
        operation: str,
        attempt_id: str,
    ) -> None:
        self.arms.append((actor, repo, pr, operation, attempt_id))

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
        self.call_bindings.append(
            (actor, repo, pr, operation, attempt_id, call_count, call_id)
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
        self.clears.append((actor, repo, pr, operation, attempt_id))


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def snap(
    *,
    state: str = "OPEN",
    head: str = X,
    base: str = "main",
    draft: bool = False,
    review: str = "APPROVED",
    merge_commit: str | None = None,
) -> land.PullRequestSnapshot:
    return land.PullRequestSnapshot(
        number=42,
        state=state,
        is_draft=draft,
        head=head,
        base=base,
        review_decision=review,
        merge_commit=merge_commit,
    )


def executor(
    tmp_path: Path,
    github: FakeGitHub,
    repository: FakeRepository,
    receipts: FakeReceiptAuthority,
    armer: FakeArmer | None = None,
) -> tuple[land.LandingExecutor, FakeArmer, land.EventStore, Clock]:
    clock = Clock()
    actual_armer = armer or FakeArmer()
    store = land.EventStore(tmp_path / "landing.jsonl")
    return (
        land.LandingExecutor(
            github=github,
            repository=repository,
            receipt_authority=receipts,
            armer=actual_armer,
            mutation_barrier=FakeMutationBarrier(),
            store=store,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            attempt_id=lambda: "a" * 32,
            event_id=(
                lambda values=iter(f"{n:032x}" for n in range(1, 1000)): next(values)
            ),
            now=lambda: "2026-08-05T00:00:00Z",
        ),
        actual_armer,
        store,
        clock,
    )


def run(ex: land.LandingExecutor, **overrides: object) -> land.LandingResult:
    args: dict[str, object] = {
        "repo": land.SUPPORTED_REPO,
        "pr": 42,
        "expected_head": X,
        "actor": "hermit-lander",
        "timeout": 2.0,
        "poll_seconds": 1.0,
    }
    args.update(overrides)
    return ex.run(**args)  # type: ignore[arg-type]


def forge_inner_receipt_digest(store: land.EventStore, event_types: set[str]) -> None:
    rows = [json.loads(line) for line in store.path.read_text().splitlines()]
    for row in rows:
        if row["event_type"] not in event_types:
            continue
        envelope = row["source_receipt"]
        report = envelope["report"]
        report["newest_qualifying"]["receipt_identity"]["digest"] = "e" * 64
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        envelope["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    store.path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_soft_green_requires_exact_source_and_actual_base_receipts(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O, replay_base_is_ancestor=False)
    auth = FakeReceiptAuthority({X, O})
    ex, armer, store, _ = executor(tmp_path, gh, repo, auth)

    result = run(ex)

    assert result.green_class == land.GREEN_SOFT
    assert result.soft_green == land.SOFT_ZERO_CONFLICT
    assert auth.calls.count(X) >= 3 and auth.calls.count(O) >= 3
    assert armer.calls == [(land.SUPPORTED_REPO, 42, MC, "hermit-lander")]
    verified = [e for e in store.load() if e["event_type"] == "landing_verified"][0]
    assert verified["green_class"] == land.GREEN_SOFT
    assert verified["base_receipt"]["report"]["sha"] == O


def test_hard_green_when_actual_base_is_in_source_lineage(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository(
        [O, O, MC],
        source_base=O,
        base_is_ancestor=True,
        replay_base=O,
        replay_base_is_ancestor=True,
    )
    auth = FakeReceiptAuthority({X})
    ex, _, store, _ = executor(tmp_path, gh, repo, auth)

    result = run(ex)

    assert result.green_class == land.GREEN_HARD
    verified = [e for e in store.load() if e["event_type"] == "landing_verified"][0]
    assert verified["soft_green"] is None and verified["base_receipt"] is None
    assert O not in auth.calls


@pytest.mark.parametrize("planned_hard", [False, True])
def test_actual_y_race_refuses_soft_only_base(
    tmp_path: Path, planned_hard: bool
) -> None:
    source_base = O if planned_hard else S
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository(
        [O, O, MC],
        source_base=source_base,
        base_is_ancestor=planned_hard,
        replay_base=Y,
        replay_base_is_ancestor=False,
    )
    allowed = {X} if planned_hard else {X, O}
    auth = FakeReceiptAuthority(allowed)
    ex, armer, store, _ = executor(tmp_path, gh, repo, auth)

    with pytest.raises(land.Pending, match="awaits exact hard-green"):
        run(ex)

    assert not armer.calls
    events = store.load()
    assert not any(e["event_type"] == "landing_verified" for e in events)
    assert events[-1]["event_type"] == "merge_pending"
    assert events[-1]["reason_code"] == "actual_source_or_base_not_hard_green"
    assert events[-1]["replay_base"] == Y

    auth.allowed.add(Y)
    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [MC]
    result = run(ex)
    assert result.green_class == land.GREEN_SOFT
    assert len(armer.calls) == 1


def test_main_advance_before_request_is_refused_without_merge(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap()])
    repo = FakeRepository([O, W])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="main advanced"):
        run(ex)

    assert gh.merge_calls == 0 and not armer.calls
    assert store.load()[-1]["reason_code"] == "main_advanced_before_merge"


def test_pushed_different_head_is_refused_without_merge(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap(head=W)])
    repo = FakeRepository([O])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="PR head drift"):
        run(ex)

    assert gh.merge_calls == 0 and not armer.calls
    assert store.load()[-1]["event_type"] == "failure"


def test_github_merged_w_instead_of_x_retains_operation_and_never_arms(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub(
        [snap(), snap(), snap(), snap(state="MERGED", head=W, merge_commit=MC)]
    )
    repo = FakeRepository([O, O])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending, match="expected X"):
        run(ex)

    assert gh.merge_calls == 1 and not armer.calls
    pending = store.load()[-1]
    assert pending["reason_code"] == "postrequest_head_changed"
    assert pending["expected_head"] == X


def test_synchronous_success_propagation_timeout_does_not_resubmit(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap()])
    repo = FakeRepository([O, O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    with pytest.raises(land.Pending):
        run(ex, timeout=1.0)
    assert gh.merge_calls == 1
    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert len(barrier.arms) == 1 and barrier.clears == []
    assert store.load()[-1]["event_type"] == "merge_pending"

    gh.snapshots = [snap(), snap(), snap()]
    gh.last_snapshot = snap()
    with pytest.raises(land.Pending):
        run(ex, timeout=1.0)
    assert gh.merge_calls == 1


def test_adopted_barrier_with_missing_attempt_history_never_creates_intent(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending, match="no matching durable landing attempt"):
        run(
            ex,
            adopted_barrier=land.MutationBarrierBinding("b" * 32, 0, None),
        )

    assert gh.merge_calls == 0
    assert not store.path.exists() or store.load() == []


def test_adopted_barrier_refuses_truncated_same_attempt_call_history(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )

    with pytest.raises(land.Pending, match="call high-water differs"):
        run(
            ex,
            adopted_barrier=land.MutationBarrierBinding(
                str(intent["attempt_id"]), 1, "b" * 32
            ),
        )

    assert gh.merge_calls == 0
    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert barrier.clears == []
    assert not any(row["event_type"] == "merge_call_started" for row in store.load())


def test_adopted_zero_call_barrier_resumes_exact_request(tmp_path: Path) -> None:
    gh = FakeGitHub(
        [snap(), snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)]
    )
    repo = FakeRepository([O, O, MC], replay_base=O)
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )

    result = run(
        ex,
        adopted_barrier=land.MutationBarrierBinding(str(intent["attempt_id"]), 0, None),
    )

    assert result.recovered is True and gh.merge_calls == 1
    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert barrier.call_bindings[0][5:] == (1, "3".zfill(32))


def test_synchronous_negative_clears_barrier_before_terminal_refusal(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub(
        [snap(), snap(), snap()],
        merge_payload={"sha": None, "merged": False, "message": "not mergeable"},
    )
    repo = FakeRepository([O, O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="not mergeable"):
        run(ex)

    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    expected = ("hermit-lander", land.SUPPORTED_REPO, 42, X, "a" * 32)
    assert barrier.arms == [expected]
    assert barrier.clears == [expected]
    assert store.load()[-1]["reason_code"] == "synchronous_merge_refused"


@pytest.mark.parametrize("status", [404, 405, 409, 422])
def test_first_complete_definitive_http_negative_clears_barrier(
    tmp_path: Path, status: int
) -> None:
    refusal = included_merge_response(
        status, {"message": "Merge cannot be performed", "status": str(status)}
    )
    gh = FakeGitHub(
        [snap(), snap()],
        merge_responses=[refusal],
    )
    repo = FakeRepository([O, O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="cannot be performed"):
        run(ex)

    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert len(barrier.arms) == 1 and len(barrier.clears) == 1
    response = next(
        row for row in store.load() if row["event_type"] == "merge_response"
    )
    assert response["http_status"] == status
    assert response["definitive_no_mutation"] is True
    assert response["http_envelope"] == refusal.stdout


@pytest.mark.parametrize("status", [404, 405, 409, 422])
def test_ambiguous_call_then_definitive_negative_retains_until_merged_proof(
    tmp_path: Path, status: int
) -> None:
    gh = FakeGitHub(
        [snap(), snap(), snap(), snap()],
        merge_responses=[land.LandingError("transport timed out")],
    )
    repo = FakeRepository([O, O])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending):
        run(ex, timeout=1.0)
    assert gh.merge_calls == 1

    gh.merge_responses = [
        included_merge_response(status, {"message": "already merged", "status": status})
    ]
    gh.snapshots = [snap(), snap()]
    gh.last_snapshot = snap()
    with pytest.raises(land.Pending, match="earlier merge call"):
        run(ex, timeout=1.0)

    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert gh.merge_calls == 2 and barrier.clears == []
    assert store.load()[-1]["reason_code"] == "negative_after_ambiguous_merge_call"

    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [MC]
    result = run(ex)
    assert result.merge_commit == MC
    assert gh.merge_calls == 2 and len(armer.calls) == 1
    assert len(barrier.clears) == 1


def test_orphaned_call_then_409_cannot_clear(tmp_path: Path) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    store.append(
        ex._event(
            intent,
            "merge_call_started",
            call_id="b" * 32,
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
        )
    )
    gh.merge_responses = [
        included_merge_response(409, {"message": "conflict", "status": "409"})
    ]
    gh.snapshots = [snap(), snap()]
    gh.last_snapshot = snap()

    with pytest.raises(land.Pending, match="earlier merge call"):
        run(ex)

    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert barrier.clears == [] and gh.merge_calls == 1


def test_recovery_reparses_definitive_response_before_clear(tmp_path: Path) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    call_id = "b" * 32
    store.append(
        ex._event(
            intent,
            "merge_call_started",
            call_id=call_id,
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
        )
    )
    response = included_merge_response(
        405, {"message": "not mergeable", "status": "405"}
    )
    store.append(
        ex._event(
            intent,
            "merge_response",
            call_id=call_id,
            returncode=1,
            stdout_sha256=hashlib.sha256(response.stdout.encode()).hexdigest(),
            stderr_sha256=hashlib.sha256(response.stderr.encode()).hexdigest(),
            output_excerpt=response.stderr,
            http_envelope=response.stdout,
            http_status=409,
            merged=False,
            response_merge_commit=None,
            response_message="not mergeable",
            definitive_no_mutation=True,
            parse_error=None,
        )
    )
    gh.snapshots = [snap()]
    gh.last_snapshot = snap()

    with pytest.raises(land.StoreError, match="does not match"):
        run(ex)

    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    assert barrier.clears == [] and gh.merge_calls == 0


def test_crash_after_intent_recovers_same_attempt_without_duplicate(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")

    gh.snapshots = [snap(), snap(), snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [O, MC]
    result = run(ex)

    assert result.recovered is True
    assert sum(e["event_type"] == "intent" for e in store.load()) == 1


def test_recovered_intent_refuses_forged_inner_receipt_with_recomputed_outer_hash(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    forge_inner_receipt_digest(store, {"intent"})
    gh.snapshots = [snap()]
    gh.last_snapshot = gh.snapshots[-1]

    with pytest.raises(land.Refused, match="identity digest differs"):
        run(ex)

    assert len(gh.snapshots) == 1
    assert gh.merge_calls == 0 and not armer.calls
    assert store.load()[-1]["reason_code"] == "persisted_receipt_not_live"


def test_recovered_requested_merge_stays_pending_when_persisted_receipt_is_missing(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    receipts = FakeReceiptAuthority({X, O})
    ex, armer, store, _ = executor(tmp_path, gh, repo, receipts)
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    store.append(
        ex._event(
            intent,
            "merge_call_started",
            call_id="b" * 32,
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
        )
    )
    receipts.allowed.remove(X)
    gh.snapshots = [snap()]
    gh.last_snapshot = gh.snapshots[-1]

    with pytest.raises(land.Pending, match="awaits exact persisted receipt"):
        run(ex)

    assert len(gh.snapshots) == 1
    assert gh.merge_calls == 0 and not armer.calls
    pending = store.load()[-1]
    assert pending["event_type"] == "merge_pending"
    assert pending["reason_code"] == "persisted_receipt_not_live"


def test_verified_arm_failure_recovers_without_second_merge(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O)
    armer = FakeArmer(failures=1)
    receipts = FakeReceiptAuthority({X, O})
    ex, _, store, _ = executor(tmp_path, gh, repo, receipts, armer)
    with pytest.raises(land.LandingError, match="arm failure"):
        run(ex)
    assert gh.merge_calls == 1
    assert store.load()[-1]["event_type"] == "arm_failed"

    calls_before_recovery = len(receipts.calls)
    result = run(ex)
    assert result.recovered is True
    assert gh.merge_calls == 1 and len(armer.calls) == 2
    assert receipts.calls[calls_before_recovery:] == [X, O]
    assert store.load()[-1]["event_type"] == "obligation_armed"


def test_terminal_armed_recovery_reverifies_live_obligation_before_clear(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O)
    ex, armer, _, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    first = run(ex)
    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    clears_before = len(barrier.clears)
    merge_calls_before = gh.merge_calls

    recovered = run(ex)

    assert recovered.attempt_id == first.attempt_id
    assert recovered.recovered is True
    assert gh.merge_calls == merge_calls_before
    assert len(armer.calls) == 1
    assert armer.verify_calls[-1] == (
        land.SUPPORTED_REPO,
        MC,
        "obligation-fixture",
    )
    assert len(barrier.clears) == clears_before + 1


def test_terminal_armed_recovery_missing_live_obligation_retains_barrier(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O)
    ex, armer, _, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    run(ex)
    barrier = ex.mutation_barrier
    assert isinstance(barrier, FakeMutationBarrier)
    clears_before = len(barrier.clears)
    armer.obligations.clear()

    with pytest.raises(land.Pending, match="not live in the canonical obligation"):
        run(ex)

    assert len(barrier.clears) == clears_before
    assert gh.merge_calls == 1 and len(armer.calls) == 1


def test_postland_arm_recovery_refuses_forged_inner_receipt_even_if_rehashed(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O)
    armer = FakeArmer(failures=1)
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}), armer)
    with pytest.raises(land.LandingError, match="arm failure"):
        run(ex)
    forge_inner_receipt_digest(store, {"landing_verified", "arm_failed"})

    with pytest.raises(land.Refused, match="identity digest differs"):
        run(ex)

    events = store.load()
    assert gh.merge_calls == 1 and len(armer.calls) == 1
    assert events[-1]["event_type"] == "arm_failed"
    assert not any(row["event_type"] == "obligation_armed" for row in events)


def test_merged_wrong_base_on_recovery_retains_operation(tmp_path: Path) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    gh.snapshots = [snap(state="MERGED", base="release", merge_commit=MC)]

    with pytest.raises(land.Pending, match="not 'main'"):
        run(ex)

    assert store.load()[-1]["reason_code"] == "postrequest_merged_to_wrong_base"


def test_already_merged_after_durable_request_recovers_without_resubmit(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O], replay_base=O)
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    store.append(
        ex._event(
            intent,
            "merge_call_started",
            call_id="b" * 32,
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
        )
    )
    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [MC]

    result = run(ex)

    assert result.recovered is True
    assert gh.merge_calls == 0 and len(armer.calls) == 1
    assert store.load()[-1]["event_type"] == "obligation_armed"


def test_delayed_merge_commit_visibility_retries_then_arms(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, W, MC], replay_base=O, commit_visibility=[False, True])
    ex, armer, _, clock = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    result = run(ex, timeout=3.0)

    assert result.merge_commit == MC and len(armer.calls) == 1
    assert clock.value == 1.0


def test_merged_null_oid_propagates_then_verifies_without_resubmit(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub(
        [
            snap(),
            snap(),
            snap(),
            snap(state="MERGED", merge_commit=None),
            snap(state="MERGED", merge_commit=MC),
        ]
    )
    repo = FakeRepository([O, O, MC], replay_base=O)
    ex, armer, store, clock = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    result = run(ex, timeout=3.0)

    assert result.merge_commit == MC and len(armer.calls) == 1
    assert gh.merge_calls == 1 and clock.value == 1.0
    assert not any(e["event_type"] == "failure" for e in store.load())


def test_recovered_merged_null_oid_times_out_pending_without_resubmit(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O], replay_base=O)
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    store.append(
        ex._event(
            intent,
            "merge_requested",
            merge_method="rebase",
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
            observed_base=O,
        )
    )
    store.append(
        ex._event(
            intent,
            "merge_call_started",
            call_id="b" * 32,
            request_semantics="synchronous-rest-v1",
            expected_head_guard=X,
        )
    )
    null_merged = snap(state="MERGED", merge_commit=None)
    gh.snapshots = [null_merged, null_merged, null_merged]
    gh.last_snapshot = null_merged

    with pytest.raises(land.Pending, match="no mergeCommit.oid"):
        run(ex, timeout=1.0)

    assert gh.merge_calls == 0 and not armer.calls
    events = store.load()
    assert events[-1]["event_type"] == "merge_pending"
    assert events[-1]["reason_code"] == "merge_commit_oid_pending"
    assert not any(e["event_type"] == "failure" for e in events)

    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [MC]
    assert run(ex).merge_commit == MC
    assert gh.merge_calls == 0 and len(armer.calls) == 1


def test_invisible_merge_commit_is_recoverable_pending(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, W], replay_base=O, commit_visibility=[False])
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending, match="not visible"):
        run(ex, timeout=1.0)

    assert not armer.calls
    assert store.load()[-1]["event_type"] == "merge_pending"


def test_replay_mismatch_quarantines_then_recovers_same_attempt(
    tmp_path: Path,
) -> None:
    mismatch = land.ReplayMismatch(
        "planted tree mismatch",
        {
            "expected_head": X,
            "observed_base": O,
            "source_base": S,
            "source_commit_count": 2,
            "replay_base": O,
            "merge_commit": MC,
            "fetched_main": MC,
            "composition_merge_base": S,
            "expected_tree": TREE,
            "actual_tree": W,
        },
    )
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_error=mismatch)
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending, match="quarantined"):
        run(ex)

    assert not armer.calls
    quarantine = store.load()[-1]
    assert quarantine["event_type"] == "landing_quarantined"
    assert quarantine["diagnostics"]["expected_tree"] == TREE
    assert quarantine["diagnostics"]["actual_tree"] == W
    assert gh.merge_calls == 1

    repo.replay_error = None
    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    result = run(ex)

    assert result.recovered is True
    assert gh.merge_calls == 1 and len(armer.calls) == 1
    events = store.load()
    assert sum(row["event_type"] == "intent" for row in events) == 1
    assert events[-1]["event_type"] == "obligation_armed"


def test_persistent_quarantine_never_resubmits_on_open_snapshot_regression(
    tmp_path: Path,
) -> None:
    mismatch = land.ReplayMismatch(
        "persistent replay mismatch",
        {"expected_tree": TREE, "actual_tree": W},
    )
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_error=mismatch)
    ex, armer, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Pending, match="quarantined"):
        run(ex)
    assert gh.merge_calls == 1 and not armer.calls

    gh.snapshots = [snap(), snap(), snap()]
    gh.last_snapshot = snap()
    with pytest.raises(land.Pending):
        run(ex, timeout=1.0)
    assert gh.merge_calls == 1

    gh.snapshots = [snap(state="MERGED", merge_commit=MC)]
    gh.last_snapshot = gh.snapshots[-1]
    repo.bases = [MC]
    with pytest.raises(land.Pending, match="quarantined"):
        run(ex)
    assert gh.merge_calls == 1 and not armer.calls
    assert sum(row["event_type"] == "landing_quarantined" for row in store.load()) == 2


def test_github_commit_list_mismatch_refuses_before_intent(tmp_path: Path) -> None:
    gh = FakeGitHub([snap()], commits=(C2, C1))
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="commit list"):
        run(ex)

    assert not store.path.exists() or not store.load()


def test_forbidden_repo_and_ref_are_refused(tmp_path: Path) -> None:
    gh = FakeGitHub([snap(base="release")])
    repo = FakeRepository([O])
    ex, _, _, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    with pytest.raises(land.Refused, match="unsupported repository"):
        run(ex, repo="facebookexperimental/hermit")
    with pytest.raises(land.Refused, match="forbidden base"):
        run(ex)
    assert gh.merge_calls == 0


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/rrnewton/hermit",
        "https://github.com/rrnewton/hermit.git",
        "ssh://git@github.com/rrnewton/hermit",
        "ssh://git@github.com/rrnewton/hermit.git",
        "git@github.com:rrnewton/hermit",
        "git@github.com:rrnewton/hermit.git",
    ],
)
def test_canonical_github_remote_forms_bind_exact_repo(remote: str) -> None:
    assert land.github_remote_repo(remote) == land.SUPPORTED_REPO


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/rrnewton/hermit-evil.git",
        "https://github.com.evil/rrnewton/hermit.git",
        "https://token@github.com/rrnewton/hermit.git",
        "ssh://root@github.com/rrnewton/hermit.git",
        "ssh://git@github.com:22/rrnewton/hermit.git",
        "git@github.com:rrnewton/hermit.git/extra",
        "file:///tmp/rrnewton/hermit.git",
        "/tmp/rrnewton/hermit.git",
        "https://github.com/rrnewton/hermit.git?lookalike=1",
    ],
)
def test_lookalike_or_noncanonical_remote_is_not_an_identity(remote: str) -> None:
    assert land.github_remote_repo(remote) != land.SUPPORTED_REPO


def test_checkout_origin_is_bound_before_any_github_or_git_evidence(
    tmp_path: Path,
) -> None:
    class WrongOriginRepository(FakeRepository):
        def ensure_checkout(self, expected_repo: str) -> None:
            raise land.Refused("checkout origin identity mismatch")

    gh = FakeGitHub([snap()])
    repo = WrongOriginRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))

    with pytest.raises(land.Refused, match="origin identity mismatch"):
        run(ex)

    assert len(gh.snapshots) == 1
    assert not repo.fetch_head_calls and gh.merge_calls == 0
    assert not store.path.exists() or not store.load()


@pytest.mark.parametrize(
    ("returncode", "output", "message"),
    [
        (2, "", "no readable origin"),
        (0, "", "observed 0"),
        (
            0,
            "https://github.com/rrnewton/hermit.git\n"
            "git@github.com:rrnewton/hermit.git\n",
            "observed 2",
        ),
        (0, "https://github.com/rrnewton/hermit-evil.git\n", "identity mismatch"),
    ],
)
def test_checkout_refuses_missing_multiple_or_unrelated_origin(
    tmp_path: Path, returncode: int, output: str, message: str
) -> None:
    class OriginRunner:
        def run(
            self, command: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            argv = list(command)  # type: ignore[arg-type]
            if "rev-parse" in argv:
                return subprocess.CompletedProcess(argv, 0, "true\n", "")
            return subprocess.CompletedProcess(argv, returncode, output, "missing")

    repository = land.GitRepository(OriginRunner(), tmp_path)
    with pytest.raises(land.Refused, match=message):
        repository.ensure_checkout(land.SUPPORTED_REPO)


def test_checkout_accepts_one_exact_origin(tmp_path: Path) -> None:
    class OriginRunner:
        def run(
            self, command: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            argv = list(command)  # type: ignore[arg-type]
            output = (
                "true\n"
                if "rev-parse" in argv
                else "https://github.com/rrnewton/hermit.git\n"
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

    land.GitRepository(OriginRunner(), tmp_path).ensure_checkout(land.SUPPORTED_REPO)


def test_cli_defaults_to_purpose_fixed_lander_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT", "unrelated-session-name")
    args = land.build_parser().parse_args(
        ["--repo", land.SUPPORTED_REPO, "--pr", "42", "--expected-head", X]
    )
    assert args.actor == "hermit-lander"


def test_lander_skill_discovery_resolves_to_live_safe_executor() -> None:
    root = land.ROOT
    canonical = root / ".claude/skills/hermit-lander/SKILL.md"
    agents = root / ".agents/skills/hermit-lander/SKILL.md"
    llms = root / ".llms/skills/hermit-lander/SKILL.md"
    assert canonical.samefile(agents)
    assert canonical.samefile(llms)

    skill = canonical.read_text(encoding="utf-8")
    assert "ci-hub/bin/safe-exact-head-land" in skill
    assert "land-pr.sh` as landing authority or fallback" in skill
    assert "remains an executable file" in skill
    assert "unresolved fleet-wide migration" in skill

    wrapper = root / "ci-hub/bin/safe-exact-head-land"
    assert wrapper.stat().st_mode & 0o111
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "ci-hub/landing/safe_exact_head_land.py" in wrapper_text
    help_result = subprocess.run(
        [str(wrapper), "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--expected-head" in help_result.stdout
    assert "--actor" in help_result.stdout


def test_all_live_landing_policy_routes_to_safe_executor() -> None:
    root = land.ROOT
    skill_names = (
        "hermit-lander",
        "manual-ci-mode",
        "post-facto-review",
        "pr-landing-mechanics-merge-gate-uptodate-chase",
    )
    routing_paths: list[Path] = []
    for name in skill_names:
        canonical = root / f".claude/skills/{name}/SKILL.md"
        assert canonical.samefile(root / f".agents/skills/{name}/SKILL.md")
        assert canonical.samefile(root / f".llms/skills/{name}/SKILL.md")
        routing_paths.append(canonical)
    routing_paths.extend((root / "ci-hub/README.md", root / "ci-hub/landing/README.md"))

    for path in routing_paths:
        policy = path.read_text(encoding="utf-8")
        assert "safe-exact-head-land" in policy, path
        if "land-pr.sh" in policy:
            assert "executable" in policy.lower(), path
            assert "unresolved" in policy.lower(), path
            assert any(
                phrase in policy.lower()
                for phrase in (
                    "must not be used",
                    "do not invoke",
                    "never use",
                    "never as a fallback",
                    "not a landing authority",
                    "not canonical authority",
                    "not landing authority",
                    "not authority",
                )
            ), path


@pytest.mark.parametrize(
    ("snapshot", "unresolved", "message"),
    [
        (snap(draft=True), 0, "draft PR"),
        (snap(review="CHANGES_REQUESTED"), 0, "unresolved review decision"),
        (snap(), 2, "unresolved review thread"),
    ],
)
def test_draft_and_unresolved_review_are_refused_before_intent(
    tmp_path: Path,
    snapshot: land.PullRequestSnapshot,
    unresolved: int,
    message: str,
) -> None:
    gh = FakeGitHub([snapshot], unresolved=unresolved)
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    with pytest.raises(land.Refused, match=message):
        run(ex)
    assert gh.merge_calls == 0
    assert not store.path.exists() or not store.load()


class StaticRunner:
    def __init__(self, payload: dict[str, object], returncode: int = 0):
        self.payload = payload
        self.returncode = returncode
        self.commands: list[list[str]] = []

    def run(
        self, command: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(
            command, self.returncode, json.dumps(self.payload), ""
        )


def durable_obligation() -> dict[str, object]:
    return {
        "repo": land.SUPPORTED_REPO,
        "landed_sha": MC,
        "obligation_id": "exact-mc-obligation",
        "overall_state": "open",
        "launch": {"state": "armed"},
        "local": {
            "state": "running",
            "registered_at": "2026-08-05T00:00:00Z",
            "pid": os.getpid(),
        },
        "watcher": {
            "state": "running",
            "started_at": "2026-08-05T00:00:00Z",
            "pid": os.getpid(),
        },
    }


class SequenceRunner:
    def __init__(
        self,
        *,
        arm_returncode: int = 0,
        obligation_returncode: int = 0,
        obligation: dict[str, object] | None = None,
    ) -> None:
        self.commands: list[list[str]] = []
        self.arm_returncode = arm_returncode
        self.obligation_returncode = obligation_returncode
        self.obligation = obligation if obligation is not None else durable_obligation()

    def run(
        self, command: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)  # type: ignore[arg-type]
        self.commands.append(argv)
        if "obligations" in argv:
            payload = {"obligations": [self.obligation]}
            return subprocess.CompletedProcess(
                argv, self.obligation_returncode, json.dumps(payload), ""
            )
        return subprocess.CompletedProcess(
            argv, self.arm_returncode, "armed", "pending"
        )


def test_obligation_armer_passes_pr_and_dereferences_exact_mc(tmp_path: Path) -> None:
    runner = SequenceRunner()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    result = armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")

    arm_command = runner.commands[0]
    assert arm_command[:3] == ["/fixture/ci-hub", "arm-land", MC]
    assert arm_command[arm_command.index("--pr") + 1] == "42"
    assert result["obligation_id"] == "exact-mc-obligation"
    assert result["launch_durable"] is True


@pytest.mark.parametrize(
    ("returncode", "overall_state"),
    [(1, "open"), (2, "remediation_required")],
)
def test_obligation_armer_accepts_typed_nonzero_query_state(
    tmp_path: Path, returncode: int, overall_state: str
) -> None:
    obligation = durable_obligation()
    obligation["overall_state"] = overall_state
    runner = SequenceRunner(
        obligation_returncode=returncode, obligation=obligation
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    result = armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")

    assert result["obligation_id"] == "exact-mc-obligation"
    assert result["overall_state"] == overall_state
    assert result["launch_durable"] is True


def test_obligation_armer_refuses_unexpected_query_returncode(
    tmp_path: Path,
) -> None:
    runner = SequenceRunner(obligation_returncode=3)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    with pytest.raises(land.LandingError, match="cannot dereference"):
        armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")


@pytest.mark.parametrize("stdout", ["", "{", "[]"])
def test_obligation_armer_refuses_malformed_json_for_typed_nonzero_query(
    tmp_path: Path, stdout: str
) -> None:
    class RawQueryRunner(SequenceRunner):
        def run(
            self, command: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            argv = list(command)  # type: ignore[arg-type]
            self.commands.append(argv)
            if "obligations" in argv:
                return subprocess.CompletedProcess(argv, 1, stdout, "")
            return subprocess.CompletedProcess(argv, 0, "armed", "")

    runner = RawQueryRunner()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    with pytest.raises(land.LandingError):
        armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"obligations": "not-a-list"}, "did not return a record list"),
        ({"obligations": []}, "does not contain exactly one"),
        (
            {"obligations": [durable_obligation(), durable_obligation()]},
            "does not contain exactly one",
        ),
        (
            {"obligations": [{**durable_obligation(), "repo": "wrong/repo"}]},
            "does not bind the exact repository and merge commit",
        ),
        (
            {"obligations": [{**durable_obligation(), "landed_sha": O}]},
            "does not bind the exact repository and merge commit",
        ),
    ],
)
def test_obligation_armer_keeps_record_checks_for_typed_nonzero_query(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    runner = StaticRunner(payload, returncode=1)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    with pytest.raises(land.LandingError, match=message):
        armer.verify(land.SUPPORTED_REPO, MC, "exact-mc-obligation")


def test_obligation_armer_refuses_nonzero_arm_result_even_with_durable_record(
    tmp_path: Path,
) -> None:
    runner = SequenceRunner(arm_returncode=3)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    with pytest.raises(land.LandingError, match="did not report a durable launch"):
        armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")


def test_obligation_armer_refuses_exact_mc_record_without_durable_watcher(
    tmp_path: Path,
) -> None:
    pending = durable_obligation()
    pending["watcher"] = {"state": "pending"}
    runner = SequenceRunner(obligation=pending)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )

    with pytest.raises(land.LandingError, match="launch durability is pending"):
        armer.arm(land.SUPPORTED_REPO, 42, MC, "hermit-lander")


def test_nondurable_watcher_never_records_armed_and_recovery_retries(
    tmp_path: Path,
) -> None:
    pending = durable_obligation()
    pending["watcher"] = {"state": "pending"}
    runner = SequenceRunner(obligation=pending)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    armer = land.CanonicalObligationArmer(
        runner, checkout, ci_hub=Path("/fixture/ci-hub")
    )
    gh = FakeGitHub([snap(), snap(), snap(), snap(state="MERGED", merge_commit=MC)])
    repo = FakeRepository([O, O, MC], replay_base=O)
    ex, _, store, _ = executor(
        tmp_path, gh, repo, FakeReceiptAuthority({X, O}), armer  # type: ignore[arg-type]
    )

    with pytest.raises(land.LandingError, match="launch durability is pending"):
        run(ex)

    first_events = store.load()
    assert gh.merge_calls == 1
    assert any(row["event_type"] == "landing_verified" for row in first_events)
    assert first_events[-1]["event_type"] == "arm_failed"
    assert not any(row["event_type"] == "obligation_armed" for row in first_events)

    runner.obligation = durable_obligation()
    result = run(ex)

    assert result.recovered is True
    assert gh.merge_calls == 1
    assert store.load()[-1]["event_type"] == "obligation_armed"


def test_github_merge_mutation_is_atomic_expected_head_and_no_rewrite() -> None:
    runner = SequenceRunner()
    client = land.GitHubClient(runner)

    client.request_rebase_merge(land.SUPPORTED_REPO, 42, X)

    command = runner.commands[0]
    assert command == [
        "with-proxy",
        "gh",
        "api",
        "--include",
        "--method",
        "PUT",
        f"repos/{land.SUPPORTED_REPO}/pulls/42/merge",
        "-f",
        f"sha={X}",
        "-f",
        "merge_method=rebase",
    ]
    assert "push" not in command and "checkout" not in command


def test_canonical_mutation_barrier_carries_exact_operation_and_child() -> None:
    class BarrierRunner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run(
            self,
            command: object,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            argv = list(command)  # type: ignore[arg-type]
            self.commands.append(argv)
            action = argv[2]
            if action == "bind-mutation-call":
                marker = (
                    f"MUTATION_CALL_BOUND agent=hermit-lander "
                    f"repo={land.SUPPORTED_REPO} pr=42 operation={X} "
                    f"attempt_id={'a' * 32} call_count=1 call_id={'b' * 32}\n"
                )
            else:
                state = "ARMED" if action == "arm-mutation" else "CLEARED"
                marker = (
                    f"MUTATION_BARRIER_{state} agent=hermit-lander "
                    f"repo={land.SUPPORTED_REPO} pr=42 operation={X} "
                    f"attempt_id={'a' * 32}\n"
                )
            return subprocess.CompletedProcess(argv, 0, marker, "")

    runner = BarrierRunner()
    barrier = land.CanonicalMutationBarrier(runner, Path("/fixture/ci-hub"))
    identity = {
        "actor": "hermit-lander",
        "repo": land.SUPPORTED_REPO,
        "pr": 42,
        "operation": X,
        "attempt_id": "a" * 32,
    }

    barrier.arm(**identity)
    barrier.bind_call(**identity, call_count=1, call_id="b" * 32)
    barrier.clear(**identity)

    for command, action in zip(
        runner.commands,
        ("arm-mutation", "bind-mutation-call", "clear-mutation"),
        strict=True,
    ):
        assert command[:3] == ["/fixture/ci-hub", "land-lock", action]
        assert command[command.index("--operation") + 1] == X
        assert command[command.index("--attempt-id") + 1] == "a" * 32
        assert command[command.index("--child-pid") + 1] == str(os.getpid())
    bind_command = runner.commands[1]
    assert bind_command[bind_command.index("--call-count") + 1] == "1"
    assert bind_command[bind_command.index("--call-id") + 1] == "b" * 32


def test_vacuous_validate_status_report_is_refused() -> None:
    payload = receipt(X).report | {
        "qualifying_count": 0,
        "newest_qualifying": None,
    }
    authority = land.CanonicalReceiptAuthority(StaticRunner(payload), Path("/x/ci-hub"))
    with pytest.raises(land.Refused, match="no qualifying counted receipt"):
        authority.verify(X)


def test_receipt_authority_queries_exact_repository_and_head() -> None:
    runner = StaticRunner(receipt(X).report)
    authority = land.CanonicalReceiptAuthority(runner, Path("/fixture/ci-hub"))

    evidence = authority.verify(X)

    assert runner.commands == [
        [
            "/fixture/ci-hub",
            "validate-status",
            "--repo",
            land.SUPPORTED_REPO,
            "--sha",
            X,
            "--json",
        ]
    ]
    assert evidence.report["repo"] == land.SUPPORTED_REPO
    assert evidence.report["newest_qualifying"]["receipt_identity"]["digest"]


def test_sparse_validate_status_report_is_refused() -> None:
    sparse = {
        "schema_version": 1,
        "repo": land.SUPPORTED_REPO,
        "sha": X,
        "verdict": "VALIDATED",
        "exit_code": 0,
        "qualifying_count": 1,
        "disqualified_count": 0,
        "ledger": "/durable/validate-run-ledger.jsonl",
        "newest_qualifying": {
            "profile": "full",
            "selection_mode": "full",
            "result": "pass",
        },
    }
    authority = land.CanonicalReceiptAuthority(
        StaticRunner(sparse), Path("/fixture/ci-hub")
    )

    with pytest.raises(land.Refused, match="not repository-bound"):
        authority.verify(X)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("repo",), "rrnewton/hermit-lookalike", "not bound to the repository"),
        (("newest_qualifying", "sha"), W, "not exact-SHA-bound"),
        (("newest_qualifying", "tree_dirty"), True, "not clean"),
        (("newest_qualifying", "executed_tests"), 0, "invalid or unbound test"),
        (("newest_qualifying", "gates_run"), 0, "inconsistent gate coverage"),
        (("newest_qualifying", "coverage_satisfied"), False, "unsatisfied coverage"),
        (("newest_qualifying", "failures"), 1, "zero-failure pass"),
        (
            ("newest_qualifying", "receipt_identity", "digest"),
            "not-a-digest",
            "invalid canonical digest",
        ),
    ],
)
def test_enriched_receipt_conditions_are_individually_required(
    path: tuple[str, ...], value: object, message: str
) -> None:
    payload = receipt(X).report
    target: dict[str, object] = payload
    for key in path[:-1]:
        child = target[key]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value
    authority = land.CanonicalReceiptAuthority(
        StaticRunner(payload), Path("/fixture/ci-hub")
    )

    with pytest.raises(land.Refused, match=message):
        authority.verify(X)


def test_malformed_and_tampered_store_records_are_refused(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(land.StoreError, match="invalid landing JSONL"):
        land.EventStore(malformed).load()

    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(
        tmp_path / "valid", gh, repo, FakeReceiptAuthority({X, O})
    )
    ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record["source_receipt"]["report"]["qualifying_count"] = 0
    store.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(land.StoreError, match="source receipt"):
        store.load()


def test_duplicate_intent_and_multiple_nonterminal_heads_are_refused(
    tmp_path: Path,
) -> None:
    gh = FakeGitHub([snap()])
    repo = FakeRepository([O])
    ex, _, store, _ = executor(tmp_path, gh, repo, FakeReceiptAuthority({X, O}))
    intent = ex._new_intent(land.SUPPORTED_REPO, 42, X, "hermit-lander")
    duplicate = dict(intent)
    duplicate["event_id"] = "f" * 32
    with pytest.raises(land.StoreError, match="duplicate intent"):
        store.append(duplicate)

    second = dict(intent)
    second["attempt_id"] = "b" * 32
    second["event_id"] = "e" * 32
    second["expected_head"] = W
    second["source_receipt"] = receipt(W).as_json()
    second["github_pr_commits"] = list(second["source_commits"])
    with pytest.raises(land.StoreError, match="multiple nonterminal"):
        store.append(second)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit_file(repo: Path, path: str, value: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def test_real_git_replay_proof_classifies_conflict_free_divergence_soft(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.com")
    (repo / "common").write_text("root\n", encoding="utf-8")
    git(repo, "add", "common")
    git(repo, "commit", "-m", "root")
    source_base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "source")
    commit_file(repo, "source-a", "one\n", "source one")
    expected_head = commit_file(repo, "source-b", "two\n", "source two")
    git(repo, "switch", "-c", "base", source_base)
    observed_base = commit_file(repo, "base-only", "base\n", "base advance")
    git(repo, "switch", "-c", "replay", expected_head)
    git(repo, "rebase", "--onto", observed_base, source_base)
    merge_commit = git(repo, "rev-parse", "HEAD")

    runner = land.SubprocessRunner()
    repository = land.GitRepository(runner, repo)
    source = repository.source_provenance(expected_head, observed_base)
    proof = repository.verify_replay(
        expected_head=expected_head,
        observed_base=observed_base,
        source_base=source.source_base,
        source_commit_count=source.source_commit_count,
        source_commits=source.source_commits,
        merge_commit=merge_commit,
        fetched_main=merge_commit,
    )

    assert proof.replay_base == observed_base
    assert proof.composition_merge_base == source_base
    assert proof.green_class == land.GREEN_SOFT
    assert proof.expected_tree == proof.actual_tree


def test_real_git_replay_tree_mismatch_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.com")
    (repo / "root").write_text("root\n", encoding="utf-8")
    git(repo, "add", "root")
    git(repo, "commit", "-m", "root")
    source_base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "source")
    expected_head = commit_file(repo, "source", "source\n", "source")
    git(repo, "switch", "-c", "base", source_base)
    observed_base = commit_file(repo, "base", "base\n", "base")
    # One replay-shaped commit, but with a planted extra tree change.
    commit_file(repo, "wrong", "wrong\n", "wrong replay")
    merge_commit = git(repo, "rev-parse", "HEAD")

    repository = land.GitRepository(land.SubprocessRunner(), repo)
    source = repository.source_provenance(expected_head, observed_base)
    with pytest.raises(land.ReplayMismatch, match="MC tree differs"):
        repository.verify_replay(
            expected_head=expected_head,
            observed_base=observed_base,
            source_base=source.source_base,
            source_commit_count=source.source_commit_count,
            source_commits=source.source_commits,
            merge_commit=merge_commit,
            fetched_main=merge_commit,
        )


def test_main_always_reexecs_under_canonical_lock_and_strips_bypass_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class ExecIntercept(RuntimeError):
        pass

    def intercept(path: str, command: list[str], environment: dict[str, str]) -> None:
        captured.update(path=path, command=command, environment=environment)
        raise ExecIntercept

    monkeypatch.setenv("CI_HUB_SAFE_EXACT_HEAD_LAND_UNDER_LOCK", "1")
    monkeypatch.setenv(land.LAND_LOCK_OVERRIDE, "/tmp/attacker-lock")
    monkeypatch.setenv(land.LAND_STORE_OVERRIDE, "/tmp/attacker-landings")
    monkeypatch.setenv(land.OBLIGATION_STORE_OVERRIDE, "/tmp/attacker-obligations")
    monkeypatch.setenv(land.CI_HUB_PARSE_ONLY, "1")
    monkeypatch.setattr(land.os, "execve", intercept)
    arguments = [
        "--repo",
        land.SUPPORTED_REPO,
        "--pr",
        "1628",
        "--expected-head",
        X,
        "--actor",
        "fixture-lander",
    ]

    with pytest.raises(ExecIntercept):
        land.main(arguments)

    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:9] == [
        "land-lock",
        "run",
        "--agent",
        "fixture-lander",
        "--repo",
        land.SUPPORTED_REPO,
        "--pr",
        "1628",
    ]
    assert command[9:11] == ["--operation", X]
    assert command[-1] == "--lock-child"
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert land.LAND_LOCK_OVERRIDE not in environment
    assert land.LAND_STORE_OVERRIDE not in environment
    assert land.OBLIGATION_STORE_OVERRIDE not in environment
    assert land.CI_HUB_PARSE_ONLY not in environment


def test_public_cli_rejects_store_authority_overrides() -> None:
    base = [
        "--repo",
        land.SUPPORTED_REPO,
        "--pr",
        "1628",
        "--expected-head",
        X,
    ]
    with pytest.raises(SystemExit):
        land.build_parser().parse_args([*base, "--store", "/tmp/split-history"])
    with pytest.raises(SystemExit):
        land.build_parser().parse_args(
            [*base, "--obligation-store", "/tmp/split-obligations"]
        )


def test_forged_hidden_child_flag_requires_canonical_process_assertion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def refuse_assertion(
        self: land.SubprocessRunner,
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 3, "", "no canonical lease")

    monkeypatch.setenv(land.LAND_LOCK_OVERRIDE, "/tmp/attacker-lock")
    monkeypatch.setenv(land.LAND_STORE_OVERRIDE, "/tmp/attacker-landings")
    monkeypatch.setenv(land.OBLIGATION_STORE_OVERRIDE, "/tmp/attacker-obligations")
    monkeypatch.setenv(land.CI_HUB_PARSE_ONLY, "1")
    monkeypatch.setattr(land.SubprocessRunner, "run", refuse_assertion)

    code = land.main(
        [
            "--repo",
            land.SUPPORTED_REPO,
            "--pr",
            "1628",
            "--expected-head",
            X,
            "--actor",
            "fixture-lander",
            "--lock-child",
            "--json",
        ]
    )

    assert code == land.EXIT_REFUSED
    assert len(calls) == 1
    assert calls[0][1:4] == ["land-lock", "assert-child", "--agent"]
    assert calls[0][-2:] == ["--child-pid", str(os.getpid())]
    assert land.LAND_LOCK_OVERRIDE not in os.environ
    assert land.LAND_STORE_OVERRIDE not in os.environ
    assert land.OBLIGATION_STORE_OVERRIDE not in os.environ
    assert land.CI_HUB_PARSE_ONLY not in os.environ
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "REFUSED"
    assert "canonical landing-lock child assertion failed" in payload["reason"]


def test_public_lock_cli_authorizes_real_chain_and_refuses_too_deep_chain(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    probe = (
        "import os,pathlib,subprocess,sys\n"
        "hub=sys.argv[1]\n"
        "if not pathlib.Path(sys.argv[3]+'.cleanup-required').is_file(): raise SystemExit(99)\n"
        "pid=str(os.getpid())\n"
        "base=[hub,'land-lock','assert-child','--agent','fixture-lander',"
        "'--repo','rrnewton/hermit','--pr','1628','--operation',sys.argv[2],'--child-pid',pid]\n"
        "positive=subprocess.run(base,capture_output=True,text=True)\n"
        "wrong_repo=base.copy(); wrong_repo[6]='rrnewton/reverie'\n"
        "wrong_repo_result=subprocess.run(wrong_repo,capture_output=True,text=True)\n"
        "wrong_pr=base.copy(); wrong_pr[8]='1629'\n"
        "wrong_pr_result=subprocess.run(wrong_pr,capture_output=True,text=True)\n"
        "wrong_operation=base.copy(); wrong_operation[10]='0000000000000000000000000000000000000004'\n"
        "wrong_operation_result=subprocess.run(wrong_operation,capture_output=True,text=True)\n"
        "wrong_child=base.copy(); wrong_child[12]='1'\n"
        "wrong_child_result=subprocess.run(wrong_child,capture_output=True,text=True)\n"
        "wrapper='import subprocess,sys; raise SystemExit(subprocess.run(sys.argv[1:]).returncode)'\n"
        "deep=base\n"
        "for _ in range(10): deep=[sys.executable,'-c',wrapper,*deep]\n"
        "negative=subprocess.run(deep,capture_output=True,text=True)\n"
        "sys.stdout.write(positive.stdout)\n"
        "sys.stderr.write(positive.stderr+wrong_repo_result.stderr+wrong_pr_result.stderr+wrong_operation_result.stderr+wrong_child_result.stderr+negative.stderr)\n"
        "codes=[wrong_repo_result.returncode,wrong_pr_result.returncode,wrong_operation_result.returncode,wrong_child_result.returncode,negative.returncode]\n"
        "raise SystemExit(0 if positive.returncode==0 and codes==[3,3,3,3,3] else 1)\n"
    )
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    environment.pop(land.CI_HUB_PARSE_ONLY, None)
    result = subprocess.run(
        [
            str(hub),
            "land-lock",
            "run",
            "--agent",
            "fixture-lander",
            "--repo",
            land.SUPPORTED_REPO,
            "--pr",
            "1628",
            "--operation",
            X,
            "--",
            sys.executable,
            "-c",
            probe,
            str(hub),
            X,
            str(lock_path),
        ],
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "LOCK_CHILD_VERIFIED agent=fixture-lander" in result.stdout
    assert "not a bounded descendant" in result.stderr
    assert not lock_path.exists()
    assert not Path(f"{lock_path}.owner").exists()

    direct = subprocess.run(
        [
            str(hub),
            "land-lock",
            "assert-child",
            "--agent",
            "fixture-lander",
            "--repo",
            land.SUPPORTED_REPO,
            "--pr",
            "1628",
            "--operation",
            X,
            "--child-pid",
            str(os.getpid()),
        ],
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert direct.returncode == 3
    assert "no landing lease is held" in direct.stderr


def _wait_for_path(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def _pid_is_live(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    close = stat.rfind(")")
    return close >= 0 and stat[close + 1 :].split()[0] not in {"Z", "X"}


def _pid_start_ticks(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text()
    close = stat.rfind(")")
    assert close >= 0
    fields = stat[close + 1 :].split()
    return int(fields[19])


def _pid_parent(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text()
    close = stat.rfind(")")
    assert close >= 0
    fields = stat[close + 1 :].split()
    return int(fields[1])


def _exact_process_is_live(pid: int, start_ticks: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    close = stat.rfind(")")
    if close < 0:
        return False
    fields = stat[close + 1 :].split()
    return fields[0] not in {"Z", "X"} and int(fields[19]) == start_ticks


def _wait_for_exact_process_exit(
    pid: int, start_ticks: int, timeout: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout
    while _exact_process_is_live(pid, start_ticks) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _exact_process_is_live(pid, start_ticks)


def _public_lock_command(
    hub: Path,
    *,
    agent: str,
    operation: str,
    child: list[str],
    wait: int = 0,
    hold: int = 30,
    deadline: int = 30,
) -> list[str]:
    return [
        str(hub),
        "land-lock",
        "run",
        "--agent",
        agent,
        "--repo",
        land.SUPPORTED_REPO,
        "--pr",
        "1628",
        "--operation",
        operation,
        "--wait",
        str(wait),
        "--hold",
        str(hold),
        "--child-deadline",
        str(deadline),
        "--",
        *child,
    ]


@pytest.mark.skip(
    reason="retired .domain watchdog model; current CleanupRecord crash windows are bracketed in landing_lock Rust tests"
)
def test_supervisor_watchdog_bootstrap_deadline_kills_exact_unarmed_parent_only(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    marker = tmp_path / "bootstrap-identities"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    holder = subprocess.Popen(
        _public_lock_command(
            hub,
            agent="bootstrap-unrelated-holder",
            operation=X,
            child=["sleep", "120"],
            hold=120,
            deadline=120,
        ),
        cwd=land.ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    helper: subprocess.Popen[str] | None = None
    holder_pid = 0
    holder_start_ticks = 0
    holder_watchdog_pid = 0
    holder_watchdog_start_ticks = 0
    watchdog_pid = 0
    watchdog_start_ticks = 0
    try:
        owner_path = Path(f"{lock_path}.owner")
        _wait_for_path(owner_path, timeout=30)
        _wait_for_path(Path(f"{lock_path}.domain"), timeout=30)
        holder_owner = dict(
            line.split("=", 1) for line in owner_path.read_text().splitlines()
        )
        holder_pid = int(holder_owner["pid"])
        holder_start_ticks = int(holder_owner["start_ticks"])
        holder_domain = dict(
            line.split("=", 1)
            for line in Path(f"{lock_path}.domain").read_text().splitlines()
        )
        holder_watchdog_pid = int(holder_domain["watchdog_pid"])
        holder_watchdog_start_ticks = int(holder_domain["watchdog_start_ticks"])
        executable = Path(f"/proc/{holder_pid}/exe").resolve(strict=True)

        helper_code = (
            "import os,pathlib,signal,subprocess,sys\n"
            "def start_ticks(pid):\n"
            " s=pathlib.Path(f'/proc/{pid}/stat').read_text(); c=s.rfind(')')\n"
            " return int(s[c+1:].split()[19])\n"
            "parent_start=start_ticks(os.getpid())\n"
            "environment=dict(os.environ)\n"
            "environment['RUST_SCRIPT_PATH']=sys.argv[3]\n"
            "watchdog=subprocess.Popen([sys.argv[1],'land-lock','supervisor-watchdog',"
            "'--supervisor-start-ticks',str(parent_start)],stdin=subprocess.PIPE,"
            "stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,"
            "env=environment)\n"
            "ready=watchdog.stdout.readline().strip()\n"
            "if ready != 'READY':\n"
            " raise SystemExit(f'watchdog did not become ready: {ready!r}')\n"
            "watchdog_start=start_ticks(watchdog.pid)\n"
            "pathlib.Path(sys.argv[2]).write_text("
            "f'{os.getpid()} {parent_start} {watchdog.pid} {watchdog_start}\\n')\n"
            "os.kill(os.getpid(),signal.SIGSTOP)\n"
            "raise SystemExit('watchdog failed to enforce its bootstrap deadline')\n"
        )
        helper = subprocess.Popen(
            [
                sys.executable,
                "-c",
                helper_code,
                str(executable),
                str(marker),
                str(land.ROOT / "ci-hub/ci-hub.rs"),
            ],
            cwd=land.ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_path(marker, timeout=15)
        (
            recorded_helper_pid,
            recorded_helper_start,
            watchdog_pid,
            watchdog_start_ticks,
        ) = (int(value) for value in marker.read_text().split())
        helper_start_ticks = _pid_start_ticks(helper.pid)
        assert (recorded_helper_pid, recorded_helper_start) == (
            helper.pid,
            helper_start_ticks,
        )
        assert _exact_process_is_live(watchdog_pid, watchdog_start_ticks)
        assert _pid_parent(watchdog_pid) == helper.pid
        assert (
            dict(line.split("=", 1) for line in owner_path.read_text().splitlines())
            == holder_owner
        )
        assert _exact_process_is_live(holder_pid, holder_start_ticks)

        assert helper.wait(timeout=40) == -signal.SIGKILL
        _wait_for_exact_process_exit(watchdog_pid, watchdog_start_ticks, timeout=10)
        assert _exact_process_is_live(holder_pid, holder_start_ticks)
        assert (
            dict(line.split("=", 1) for line in owner_path.read_text().splitlines())
            == holder_owner
        )
    finally:
        try:
            if helper is not None and helper.poll() is None:
                try:
                    os.kill(helper.pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
                try:
                    helper.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait(timeout=10)
            if watchdog_pid and _exact_process_is_live(
                watchdog_pid, watchdog_start_ticks
            ):
                try:
                    _wait_for_exact_process_exit(
                        watchdog_pid, watchdog_start_ticks, timeout=40
                    )
                except AssertionError:
                    if _exact_process_is_live(watchdog_pid, watchdog_start_ticks):
                        os.kill(watchdog_pid, signal.SIGKILL)
                    _wait_for_exact_process_exit(
                        watchdog_pid, watchdog_start_ticks, timeout=10
                    )
        finally:
            if holder_pid and _exact_process_is_live(holder_pid, holder_start_ticks):
                os.kill(holder_pid, signal.SIGTERM)
            try:
                holder.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if holder_pid and _exact_process_is_live(
                    holder_pid, holder_start_ticks
                ):
                    os.kill(holder_pid, signal.SIGKILL)
                holder.kill()
                holder.wait(timeout=10)
            if holder_watchdog_pid and _exact_process_is_live(
                holder_watchdog_pid, holder_watchdog_start_ticks
            ):
                try:
                    _wait_for_exact_process_exit(
                        holder_watchdog_pid,
                        holder_watchdog_start_ticks,
                        timeout=15,
                    )
                except AssertionError:
                    if _exact_process_is_live(
                        holder_watchdog_pid, holder_watchdog_start_ticks
                    ):
                        os.kill(holder_watchdog_pid, signal.SIGKILL)
                    _wait_for_exact_process_exit(
                        holder_watchdog_pid,
                        holder_watchdog_start_ticks,
                        timeout=10,
                    )


@pytest.mark.skip(
    reason="retired .domain watchdog model; current CleanupRecord residual census is bracketed in landing_lock Rust tests"
)
def test_supervisor_sigkill_watchdog_empties_grandchild_and_allows_recovery(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    grandchild_path = tmp_path / "grandchild.pid"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    worker = (
        "import pathlib,subprocess,sys,time\n"
        "code='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)'\n"
        "child=subprocess.Popen([sys.executable,'-c',code])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(
        _public_lock_command(
            hub,
            agent="sigkill-owner",
            operation=X,
            child=[sys.executable, "-c", worker, str(grandchild_path)],
            deadline=60,
        ),
        cwd=land.ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    grandchild_pid = 0
    watchdog_pid = 0
    watchdog_start_ticks = 0
    try:
        _wait_for_path(Path(f"{lock_path}.owner"))
        _wait_for_path(Path(f"{lock_path}.domain"))
        _wait_for_path(grandchild_path)
        owner_fields = dict(
            line.split("=", 1)
            for line in Path(f"{lock_path}.owner").read_text().splitlines()
        )
        domain_fields = dict(
            line.split("=", 1)
            for line in Path(f"{lock_path}.domain").read_text().splitlines()
        )
        supervisor_pid = int(owner_fields["pid"])
        watchdog_pid = int(domain_fields["watchdog_pid"])
        watchdog_start_ticks = int(domain_fields["watchdog_start_ticks"])
        grandchild_pid = int(grandchild_path.read_text())
        os.kill(supervisor_pid, signal.SIGKILL)
        process.wait(timeout=15)

        refused = subprocess.run(
            _public_lock_command(
                hub,
                agent="replacement",
                operation=W,
                child=["/bin/true"],
            ),
            cwd=land.ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert refused.returncode == 1
        assert "supervised" in refused.stderr or "held by" in refused.stderr
        deadline = time.monotonic() + 15
        while (
            grandchild_pid
            and _pid_is_live(grandchild_pid)
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not _pid_is_live(grandchild_pid)
        _wait_for_exact_process_exit(watchdog_pid, watchdog_start_ticks)

        recovered = subprocess.run(
            _public_lock_command(
                hub,
                agent="replacement",
                operation=W,
                child=["/bin/true"],
            ),
            cwd=land.ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert recovered.returncode == 0, recovered.stderr
        assert not lock_path.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if grandchild_pid and _pid_is_live(grandchild_pid):
            os.kill(grandchild_pid, signal.SIGKILL)


@pytest.mark.skip(
    reason="retired hidden watchdog command; typed cleanup signaling is bracketed in landing_lock Rust tests"
)
def test_direct_watchdog_without_canonical_domain_cannot_signal_group(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    target = subprocess.Popen(["sleep", "120"], start_new_session=True)
    target_start_ticks = _pid_start_ticks(target.pid)
    try:
        result = subprocess.run(
            [
                str(hub),
                "land-lock",
                "watchdog",
                "--pgid",
                str(target.pid),
                "--leader-pid",
                str(target.pid),
                "--leader-start-ticks",
                str(target_start_ticks),
                "--deadline-at",
                str(int(time.time()) + 30),
            ],
            cwd=land.ROOT,
            env=environment,
            input="armed\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 3
        assert result.stdout.splitlines() == ["READY"]
        assert "no persisted process domain" in result.stderr
        assert _exact_process_is_live(target.pid, target_start_ticks)
    finally:
        if _exact_process_is_live(target.pid, target_start_ticks):
            target.kill()
        target.wait(timeout=10)


@pytest.mark.skip(
    reason="retired .domain watchdog model; current cleanup quarantine/recovery is bracketed in landing_lock Rust tests"
)
def test_stopped_live_supervisor_is_fenced_after_persisted_deadline(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    replacement_started = tmp_path / "replacement-started"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    old = subprocess.Popen(
        _public_lock_command(
            hub,
            agent="wedged-owner",
            operation=X,
            child=["sleep", "120"],
            hold=30,
            deadline=1,
        ),
        cwd=land.ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    replacement: subprocess.Popen[str] | None = None
    old_supervisor_pid = 0
    old_supervisor_start_ticks = 0
    try:
        owner_path = Path(f"{lock_path}.owner")
        _wait_for_path(owner_path)
        _wait_for_path(Path(f"{lock_path}.domain"))
        old_owner = dict(
            line.split("=", 1) for line in owner_path.read_text().splitlines()
        )
        old_supervisor_pid = int(old_owner["pid"])
        old_supervisor_start_ticks = int(old_owner["start_ticks"])
        os.kill(old_supervisor_pid, signal.SIGSTOP)

        replacement_worker = (
            "import pathlib,sys,time\n"
            "pathlib.Path(sys.argv[1]).write_text('started')\n"
            "time.sleep(8)\n"
        )
        replacement = subprocess.Popen(
            _public_lock_command(
                hub,
                agent="wedged-owner",
                operation=X,
                child=[
                    sys.executable,
                    "-c",
                    replacement_worker,
                    str(replacement_started),
                ],
                wait=25,
                hold=30,
                deadline=30,
            ),
            cwd=land.ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_path(replacement_started, timeout=25)
        replacement_owner = dict(
            line.split("=", 1) for line in owner_path.read_text().splitlines()
        )
        assert int(replacement_owner["pid"]) != old_supervisor_pid

        if _exact_process_is_live(old_supervisor_pid, old_supervisor_start_ticks):
            os.kill(old_supervisor_pid, signal.SIGCONT)
        else:
            assert not _exact_process_is_live(
                old_supervisor_pid, old_supervisor_start_ticks
            )
        assert old.wait(timeout=10) != 0
        time.sleep(0.25)
        assert replacement.poll() is None
        assert (
            dict(line.split("=", 1) for line in owner_path.read_text().splitlines())
            == replacement_owner
        )
        assert replacement.wait(timeout=30) == 0
        assert not lock_path.exists()
    finally:
        if _exact_process_is_live(old_supervisor_pid, old_supervisor_start_ticks):
            try:
                os.kill(old_supervisor_pid, signal.SIGCONT)
                os.kill(old_supervisor_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if old.poll() is None:
            old.kill()
            old.wait(timeout=10)
        if replacement is not None and replacement.poll() is None:
            replacement.kill()
            replacement.wait(timeout=10)


def test_deadline_kills_term_ignoring_grandchild_before_release(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    grandchild_path = tmp_path / "grandchild.pid"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    worker = (
        "import pathlib,subprocess,sys,time\n"
        "code='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)'\n"
        "child=subprocess.Popen([sys.executable,'-c',code])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    result = subprocess.run(
        _public_lock_command(
            hub,
            agent="deadline-owner",
            operation=X,
            child=[sys.executable, "-c", worker, str(grandchild_path)],
            deadline=1,
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 124, result.stderr
    grandchild_pid = int(grandchild_path.read_text())
    assert not _pid_is_live(grandchild_pid)
    assert not lock_path.exists()
    assert not Path(f"{lock_path}.domain").exists()


@pytest.mark.skip(
    reason="retired .domain watchdog model; heartbeat empty-release and incomplete-census quarantine have Rust brackets"
)
def test_missing_holder_blocks_until_heartbeat_empties_domain(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    process = subprocess.Popen(
        _public_lock_command(
            hub,
            agent="heartbeat-owner",
            operation=X,
            child=["sleep", "120"],
            hold=9,
            deadline=30,
        ),
        cwd=land.ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_path(Path(f"{lock_path}.domain"))
        lock_path.unlink()
        refused = subprocess.run(
            _public_lock_command(
                hub,
                agent="heartbeat-competitor",
                operation=W,
                child=["/bin/true"],
            ),
            cwd=land.ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert refused.returncode == 1, refused.stderr
        assert process.wait(timeout=15) == 125
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert not lock_path.exists()
    assert not Path(f"{lock_path}.owner").exists()
    assert not Path(f"{lock_path}.domain").exists()


def test_pending_mutation_requires_exact_operation_recovery(
    tmp_path: Path,
) -> None:
    hub = land.ROOT / "ci-hub/ci-hub"
    lock_path = tmp_path / "landing-lock"
    environment = dict(os.environ)
    environment[land.LAND_LOCK_OVERRIDE] = str(lock_path)
    barrier_probe = (
        "import os,subprocess,sys\n"
        "cmd=[sys.argv[1],'land-lock',sys.argv[2],'--agent','barrier-owner',"
        "'--repo','rrnewton/hermit','--pr','1628','--operation',sys.argv[3],"
        "'--attempt-id',sys.argv[4],'--child-pid',str(os.getpid())]\n"
        "result=subprocess.run(cmd,capture_output=True,text=True)\n"
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)\n"
        "raise SystemExit(int(sys.argv[5]) if result.returncode==0 else result.returncode)\n"
    )
    call_probe = (
        "import os,subprocess,sys\n"
        "cmd=[sys.argv[1],'land-lock','bind-mutation-call','--agent','barrier-owner',"
        "'--repo','rrnewton/hermit','--pr','1628','--operation',sys.argv[2],"
        "'--attempt-id',sys.argv[3],'--call-count','1','--call-id',sys.argv[4],"
        "'--child-pid',str(os.getpid())]\n"
        "result=subprocess.run(cmd,capture_output=True,text=True)\n"
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)\n"
        "raise SystemExit(4 if result.returncode==0 else result.returncode)\n"
    )
    armed = subprocess.run(
        _public_lock_command(
            hub,
            agent="barrier-owner",
            operation=X,
            child=[
                sys.executable,
                "-c",
                barrier_probe,
                str(hub),
                "arm-mutation",
                X,
                "a" * 32,
                "4",
            ],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert armed.returncode == 4, armed.stderr
    assert "pending_mutation=" + X in lock_path.read_text()
    assert "pending_attempt=" + "a" * 32 in lock_path.read_text()

    bound = subprocess.run(
        _public_lock_command(
            hub,
            agent="barrier-owner",
            operation=X,
            child=[
                sys.executable,
                "-c",
                call_probe,
                str(hub),
                X,
                "a" * 32,
                "c" * 32,
            ],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert bound.returncode == 4, bound.stderr
    assert "pending_call_count=1" in lock_path.read_text()
    assert "pending_call_id=" + "c" * 32 in lock_path.read_text()

    wrong = subprocess.run(
        _public_lock_command(
            hub,
            agent="other-owner",
            operation=W,
            child=["/bin/true"],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert wrong.returncode == 3
    assert "external mutation remains pending" in wrong.stderr

    wrong_attempt = subprocess.run(
        _public_lock_command(
            hub,
            agent="barrier-owner",
            operation=X,
            child=[
                sys.executable,
                "-c",
                barrier_probe,
                str(hub),
                "clear-mutation",
                X,
                "b" * 32,
                "0",
            ],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert wrong_attempt.returncode == 3
    assert "pending_mutation=" + X in lock_path.read_text()
    assert "pending_attempt=" + "a" * 32 in lock_path.read_text()

    uncleared = subprocess.run(
        _public_lock_command(
            hub,
            agent="barrier-owner",
            operation=X,
            child=["/bin/true"],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert uncleared.returncode == 3
    assert "external mutation remains pending" in uncleared.stderr
    assert "pending_mutation=" + X in lock_path.read_text()

    cleared = subprocess.run(
        _public_lock_command(
            hub,
            agent="barrier-owner",
            operation=X,
            child=[
                sys.executable,
                "-c",
                barrier_probe,
                str(hub),
                "clear-mutation",
                X,
                "a" * 32,
                "0",
            ],
        ),
        cwd=land.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert cleared.returncode == 0, cleared.stderr
    assert not lock_path.exists()
