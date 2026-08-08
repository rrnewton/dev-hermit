#!/usr/bin/env python3
"""Incrementally import machine-local validate-run rows into the tracked shard.

WHY THIS EXISTS. `migrate_legacy` was a library function with no entry point and
no caller outside its own tests, so the durable shard had exactly ONE commit ever
-- `ledger: append 654 event(s)`, 2026-08-06 -- and then drifted ~28h behind the
live writer. Nothing scheduled the import because there was nothing to schedule.
The consequence is not cosmetic: the sharpest piece of certification-condition-4
evidence on the box (commit f65f7446 at `concurrent_validates=18`, `result=pass`)
existed ONLY in a gitignored file, so no consumer of the durable record could see
it and a fresh checkout would not have it at all.

THE RULE THIS IMPORTER IS BUILT AROUND: **NEVER COERCE AN UNKNOWN INTO A VALUE.**
A field that was never recorded must stay distinguishable from one measured as
zero. The live corpus contains all three states at once, which is exactly why the
distinction is not hypothetical:

    key absent     483 rows   never measured
    explicit null   13 rows   recorded as unknown
    explicit 0       1 row    measured, and genuinely zero

Both collapses are lossy and this importer commits neither. It does not normalise,
default, or fill: `migrate_legacy` carries the source row VERBATIM under
`legacy_row`, and `replay_legacy` reconstructs it exactly. Verbatim carry is what
makes absent-preservation structural rather than a rule someone has to remember,
and `--check-only` re-derives it from the shard so the claim is checked rather
than trusted.

TWO HAZARDS THIS HANDLES, both of which would be silent:

1.  ID COLLISION. `run_id` and `legacy_index` are POSITIONAL and restart at zero.
    `union_events` deduplicates BY `event_id`, so a second import restarting at
    zero would not collide loudly -- it would silently REPLACE already-published
    events. Numbering continues from the shard's high-water mark instead.
2.  NON-APPEND. Committed bytes are never edited. The new text is checked with
    `verify_append_only` against the old before anything is written, and a
    non-extension refuses rather than being forced.

Identity for "already imported" is the exact canonical bytes of the legacy row,
counted as a MULTISET. Not a natural key: rows legitimately repeat within one run
(that is what the `run.enrich` chain is), so a key-based check would silently drop
the second and later members of a group.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger import (  # noqa: E402
    migrate_legacy,
    read_shard,
    validate_event,
    verify_append_only,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIVE = REPO / "ignored/validate-run-ledger.jsonl"
DEFAULT_TEAM = "hermit"
FIELD = "concurrent_validates"
_RUN_ID_RE = re.compile(r"^legacy-(\d+)$")

#: The tracked shard stores workspace-relative paths behind this token, because
#: `validate_event` refuses an owner home path anywhere in an event at any depth
#: (violation `owner-path`) and `check-portability` greps tracked files for it.
#: The live ledger records absolute paths, so every row must be rewritten before
#: it can be published. NOTHING IN TRACKED CODE DID THIS: the 2026-08-06 import
#: applied the substitution by hand and never captured it, which is the concrete
#: reason the import was not repeatable. Owning it here is what makes it so.
WORKSPACE_TOKEN = "{{WORKSPACE_ROOT}}"


def redact(value, root: str):
    """Replace the workspace root with a placeholder, at any nesting depth.

    Recurses exactly like `_contains_owner_path`, so anything the linter can find
    is something this can rewrite. It SUBSTITUTES rather than deletes: the path
    stays readable and workspace-relative instead of becoming another unknown.
    A `/home/` path outside the workspace is deliberately left alone -- the
    linter then refuses it, which is the correct fail-closed outcome for a path
    this function does not understand.
    """
    if isinstance(value, str):
        return value.replace(root, WORKSPACE_TOKEN)
    if isinstance(value, dict):
        return {k: redact(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, root) for v in value]
    return value


def owner_path_fields(value, path: str = "") -> list[tuple[str, str]]:
    """Every (field-path, string) still carrying an owner home path.

    Mirrors `_contains_owner_path`'s recursion but REPORTS instead of returning a
    boolean, because "which field" is the only form of this answer anyone can act
    on. Used to quarantine a row before it is turned into an event, so one
    un-publishable row cannot block the rest of the import.
    """
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        if "/" + "home" + "/" in value:
            out.append((path or ".", value))
    elif isinstance(value, dict):
        for key, item in value.items():
            out.extend(owner_path_fields(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.extend(owner_path_fields(item, f"{path}[{index}]"))
    return out


def read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping blank lines. A malformed line is fatal: a
    silently dropped row is exactly the class of loss this task exists to fix."""
    rows: list[dict] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: malformed JSON: {exc}")
    return rows


def canonical(row: dict) -> str:
    """Byte-exact identity for a legacy row, stable across dict ordering."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def imported_rows(events: Iterable[dict]) -> list[dict]:
    """Every legacy row already carried by the shard, in shard order."""
    return [e["legacy_row"] for e in events if isinstance(e.get("legacy_row"), dict)]


def high_water(events: Iterable[dict]) -> tuple[int, int]:
    """Next free (run index, legacy index) for this shard."""
    run_max = -1
    legacy_max = -1
    for event in events:
        match = _RUN_ID_RE.match(str(event.get("run_id", "")))
        if match:
            run_max = max(run_max, int(match.group(1)))
        index = event.get("legacy_index")
        if isinstance(index, int):
            legacy_max = max(legacy_max, index)
    return run_max + 1, legacy_max + 1


def pending(live: list[dict], already: list[dict]) -> list[dict]:
    """Live rows not yet in the shard, preserving multiplicity and live order."""
    counts: dict[str, int] = {}
    for row in already:
        key = canonical(row)
        counts[key] = counts.get(key, 0) + 1
    out: list[dict] = []
    for row in live:
        key = canonical(row)
        if counts.get(key):
            counts[key] -= 1
        else:
            out.append(row)
    return out


def field_census(rows: Iterable[dict], field: str = FIELD) -> dict[str, int]:
    """Count the three states separately. A census that reported only
    present/absent would hide the very distinction this importer protects."""
    census = {"present": 0, "absent": 0, "null": 0, "zero": 0, "ge2": 0}
    for row in rows:
        if field not in row:
            census["absent"] += 1
        elif row[field] is None:
            census["null"] += 1
        else:
            census["present"] += 1
            if row[field] == 0:
                census["zero"] += 1
            elif isinstance(row[field], int) and row[field] >= 2:
                census["ge2"] += 1
    return census


def render(census: dict[str, int]) -> str:
    return (" ".join(f"{k}={v}" for k, v in census.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--shard", type=Path, required=True,
                    help="git-tracked shard, e.g. ledger/<team>/<short-host>/2026-08.jsonl")
    ap.add_argument("--live", type=Path, default=DEFAULT_LIVE,
                    help="machine-local validate-run ledger (gitignored)")
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--workspace-root", default=str(REPO),
                    help="absolute path rewritten to {{WORKSPACE_ROOT}} before publishing")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be imported; write nothing")
    ap.add_argument("--check-only", action="store_true",
                    help="exit 1 if the shard is behind the live ledger; write nothing")
    args = ap.parse_args(argv)

    if not args.live.exists():
        print(f"import-validate-runs: live ledger absent: {args.live}", file=sys.stderr)
        return 2
    # Redact BEFORE anything else: the shard stores redacted rows, so identity,
    # census and publication must all be computed on the same representation.
    # Comparing raw live rows against redacted stored rows made every one of 655
    # already-imported runs look new.
    live = [redact(row, args.workspace_root) for row in read_jsonl(args.live)]
    events = read_shard(args.shard) if args.shard.exists() else []
    already = imported_rows(events)

    todo = pending(live, already)
    before = field_census(already)
    incoming = field_census(todo)

    print(f"shard   {args.shard}")
    print(f"  events={len(events)} legacy_rows={len(already)}  {FIELD}: {render(before)}")
    print(f"live    {args.live}")
    print(f"  rows={len(live)}")
    print(f"pending {len(todo)} row(s) not yet in the shard  {FIELD}: {render(incoming)}")

    if not todo:
        print("shard is current: every live row is already imported.")
        return 0
    if args.check_only:
        print(f"CHECK FAILED: shard is behind by {len(todo)} row(s).", file=sys.stderr)
        return 1

    # QUARANTINE, never silently drop. A row whose owner path is NOT under the
    # workspace (a ~/.cargo checkout inside a compiler error line, say) cannot be
    # redacted by the one token this shard defines, and inventing a second token
    # would be a schema decision this importer has no standing to make. So it is
    # held back, named, and counted -- and because the importer is incremental it
    # stays pending and is re-reported on every future run rather than being lost.
    quarantined: list[tuple[dict, list[tuple[str, str]]]] = []
    clean: list[dict] = []
    for row in todo:
        offenders = owner_path_fields(row)
        (quarantined if offenders else clean).append((row, offenders) if offenders else row)
    if quarantined:
        print(f"QUARANTINED {len(quarantined)} row(s): an owner path outside "
              f"{args.workspace_root} that no defined token covers.")
        for row, offenders in quarantined:
            field, text = offenders[0]
            print(f"  commit={str(row.get('commit'))[:12]} "
                  f"finished={row.get('finished_at')} field={field}")
            print(f"    {text[:120]}")
        print("  These are NOT imported and NOT dropped: they remain pending and will "
              "be reported again on every run until a token is defined for them.")
    todo = clean
    if not todo:
        print("nothing importable after quarantine.")
        return 6

    start_run, start_legacy = high_water(events)
    print(f"continuing numbering at run index {start_run}, legacy_index {start_legacy}")
    new_events = migrate_legacy(
        todo, team=args.team,
        start_run_index=start_run, start_legacy_index=start_legacy,
    )

    # Refuse to mint an id the shard already carries. union_events dedups by
    # event_id, so a collision would REPLACE published history rather than error.
    known = {str(e.get("event_id")) for e in events}
    clashes = sorted(known & {str(e.get("event_id")) for e in new_events})
    if clashes:
        print(f"REFUSED: {len(clashes)} event_id(s) already in the shard, "
              f"first={clashes[0]}", file=sys.stderr)
        return 3

    problems: list[str] = []
    seen = set(known)
    for event in new_events:
        codes = validate_event(event, args.shard, known_ids=seen)
        if codes:
            problems.append(f"{event.get('event_id')}: {','.join(codes)}")
        seen.add(str(event.get("event_id")))
    if problems:
        print(f"REFUSED: {len(problems)} event(s) fail the linter; first: {problems[0]}",
              file=sys.stderr)
        if any("owner-path" in p for p in problems):
            print("  owner-path means a /home/ path survived redaction. Re-run with the "
                  "correct --workspace-root, or the row references a path outside the "
                  "workspace that this importer must not guess at.", file=sys.stderr)
        return 4

    old_text = args.shard.read_text() if args.shard.exists() else ""
    addition = "".join(
        json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in new_events
    )
    if old_text and not old_text.endswith("\n"):
        old_text += "\n"
    new_text = old_text + addition

    violations = verify_append_only(old_text, new_text)
    if violations:
        print(f"REFUSED: not a pure append: {','.join(violations)}", file=sys.stderr)
        return 5

    if args.dry_run:
        print(f"DRY RUN: would append {len(new_events)} event(s); nothing written.")
        return 0

    tmp = args.shard.with_suffix(args.shard.suffix + f".import.{os.getpid()}")
    tmp.write_text(new_text)
    tmp.replace(args.shard)

    after = field_census(imported_rows(read_shard(args.shard)))
    print(f"appended {len(new_events)} event(s).")
    print(f"  {FIELD} before: {render(before)}")
    print(f"  {FIELD} after:  {render(after)}")
    if quarantined:
        print(f"PARTIAL: {len(quarantined)} row(s) quarantined and still pending.")
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
