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
    @classmethod
    def setUpClass(cls) -> None:
        # The shard measures command behavior, not a cold rustc bootstrap. Build
        # the rust-script front door before applying the per-command 5s CPU box;
        # subsequent --force checks still detect changed #[path] modules but use
        # Cargo's no-op path when the binary is current.
        subprocess.run(
            [str(CI_HUB), "--help"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

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
                ("obligations", "--actionable", "--store", str(self.store)),
                {0},
                self.env,
            ),
            (
                (
                    "inherit-obligations",
                    "--agent",
                    "test-lander",
                    "--session",
                    "test-session",
                    "--store",
                    str(self.store),
                ),
                {0},
                self.env,
            ),
            (
                (
                    "record-obligation-wake",
                    "--target",
                    "test-lander",
                    "--source",
                    "test",
                    "--store",
                    str(self.store),
                ),
                {0},
                self.env,
            ),
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
                ("load-probe", "--sample-seconds", "0.1", "--top", "1"),
                {0, 1},
                self.env,
            ),
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

    def test_composite_health_surfaces_unacknowledged_remediation(self) -> None:
        self.store.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "obligation_id": "owed-remediation",
                    "repo": "rrnewton/hermit",
                    "landed_sha": "a" * 40,
                    "opened_at": "2026-08-03T00:00:00Z",
                    "overall_state": "remediation_required",
                    "local": {"state": "red"},
                    "github": {"state": "running"},
                    "recommendation": {"action": "revert"},
                    "remediation": {
                        "state": "triggered",
                        "dispatch": {"state": "sent_unacknowledged"},
                    },
                }
            )
            + "\n"
        )
        env = self.env | {
            "CI_HUB_AGENT_TOOL": str(self.stall),
            "CI_HUB_OBLIGATIONS_STORE": str(self.store),
        }
        result = self.run_bounded("health", expected={2}, env=env)
        output = result.stdout + result.stderr
        self.assertIn("Speculative-land obligations: REMEDIATION REQUIRED", output)
        self.assertIn("dispatch=sent_unacknowledged", output)

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
        # A wedged land subtree must NOT hold the lock forever (the head-of-line
        # starvation bug): --child-deadline hard-kills the child, exits 124, and
        # RELEASES the lock so the FIFO can proceed -- all well within the wall.
        deadline_run = self.run_bounded(
            "land-lock",
            "run",
            "--agent",
            "ci-shard-wedged",
            "--pr",
            "test-wedged",
            "--wait",
            "0",
            "--hold",
            "30",
            "--child-deadline",
            "1",
            "--",
            "/bin/sleep",
            "30",
            expected={124},
            env=env,
        )
        self.assertIn("ABANDON", deadline_run.stdout + deadline_run.stderr)
        self.assertIn("FREE", self.run_bounded("land-lock", "status", env=env).stdout)
        # The old behavior could be restored with --child-deadline 0. Reject it
        # before acquiring so no caller can create an unbounded FIFO holder.
        unbounded = self.run_bounded(
            "land-lock",
            "run",
            "--agent",
            "ci-shard-unbounded",
            "--pr",
            "test-unbounded",
            "--wait",
            "0",
            "--hold",
            "30",
            "--child-deadline",
            "0",
            "--",
            "/bin/true",
            expected={2},
            env=env,
        )
        self.assertIn("must be positive", unbounded.stdout + unbounded.stderr)
        self.assertIn("FREE", self.run_bounded("land-lock", "status", env=env).stdout)

    def test_land_lock_reclaims_a_killed_supervisor_from_process_evidence(
        self,
    ) -> None:
        lock = self.temp / "killed-supervisor.lock"
        owner = Path(f"{lock}.owner")
        child_pid_file = self.temp / "land-child.pid"
        child = self._script(
            "long-land",
            f"printf '%s\\n' \"$$\" > {child_pid_file!s}; exec sleep 30",
        )
        env = self.env | {"CI_HUB_LANDING_LOCK": str(lock)}
        process = subprocess.Popen(
            [
                str(CI_HUB),
                "land-lock",
                "run",
                "--agent",
                "killed-lander",
                "--pr",
                "test-killed",
                "--wait",
                "0",
                "--hold",
                "300",
                "--child-deadline",
                "60",
                "--",
                str(child),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=_limit_cpu,
        )
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if owner.exists() and child_pid_file.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(owner.exists(), "supervised lock did not record its owner")
            self.assertTrue(child_pid_file.exists(), "supervised child did not start")
            child_pid = int(child_pid_file.read_text().strip())
            owner_pid = int(
                next(
                    line.removeprefix("pid=")
                    for line in owner.read_text().splitlines()
                    if line.startswith("pid=")
                )
            )
            live = self.run_bounded("land-lock", "status", env=env)
            self.assertIn("owner_process=alive", live.stdout)

            os.kill(owner_pid, signal.SIGKILL)
            process.wait(timeout=5)

            orphaned = self.run_bounded("land-lock", "status", env=env)
            self.assertIn("ORPHANED (reclaimable)", orphaned.stdout)
            self.assertIn("owner_process=dead:", orphaned.stdout)
            reclaimed = self.run_bounded("land-lock", "reclaim-dead", env=env)
            self.assertIn(
                "evidence-reclaimed dead owner",
                reclaimed.stdout + reclaimed.stderr,
            )
            self.assertIn(
                "FREE", self.run_bounded("land-lock", "status", env=env).stdout
            )
        finally:
            if process.poll() is None:
                process.kill()
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=5)

    def test_shared_lander_preserves_recoverable_arm_and_durable_abandonment(
        self,
    ) -> None:
        front_door = (ROOT / "ci-hub/ci-hub.rs").read_text()
        self.assertTrue(
            front_door.startswith("#!/usr/bin/env -S rust-script --force\n")
        )
        script = (ROOT / "ci-hub/landing/land-pr.sh").read_text()
        detached = script.index('nohup setsid "$0"')
        inherit = script.index('"$ROOT/ci-hub/ci-hub" inherit-obligations')
        prepare = script.index('"$ROOT/ci-hub/remediation/land_and_arm.py" prepare')
        acquire = script.index('"$ROOT/ci-hub/ci-hub" land-lock run')
        merge = script.index('gh pr merge "$PR"')
        ancestry = script.index("merge-base --is-ancestor")
        complete = script.index('"$ROOT/ci-hub/remediation/land_and_arm.py" complete')
        self.assertLess(detached, inherit)
        self.assertLess(inherit, prepare)
        self.assertLess(prepare, acquire)
        self.assertLess(acquire, merge)
        self.assertLess(merge, ancestry)
        self.assertLess(ancestry, complete)
        self.assertIn("124) comment_abandon", script)
        self.assertIn("[coordinator, $MODEL] ABANDONED", script)
        self.assertIn("CI_HUB_LANDING_LOG_DIR", script)
        self.assertIn("DETACHED LAND: pid=%s log=%s", script)

        plugin = (ROOT / ".orc/plugins/hermit-dev/index.ts").read_text()
        heartbeat = plugin[plugin.index("speculativeLandRemediationHeartbeat") :]
        wake = heartbeat.index("await orc.sendWakeup(")
        record = heartbeat.index("hermitSpeculativeLandWakeSent", wake)
        self.assertLess(wake, record)
        self.assertIn("sent but not yet acknowledged", plugin)


if __name__ == "__main__":
    unittest.main()
