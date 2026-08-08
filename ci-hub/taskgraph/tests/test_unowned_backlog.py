#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / "ci-hub" / "taskgraph" / "unowned_backlog.py"
TICK_CONFIG = ROOT / "ci-hub" / "health" / "tick-hub.yaml"


class UnownedBacklogTest(unittest.TestCase):
    def make_db(
        self,
        tasks: list[tuple[str, str, str, str | None]],
        notes: list[tuple[str, str, str]] | None = None,
    ) -> Path:
        temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp.close()
        db = Path(temp.name)
        connection = sqlite3.connect(db)
        connection.executescript(
            """
            CREATE TABLE tasks (
                local_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                priority TEXT NOT NULL,
                owner TEXT
            );
            CREATE TABLE task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO tasks(local_id,status,priority,owner) VALUES(?,?,?,?)", tasks
        )
        connection.executemany(
            "INSERT INTO task_notes(task_id,content,created_at) VALUES(?,?,?)",
            notes or [],
        )
        connection.commit()
        connection.close()
        self.addCleanup(db.unlink)
        return db

    def run_checker(self, db: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CHECKER), "--db", str(db), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_planted_unowned_p0_is_one_of_one_and_alerts(self) -> None:
        db = self.make_db([("planted-p0", "BACKLOG", "P0", None)])
        result = self.run_checker(db, "--gate")
        self.assertEqual(1, result.returncode)
        self.assertIn("state=alert", result.stdout)
        self.assertIn("population=1/1", result.stdout)
        self.assertIn("p0=1/1", result.stdout)
        self.assertIn("p1=0/1", result.stdout)
        self.assertIn("unclassified=1/1", result.stdout)
        self.assertIn("unclassified_row=P0:planted-p0", result.stdout)

    def test_zero_qualifying_control_executes_as_zero_of_zero(self) -> None:
        db = self.make_db(
            [
                ("owned-p0", "BACKLOG", "P0", "worker"),
                ("unowned-p2", "BACKLOG", "P2", None),
                ("unowned-open-p0", "OPEN", "P0", None),
            ]
        )
        result = self.run_checker(db, "--gate")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("state=clean", result.stdout)
        for field in (
            "population",
            "p0",
            "p1",
            "actionable",
            "blocked",
            "stale_premise",
            "already_implemented",
            "classified",
            "unclassified",
        ):
            self.assertIn(f"{field}=0/0", result.stdout)

    def test_all_four_classifications_carry_one_denominator(self) -> None:
        tasks = [
            ("action", "BACKLOG", "P0", None),
            ("blocked", "BACKLOG", "P1", ""),
            ("stale", "BACKLOG", "P1", "   "),
            ("implemented", "backlog", "p1", None),
        ]
        notes = [
            (
                local_id,
                "CLASSIFICATION [complete-unowned-high-priority-drain 2026-08-07]: "
                f"{classification} — fixture",
                f"2026-08-07T00:00:0{index}Z",
            )
            for index, (local_id, classification) in enumerate(
                zip(
                    ("action", "blocked", "stale", "implemented"),
                    ("ACTIONABLE", "BLOCKED", "STALE-PREMISE", "ALREADY-IMPLEMENTED"),
                )
            )
        ]
        db = self.make_db(tasks, notes)
        result = self.run_checker(db, "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual({"numerator": 4, "denominator": 4}, report["population"])
        self.assertEqual({"numerator": 1, "denominator": 4}, report["priorities"]["P0"])
        self.assertEqual({"numerator": 3, "denominator": 4}, report["priorities"]["P1"])
        for classification in (
            "ACTIONABLE",
            "BLOCKED",
            "STALE-PREMISE",
            "ALREADY-IMPLEMENTED",
        ):
            self.assertEqual(
                {"numerator": 1, "denominator": 4},
                report["classifications"][classification],
            )
        self.assertEqual(0, report["unclassified"]["numerator"])
        self.assertEqual(4, report["unclassified"]["denominator"])

    def test_latest_malformed_classification_does_not_fall_back(self) -> None:
        db = self.make_db(
            [("changed", "BACKLOG", "P1", None)],
            [
                (
                    "changed",
                    "CLASSIFICATION [complete-unowned-high-priority-drain old]: BLOCKED",
                    "2026-08-07T00:00:00Z",
                ),
                (
                    "changed",
                    "CLASSIFICATION [complete-unowned-high-priority-drain new]: MAYBE",
                    "2026-08-07T00:01:00Z",
                ),
            ],
        )
        result = self.run_checker(db, "--gate")
        self.assertEqual(1, result.returncode)
        self.assertIn("blocked=0/1", result.stdout)
        self.assertIn("unclassified=1/1", result.stdout)

    def test_complete_query_has_no_hidden_limit(self) -> None:
        # Exceeds SQLite's common 999-bound-variable cap as well as any
        # plausible display limit.  Classification retrieval must be a join,
        # not a generated IN list that makes a large complete census fail.
        tasks = [(f"task-{index:04}", "BACKLOG", "P1", None) for index in range(1105)]
        notes = [
            (
                local_id,
                "CLASSIFICATION [complete-unowned-high-priority-drain fixture]: BLOCKED",
                "2026-08-07T00:00:00Z",
            )
            for local_id, _status, _priority, _owner in tasks
        ]
        db = self.make_db(tasks, notes)
        result = self.run_checker(db, "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual({"numerator": 1105, "denominator": 1105}, report["population"])
        self.assertEqual(
            {"numerator": 1105, "denominator": 1105},
            report["classifications"]["BLOCKED"],
        )
        self.assertEqual(1105, len(report["rows"]))

    def test_census_holds_no_standing_tick_authority(self) -> None:
        """This census must never regain a tick-hub gate.

        It used to have one, and that is exactly what went wrong: reading the
        TaskGraph through a hardcoded path, it reported a 108-task backlog
        against a database the orchestrator could not see for a whole session.
        The gate was withdrawn on 2026-08-08 under the owner directive that
        tick-hub must not police orc-internal state -- ORC is the authority on
        tasks and ownership, so a second observer can only manufacture
        disagreement, and that disagreement reaches a coordinator mandated to
        close and respawn agents without asking.

        The script stays useful and stays tested; what is withdrawn is its
        standing authority to interrupt. So this asserts the ABSENCE of the
        wiring, and that the removal is recorded rather than silently dropped.
        """

        config = TICK_CONFIG.read_text()
        self.assertNotIn("  - name: unowned_high_priority_backlog\n", config)
        self.assertNotIn("unowned_backlog.py --gate", config)
        self.assertIn("unowned_high_priority_backlog", config)  # the removal record


if __name__ == "__main__":
    unittest.main()
