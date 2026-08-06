#!/usr/bin/env python3
"""Network-free tests for the composite validation admission authority."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preflight_validate as pv  # noqa: E402


HEAD = "a" * 40
BASE = "b" * 40


def floors(ok: bool = True) -> dict:
    return {
        "head": HEAD,
        "repo": "rrnewton/hermit",
        "n_anchors": 2,
        "anchors": [],
        "missing": [] if ok else [{"sha": "c" * 40}],
        "ok": ok,
    }


class AdmissionVerdictTest(unittest.TestCase):
    def evaluate(self, contains: bool, *, fixed_ok: bool = True) -> dict:
        with mock.patch.object(pv.preflight_anchor, "preflight", return_value=floors(fixed_ok)), \
             mock.patch.object(pv, "resolve_current_base", return_value=BASE), \
             mock.patch.object(
                 pv.preflight_anchor,
                 "head_contains",
                 return_value=(contains, "local-merge-base"),
             ):
            return pv.admission(
                HEAD,
                checkout="/checkout",
                repo="rrnewton/hermit",
                anchors_path="/anchors.json",
                base_branch="main",
            )

    def test_stale_head_is_refused_and_names_exact_moving_base(self) -> None:
        result = self.evaluate(False)

        self.assertFalse(result["ok"])
        self.assertEqual(BASE, result["moving_base"]["sha"])
        message = pv.render(result)
        self.assertIn("REFUSE:", message)
        self.assertIn(BASE, message)
        self.assertIn("cannot land without another rebase", message)

        with mock.patch.object(pv, "admission", return_value=result), \
             contextlib.redirect_stdout(io.StringIO()):
            rc = pv.main(["--head", HEAD])
        self.assertEqual(pv.EXIT_REFUSED, rc)

    def test_head_containing_current_base_is_admitted(self) -> None:
        result = self.evaluate(True)

        self.assertTrue(result["ok"])
        self.assertIn("OK:", pv.render(result))
        self.assertIn(BASE[:12], pv.render(result))

    def test_fixed_floor_still_refuses_even_when_moving_base_passes(self) -> None:
        result = self.evaluate(True, fixed_ok=False)

        self.assertFalse(result["ok"])

    def test_non_exact_head_is_an_error(self) -> None:
        with self.assertRaises(pv.AdmissionError):
            pv.admission(
                "origin/feature",
                checkout="/checkout",
                repo="rrnewton/hermit",
                anchors_path="/anchors.json",
                base_branch="main",
            )


class MovingBaseResolutionTest(unittest.TestCase):
    def test_fetches_origin_main_before_resolving_exact_tip(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], *, timeout: float):
            calls.append(command)
            if command[0] == "with-proxy":
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, BASE + "\n", "")

        with mock.patch.object(pv, "_run", side_effect=fake_run):
            result = pv.resolve_current_base("/checkout", "main")

        self.assertEqual(BASE, result)
        self.assertEqual("with-proxy", calls[0][0])
        self.assertIn(
            "refs/heads/main:refs/remotes/origin/main",
            calls[0],
        )
        self.assertEqual(
            ["git", "-C", "/checkout", "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
            calls[1],
        )

    def test_fetch_failure_is_error_not_permission(self) -> None:
        failed = subprocess.CompletedProcess(["with-proxy", "git"], 1, "", "egress down")
        with mock.patch.object(pv, "_run", return_value=failed):
            with self.assertRaisesRegex(pv.AdmissionError, "cannot refresh"):
                pv.resolve_current_base("/checkout", "main")

        with mock.patch.object(
            pv,
            "admission",
            side_effect=pv.AdmissionError("cannot refresh origin/main"),
        ), contextlib.redirect_stderr(io.StringIO()):
            rc = pv.main(["--head", HEAD])
        self.assertEqual(pv.EXIT_ERROR, rc)


if __name__ == "__main__":
    unittest.main()
