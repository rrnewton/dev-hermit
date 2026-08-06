#!/usr/bin/env python3
"""Plant-a-violation brackets for the `.gitmodules` hazard ratchet.

The cold-clone-verify fix was applied but unguarded, so it could regress
silently. These tests are BOTH the bracket and the wiring: running in the ci-hub
suite is what makes the lint fire on the live tree rather than only on demand.

Every hazard gets a planted violation AND the live tree gets a positive control,
because a lint that has only ever been run on clean input has not been shown to
detect anything.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gitmodules_lint as gl  # noqa: E402

# parents: [0]=tests [1]=validate [2]=ci-hub [3]=dev-hermit parent.
ROOT = Path(__file__).resolve().parents[3]

CLEAN = """\
[submodule "hermit"]
\tpath = hermit
\turl = https://github.com/rrnewton/hermit.git
\tupdate = checkout
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".gitmodules"
    path.write_text(text)
    return path


# --- POSITIVE CONTROL --------------------------------------------------------


def test_a_clean_gitmodules_has_no_hazard(tmp_path: Path) -> None:
    report = gl.lint_file(_write(tmp_path, CLEAN))
    assert report["violations"] == []
    assert report["entries"] == 1


def test_implicit_update_default_is_reported_but_not_a_hazard(tmp_path: Path) -> None:
    """Absence of `update` IS `checkout` per git-submodule(1); flagging it would
    make the lint fire on a non-defect."""
    text = CLEAN.replace("\tupdate = checkout\n", "")
    report = gl.lint_file(_write(tmp_path, text))
    assert report["violations"] == []
    assert report["implicit_update_checkout"] == ["hermit"]


# --- PLANTED VIOLATIONS ------------------------------------------------------


def test_planted_shallow_is_caught(tmp_path: Path) -> None:
    """The regression the ratchet exists for: a shallow submodule makes
    cold-clone verify pass by removing the history it would have checked."""
    report = gl.lint_file(_write(tmp_path, CLEAN + "\tshallow = true\n"))
    assert report["violations"] == [{"submodule": "hermit", "hazard": "shallow"}]


def test_planted_shallow_spellings_are_all_caught(tmp_path: Path) -> None:
    for value in ("true", "yes", "on", "1", "TRUE", " true "):
        report = gl.lint_file(_write(tmp_path, CLEAN + f"\tshallow = {value}\n"))
        assert report["violations"], f"shallow = {value!r} was not caught"


def test_shallow_false_is_not_a_hazard(tmp_path: Path) -> None:
    """Guard against flags-everything: an explicit `shallow = false` is the
    desired state, not a violation."""
    report = gl.lint_file(_write(tmp_path, CLEAN + "\tshallow = false\n"))
    assert report["violations"] == []


def test_planted_update_none_is_caught(tmp_path: Path) -> None:
    """`update = none` leaves an empty directory where a consumer expects a
    tree -- the failure the 2026-08-02 checked-out-by-default policy retired."""
    text = CLEAN.replace("\tupdate = checkout\n", "\tupdate = none\n")
    report = gl.lint_file(_write(tmp_path, text))
    assert report["violations"] == [{"submodule": "hermit", "hazard": "update-none"}]


def test_planted_branch_field_is_caught(tmp_path: Path) -> None:
    """The parent guide forbids `branch =`: it turns an exact gitlink into a
    moving target."""
    report = gl.lint_file(_write(tmp_path, CLEAN + "\tbranch = main\n"))
    assert report["violations"] == [{"submodule": "hermit", "hazard": "branch"}]


def test_multiple_hazards_are_all_reported_not_just_the_first(tmp_path: Path) -> None:
    text = CLEAN.replace("\tupdate = checkout\n", "\tupdate = none\n")
    report = gl.lint_file(_write(tmp_path, text + "\tshallow = true\n\tbranch = main\n"))
    assert {v["hazard"] for v in report["violations"]} == {"shallow", "update-none", "branch"}


def test_exit_code_is_nonzero_when_a_hazard_is_planted(tmp_path: Path) -> None:
    """The CLI contract, not just the library: a consumer gates on the exit."""
    planted = _write(tmp_path, CLEAN + "\tshallow = true\n")
    assert gl.main(["--file", str(planted)]) == 1
    clean = tmp_path / "clean"
    clean.mkdir()
    assert gl.main(["--file", str(_write(clean, CLEAN))]) == 0


# --- THE WIRING: the live tree ----------------------------------------------


def test_live_tree_has_no_gitmodules_hazard() -> None:
    """This is the ratchet. It runs on every ci-hub suite invocation, so the
    cold-clone-verify fix can no longer regress silently."""
    paths = gl.discover(ROOT)
    assert paths, "no .gitmodules discovered -- the ratchet would be vacuous"
    offenders = []
    for path in paths:
        offenders.extend(
            f"{path}: {v['hazard']} in [submodule \"{v['submodule']}\"]"
            for v in gl.lint_file(path)["violations"]
        )
    assert offenders == [], "\n  ".join(["gitmodules hazards found:"] + offenders)


def test_discovery_actually_reaches_the_submodules() -> None:
    """Without this, the ratchet above could pass by discovering nothing.

    Discovery must find the parent's own .gitmodules and at least one
    submodule's, or a hazard introduced in a submodule would go unseen.
    """
    found = {p.relative_to(ROOT).as_posix() for p in gl.discover(ROOT)}
    assert ".gitmodules" in found, found
    assert any(p != ".gitmodules" for p in found), f"only the parent was scanned: {found}"
