#!/usr/bin/env python3
"""Negative tests for the landing preflight — each check must REFUSE its real defect.

The task's acceptance bar is "each check has a NEGATIVE test proving it refuses
the bad case". Every negative here is modelled on the incident that produced the
rule, not on an invented input, and every negative is paired with a positive
control: a check that refuses everything is as useless as one that refuses
nothing, and only the pair distinguishes them.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight as P  # noqa: E402

HEAD = "a" * 40
OTHER = "b" * 40


# --- 1. the handed SHA is a cache -------------------------------------------


def test_stale_handed_sha_is_refused() -> None:
    """THE INCIDENT: four handed SHAs went stale in one night, one quoted into
    agent instructions for hours after main advanced twice."""
    r = P.check_sha_is_current(HEAD, OTHER)
    assert r.verdict == P.REFUSE
    assert "STALE" in r.reason
    assert r.detail["live_head"] == OTHER


def test_current_handed_sha_passes() -> None:
    assert P.check_sha_is_current(HEAD, HEAD).verdict == P.PASS


def test_unresolvable_head_is_unknown_not_pass() -> None:
    """An unanswerable question must not read as a satisfied one."""
    r = P.check_sha_is_current(HEAD, None)
    assert r.verdict == P.UNKNOWN
    assert r.ok is False, "UNKNOWN must not count as success"


def test_a_prefix_sha_is_refused_rather_than_compared() -> None:
    assert P.check_sha_is_current("a" * 12, HEAD).verdict == P.REFUSE


def test_case_difference_alone_is_not_staleness() -> None:
    """Guard against flags-everything: hex case is not a change of commit."""
    assert P.check_sha_is_current(HEAD.upper(), HEAD).verdict == P.PASS


# --- 2. a green must carry a nonzero executed count -------------------------


ZERO_LOG = """\
   Compiling detcore v0.1.0
    Finished test profile [unoptimized + debuginfo]
     Running unittests src/lib.rs (target/debug/deps/detcore-1234)

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""

REAL_LOG = """\
     Running unittests src/lib.rs (target/debug/deps/detcore-1234)

running 36 tests
test result: ok. 36 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""


def test_zero_executed_tests_is_refused() -> None:
    """THE INCIDENT: --features gating -- build succeeds, target runs, ZERO tests
    execute, SUCCESS reported. Proven by planting a feature-gated fixture."""
    r = P.check_green_carries_executed_tests(ZERO_LOG)
    assert r.verdict == P.REFUSE
    assert "NO-RESULT WEARING A SUCCESS BADGE" in r.reason
    assert r.detail["executed_tests"] == 0


def test_a_real_green_passes() -> None:
    r = P.check_green_carries_executed_tests(REAL_LOG)
    assert r.verdict == P.PASS
    assert r.detail["executed_tests"] == 36


def test_absent_log_is_refused() -> None:
    """CLOSES THE RECORDED KNOWN GAP. A check that only looked for `N == 0`
    passes an absent log, because there is no zero in it to find."""
    r = P.check_green_carries_executed_tests(None, log_path="/nope.log")
    assert r.verdict == P.REFUSE
    assert "ABSENT" in r.reason


def test_empty_log_is_refused() -> None:
    """The other half of the same recorded gap."""
    for text in ("", "   \n\n  "):
        r = P.check_green_carries_executed_tests(text)
        assert r.verdict == P.REFUSE, repr(text)
        assert "EMPTY" in r.reason


def test_log_with_no_count_at_all_is_refused() -> None:
    """A build log that never ran a harness cannot certify a green."""
    r = P.check_green_carries_executed_tests("Compiling foo\nFinished dev profile\n")
    assert r.verdict == P.REFUSE
    assert "NO executed-test count" in r.reason


def test_counts_are_summed_across_harness_sections() -> None:
    """Multi-crate runs: the total is what matters, and a partial zero must not
    hide behind a nonzero sibling in a way that loses the count."""
    log = REAL_LOG + "\nrunning 4 tests\ntest result: ok. 4 passed\n"
    r = P.check_green_carries_executed_tests(log)
    assert r.verdict == P.PASS
    assert r.detail["executed_tests"] == 40
    assert r.detail["sections"] == 2


# --- 3. landing is ancestry, never the MERGED flag --------------------------


def _anc(true_for: set[str]):
    return lambda sha: sha in true_for


def test_merged_flag_without_ancestry_is_refused() -> None:
    """THE INCIDENT: ~12 PRs orphaned by a later force-push on 2026-08-03 --
    still flagged MERGED, replay SHA no longer reachable."""
    r = P.check_landed_by_ancestry(
        pr_state="MERGED", merge_commit_oid="c" * 40,
        is_ancestor=_anc(set()), fetched_fresh=True)
    assert r.verdict == P.REFUSE
    assert "orphaned" in r.reason


def test_merged_with_ancestry_passes() -> None:
    oid = "c" * 40
    r = P.check_landed_by_ancestry(
        pr_state="MERGED", merge_commit_oid=oid,
        is_ancestor=_anc({oid}), fetched_fresh=True)
    assert r.verdict == P.PASS


def test_stale_remote_is_unknown_not_pass() -> None:
    """A stale ref answers about the past."""
    oid = "c" * 40
    r = P.check_landed_by_ancestry(
        pr_state="MERGED", merge_commit_oid=oid,
        is_ancestor=_anc({oid}), fetched_fresh=False)
    assert r.verdict == P.UNKNOWN
    assert r.ok is False


def test_merged_without_a_merge_commit_is_refused() -> None:
    r = P.check_landed_by_ancestry(
        pr_state="MERGED", merge_commit_oid=None,
        is_ancestor=_anc(set()), fetched_fresh=True)
    assert r.verdict == P.REFUSE
    assert "not landing evidence" in r.reason


def test_the_check_never_consults_the_pr_head() -> None:
    """THE INCIDENT: testing ancestry on the PR HEAD read 79 unlanded when 46 had
    landed, because a rebase replay makes the head NEVER an ancestor.

    Proven structurally: ancestry is TRUE for the merge commit and FALSE for the
    head, and the verdict follows the merge commit.
    """
    oid, head = "c" * 40, "d" * 40
    r = P.check_landed_by_ancestry(
        pr_state="MERGED", merge_commit_oid=oid, pr_head=head,
        is_ancestor=_anc({oid}), fetched_fresh=True)
    assert r.verdict == P.PASS, "must not have been decided on the head"
    asked: list[str] = []

    def spy(sha):
        asked.append(sha)
        return sha == oid

    P.check_landed_by_ancestry(pr_state="MERGED", merge_commit_oid=oid, pr_head=head,
                               is_ancestor=spy, fetched_fresh=True)
    assert asked == [oid], f"the head must never be tested for ancestry: {asked}"


# --- 4. the reverie patch override ------------------------------------------


PATCH_DIFF = '''\
diff --git a/Cargo.toml b/Cargo.toml
--- a/Cargo.toml
+++ b/Cargo.toml
@@ -41,6 +41,10 @@
+[patch."https://github.com/rrnewton/reverie.git"]
+reverie = { path = "../reverie" }
'''


def test_added_reverie_patch_override_is_refused() -> None:
    """THE TRAP: live in worktrees/250-delegate/hermit Cargo.toml L44-53."""
    r = P.check_no_uncommitted_patch_override(PATCH_DIFF)
    assert r.verdict == P.REFUSE


def test_unrelated_diff_passes() -> None:
    assert P.check_no_uncommitted_patch_override(
        "diff --git a/src/x.rs b/src/x.rs\n+fn x() {}\n").verdict == P.PASS


def test_a_removed_override_is_not_flagged() -> None:
    """Keyed on ADDED lines: deleting an override is the fix, not the defect."""
    removal = PATCH_DIFF.replace("\n+[patch", "\n-[patch").replace(
        "\n+reverie =", "\n-reverie =")
    assert P.check_no_uncommitted_patch_override(removal).verdict == P.PASS


# --- 5. the byte-identical branch -------------------------------------------


def test_byte_identical_branch_is_refused() -> None:
    """THE TRAP: live on #355 -- work opened that already existed verbatim."""
    r = P.check_no_byte_identical_branch(
        candidate_tree="t1", remote_trees={"fix/a": "t1", "other": "t2"})
    assert r.verdict == P.REFUSE
    assert r.detail["branches"] == ["fix/a"]


def test_distinct_tree_passes() -> None:
    assert P.check_no_byte_identical_branch(
        candidate_tree="t9", remote_trees={"fix/a": "t1"}).verdict == P.PASS


# --- the gate as a whole -----------------------------------------------------


def test_unknown_blocks_the_gate(tmp_path: Path) -> None:
    """UNKNOWN must not be launderable into success by the exit code."""
    log = tmp_path / "v.log"
    log.write_text(REAL_LOG)
    rc = P.main(["--sha", HEAD, "--log", str(log), "--no-network"])
    assert rc == 1, "an unresolved head must block, not pass"


def test_all_pass_exits_zero(tmp_path: Path) -> None:
    """POSITIVE CONTROL for the gate itself: it can actually succeed."""
    log = tmp_path / "v.log"
    log.write_text(REAL_LOG)
    rc = P.main(["--sha", HEAD, "--live-head", HEAD, "--log", str(log), "--no-network"])
    assert rc == 0


def test_zero_test_log_fails_the_gate(tmp_path: Path) -> None:
    log = tmp_path / "v.log"
    log.write_text(ZERO_LOG)
    rc = P.main(["--sha", HEAD, "--live-head", HEAD, "--log", str(log), "--no-network"])
    assert rc == 1
