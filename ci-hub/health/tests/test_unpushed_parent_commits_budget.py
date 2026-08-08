#!/usr/bin/env python3
"""Bracket the unpushed-parent-commits gate's RUNTIME and its SCOPE.

On 2026-08-08 this gate surfaced as ``ERROR: gate command could not run ...
timed out after 30s`` while a real unpushed stack existed on the box. The
reporting was CORRECT -- could-not-measure, not a clean pass -- and the tests
below exist to keep it that way while pinning down what actually costs time.

Two properties are asserted, because the incident turned on both:

1.  COST IS PER LOCAL-ONLY COMMIT, NOT PER WORKTREE. ``--scope all`` means "all
    REFS of the parent repo", not "all worktrees on the box". Detection is a
    single ``rev-list``; it is ``--rescue`` that spends two network round trips
    PER COMMIT FOUND. That gives the gate a vicious shape: it is instant when
    there is nothing to find and only becomes expensive once it finds
    something, so its timeout is anti-correlated with the risk it exists to
    cover. ``test_runtime_scales_with_commit_count`` measures that directly.

2.  SILENCE IS NOT COVERAGE. Every git call in the gate runs with ``cwd=PARENT``,
    so a commit living only in a product worktree is invisible to it no matter
    how long it is allowed to run. ``test_scope_all_does_not_see_a_nested_repo``
    pins that as a known, deliberate boundary rather than an assumed one.

The fixtures are fully inert: a local bare repo stands in for ``origin`` and a
stub replaces ``herdr-run``, so nothing here touches the network, the real
parent checkout, or any live tmux pane.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HEALTH = Path(__file__).resolve().parents[1]
REPO_ROOT = HEALTH.parents[1]
GATE_SOURCE = HEALTH / "unpushed_parent_commits.py"


def run(*args: str, cwd: Path) -> str:
    out = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    return out.stdout.strip()


class Fixture:
    """An inert parent-shaped tree: real git, local ``origin``, stub herdr-run.

    The gate derives PARENT from ``__file__.resolve().parents[2]``, so placing a
    copy at ``<root>/ci-hub/health/`` is what re-points it at the fixture. That
    is the whole isolation mechanism -- no env var and no flag can do it.
    """

    def __init__(self, tmp: Path, herdr_delay: float = 0.0) -> None:
        self.root = tmp / "parent"
        self.origin = tmp / "origin.git"
        (self.root / "ci-hub" / "health").mkdir(parents=True)
        (self.root / "agent-utils" / "bin").mkdir(parents=True)
        shutil.copy(GATE_SOURCE, self.root / "ci-hub" / "health" / GATE_SOURCE.name)
        self.gate = self.root / "ci-hub" / "health" / GATE_SOURCE.name
        self._write_herdr_stub(herdr_delay)

        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            run("git", "config", key, value, cwd=self.root)
        run("git", "remote", "add", "origin", str(self.origin), cwd=self.root)
        self.commit("seed")
        run("git", "push", "-q", "origin", "main", cwd=self.root)
        run("git", "fetch", "-q", "origin", cwd=self.root)

    def _write_herdr_stub(self, delay: float) -> None:
        """Stand in for agent-utils/bin/herdr-run.

        The real one injects into a tmux pane and reaches the network. The stub
        strips ``--agent NAME`` and the ``with-proxy`` prefix, then runs the rest
        locally, optionally after `delay` seconds so a network cost can be
        simulated without one.
        """
        stub = self.root / "agent-utils" / "bin" / "herdr-run"
        stub.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import subprocess, sys, time
            argv = sys.argv[1:]
            if argv[:1] == ["--agent"]:
                argv = argv[2:]
            time.sleep({delay!r})
            line = argv[0]
            if line.startswith("with-proxy "):
                line = line[len("with-proxy "):]
            sys.exit(subprocess.run(["bash", "-c", line]).returncode)
            """))
        stub.chmod(0o755)

    def commit(self, subject: str) -> str:
        path = self.root / "f.txt"
        path.write_text(f"{subject}\n")
        run("git", "add", "--", "f.txt", cwd=self.root)
        run("git", "commit", "-q", "-m", subject, cwd=self.root)
        return run("git", "rev-parse", "HEAD", cwd=self.root)

    def gate_run(self, *args: str) -> tuple[int, str, float]:
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(self.gate), *args],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr, time.monotonic() - started


class UnpushedParentCommitsBudget(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="unpushed-gate-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # ------------------------------------------------------------ positive --
    def test_detects_and_publishes_a_planted_unpushed_commit(self) -> None:
        """POSITIVE BRACKET: it finds a planted commit, publishes it, and the
        publication is confirmed by re-reading the remote -- not by a push exit
        code."""
        fx = Fixture(self.tmp)
        sha = fx.commit("planted local-only commit")

        rc, out, elapsed = fx.gate_run("--scope", "all", "--rescue")

        self.assertEqual(rc, 1, f"nonzero rc signals findings; got {rc}\n{out}")
        self.assertIn("count=1", out, out)
        self.assertIn(sha[:7], out, out)
        self.assertIn("[verified]", out,
                      f"publication must be verified at the remote:\n{out}")
        # The rescue ref really exists in the inert origin: publication is a
        # fact about the remote, not a claim in the log line.
        refs = run("git", "for-each-ref", "--format=%(refname)", cwd=fx.origin)
        self.assertIn(f"refs/heads/rescue/auto-{sha[:7]}", refs, refs)
        self.assertLess(elapsed, 30.0, f"must fit the 30s gate budget; took {elapsed:.2f}s")

    def test_clean_tree_reports_zero_and_is_cheap(self) -> None:
        """CONTROL: with nothing to find the gate passes, and it is fast for the
        very reason that makes it slow later -- the rescue loop never runs."""
        fx = Fixture(self.tmp, herdr_delay=1.0)
        rc, out, elapsed = fx.gate_run("--scope", "all", "--rescue")
        self.assertEqual(rc, 0, out)
        self.assertIn("count=0", out, out)
        self.assertLess(elapsed, 30.0, f"took {elapsed:.2f}s")

    # ------------------------------------------------------- cost structure --
    def test_runtime_scales_with_commit_count_not_worktree_count(self) -> None:
        """ROOT CAUSE: runtime grows with the number of local-only commits,
        because --rescue spends two herdr round trips on each one.

        Same fixture, same single repo, no worktrees anywhere: only the commit
        count changes, and the runtime tracks it. That is what makes a 30s
        budget unsurvivable once a backlog exists.
        """
        delay = 0.25
        timings = {}
        for count in (1, 3):
            with tempfile.TemporaryDirectory(prefix="unpushed-scale-") as raw:
                fx = Fixture(Path(raw), herdr_delay=delay)
                for i in range(count):
                    fx.commit(f"local-only {i}")
                rc, out, elapsed = fx.gate_run("--scope", "all", "--rescue")
                self.assertEqual(rc, 1, out)
                self.assertIn(f"count={count}", out, out)
                timings[count] = elapsed

        # Two herdr calls per commit (push + verify), so each extra commit adds
        # ~2*delay. Assert the SLOPE, which is the structural claim; absolute
        # times carry process-start noise and a loaded box.
        added = timings[3] - timings[1]
        self.assertGreater(
            added, 2 * delay,
            f"expected >=2 extra commits x 2 calls x {delay}s; "
            f"1->{timings[1]:.2f}s 3->{timings[3]:.2f}s")

    # -------------------------------------------------------------- scope ----
    def test_scope_all_does_not_see_a_nested_repo(self) -> None:
        """COVERAGE BOUNDARY, asserted rather than assumed.

        `--scope all` is all REFS of the parent repo. A separate repository
        nested inside the tree -- which is exactly what a product worktree is --
        is invisible, and no timeout increase would ever change that. Pinning
        it here means widening the scope later has to break this test on
        purpose.
        """
        fx = Fixture(self.tmp)
        nested = fx.root / "product"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(nested)], check=True)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            run("git", "config", key, value, cwd=nested)
        (nested / "p.txt").write_text("hidden\n")
        run("git", "add", "--", "p.txt", cwd=nested)
        run("git", "commit", "-q", "-m", "unpushed inside a nested repo", cwd=nested)
        hidden = run("git", "rev-parse", "HEAD", cwd=nested)

        rc, out, _ = fx.gate_run("--scope", "all")

        self.assertEqual(rc, 0, f"parent itself is clean, so the gate passes:\n{out}")
        self.assertIn("count=0", out, out)
        self.assertNotIn(hidden[:7], out,
                         "nested-repo commit must not appear; it is out of scope")

    # ------------------------------------------------- negative / fail-closed --
    def test_overrunning_gate_reports_could_not_measure_not_success(self) -> None:
        """NEGATIVE BRACKET, against the REAL runner.

        This is the property the incident got right and that must not be traded
        away for a faster gate: a command that cannot finish inside its budget
        must surface as could-not-measure, never as a pass.
        """
        sys.path.insert(0, str(REPO_ROOT / "agent-utils" / "py"))
        self.addCleanup(lambda: sys.path.remove(str(REPO_ROOT / "agent-utils" / "py")))
        try:
            from tick_hub.probes import SubprocessGateRunner
        except ImportError as exc:  # pragma: no cover - agent-utils absent
            self.skipTest(f"tick_hub not importable: {exc}")

        result = SubprocessGateRunner(timeout=1).run("sleep 30")

        self.assertFalse(result.ok, "an overrun must NOT be reported as ok")
        self.assertIn("timed out", (result.error or "").lower(), result.error)
        # A pass is rc 0 and a finding is rc 1; neither may be forged here.
        self.assertNotEqual(result.returncode, 0,
                            "an overrun must never present as a clean pass")

    def test_completing_gate_is_reported_as_ran(self) -> None:
        """ADMIT CONTROL for the runner: without this, a runner that reported
        could-not-measure unconditionally would satisfy the test above."""
        sys.path.insert(0, str(REPO_ROOT / "agent-utils" / "py"))
        self.addCleanup(lambda: sys.path.remove(str(REPO_ROOT / "agent-utils" / "py")))
        try:
            from tick_hub.probes import SubprocessGateRunner
        except ImportError as exc:  # pragma: no cover - agent-utils absent
            self.skipTest(f"tick_hub not importable: {exc}")

        result = SubprocessGateRunner(timeout=30).run("echo measured")

        self.assertTrue(result.ok, f"a completing gate must report ok: {result}")
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("measured", result.stdout)


if __name__ == "__main__":
    unittest.main()


class ObservationSurvivesRescue(unittest.TestCase):
    """The 2026-08-08 fix: the measurement is emitted BEFORE rescue is attempted.

    The gate had never emitted a measurement in a full day of ticks, and the
    reason was not that it could not measure. `main` computed the correct answer
    in 0.4s, then called `rescue`, and printed only afterwards -- so the 30s
    timeout killed a process that was already holding a complete result. These
    tests pin the ordering and the bounded-rescue reporting so the measurement
    can never again be discarded by a slow or broken publish leg.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="unpushed-order-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_measurement_survives_a_rescue_that_never_finishes(self) -> None:
        """POSITIVE: kill the gate mid-rescue; the count is already on stdout.

        This reproduces the tick timeout exactly -- an external killer stops the
        process while rescue is still running -- and asserts the observation is
        not lost with it.
        """
        fx = Fixture(self.tmp, herdr_delay=30.0)
        fx.commit("local-only-and-unpublished")

        proc = subprocess.Popen(
            [sys.executable, str(fx.gate), "--scope", "all", "--rescue"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # Our own child, by exact pid, and reaped so it cannot linger on a
            # shared box (Hard Invariant 15).
            proc.kill()
            proc.wait(timeout=10)
        out = proc.stdout.read()
        proc.stdout.close()

        self.assertIn("unpushed-parent-commits scope=all count=1", out)
        self.assertIn("local-only-and-unpublished", out)
        self.assertIn("summary=", out)
        # And the publish leg genuinely had not finished when we killed it.
        self.assertNotIn("rescue attempted=", out)

    def test_deadline_reports_skipped_never_verified(self) -> None:
        """NEGATIVE: commits the budget never reached are named, not implied done."""
        fx = Fixture(self.tmp, herdr_delay=1.0)
        for n in range(3):
            fx.commit(f"unpublished-{n}")

        rc, out, _ = fx.gate_run(
            "--scope", "all", "--rescue", "--rescue-deadline", "0.2"
        )
        self.assertIn("skipped-deadline", out)
        skipped = out.count("skipped-deadline")
        self.assertGreaterEqual(skipped, 1)
        # Partial coverage is not success: nothing unpublished may exit 0.
        self.assertEqual(1, rc)
        # The count line states what was skipped rather than burying it.
        self.assertRegex(out, r"skipped_deadline=[1-9]")

    def test_summary_key_is_emitted_so_the_alarm_names_its_subject(self) -> None:
        """tick-hub resolves `{summary}` from key=value stdout lines.

        Without a `summary=` line the emitted warning renders the LITERAL
        `{summary}` -- the unactionable-alarm defect the worktree-liveness gate
        shipped. It was invisible here only because the gate never completed.
        """
        fx = Fixture(self.tmp)
        fx.commit("names-its-subject")
        _, out, _ = fx.gate_run("--scope", "all")

        summary = [l for l in out.splitlines() if l.startswith("summary=")]
        self.assertEqual(1, len(summary), out)
        self.assertIn("names-its-subject", summary[0])
        self.assertNotIn("{summary}", out)

    def test_clean_tree_still_emits_a_summary(self) -> None:
        """A gate that only names its subject when firing leaves `{summary}`
        literal on the quiet path. Both branches must emit the key."""
        fx = Fixture(self.tmp)
        rc, out, _ = fx.gate_run("--scope", "all")
        self.assertEqual(0, rc)
        self.assertIn("summary=no local-only commits", out)


class TickGateKeepsObservationSeparateFromRescue(unittest.TestCase):
    """The split is the fix; pin it so it cannot silently regress.

    Measured 2026-08-08: scan 0.40s versus one herdr-run rescue round-trip at
    65.8s and 74.8s (both failing), two per commit. Putting `--rescue` back on a
    30s tick budget restores a gate that can never report.
    """

    def test_tick_gate_runs_observation_only(self) -> None:
        config = (REPO_ROOT / "ci-hub" / "health" / "tick-hub.yaml").read_text()
        block = config.split("- name: unpushed_parent_commits", 1)
        self.assertEqual(2, len(block), "gate missing from tick-hub.yaml")
        cmd = [
            line for line in block[1].splitlines()
            if "unpushed_parent_commits.py" in line
        ]
        self.assertEqual(1, len(cmd), block[1][:400])
        self.assertIn("--scope all", cmd[0])
        self.assertNotIn("--rescue", cmd[0])
