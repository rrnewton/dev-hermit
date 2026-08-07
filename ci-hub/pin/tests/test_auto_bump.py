#!/usr/bin/env python3
"""Tests for the Reverie auto-safe-bump.

The bump itself is the easy half. Every test here is about the SAFE half: that a
bump which cannot complete cleanly leaves the tree byte-identical to how it was
found, and that nothing short of all-or-nothing is accepted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_bump  # noqa: E402

OLD = "d" * 40
NEW = "0" * 39 + "a"
OTHER = "b" * 40


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature hermit checkout: several tracked manifests + a lockfile.

    Shaped like the real thing — revisions spread across nested manifests and a
    lockfile that spells the rev differently (`?rev=`) — because the partial
    application hazard lives precisely in "some files use the other spelling".
    """
    r = tmp_path / "hermit"
    (r / "scripts").mkdir(parents=True)
    (r / "scripts" / "check-reverie-pin.rs").write_text("// canonical tool\n")
    (r / "Cargo.toml").write_text(
        f'[dependencies]\nreverie = {{ git = "{auto_bump.REVERIE_REMOTE}", rev = "{OLD}" }}\n'
        f'liteinst2 = {{ git = "https://github.com/rrnewton/liteinst2", rev = "{OTHER}" }}\n')
    for sub in ("detcore", "detcore-model", "hermit-cli"):
        (r / sub).mkdir()
        # Realistic: the reverie remote appears ON the rev line, and a
        # liteinst2 pin sits alongside it in the SAME file -- which is how the
        # real hermit manifests are shaped.
        (r / sub / "Cargo.toml").write_text(
            f'[dependencies]\n'
            f'reverie-core = {{ git = "{auto_bump.REVERIE_REMOTE}", rev = "{OLD}" }}\n'
            f'reverie-ptrace = {{ git = "{auto_bump.REVERIE_REMOTE}", rev = "{OLD}" }}\n'
            f'liteinst2 = {{ git = "https://github.com/rrnewton/liteinst2", rev = "{OTHER}" }}\n')
    (r / "Cargo.lock").write_text(
        f'[[package]]\nsource = "git+{auto_bump.REVERIE_REMOTE}?rev={OLD}#dddddddd"\n')
    (r / "README.md").write_text(f"mentions {OLD} but is not Cargo metadata\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return r


def snapshot(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes()
            for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.parts}


# ---- derivation is canonical, never a hand list ----------------------------

def test_entry_set_is_derived_from_tracked_cargo_files_only(repo: Path):
    entries = auto_bump.derive_entries(repo)
    names = {str(e.path.relative_to(repo)) for e in entries}
    assert "Cargo.toml" in names and "Cargo.lock" in names
    assert "detcore/Cargo.toml" in names
    assert "README.md" not in names, "a rev in prose is not a pin entry"
    # 1 root + 3 nested x2 + 1 lock = 8 REVERIE entries across 5 files.
    # The four liteinst2 revs in those same files are excluded by the per-line
    # predicate; counting them would be the real bug this fixture now guards.
    assert sum(len(e.revs) for e in entries) == 8


def test_an_untracked_manifest_is_out_of_scope(repo: Path):
    (repo / "scratch").mkdir()
    (repo / "scratch" / "Cargo.toml").write_text(f'rev = "{OTHER}"\n')
    names = {str(e.path.relative_to(repo)) for e in auto_bump.derive_entries(repo)}
    assert "scratch/Cargo.toml" not in names, "git ls-files defines the scope"


def test_a_neighbouring_liteinst2_pin_is_never_rewritten(repo: Path):
    """THE BUG THE REAL CHECKOUT CAUGHT, and hermetic fixtures had missed.

    hermit pins liteinst2 by git rev on lines adjacent to the Reverie ones, in
    the same manifests. An entry matcher scoped by FILE rather than by LINE
    rewrites the liteinst2 rev to the Reverie tip: a silent, plausible-looking
    corruption of a different dependency that no Reverie-focused check would
    notice. Measured on the real tree: 48 rev entries over 10 files carrying TWO
    distinct revisions.
    """
    auto_bump.auto_safe_bump(repo, target=NEW, validate=lambda: True)
    for manifest in repo.rglob("Cargo.toml"):
        text = manifest.read_text()
        for line in text.splitlines():
            if "liteinst2" in line:
                assert OTHER in line, f"liteinst2 pin was rewritten in {manifest}"
                assert NEW not in line


# ---- no floating refs ------------------------------------------------------

@pytest.mark.parametrize("bad", ["main", "0" * 39, "HEAD", "0" * 41, "refs/heads/main"])
def test_a_non_40_hex_target_is_refused_before_anything_is_touched(repo: Path, bad: str):
    before = snapshot(repo)
    with pytest.raises(auto_bump.BumpRefused):
        auto_bump.auto_safe_bump(repo, target=bad)
    assert snapshot(repo) == before, "a refused target must not touch the tree"


# ---- the positive path -----------------------------------------------------

def test_clean_bump_moves_every_entry_and_leaves_prose_alone(repo: Path):
    report = auto_bump.auto_safe_bump(repo, target=NEW, validate=lambda: True)
    assert report.refused_reason is None
    assert report.entries_before == report.entries_after == 8
    assert report.validated is True
    for e in auto_bump.derive_entries(repo):
        assert all(r == NEW for r in e.revs), f"{e.path} not fully bumped"
    assert OLD in (repo / "README.md").read_text(), "prose must be untouched"


def test_bump_is_idempotent(repo: Path):
    auto_bump.auto_safe_bump(repo, target=NEW, validate=lambda: True)
    second = auto_bump.auto_safe_bump(repo, target=NEW, validate=lambda: True)
    assert second.refused_reason is None
    assert second.changed_files == ()


# ---- ATOMICITY: the 13-of-21 hazard ---------------------------------------

def test_a_partial_application_is_refused_and_fully_rolled_back(repo: Path):
    """THE hazard, planted directly.

    A bump that updates some files and not others leaves the tree claiming two
    Reverie revisions at once, and every consumer reads whichever it happens to
    open. So a partial apply must be detected over the whole derived set and
    undone completely.
    """
    before = snapshot(repo)

    def partial(r: Path, target: str, entries):
        for entry in entries[:2]:            # update only the first two files
            text = entry.path.read_text()
            for rev in set(entry.revs):
                text = text.replace(rev, target)
            entry.path.write_text(text)

    with pytest.raises(auto_bump.BumpRefused, match="PARTIAL APPLICATION"):
        auto_bump.auto_safe_bump(repo, target=NEW, apply_fn=partial, validate=lambda: True)

    assert snapshot(repo) == before, "rollback must restore BYTES, not approximate them"
    assert {r for e in auto_bump.derive_entries(repo) for r in e.revs} == {OLD}


def test_an_apply_that_raises_midway_is_rolled_back(repo: Path):
    before = snapshot(repo)

    def explode(r: Path, target: str, entries):
        entry = entries[0]
        entry.path.write_text(entry.path.read_text().replace(OLD, target))
        raise RuntimeError("writer died mid-bump")

    with pytest.raises(auto_bump.BumpRefused):
        auto_bump.auto_safe_bump(repo, target=NEW, apply_fn=explode)
    assert snapshot(repo) == before


def test_an_apply_that_drops_an_entry_is_refused(repo: Path):
    """Rewriting in place is required; silently deleting a pin site is not a bump."""
    before = snapshot(repo)

    def delete_one(r: Path, target: str, entries):
        for entry in entries:
            text = entry.path.read_text()
            for rev in set(entry.revs):
                text = text.replace(rev, target)
            entry.path.write_text(text)
        (r / "detcore" / "Cargo.toml").write_text("[dependencies]\n")  # entries vanish

    with pytest.raises(auto_bump.BumpRefused, match="ENTRY COUNT CHANGED"):
        auto_bump.auto_safe_bump(repo, target=NEW, apply_fn=delete_one)
    assert snapshot(repo) == before


# ---- the green gate --------------------------------------------------------

def test_a_failing_validate_rolls_the_bump_back(repo: Path):
    """A bump that cannot prove itself green does not land."""
    before = snapshot(repo)
    with pytest.raises(auto_bump.BumpRefused, match="validation FAILED"):
        auto_bump.auto_safe_bump(repo, target=NEW, validate=lambda: False)
    assert snapshot(repo) == before


def test_a_validate_that_raises_is_treated_as_failure_not_success(repo: Path):
    """Fail-closed: an exception is not 'probably fine'."""
    before = snapshot(repo)

    def boom():
        raise RuntimeError("validate harness died")

    with pytest.raises(auto_bump.BumpRefused):
        auto_bump.auto_safe_bump(repo, target=NEW, validate=boom)
    assert snapshot(repo) == before


def test_validate_runs_against_the_BUMPED_tree_not_the_old_one(repo: Path):
    """The thing being validated must be the tree that would land."""
    seen: list[set[str]] = []

    def observe() -> bool:
        seen.append({r for e in auto_bump.derive_entries(repo) for r in e.revs})
        return True

    auto_bump.auto_safe_bump(repo, target=NEW, validate=observe)
    assert seen == [{NEW}], "validate saw the pre-bump revisions"


# ---- refusing to succeed vacuously ----------------------------------------

def test_a_repo_with_no_entries_is_refused_rather_than_reported_clean(tmp_path: Path):
    r = tmp_path / "empty"
    (r / "scripts").mkdir(parents=True)
    (r / "scripts" / "check-reverie-pin.rs").write_text("// canonical\n")
    (r / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    with pytest.raises(auto_bump.BumpRefused, match="vacuously"):
        auto_bump.auto_safe_bump(r, target=NEW)


def test_missing_canonical_tool_is_refused(tmp_path: Path):
    """The scope must come from the canonical tool's repo, not from guesswork."""
    r = tmp_path / "nocanon"
    r.mkdir()
    (r / "Cargo.toml").write_text(f'rev = "{OLD}"\n')
    _git(r, "init", "-q")
    with pytest.raises(auto_bump.BumpRefused, match="canonical"):
        auto_bump.auto_safe_bump(r, target=NEW)


# ---- tip resolution --------------------------------------------------------

def test_tip_resolution_rejects_a_non_sha_answer():
    with pytest.raises(auto_bump.BumpRefused):
        auto_bump.resolve_reverie_tip(runner=lambda cmd: "not-a-sha\trefs/heads/main\n")


def test_tip_resolution_accepts_a_40_hex_answer():
    assert auto_bump.resolve_reverie_tip(
        runner=lambda cmd: f"{NEW}\trefs/heads/main\n") == NEW
