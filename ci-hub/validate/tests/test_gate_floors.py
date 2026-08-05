#!/usr/bin/env python3
"""Tests for ci-hub/validate/gate_floors.py.

The tool enumerates every rebase-base floor and derives the EFFECTIVE (newest)
floor from the branch's first-parent history, or checks whether a head clears
ALL floors. git is mocked at the seam (first_parent / is_ancestor) so no test
touches the network or a real checkout. Every required behaviour is bracketed
from BOTH sides, as the owning task demands:
  * effective floor = the NEWEST (closest-to-tip) of the enumerated floors;
  * a floor OFF first-parent history -> REFUSE (empty-intersection guard);
  * --head: a PRE-floor head is refused even when it would be "green"; N
    POST-floor heads pass, N stated.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_floors as gf

MERGE_GATE = "c369be3ff8e2c751a313b27979fa8f470dafecf0"
ANCHOR = "bfb0a9ef1c303d1977f5f02903b70cc93e514cb5"
OTHER = "abcdef0123456789abcdef0123456789abcdef01"
TIP = "1b12bc1a9f2adc067334a2f3f20af1727ee9c498"


def _registry(entries: list[dict]) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump({"anchors": entries}, fh)
    return path


def _mg(sha: str = MERGE_GATE) -> dict:
    return {"sha": sha, "kind": "merge-gate", "field": "merge-gate-v2",
            "landed_utc": "2026-08-04T15:45:04Z", "reason": "green-but-refused."}


def _pa(sha: str = ANCHOR) -> dict:
    return {"sha": sha, "kind": "producer-anchor", "field": "commit_anchored",
            "landed_utc": "2026-08-03T18:43:14Z", "reason": "green-but-null."}


class LoadFloorsTest(unittest.TestCase):
    def test_shipped_registry_parses_and_carries_both_floors(self):
        floors = gf.load_floors(gf.DEFAULT_REGISTRY)
        shas = {f["sha"] for f in floors}
        self.assertIn(MERGE_GATE, shas)
        self.assertIn(ANCHOR, shas)
        for f in floors:
            self.assertEqual(len(f["sha"]), 40)

    def test_placeholder_sha_rejected(self):
        path = _registry([{"sha": "TBD-not-real", "field": "x"}])
        try:
            with self.assertRaises(gf.FloorError):
                gf.load_floors(path)
        finally:
            os.unlink(path)

    def test_missing_registry_is_error_not_empty_ok(self):
        with self.assertRaises(gf.FloorError):
            gf.load_floors("/nonexistent/floors.json")

    def test_empty_registry_refuses(self):
        # An empty `anchors` must NOT be read as "no floor" -- that would pass
        # every base. It is an ERROR.
        path = _registry([])
        try:
            with self.assertRaises(gf.FloorError):
                gf.load_floors(path)
        finally:
            os.unlink(path)


class DeriveEffectiveTest(unittest.TestCase):
    # First-parent history newest-first: MERGE_GATE at idx 2, ANCHOR at idx 4.
    HISTORY = [TIP, "aa" * 20, MERGE_GATE, "bb" * 20, ANCHOR, "cc" * 20]

    def test_effective_is_the_newest_floor(self):
        res = gf.derive_effective([_mg(), _pa()], self.HISTORY)
        self.assertTrue(res["ok"])
        # MERGE_GATE (idx 2) is closer to the tip than ANCHOR (idx 4).
        self.assertEqual(res["effective_floor"], MERGE_GATE)
        self.assertEqual(res["effective_kind"], "merge-gate")
        eff = [f for f in res["floors"] if f["effective"]]
        self.assertEqual(len(eff), 1)
        self.assertEqual(eff[0]["sha"], MERGE_GATE)

    def test_order_in_registry_does_not_change_effective(self):
        # Even if the older floor is listed first, history order wins.
        res = gf.derive_effective([_pa(), _mg()], self.HISTORY)
        self.assertEqual(res["effective_floor"], MERGE_GATE)

    def test_floor_off_history_refuses(self):
        # A floor absent from first-parent history is the empty-intersection
        # state: REFUSE, name it off-history, derive NO effective floor.
        res = gf.derive_effective([_mg(), _pa(OTHER)], self.HISTORY)
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["off_history"]), 1)
        self.assertEqual(res["off_history"][0]["sha"], OTHER)

    def test_publish_main_refuses_when_floor_off_history(self):
        path = _registry([_mg(), _pa(OTHER)])
        try:
            with mock.patch.object(
                gf, "repository_state",
                return_value={"checkout": "/co", "is_shallow": False},
            ), mock.patch.object(gf, "first_parent", return_value=self.HISTORY):
                rc = gf.main(["--no-fetch", "--registry", path])
            self.assertEqual(rc, gf.EXIT_REFUSED)
        finally:
            os.unlink(path)

    def test_publish_main_ok_when_all_on_history(self):
        path = _registry([_mg(), _pa()])
        try:
            with mock.patch.object(
                gf, "repository_state",
                return_value={"checkout": "/co", "is_shallow": False},
            ), mock.patch.object(gf, "first_parent", return_value=self.HISTORY):
                rc = gf.main(["--no-fetch", "--registry", path])
            self.assertEqual(rc, gf.EXIT_OK)
        finally:
            os.unlink(path)


class RepositoryDepthTest(unittest.TestCase):
    def _git(self, cwd: str, *args: str) -> str:
        cp = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True,
            check=True,
        )
        return cp.stdout.strip()

    def test_uninitialized_product_checkout_does_not_climb_to_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._git(tmp, "init", "-q", "-b", "main")
            product = os.path.join(tmp, "hermit")
            os.mkdir(product)
            with self.assertRaisesRegex(gf.FloorError,
                                        "UNVERIFIABLE-CHECKOUT"):
                gf.repository_state(product)

    def test_shallow_history_is_unverifiable_and_full_history_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source")
            shallow = os.path.join(tmp, "shallow")
            full = os.path.join(tmp, "full")
            os.mkdir(source)
            self._git(source, "init", "-q", "-b", "main")
            commits = []
            for index in range(5):
                Path(source, "history.txt").write_text(
                    f"{index}\n", encoding="utf-8")
                self._git(source, "add", "history.txt")
                self._git(
                    source, "-c", "user.name=CI Hub", "-c",
                    "user.email=ci-hub@example.invalid", "commit", "-q", "-m",
                    f"commit {index}",
                )
                commits.append(self._git(source, "rev-parse", "HEAD"))

            subprocess.run(
                ["git", "clone", "-q", "--no-local", "--depth", "2",
                 source, shallow], check=True)
            subprocess.run(
                ["git", "clone", "-q", "--no-local", source, full],
                check=True)
            registry = os.path.join(tmp, "floors.json")
            Path(registry).write_text(
                json.dumps({"anchors": [_mg(commits[0])]}), encoding="utf-8")

            shallow_out = io.StringIO()
            with redirect_stdout(shallow_out):
                shallow_rc = gf.main([
                    "--repo-checkout", shallow, "--no-fetch", "--registry",
                    registry, "--json",
                ])
            shallow_doc = json.loads(shallow_out.getvalue())
            self.assertEqual(shallow_rc, gf.EXIT_REFUSED)
            self.assertEqual(shallow_doc["verdict"], gf.VERDICT_SHALLOW)
            self.assertEqual(shallow_doc["history_depth"], 2)
            self.assertEqual(shallow_doc["required_history_depth_min"], 3)
            self.assertEqual(shallow_doc["required_history_depth"], "full")
            self.assertNotIn("FAILED", shallow_doc["verdict"])

            full_out = io.StringIO()
            with redirect_stdout(full_out):
                full_rc = gf.main([
                    "--repo-checkout", full, "--no-fetch", "--registry",
                    registry, "--json",
                ])
            full_doc = json.loads(full_out.getvalue())
            self.assertEqual(full_rc, gf.EXIT_OK)
            self.assertEqual(full_doc["verdict"], gf.VERDICT_EFFECTIVE)
            self.assertEqual(full_doc["history_depth"], 5)
            self.assertEqual(full_doc["effective_floor"], commits[0])


class ClearsAllHeadTest(unittest.TestCase):
    """--head bracketed both ways: pre-floor refused, N post-floor accepted."""

    def test_pre_floor_head_refused_even_if_green(self):
        # NEGATIVE: head predates the merge-gate floor -> REFUSED, and the
        # message names that specific floor. (A green run would still not land.)
        path = _registry([_mg(), _pa()])
        with mock.patch.object(gf, "is_ancestor",
                               side_effect=lambda c, a, h: a == ANCHOR):
            res = gf.clears_all([_mg(), _pa()], "/co", "deadbeef")
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["unmet"]), 1)
        self.assertEqual(res["unmet"][0]["sha"], MERGE_GATE)
        text = gf.render_head(res)
        self.assertIn("REFUSE:", text)
        self.assertIn(MERGE_GATE[:12], text)
        self.assertIn("Rebase", text)
        os.unlink(path)

    def test_n_post_floor_heads_accepted(self):
        # POSITIVE, N=3: three distinct heads that each contain every floor all
        # pass -- proves the check is not inert.
        path = _registry([_mg(), _pa()])
        accepted = 0
        try:
            with mock.patch.object(
                gf, "repository_state",
                return_value={"checkout": "/co", "is_shallow": False},
            ), mock.patch.object(gf, "is_ancestor", return_value=True):
                for head in ("1" * 40, "2" * 40, "3" * 40):
                    res = gf.clears_all([_mg(), _pa()], "/co", head)
                    self.assertTrue(res["ok"])
                    accepted += 1
                    self.assertEqual(gf.main(["--head", head, "--registry", path]),
                                     gf.EXIT_OK)
        finally:
            os.unlink(path)
        self.assertEqual(accepted, 3)


class FirstParentSeamTest(unittest.TestCase):
    def test_first_parent_parses_rev_list(self):
        def fake_run(cmd, *, timeout):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{TIP}\n{MERGE_GATE}\n\n", stderr="")
        with mock.patch.object(gf, "_run", side_effect=fake_run):
            self.assertEqual(gf.first_parent("/co", "origin/main"),
                             [TIP, MERGE_GATE])

    def test_first_parent_git_failure_is_error(self):
        def fake_run(cmd, *, timeout):
            return subprocess.CompletedProcess(cmd, 128, stdout="",
                                               stderr="bad ref")
        with mock.patch.object(gf, "_run", side_effect=fake_run):
            with self.assertRaises(gf.FloorError):
                gf.first_parent("/co", "origin/nope")

    def test_is_ancestor_maps_returncodes(self):
        calls = {"n": 0}

        def fake_run(cmd, *, timeout):
            # rev-parse verifications succeed; the is-ancestor call returns 1.
            if "merge-base" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(gf, "_run", side_effect=fake_run):
            self.assertFalse(gf.is_ancestor("/co", ANCHOR, TIP))


if __name__ == "__main__":
    unittest.main()
