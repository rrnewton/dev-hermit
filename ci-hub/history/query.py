#!/usr/bin/env python3
"""Read side of the ci-hub CI history store.

Reads only the file-contract stores that ingest.py maintains
(ignored/ci-hub/gha-runs.csv, downloaded ci-perf step_profiles CSVs, and local
.safe-ci-dag-runner/profiles/ CSVs). No GitHub access; no imports of ingest.py
internals.

Commands:
  (default)          summary (queue vs run-time shape) PLUS the K most-recent
                     individual runs, so a bare `ci-hub history` shows both the
                     distribution and the runs behind it. `--since` works here.
  node-cpu-budgets   per DAG-node CPU-second budgets for the cpu_timeout
                     derivation (round(max_cpu_s * 1.5), n>=5 else thin/UNSET).
  green-time         % of main-branch wall-clock time whose authoritative CI
                     conclusion was success, DERIVED from the store (never
                     estimated).
  runs               same summary + recent-runs listing (alias of the default).

Usage:
  ci-hub/history/query.py [--repo R] [--since DATE] [--branch B] [--status S]
                          [--limit K] [--summary-only] [--json]
  ci-hub/history/query.py node-cpu-budgets [--repo R] [--since SHA|DATE] [--format csv|json]
  ci-hub/history/query.py green-time [--repo R] [--since DATE] [--workflow NAME] [--format text|json]
  ci-hub/history/query.py runs [--repo R] [--since DATE] [--branch B] [--status S] [--limit K]

The `--since`/`--repo`/`--branch`/`--limit`/`--json` flag names deliberately match
`ci-hub local-history` so the two history subcommands do not diverge. Every view
reads the SAME ignored/ci-hub/gha-runs.csv store, so this listing and any other
consumer of the store (e.g. queued-run analyses) report the same numbers.
"""
from __future__ import annotations

import argparse
import csv as csvmod
import datetime as dt
import json
import math
import os
import re
import sys

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

# Authoritative main-branch workflow per repo for the green-time metric.
# Override with --workflow (repeatable). These are the current fork gates; the
# hermit portable CI and the reverie Rust suite.
AUTHORITATIVE = {
    "rrnewton/hermit": ["CI (GitHub-managed portable)"],
    "rrnewton/reverie": ["Rust"],
}

PRUNE = {"target", ".git", "node_modules", ".cargo", "incremental", "deps",
         "build", ".venv", "__pycache__"}


def parent_root() -> str:
    env = os.environ.get("DEV_HERMIT_PARENT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def store_dir(parent: str) -> str:
    return os.path.join(parent, "ignored", "ci-hub")


def gha_store_path(parent: str) -> str:
    return os.path.join(store_dir(parent), "gha-runs.csv")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    rank = max(1, math.ceil(p / 100.0 * len(sorted_vals)))
    return sorted_vals[rank - 1]


def walk_pruned(root: str, want, max_depth: int = 8):
    root = os.path.abspath(root)
    base_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip("/").count("/") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames
                       if d not in PRUNE and not d.startswith(".safe-ci-dag-runner.")]
        for fn in filenames:
            if want(fn):
                yield os.path.join(dirpath, fn)


def load_gha_rows(parent: str, repo: str | None, since: str | None,
                  branch: str | None) -> list[dict]:
    path = gha_store_path(parent)
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, newline="", errors="replace") as fh:
        for row in csvmod.DictReader(fh):
            if repo and row.get("repo") != repo:
                continue
            if branch and row.get("head_branch") != branch:
                continue
            if since and (row.get("created_at") or "") < since:
                continue
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# node-cpu-budgets
# ---------------------------------------------------------------------------

def discover_step_profiles(parent: str, repo: str | None) -> list[dict]:
    """Every step_profiles_*.csv row from local checkouts + downloaded ci-perf."""
    rows: list[dict] = []

    def is_step_csv(fn: str) -> bool:
        return fn.startswith("step_profiles_") and fn.endswith(".csv")

    # Local safe-ci-dag-runner profiling under any checkout.
    for path in walk_pruned(parent, is_step_csv):
        if "/.safe-ci-dag-runner/profiles/" not in path:
            continue
        _read_step_csv(path, rows, origin="local")

    # Downloaded GitHub ci-perf artifacts.
    gha_prof = os.path.join(store_dir(parent), "gha-profiles")
    if os.path.isdir(gha_prof):
        sub = gha_prof
        if repo:
            cand = os.path.join(gha_prof, repo.replace("/", "__"))
            sub = cand if os.path.isdir(cand) else gha_prof
        for path in walk_pruned(sub, is_step_csv):
            _read_step_csv(path, rows, origin="github")
    return rows


def _read_step_csv(path: str, rows: list[dict], origin: str) -> None:
    try:
        with open(path, newline="", errors="replace") as fh:
            for row in csvmod.DictReader(fh):
                row["_origin"] = origin
                rows.append(row)
    except OSError:
        pass


def node_cpu_budgets(parent: str, repo: str | None, since: str | None,
                     min_samples: int) -> list[dict]:
    profiles = discover_step_profiles(parent, repo)

    since_sha = since if since and SHA_RE.match(since) else None
    since_date = since if since and not since_sha else None

    by_node: dict[str, dict] = {}
    for r in profiles:
        node = (r.get("step") or "").strip()
        if not node:
            continue
        if since_sha and not (r.get("git_sha") or "").startswith(since_sha):
            continue
        if since_date and (r.get("timestamp") or "") < since_date:
            continue
        user = _float(r.get("user_s"))
        sys_ = _float(r.get("sys_s"))
        wall = _float(r.get("elapsed_s"))
        agg = by_node.setdefault(node, {"cpu": [], "wall": [], "rows": 0})
        agg["rows"] += 1
        if user is not None and sys_ is not None:
            agg["cpu"].append(user + sys_)
        if wall is not None:
            agg["wall"].append(wall)

    out = []
    for node, agg in sorted(by_node.items()):
        cpu = sorted(agg["cpu"])
        wall = sorted(agg["wall"])
        n = len(cpu)
        max_cpu = cpu[-1] if cpu else None
        thin = n < min_samples
        suggested = None if (thin or max_cpu is None) else int(round(max_cpu * 1.5))
        out.append({
            "node": node,
            "n_samples": n,
            "n_rows": agg["rows"],
            "max_cpu_s": round(max_cpu, 2) if max_cpu is not None else None,
            "p95_cpu_s": round(percentile(cpu, 95), 2) if cpu else None,
            "p50_cpu_s": round(percentile(cpu, 50), 2) if cpu else None,
            "max_wall_s": round(wall[-1], 2) if wall else None,
            "suggested_cpu_timeout": suggested,
            "thin": thin,
        })
    return out


def render_node_budgets(rows: list[dict], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(rows, indent=2)
    if fmt == "csv":
        import io
        buf = io.StringIO()
        cols = ["node", "n_samples", "max_cpu_s", "p95_cpu_s", "p50_cpu_s",
                "max_wall_s", "suggested_cpu_timeout", "thin"]
        w = csvmod.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
        return buf.getvalue().rstrip("\n")
    # text table. CPU/WALL columns are seconds; the header says so, so a bare
    # number is never unit-less (matches the JSON/CSV `_s` field names).
    hdr = ("NODE", "N", "MAX_CPU(s)", "P95_CPU(s)", "P50_CPU(s)", "MAX_WALL(s)",
           "SUGGEST_TIMEOUT(s)", "THIN")
    body = [(r["node"], str(r["n_samples"]),
             _s(r["max_cpu_s"]), _s(r["p95_cpu_s"]), _s(r["p50_cpu_s"]),
             _s(r["max_wall_s"]),
             "-" if r["suggested_cpu_timeout"] is None else str(r["suggested_cpu_timeout"]),
             "thin" if r["thin"] else "") for r in rows]
    return _table(hdr, body)


def _s(v):
    return "-" if v is None else f"{v:g}"


def _table(hdr, body) -> str:
    widths = [len(h) for h in hdr]
    for row in body:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    lines = [fmt.format(*hdr), fmt.format(*["-" * w for w in widths])]
    for row in body:
        lines.append(fmt.format(*[str(c) for c in row]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# green-time  (owner headline metric — derived, never estimated)
# ---------------------------------------------------------------------------

def green_time(parent: str, repo: str, since: str | None,
               workflows: list[str] | None) -> dict:
    wanted = workflows or AUTHORITATIVE.get(repo, [])
    rows = load_gha_rows(parent, repo, since, branch="main")
    # Only terminal authoritative runs define the state timeline.
    events = []
    for r in rows:
        if wanted and r.get("workflow_name") not in wanted:
            continue
        if r.get("status") != "completed":
            continue
        ts = _epoch(r.get("updated_at"))
        if ts is None:
            continue
        events.append((ts, r.get("conclusion") or "", r.get("head_sha") or "",
                       r.get("updated_at") or ""))
    events.sort()

    if not events:
        return {"repo": repo, "workflows": wanted, "samples": 0,
                "green_pct": None,
                "note": "no terminal authoritative main-branch runs in store"}

    now = dt.datetime.now(dt.timezone.utc).timestamp()
    total = 0.0
    green = 0.0
    for i, (ts, concl, _sha, _iso) in enumerate(events):
        end = events[i + 1][0] if i + 1 < len(events) else now
        span = max(0.0, end - ts)
        total += span
        if concl == "success":
            green += span
    by_concl: dict[str, int] = {}
    for _ts, concl, _sha, _iso in events:
        by_concl[concl] = by_concl.get(concl, 0) + 1
    return {
        "repo": repo,
        "workflows": wanted,
        "samples": len(events),
        "window_start": events[0][3],
        "window_end_utc": dt.datetime.fromtimestamp(
            now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "green_pct": round(100.0 * green / total, 2) if total > 0 else None,
        "green_hours": round(green / 3600.0, 2),
        "total_hours": round(total / 3600.0, 2),
        "runs_by_conclusion": by_concl,
        "current_state": events[-1][1],
        "current_sha": events[-1][2],
    }


# ---------------------------------------------------------------------------
# runs summary
# ---------------------------------------------------------------------------

def runs_summary(parent: str, repo: str | None, since: str | None,
                 branch: str | None) -> str:
    rows = load_gha_rows(parent, repo, since, branch)
    if not rows:
        return "ci-hub history: gha-runs.csv empty or missing (run refresh-history first)."
    by_repo: dict[str, dict] = {}
    for r in rows:
        rp = r.get("repo", "?")
        agg = by_repo.setdefault(rp, {"n": 0, "concl": {}, "queue": [], "run": []})
        agg["n"] += 1
        c = r.get("conclusion") or (r.get("status") or "?")
        agg["concl"][c] = agg["concl"].get(c, 0) + 1
        q = _float(r.get("queue_s"))
        rn = _float(r.get("run_s"))
        if q is not None:
            agg["queue"].append(q)
        if rn is not None:
            agg["run"].append(rn)
    out = ["=== ci-hub GHA run history ==="]
    for rp, agg in sorted(by_repo.items()):
        q = sorted(agg["queue"])
        rn = sorted(agg["run"])
        out.append(f"\n{rp}: {agg['n']} runs"
                   + (f" (branch={branch})" if branch else ""))
        out.append("  by conclusion: " +
                   ", ".join(f"{k}={v}" for k, v in sorted(agg["concl"].items(),
                                                           key=lambda x: -x[1])))
        if q:
            out.append(f"  queue_s : median={percentile(q,50):.0f} "
                       f"p95={percentile(q,95):.0f} max={q[-1]:.0f}")
        if rn:
            out.append(f"  run_s   : median={percentile(rn,50):.0f} "
                       f"p95={percentile(rn,95):.0f} max={rn[-1]:.0f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# recent runs listing (individual runs behind the summary shape)
# ---------------------------------------------------------------------------

# A run is flagged as a queue outlier when it waited longer than this floor AND
# above the window's p95 queue time. The floor keeps a p95 of 0 (the common case
# — most runs start instantly) from flagging every run; only genuinely stuck
# runs (waiting on a runner) get marked.
QUEUE_OUTLIER_FLOOR_S = 300.0


def _effective_conclusion(r: dict) -> str:
    """What actually happened: the terminal conclusion, else the live status."""
    return (r.get("conclusion") or r.get("status") or "?")


def _branch_or_pr(r: dict) -> str:
    """Prefer the PR reference over the raw branch when the run has one."""
    prs = (r.get("pull_requests") or "").strip()
    if prs:
        first = prs.split()[0].split(",")[0].strip()
        if first:
            return f"#{first}"
    return r.get("head_branch") or "-"


def _short_utc(s: str | None) -> str:
    """2026-08-02T00:00:44Z -> '08-02 00:00' (compact, still UTC)."""
    if not s or len(s) < 16:
        return s or "-"
    return f"{s[5:10]} {s[11:16]}"


def _is_queue_outlier(q: float | None, thresh: float) -> bool:
    return q is not None and q > thresh and q > 0


def recent_runs(parent: str, repo: str | None, since: str | None,
                branch: str | None, status: str | None, limit: int,
                slowest: bool = False) -> dict:
    """K runs plus the window's queue/run shape they came from.

    Ordered newest-first by default, or by descending queue wait with
    ``slowest=True`` (surfaces the handful of runs stuck for hours behind the
    p95=0 median). The queue-outlier flag is computed against the p95 of the
    FILTERED window, so it means "slow relative to this selection".
    """
    rows = load_gha_rows(parent, repo, since, branch)
    if status:
        rows = [r for r in rows if _effective_conclusion(r) == status]
    queues = sorted(q for q in (_float(r.get("queue_s")) for r in rows)
                    if q is not None)
    p95_queue = percentile(queues, 95) or 0.0
    thresh = max(QUEUE_OUTLIER_FLOOR_S, p95_queue)
    # Count outliers across the WHOLE matched window, not just the shown K, so
    # the "handful stuck for hours" is visible even when the newest K were fast.
    window_outliers = sum(1 for q in queues if _is_queue_outlier(q, thresh))
    if slowest:
        rows.sort(key=lambda r: _float(r.get("queue_s")) or -1.0, reverse=True)
    else:
        # created_at is a fixed-width ISO string, so lexical == chronological.
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    out = []
    for r in rows[:max(0, limit)]:
        q = _float(r.get("queue_s"))
        out.append({
            "created_at": r.get("created_at") or "",
            "repo": r.get("repo") or "?",
            "run_id": r.get("run_id") or "",
            "workflow": r.get("workflow_name") or "?",
            "ref": _branch_or_pr(r),
            "conclusion": _effective_conclusion(r),
            "queue_s": q,
            "run_s": _float(r.get("run_s")),
            "url": r.get("html_url") or "",
            "queue_outlier": _is_queue_outlier(q, thresh),
        })
    return {
        "total_matched": len(rows),
        "shown": len(out),
        "order": "slowest-queue" if slowest else "newest",
        "queue_p95_s": round(p95_queue, 1),
        "queue_outlier_threshold_s": round(thresh, 1),
        "window_outliers": window_outliers,
        "runs": out,
    }


def render_recent(res: dict, limit: int) -> str:
    runs = res["runs"]
    if not runs:
        return "  (no individual runs match this filter)"
    hdr = ("", "TIME(UTC)", "REPO", "RUN_ID", "WORKFLOW", "BRANCH/PR",
           "CONCL", "QUEUE(s)", "RUN(s)", "URL")
    body = []
    for r in runs:
        mark = "!" if r["queue_outlier"] else ""
        body.append((
            mark,
            _short_utc(r["created_at"]),
            r["repo"],
            str(r["run_id"]),
            r["workflow"],
            r["ref"],
            r["conclusion"],
            _s(r["queue_s"]),
            _s(r["run_s"]),
            r["url"],
        ))
    n_out = sum(1 for r in runs if r["queue_outlier"])
    order = "slowest-queue first" if res.get("order") == "slowest-queue" \
        else "newest first"
    head = (f"--- {res['shown']} of {res['total_matched']} matched "
            f"runs ({order}) ---")
    legend = (f"! = queued > {res['queue_outlier_threshold_s']:.0f}s "
              f"(floor {QUEUE_OUTLIER_FLOOR_S:.0f}s, window p95 "
              f"{res['queue_p95_s']:.0f}s) — waiting on a runner; "
              f"{n_out} of {res['shown']} shown flagged, "
              f"{res['window_outliers']} of {res['total_matched']} in window "
              f"(use --slowest to see them)")
    return "\n".join([head, _table(hdr, body), legend])


# ---------------------------------------------------------------------------

def emit_history(parent: str, args) -> int:
    """Shared renderer for the default view and the `runs` alias."""
    repo = getattr(args, "repo", None)
    since = getattr(args, "since", None)
    branch = getattr(args, "branch", None)
    status = getattr(args, "status", None)
    limit = getattr(args, "limit", 20)
    slowest = getattr(args, "slowest", False)
    summary_only = getattr(args, "summary_only", False)
    as_json = getattr(args, "json", False)

    if as_json:
        res = recent_runs(parent, repo, since, branch, status, limit, slowest)
        print(json.dumps(res, indent=2))
        return 0
    print(runs_summary(parent, repo, since, branch))
    if not summary_only:
        res = recent_runs(parent, repo, since, branch, status, limit, slowest)
        print()
        print(render_recent(res, limit))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # Top-level filters so a bare `ci-hub history --since ...` works (the flag
    # was previously only defined on subparsers, so `history --since` errored).
    # Names mirror `ci-hub local-history` for a consistent surface.
    ap.add_argument("--repo")
    ap.add_argument("--since", help="YYYY-MM-DD (or full ISO timestamp)")
    ap.add_argument("--branch")
    ap.add_argument("--status",
                    help="filter by conclusion/status, e.g. queued, failure")
    ap.add_argument("--limit", type=int, default=20,
                    help="show this many runs (default 20)")
    ap.add_argument("--slowest", action="store_true",
                    help="order by descending queue wait instead of recency")
    ap.add_argument("--summary-only", action="store_true",
                    help="print only the queue/run shape, omit the run listing")
    ap.add_argument("--json", action="store_true",
                    help="emit the recent-runs listing as JSON")
    sub = ap.add_subparsers(dest="cmd")

    p_nb = sub.add_parser("node-cpu-budgets",
                          help="per-DAG-node CPU budgets for cpu_timeout")
    p_nb.add_argument("--repo")
    p_nb.add_argument("--since", help="git SHA prefix or YYYY-MM-DD")
    p_nb.add_argument("--min-samples", type=int, default=5)
    p_nb.add_argument("--format", choices=["text", "csv", "json"], default="text")

    p_gt = sub.add_parser("green-time",
                          help="%% main wall-clock time green (derived)")
    p_gt.add_argument("--repo", default="rrnewton/hermit")
    p_gt.add_argument("--since", help="YYYY-MM-DD")
    p_gt.add_argument("--workflow", action="append",
                      help="authoritative workflow name (repeatable)")
    p_gt.add_argument("--format", choices=["text", "json"], default="text")

    p_ru = sub.add_parser("runs",
                          help="summary + recent-runs listing (alias of default)")
    p_ru.add_argument("--repo")
    p_ru.add_argument("--since", help="YYYY-MM-DD")
    p_ru.add_argument("--branch")
    p_ru.add_argument("--status")
    p_ru.add_argument("--limit", type=int, default=20)
    p_ru.add_argument("--slowest", action="store_true")
    p_ru.add_argument("--summary-only", action="store_true")
    p_ru.add_argument("--json", action="store_true")

    args = ap.parse_args()
    parent = parent_root()

    if args.cmd == "node-cpu-budgets":
        rows = node_cpu_budgets(parent, args.repo, args.since, args.min_samples)
        print(render_node_budgets(rows, args.format))
        return 0
    if args.cmd == "green-time":
        res = green_time(parent, args.repo, args.since, args.workflow)
        if args.format == "json":
            print(json.dumps(res, indent=2))
        else:
            if res.get("green_pct") is None:
                print(f"{res['repo']}: green-time UNAVAILABLE — {res.get('note','')}")
            else:
                print(f"{res['repo']} main green-time (authoritative "
                      f"{res['workflows']}): {res['green_pct']}% "
                      f"({res['green_hours']}h green / {res['total_hours']}h) "
                      f"over {res['samples']} runs since {res['window_start']}; "
                      f"current state={res['current_state']} @ "
                      f"{res['current_sha'][:8]}")
                print(f"  runs by conclusion: {res['runs_by_conclusion']}")
        return 0
    if args.cmd == "runs" or args.cmd is None:
        return emit_history(parent, args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
