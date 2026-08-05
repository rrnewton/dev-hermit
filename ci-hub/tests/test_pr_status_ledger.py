#!/usr/bin/env python3
"""Contract tests for the pr-status ledger cross-reference (second authority).

GitHub ``statusCheckRollup`` is the ONLY input to ``green``/``gate``; the LOCAL
validate ledger is a distinct authority the GitHub view is blind to. These tests
pin the reconciliation: a PR head whose EXACT SHA carries a full-green ledger
receipt is counted as ``green_local`` and kept OUT of every red bucket, so a
GitHub ``green=0`` no longer contradicts banked local greens, and ``real_reds``
(and thus the unhealthy verdict) stay honest.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CI_HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_HUB / "health"))

import pr_status  # noqa: E402


def _rollup(state: str, name: str = "Regular tests") -> list[dict[str, object]]:
    # Minimal statusCheckRollup entry: a CheckRun with a conclusion/status.
    if state == "green":
        return [{"__typename": "CheckRun", "name": name, "status": "COMPLETED",
                 "conclusion": "SUCCESS"}]
    if state == "red":
        return [{"__typename": "CheckRun", "name": name, "status": "COMPLETED",
                 "conclusion": "FAILURE"}]
    return []  # empty -> pending


def _pr(number: int, head: str, state: str, *, mergeable: str = "MERGEABLE",
        merge_state: str = "CLEAN", check: str = "Regular tests") -> dict[str, object]:
    return {
        "number": number,
        "title": f"pr-{number}",
        "isDraft": False,
        "headRefOid": head,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "statusCheckRollup": _rollup(state, check),
    }


class LedgerCrossReferenceTests(unittest.TestCase):
    def _by_pr(self, status: pr_status.RepoStatus) -> dict[int, dict[str, object]]:
        return {p["pr"]: p for p in status.prs}

    def test_github_red_head_with_ledger_green_is_green_local_not_real_red(self) -> None:
        # A gate-red PR whose exact head is a banked full-green commit: the local
        # receipt is authoritative, so it must NOT count as a real/gate red.
        raw = [_pr(1624, "aaaa", "red", merge_state="BLOCKED",
                   check="Hermit Merge Gate")]
        st = pr_status._classify_gh_prs("rrnewton/hermit", raw, frozenset({"aaaa"}))
        self.assertEqual(st.green_local, 1)
        self.assertEqual(st.real_reds, 0)
        self.assertEqual(st.gate_reds, 0)
        self.assertEqual(st.product_reds, 0)
        self.assertFalse(st.unhealthy)
        self.assertEqual(self._by_pr(st)[1624]["red_class"], "ledger-green")
        self.assertTrue(self._by_pr(st)[1624]["ledger_green"])

    def test_github_red_head_without_ledger_green_stays_real_red(self) -> None:
        raw = [_pr(1468, "bbbb", "red", check="Regular tests")]
        st = pr_status._classify_gh_prs("rrnewton/hermit", raw, frozenset({"aaaa"}))
        self.assertEqual(st.green_local, 0)
        self.assertEqual(st.real_reds, 1)
        self.assertEqual(st.product_reds, 1)
        self.assertTrue(st.unhealthy)
        self.assertEqual(self._by_pr(st)[1468]["red_class"], "real-red")
        self.assertFalse(self._by_pr(st)[1468]["ledger_green"])

    def test_pending_head_with_ledger_green_counts_green_local(self) -> None:
        raw = [_pr(1622, "cccc", "pending", merge_state="BLOCKED")]
        st = pr_status._classify_gh_prs("rrnewton/hermit", raw, frozenset({"cccc"}))
        self.assertEqual(st.green_local, 1)
        self.assertEqual(st.pending, 1)
        self.assertEqual(st.real_reds, 0)
        self.assertTrue(self._by_pr(st)[1622]["ledger_green"])

    def test_stale_base_red_is_not_real_red_and_not_ledger_green(self) -> None:
        raw = [_pr(1200, "dddd", "red", mergeable="CONFLICTING", merge_state="DIRTY")]
        st = pr_status._classify_gh_prs("rrnewton/hermit", raw, frozenset())
        self.assertEqual(st.real_reds, 0)
        self.assertEqual(st.green_local, 0)
        self.assertEqual(self._by_pr(st)[1200]["red_class"], "stale-base")

    def test_empty_banked_set_is_prior_github_only_behavior(self) -> None:
        # No ledger available -> green_local stays 0, red classification unchanged.
        raw = [_pr(1, "e1", "red", check="Regular tests"),
               _pr(2, "e2", "green"),
               _pr(3, "e3", "pending")]
        st = pr_status._classify_gh_prs("rrnewton/hermit", raw, frozenset())
        self.assertEqual(st.green_local, 0)
        self.assertEqual(st.green, 1)
        self.assertEqual(st.real_reds, 1)
        self.assertEqual(st.pending, 1)


if __name__ == "__main__":
    unittest.main()
