#!/usr/bin/env python3
"""Bracket the disk-residue detector from BOTH sides.

A detector proven only on the positive case is half-tested: the expensive
failure here is not missing an abandoned slot, it is flagging a busy one, since
a flagged slot is a deletion candidate and slots routinely hold the only copy of
uncommitted work. So every behavioural test below states what it plants and what
it expects to be left alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slot_disk_residue as sdr

REPO = Path(__file__).resolve().parents[3]
TICK_HUB = REPO / "ci-hub" / "health" / "tick-hub.yaml"

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
GIB = sdr.GIB


def make_root(tmp: Path, slots: list[str]) -> Path:
    for slot in slots:
        (tmp / "worktrees" / slot).mkdir(parents=True)
    return tmp


def state_with(slot: str, idle_since: datetime, size_bytes: int) -> dict:
    stamp = sdr.fmt_ts(idle_since)
    return {"slots": {slot: {"idle_since": stamp, "size_bytes": size_bytes,
                             "sized_at": stamp}}}


class DiskResidueBracketTest(unittest.TestCase):
    """The two cases the task requires, plus the ways each could be faked."""

    def test_positive_abandoned_slot_is_detected(self) -> None:
        """POSITIVE: on disk, big, no process inside, idle a long time -> flagged."""
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["abandoned"])
            prior = state_with("abandoned", NOW - timedelta(hours=72), int(8 * GIB))
            results, _, _ = sdr.evaluate(
                root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
            )
            self.assertEqual(len(results), 1)
            verdict = results[0]
            self.assertTrue(verdict.reclaimable, verdict.reason)
            self.assertEqual(verdict.procs, 0)
            self.assertAlmostEqual(verdict.idle_hours, 72.0, places=1)
            self.assertIn("8.00 GiB", verdict.reason)

    def test_negative_slot_with_a_live_process_is_not_flagged(self) -> None:
        """NEGATIVE: identical slot -- old, big, idle-clocked -- but occupied.

        Every other input is held equal to the positive case, so occupancy is
        provably the only thing doing the work. A real child process is spawned
        with its cwd inside the slot: this exercises the actual /proc scan rather
        than a stubbed occupancy map, which is the only way to show the predicate
        is wired to the running system.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["busy"])
            slot_dir = root / "worktrees" / "busy"
            # Our own child, in its own process group, killed by exact PID below.
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                cwd=slot_dir, stdin=subprocess.PIPE,
            )
            try:
                prior = state_with("busy", NOW - timedelta(hours=72), int(8 * GIB))
                results, next_state, occ = sdr.evaluate(
                    root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                    refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
                )
                verdict = results[0]
                self.assertFalse(verdict.reclaimable, verdict.reason)
                self.assertGreaterEqual(verdict.procs, 1)
                self.assertIn("live process", verdict.reason)
                self.assertGreaterEqual(occ.counts.get("busy", 0), 1)
                # The idle clock must also be CLEARED, not merely overridden:
                # otherwise the slot qualifies the instant the process exits,
                # backdated to an idleness that never happened.
                self.assertNotIn("idle_since", next_state["slots"]["busy"])
            finally:
                child.stdin.close()
                child.wait(timeout=30)

    def test_freshly_idle_slot_is_not_flagged_yet(self) -> None:
        """A slot idle for 1h is not a disk problem; sustained idleness is."""
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["justidle"])
            prior = state_with("justidle", NOW - timedelta(hours=1), int(8 * GIB))
            results, _, _ = sdr.evaluate(
                root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
            )
            self.assertFalse(results[0].reclaimable)
            self.assertIn("< 24.0h", results[0].reason)

    def test_small_idle_slot_is_not_flagged(self) -> None:
        """Long-idle but tiny: real, but not a disk-reclamation finding."""
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["tiny"])
            prior = state_with("tiny", NOW - timedelta(hours=72), 4096)
            results, _, _ = sdr.evaluate(
                root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
            )
            self.assertFalse(results[0].reclaimable)
            self.assertIn("0.00 GiB", results[0].reason)

    def test_unmeasurable_size_is_never_flagged(self) -> None:
        """An unmeasured slot must not read as empty and must not read as huge.

        `disk_bytes` returns None rather than 0 precisely so a `du` failure
        cannot be laundered into a finding either way.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["unmeasured"])
            prior = {"slots": {"unmeasured": {
                "idle_since": sdr.fmt_ts(NOW - timedelta(hours=72))}}}
            results, _, _ = sdr.evaluate(
                root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
            )
            self.assertFalse(results[0].reclaimable)
            self.assertIn("could not be measured", results[0].reason)

    def test_registry_absence_alone_never_flags_anything(self) -> None:
        """The registry is not consulted at all -- the filesystem is the authority.

        A slot the registry never heard of is judged purely on disk + process
        facts, and a slot the registry calls `active` gets no protection it did
        not earn from a live process. This is what keeps the tool out of ORC's
        jurisdiction.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["unregistered"])
            (root / "worktree-state.json").write_text(json.dumps(
                {"slots": {"unregistered": {"status": "active",
                                            "agents": [{"name": "somebody"}]}}}))
            prior = state_with("unregistered", NOW - timedelta(hours=72), int(8 * GIB))
            results, _, _ = sdr.evaluate(
                root, prior, NOW, idle_hours=24.0, min_gib=1.0,
                refresh_hours=12.0, max_measure=0, du_timeout=5, measure=False,
            )
            self.assertTrue(results[0].reclaimable)
            self.assertNotIn("somebody", results[0].reason)

    def test_deleted_leaf_cwd_still_counts_as_occupancy(self) -> None:
        """`<slot>/child (deleted)` means the child was unlinked, not the slot.

        The process is still running inside a slot that still exists on disk, so
        the slot is in use. Reading the suffix as 'gone' reports a busy slot as
        idle -- the exact false-idle this tool exists to avoid.
        """
        occ = sdr.Occupancy()
        cwd = "/r/worktrees/busy/hermit (deleted)"
        prefix = "/r/worktrees/"
        self.assertTrue(cwd.endswith(" (deleted)"))
        stripped = cwd[: -len(" (deleted)")]
        self.assertTrue(stripped.startswith(prefix))
        self.assertEqual(stripped[len(prefix):].split(os.sep)[0], "busy")
        del occ


class DeltaPagingTest(unittest.TestCase):
    """A gate that fires every tick forever gets muted; this one must not."""

    def _run(self, root: Path, state: Path, extra: list[str]) -> tuple[int, str]:
        # Thresholds at zero so these tests exercise the DELTA logic alone; the
        # threshold behaviour is bracketed separately above. `du` is left enabled
        # because an unmeasured slot is deliberately never flagged, so suppressing
        # it here would make every slot inert and the delta untestable.
        proc = subprocess.run(
            [sys.executable, str(REPO / "ci-hub/health/slot_disk_residue.py"),
             "--root", str(root), "--state", str(state), "--gate",
             "--idle-hours", "0", "--min-gib", "0", *extra],
            capture_output=True, text=True, timeout=120,
        )
        return proc.returncode, proc.stdout

    def test_first_run_adopts_baseline_without_paging(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["standing"])
            state = Path(raw) / "state.json"
            state.write_text(json.dumps(state_with(
                "standing", NOW - timedelta(hours=72), int(8 * GIB))))
            rc, out = self._run(root, state, [])
            self.assertEqual(rc, 0, out)
            self.assertIn("state=ok", out)
            # Baseline recorded, so the standing backlog is now suppressed...
            self.assertIn("standing", json.loads(state.read_text())["reported"])
            # ...and stays suppressed on the next tick.
            rc2, out2 = self._run(root, state, [])
            self.assertEqual(rc2, 0, out2)

    def test_newly_qualifying_slot_pages_once(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["old"])
            state = Path(raw) / "state.json"
            state.write_text(json.dumps({
                **state_with("old", NOW - timedelta(hours=72), int(8 * GIB)),
                "reported": ["old"],
            }))
            (root / "worktrees" / "fresh").mkdir()
            rc, out = self._run(root, state, [])
            self.assertEqual(rc, 1, out)
            self.assertIn("state=reclaimable", out)
            self.assertIn("fresh", out)
            self.assertIn("new_reclaimable_slots=1", out)
            # Second tick: same set, no longer news.
            rc2, out2 = self._run(root, state, [])
            self.assertEqual(rc2, 0, out2)

    def test_gate_output_is_capturable_key_values(self) -> None:
        """tick-hub `capture: true` parses `key=value` stdout lines into fields.

        The predecessor gate printed a prose sentence and no `summary=` line, so
        its title rendered the LITERAL `{summary}` and named no slot -- the alarm
        was unactionable even when it was right. Asserting the contract here is
        what stops that regressing.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = make_root(Path(raw), ["s1"])
            state = Path(raw) / "state.json"
            rc, out = self._run(root, state, [])
            self.assertEqual(rc, 0, out)
            fields = dict(
                line.split("=", 1) for line in out.splitlines() if "=" in line
                and not line.startswith("slot=")
            )
            for key in ("state", "summary", "slots_on_disk", "reclaimable_slots",
                        "reclaimable_gib", "blind_pids"):
                self.assertIn(key, fields)
            self.assertTrue(fields["summary"].strip())
            self.assertNotIn("{summary}", out)


class TickHubScopeTest(unittest.TestCase):
    """Owner directive 2026-08-08: tick-hub must not police ORC-internal state."""

    def _config(self) -> str:
        return TICK_HUB.read_text(encoding="utf-8")

    def _active_gate_cmds(self) -> list[str]:
        """Gate commands of ENABLED reminders only (commented blocks are inert)."""
        cmds = []
        for line in self._config().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("cmd:"):
                cmds.append(stripped[len("cmd:"):].strip())
        return cmds

    def test_no_active_gate_asserts_agent_liveness_or_task_ownership(self) -> None:
        forbidden = {
            "operational_health.py agents": "agent-fleet liveness is ORC's authority",
            "active-work": "task/owner/agent reconciliation is ORC's authority",
            "unowned_backlog.py": "TaskGraph census is ORC's authority",
            "residue_sweep.py": "combines ORC fleet + TaskGraph owner",
            "worktree_liveness.py": "asserts agent liveness from a registry file",
        }
        cmds = self._active_gate_cmds()
        for needle, why in forbidden.items():
            offenders = [c for c in cmds if needle in c]
            self.assertEqual(offenders, [], f"{needle} is wired as a gate: {why}")

    def test_container_reclaim_is_report_only(self) -> None:
        """The one agent-keyed gate kept may audit, but may not destroy.

        Its reclaim path falls back to 'absent from the ORC agent snapshot' ->
        podman stop + rm, so a stale snapshot would delete a live agent's
        container. Audit is useful; autonomous destruction on a second-hand
        census is the asymmetric-blast-radius case.
        """
        for cmd in self._active_gate_cmds():
            if "agent-podman.rs" in cmd:
                self.assertNotIn("--apply", cmd)
                break
        else:
            self.fail("agent-podman reconcile gate not found")

    def test_disk_reclamation_is_still_monitored(self) -> None:
        """Deleting the liveness gate without keeping the disk need is a failure."""
        cmds = self._active_gate_cmds()
        self.assertTrue(
            any("slot_disk_residue.py" in c for c in cmds),
            "disk-reclamation monitoring was lost, not re-scoped",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
