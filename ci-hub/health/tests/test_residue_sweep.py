#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import residue_sweep


class ResidueSweepTest(unittest.TestCase):
    def test_exact_decline_marker_routes_to_coordinator(self) -> None:
        notes = [residue_sweep.NoteRow(7, "task-a", "worker", "file left untracked")]
        result = residue_sweep.classify_notes(notes, set())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].actionable)
        self.assertEqual(result[0].route_authority, "coordinator")
        self.assertEqual(result[0].evidence, "source_note_ids=7")

    def test_owner_authorization_routes_to_owner_queue(self) -> None:
        notes = [residue_sweep.NoteRow(8, "task-b", "worker", "needs authorization")]
        result = residue_sweep.classify_notes(notes, set())
        self.assertEqual(result[0].route_task, residue_sweep.OWNER_QUEUE)
        self.assertEqual(result[0].route_authority, "owner")

    def test_explicit_nothing_left_is_no_action(self) -> None:
        notes = [
            residue_sweep.NoteRow(
                9, "task-c", "worker", "Nothing uncommitted; only build output existed."
            )
        ]
        result = residue_sweep.classify_notes(notes, set())
        self.assertEqual(result[0].disposition, "no-action")
        self.assertFalse(result[0].actionable)

    def test_previous_disposition_suppresses_source_note(self) -> None:
        notes = [residue_sweep.NoteRow(10, "task-d", "worker", "not committed")]
        self.assertEqual(residue_sweep.classify_notes(notes, {10}), [])

    def test_same_task_notes_are_aggregated(self) -> None:
        notes = [
            residue_sweep.NoteRow(11, "task-e", "worker", "not committed"),
            residue_sweep.NoteRow(12, "task-e", "worker", "untracked artifact"),
        ]
        result = residue_sweep.classify_notes(notes, set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence, "source_note_ids=11,12")

    def test_live_owner_prevents_slot_hit(self) -> None:
        registry = {
            "slots": {
                "one": {
                    "status": "active",
                    "agents": [{"name": "live", "task": "task"}],
                }
            }
        }
        self.assertEqual(residue_sweep.classify_slots(registry, {"live"}, set()), [])

    def test_process_cwd_prevents_dead_owner_slot_hit(self) -> None:
        registry = {
            "slots": {
                "two": {
                    "status": "active",
                    "agents": [{"name": "dead", "task": "task"}],
                }
            }
        }
        slot = (residue_sweep.ROOT / "worktrees" / "two").resolve()
        self.assertEqual(
            residue_sweep.classify_slots(registry, set(), {slot / "hermit"}), []
        )

    def test_dead_owner_and_no_process_routes_slot_without_cleaning(self) -> None:
        registry = {
            "slots": {
                "three": {
                    "status": "held",
                    "agents": [{"name": "dead", "task": "task-z"}],
                }
            }
        }
        result = residue_sweep.classify_slots(registry, set(), set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, "held-slot-dead-owner")
        self.assertEqual(result[0].route_authority, "coordinator-slot-lifecycle")
        self.assertIn("live_owner=0 process_cwd_under_slot=0", result[0].evidence)

    def test_released_slot_is_not_residue(self) -> None:
        registry = {
            "slots": {
                "four": {
                    "status": "released",
                    "agents": [{"name": "dead", "task": "task"}],
                }
            }
        }
        self.assertEqual(residue_sweep.classify_slots(registry, set(), set()), [])

    def test_every_item_has_typed_disposition(self) -> None:
        notes = [
            residue_sweep.NoteRow(13, "task-f", "worker", "untracked"),
            residue_sweep.NoteRow(14, "task-g", "worker", "nothing to commit"),
        ]
        for item in residue_sweep.classify_notes(notes, set()):
            self.assertIn(item.disposition, {"route", "no-action"})
            if item.actionable:
                self.assertNotEqual(item.route_authority, "none")
                self.assertNotEqual(item.route_task, "none")


if __name__ == "__main__":
    unittest.main()
