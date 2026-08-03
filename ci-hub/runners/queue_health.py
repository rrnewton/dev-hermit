#!/usr/bin/env python3
"""GitHub Actions queue-depth, wait-time, and last-green health for the Hermit repos.

Non-mutating. Answers four operational questions the bare `runner-health`
histogram could not, keeping *time in queue* strictly separate from *run
duration* (the hosted/self-hosted pools are queue-starved, so conflating the two
poisons every downstream analysis):

  1. QUEUE DEPTH per workflow      — how many runs are queued vs already running.
  2. WAIT TIMES                    — time-in-queue distribution, reported apart
                                     from run duration. Two independent bases:
                                       * current queue age  = now - run.createdAt
                                         for still-queued runs (a *lower bound* on
                                         each run's final wait; free from the run
                                         list);
                                       * historical time-in-queue = per-job
                                         (started_at - created_at) from the jobs
                                         API over a bounded sample of recent
                                         COMPLETED runs (the true runner-pickup
                                         latency; run-level `startedAt` equals
                                         `createdAt` and cannot be used).
  3. TIME SINCE LAST GREEN         — per workflow, both the elapsed wall time and
                                     HOW MANY RUNS BACK the last success was, so a
                                     green that is three pages deep surfaces here.
  4. SELF-HOSTED RUNNER HEALTH     — count, busy/idle, labels, and whether the
                                     single serial PMU lane (`pmu-serial`) is the
                                     binding constraint.

Every number is derived with its basis stated, per the workspace honesty rule.

This module is importable (used by `ci-status.py` for the human report and by
`ci-hub/health/operational_health.py queue-health` for the ops tick). `gh` is
invoked through `$GH` (default "with-proxy gh") so it works behind the devserver
proxy without touching the machine-global gh account.

Design note: this is the dev-hermit equivalent of the self-hosted+GitHub CI
"stats" reporting a sibling project surfaced via a Makefile target; that target
was never ported here (see runners/README.md). This ports the *capability* into
ci-hub, wired to the tick, rather than the target.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- Status vocabulary (GitHub Actions run/job status strings) ----------------
QUEUED_STATUSES = frozenset({"queued", "pending", "waiting", "requested"})
RUNNING_STATUSES = frozenset({"in_progress"})
INFLIGHT_STATUSES = QUEUED_STATUSES | RUNNING_STATUSES

# --- Tick thresholds (env-overridable) ---------------------------------------
# Each is a warn boundary for the fail-loud tick gate; overriding lets the
# coordinator tune sensitivity without a code change.
QUEUE_DEPTH_WARN = int(os.environ.get("CI_QUEUE_DEPTH_WARN", "10"))
QUEUE_AGE_WARN_SECS = int(os.environ.get("CI_QUEUE_AGE_WARN_SECS", str(30 * 60)))
GREEN_RUNS_BACK_WARN = int(os.environ.get("CI_GREEN_RUNS_BACK_WARN", "15"))
GREEN_AGE_WARN_SECS = int(os.environ.get("CI_GREEN_AGE_WARN_SECS", str(6 * 3600)))
# Only gate "no recent green" for workflows with at least this many runs in the
# window, so a rarely-triggered workflow does not flap the tick.
GREEN_GATE_MIN_RUNS = int(os.environ.get("CI_GREEN_GATE_MIN_RUNS", "4"))
# Comma-separated substrings of workflow names to exclude from the "no recent
# green" gate (e.g. an if:always aggregator that is structurally never "success").
# Default empty: nothing is silenced, so the current signal stays fully honest.
GREEN_GATE_EXCLUDE = [s.strip() for s in
                      os.environ.get("CI_GREEN_GATE_EXCLUDE", "").split(",")
                      if s.strip()]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def humanize_secs(secs: float | None) -> str:
    if secs is None:
        return "n/a"
    secs = int(secs)
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d{(secs % 86400) // 3600:02d}h"


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0,1]) of a non-empty sorted list."""
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    idx = int(round(q * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


# --- gh plumbing --------------------------------------------------------------
def gh_json(args: list[str], gh_cmd: str):
    """Run a gh subcommand and parse JSON stdout. Returns None on failure."""
    cmd = shlex.split(gh_cmd) + args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"  ! gh call failed ({exc.__class__.__name__}): {' '.join(args)}",
              file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"  ! gh returned {out.returncode}: {out.stderr.strip()[:200]}",
              file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def fetch_runs(repo: str, gh_cmd: str, limit: int) -> list[dict] | None:
    fields = ("databaseId,workflowName,status,conclusion,createdAt,updatedAt,"
              "headBranch,event")
    return gh_json(
        ["run", "list", "--repo", repo, "--limit", str(limit), "--json", fields],
        gh_cmd,
    )


def fetch_runners(repo: str, gh_cmd: str) -> dict | None:
    return gh_json(["api", f"repos/{repo}/actions/runners"], gh_cmd)


def fetch_job_timings(repo: str, gh_cmd: str, run_ids: list[int]) -> list[dict]:
    """Per-job (wait, duration, runner, workflow) over the given runs.

    wait     = job.started_at - job.created_at  (runner-pickup latency)
    duration = job.completed_at - job.started_at
    Only jobs that actually ran on a runner are returned; skipped/never-dispatched
    jobs (no runner, no real start) are dropped so they cannot deflate the wait
    distribution.
    """
    jobs: list[dict] = []
    for rid in run_ids:
        data = gh_json(["api", f"repos/{repo}/actions/runs/{rid}/jobs"], gh_cmd)
        if not data:
            continue
        for j in data.get("jobs", []):
            created = _parse_ts(j.get("created_at"))
            started = _parse_ts(j.get("started_at"))
            completed = _parse_ts(j.get("completed_at"))
            runner = j.get("runner_name")
            # A job that never got a runner (skipped/queued-then-cancelled) has
            # no meaningful pickup latency; exclude it from the wait sample.
            if runner is None or started is None or created is None:
                continue
            wait = (started - created).total_seconds()
            dur = ((completed - started).total_seconds()
                   if completed is not None else None)
            if wait < 0:
                wait = 0.0
            jobs.append({
                "run_id": rid,
                "name": j.get("name"),
                "runner": runner,
                "conclusion": j.get("conclusion"),
                "wait": wait,
                "duration": dur,
            })
    return jobs


# --- Analysis (pure functions over already-fetched data) ----------------------
@dataclass
class WorkflowQueue:
    workflow: str
    queued: int = 0
    running: int = 0
    queue_ages: list[float] = field(default_factory=list)  # secs, still-queued

    @property
    def inflight(self) -> int:
        return self.queued + self.running

    @property
    def max_age(self) -> float:
        return max(self.queue_ages) if self.queue_ages else 0.0

    @property
    def median_age(self) -> float:
        return _pct(sorted(self.queue_ages), 0.5) if self.queue_ages else 0.0


@dataclass
class LastGreen:
    workflow: str
    total_in_window: int
    runs_back: int | None          # 0 = most recent run is green; None = none found
    green_created: datetime | None
    green_id: int | None
    latest_status: str
    latest_conclusion: str | None


def analyze_queue(runs: list[dict], now: datetime | None = None
                  ) -> dict[str, WorkflowQueue]:
    now = now or _now()
    out: dict[str, WorkflowQueue] = {}
    for r in runs:
        wf = r.get("workflowName", "?")
        status = r.get("status", "")
        wq = out.setdefault(wf, WorkflowQueue(workflow=wf))
        if status in QUEUED_STATUSES:
            wq.queued += 1
            created = _parse_ts(r.get("createdAt"))
            if created is not None:
                wq.queue_ages.append((now - created).total_seconds())
        elif status in RUNNING_STATUSES:
            wq.running += 1
    return out


def analyze_last_green(runs: list[dict]) -> dict[str, LastGreen]:
    """runs must be newest-first (gh run list default)."""
    per_wf_runs: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        per_wf_runs[r.get("workflowName", "?")].append(r)
    out: dict[str, LastGreen] = {}
    for wf, wf_runs in per_wf_runs.items():
        runs_back = None
        green = None
        for i, r in enumerate(wf_runs):
            if r.get("conclusion") == "success":
                runs_back = i
                green = r
                break
        latest = wf_runs[0]
        out[wf] = LastGreen(
            workflow=wf,
            total_in_window=len(wf_runs),
            runs_back=runs_back,
            green_created=_parse_ts(green["createdAt"]) if green else None,
            green_id=green["databaseId"] if green else None,
            latest_status=latest.get("status", "?"),
            latest_conclusion=latest.get("conclusion"),
        )
    return out


@dataclass
class RunnerHealth:
    total: int
    online: int
    idle: int
    busy: int
    runners: list[dict]              # raw runner objects
    pmu_total: int
    pmu_idle: int
    serial_runners: list[str]        # names carrying pmu-serial
    serial_busy: bool


def analyze_runners(runner_api: dict | None) -> RunnerHealth | None:
    if not runner_api:
        return None
    runners = runner_api.get("runners", [])
    total = runner_api.get("total_count", len(runners))

    def labels_of(r):
        return {l["name"] for l in r.get("labels", [])}

    online = sum(1 for r in runners if r.get("status") == "online")
    busy = sum(1 for r in runners if r.get("busy"))
    idle = sum(1 for r in runners
               if r.get("status") == "online" and not r.get("busy"))
    pmu = [r for r in runners if "pmu" in labels_of(r)]
    pmu_idle = sum(1 for r in pmu
                   if r.get("status") == "online" and not r.get("busy"))
    serial = [r for r in runners if "pmu-serial" in labels_of(r)]
    serial_busy = any(r.get("busy") for r in serial)
    return RunnerHealth(
        total=total, online=online, idle=idle, busy=busy, runners=runners,
        pmu_total=len(pmu), pmu_idle=pmu_idle,
        serial_runners=[r.get("name") for r in serial], serial_busy=serial_busy,
    )


def binding_constraint(queues: dict[str, WorkflowQueue],
                       rh: RunnerHealth | None) -> str | None:
    """Return a one-line binding-constraint verdict, or None if not saturated.

    Basis is embedded in the string: it is derived from live runner idle counts
    and current queued depth, not folklore.
    """
    if rh is None:
        return None
    total_queued = sum(q.queued for q in queues.values())
    if rh.pmu_total and rh.pmu_idle == 0 and total_queued > 0:
        serial = (f"; single serial lane pmu-serial={','.join(rh.serial_runners)}"
                  f" is {'BUSY' if rh.serial_busy else 'idle'}"
                  if rh.serial_runners else "")
        return (f"self-hosted PMU lane saturated: 0 idle of {rh.pmu_total} pmu "
                f"runners with {total_queued} run(s) queued{serial}")
    if rh.idle == 0 and total_queued > 0:
        return (f"no idle self-hosted runner (0 of {rh.total}) with "
                f"{total_queued} run(s) queued")
    return None


@dataclass
class WaitSample:
    """Historical time-in-queue vs run duration, per workflow, from the jobs API."""
    workflow: str
    n: int
    wait_median: float
    wait_p90: float
    wait_max: float
    dur_median: float
    dur_p90: float


def analyze_waits(jobs: list[dict], run_workflow: dict[int, str]
                  ) -> dict[str, WaitSample]:
    by_wf_wait: dict[str, list[float]] = defaultdict(list)
    by_wf_dur: dict[str, list[float]] = defaultdict(list)
    for j in jobs:
        wf = run_workflow.get(j["run_id"], "?")
        by_wf_wait[wf].append(j["wait"])
        if j["duration"] is not None:
            by_wf_dur[wf].append(j["duration"])
    out: dict[str, WaitSample] = {}
    for wf, waits in by_wf_wait.items():
        sw = sorted(waits)
        sd = sorted(by_wf_dur.get(wf, []))
        out[wf] = WaitSample(
            workflow=wf, n=len(sw),
            wait_median=_pct(sw, 0.5), wait_p90=_pct(sw, 0.9), wait_max=sw[-1],
            dur_median=_pct(sd, 0.5) if sd else 0.0,
            dur_p90=_pct(sd, 0.9) if sd else 0.0,
        )
    return out


# --- Human report -------------------------------------------------------------
def report_repo(repo: str, gh_cmd: str, limit: int, sample: int) -> None:
    print(f"\n================ {repo} ================")
    now = _now()
    runs = fetch_runs(repo, gh_cmd, limit)
    runner_api = fetch_runners(repo, gh_cmd)
    rh = analyze_runners(runner_api)

    # (4) Runner health.
    if rh is None:
        print("  runners: (none at repo scope, or no access)")
    else:
        print(f"  runners: total={rh.total} online={rh.online} idle={rh.idle} "
              f"busy={rh.busy} | pmu={rh.pmu_total} (idle {rh.pmu_idle}) | "
              f"pmu-serial={','.join(rh.serial_runners) or 'none'}")
        for r in rh.runners:
            labels = ",".join(l["name"] for l in r.get("labels", []))
            job = "busy" if r.get("busy") else "idle"
            print(f"    - {r.get('name'):<22} {r.get('status'):<8} {job:<5} "
                  f"[{labels}]")

    if runs is None:
        print("  runs: (could not fetch) — basis: gh run list failed")
        return

    queues = analyze_queue(runs, now=now)
    greens = analyze_last_green(runs)

    # (1)+(2a) Queue depth + current queue age, per workflow.
    print(f"  queue depth (basis: gh run list, last {len(runs)} runs; "
          f"queue age = now - createdAt, a LOWER BOUND on final wait):")
    active = sorted((q for q in queues.values() if q.inflight),
                    key=lambda q: (-q.inflight, q.workflow))
    if not active:
        print("    (nothing in flight)")
    for q in active:
        age = (f" | queue age median {humanize_secs(q.median_age)} "
               f"max {humanize_secs(q.max_age)}" if q.queue_ages else "")
        print(f"    - {q.workflow:<32} queued={q.queued} running={q.running}"
              f"{age}")

    # Binding constraint.
    bc = binding_constraint(queues, rh)
    if bc:
        print(f"  ^ BINDING CONSTRAINT: {bc}")

    # (3) Time since last green + runs back.
    print("  last green per workflow (basis: newest-first scan of the window):")
    for wf in sorted(greens):
        g = greens[wf]
        if g.runs_back is None:
            print(f"    - {wf:<32} NO GREEN in last {g.total_in_window} runs "
                  f"(latest {g.latest_status}/{g.latest_conclusion or '-'})")
        else:
            elapsed = (humanize_secs((now - g.green_created).total_seconds())
                       if g.green_created else "n/a")
            back = ("most recent run" if g.runs_back == 0
                    else f"{g.runs_back} run(s) back")
            print(f"    - {wf:<32} GREEN {elapsed} ago, {back} "
                  f"(id {g.green_id})")

    # (2b) Historical time-in-queue vs run duration (jobs API sample).
    if sample > 0:
        completed_ids = [r["databaseId"] for r in runs
                         if r.get("status") == "completed"][:sample]
        jobs = fetch_job_timings(repo, gh_cmd, completed_ids)
        run_wf = {r["databaseId"]: r.get("workflowName", "?") for r in runs}
        waits = analyze_waits(jobs, run_wf)
        print(f"  time-in-queue vs run duration (basis: jobs API over "
              f"{len(completed_ids)} completed runs, {len(jobs)} jobs; wait = "
              f"job.started-created, SEPARATE from duration):")
        if not waits:
            print("    (no completed jobs sampled)")
        for wf in sorted(waits):
            w = waits[wf]
            print(f"    - {wf:<32} n={w.n} wait med/p90/max "
                  f"{humanize_secs(w.wait_median)}/{humanize_secs(w.wait_p90)}/"
                  f"{humanize_secs(w.wait_max)} | dur med/p90 "
                  f"{humanize_secs(w.dur_median)}/{humanize_secs(w.dur_p90)}")
    else:
        print("  time-in-queue distribution: skipped (--sample 0); "
              "current queue age above is the free lower-bound signal")


# --- Tick gate ----------------------------------------------------------------
def _field(value: object) -> str:
    return " ".join(str(value).split()) or "none"


def compute_gate(repo: str, gh_cmd: str, limit: int, now: datetime | None = None
                 ) -> tuple[int, dict[str, object]]:
    """Return (exit_code, fields) for the ops tick. Cheap: no jobs-API sampling.

    Unhealthy (exit 1) when any of: a workflow's queued depth >= QUEUE_DEPTH_WARN;
    max current queue age >= QUEUE_AGE_WARN_SECS; a workflow with >=
    GREEN_GATE_MIN_RUNS runs in the window has no green within GREEN_RUNS_BACK_WARN
    runs or GREEN_AGE_WARN_SECS; or the self-hosted lane is the active binding
    constraint. Every emitted number carries its basis in `summary`.
    """
    now = now or _now()
    runs = fetch_runs(repo, gh_cmd, limit)
    runner_api = fetch_runners(repo, gh_cmd)
    rh = analyze_runners(runner_api)
    if runs is None:
        return 1, {"state": "unknown", "summary": "gh-run-list-failed"}

    queues = analyze_queue(runs, now=now)
    greens = analyze_last_green(runs)

    max_depth = max((q.queued for q in queues.values()), default=0)
    worst_depth_wf = max(queues.values(), key=lambda q: q.queued, default=None)
    max_age = max((q.max_age for q in queues.values()), default=0.0)
    worst_age_wf = max(queues.values(), key=lambda q: q.max_age, default=None)

    stale_green: list[str] = []
    for wf, g in greens.items():
        if g.total_in_window < GREEN_GATE_MIN_RUNS:
            continue
        if any(sub in wf for sub in GREEN_GATE_EXCLUDE):
            continue
        no_recent = (g.runs_back is None
                     or g.runs_back >= GREEN_RUNS_BACK_WARN)
        old = (g.green_created is not None
               and (now - g.green_created).total_seconds() >= GREEN_AGE_WARN_SECS)
        if g.runs_back is None or no_recent or old:
            if g.runs_back is None:
                stale_green.append(f"{wf}:none/{g.total_in_window}")
            else:
                stale_green.append(f"{wf}:{g.runs_back}back")

    bc = binding_constraint(queues, rh)

    reasons: list[str] = []
    if max_depth >= QUEUE_DEPTH_WARN and worst_depth_wf:
        reasons.append(f"depth {worst_depth_wf.workflow}={max_depth}")
    if max_age >= QUEUE_AGE_WARN_SECS and worst_age_wf:
        reasons.append(f"age {worst_age_wf.workflow}={humanize_secs(max_age)}")
    if stale_green:
        reasons.append("stale-green " + ",".join(stale_green))
    if bc:
        reasons.append("binding-constraint")

    state = "red" if reasons else "ok"
    runners_txt = (f"total={rh.total},idle={rh.idle},pmu_idle={rh.pmu_idle}"
                   if rh else "unavailable")
    fields = {
        "state": state,
        "repo": repo,
        "max_queue_depth": max_depth,
        "max_queue_age": humanize_secs(max_age),
        "worst_workflow": worst_depth_wf.workflow if worst_depth_wf else "none",
        "stale_green": ",".join(stale_green) or "none",
        "binding_constraint": bc or "none",
        "runners": runners_txt,
        "summary": (f"depth<= {max_depth}, age<= {humanize_secs(max_age)}, "
                    f"reasons=[{'; '.join(reasons) or 'none'}] "
                    f"(basis: last {len(runs)} runs; queue age=now-createdAt)"),
    }
    return (1 if reasons else 0), fields


def gate(repos: list[str], gh_cmd: str, limit: int) -> int:
    rc = 0
    agg: dict[str, object] = {}
    for repo in repos:
        code, fields = compute_gate(repo, gh_cmd, limit)
        rc = rc or code
        if len(repos) > 1:
            agg[repo] = fields["state"]
        else:
            agg = fields
    if len(repos) > 1:
        # Multi-repo: emit a compact per-repo state map plus overall.
        print(f"state={'red' if rc else 'ok'}")
        print("summary=" + _field(",".join(f"{r}:{s}" for r, s in agg.items())))
    else:
        for k, v in agg.items():
            print(f"{k}={_field(v)}")
    return rc


# --- CLI ----------------------------------------------------------------------
DEFAULT_REPO = "rrnewton/hermit"
ALL_REPOS = ["rrnewton/hermit", "rrnewton/reverie",
             "facebookexperimental/hermit"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="GitHub Actions queue-depth / wait-time / last-green health",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--all", action="store_true", help="all three Hermit repos")
    p.add_argument("--limit", type=int, default=100,
                   help="recent runs to summarize (default 100)")
    p.add_argument("--sample", type=int, default=15,
                   help="completed runs to sample via the jobs API for the "
                        "time-in-queue distribution (0 disables; default 15)")
    p.add_argument("--gate", action="store_true",
                   help="emit key=value tick fields; exit 1 when unhealthy "
                        "(no jobs-API sampling)")
    p.add_argument("--gh", default=None,
                   help="gh command (default: $GH or 'with-proxy gh')")
    args = p.parse_args(argv)

    gh_cmd = args.gh or os.environ.get("GH", "with-proxy gh")
    repos = ALL_REPOS if args.all else [args.repo]

    if args.gate:
        return gate(repos, gh_cmd, args.limit)

    print(f"Hermit CI queue health — gh via: {gh_cmd!r}")
    for repo in repos:
        report_repo(repo, gh_cmd, args.limit, args.sample)
    print("\n(Remediation options: ci-hub/runners/README.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
