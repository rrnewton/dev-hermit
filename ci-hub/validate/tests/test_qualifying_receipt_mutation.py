#!/usr/bin/env python3
"""Mutation and call-site proof for the one Rust receipt verifier."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CI_HUB = ROOT / "ci-hub" / "ci-hub"
PREDICATE = ROOT / "ci-hub/validate/qualifying-receipt.json"
PIN = "d" * 40


def _fixture(tmp_path: Path) -> tuple[Path, str, Path, dict[str, str]]:
    repo = tmp_path / "hermit"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "ci test"], check=True)
    (repo / "Cargo.toml").write_text(
        '[package]\nname="fixture"\nversion="0.1.0"\n[dependencies]\n'
        f'reverie={{git="https://github.com/rrnewton/reverie.git",rev="{PIN}"}}\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "Cargo.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    log = tmp_path / "validate.log"
    log.write_text("fixture validation log\n")
    log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
    row = {
        "schema_version": 6,
        "commit": sha,
        "commit_anchored": True,
        "tree_dirty": False,
        "selection_mode": "full",
        "profile": "full",
        "result": "pass",
        "failures": 0,
        "executed_tests": 740,
        "filtered_tests": 3,
        "finished_at": "2026-08-05T00:10:00Z",
        "log_file": str(log),
        "source_log_sha256": log_digest,
        "coverage": {
            "planned_test_nodes": 4,
            "executed_test_nodes": 4,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
        "reverie_binding": {
            "repository": "rrnewton/reverie",
            "ref": "refs/heads/main",
            "pinned_sha": PIN,
            "resolved_sha": PIN,
        },
    }
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(row) + "\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    proxy = bindir / "with-proxy"
    proxy.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == git && $2 == ls-remote ]]; then\n"
        f"  printf '%s\\trefs/heads/main\\n' {PIN}\n"
        "  exit 0\n"
        "fi\n"
        "exec \"$@\"\n"
    )
    proxy.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return repo, sha, ledger, env


def _validate(repo: Path, sha: str, ledger: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CI_HUB), "validate-status", "--sha", sha, "--ledger", str(ledger),
         "--hermit-repo", str(repo), "--repo", "rrnewton/hermit", "--json"],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _bulk(repo: Path, ledger: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CI_HUB), "ledger", "qualified-rows", "--ledger", str(ledger),
         "--hermit-repo", str(repo), "--repo", "rrnewton/hermit"],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _tightened_env(tmp_path: Path, env: dict[str, str]) -> dict[str, str]:
    tightened = json.loads(PREDICATE.read_text())
    tightened["require"]["executed_tests_min"] = 999_999
    tightened_path = tmp_path / "tight.json"
    tightened_path.write_text(json.dumps(tightened))
    moved = dict(env)
    moved["QUALIFYING_RECEIPT_PREDICATE"] = str(tightened_path)
    return moved


def _replace_binding(ledger: Path, binding: dict[str, str] | None) -> None:
    row = json.loads(ledger.read_text())
    if binding is None:
        row.pop("reverie_binding")
    else:
        row["reverie_binding"] = binding
    ledger.write_text(json.dumps(row) + "\n")


def test_live_validate_status_accepts_a_bound_green(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    live = _validate(repo, sha, ledger, env)
    assert live.returncode == 0
    assert json.loads(live.stdout)["qualifying_count"] == 1


def test_live_bulk_view_accepts_the_same_bound_green(tmp_path: Path) -> None:
    repo, _sha, ledger, env = _fixture(tmp_path)
    bulk_live = _bulk(repo, ledger, env)
    assert len(bulk_live.stdout.splitlines()) == 1


def test_tightened_predicate_refuses_validate_status(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    env = _tightened_env(tmp_path, env)
    moved = _validate(repo, sha, ledger, env)
    assert moved.returncode == 4
    assert json.loads(moved.stdout)["qualifying_count"] == 0


def test_tightened_predicate_removes_the_bulk_row(tmp_path: Path) -> None:
    repo, _sha, ledger, env = _fixture(tmp_path)
    env = _tightened_env(tmp_path, env)
    bulk_moved = _bulk(repo, ledger, env)
    assert bulk_moved.returncode == 0
    assert bulk_moved.stdout == ""


def test_missing_binding_refuses_every_executable_view(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    _replace_binding(ledger, None)
    verdict = _validate(repo, sha, ledger, env)
    bulk = _bulk(repo, ledger, env)
    assert verdict.returncode == 4
    assert json.loads(verdict.stdout)["qualifying_count"] == 0
    assert bulk.returncode == 0
    assert bulk.stdout == ""


def test_tampered_binding_refuses_every_executable_view(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    _replace_binding(
        ledger,
        {
            "repository": "rrnewton/reverie",
            "ref": "refs/heads/main",
            "pinned_sha": PIN,
            "resolved_sha": "e" * 40,
        },
    )
    verdict = _validate(repo, sha, ledger, env)
    bulk = _bulk(repo, ledger, env)
    assert verdict.returncode == 4
    assert json.loads(verdict.stdout)["qualifying_count"] == 0
    assert bulk.returncode == 0
    assert bulk.stdout == ""


def test_receipt_repo_cannot_select_the_trusted_target(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    row = json.loads(ledger.read_text())
    row["repo"] = "reverie"
    ledger.write_text(json.dumps(row) + "\n")
    verdict = _validate(repo, sha, ledger, env)
    bulk = _bulk(repo, ledger, env)
    assert verdict.returncode == 4
    assert json.loads(verdict.stdout)["qualifying_count"] == 0
    assert bulk.stdout == ""


def test_explicit_reverie_target_accepts_full_sha_and_refuses_prefix(tmp_path: Path) -> None:
    _repo, sha, ledger, env = _fixture(tmp_path)
    row = json.loads(ledger.read_text())
    row["repo"] = "reverie"
    row.pop("reverie_binding")
    ledger.write_text(json.dumps(row) + "\n")
    command = [
        str(CI_HUB), "ledger", "qualified-rows", "--ledger", str(ledger),
        "--repo", "rrnewton/reverie",
    ]
    full = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    assert full.returncode == 0
    assert len(full.stdout.splitlines()) == 1

    row["commit"] = sha[:12]
    ledger.write_text(json.dumps(row) + "\n")
    short = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    assert short.returncode == 0
    assert short.stdout == ""


def test_all_authoritative_non_rust_consumers_delegate_to_cli() -> None:
    sources = {
        "immutable verifier": ROOT / "ci-hub/validation/verify_receipt.sh",
        "history": ROOT / "ci-hub/history/query.py",
        "pr status": ROOT / "ci-hub/health/pr_status.py",
    }
    for name, path in sources.items():
        source = path.read_text()
        assert "qualifying_receipt.row_qualifies" not in source, name
        assert "validate-status" in source or "qualified-rows" in source, name

    publisher = (ROOT / "ci-hub/validation/publish_receipt.py").read_text()
    assert "selected-receipt-sha256" in publisher
    assert "validate-status" not in publisher
    assert "--add-label" not in publisher
