#!/usr/bin/env python3
"""Standing check: parent commits that exist ONLY locally.

An unpushed commit emits no error, no failing check and no wakeup. On
2026-08-06 a local-main rewrite orphaned 45 of them; they were recovered only
because someone happened to run an fsck sweep. Three were noticed by their own
authors. The other 42 had nobody looking.

TWO DESIGN RULES, both learned that day:

1.  NEVER REPORT A BOOLEAN. ``scm: dirty`` was useless because it cannot
    distinguish 1 from 45. This prints the COUNT and the SUBJECTS.

2.  SCOPE TO ALL REFS, NOT HEAD. The obvious probe is
    ``rev-list --count HEAD --not --remotes``, but HEAD-scoped counting only
    sees the branch that happens to be checked out. Measured on this repo at
    the time of writing: HEAD-scoped said **1**, all-refs said **25**. Both are
    reported, and the all-refs number is the exposure.

Neither number covers commits that are unreachable from ANY ref -- that is what
the 45 were, and only ``git fsck --lost-found`` finds those. This check exists
to stop commits BECOMING unreachable, by publishing them while a ref still
points at them.

DETECT AND PUBLISH ONLY. This never merges, resets, rewrites, gcs, prunes,
expires a reflog, repacks or cleans. Its only mutation is creating new remote
refs under ``rescue/``, which cannot destroy anything.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PARENT = str(Path(__file__).resolve().parents[2])
HERDR = f"{PARENT}/agent-utils/bin/herdr-run"


def git(*args: str, cwd: str = PARENT) -> str:
    out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return out.stdout.strip()


def local_only(scope: str) -> list[dict[str, str]]:
    """Commits reachable from `scope` but from no remote-tracking ref."""
    spec = ["--all"] if scope == "all" else ["HEAD"]
    shas = git("rev-list", *spec, "--not", "--remotes").splitlines()
    rows = []
    for sha in [s for s in shas if s]:
        line = git("log", "-1", "--format=%h\t%an\t%ad\t%s", "--date=short", sha)
        h, an, ad, subj = (line.split("\t", 3) + ["", "", "", ""])[:4]
        rows.append({"sha": sha, "short": h, "author": an, "date": ad, "subject": subj})
    return rows


def rescue(rows: list[dict[str, str]], agent: str, dry: bool) -> list[dict[str, str]]:
    """Push each local-only commit to its own rescue ref, then VERIFY at the
    remote. A push exit code is not evidence; the ls-remote re-read is."""
    done = []
    for r in rows:
        ref = f"rescue/auto-{r['short']}"
        if dry:
            done.append({**r, "ref": ref, "published": "dry-run"})
            continue
        subprocess.run(
            [HERDR, "--agent", agent,
             f"with-proxy git -C {PARENT} push origin {r['sha']}:refs/heads/{ref}"],
            capture_output=True, text=True, timeout=600)
        seen = subprocess.run(
            [HERDR, "--agent", agent,
             f"with-proxy git -C {PARENT} ls-remote --heads origin refs/heads/{ref}"],
            capture_output=True, text=True, timeout=300).stdout
        ok = r["sha"] in seen
        done.append({**r, "ref": ref, "published": "verified" if ok else "FAILED"})
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=["all", "head"], default="all",
                    help="all = every local ref (the real exposure); head = the "
                         "checked-out branch only (under-reports)")
    ap.add_argument("--rescue", action="store_true",
                    help="publish each local-only commit to rescue/auto-<sha> and "
                         "verify at the remote. A report needs a reader; this does not")
    ap.add_argument("--dry-run", action="store_true", help="with --rescue, do not push")
    ap.add_argument("--agent", default="hermit-det2")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = local_only(a.scope)
    head_n = len(local_only("head")) if a.scope == "all" else len(rows)
    published = rescue(rows, a.agent, a.dry_run) if a.rescue else []

    if a.json:
        print(json.dumps({"scope": a.scope, "count": len(rows),
                          "head_scoped_count": head_n,
                          "commits": published or rows}, indent=2))
    else:
        # COUNT AND SUBJECTS, never a bare boolean.
        print(f"unpushed-parent-commits scope={a.scope} count={len(rows)} "
              f"head_scoped_count={head_n}")
        for r in (published or rows):
            extra = f"  -> {r['ref']} [{r['published']}]" if "ref" in r else ""
            print(f"  {r['short']}  {r['date']}  {r['author']:<14.14}  "
                  f"{r['subject'][:72]}{extra}")
        if not rows:
            print("  (none: every local commit is reachable from a remote ref)")
        if head_n != len(rows) and a.scope == "all":
            print(f"  NOTE: a HEAD-scoped probe would report {head_n}, missing "
                  f"{len(rows) - head_n}. Scope to --all.")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
