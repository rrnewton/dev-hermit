#!/usr/bin/env python3
"""Mutation brackets for the repository-derived unlanded count."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ancestry_audit as AA  # noqa: E402


def run(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def commit_file(repo: Path, path: str, contents: str, subject: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents)
    run(repo, "add", path)
    run(repo, "commit", "-m", subject)
    return run(repo, "rev-parse", "HEAD")


def init_repo(path: Path) -> tuple[Path, str]:
    path.mkdir()
    run(path, "init", "-b", "main")
    run(path, "config", "user.name", "Unlanded Count Test")
    run(path, "config", "user.email", "unlanded-count@example.invalid")
    base = commit_file(path, "base.txt", "base\n", "base")
    run(path, "update-ref", "refs/remotes/origin/main", base)
    return path, base


def make_db(path: Path, rows: list[tuple[str, str, list[str], str, list[str]]]) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table tasks (
          local_id text primary key,
          status text not null,
          tags text not null,
          title text
        );
        create table task_notes (
          task_id text not null,
          content text not null
        );
        """
    )
    for task, status, tags, title, notes in rows:
        con.execute(
            "insert into tasks(local_id,status,tags,title) values(?,?,?,?)",
            (task, status, json.dumps(tags), title),
        )
        con.executemany(
            "insert into task_notes(task_id,content) values(?,?)",
            [(task, note) for note in notes],
        )
    con.commit()
    con.close()
    return path


def one_repo(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(AA, "REPOS", {"dev-hermit": (".", None)})


def test_git_probes_disable_promisor_lazy_fetch(tmp_path: Path, monkeypatch) -> None:
    one_repo(monkeypatch, tmp_path)
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 1, "", "missing")

    monkeypatch.setattr(AA.subprocess, "run", fake_run)
    AA.git(tmp_path, "dev-hermit", "cat-file", "-e", "deadbeef^{commit}")

    assert observed["GIT_NO_LAZY_FETCH"] == "1"


def test_failed_fresh_fetch_refuses_even_when_a_stale_ref_exists(
    tmp_path: Path, monkeypatch
) -> None:
    one_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(AA, "fetch_main", lambda *_args: (False, "a" * 40))
    monkeypatch.setattr(
        sys,
        "argv",
        ["ancestry-audit", "--root", str(tmp_path), "--db", str(tmp_path / "unused.db")],
    )

    assert AA.main() == 2


def test_closed_but_unlanded_commit_is_counted_without_task_tags(
    tmp_path: Path, monkeypatch
) -> None:
    """The planted failure: CLOSED must not delete unlanded work from the count."""
    repo, _ = init_repo(tmp_path / "repo")
    unlanded = commit_file(repo, "unlanded.txt", "not on main\n", "closed work")
    db = make_db(
        tmp_path / "tasks.db",
        [
            (
                "closed-unlanded",
                "CLOSED",
                [],
                "closed task whose commit never landed",
                [f"IMPLEMENTED: SHA {unlanded} | tested from base {'b' * 40}"],
            ),
        ],
    )
    one_repo(monkeypatch, repo)

    candidates = AA.pile(db)
    results = [AA.classify(item, {}, AA.Ancestry(repo)) for item in candidates]

    assert len(candidates) == 1, "denominator is code-bearing implementation tasks"
    assert candidates[0]["task"] == "closed-unlanded"
    assert candidates[0]["status"] == "CLOSED"
    assert candidates[0]["tags"] == "[]"
    assert candidates[0]["shas"] == [unlanded]
    assert [result["bucket"] for result in results] == ["NOT-LANDED"]
    assert sum(result["bucket"] == "NOT-LANDED" for result in results) == 1


def test_task_tags_and_artifact_closures_do_not_define_the_code_denominator(
    tmp_path: Path,
) -> None:
    db = make_db(
        tmp_path / "tasks.db",
        [
            (
                "tag-proxy-only",
                "IN_PROGRESS",
                ["implemented"],
                "a tag with no implementation authority",
                ["progress: no canonical implementation note"],
            ),
            (
                "artifact-only",
                "CLOSED",
                ["implemented"],
                "research closure",
                ["CLOSURE-VERIFIED: artifact=ai_docs/research.md"],
            ),
        ],
    )

    assert AA.pile(db) == []


def test_rewritten_subject_is_landed_by_unique_stable_patch_id(
    tmp_path: Path, monkeypatch
) -> None:
    repo, base = init_repo(tmp_path / "repo")
    recorded = commit_file(repo, "feature.txt", "same patch\n", "original subject")
    run(repo, "switch", "-c", "replayed-main", base)
    replay = commit_file(repo, "feature.txt", "same patch\n", "rewritten subject")
    run(repo, "update-ref", "refs/remotes/origin/main", replay)
    one_repo(monkeypatch, repo)

    item = {
        "task": "rewritten", "status": "CLOSED", "tags": "[]",
        "title": "rewritten commit", "prs": [], "bare_prs": [],
        "shas": [recorded], "artifacts": [],
    }
    result = AA.classify(item, {}, AA.Ancestry(repo))

    assert result["bucket"] == "LANDED-BY-PATCH-ID"
    assert result["sha"] == replay


def test_subject_match_is_bound_to_the_same_stable_patch_id(
    tmp_path: Path, monkeypatch
) -> None:
    repo, base = init_repo(tmp_path / "repo")
    recorded = commit_file(repo, "feature.txt", "same patch\n", "stable subject")
    run(repo, "switch", "-c", "same-main", base)
    replay = commit_file(repo, "feature.txt", "same patch\n", "stable subject (#403)")
    run(repo, "update-ref", "refs/remotes/origin/main", replay)
    one_repo(monkeypatch, repo)

    item = {
        "task": "subject-and-patch", "status": "CLOSED", "tags": "[]",
        "title": "subject and patch match", "prs": [], "bare_prs": [],
        "shas": [recorded], "artifacts": [],
    }
    result = AA.classify(item, {}, AA.Ancestry(repo))

    assert result["bucket"] == "LANDED-BY-SUBJECT+PATCH-ID"
    assert result["sha"] == replay


def test_same_subject_with_different_patch_is_not_landing_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    repo, base = init_repo(tmp_path / "repo")
    recorded = commit_file(repo, "feature.txt", "candidate patch\n", "shared subject")
    run(repo, "switch", "-c", "different-main", base)
    different = commit_file(repo, "other.txt", "different patch\n", "shared subject")
    run(repo, "update-ref", "refs/remotes/origin/main", different)
    one_repo(monkeypatch, repo)

    item = {
        "task": "subject-collision", "status": "CLOSED", "tags": "[]",
        "title": "subject collision", "prs": [], "bare_prs": [],
        "shas": [recorded], "artifacts": [],
    }
    result = AA.classify(item, {}, AA.Ancestry(repo))

    assert result["bucket"] == "NOT-LANDED"
    assert "subject+patch-id" in result["why"]
