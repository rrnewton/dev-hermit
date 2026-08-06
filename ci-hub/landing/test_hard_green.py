#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


PATH = Path(__file__).with_name("hard_green.py")
SPEC = importlib.util.spec_from_file_location("hard_green", PATH)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

SHA = "a" * 40


def _source(state: str, authority: str) -> dict:
    return {"state": state, "authority": authority, "sha": SHA}


def test_either_exact_sha_authority_is_positive_evidence() -> None:
    local = _source(M.STATE_PASSED, "local-full-validate")
    hosted_missing = _source(M.STATE_NO_RESULT, "github-portable+privileged")
    report = M.combine(local, hosted_missing)
    assert report["verdict"] == "HARD_GREEN"
    assert report["exit_code"] == M.EXIT_GREEN

    local_missing = _source(M.STATE_NO_RESULT, "local-full-validate")
    hosted = _source(M.STATE_PASSED, "github-portable+privileged")
    report = M.combine(local_missing, hosted)
    assert report["verdict"] == "HARD_GREEN"
    assert report["passing_authorities"] == ["github-portable+privileged"]


def test_missing_is_not_green_or_red() -> None:
    report = M.combine(
        _source(M.STATE_NO_RESULT, "local-full-validate"),
        _source(M.STATE_ERROR, "github-portable+privileged"),
    )
    assert report["verdict"] == "NO_RESULT"
    assert report["exit_code"] == M.EXIT_NO_RESULT


def test_genuine_cross_authority_contradiction_is_visible() -> None:
    report = M.combine(
        _source(M.STATE_PASSED, "local-full-validate"),
        _source(M.STATE_FAILED, "github-portable+privileged"),
    )
    assert report["verdict"] == "DISAGREEMENT"
    assert report["exit_code"] == M.EXIT_RED


def test_local_status_carries_counted_receipt(monkeypatch) -> None:
    payload = {
        "verdict": "VALIDATED",
        "qualifying_count": 1,
        "ledger": "/tmp/ledger",
        "newest_qualifying": {
            "commit": SHA,
            "profile": "full",
            "selection_mode": "full",
            "executed_tests": 27,
            "failures": 0,
        },
    }
    monkeypatch.setattr(
        M,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    result = M.local_status(SHA)
    assert result["state"] == M.STATE_PASSED
    assert result["receipt"]["executed_tests"] == 27


def test_local_known_failure_is_distinct_from_no_result(monkeypatch) -> None:
    payload = {
        "verdict": "FAILED",
        "qualifying_count": 0,
        "disqualified_count": 1,
        "newest_qualifying": None,
    }
    monkeypatch.setattr(
        M,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 3, json.dumps(payload), ""),
    )
    assert M.local_status(SHA)["state"] == M.STATE_FAILED


def test_github_lane_uses_exact_latest_named_job(monkeypatch) -> None:
    lane = M.LANES[0]

    def fake_api(endpoint: str):
        if "/runs?" in endpoint:
            return {
                "workflow_runs": [
                    {
                        "id": 10,
                        "head_sha": SHA,
                        "event": "workflow_dispatch",
                        "created_at": "2026-08-04T00:00:00Z",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "id": 11,
                        "head_sha": SHA,
                        "event": "workflow_dispatch",
                        "created_at": "2026-08-04T01:00:00Z",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 12,
                        "head_sha": "b" * 40,
                        "event": "workflow_dispatch",
                        "created_at": "2026-08-04T02:00:00Z",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ]
            }
        return {
            "jobs": [
                {
                    "id": 21,
                    "name": lane["job"],
                    "head_sha": SHA,
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(M, "_gh_json", fake_api)
    result = M.github_lane_status(M.DEFAULT_REPO, SHA, lane)
    assert result["state"] == M.STATE_PASSED
    assert result["run_id"] == 11
    assert result["job_id"] == 21


def test_hosted_green_requires_both_lanes(monkeypatch) -> None:
    states = iter((M.STATE_PASSED, M.STATE_NO_RESULT))
    monkeypatch.setattr(
        M,
        "github_lane_status",
        lambda *args, **kwargs: {"state": next(states), "authority": "fixture"},
    )
    assert M.github_status(M.DEFAULT_REPO, SHA)["state"] == M.STATE_NO_RESULT


def test_untrusted_workflow_event_does_not_supply_hard_green(monkeypatch) -> None:
    lane = M.LANES[0]
    monkeypatch.setattr(M, "_gh_json", lambda endpoint: {
        "workflow_runs": [{
            "id": 99, "head_sha": SHA, "event": "issue_comment",
            "created_at": "2026-08-04T00:00:00Z", "status": "completed",
            "conclusion": "success",
        }]
    })
    result = M.github_lane_status(M.DEFAULT_REPO, SHA, lane)
    assert result["state"] == M.STATE_NO_RESULT
    assert result["run_id"] is None


def test_lane_failure_survives_other_lane_api_error(monkeypatch) -> None:
    calls = 0

    def lane_status(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"state": M.STATE_FAILED, "authority": "github-portable", "sha": SHA}
        raise M.AuthorityError("privileged API unavailable")

    monkeypatch.setattr(M, "github_lane_status", lane_status)
    hosted = M.github_status(M.DEFAULT_REPO, SHA)
    assert hosted["state"] == M.STATE_FAILED
    report = M.combine(_source(M.STATE_PASSED, "local-full-validate"), hosted)
    assert report["verdict"] == "DISAGREEMENT"
    assert report["exit_code"] == M.EXIT_RED
