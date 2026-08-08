#!/usr/bin/env python3
"""Scorecard schema checker with an EXPLICIT, STATED population.

A refusal count is unquotable unless the set it counted over is stated. This
checker was rewritten after a measured incident: an earlier version derived its
population by globbing relative to its own file location, so a copy left in
``/tmp`` enumerated 7 stray CSVs and reported "7 schema violations", while the
same logic run against the committed tree enumerates 4. Both numbers were
"correct"; neither was comparable, because neither carried its population.

Reproduced at 2026-08-07: ``/tmp/*scorecard*.csv`` matched exactly 7 files
(backend-parity, backend-parity-lf, dbi-matrix-relocate, reconcile-pr79,
scorecard-migration-contract, w2, w7-pr48) and
``compat-envelope/*scorecard*.csv`` matched exactly 4. The 7 was the temp
directory, not a finding.

Three rules follow, and this tool implements all three:

1. **The root is explicit.** It is an argument. Its default is the *repository*
   root, discovered with ``git rev-parse --show-toplevel``, never ``__file__``
   and never the current working directory. Copying this script elsewhere does
   not move its population; running it from elsewhere does not either.
2. **The population is stated, not implied.** Every run prints the resolved
   root, the pattern, and every enumerated path, before any verdict.
3. **A count never travels without its denominator.** Violations are always
   reported as ``k of N``.

Usage::

    check_scorecard_schema.py                      # repo root, auto-discovered
    check_scorecard_schema.py --root /path/to/tree # explicit population
    check_scorecard_schema.py --json

Exit codes: 0 clean, 1 violations found, 2 the population could not be
established (which is refused, not reported as zero violations).
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Columns every scorecard must carry, whatever else it adds. Derived from the
#: intersection of the four committed scorecards at 2026-08-07; newer producers
#: append columns (tier, verify_compare, ...) and that is allowed, but none of
#: these may go missing.
CORE_COLUMNS = (
    "run_id",
    "run_utc",
    "hermit_sha",
    "reverie_sha",
    "dirty",
    "run_mode",
    "lane",
    "bucket",
    "test_id",
    "test_mode",
    "backend",
    "cell_state",
    "outcome",
    "deterministic",
    "parity",
    "output_hash",
    "duration_ms",
    "max_rss_kb",
    "reason",
)

#: Where scorecards live, relative to the tree root. Part of the population
#: definition, so it is printed with the results rather than left implicit.
SCORECARD_SUBDIR = "compat-envelope"
SCORECARD_PATTERN = "*scorecard*.csv"


class PopulationError(RuntimeError):
    """The set to check over could not be established. Refuse; do not report 0."""


@dataclass
class FileResult:
    path: str
    columns: int
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing


@dataclass
class Report:
    root: str
    pattern: str
    files: list[FileResult] = field(default_factory=list)

    @property
    def population(self) -> int:
        return len(self.files)

    @property
    def violations(self) -> list[FileResult]:
        return [f for f in self.files if not f.ok]

    def render(self) -> str:
        lines = [
            "scorecard schema check",
            f"  resolved root : {self.root}",
            f"  pattern       : {self.pattern}",
            f"  population    : {self.population} file(s) enumerated",
        ]
        for f in self.files:
            lines.append(f"      {f.path}  ({f.columns} cols)")
        lines.append("")
        for f in self.violations:
            lines.append(f"  VIOLATION {f.path}: missing {','.join(f.missing)}")
        lines.append(
            f"  result        : {len(self.violations)} of {self.population} "
            "scorecard(s) violate the core schema"
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "root": self.root,
                "pattern": self.pattern,
                "population": self.population,
                "files": [
                    {"path": f.path, "columns": f.columns, "missing": list(f.missing)}
                    for f in self.files
                ],
                "violations": len(self.violations),
            },
            indent=2,
            sort_keys=True,
        )


def repo_root(start: Path | None = None) -> Path:
    """The repository root.

    Deliberately NOT ``Path(__file__).parent`` (a copy of this script would
    take its population with it) and NOT ``Path.cwd()`` (the population would
    follow the caller). ``git rev-parse`` answers for the tree, which is the
    thing the population is a property of.
    """
    start = start or Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PopulationError(f"cannot discover repository root from {start}: {error}") from error
    return Path(out.stdout.strip())


def enumerate_population(root: Path) -> list[Path]:
    directory = root / SCORECARD_SUBDIR
    if not directory.is_dir():
        raise PopulationError(f"no {SCORECARD_SUBDIR}/ under {root}; population undefined")
    # Sorted so the stated population is stable across filesystems -- an
    # unordered population is not reproducible even when it is correct.
    return sorted(directory.glob(SCORECARD_PATTERN))


def check(root: Path) -> Report:
    root = root.resolve()
    report = Report(root=str(root), pattern=f"{SCORECARD_SUBDIR}/{SCORECARD_PATTERN}")
    for path in enumerate_population(root):
        try:
            with path.open(newline="") as handle:
                header = next(csv.reader(handle), [])
        except OSError as error:
            raise PopulationError(f"enumerated {path} but could not read it: {error}") from error
        missing = tuple(c for c in CORE_COLUMNS if c not in header)
        # Paths are reported relative to the stated root, so two runs from
        # different working directories produce identical output.
        report.files.append(
            FileResult(path=str(path.relative_to(root)), columns=len(header), missing=missing)
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="tree root to resolve the population against (default: git repo root)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = args.root if args.root is not None else repo_root()
        report = check(root)
    except PopulationError as error:
        print(f"check-scorecard-schema: REFUSED: {error}", file=sys.stderr)
        return 2

    print(report.to_json() if args.json else report.render())
    return 1 if report.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
