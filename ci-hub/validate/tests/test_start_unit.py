from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start_unit  # noqa: E402


SHA = "a" * 40


def completed(command: list[str], rc: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, rc, stdout, stderr)


class FakeRun:
    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout
        self.commands: list[list[str]] = []
        self.dirty = ""
        self.preflight_rc = 0

    def __call__(self, command: list[str], **_kwargs: object):
        self.commands.append(command)
        if command[:4] == ["git", "-C", str(self.checkout), "rev-parse"]:
            if command[-1] == "--show-toplevel":
                return completed(command, stdout=f"{self.checkout}\n")
            return completed(command, stdout=f"{SHA}\n")
        if command[:4] == ["git", "-C", str(self.checkout), "status"]:
            return completed(command, stdout=self.dirty)
        if command[0].endswith("preflight_anchor.py"):
            return completed(command, rc=self.preflight_rc, stderr="pre-anchor" if self.preflight_rc else "")
        if command[0] == "systemd-run":
            return completed(command, stdout="Running as unit: validate-test.service\n")
        raise AssertionError(command)


class StartUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "hermit"
        self.checkout.mkdir()
        (self.checkout / "validate.sh").write_text("#!/bin/sh\n")
        (self.root / "ci-hub/validate").mkdir(parents=True)
        self.fake = FakeRun(self.checkout)
        self.environment = {"HOME": "/home/test", "PATH": "/usr/bin:/bin"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, extra: list[str] | None = None) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        argv = [
            "--checkout",
            str(self.checkout),
            "--agent",
            "hermit-test",
            "--target",
            SHA,
            "--unit",
            "validate-test",
            "--log",
            str(self.root / "run.log"),
            *(extra or []),
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = start_unit.main(
                argv,
                run=self.fake,
                environment=self.environment,
                root=self.root,
            )
        return rc, out.getvalue(), err.getvalue()

    def test_positive_launch_routes_systemd_service_through_validate_lock(self) -> None:
        rc, output, error = self.invoke(["--pr", "123", "--", "full", "--ignore-cache"])

        self.assertEqual(0, rc, error)
        systemd = next(command for command in self.fake.commands if command[0] == "systemd-run")
        self.assertIn(str(self.root / "ci-hub/ci-hub"), systemd)
        lock = systemd.index("validate-lock")
        self.assertEqual(["validate-lock", "run"], systemd[lock : lock + 2])
        self.assertEqual(
            ["/usr/bin/env", "PR_NUMBER=123", "with-proxy", "./validate.sh", "full", "--ignore-cache"],
            systemd[-6:],
        )
        self.assertIn("admission=ci-hub validate-lock", output)

    def test_dry_run_is_non_mutating_but_exposes_exact_command(self) -> None:
        rc, output, error = self.invoke(["--dry-run"])

        self.assertEqual(0, rc, error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))
        self.assertIn("WOULD-START", output)
        self.assertIn("ci-hub validate-lock run", output)
        self.assertFalse((self.root / "run.log").exists())

    def test_dirty_checkout_is_refused_before_admission(self) -> None:
        self.fake.dirty = " M validate.sh\n"

        rc, _output, error = self.invoke()

        self.assertEqual(2, rc)
        self.assertIn("checkout is dirty", error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))

    def test_pre_anchor_head_is_refused_before_admission(self) -> None:
        self.fake.preflight_rc = 2

        rc, _output, error = self.invoke()

        self.assertEqual(2, rc)
        self.assertIn("anchor preflight refused", error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))


if __name__ == "__main__":
    unittest.main()
