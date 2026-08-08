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

OBSERVATION AND RESCUE ARE SEPARATE, AND THE ORDER IS LOAD-BEARING (2026-08-08).
Measured on this repo: the scan takes **0.40s**; a single ``herdr-run`` rescue
round-trip took **65.8s and 74.8s** (both failing, rc=69), and rescue makes TWO
per commit. Under the tick's 30s budget the gate had therefore NEVER emitted a
measurement -- not because it could not measure, but because ``main`` computed
the correct answer in 0.4s, then handed control to rescue, and printed only
afterwards. The timeout killed a process that was already holding a complete
result. **So the report is emitted BEFORE rescue is attempted.** A rescue that
hangs, fails, or is killed can no longer discard a measurement that succeeded.

Corollaries, each measured rather than assumed:

*   Cost does NOT scale with worktree count. 47 worktrees: ``rev-list --all``
    0.13s versus ``--single-worktree`` 0.12s. Narrowing scope would buy 10ms and
    lose the coverage that is the whole point -- head-scoped read 0 while
    all-refs read 1.
*   The rescue transport can be broken while egress is fine: the same ls-remote
    took 0.32s through ``with-proxy`` directly and timed out twice through
    ``herdr-run``. Rescue therefore bounds itself and reports what it skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PARENT = str(Path(__file__).resolve().parents[2])
HERDR = f"{PARENT}/agent-utils/bin/herdr-run"

# Per-call ceilings for the two remote legs. The old 600s/300s could not be
# reached by any caller with a budget, and one stuck call ate more than the
# whole tick. Measured: a healthy round-trip is sub-second (0.32s direct); a
# broken one runs ~70s. 45s is far above healthy and well below two-stuck-calls.
PUSH_TIMEOUT_SECS = 45
VERIFY_TIMEOUT_SECS = 45
# Total wall ceiling for the whole rescue phase. Rescue is remote mutation, not
# observation, so it gets its OWN budget and must never borrow the scan's.
RESCUE_DEADLINE_SECS = 120


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


def rescue(
    rows: list[dict[str, str]],
    agent: str,
    dry: bool,
    deadline_secs: float = RESCUE_DEADLINE_SECS,
) -> list[dict[str, str]]:
    """Push each local-only commit to its own rescue ref, then VERIFY at the
    remote. A push exit code is not evidence; the ls-remote re-read is.

    Bounded, and every commit gets an explicit disposition. A commit the budget
    never reached is `skipped-deadline` -- NOT absent, and NOT `verified`. The
    caller can then say what it did and did not publish instead of implying
    coverage it never attempted.
    """
    done: list[dict[str, str]] = []
    started = time.monotonic()
    for index, r in enumerate(rows):
        ref = f"rescue/auto-{r['short']}"
        if dry:
            done.append({**r, "ref": ref, "published": "dry-run"})
            continue
        if time.monotonic() - started >= deadline_secs:
            # Do not start work that cannot finish, and do not pretend the
            # remaining rows were examined.
            done.extend(
                {**row, "ref": f"rescue/auto-{row['short']}",
                 "published": "skipped-deadline"}
                for row in rows[index:]
            )
            break
        try:
            subprocess.run(
                [HERDR, "--agent", agent,
                 f"with-proxy git -C {PARENT} push origin {r['sha']}:refs/heads/{ref}"],
                capture_output=True, text=True, timeout=PUSH_TIMEOUT_SECS)
            seen = subprocess.run(
                [HERDR, "--agent", agent,
                 f"with-proxy git -C {PARENT} ls-remote --heads origin refs/heads/{ref}"],
                capture_output=True, text=True, timeout=VERIFY_TIMEOUT_SECS).stdout
        except subprocess.TimeoutExpired:
            # A stuck transport is a FAILED publish, never a silent success.
            done.append({**r, "ref": ref, "published": "FAILED-timeout"})
            continue
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
    ap.add_argument("--rescue-deadline", type=float, default=RESCUE_DEADLINE_SECS,
                    help="wall ceiling for the whole rescue phase; commits the "
                         "budget never reaches are reported skipped-deadline, "
                         "never verified")
    ap.add_argument("--agent", default="hermit-det2")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    scan_started = time.monotonic()
    rows = local_only(a.scope)
    head_n = len(local_only("head")) if a.scope == "all" else len(rows)
    scan_secs = time.monotonic() - scan_started

    def emit(published: list[dict[str, str]]) -> None:
        if a.json:
            print(json.dumps({"scope": a.scope, "count": len(rows),
                              "head_scoped_count": head_n,
                              "scan_seconds": round(scan_secs, 3),
                              "rescued": bool(published),
                              "commits": published or rows}, indent=2), flush=True)
            return
        # COUNT AND SUBJECTS, never a bare boolean.
        print(f"unpushed-parent-commits scope={a.scope} count={len(rows)} "
              f"head_scoped_count={head_n} scan_seconds={scan_secs:.2f}", flush=True)
        for r in (published or rows):
            extra = f"  -> {r['ref']} [{r['published']}]" if "ref" in r else ""
            print(f"  {r['short']}  {r['date']}  {r['author']:<14.14}  "
                  f"{r['subject'][:72]}{extra}", flush=True)
        if not rows:
            print("  (none: every local commit is reachable from a remote ref)",
                  flush=True)
        if head_n != len(rows) and a.scope == "all":
            print(f"  NOTE: a HEAD-scoped probe would report {head_n}, missing "
                  f"{len(rows) - head_n}. Scope to --all.", flush=True)
        # tick-hub resolves `{summary}` from `key=value` stdout lines
        # (parse_kv_lines). Without this the emitted warning renders the LITERAL
        # string `{summary}` -- the same unactionable alarm the worktree-liveness
        # gate shipped. It stayed invisible here only because the gate never
        # completed, so it never emitted anything at all. Name the subjects: an
        # alarm that omits its own subject cannot be verified.
        subjects = ", ".join(f"{r['short']} {r['subject'][:40]}" for r in rows[:3])
        residue = f" (+{len(rows) - 3} more)" if len(rows) > 3 else ""
        print(
            f"summary={len(rows)} parent commit(s) exist only locally: "
            f"{subjects}{residue}" if rows else "summary=no local-only commits",
            flush=True,
        )

    # EMIT THE MEASUREMENT FIRST. Rescue is slower than the scan by two orders
    # of magnitude and can hang on a broken transport; printing afterwards is
    # how a completed 0.4s measurement got discarded by a 30s timeout on every
    # tick for a full day. A rescue failure must cost the rescue, not the
    # observation.
    emit([])
    if not a.rescue:
        return 1 if rows else 0

    published = rescue(rows, a.agent, a.dry_run, a.rescue_deadline)
    verified = sum(1 for r in published if r.get("published") == "verified")
    skipped = sum(1 for r in published if r.get("published") == "skipped-deadline")
    failed = len(published) - verified - skipped
    if not a.json:
        for r in published:
            print(f"  rescue {r['short']} -> {r['ref']} [{r['published']}]", flush=True)
    print(f"unpushed-parent-commits rescue attempted={len(published) - skipped} "
          f"verified={verified} failed={failed} skipped_deadline={skipped}",
          flush=True)
    # Partial coverage is NOT success. Anything unpublished keeps the nonzero
    # exit, so a deadline or a broken transport can never read as "all clear".
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
