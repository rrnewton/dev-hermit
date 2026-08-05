#!/usr/bin/env python3
"""Mutation and call-site proof for the one Rust receipt verifier."""

from __future__ import annotations

import json
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
         "--hermit-repo", str(repo), "--json"],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _bulk(repo: Path, ledger: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CI_HUB), "ledger", "qualified-rows", "--ledger", str(ledger),
         "--hermit-repo", str(repo)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_live_and_tightened_predicate_move_every_executable_view(tmp_path: Path) -> None:
    repo, sha, ledger, env = _fixture(tmp_path)
    live = _validate(repo, sha, ledger, env)
    bulk_live = _bulk(repo, ledger, env)
    assert live.returncode == 0
    assert json.loads(live.stdout)["qualifying_count"] == 1
    assert len(bulk_live.stdout.splitlines()) == 1

    tightened = json.loads(PREDICATE.read_text())
    tightened["require"]["executed_tests_min"] = 999_999
    tightened_path = tmp_path / "tight.json"
    tightened_path.write_text(json.dumps(tightened))
    env["QUALIFYING_RECEIPT_PREDICATE"] = str(tightened_path)
    moved = _validate(repo, sha, ledger, env)
    bulk_moved = _bulk(repo, ledger, env)
    assert moved.returncode == 4
    assert json.loads(moved.stdout)["qualifying_count"] == 0
    assert bulk_moved.returncode == 0
    assert bulk_moved.stdout == ""


def test_all_authoritative_non_rust_consumers_delegate_to_cli() -> None:
    sources = {
        "publisher": ROOT / "ci-hub/validation/publish_receipt.py",
        "immutable verifier": ROOT / "ci-hub/validation/verify_receipt.sh",
        "history": ROOT / "ci-hub/history/query.py",
        "pr status": ROOT / "ci-hub/health/pr_status.py",
    }
    for name, path in sources.items():
        source = path.read_text()
        assert "qualifying_receipt.row_qualifies" not in source, name
        assert "validate-status" in source or "qualified-rows" in source, name
