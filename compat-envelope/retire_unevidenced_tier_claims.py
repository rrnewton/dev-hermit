#!/usr/bin/env python3
"""Demote a qualifying tier claim that its own row cannot evidence.

WHY THIS EXISTS. The six `full-stdout-info-stack-heap` rows in `scorecard.csv`
were the entire measured full-tier envelope, and they had NO PRODUCER IN THE
TREE. `ptrace-short-full-tier` appears only in docs, the tier-evidence register
and the CSVs -- never in a `.rs`, `.py` or `.sh`. Nothing writes the tier value
into a row; nothing writes `stack_hash`/`heap_hash` into a scorecard. Their
commit `ddfd448` changed ONLY `scorecard.csv`: 625 insertions, 619 deletions,
zero code. They came from an ad-hoc path nobody committed, so they cannot be
re-derived and no producer fix will re-emit them.

WHY DEMOTE RATHER THAN RE-EARN. Both qualifying tiers name `stack` and `heap`.
The schema carries a single `stack_hash`/`heap_hash` with no reference operand,
so a parity is inexpressible and `tier_evidence.py` reports
`schema-cannot-express`. Settling that is a separate, deliberately reserved
schema decision. Until it is settled, NO producer can emit a qualifying tier
honestly, so a "re-earned" row would be one `validate-envelope.sh` still refuses
-- the same defect with more machinery in front of it.

WHY DEMOTE RATHER THAN DELETE. This is the doctrine `migrate-scorecard-schema.py`
already applies to parity booleans whose operands were discarded: move the
historical observation somewhere a consumer cannot count it, rather than erase
the fact that a run happened. Its words: "historical rows are never guessed to
have met either strict comparison tier."

WHY A TOOL RATHER THAN AN EDIT, which is the whole point. A hand-edited row is
what created this. A hand-edited row that FIXES it would reproduce the defect
while appearing to cure it: the next reader would again find a value in the CSV
with nothing in the tree that can produce it. So the demotion is mechanical,
idempotent, and re-runnable by someone who was not here.

THE CRITERION IS COMPUTED, NOT LISTED. This file contains no list of six test
ids. A row is demoted iff its `comparison_tier` is qualifying AND
`tier_evidence.check_row` cannot evidence it -- the same predicate the wired gate
uses. Hard-coding the six would be another unreproducible artifact.

WHAT IT NEVER DOES: invent evidence, populate an operand, or promote anything.
The only write is `comparison_tier` -> `legacy-unqualified`, plus a note in
`reason` recording which components were unevidenced. Every other field is
byte-preserved.

Exit codes:
  0  nothing to demote (or, with --apply, the demotion succeeded)
  1  --check found rows that need demoting
  2  the population or the tier vocabulary is unavailable
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import fcntl
import importlib.util
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise SystemExit(f"retire-unevidenced: cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_evidence = _load("_tier_evidence", "tier_evidence.py")
_tier = _evidence._tier
_cadence = _evidence._cadence

DEMOTED_TIER = "legacy-unqualified"
#: Appended to `reason` so the demotion carries its own justification in the row.
NOTE = "tier demoted to legacy-unqualified: unevidenced ({components})"


def rows_to_demote(path: Path, *, now, cadence_days, ledger) -> list[tuple[int, dict, list[str]]]:
    """(line, row, reasons) for every qualifying claim this row cannot evidence."""
    out = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        for line, row in enumerate(reader, start=2):
            tier = (row.get(_tier.REQUIRED) or "").strip()
            if tier not in _evidence.QUALIFYING:
                continue
            key = (row.get("test_id", ""), row.get("test_mode", ""), row.get("backend", ""))
            reasons = _evidence.check_row(row, header, key, ledger, now, cadence_days)
            if reasons:
                out.append((line, row, reasons))
    return out


def _components(reasons: list[str]) -> str:
    """`missing:stdout (...)` / `schema-cannot-express:stack (...)` -> "stdout, stack"."""
    seen = []
    for reason in reasons:
        head = reason.split(" ", 1)[0]
        component = head.split(":", 1)[1] if ":" in head else head
        if component not in seen:
            seen.append(component)
    return ", ".join(seen)


def retire(path: Path, *, apply: bool, now, cadence_days: int, ledger) -> int:
    """Returns the number of rows demoted (or that would be). Idempotent: a second
    run finds nothing, because a demoted row no longer carries a qualifying tier."""
    targets = rows_to_demote(path, now=now, cadence_days=cadence_days, ledger=ledger)
    if not targets:
        return 0
    lines = {line for line, _, _ in targets}
    notes = {line: _components(reasons) for line, _, reasons in targets}
    if not apply:
        return len(targets)
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            rewritten = []
            for line, row in enumerate(reader, start=2):
                if line in lines:
                    row[_tier.REQUIRED] = DEMOTED_TIER
                    note = NOTE.format(components=notes[line])
                    existing = (row.get("reason") or "").strip()
                    row["reason"] = f"{existing}; {note}" if existing else note
                rewritten.append(row)
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rewritten)
            handle.seek(0)
            handle.write(buffer.getvalue())
            handle.truncate()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return len(targets)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite in place; without it this is a --check that only reports")
    ap.add_argument("--ledger", type=Path, default=None)
    ap.add_argument("--cadence-days", type=int, default=_cadence.CADENCE_DAYS)
    ap.add_argument("--now", default=None)
    a = ap.parse_args(argv)

    found = _tier.scorecards(a.root)
    if not found:
        print(f"retire-unevidenced: REFUSED: no *scorecard*.csv under {a.root}",
              file=sys.stderr)
        return 2
    now = _cadence._parse(a.now) or _dt.datetime.now(_dt.timezone.utc)
    ledger_path = a.ledger if a.ledger is not None else _cadence.LEDGER
    ledger = _cadence.load_ledger(ledger_path) if ledger_path.exists() else {}

    total = 0
    for path in found:
        count = retire(path, apply=a.apply, now=now, cadence_days=a.cadence_days,
                       ledger=ledger)
        total += count
        if count:
            verb = "demoted" if a.apply else "would demote"
            print(f"  {path.name}: {verb} {count} unevidenced qualifying claim(s)")
    print(f"retire-unevidenced: {total} unevidenced qualifying claim(s) across "
          f"{len(found)} scorecard(s)"
          + ("" if a.apply else " (--check; pass --apply to rewrite)"))
    if total and not a.apply:
        print("A qualifying tier its own row cannot evidence must be demoted, not "
              "left standing. Run with --apply.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
