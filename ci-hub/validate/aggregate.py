#!/usr/bin/env python3
"""Machine-wide validate-run visibility.

Sweeps and aggregates EVERY `hermit validate` run on this machine into one view,
regardless of which worktree/agent/slot produced it. It unifies three data
sources that today live in disconnected places:

  1. Structured JSONL ledgers written by hermit/validate.sh
     (append_validation_ledger). Default path is
     `$DEV_HERMIT_PARENT/ignored/validate-run-ledger.jsonl`, but a run whose
     DEV_HERMIT_PARENT is unset (most worktree / standalone runs) skips the
     append entirely, so the parent ledger only ever sees a fraction of runs.
  2. Raw per-run logs `$TMPDIR/hermit-validate.XXXXXX.log` (always written by
     validate.sh via mktemp, for EVERY run). These are the ground truth: this
     tool reconstructs a ledger-equivalent record for any log not covered by a
     JSONL ledger, recovering the otherwise-invisible worktree/standalone runs.
  3. safe-ci-dag-runner per-node profiling CSVs under
     `<checkout>/.safe-ci-dag-runner/profiles/` (aggregate + per-step). These
     are retained and appended across runs; this tool indexes them and links
     each run to its profiling by (checkout, git_sha, timestamp).

This is the "validate-run-ledger, extended to machine-wide" deliverable and
lives in the dev-hermit PARENT per the tracking-goes-outer principle.

Read-only by default. `--write-global` persists the unified view to
`<parent>/ignored/validate-run-global.jsonl` (a gitignored durable artifact).

Usage:
  ci-hub/validate/aggregate.py                 # table, newest last
  ci-hub/validate/aggregate.py --json          # unified records as JSON
  ci-hub/validate/aggregate.py --csv out.csv   # flat CSV
  ci-hub/validate/aggregate.py --write-global  # persist unified JSONL
  ci-hub/validate/aggregate.py --since 2026-08-03  # filter by date
  ci-hub/validate/aggregate.py --profiling     # profiling coverage view
"""
from __future__ import annotations

import argparse
import csv as csvmod
import datetime as dt
import glob
import json
import os
import re
import sys

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
DUR_RE = re.compile(r"(\d+)")


def parent_root() -> str:
    """Locate the dev-hermit parent (this script lives in ci-hub/validate)."""
    env = os.environ.get("DEV_HERMIT_PARENT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def tmpdirs() -> list[str]:
    seen, out = set(), []
    for d in (os.environ.get("TMPDIR", "").rstrip("/"), "/tmp"):
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


PRUNE = {"target", ".git", "node_modules", ".cargo", "incremental", "deps",
         "build", ".venv", "__pycache__"}


def walk_pruned(root: str, want, max_depth: int = 6):
    """Yield files matching predicate `want(name)`, skipping heavy build dirs.

    Recursive `glob("**")` traverses every worktree's target/ tree (millions of
    files) and never returns; this prunes those and bounds depth instead.
    """
    root = os.path.abspath(root)
    base_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip("/").count("/") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        # Prune heavy dirs in place (but keep .safe-ci-dag-runner).
        dirnames[:] = [d for d in dirnames
                       if d not in PRUNE and not d.startswith(".safe-ci-dag-runner.")]
        for fn in filenames:
            if want(fn):
                yield os.path.join(dirpath, fn)


def slot_from_cwd(cwd: str | None) -> str:
    if not cwd:
        return "unknown"
    m = re.search(r"/worktrees/([^/]+)/", cwd + "/")
    if m:
        return m.group(1)
    if cwd.startswith("/tmp") or "/tmp/" in cwd:
        return "standalone"
    if cwd.rstrip("/").endswith("/dev-hermit/hermit"):
        return "primary"
    return os.path.basename(cwd.rstrip("/")) or "unknown"


# ---------------------------------------------------------------------------
# Source 1 + 2: ledgers and raw logs -> unified run records
# ---------------------------------------------------------------------------

def discover_ledgers(parent: str) -> list[str]:
    paths = set()
    paths.add(os.path.join(parent, "ignored", "validate-run-ledger.jsonl"))
    # Per-worktree / nested ledgers.
    for pat in (
        os.path.join(parent, "worktrees", "*", "ignored", "validate-run-ledger.jsonl"),
        os.path.join(parent, "worktrees", "*", "*", "ignored", "validate-run-ledger.jsonl"),
        os.path.join(parent, "*", "ignored", "validate-run-ledger.jsonl"),
    ):
        paths.update(glob.glob(pat))
    # Ad-hoc named ledgers (HERMIT_VALIDATE_LEDGER overrides) usually land in tmp.
    for d in tmpdirs():
        paths.update(glob.glob(os.path.join(d, "hermit-validate-ledger*.jsonl")))
        paths.update(glob.glob(os.path.join(d, "*validate*ledger*.jsonl")))
    return sorted(p for p in paths if os.path.isfile(p))


def load_ledger_records(parent: str):
    records = {}  # keyed by canonical log_file (fallback: synthetic key)
    for lf in discover_ledgers(parent):
        try:
            with open(lf, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec["_source"] = "ledger"
                    rec["_ledger_file"] = lf
                    key = rec.get("log_file") or f"{lf}:{rec.get('started_at')}"
                    records[key] = rec
        except OSError:
            continue
    return records


# Faithful per-gate classes. Each is anchored ONLY on markers hermit/validate.sh
# writes itself, never on workload text (a test named "panic" or a program that
# prints "timeout" must not sway the class). See hermit/validate.sh:
#   * real gate  -> `=== name ===`, `Command: ...`, [output], then ALWAYS
#     `Exit: <status>` + `Duration: <n>s`
#     (run_check_with_timeout / wait_for_background_checks / *_compatibility_probe).
#   * killed by a bound -> a real gate whose body carries `Gate timed out after`
#     (run_timed_command) and/or Exit 124 (a bare `timeout` in a compat probe).
#     This is the RESOURCE story: timeouts / cgroup caps / contention.
#   * deferred    -> `=== name ===` + `Skipped: ...`, no Exit (not run).
#   * section banner -> `=== name ===` alone, no `Command:`/`Exit:` (the
#     Record/replay, SaBRe, e9patch, and Strict-envelope headers).
# A real gate with a `Command:` but no `Exit:` means the log was cut off mid-gate
# (the run did not finish) -> `incomplete`, which is NOT a product failure.
GATE_KINDS = ("pass", "fail", "timeout", "incomplete", "skipped", "banner")
# Verdict-bearing gates: the ones that count toward the pass/total denominator.
VERDICT_KINDS = ("pass", "fail", "timeout", "incomplete")
# Legacy per-gate `result` (pass/fail/skip/unknown/banner) kept for any external
# reader; the killed-by-a-bound vs product-fail split is carried by `kind`.
_KIND_TO_RESULT = {"pass": "pass", "fail": "fail", "timeout": "fail",
                   "skipped": "skip", "incomplete": "unknown", "banner": "banner"}


def _classify_gate(g: dict) -> str:
    if g.get("_has_exit"):
        if g.get("exit_code") == 0:
            return "pass"
        if g.get("_timed_out") or g.get("exit_code") == 124:
            return "timeout"
        return "fail"
    if g.get("_skipped"):
        return "skipped"
    if g.get("_has_command"):
        return "incomplete"
    return "banner"


def gate_kind(g: dict) -> str:
    """Class of a gate, tolerant of ledger rows that only carry `result`."""
    k = g.get("kind")
    if k in GATE_KINDS:
        return k
    return {"pass": "pass", "fail": "fail", "skip": "skipped",
            "banner": "banner", "unknown": "incomplete"}.get(
                g.get("result"),
                "pass" if g.get("exit_code") == 0 else "incomplete")


def parse_raw_log(path: str) -> dict:
    root = level = None
    gates = []
    cur = None
    commit = None
    try:
        with open(path, errors="replace") as fh:
            for ln in fh:
                ln = ln.rstrip("\n")
                if root is None and ln.startswith("Root:"):
                    root = ln.split(":", 1)[1].strip()
                elif level is None and ln.startswith("Level:"):
                    level = ln.split(":", 1)[1].strip()
                elif ln.startswith("=== ") and ln.endswith(" ==="):
                    if cur is not None:  # previous gate never got its Duration footer
                        gates.append(cur)
                    cur = {"name": ln[4:-4].strip()}
                elif cur is not None and ln.startswith("Command:"):
                    cur["_has_command"] = True
                elif cur is not None and ln.startswith("Skipped:"):
                    cur["_skipped"] = True
                elif cur is not None and ln.startswith("Gate timed out after"):
                    # validate.sh writes this as its own line (run_timed_command);
                    # requiring the line prefix keeps guest stdout from ever
                    # forging the marker.
                    cur["_timed_out"] = True
                elif cur is not None and ln.startswith("Exit:"):
                    try:
                        cur["exit_code"] = int(ln.split(":", 1)[1].strip())
                    except ValueError:
                        cur["exit_code"] = -1
                    cur["_has_exit"] = True
                elif cur is not None and ln.startswith("Duration:"):
                    m = DUR_RE.search(ln)
                    cur["real_seconds"] = int(m.group(1)) if m else None
                    gates.append(cur)
                    cur = None
                elif commit is None:
                    m = SHA_RE.search(ln)
                    if m:
                        commit = m.group(0)
    except OSError:
        pass
    if cur is not None:  # trailing gate with no Duration footer (run cut off)
        gates.append(cur)
    counts = {k: 0 for k in GATE_KINDS}
    for g in gates:
        g["kind"] = _classify_gate(g)
        g["result"] = _KIND_TO_RESULT[g["kind"]]
        counts[g["kind"]] += 1
        for k in ("_has_command", "_skipped", "_timed_out", "_has_exit"):
            g.pop(k, None)
    # Run verdict, most-severe first. Killed-by-a-bound (`timeout`: a RESOURCE
    # story) is deliberately kept distinct from a product `fail` and from an
    # `incomplete` (cut-off) run, so attribution is never conflated. Banners and
    # skipped gates cannot decide the verdict.
    if counts["fail"]:
        result = "fail"
    elif counts["timeout"]:
        result = "timeout"
    elif counts["incomplete"]:
        result = "incomplete"
    else:
        result = "pass"
    non_banner = sum(counts[k] for k in GATE_KINDS if k != "banner")
    mtime = os.path.getmtime(path)
    return {
        "schema_version": 1,
        "finished_at": iso(mtime),
        "started_at": None,
        "host": os.uname().nodename,
        "slot": slot_from_cwd(root),
        "cwd": root,
        "profile": level,
        "commit": commit or "unknown",
        "result": result,
        "checks": non_banner,
        "failures": counts["fail"],            # product failures only
        "killed_by_bound": counts["timeout"],  # resource story: timeout/cgroup/contention
        "incomplete_gates": counts["incomplete"],  # run cut off before the gate footer
        "skipped_gates": counts["skipped"],
        "banner_lines": counts["banner"],
        "gate_classification": counts,
        "real_seconds": sum(g.get("real_seconds") or 0 for g in gates),
        "user_seconds": None,
        "sys_seconds": None,
        "log_file": path,
        "gates": gates,
        "_source": "reconstructed",
        "_ledger_file": None,
        "_mtime": mtime,
    }


def load_all_runs(parent: str) -> list[dict]:
    records = load_ledger_records(parent)
    # Add reconstructed records for every raw log not already covered.
    for d in tmpdirs():
        for path in glob.glob(os.path.join(d, "hermit-validate.*.log")):
            if path in records:
                continue
            records[path] = parse_raw_log(path)
    # Also scan committed worktree run logs (validate-run-*.log) under any
    # ignored/ dir, pruning heavy build trees.
    def is_run_log(fn: str) -> bool:
        return fn.startswith("validate-run") and fn.endswith(".log")

    for path in walk_pruned(parent, is_run_log):
        if os.sep + "ignored" + os.sep in path and path not in records:
            records[path] = parse_raw_log(path)
    runs = list(records.values())
    for r in runs:
        r.setdefault("_mtime", 0)
        if not r.get("_mtime"):
            lf = r.get("log_file")
            r["_mtime"] = os.path.getmtime(lf) if lf and os.path.isfile(lf) else 0
        r["_sortkey"] = r.get("started_at") or r.get("finished_at") or iso(r["_mtime"])
    runs.sort(key=lambda r: r["_sortkey"])
    return runs


# ---------------------------------------------------------------------------
# Source 3: safe-ci-dag-runner profiling
# ---------------------------------------------------------------------------

def discover_profiles(parent: str) -> list[dict]:
    rows = []

    def is_agg_csv(fn: str) -> bool:
        return (fn.endswith(".csv") and not fn.startswith("step_profiles_"))

    for path in walk_pruned(parent, is_agg_csv):
        if "/.safe-ci-dag-runner/profiles/" not in path:
            continue
        checkout = path.split("/.safe-ci-dag-runner/")[0]
        try:
            with open(path, errors="replace") as fh:
                for row in csvmod.DictReader(fh):
                    row["_checkout"] = checkout
                    row["_slot"] = slot_from_cwd(checkout + "/")
                    row["_file"] = path
                    rows.append(row)
        except OSError:
            continue
    return rows


def _epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def link_profiling(runs: list[dict], profiles: list[dict], window_s: int = 90) -> int:
    by_sha = {}
    for p in profiles:
        by_sha.setdefault((p.get("git_sha") or "")[:12], []).append(p)
    linked = 0
    for r in runs:
        sha = (r.get("commit") or "")[:12]
        cands = by_sha.get(sha, []) if sha and sha != "unknown"[:12] else []
        match_kind = "git_sha"
        if not cands:
            # Fallback: same slot + profiling timestamp within window of the run.
            # (validate.sh and safe-ci-dag-runner record different SHA fields for
            # the same invocation, but the wall-clock timestamps coincide.)
            rt = _epoch(r.get("finished_at") or r.get("started_at"))
            if rt is not None:
                cands = [p for p in profiles
                         if p.get("_slot") == r.get("slot")
                         and (pt := _epoch(p.get("timestamp"))) is not None
                         and abs(pt - rt) <= window_s]
                match_kind = "slot+time"
        # Prefer same slot.
        same = [c for c in cands if c.get("_slot") == r.get("slot")]
        chosen = (same or cands)
        if chosen:
            r["_profiling_match"] = match_kind
            r["_profiling"] = {
                "n_profiles": len(chosen),
                "checkouts": sorted({c["_checkout"] for c in chosen}),
                "sample": {k: chosen[0].get(k) for k in
                           ("timestamp", "wall_s", "user_s", "sys_s", "n_steps",
                            "total_busy_pct", "jobs", "nproc")},
            }
            linked += 1
        else:
            r["_profiling"] = None
    return linked


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_secs(v):
    return "-" if v is None else f"{float(v):.0f}"


def render_table(runs: list[dict]) -> str:
    # WALL/USER/SYS carry an explicit `(s)` so a bare number is never unit-less.
    # PROFILE is never truncated: a clipped identifier (`portable-stric`) is not
    # an identifier.
    hdr = ("TIME (UTC)", "SLOT", "COMMIT", "PROFILE", "RESULT",
           "GATES", "WALL(s)", "USER(s)", "SYS(s)", "SRC", "PROF")
    lines = []
    rows = []
    for r in runs:
        gates = r.get("gates") or []
        verdict = [g for g in gates if gate_kind(g) in VERDICT_KINDS]
        npass = sum(1 for g in verdict if gate_kind(g) == "pass")
        # GATES denominator counts only verdict-bearing gates, so section banners
        # never inflate it. Fall back to the run's own tallies for ledger rows
        # whose gate list is absent.
        if verdict:
            ngates = f"{npass}/{len(verdict)}"
        else:
            checks = r.get("checks")
            fails = r.get("failures") or 0
            ngates = f"{(checks - fails)}/{checks}" if checks is not None else "-"
        t = (r.get("started_at") or r.get("finished_at") or "?")[:19]
        rows.append((
            t,
            (r.get("slot") or "?")[:16],
            (r.get("commit") or "?")[:8],
            (r.get("profile") or "?"),
            r.get("result") or "?",
            ngates,
            fmt_secs(r.get("real_seconds")),
            fmt_secs(r.get("user_seconds")),
            fmt_secs(r.get("sys_seconds")),
            "L" if r.get("_source") == "ledger" else "R",
            "y" if r.get("_profiling") else "-",
        ))
    widths = [len(h) for h in hdr]
    for row in rows:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    lines.append(fmt.format(*hdr))
    lines.append(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        lines.append(fmt.format(*[str(c) for c in row]))
    return "\n".join(lines)


def summarize(runs, profiles, linked) -> str:
    by_result, by_slot, by_src = {}, {}, {}
    for r in runs:
        by_result[r.get("result")] = by_result.get(r.get("result"), 0) + 1
        by_slot[r.get("slot")] = by_slot.get(r.get("slot"), 0) + 1
        by_src[r.get("_source")] = by_src.get(r.get("_source"), 0) + 1
    # De-conflate the old catch-all: killed-by-a-bound (resource story) vs
    # product failures vs cut-off runs are separate attributions.
    killed = sum(r.get("killed_by_bound") or 0 for r in runs)
    incomplete = sum(r.get("incomplete_gates") or 0 for r in runs)
    prod_fail = sum(r.get("failures") or 0 for r in runs)
    out = ["", "=== Machine-wide validate-run summary ==="]
    out.append(f"  total runs        : {len(runs)}")
    out.append(f"  by result         : " +
               ", ".join(f"{k}={v}" for k, v in sorted(by_result.items(), key=lambda x: str(x[0]))))
    out.append(f"  gate attribution  : "
               f"product-fail={prod_fail}, killed-by-bound={killed} "
               f"(timeout/cgroup/contention), incomplete={incomplete} (run cut off)"
               "   (RESULT values: pass/fail/timeout/incomplete)")
    out.append(f"  by source         : " +
               ", ".join(f"{k}={v}" for k, v in sorted(by_src.items(), key=lambda x: str(x[0]))) +
               "   (L=ledger, R=reconstructed-from-raw-log)")
    out.append(f"  by slot/worktree  : " +
               ", ".join(f"{k}={v}" for k, v in sorted(by_slot.items(), key=lambda x: -x[1])))
    out.append(f"  profiling CSVs    : {len(profiles)} rows across "
               f"{len({p['_checkout'] for p in profiles})} checkouts; "
               f"{linked}/{len(runs)} runs linked to profiling by git_sha")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit unified records as JSON")
    ap.add_argument("--csv", metavar="FILE", help="write a flat CSV")
    ap.add_argument("--write-global", action="store_true",
                    help="persist unified JSONL to <parent>/ignored/validate-run-global.jsonl")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only runs on/after this UTC date")
    ap.add_argument("--slot", metavar="NAME", help="filter to one slot/worktree")
    ap.add_argument("--profiling", action="store_true",
                    help="show profiling-source coverage instead of the run table")
    args = ap.parse_args()

    parent = parent_root()
    runs = load_all_runs(parent)
    profiles = discover_profiles(parent)
    linked = link_profiling(runs, profiles)

    if args.since:
        runs = [r for r in runs if (r.get("_sortkey") or "") >= args.since]
    if args.slot:
        runs = [r for r in runs if r.get("slot") == args.slot]

    if args.profiling:
        by_ckt = {}
        for p in profiles:
            by_ckt.setdefault(p["_checkout"], []).append(p)
        print("safe-ci-dag-runner profiling (retained per checkout, appended per run):")
        for ckt, rows in sorted(by_ckt.items()):
            ts = sorted({r.get("timestamp") for r in rows})
            print(f"  {slot_from_cwd(ckt + '/'):20} runs={len(ts):3}  "
                  f"first={ts[0]}  last={ts[-1]}")
            print(f"      dir: {ckt}/.safe-ci-dag-runner/profiles/")
        print(f"\n  total profiling rows: {len(profiles)} across {len(by_ckt)} checkouts")
        return 0

    if args.write_global:
        outp = os.path.join(parent, "ignored", "validate-run-global.jsonl")
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        with open(outp, "w") as fh:
            for r in runs:
                clean = {k: v for k, v in r.items() if not k.startswith("_")}
                clean["source"] = r.get("_source")
                clean["profiling_linked"] = bool(r.get("_profiling"))
                fh.write(json.dumps(clean) + "\n")
        print(f"wrote {len(runs)} unified records -> {outp}")

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csvmod.writer(fh)
            # Units: real_s/user_s/sys_s are seconds. failures = product
            # failures only; killed_by_bound (timeout/cgroup/contention) and
            # incomplete (run cut off) are separate columns, not folded in.
            w.writerow(["time", "slot", "commit", "profile", "result",
                        "checks", "failures", "killed_by_bound", "incomplete",
                        "real_s", "user_s", "sys_s",
                        "source", "profiling_linked", "log_file"])
            for r in runs:
                w.writerow([
                    r.get("started_at") or r.get("finished_at"), r.get("slot"),
                    r.get("commit"), r.get("profile"), r.get("result"),
                    r.get("checks"), r.get("failures"),
                    r.get("killed_by_bound"), r.get("incomplete_gates"),
                    r.get("real_seconds"),
                    r.get("user_seconds"), r.get("sys_seconds"), r.get("_source"),
                    bool(r.get("_profiling")), r.get("log_file")])
        print(f"wrote CSV -> {args.csv}")

    if args.json:
        out = []
        for r in runs:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            clean["source"] = r.get("_source")
            clean["profiling"] = r.get("_profiling")
            out.append(clean)
        print(json.dumps(out, indent=2))
        return 0

    if not args.csv and not args.write_global:
        print(render_table(runs))
    print(summarize(runs, profiles, linked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
