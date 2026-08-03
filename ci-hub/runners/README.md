# Hermit CI runner / status tooling

On-demand visibility into the Hermit repos' GitHub Actions state, sized to
Hermit's reality: Hermit does **not** run a container fleet - it uses a single,
permanently-installed PMU self-hosted runner per repo. This directory currently
provides a **non-mutating status reporter**, not fleet provisioning.

> Scope note: Fleet provisioning, a CI shepherd/reconciler, and a runner
> container image are deliberately out of scope. Hermit's runners are host-local
> PMU machines (they need `perf` RCB counters), so cloud-fleet scale-out does not
> apply. Add those capabilities only if Hermit moves to on-demand runners.

## Quick start

```sh
cd ~/work/dev-hermit/ci-hub/runners
./ci-status.py                 # rrnewton/hermit (default)
./ci-status.py --all           # all three Hermit repos
./ci-status.py --repo rrnewton/reverie --limit 60
```

`gh` is invoked through `$GH` (default `with-proxy gh`) so it works behind the
devserver proxy without changing the machine-global `gh` account. Override with
`GH='gh' ./ci-status.py` if you are already off-proxy/authed.

The report shows, per repo (analysis lives in the importable `queue_health.py`;
`ci-status.py` is the stable entrypoint that renders it):

- **runner health** — count + per-runner status/busy/labels, with a `pmu` /
  `pmu-serial` breakdown; flags when there is no idle runner.
- **queue depth, per workflow** — queued vs running counts split out for each
  workflow (not one aggregate number), plus the **current queue age**
  (`now − createdAt`) median/max, an honest *lower bound* on each run's final
  wait.
- **binding constraint** — a live verdict derived from runner idle counts and
  queued depth, e.g. "self-hosted PMU lane saturated: 0 idle of 3 pmu runners …
  single serial lane pmu-serial=<name> is BUSY".
- **time since last green, per workflow** — both the elapsed wall time and **how
  many runs back** the last success was, so a green buried pages deep surfaces
  here instead of forcing a manual page-through.
- **time-in-queue vs run duration** — a historical distribution from the jobs
  API over a bounded sample of recent completed runs, keeping wait
  (`job.started_at − job.created_at`, the runner-pickup latency) **strictly
  separate** from run duration. Run-level `startedAt` equals `createdAt`, so it
  cannot supply this; the jobs API is the only faithful source. Control the
  sample with `--sample N` (`0` disables it; the current-queue-age above is the
  free lower-bound signal).

### Ops-tick integration

`queue_health` is wired into the operational tick as the `ci_queue_health`
reminder (`ci-hub/health/tick-hub.yaml`, cadence 900s → gate
`operational_health.py queue-health`). The gate is **fail-loud**: it returns
non-zero (hard-warning the coordinator) when any workflow's queued depth reaches
`CI_QUEUE_DEPTH_WARN` (10), the max current queue age reaches
`CI_QUEUE_AGE_WARN_SECS` (1800), a workflow with at least
`CI_GREEN_GATE_MIN_RUNS` (4) runs in the window has no green within
`CI_GREEN_RUNS_BACK_WARN` (15) runs / `CI_GREEN_AGE_WARN_SECS` (6h), or the
self-hosted lane is the active binding constraint. All thresholds are
environment-overridable; `CI_GREEN_GATE_EXCLUDE` (comma-separated substrings,
default empty) silences the no-green gate for a structurally non-green workflow
without a code change. The gate does no jobs-API sampling, so a tick stays cheap.

Standalone: `./ci-status.py --gate` (or `--gate --all`) emits the same
`key=value` fields.

### Relationship to prior "CI stats" tooling

A sibling project surfaced comparable self-hosted + GitHub CI reporting through a
Makefile `stats` target. That target was **not** ported here — `runners/Makefile`
carries only the single-runner lifecycle targets (`build`/`init`/`start`/
`verify`/`stop`/`drain`/`status`). What existed before this change was a partial
reimplementation (`ci-status.py`: runner counts + one aggregate in-flight number
+ last-green), off the tick and missing per-workflow depth, wait-time
distribution, runs-back, and the binding-constraint verdict. This directory now
ports the **capability** (the four items above) into ci-hub and wires it to the
tick, rather than porting the target.

## The Hermit CI situation (2026-07-24)

This is a **capacity** problem, not a broken-tests problem.

- The `pmu` label is required because the determinism suite reads hardware
  retired-branch counters, so these jobs **cannot fall back to GitHub-hosted
  runners**. (Update — basis: `gh api .../actions/runners`, 2026-08-03:
  `rrnewton/hermit` now has **three** PMU runners `hermit-ci-newton{,-2,-3}` plus
  a gate-only `hermit-gate-newton`; exactly one, `hermit-ci-newton`, carries
  `pmu-serial` — the single serial lane. `runner-health` reports the live count,
  so prefer it over this prose.)
- **reverie** drains fine: its Rust job runs ~2–3 min, so one runner stays idle
  and every push goes green quickly.
- **hermit** is jammed: the Rust ("Regular tests") job is much heavier and PR/push
  volume is high, so a single runner cannot keep up. Result: dozens of queued Rust
  runs (observed 23–59 in-flight), frequent supersession cancellations, and **zero
  green Rust runs**. The GitHub-hosted **Docs** workflow stays green and is the
  practical hosted gate.
- **facebookexperimental/hermit** (the fbcode-sync mirror) has a high Rust failure
  rate (~50% of recent runs) from fbcode/folly-fmt sync breakage — a separate
  issue from the rrnewton runner bottleneck.

### Landing discipline

Because self-hosted Rust CI cannot reliably go green here, changes land under the
**post-facto-review** discipline: run the affected checks locally, prove any
residual failure is baseline/environmental, apply the **`locally-validated`**
label, and merge on GitHub-hosted (Docs) green. Merged PRs should carry
`locally-validated` + `post-facto-review`. See
`hermit/.llms/skills/post-facto-review/SKILL.md`.

### Remediation options (for the human)

1. **Add PMU self-hosted runner(s)** for `rrnewton/hermit` — reverie proves one
   runner drains a light load; hermit's heavy load needs N>1.
2. **Split the Rust job** so non-PMU parts (build, clippy, fmt, unit tests) run on
   GitHub-hosted runners in parallel, leaving only the RCB/determinism tests on
   the PMU runner.
3. **Throttle redundant triggers** (cancel-in-progress per branch is already
   happening as supersession cancels; also consider path filters / fewer
   pull_request event types).
4. **Formally accept `locally-validated`** as the gate (current de-facto practice).

### Starting additional runner slots

```sh
# Hermit slots 2 and 3
make start-hermit SLOT=2 START_ARGS=--detach
make start-hermit SLOT=3 START_ARGS=--detach

# Reverie slot 2
make start-reverie SLOT=2 START_ARGS=--detach
```

## Files

- `ci-status.py` — self-contained, non-mutating status reporter (Python 3, stdlib
  only; shells out to `gh`).
