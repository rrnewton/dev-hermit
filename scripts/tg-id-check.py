#!/usr/bin/env python3
"""Check that a cited TaskGraph id can actually resolve.

WHY THIS EXISTS.  Hermit PR #1701 was closed without landing on the promise that
"TaskGraph audit-port-epoll-fixture-duplication preserves" the work.  That id can
never resolve, so the work was simply lost until someone went looking.  It was the
second occurrence; the first was repaired on PR #1698.  A citation that points at
an unreachable id is indistinguishable from one that points at nothing -- the
reader sees a plausible slug either way.

THE RULE, derived from the live graph rather than from documentation (4537 tasks;
of the 292 auto-derived ids with spaced titles, 262 match exactly and the other 30
are collision suffixes):

    slugify(title)  ->  lowercase, every run of non-alphanumerics becomes "_"
    truncate        ->  keep only the FIRST FOUR underscore-separated tokens
    disambiguate    ->  append _2, _3, ... if that slug is already taken

The truncation is to four tokens OF THE SLUG, not four whitespace words -- a title
word that already contains underscores spends several tokens at once.  Getting
this wrong produces a confidently wrong suggestion, which is the same defect one
level up, so the transform is asserted against the live graph by --self-test.

Usage:
    scripts/tg-id-check.py <cited-id> [<cited-id> ...]
    scripts/tg-id-check.py --self-test

Exit codes:
    0  every cited id resolves
    1  an id is unreachable BUT the truncated form exists (the #1701 shape)
    2  an id is unreachable and no candidate exists
    3  usage / could not query the graph
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# One resolver for the TaskGraph, shared with every ci-hub consumer.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci-hub" / "lib"))
import taskgraph_db  # noqa: E402


def _tg_env():
    """Bind `tg` explicitly, or exit 3 -- never query an unbound default."""
    try:
        return taskgraph_db.child_env(taskgraph_db.resolve())
    except taskgraph_db.TaskGraphUnavailable as error:
        print(f"tg-id-check: {error}", file=sys.stderr)
        sys.exit(3)


def slug4(title: str) -> str:
    """The id `tg` derives from a title, before collision suffixing."""
    s = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return "_".join(s.split("_")[:4])


def known_ids() -> set[str]:
    proc = subprocess.run(
        ["tg", "sql", "SELECT local_id FROM tasks"],
        capture_output=True,
        text=True,
        env=_tg_env(),
    )
    if proc.returncode != 0:
        print(f"tg-id-check: cannot query the graph: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(3)
    out = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("local_id") or line.startswith("-") or line.endswith("rows)"):
            continue
        out.add(line)
    return out


def check(cited: str, ids: set[str]) -> int:
    if cited in ids:
        print(f"OK          {cited}")
        return 0
    candidate = slug4(cited)
    if candidate != cited and candidate in ids:
        print(
            f"UNREACHABLE {cited}\n"
            f"            this id can never resolve: tg truncates a title-derived id to the\n"
            f"            first FOUR underscore tokens.\n"
            f"            did you mean: {candidate}   (exists)"
        )
        return 1
    near = sorted(i for i in ids if i.startswith(candidate)) if candidate else []
    print(
        f"MISSING     {cited}\n"
        f"            no such task; truncated form '{candidate}' does not exist either.\n"
        f"            {len(near)} id(s) share that prefix"
        + (f": {', '.join(near[:5])}" if near else "")
    )
    return 2


def self_test() -> int:
    """Bracket the transform BOTH ways against the live graph."""
    proc = subprocess.run(
        ["tg", "sql", "SELECT local_id || '|' || title FROM tasks"],
        capture_output=True,
        text=True,
        env=_tg_env(),
    )
    if proc.returncode != 0:
        print("self-test: cannot query the graph", file=sys.stderr)
        return 3
    rows = []
    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        i, t = line.split("|", 1)
        i, t = i.strip(), t.strip()
        # Drop the query's own header/rule rows -- `local_id | title` otherwise
        # parses as a task and shows up as an unexplained mismatch.
        if not i or not t or i.startswith("-") or i == "local_id":
            continue
        rows.append((i, t))
    auto = [(i, t) for i, t in rows if "_" in i and " " in t]
    exact = [(i, t) for i, t in auto if i == slug4(t)]
    suffixed = [
        (i, t) for i, t in auto if i != slug4(t) and re.fullmatch(re.escape(slug4(t)) + r"_\d+", i)
    ]
    residue = [
        (i, t)
        for i, t in auto
        if i != slug4(t) and not re.fullmatch(re.escape(slug4(t)) + r"_\d+", i)
    ]
    truncated = [
        (i, t) for i, t in exact if slug4(t) != re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")
    ]
    explained = len(exact) + len(suffixed)
    print(f"corpus: {len(rows)} tasks, {len(auto)} auto-derived (underscore id + spaced title)")
    print(f"  exact match to slug4      : {len(exact)}")
    print(f"  slug4 + collision suffix  : {len(suffixed)}")
    print(f"  residue (title edited)    : {len(residue)}")
    print(f"  TRUNCATED (full-title slug is unreachable): {len(truncated)}")

    # THE BAR, and why it is where it is rather than tuned to pass.  The residue is
    # not noise: every case is a task whose TITLE WAS REWRITTEN AFTER CREATION --
    # "LANDED a3a0f9c: ...", "ANSWERED: NO ...", "DUPLICATE of ...", "P0 `ci/...`".
    # The id freezes at creation; the title moves.  Nothing in this table dates a
    # title, so that class is unfalsifiable from this data and cannot be asserted
    # away.  The bar is therefore set below the measured drift rate, and the residue
    # is PRINTED rather than tolerated silently.
    #
    # It also sharpens the warning this tool exists for: deriving an id from a
    # task's CURRENT title is wrong for the residue too, not only for long titles.
    ok = bool(auto) and explained / len(auto) >= 0.90 and len(truncated) > 0
    print("SELF-TEST:", "PASS" if ok else "FAIL",
          f"({explained}/{len(auto)} = {100 * explained // len(auto)}% explained,"
          f" {len(truncated)} truncated, {len(residue)} title-edited)")
    if residue:
        print("  residue sample (id frozen at creation, title since rewritten):")
        for i, t in residue[:3]:
            print(f"    {i}  <-  now titled: {t[:56]}")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 3
    if args[0] == "--self-test":
        return self_test()
    ids = known_ids()
    return max(check(a, ids) for a in args)


if __name__ == "__main__":
    sys.exit(main())
