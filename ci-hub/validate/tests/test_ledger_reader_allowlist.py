#!/usr/bin/env python3
"""Lint: every reader of the validate run ledger must be a declared reader.

This is the second half of the `validate-ledger-qualified-rows` guard. The
accessor (`qualified_rows.py`) and its docstring state the two invariants; this
file makes them *enforceable* against code that has not been written yet.

WHY A LINT AND NOT VIGILANCE
----------------------------
The defect class is not "someone wrote a bad filter", it is "someone opened
``ignored/validate-run-ledger.jsonl`` directly and bucketed it". Both 2026-08-04
incidents were that shape:

* a ``grep | tail`` read file POSITION as event order and reported a false 13h
  schema-5 producer outage, escalated as the drain's root cause;
* an unfiltered concurrency curve showed a false "knee" that was 38 aborted runs
  sitting in the n=0 bucket.

Neither was a bug inside a hardened tool. Both bypassed the hardened tools. A
reviewer cannot reliably catch the next one by reading diffs, because the
bypassing line looks perfectly ordinary in isolation. So: enumerate the readers,
and make a NEW one fail this test until someone declares it.

THE TWO INVARIANTS a declared reader must respect
-------------------------------------------------
1. Order by event time (``finished_at``), never append/file position.
2. Drop incomplete, aborted, and zero-executed rows before bucketing or timing.

HOW TO SATISFY THIS LINT
------------------------
Preferred: do not open the ledger at all — call ``qualified_rows.qualified_rows``
for the green population, or ``flake_class.effective_result`` for failure
taxonomy, or shell out to ``ci-hub ledger qualified-rows``.

If you genuinely must read the raw file, add your path to ``DECLARED_READERS``
with a one-line reason saying which invariant handling you implement. The point
is not to forbid raw reads; it is to make every one of them a decision somebody
wrote down.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest


CI_HUB = Path(__file__).resolve().parents[2]
LEDGER_BASENAME = "validate-run-ledger"
SCANNED_SUFFIXES = {".py", ".rs", ".sh"}


def _scannable_paths() -> list[Path]:
    """Every ci-hub source file this lint looks at, enumerated through GIT.

    NOT a filesystem walk. `CI_HUB.rglob("*")` reported every file ON DISK and
    hand-rolled its exclusions (`__pycache__`, `.git`), which is the wrong shape
    twice over: the hand list can never keep up with .gitignore, and it made a
    GATE depend on checkout state instead of repository content. Demonstrated:
    planting `ci-hub/ignored/leaky_reader.py` -- machine-local scratch, matched
    by the repo-wide `ignored/` rule, invisible to `git status` -- flipped
    test_no_undeclared_ledger_readers from pass to fail at an unchanged commit.
    A gate must not consult `ignored/`; that subtree is disposable by definition.

    `--cached --others --exclude-standard` is tracked files PLUS genuinely new
    untracked ones, MINUS ignored output, so a NEW undeclared reader is still
    caught -- which is the entire purpose of this lint and must not be relaxed.
    """
    listing = subprocess.run(
        ["git", "-C", str(CI_HUB), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        CI_HUB / rel
        for rel in listing
        if Path(rel).suffix in SCANNED_SUFFIXES and (CI_HUB / rel).is_file()
    ]

# Files that legitimately name the ledger, each with the reason it is allowed.
# Adding an entry here is the declaration; the reason is the review record.
DECLARED_READERS: dict[str, str] = {
    # --- the canonical accessors themselves -------------------------------
    "validate/qualified_rows.py": "the canonical green accessor; defines both invariants",
    "lib/validate_status.rs": "authoritative Rust row parser + is_clean_full_pass",
    # --- hardened tools that qualify before aggregating -------------------
    "validate/aggregate.py": "types zero-executed as no-result; uses effective_result",
    "validate/wall_cpu_ratchet.py": "_baseline() drops result != pass before medianing",
    "history/query.py": "select_latest_workflow_attempts is timestamp-aware, not positional",
    "validate/attribute_reds.py": "RED taxonomy consumer (not a green population); keys on gates",
    "validate/anchor_select.py": "anchor selection; routes through the shared qualification",
    "validate/totality.py": "scope census needs ALL rows (a fail can also be partial), so it cannot use the pass-only accessor; orders by finished_at for chain depth and counts only result==pass runs in it, reporting the composition alongside",
    # --- producers / plumbing (write or pass the path, never bucket) ------
    "validate/scan-finalize.sh": "finalizer PRODUCER; appends rows, does not derive a view",
    "health/pr_status.py": "shells out to `ci-hub ledger qualified-rows`",
    # --- tests -------------------------------------------------------------
    "validate/test_finalize_receipt.py": "test fixture",
    "remediation/tests/test_protocol.py": (
        "test fixture: embeds a synthetic ledger path only inside planted "
        "validate-status response JSON; it never opens or orders the ledger"
    ),
    "health/tests/test_pr_status.py": "test fixture",
    "history/tests/test_history.py": "test fixture",
    "validate/tests/test_ledger_reader_allowlist.py": "this lint",
    "validate/mutation_suite.py": (
        "POPULATION CONTROL for the guard mutation suite: reads the raw ledger only "
        "to count how many rows `qualified_rows.is_qualified` still accepts, which is "
        "the check that the guard is not a kill-everything. It derives no view, does "
        "no bucketing or timing, and orders nothing -- the raw file is the correct "
        "input precisely because the control must see rows the accessor REJECTS."
    ),
    "validate/tests/test_green_class_wiring.py": (
        "population invariant for the green-class wiring: asserts the new clause "
        "in qualified_rows refuses no EXISTING producer (every real row derives "
        "HARD). Reads the real ledger read-only and skips when absent."
    ),
    "validate/test_green_class_predicate_wiring.py": (
        "test-only population invariant for the shared qualifying-receipt consumer; "
        "reads the complete live ledger without bucketing or timing, counts every "
        "parsed row, and asserts the new class clause refuses no previously "
        "qualifying producer. It skips when the ledger is absent."
    ),
}

# Readers that DO bypass the guard and are not yet fixed. Listed explicitly so
# the debt is counted rather than hidden: the lint stays green (it is landable
# today) but the set may not GROW without a deliberate edit here.
#
# Keep the reason specific enough that a successor can fix it without re-deriving
# the analysis.
KNOWN_BYPASSES: dict[str, str] = {
    "remediation/protocol.py": (
        "estimate_local_validate_cost() filters only profile=='full' and wall>0, "
        "then takes samples[-50:] — the last 50 by FILE POSITION, with no "
        "result/completeness qualification. Violates BOTH invariants. Its own "
        "basis string calls the window 'usable successful full-profile' rows; "
        "measured on the 585-row ledger the window is 23 pass / 23 fail / "
        "4 no_result raw (19/21/10 by effective_result), i.e. 54% not successful. "
        "Current numeric impact is near zero (p90 wall 700.0s contaminated vs "
        "702.0s qualified; p90 CPU identical) because the contaminating rows sit "
        "in the LOWER tail where p90 does not look — truncated median 100s and "
        "fail min 35s against a pass median of 655s. That is luck, not design: a "
        "long-running red (one is 1470s, above the 860s pass max) biases p90 "
        "UPWARD. Fix = sort by finished_at and drop non-pass rows before the "
        "p90, mirroring wall_cpu_ratchet._baseline()."
    ),
}


def code_only(text: str, suffix: str) -> str:
    """`text` with comments and docstrings blanked, so only EXECUTABLE code remains.

    THE FACT vs THE OBSERVABLE. This lint's fact is "does this file handle the
    ledger"; its observable used to be "does the basename appear anywhere in the
    bytes". A COMMENT satisfies the observable without the fact, so the lint
    flagged NON-READERS -- and the tempting remedy, adding the flagged file to
    ``DECLARED_READERS``, launders a false declaration into the registry. The
    registry then records what SILENCED the lint rather than what is true, and no
    successor can tell which entries are real. Narrowing the observable toward the
    fact is the fix that keeps the registry honest.

    A genuine handler names the path in CODE -- ``Path(...) / "…" /
    "validate-run-ledger.jsonl"``, ``os.path.join(...)``, ``const LEDGER_REL:
    &str = "…"``. A non-handler names it in prose. That distinction is what this
    strips on, and it is deliberately NOT a read-verb search: the path is
    routinely constructed in one place and opened in another, so requiring
    ``open(`` near the literal produced FALSE NEGATIVES on the canonical accessor
    itself (measured: 21 matches collapsed to 4, losing ``qualified_rows.py``).
    A lint that misses the real readers is worse than one that over-matches.

    Python docstrings are located with ``ast`` rather than a triple-quote regex,
    which would also blank ordinary multi-line string literals and silently drop
    a real path constant.
    """
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return re.sub(r"^\s*#.*$", "", text, flags=re.M)
        lines = text.splitlines()
        drop: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                if ast.get_docstring(node, clean=False) is not None and node.body:
                    first = node.body[0]
                    for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                        drop.add(ln)
        kept = ["" if i + 1 in drop else line for i, line in enumerate(lines)]
        return re.sub(r"^\s*#.*$", "", "\n".join(kept), flags=re.M)
    if suffix == ".rs":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


def _relative_reader_paths() -> set[str]:
    """Every ci-hub file naming the ledger IN CODE, as ci-hub-relative POSIX paths."""
    found: set[str] = set()
    for path in _scannable_paths():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if LEDGER_BASENAME in code_only(text, path.suffix):
            found.add(path.relative_to(CI_HUB).as_posix())
    return found


def classify(
    paths: set[str], *, present: set[str] | None = None
) -> tuple[set[str], set[str]]:
    """Split into (undeclared readers, stale declarations).

    Pure and injectable so the checker itself can be bracketed, rather than only
    being run against the live tree — a lint whose own logic is untested can pass
    for the wrong reason.

    ``paths`` are the files that reference the ledger. ``present`` are the files
    that exist at all (defaults to ``paths``). A declaration is STALE only when
    its file EXISTS but has stopped referencing the ledger. A declaration whose
    file is simply absent is NOT stale: the parent tree routinely carries another
    agent's untracked work-in-progress, and a clean checkout legitimately lacks
    those paths. Failing on absence would make this lint red for everyone whose
    checkout differs from the author's — the fastest way to get a guard disabled.
    """
    declared = set(DECLARED_READERS) | set(KNOWN_BYPASSES)
    existing = paths if present is None else present
    undeclared = paths - declared
    stale = (declared & existing) - paths
    return undeclared, stale


def _existing_scanned_paths() -> set[str]:
    """Every ci-hub source file the scanner looks at, referencing or not."""
    found: set[str] = set()
    for path in _scannable_paths():
        found.add(path.relative_to(CI_HUB).as_posix())
    return found


def test_no_undeclared_ledger_readers() -> None:
    """A NEW raw reader fails here until it is declared or routed."""
    undeclared, _ = classify(_relative_reader_paths())
    assert not undeclared, (
        "Undeclared reader(s) of the validate run ledger:\n  "
        + "\n  ".join(sorted(undeclared))
        + "\n\nPrefer qualified_rows.qualified_rows() (green population) or "
        "flake_class.effective_result (failure taxonomy). If a raw read is "
        "genuinely required, add the path to DECLARED_READERS with a reason "
        "stating how it orders by finished_at and how it drops incomplete/"
        "aborted/zero-executed rows."
    )


def test_declarations_do_not_go_stale() -> None:
    """A declaration for a file that no longer reads the ledger is removed.

    Without this the allowlist only ever grows, and a stale entry silently
    pre-authorizes a future file at that path.
    """
    _, stale = classify(_relative_reader_paths(), present=_existing_scanned_paths())
    assert not stale, (
        "Declared reader(s) that still exist but no longer reference the ledger "
        "— delete the entry:\n  " + "\n  ".join(sorted(stale))
    )


def test_known_bypasses_do_not_grow() -> None:
    """The debt set is a ratchet: it may shrink, never grow silently."""
    assert set(KNOWN_BYPASSES) <= {"remediation/protocol.py"}, (
        "A new known-bypass was added. A bypass is a defect, not a config "
        "option: fix the reader or justify the addition in review."
    )
    for path, reason in KNOWN_BYPASSES.items():
        assert len(reason) > 80, f"{path}: a bypass needs an actionable reason, not a label"


# --- brackets on the checker itself ---------------------------------------
# Running a lint proves nothing if the lint cannot fail. Plant a violation and
# confirm refusal; plant a legitimate case and confirm it is accepted.


def test_planted_undeclared_reader_is_refused() -> None:
    """POSITIVE control for the failure direction."""
    undeclared, _ = classify({"validate/qualified_rows.py", "tools/my_new_curve.py"})
    assert undeclared == {"tools/my_new_curve.py"}


def test_declared_and_bypass_paths_are_accepted() -> None:
    """NEGATIVE control: the lint is not merely refusing everything."""
    undeclared, stale = classify(set(DECLARED_READERS) | set(KNOWN_BYPASSES))
    assert undeclared == set()
    assert stale == set()


def test_declaration_is_stale_only_when_the_file_still_exists() -> None:
    """A declared file that stopped reading is stale; an ABSENT one is not.

    The second half is what keeps this lint portable across checkouts that carry
    different untracked work-in-progress.
    """
    declared_path = next(iter(DECLARED_READERS))
    # exists, but no longer references the ledger -> STALE
    _, stale = classify(set(), present={declared_path})
    assert stale == {declared_path}
    # absent entirely -> NOT stale
    _, stale_absent = classify(set(), present=set())
    assert stale_absent == set()


def test_comment_only_mention_is_not_a_reader() -> None:
    """DIRECTION (a): a file that MENTIONS the ledger but never names it in code.

    This is the misfire that motivated the change: the old matcher searched raw
    bytes, so prose tripped it, and the tempting fix was to declare the file --
    laundering a non-reader into the registry. One planted case per language,
    because each has its own comment syntax and a per-language strip.
    """
    py = '"""Docstring mentioning validate-run-ledger.jsonl in prose."""\n'
    py += "# comment naming validate-run-ledger.jsonl too\n"
    py += "VALUE = 1\n"
    assert LEDGER_BASENAME not in code_only(py, ".py")

    rs = "/// doc comment about validate-run-ledger.jsonl\n"
    rs += "// plain comment about validate-run-ledger.jsonl\n"
    rs += "/* block about validate-run-ledger.jsonl */\n"
    rs += "pub const X: i64 = 1;\n"
    assert LEDGER_BASENAME not in code_only(rs, ".rs")

    sh = "# shell comment about validate-run-ledger.jsonl\necho hi\n"
    assert LEDGER_BASENAME not in code_only(sh, ".sh")


def test_genuine_code_reference_is_still_a_reader() -> None:
    """DIRECTION (b): a file that NAMES THE PATH IN CODE is still caught.

    Without this the change would pass direction (a) by matching nothing at all,
    which is the failure mode the narrowing could most easily introduce. Each
    case is the real shape taken from a live declared reader.
    """
    # qualified_rows.py:31 shape
    py = '"""Prose."""\nP = Path(__file__) / "ignored" / "validate-run-ledger.jsonl"\n'
    assert LEDGER_BASENAME in code_only(py, ".py")
    # aggregate.py:164 shape
    py2 = 'paths.add(os.path.join(parent, "ignored", "validate-run-ledger.jsonl"))\n'
    assert LEDGER_BASENAME in code_only(py2, ".py")
    # validate_status.rs:88 shape
    rs = '/// prose\npub const LEDGER_REL: &str = "ignored/validate-run-ledger.jsonl";\n'
    assert LEDGER_BASENAME in code_only(rs, ".rs")
    # shell producer shape
    sh = '# prose\nLEDGER="$PARENT/ignored/validate-run-ledger.jsonl"\n'
    assert LEDGER_BASENAME in code_only(sh, ".sh")


def test_docstring_strip_does_not_eat_a_real_path_literal() -> None:
    """A multi-line STRING LITERAL that is not a docstring must survive.

    This is why docstrings are found with `ast` rather than a triple-quote regex:
    the regex would blank this and silently lose a real reader.
    """
    py = 'SQL = """\n  path: ignored/validate-run-ledger.jsonl\n"""\n'
    assert LEDGER_BASENAME in code_only(py, ".py")


def test_live_tree_actually_has_readers_to_check() -> None:
    """Guard against a vacuous pass if the scan ever silently finds nothing."""
    paths = _relative_reader_paths()
    assert len(paths) >= 10, f"scan found only {len(paths)} ledger readers; check the matcher"
    assert "validate/qualified_rows.py" in paths


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
