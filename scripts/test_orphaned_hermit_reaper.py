#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("orphaned-hermit-reaper.py")
SPEC = importlib.util.spec_from_file_location("orphaned_hermit_reaper", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
reaper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reaper
SPEC.loader.exec_module(reaper)


UID = 212630
WORKSPACE = Path("/home/test/work/dev-hermit")
CGROUP = (
    "/user.slice/user-212630.slice/user@212630.service/"
    "3pai_sandbox.slice/run-p847299-iABC.scope"
)


def snapshot(**overrides: object) -> reaper.ProcessSnapshot:
    values: dict[str, object] = {
        "pid": 1234,
        "start_ticks": 10_000,
        "ppid": 1,
        "uid": UID,
        "state": "R",
        "cpu_seconds": 900.0,
        "age_seconds": 1000.0,
        "exe": WORKSPACE / "hermit/target/release/hermit",
        "argv": ("hermit", "--backend", "kvm", "run", "--", "/bin/true"),
        "cgroup": CGROUP,
    }
    values.update(overrides)
    return reaper.ProcessSnapshot(**values)


class SelectionTests(unittest.TestCase):
    def decision(self, item: reaper.ProcessSnapshot) -> reaper.Decision:
        return reaper.decide(
            item,
            caller_uid=UID,
            workspace=WORKSPACE,
            bounds=reaper.Bounds(),
        )

    def test_proven_runaway_is_candidate(self) -> None:
        verdict = self.decision(snapshot())
        self.assertTrue(verdict.eligible)
        self.assertEqual(verdict.kind, "hermit-run")

    def test_every_binding_is_fail_closed(self) -> None:
        cases = {
            "live-parent": snapshot(ppid=42),
            "other-user": snapshot(uid=UID + 1),
            "other-cgroup": snapshot(cgroup="/user.slice/untrusted.scope"),
            "outside-workspace": snapshot(exe=Path("/tmp/hermit")),
            "not-run": snapshot(argv=("hermit", "coordinator")),
            "young": snapshot(age_seconds=100.0),
            "low-cpu": snapshot(cpu_seconds=100.0),
            "not-spinning": snapshot(cpu_seconds=600.0, age_seconds=2000.0),
            "zombie": snapshot(state="Z"),
        }
        for label, item in cases.items():
            with self.subTest(label=label):
                self.assertFalse(self.decision(item).eligible)

    def test_cargo_test_under_target_deps_is_candidate(self) -> None:
        item = snapshot(
            exe=WORKSPACE / "hermit/target/debug/deps/detcore_misc-abcdef",
            argv=("detcore_misc-abcdef", "--nocapture"),
        )
        verdict = self.decision(item)
        self.assertTrue(verdict.eligible)
        self.assertEqual(verdict.kind, "cargo-test")

    def test_similarly_named_path_is_not_a_test(self) -> None:
        item = snapshot(exe=WORKSPACE / "targetish/debug/deps/fake")
        self.assertFalse(self.decision(item).eligible)


class ProcParsingTests(unittest.TestCase):
    def test_stat_parser_handles_spaces_and_parentheses(self) -> None:
        # field 2 deliberately contains both spaces and a right parenthesis.
        fields = ["R", "1"] + ["0"] * 9 + ["800", "100"] + ["0"] * 6 + ["10000"]
        state, ppid, utime, stime, start = reaper._parse_stat(
            "1234 (weird ) test) " + " ".join(fields)
        )
        self.assertEqual((state, ppid, utime, stime, start), ("R", 1, 800, 100, 10000))

    def test_reader_derives_cpu_age_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "uptime").write_text("20.00 0.00\n")
            base = root / "1234"
            base.mkdir()
            fields = ["R", "1"] + ["0"] * 9 + ["800", "100"] + ["0"] * 6 + ["1000"]
            (base / "stat").write_text("1234 (hermit) " + " ".join(fields))
            (base / "status").write_text(f"Name:\thermit\nUid:\t{UID}\t{UID}\t{UID}\t{UID}\n")
            (base / "cgroup").write_text(f"0::{CGROUP}\n")
            (base / "cmdline").write_bytes(b"hermit\0run\0--\0/bin/true\0")
            (base / "exe").symlink_to(WORKSPACE / "hermit/target/release/hermit")
            item = reaper.ProcReader(root, clock_ticks=100).read(1234)
            self.assertEqual(item.cpu_seconds, 9.0)
            self.assertEqual(item.age_seconds, 10.0)
            self.assertEqual(item.argv[1], "run")


class SignallingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.initial = snapshot()
        self.candidate = reaper.decide(
            self.initial,
            caller_uid=UID,
            workspace=WORKSPACE,
            bounds=reaper.Bounds(),
        )

    def run_reap(
        self,
        reads: list[reaper.ProcessSnapshot],
        *,
        exited_after_term: bool,
    ) -> tuple[reaper.ReapResult, list[signal.Signals]]:
        class Reader:
            def read(inner_self, pid: int) -> reaper.ProcessSnapshot:
                self.assertEqual(pid, 1234)
                return reads.pop(0)

        sent: list[signal.Signals] = []
        result = reaper.reap(
            self.candidate,
            reader=Reader(),
            caller_uid=UID,
            workspace=WORKSPACE,
            bounds=reaper.Bounds(),
            grace_seconds=0,
            pidfd_open=lambda pid: 99,
            pidfd_send=lambda fd, sig: sent.append(sig),
            wait_pidfd=lambda fd, grace: exited_after_term,
        )
        return result, sent

    def test_term_then_kill_uses_pidfd_after_two_revalidations(self) -> None:
        original_close = reaper.os.close
        reaper.os.close = lambda fd: None
        try:
            result, sent = self.run_reap(
                [self.initial, self.initial], exited_after_term=False
            )
        finally:
            reaper.os.close = original_close
        self.assertEqual(sent, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(result.outcome, "killed-after-grace")

    def test_identity_change_refuses_kill_escalation(self) -> None:
        original_close = reaper.os.close
        reaper.os.close = lambda fd: None
        try:
            result, sent = self.run_reap(
                [self.initial, snapshot(start_ticks=10_001)], exited_after_term=False
            )
        finally:
            reaper.os.close = original_close
        self.assertEqual(sent, [signal.SIGTERM])
        self.assertEqual(result.outcome, "refused-identity-changed-before-kill")

    def test_clean_term_does_not_escalate(self) -> None:
        original_close = reaper.os.close
        reaper.os.close = lambda fd: None
        try:
            result, sent = self.run_reap([self.initial], exited_after_term=True)
        finally:
            reaper.os.close = original_close
        self.assertEqual(sent, [signal.SIGTERM])
        self.assertEqual(result.outcome, "terminated")


class CommandLineSafetyTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_apply_requires_acknowledgement(self) -> None:
        result = self.run_script("--apply", "--pid", "1234")
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --confirm", result.stderr)

    def test_apply_requires_explicit_pid(self) -> None:
        result = self.run_script("--apply", "--confirm", reaper.CONFIRMATION)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires at least one explicit --pid", result.stderr)


if __name__ == "__main__":
    unittest.main()
