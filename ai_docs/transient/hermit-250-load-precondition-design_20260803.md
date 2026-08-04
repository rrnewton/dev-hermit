# Design: "host too loaded to measure" precondition for PMU/determinism tests (hermit-250)

Status: DESIGN-ONLY (owner chose design-first on 2026-08-03; do not implement
until confirmed). Author: impl agent, opus-4.8. Task:
`tickhub-auto-invoke-ci-hub-health` Phase-2 mitigation lever (2).

## Problem

Hermit's determinism tests measure **retired conditional branches (RCBs)** from
the hardware PMU to derive virtual time and preemption points. That counter is
**load-sensitive**: under host CPU contention the RCB count *skids* (over/under
the target), which produces three distinct bad outcomes, none of them a real
product defect:

1. **False nondeterminism reds** — a `--verify` run diverges only because the two
   inner runs saw different skid, not because the guest is nondeterministic.
   (See memory `load-dependent-timeslice-skid-pmu-counter`,
   `load-independence-guardrail`.)
2. **Panic-then-hang** — the supervisor panics on "perf counter exceeds target"
   and the run wedges rather than exiting (memory `pmu-skid-panic-supervisor-hang`;
   exact source site to be reconfirmed at implementation time — the historical
   `timer.rs` line did not match a grep on 2026-08-03, so the string/location
   must be re-located, not assumed).
3. **Degraded numbers presented as measurements** — timing-sensitive tests emit
   plausible-but-wrong figures under load.

The cost is not just individual flakes: these infrastructure-caused reds land in
the **green-time integral** (now surfaced first-class in the auto-invoked health
check, parent `8b8d113`) and get mis-attributed as Hermit determinism bugs. The
`flaky-failure-attribution-capability` work (parent `b0239f5`) is the *post-hoc*
classifier for this; the load precondition is its *preventive* counterpart —
refuse to produce the bad datapoint in the first place.

## Goal and non-goals

**Goal:** before a PMU/determinism-sensitive test runs, check host contention and,
if too high, **refuse to measure** with a result that is VISIBLE and DISTINCT
from both pass and product-failure — "host too loaded to measure", not "test
failed" and not "test passed".

**Non-goals / hard line:** this must NOT change what "deterministic" means, relax
any assertion, or fake-green anything. It only decides *whether* to run the
measurement, never *weakens* it. This is the same discipline as the fetch-outcome
classification just shipped in queue_health (ci-hub-broken vs GitHub-slow): an
untrustworthy datapoint is refused and labelled, not silently downgraded. See
`validate-orchestrator-discipline`.

## The load signal (grounded finding)

Raw 1-minute loadavg is the WRONG signal on the hosts we run on. Measured on the
devserver 2026-08-03:

```
/proc/loadavg      -> 122.95 122.02 117.37   (looks catastrophic)
nproc              -> 316
/proc/pressure/cpu -> some avg10=2.00 avg60=2.01 avg300=1.92   (~2% stall)
```

loadavg 122 on a 316-core box is ~39% — not remotely saturated — while PSI
reports only ~2% of time stalled on CPU. A loadavg threshold would refuse to run
almost always here and never fire on a small CI runner. **Use a contention-
normalized signal:**

- **Primary: PSI `/proc/pressure/cpu` `some avg10`** — the fraction of recent
  wall time at least one runnable task was stalled waiting for CPU. Directly
  measures contention, core-count-independent, and it is ALREADY captured in the
  attribution bundle (`host_before/after` PSI), so thresholds can be calibrated
  from real evidence.
- **Fallback where PSI is absent: `nr_running / nproc`** from `/proc/loadavg`
  field 4 (`96/56657` -> 96 runnable) divided by `nproc`, or 1-min loadavg /
  nproc. PSI has been available on our kernels; keep the fallback for portability.

The PMU serial lane is single-runner, so the relevant contention is *other work
on the same host*, which PSI captures precisely.

## Behavior: refuse, distinct code, optional bounded wait

- On "too loaded", **refuse** and exit with a DISTINCT code (proposal: reuse the
  existing convention where 125 = killed-by-budget/wall vs 124 = wall-timeout;
  pick an unused distinct code, e.g. 123, for "host-too-loaded / not-measured"),
  plus a one-line marker on stderr: `HOST-TOO-LOADED-TO-MEASURE psi_some_avg10=<x> threshold=<t>`.
- CI classifies that code as **not-measured** — neither pass nor fail — exactly
  the third bucket the fetch-classification pattern established.
- Optional: a **bounded wait-for-quiescence** (poll PSI for up to N seconds; run
  if it drops below threshold, else refuse). Keep N small and log the wait.

## Where it lives (recommended MVP → later)

1. **MVP: harness-level gate** (least invasive, zero determinism-semantics
   change). A small precondition in the test harness (`validate.sh` / the nextest
   wrapper) checks PSI before the PMU/`hardware` job and refuses the job with the
   distinct code if over threshold. This is a hermit PR but touches only test
   orchestration, not detcore.
2. **Later / opt-in: in-hermit precondition.** A `--require-quiet-host[=THRESH]`
   flag / `HERMIT_REQUIRE_QUIET_HOST` env on `run` that aborts early with the
   distinct code when `--verify` or RCB-timing is active and PSI is over
   threshold. Useful for direct invocations outside the harness. Default OFF so
   normal `run` is unchanged.

## Interaction with green-time (important)

Non-measured (host-too-loaded) runs must NOT count as red in the green-time
denominator — otherwise refusing to measure would itself tank the very metric we
just made first-class. Options: exclude the distinct not-measured conclusion from
`green_time()`'s authoritative timeline, or track it as a separate "not-measured
hours" bucket. Recommend excluding from the green/total ratio AND reporting
not-measured-hours alongside, so a host that is chronically too loaded is visible
(that is itself a capacity signal) without being scored as product red.

## Calibration plan (evidence-driven, not a guessed constant)

Do not hardcode a threshold by intuition. Use the attribution capture bundles
(`ci-hub/attribution/`, host_before/after PSI already recorded) plus a controlled
sweep: run a known-clean determinism test under increasing synthetic load
(the calibrated matched-burst harness, memory `nightly-stress-harness`) and find
the PSI `some avg10` at which skid/false-red/panic first appears. Set the
threshold below that knee with margin. Record the sweep as an experiment under
`experiments/`.

## Open questions for the owner (confirm before implementation)

1. **Threshold + signal**: OK with PSI `some avg10` primary (nr_running/nproc
   fallback), threshold set by the calibration sweep rather than a guessed value?
2. **Refuse vs bounded-wait**: refuse immediately, or poll-for-quiescence up to a
   small N seconds first?
3. **Placement**: harness-level MVP first, in-hermit `--require-quiet-host` later?
4. **Green-time treatment**: exclude not-measured from the ratio and report
   not-measured-hours separately (recommended)?
5. **Distinct exit code**: 123 (or another unused code) for not-measured — any
   collision with existing conventions to avoid?

## Related

- `flaky-failure-attribution-capability` (b0239f5) — post-hoc classifier; this is
  the preventive counterpart.
- `load-dependent-timeslice-skid-pmu-counter`, `pmu-skid-panic-supervisor-hang`,
  `load-independence-guardrail`, `nightly-stress-harness`.
- Green-time surfacing: parent `8b8d113`.
