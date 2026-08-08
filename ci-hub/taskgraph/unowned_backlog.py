#!/usr/bin/env python3
"""Guard the complete unowned P0/P1 BACKLOG census and its classifications.

The four classifications are durable decisions written by the
``complete-unowned-high-priority-drain`` census.  This checker never guesses a
classification from a title, tag, or status: a live qualifying row without a
well-formed latest classification note is UNCLASSIFIED and makes the gate
fail.

Every count is emitted as numerator/denominator evidence.  In particular, an
empty qualifying population is a measured clean result (0/0), not a skipped
check.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


# The database is resolved, never hardcoded.  A literal here pointed at the
# predecessor's frozen ``hermit.db`` for a whole session, reporting a fixed 108
# that could never clear.  ci-hub/lib/taskgraph_db.py is the one resolver every
# consumer calls; its refusal maps onto this module's could-not-measure state.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import taskgraph_db  # noqa: E402

CLASSES = (
    "ACTIONABLE",
    "BLOCKED",
    "STALE-PREMISE",
    "ALREADY-IMPLEMENTED",
)
CLASSIFICATION_PREFIX = "CLASSIFICATION [complete-unowned-high-priority-drain "
CLASSIFICATION_RE = re.compile(
    r"\ACLASSIFICATION \[complete-unowned-high-priority-drain [^\]\r\n]+\]:\s*"
    r"(ACTIONABLE|BLOCKED|STALE-PREMISE|ALREADY-IMPLEMENTED)\b"
)


class CensusUnavailable(RuntimeError):
    """The complete census could not be proved from one database snapshot."""


@dataclass(frozen=True)
class EvidenceCount:
    numerator: int
    denominator: int

    def fraction(self) -> str:
        return f"{self.numerator}/{self.denominator}"


@dataclass(frozen=True)
class CensusRow:
    local_id: str
    priority: str
    classification: str | None
    classification_note_id: int | None
    classification_note_created_at: str | None


@dataclass(frozen=True)
class CensusReport:
    population: EvidenceCount
    aggregate_population: EvidenceCount
    priorities: dict[str, EvidenceCount]
    classifications: dict[str, EvidenceCount]
    classified: EvidenceCount
    unclassified: EvidenceCount
    rows: tuple[CensusRow, ...]

    @property
    def unclassified_rows(self) -> tuple[CensusRow, ...]:
        return tuple(row for row in self.rows if row.classification is None)

    @property
    def clean(self) -> bool:
        return self.unclassified.numerator == 0

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "query_scope": {
                "status": "BACKLOG",
                "priorities": ["P0", "P1"],
                "owner": "NULL_OR_BLANK",
                "limit": None,
                "snapshot": "single_read_transaction",
            },
            "population": asdict(self.population),
            "aggregate_population": asdict(self.aggregate_population),
            "priorities": {key: asdict(value) for key, value in self.priorities.items()},
            "classifications": {
                key: asdict(value) for key, value in self.classifications.items()
            },
            "classified": asdict(self.classified),
            "unclassified": {
                **asdict(self.unclassified),
                "rows": [asdict(row) for row in self.unclassified_rows],
            },
            "rows": [asdict(row) for row in self.rows],
        }


def _classification(note: str | None) -> str | None:
    if note is None:
        return None
    match = CLASSIFICATION_RE.match(note)
    return match.group(1) if match else None


def load_report(db: Path) -> CensusReport:
    """Read cursor, aggregate, and classification notes in one snapshot."""

    if not db.exists():
        raise CensusUnavailable(f"no TaskGraph database at {db}")

    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cursor = connection.cursor()
        cursor.execute("BEGIN")
        task_rows = cursor.execute(
            """
            SELECT local_id, UPPER(TRIM(priority)) AS priority
            FROM tasks
            WHERE UPPER(TRIM(status)) = 'BACKLOG'
              AND UPPER(TRIM(priority)) IN ('P0', 'P1')
              AND (owner IS NULL OR TRIM(owner) = '')
            ORDER BY local_id
            """
        ).fetchall()
        aggregate = int(
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE UPPER(TRIM(status)) = 'BACKLOG'
                  AND UPPER(TRIM(priority)) IN ('P0', 'P1')
                  AND (owner IS NULL OR TRIM(owner) = '')
                """
            ).fetchone()[0]
        )

        latest_notes: dict[str, tuple[int, str, str]] = {}
        if task_rows:
            note_rows = cursor.execute(
                """
                SELECT n.task_id, n.id, n.created_at, n.content
                FROM task_notes AS n
                JOIN tasks AS t ON t.local_id = n.task_id
                WHERE UPPER(TRIM(t.status)) = 'BACKLOG'
                  AND UPPER(TRIM(t.priority)) IN ('P0', 'P1')
                  AND (t.owner IS NULL OR TRIM(t.owner) = '')
                  AND n.content LIKE ?
                ORDER BY n.task_id, n.created_at, n.id
                """,
                (f"{CLASSIFICATION_PREFIX}%",),
            ).fetchall()
            for task_id, note_id, created_at, content in note_rows:
                # Ordered oldest-to-newest: the final durable classification
                # note is authoritative.  A malformed replacement is not
                # silently hidden by falling back to an older note.
                latest_notes[str(task_id)] = (int(note_id), str(created_at), str(content))

        connection.rollback()
        connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        raise CensusUnavailable(f"cannot read complete TaskGraph census: {error}") from error

    if len(task_rows) != aggregate:
        raise CensusUnavailable(
            f"cursor walk {len(task_rows)} != aggregate {aggregate} in one snapshot"
        )

    rows: list[CensusRow] = []
    for local_id_raw, priority_raw in task_rows:
        local_id = str(local_id_raw)
        note = latest_notes.get(local_id)
        rows.append(
            CensusRow(
                local_id=local_id,
                priority=str(priority_raw),
                classification=_classification(note[2]) if note else None,
                classification_note_id=note[0] if note else None,
                classification_note_created_at=note[1] if note else None,
            )
        )

    denominator = len(rows)
    priorities = {
        priority: EvidenceCount(
            sum(row.priority == priority for row in rows), denominator
        )
        for priority in ("P0", "P1")
    }
    classifications = {
        classification: EvidenceCount(
            sum(row.classification == classification for row in rows), denominator
        )
        for classification in CLASSES
    }
    classified_count = sum(row.classification is not None for row in rows)
    unclassified_count = denominator - classified_count
    return CensusReport(
        population=EvidenceCount(denominator, denominator),
        aggregate_population=EvidenceCount(aggregate, denominator),
        priorities=priorities,
        classifications=classifications,
        classified=EvidenceCount(classified_count, denominator),
        unclassified=EvidenceCount(unclassified_count, denominator),
        rows=tuple(rows),
    )


def _summary(report: CensusReport) -> str:
    class_summary = " ".join(
        f"{classification.lower()}={report.classifications[classification].fraction()}"
        for classification in CLASSES
    )
    return (
        f"population={report.population.fraction()} "
        f"p0={report.priorities['P0'].fraction()} "
        f"p1={report.priorities['P1'].fraction()} "
        f"{class_summary} "
        f"unclassified={report.unclassified.fraction()}"
    )


def _emit_gate(report: CensusReport) -> None:
    print(f"state={'clean' if report.clean else 'alert'}")
    print(f"summary={_summary(report)}")
    print(f"population={report.population.fraction()}")
    print(f"p0={report.priorities['P0'].fraction()}")
    print(f"p1={report.priorities['P1'].fraction()}")
    for classification in CLASSES:
        key = classification.lower().replace("-", "_")
        print(f"{key}={report.classifications[classification].fraction()}")
    print(f"classified={report.classified.fraction()}")
    print(f"unclassified={report.unclassified.fraction()}")
    for row in report.unclassified_rows:
        print(f"unclassified_row={row.priority}:{row.local_id}")


def _emit_human(report: CensusReport) -> None:
    print("UNOWNED P0/P1 BACKLOG CENSUS")
    print(
        "  population (cursor==aggregate; no LIMIT) ... "
        f"{report.population.fraction()}"
    )
    print(f"  P0 .......................................... {report.priorities['P0'].fraction()}")
    print(f"  P1 .......................................... {report.priorities['P1'].fraction()}")
    for classification in CLASSES:
        print(
            f"  {classification:<44}"
            f"{report.classifications[classification].fraction()}"
        )
    print(f"  CLASSIFIED .................................. {report.classified.fraction()}")
    print(f"  UNCLASSIFIED ................................ {report.unclassified.fraction()}")
    for row in report.unclassified_rows:
        print(f"    {row.priority} {row.local_id}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="emit the complete typed report")
    parser.add_argument(
        "--gate", action="store_true", help="emit tick-hub fields and fail on UNCLASSIFIED"
    )
    args = parser.parse_args(argv)

    try:
        report = load_report(taskgraph_db.resolve(args.db).path)
    except (CensusUnavailable, taskgraph_db.TaskGraphUnavailable) as error:
        if args.gate:
            print("state=unverifiable")
            print(f"summary={error}")
        else:
            print(f"UNVERIFIABLE: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    elif args.gate:
        _emit_gate(report)
    else:
        _emit_human(report)
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
