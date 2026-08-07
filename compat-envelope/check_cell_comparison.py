#!/usr/bin/env python3
"""A cell that carries a comparison VERDICT must carry the REFERENCE it compared against.

THE CLASS THIS CLOSES. A cell that observes without comparing cannot fail, so it
must not be green. The audits keep finding instances because the emission path
permits the state at all: a row could record ``stdout_parity=1`` while recording
nothing about what it matched, and a reader could not tell a genuine match from
a row where both sides happened to be empty, nor re-check the claim later. On
the population enumerated below, 1,317 cells were green with no recorded
reference before the schema was tightened.

WHAT IS AND IS NOT CHECKED HERE. This is the ROW-level invariant only. The
HEADER/core-schema axis belongs to ``check_scorecard_schema.py``, which reads
only the header and cannot see a row; the two are complementary and neither
subsumes the other. Deliberately, this tool asserts nothing about tier,
relaxation set, self-determinism, or 0/0 dimensions — those are separate
emission-time requirements with their own guards.

WHY A VERDICT OF ``0`` NEEDS A REFERENCE TOO, not just a green. A recorded
mismatch also asserts that a comparison happened and that the other side was
known. "It differed from something I did not record" is not a reproducible
refusal any more than "it matched something I did not record" is a reproducible
pass. Both are refused; only a BLANK verdict — an honest no-result — is allowed
to carry no reference.

POPULATION DISCIPLINE, adopted from ``check_scorecard_schema.py`` because it was
learned from a real incident: the root is an explicit argument defaulting to the
repository root via ``git rev-parse --show-toplevel`` (never ``__file__``, never
the cwd), every enumerated path is printed before any verdict, and counts are
always reported as ``k of N``.

Exit codes:
  0  every enumerated file satisfies the invariant
  1  at least one violation (a verdict with no reference, a schema that cannot
     express a required comparison, or zero verdicts over a non-empty row
     population)
  2  REFUSED — the population could not be established
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCORECARD_SUBDIR = "compat-envelope"
SCORECARD_PATTERN = "*scorecard*.csv"

#: Columns that record a comparison VERDICT. Any non-blank value here asserts
#: that a comparison was performed.
VERDICT_COLUMNS = ("stdout_parity", "tool_count_parity")
#: The column that records WHAT the verdict was measured against.
REFERENCE_COLUMN = "ref_output_hash"
#: Comparison dimensions the published certification standard names.  Merely
#: having a generic outcome/reason column cannot represent these facts: each
#: needs its own typed verdict field before this guard may claim the schema can
#: express it.
REQUIRED_SCHEMA_CAPABILITIES = {
    "exit_code": "exit_code_parity",
    "detlog": "detlog_parity",
    "oracle": "oracle_verdict",
}


class PopulationError(RuntimeError):
    """The set to be checked could not be established. Refused, never counted as clean."""


@dataclass
class FileResult:
    path: str
    rows: int = 0
    verdicts: int = 0
    unreferenced: int = 0
    schema_cannot_express: bool = False
    missing_schema_capabilities: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.unreferenced == 0 and not self.schema_cannot_express


@dataclass
class Report:
    root: str
    pattern: str
    files: list[FileResult] = field(default_factory=list)

    @property
    def violations(self) -> list[FileResult]:
        return [f for f in self.files if not f.ok]

    @property
    def total_rows(self) -> int:
        return sum(f.rows for f in self.files)

    @property
    def total_verdicts(self) -> int:
        return sum(f.verdicts for f in self.files)

    @property
    def zero_verdict_population(self) -> bool:
        """A non-empty population with nothing compared is a refusal, not green."""
        return self.total_rows > 0 and self.total_verdicts == 0

    @property
    def ok(self) -> bool:
        return not self.violations and not self.zero_verdict_population

    def render(self) -> str:
        out = ["cell comparison-evidence check",
               f"  resolved root : {self.root}",
               f"  pattern       : {self.pattern}",
               f"  population    : {self.total_rows} row(s) across "
               f"{len(self.files)} file(s); {self.total_verdicts} verdict(s)"]
        for f in self.files:
            notes = []
            if f.schema_cannot_express:
                missing = ", ".join(f.missing_schema_capabilities)
                notes.append(f"SCHEMA CANNOT EXPRESS: {missing}")
            if f.unreferenced:
                notes.append(f"{f.unreferenced} of {f.verdicts} verdict(s) carry NO reference")
            note = f"  {'; '.join(notes)}" if notes else ""
            out.append(f"      {f.path}  ({f.rows} rows, {f.verdicts} verdicts){note}")
            for ex in f.examples[:3]:
                out.append(f"          e.g. {ex}")
        total_u = sum(f.unreferenced for f in self.files)
        out.append("")
        verdict_state = "REFUSED (non-empty population)" if self.zero_verdict_population else "satisfied"
        out.append(f"  result        : {total_u} of {self.total_verdicts} verdict(s) lack a "
                   f"reference; {len(self.violations)} of {len(self.files)} file(s) "
                   f"violate; zero-verdict predicate {verdict_state} over "
                   f"{self.total_rows} row(s)")
        return "\n".join(out)

    def to_json(self) -> str:
        return json.dumps({
            "root": self.root, "pattern": self.pattern,
            "files": [f.__dict__ for f in self.files],
            "violating_files": len(self.violations), "population": len(self.files),
            "rows": self.total_rows, "verdicts": self.total_verdicts,
            "zero_verdict_population": self.zero_verdict_population,
        }, indent=2, default=list)


def repo_root(start: Path | None = None) -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=str(start or Path.cwd()), capture_output=True, text=True, timeout=30)
    except OSError as error:
        raise PopulationError(f"cannot run git to discover the root: {error}") from error
    if out.returncode != 0:
        raise PopulationError("not inside a git repository; pass --root explicitly")
    return Path(out.stdout.strip())


def enumerate_population(root: Path) -> list[Path]:
    directory = root / SCORECARD_SUBDIR
    if not directory.is_dir():
        raise PopulationError(f"{directory} is not a directory; the population is undefined")
    found = sorted(directory.glob(SCORECARD_PATTERN))
    if not found:
        raise PopulationError(f"no file matched {SCORECARD_SUBDIR}/{SCORECARD_PATTERN} under {root}")
    return found


def check_file(path: Path, root: Path) -> FileResult:
    result = FileResult(path=str(path.relative_to(root)))
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        verdict_cols = [c for c in VERDICT_COLUMNS if c in header]
        has_reference = REFERENCE_COLUMN in header
        result.missing_schema_capabilities = [
            name for name, column in REQUIRED_SCHEMA_CAPABILITIES.items()
            if column not in header
        ]
        # A schema carrying a verdict column but no reference column cannot
        # express a qualified cell AT ALL: every verdict it records is
        # unreferenceable by construction. That is a violation of the invariant
        # at the schema level, not merely in the data.
        if verdict_cols and not has_reference:
            result.missing_schema_capabilities.insert(0, f"reference(no {REFERENCE_COLUMN})")
        result.schema_cannot_express = bool(result.missing_schema_capabilities)
        for row in reader:
            result.rows += 1
            for col in verdict_cols:
                if (row.get(col) or "").strip():
                    result.verdicts += 1
                    if not has_reference or not (row.get(REFERENCE_COLUMN) or "").strip():
                        result.unreferenced += 1
                        if len(result.examples) < 3:
                            result.examples.append(
                                f"{row.get('backend','?')}/{row.get('test_id','?')}: "
                                f"{col}={row[col]!r} with no {REFERENCE_COLUMN}")
    return result


def check(root: Path) -> Report:
    root = root.resolve()
    report = Report(root=str(root), pattern=f"{SCORECARD_SUBDIR}/{SCORECARD_PATTERN}")
    for path in enumerate_population(root):
        try:
            report.files.append(check_file(path, root))
        except OSError as error:
            raise PopulationError(f"enumerated {path} but could not read it: {error}") from error
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = check(args.root or repo_root())
    except PopulationError as error:
        print(f"check-cell-comparison: REFUSED: {error}", file=sys.stderr)
        return 2
    print(report.to_json() if args.json else report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
