#!/usr/bin/env python3
"""Bounded, dependency-isolated smoke tests for every ci-hub command."""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_HUB = ROOT / "ci-hub/ci-hub"
WALL_SECONDS = 15
CPU_SECONDS = 5


def _limit_cpu() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))


class OperationalBoundsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary.name)
        self.bin = self.temp / "bin"
        self.bin.mkdir()
        self.stall = self._script("stall", "exec sleep 30")
        self.succeed = self._script("succeed", "exit 0")
        self._script(
            "with-proxy",
            'exec "' + str(self.stall) + '" "$@"',
        )
        self._script(
            "tg",
            "printf '%s\\n' "
            '\'{"id":"task","title":"test","owner":"worker","tags":[]}\' '
            "'(1 rows)'",
        )
        self.snapshot = self.temp / "agents.json"
        self.snapshot.write_text(
            json.dumps([{"name": "worker", "status": "busy", "current_task": "task"}])
        )
        self.store = self.temp / "obligations.jsonl"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin}:{self.env['PATH']}",
                "CI_HUB_TOOL_COST_ACTIVE": "1",
                "CI_HUB_MAIN_HEALTH_TIMEOUT": "0.1",
                "CI_HUB_MAIN_HEALTH_DEADLINE": "0.2",
                "CI_HUB_PR_STATUS_TIMEOUT": "0.1",
                "CI_HUB_PR_STATUS_DEADLINE": "0.2",
                "CI_HUB_REMEDIATION_NETWORK_TIMEOUT": "0.1",
                "DEV_HERMIT_PARENT": str(self.temp),
                "TMPDIR": str(self.temp),
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _script(self, name: str, body: str) -> Path:
        path = self.bin / name
        path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n")
        path.chmod(0o755)
        return path

    def run_bounded(
        self,
        *args: str,
        expected: set[int] = {0},
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        process = subprocess.Popen(
            [str(CI_HUB), *args],
            cwd=ROOT,
            env=self.env if env is None else env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=_limit_cpu,
        )
        try:
            stdout, stderr = process.communicate(timeout=WALL_SECONDS)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            self.fail(f"ci-hub {' '.join(args)} exceeded {WALL_SECONDS}s: {error}")
        result = subprocess.CompletedProcess(
            process.args,
            process.returncode,
            stdout,
            stderr,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, WALL_SECONDS)
        self.assertIn(
            result.returncode,
            expected,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def test_every_subcommand_dispatches_within_wall_and_cpu_bounds(self) -> None:
        tick_env = self.env | {"CI_HUB_AGENT_TOOL": str(self.succeed)}
        stall_env = self.env | {"CI_HUB_AGENT_TOOL": str(self.stall)}
        cases = (
            (
                ("active-work", "--agent-snapshot", str(self.snapshot), "--json"),
                {0},
                self.env,
            ),
            (("main-health", "--repo", "rrnewton/dev-hermit"), {2}, self.env),
            (("pr-status", "--repo", "rrnewton/dev-hermit"), {2}, stall_env),
            (("tick",), {0}, tick_env),
            (
                (
                    "arm-land",
                    "not-a-sha",
                    "--source",
                    str(ROOT),
                    "--no-dispatch",
                    "--store",
                    str(self.store),
                ),
                {2},
                self.env,
            ),
            (("obligations", "--store", str(self.store)), {0}, self.env),
            (
                ("watch-obligations", "--once", "--store", str(self.store)),
                {0},
                self.env,
            ),
            (
                (
                    "resolve-obligation",
                    "missing",
                    "--kind",
                    "revert",
                    "--ref",
                    "a" * 40,
                    "--store",
                    str(self.store),
                ),
                {2},
                self.env,
            ),
            (("refresh-history", "--", "--help"), {0}, self.env),
            (("history", "--", "--help"), {0}, self.env),
            (("local-history", "--json", "--since", "2999-01-01"), {0}, self.env),
            (
                (
                    "runner-health",
                    "--repo",
                    "rrnewton/dev-hermit",
                    "--limit",
                    "1",
                    "--sample",
                    "0",
                    "--gh",
                    "/bin/false",
                ),
                {0},
                self.env,
            ),
        )
        for args, expected, environment in cases:
            with self.subTest(command=args[0]):
                self.run_bounded(*args, expected=expected, env=environment)

    def test_composite_health_returns_explicit_partial_result_on_stalls(self) -> None:
        env = self.env | {"CI_HUB_AGENT_TOOL": str(self.stall)}
        result = self.run_bounded(
            "health",
            "--repo",
            "rrnewton/dev-hermit",
            expected={2},
            env=env,
        )
        output = result.stdout + result.stderr
        self.assertIn("DEGRADED", output)
        self.assertIn("UNAVAILABLE", output)
        self.assertGreaterEqual(output.count("PARTIAL RESULT"), 2)

    def test_land_lock_full_protocol_is_bounded(self) -> None:
        env = self.env | {"CI_HUB_LANDING_LOCK": str(self.temp / "landing.lock")}
        self.assertIn("FREE", self.run_bounded("land-lock", "status", env=env).stdout)
        self.run_bounded(
            "land-lock",
            "acquire",
            "--agent",
            "ci-shard",
            "--pr",
            "test",
            "--wait",
            "0",
            "--hold",
            "30",
            env=env,
        )
        self.run_bounded(
            "land-lock",
            "renew",
            "--agent",
            "ci-shard",
            "--hold",
            "60",
            env=env,
        )
        self.assertIn(
            "ci-shard", self.run_bounded("land-lock", "status", env=env).stdout
        )
        self.run_bounded("land-lock", "release", "--agent", "ci-shard", env=env)
        self.run_bounded(
            "land-lock",
            "run",
            "--agent",
            "ci-shard-run",
            "--pr",
            "test-run",
            "--wait",
            "0",
            "--hold",
            "30",
            "--",
            "/bin/true",
            env=env,
        )
        self.assertIn("FREE", self.run_bounded("land-lock", "status", env=env).stdout)


if __name__ == "__main__":
    unittest.main()
