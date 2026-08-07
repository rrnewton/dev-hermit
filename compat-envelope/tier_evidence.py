#!/usr/bin/env python3
"""A tier claim must carry evidence for EVERY component it names.

WHAT WAS WRONG. `check-scorecard-tier.py` validates the tier VOCABULARY: each row
must carry a value from a known set, and a raw pass is promoted to "qualified
green" iff that value is one of the two qualifying tiers (lines 108-115). It
never reads an evidence column. So `comparison_tier` is a self-declared label
with nothing binding it to the comparison actually performed -- the label-as-cache
failure. Reproduced on 2026-08-07 against main: four planted rows all claiming
`full-stdout-info-stack-heap`, one of them carrying neither stdout nor INFO,
scored `qualified_green=4/4` at rc=0.

This module is the missing half and is deliberately SEPARATE: the vocabulary
check reads only the tier column and can run on any file; this one reads the
evidence columns and needs the schema. Neither subsumes the other, the same
split `check_cell_comparison.py` documents against `check_scorecard_schema.py`.

WHAT EACH TIER CLAIMS -- taken from the tier NAMES, which are self-describing,
and from `strict_verdict.STRICT_COMPONENTS`, so this file introduces no third
vocabulary:

    full-stdout-info-stack-heap        stdout + INFO + stack + heap, EVERY run
    stdout-info-stack-heap-spot-check  stdout + INFO every run; stack/heap on a CADENCE

THE CADENCE IS ENFORCED HERE, NOT DESCRIBED. `spot-check-cadence.py` computes
CURRENT/STALE/NEVER correctly but its `main()` returns 0 unconditionally, so
nothing has ever refused a stale cell. This module calls its `age_state()` and
turns STALE and NEVER into violations. `CADENCE_TRIGGERS` there remains three
prose strings that are printed and never evaluated; the one trigger that IS
mechanically checkable -- a receipt bound to a dirty tree -- is enforced below.

A DIRTY RECEIPT IS REFUSED OUTRIGHT, ahead of any age test. A `-dirty` SHA does
not identify a tree, so the run cannot be reproduced and re-measurement cannot be
targeted; ageing it would imply it was once valid. At 2026-08-07 all three of the
41 ledger rows that were CURRENT carried `gf89c69766371-dirty`, so enforcing this
takes the qualifying spot-check population from 3 to 0. That is a DEFINITION
CORRECTION, recorded old-vs-new, not a regression: the three were never
reproducible evidence.

WHAT THIS CANNOT DO TODAY, stated because it bounds every green it emits. The
scorecard core schema carries `stdout_parity` and `compared_log_messages` but has
NO stack or heap column. So FULL's stack/heap half is unverifiable by
construction, and a FULL claim on a real scorecard is refused as
`schema-cannot-express` rather than passed. The columns are read by name here so
that widening the schema is the only change needed; nothing is back-filled,
because inventing the evidence is the defect this exists to stop.

Exit codes:
  0  every enumerated row's tier claim is fully evidenced
  1  at least one claim is not
  2  REFUSED -- the population or the vocabulary could not be established
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Import a sibling module, including hyphenated ones that `import` cannot name."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise PopulationError(f"cannot load {filename}; the tier vocabulary is unavailable")
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: a module absent from sys.modules cannot resolve its own
    # `__module__` during @dataclass processing, so a dataclass in a sibling loaded
    # this way dies with a bare AttributeError. Costs nothing; avoids a trap that
    # only appears when this file is imported rather than run.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PopulationError(RuntimeError):
    """The set to be checked, or the vocabulary to check it against, is unavailable."""


_tier = _load("_check_scorecard_tier", "check-scorecard-tier.py")
_cadence = _load("_spot_check_cadence", "spot-check-cadence.py")
_strict = _load("_strict_verdict", "strict_verdict.py")

FULL = _tier.FULL
SPOT_CHECK = _tier.SPOT_CHECK
QUALIFYING = _tier.QUALIFYING

#: Cadence classes for a component within a tier.
EVERY_RUN, CADENCED = "every-run", "cadenced"

#: What each qualifying tier claims. Keys are `strict_verdict.STRICT_COMPONENTS`.
TIER_CLAIMS = {
    FULL: {"stdout": EVERY_RUN, "info_log": EVERY_RUN,
           "stack": EVERY_RUN, "heap": EVERY_RUN},
    SPOT_CHECK: {"stdout": EVERY_RUN, "info_log": EVERY_RUN,
                 "stack": CADENCED, "heap": CADENCED},
}

#: Where a per-run component's evidence lives on the row. `stack`/`heap` have no
#: column in the core schema today; named here so widening the schema is the only
#: change required, and so their absence is reported as a SCHEMA fault, not a blank.
ROW_EVIDENCE = {
    "stdout": "stdout_parity",
    "info_log": "compared_log_messages",
    "stack": "stack_parity",
    "heap": "heap_parity",
}

#: A recorded comparison of zero records is not evidence that a comparison happened.
_EMPTY_COUNTS = {"0|0", "0/0", "0"}
_BLANKS = {"", "-", "n/a", "none", "null"}


def _blank(value) -> bool:
    return (value or "").strip().lower() in _BLANKS


def is_dirty_sha(sha: str) -> bool:
    """A receipt SHA that does not identify a reproducible tree."""
    s = (sha or "").strip()
    return not s or s.lower().endswith("-dirty") or "dirty" in s.lower()


def row_component_evidenced(row: dict, header: list[str], component: str) -> tuple[bool, str]:
    """(ok, reason). Distinguishes a missing COLUMN from a blank VALUE, deliberately:
    the first is a schema that cannot express the claim, the second is a producer
    that did not make the measurement. Collapsing them hides which one to fix."""
    column = ROW_EVIDENCE[component]
    if column not in header:
        return False, f"schema-cannot-express:{component} (no {column!r} column)"
    value = (row.get(column) or "").strip()
    if _blank(value):
        return False, f"missing:{component} ({column} is blank)"
    if component == "info_log" and value in _EMPTY_COUNTS:
        return False, f"empty-comparison:{component} ({column}={value!r} compared nothing)"
    return True, ""


@dataclass
class Violation:
    file: str
    line: int
    test_id: str
    backend: str
    tier: str
    reasons: list[str]

    def render(self) -> str:
        return (f"{self.file}:{self.line}: {self.backend}/{self.test_id} "
                f"claims {self.tier} but {'; '.join(self.reasons)}")


@dataclass
class Report:
    root: str
    files: list[str] = field(default_factory=list)
    rows: int = 0
    claims: int = 0
    upheld: int = 0
    violations: list[Violation] = field(default_factory=list)

    def render(self) -> str:
        out = ["tier-evidence check (does a tier claim carry every component it names?)",
               f"  resolved root : {self.root}",
               f"  population    : {len(self.files)} scorecard(s) enumerated"]
        out += [f"      {f}" for f in self.files]
        out.append("")
        out.append(f"  qualifying tier claims : {self.claims} of {self.rows} rows")
        out.append(f"  fully evidenced        : {self.upheld} of {self.claims}")
        out.append(f"  NOT evidenced          : {len(self.violations)} of {self.claims}")
        return "\n".join(out)


def check_ledger_receipt(key, ledger: dict, now: _dt.datetime,
                         cadence_days: int) -> tuple[bool, str]:
    """Cadenced stack/heap evidence: a receipt must exist, be CLEAN, and be CURRENT.

    Order matters. Dirtiness is tested BEFORE age: a `-dirty` receipt is not stale
    evidence, it is evidence about no identifiable tree, and reporting it as STALE
    would imply it had once been valid.
    """
    receipt = ledger.get(key)
    if not receipt:
        return False, "cadence:NEVER (no spot-check receipt on record)"
    if is_dirty_sha(receipt.get("hermit_sha", "")):
        return False, (f"dirty-receipt (hermit_sha="
                       f"{receipt.get('hermit_sha','') or '<blank>'!r} does not identify a tree)")
    state, age = _cadence.age_state(receipt.get("spot_check_utc", ""), now, cadence_days)
    if state != _cadence.CURRENT:
        return False, f"cadence:{state} (age={age} d, cadence={cadence_days} d)"
    return True, ""


def check_row(row: dict, header: list[str], key, ledger, now, cadence_days):
    """Reasons this row's tier claim is not fully evidenced; empty list == upheld."""
    tier = (row.get(_tier.REQUIRED) or "").strip()
    claims = TIER_CLAIMS.get(tier)
    if claims is None:
        return []          # not a qualifying tier: the vocabulary gate owns it
    reasons = []
    for component in _strict.STRICT_COMPONENTS:
        cadence_class = claims.get(component)
        if cadence_class == EVERY_RUN:
            ok, why = row_component_evidenced(row, header, component)
            if not ok:
                reasons.append(why)
        elif cadence_class == CADENCED:
            ok, why = check_ledger_receipt(key, ledger, now, cadence_days)
            if not ok and why not in reasons:
                reasons.append(why)
    return reasons


def check(root: Path, *, now: _dt.datetime, cadence_days: int, ledger_path: Path) -> Report:
    found = _tier.scorecards(root)
    if not found:
        raise PopulationError(f"no *scorecard*.csv under {root}")
    ledger = _cadence.load_ledger(ledger_path) if ledger_path.exists() else {}
    report = Report(root=str(root), files=[p.name for p in found])
    for path in found:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            for line, row in enumerate(reader, start=2):
                report.rows += 1
                tier = (row.get(_tier.REQUIRED) or "").strip()
                if tier not in QUALIFYING:
                    continue
                report.claims += 1
                key = (row.get("test_id", ""), row.get("test_mode", ""), row.get("backend", ""))
                reasons = check_row(row, header, key, ledger, now, cadence_days)
                if reasons:
                    report.violations.append(Violation(
                        path.name, line, row.get("test_id", "?"),
                        row.get("backend", "?"), tier, reasons))
                else:
                    report.upheld += 1
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--ledger", type=Path, default=None)
    ap.add_argument("--cadence-days", type=int, default=_cadence.CADENCE_DAYS)
    ap.add_argument("--now", default=None, help="ISO instant; defaults to real now")
    a = ap.parse_args(argv)

    now = _cadence._parse(a.now) or _dt.datetime.now(_dt.timezone.utc)
    ledger = a.ledger if a.ledger is not None else _cadence.LEDGER
    try:
        report = check(a.root, now=now, cadence_days=a.cadence_days, ledger_path=ledger)
    except PopulationError as error:
        print(f"tier-evidence: REFUSED: {error}", file=sys.stderr)
        return 2

    print(report.render())
    if report.violations:
        print(f"\nREFUSED: {len(report.violations)} tier claim(s) not supported by "
              f"their own evidence:", file=sys.stderr)
        for violation in report.violations[:20]:
            print(f"  {violation.render()}", file=sys.stderr)
        if len(report.violations) > 20:
            print(f"  ... {len(report.violations) - 20} more", file=sys.stderr)
        print("A tier names the components it compared. A row claiming one must carry "
              "evidence for every one of them, or drop to a lower tier or NO-RESULT.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
