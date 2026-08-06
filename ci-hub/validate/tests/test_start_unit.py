from __future__ import annotations

import contextlib
import io
import json
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
        self.admission_rc = 0
        self.herdr_status_rc = 0

    def __call__(self, command: list[str], **_kwargs: object):
        self.commands.append(command)
        if command[:4] == ["git", "-C", str(self.checkout), "rev-parse"]:
            if command[-1] == "--show-toplevel":
                return completed(command, stdout=f"{self.checkout}\n")
            return completed(command, stdout=f"{SHA}\n")
        if command[:4] == ["git", "-C", str(self.checkout), "status"]:
            return completed(command, stdout=self.dirty)
        if command[0].endswith("preflight_validate.py"):
            return completed(
                command,
                rc=self.admission_rc,
                stderr="stale base" if self.admission_rc else "",
            )
        if command[0] == "systemd-run" and "herdr" in command:
            herdr = command[command.index("herdr") :]
            if herdr == ["herdr", "status", "--json"]:
                return completed(
                    command,
                    rc=self.herdr_status_rc,
                    stdout=json.dumps({"server": {"running": True}}),
                    stderr="jail denied" if self.herdr_status_rc else "",
                )
            if herdr == ["herdr", "server"]:
                return completed(command, stdout="Running as unit: ci-hub-herdr.service\n")
            if herdr == ["herdr", "workspace", "list"]:
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "result": {
                                "workspaces": [
                                    {
                                        "workspace_id": "wV",
                                        "label": "validate-hermit",
                                    }
                                ]
                            }
                        }
                    ),
                )
            if herdr[:3] == ["herdr", "tab", "create"]:
                return completed(
                    command,
                    stdout=json.dumps(
                        {
                            "result": {
                                "root_pane": {"pane_id": "wV:p2"},
                                "tab": {"tab_id": "wV:t2"},
                            }
                        }
                    ),
                )
            if herdr[:3] == ["herdr", "pane", "rename"]:
                return completed(command)
            if herdr[:3] == ["herdr", "pane", "run"]:
                return completed(command)
            raise AssertionError(command)
        if command[0] == "systemd-run" and "validate-lock" in command:
            return completed(command, stdout="Running as unit: validate-test.service\n")
        if command[:3] == ["systemctl", "--user", "show"]:
            return completed(
                command,
                stdout=(
                    "ActiveState=inactive\nSubState=dead\nExecMainStatus=0\nResult=success\n"
                ),
            )
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
                sleep=lambda _seconds: None,
            )
        return rc, out.getvalue(), err.getvalue()

    def test_positive_launch_routes_systemd_service_through_validate_lock(self) -> None:
        rc, output, error = self.invoke(["--pr", "123", "--", "full", "--ignore-cache"])

        self.assertEqual(0, rc, error)
        systemd = next(
            command
            for command in self.fake.commands
            if command[0] == "systemd-run" and "validate-lock" in command
        )
        self.assertIn(str(self.root / "ci-hub/ci-hub"), systemd)
        lock = systemd.index("validate-lock")
        self.assertEqual(["validate-lock", "run"], systemd[lock : lock + 2])
        self.assertEqual(
            ["/usr/bin/env", "PR_NUMBER=123", "with-proxy", "./validate.sh", "full", "--ignore-cache"],
            systemd[-6:],
        )
        pane_run = next(command for command in self.fake.commands if "pane_watch.py" in " ".join(command))
        self.assertIn("herdr", pane_run)
        self.assertNotIn("validate.sh", pane_run)
        self.assertFalse(any(command[0] == "herdr" for command in self.fake.commands))
        self.assertIn("HANDLE", output)
        self.assertIn("PANE workspace=wV tab=wV:t2 pane=wV:p2", output)
        self.assertIn("FINISHED", output)
        record = start_unit.run_registry.read_record(
            self.root / "ignored/validate/runs/validate-test.json"
        )
        self.assertEqual("completed", record["state"])
        self.assertEqual("observer-only", record["pane_role"])

    def test_dry_run_is_non_mutating_but_exposes_exact_command(self) -> None:
        rc, output, error = self.invoke(["--dry-run"])

        self.assertEqual(0, rc, error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))
        self.assertIn("WOULD-START", output)
        self.assertIn("PANE-PLAN workspace=validate-hermit role=observer-only", output)
        self.assertIn("ci-hub validate-lock run", output)
        self.assertFalse((self.root / "run.log").exists())

    def test_dirty_checkout_is_refused_before_admission(self) -> None:
        self.fake.dirty = " M validate.sh\n"

        rc, _output, error = self.invoke()

        self.assertEqual(2, rc)
        self.assertIn("checkout is dirty", error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))

    def test_stale_head_is_refused_before_systemd_admission(self) -> None:
        self.fake.admission_rc = 2

        rc, _output, error = self.invoke()

        self.assertEqual(2, rc)
        self.assertIn("validation admission refused", error)
        self.assertIn("stale base", error)
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))

    def test_visibility_failure_refuses_before_validation_service(self) -> None:
        self.fake.herdr_status_rc = 1

        rc, _output, error = self.invoke()

        self.assertEqual(2, rc)
        self.assertIn("Herdr server did not become ready", error)
        self.assertFalse(
            any(
                command[0] == "systemd-run" and "validate-lock" in command
                for command in self.fake.commands
            )
        )

    def test_attach_waits_on_existing_handle_without_relaunching(self) -> None:
        record = self.root / "ignored/validate/runs/validate-test.json"
        start_unit.run_registry.write_record(
            record,
            {
                "schema_version": 1,
                "state": "running",
                "unit": "validate-test.service",
                "target": SHA,
                "checkout": str(self.checkout),
                "log": str(self.root / "run.log"),
                "workspace_id": "wV",
                "tab_id": "wV:t2",
                "pane_id": "wV:p2",
            },
        )
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = start_unit.main(
                ["--attach", "validate-test"],
                run=self.fake,
                environment=self.environment,
                root=self.root,
                sleep=lambda _seconds: None,
            )

        self.assertEqual(0, rc, err.getvalue())
        self.assertIn("ATTACHED", out.getvalue())
        self.assertIn("FINISHED", out.getvalue())
        self.assertFalse(any(command[0] == "systemd-run" for command in self.fake.commands))


if __name__ == "__main__":
    unittest.main()
