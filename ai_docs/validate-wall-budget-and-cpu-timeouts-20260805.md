# The 600s validate wall budget: median, load conditions, and per-node CPU budgets

**Task:** `validate-wall-budget-600s-the-median-passes-the-tail-does-not` (P0, owner)
**Date:** 2026-08-05
**Scope:** local analysis over existing run data. **No new validate run, no egress, no
product change.**

---

## 0. Framing note — the dispatch and the task disagree, and the task is right

My dispatch framed this as *"the tail exceeds the budget — a load-dependent timeout that
fires on a busy box"*. The task description explicitly retracts that framing:

> *"THIS CORRECTS MY EARLIER FRAMING … 'eliminate a 3.8x spread on identical work' —
> WRONG TARGET: the spread comes from a shared dev box with other work on it, and
> variance-chasing on a variable-load machine is optimising noise."*
> *"THE TARGET IS THE MEDIAN, AND THE DIRECTION IS ALWAYS-DOWN."*

I follow the task. **But the two asks are not in conflict**, and it is worth being precise
about why, because the reconciliation is the useful part:

- *Don't chase the tail* is about **what to optimise**: making the work faster, measured
  at the median. Correct — narrowing variance on a shared box is optimising the neighbours.
- *CPU-time budgets* is about **what to kill on**. Precisely because variance is expected
  and legitimate, **wall is the wrong kill criterion**. A CPU budget is load-immune, so it
  can be tight without firing on a busy box.

Optimise the median on wall; enforce timeouts on CPU. Both sections below.

---

## 1. The number: median wall against the 600s line

Source: `ignored/validate-run-ledger.jsonl`, 585 rows. Cohort: full-profile rows with a
positive `real_seconds` whose `flake_class.effective_result` is `pass` — 154 of 359
full-profile rows. (Using effective, not raw, result: raw `pass` includes non-full-coverage
runs that were downgraded.)

| metric | value |
| --- | --- |
| **MEDIAN wall** | **528 s** — inside the 600 s budget, 12% headroom |
| p90 wall | 816 s *(context, not a target)* |
| max wall | 1581 s *(context)* |
| min wall | 50 s |
| median CPU (user+sys) | 1378 s |
| **median parallelism** | **2.6 cores** (1378/528) |

The 528 s reproduces the owner's stated figure independently, from the ledger, at n=154.

---

## 2. Load conditions — and the finding that sharpens the owner's framing

The owner asked (deliverable 2) that every figure state load conditions. Concurrency here
is derived by interval overlap: for each run, the number of full-profile validate runs
whose `[started_at, finished_at)` overlaps it (self included).

| concurrent full validates | n | median wall |
| --- | --- | --- |
| 1 (quiet box) | 44 | **414 s** |
| 2 | 51 | 520 s |
| 3 | 16 | 687 s |
| 4 | 19 | 512 s |
| 5 | 6 | 568 s |
| 6+ | 18 | **743 s** |

> **The median is not one number — it moves 414 s → 743 s with fleet concurrency, a
> 1.79× inflation. At concurrency ≥6 the MEDIAN itself breaches the 600 s budget.**

This is the correction the owner's own deliverable (2) was reaching for. The accurate
statement is not *"median passes, tail doesn't"* — it is **"the median passes when the box
is quiet and fails when the box is busy."** The 528 s headline is a blend over a load
distribution that is itself not stable, so quoting it without the load column is quoting a
number whose denominator moves.

Note the non-monotonicity at 3 (687 s) and 4 (512 s): n=16 and n=19 are small, and
concurrency is not the only covariate (cache state, node mix). Treat the 1→6+ endpoints as
the signal and the middle as noise.

---

## 3. Wall vs CPU per node — why one uniform wall budget cannot be right

Source: `ci-hub/history/query.py node-cpu-budgets` (the canonical deriver), 130 DAG nodes.
**76 (58%) are "thin"** — fewer than 5 samples, no budget derivable. 54 are usable.

Across the 54 usable nodes the CPU-to-wall ratio spans **1774×**:

| | cpu/wall | interpretation |
| --- | --- | --- |
| min | 0.0088 (`build.flaky_harnesses`) | ~1% CPU-utilised: almost pure waiting |
| p50 | 0.59 | |
| max | 15.57 (`build.runtime_release`) | 15.6-way parallel fan-out |

Two distinct families, which need opposite treatment:

**Wall-dominated — mostly waiting; a wall budget here measures the box, not the work.**

| node | max_cpu | max_wall | CPU util |
| --- | --- | --- | --- |
| `build.flaky_harnesses` | 0.86 s | 97.99 s | 0.9% |
| `e2e.manifest_bin_c` | 12.24 s | 497.05 s | 2.5% |
| `doc.doctests` | 12.70 s | 284.32 s | 4.5% |
| `e2e.manifest_determinism_stress` | 25.68 s | 506.53 s | 5.1% |

**CPU-dominated — parallel fan-out; aggregate subtree CPU far exceeds wall.**

| node | max_cpu | max_wall | ratio |
| --- | --- | --- | --- |
| `build.runtime_release` | 2222.27 s | 142.74 s | 15.6× |
| `build.privileged_tests` | 809.33 s | 106.66 s | 7.6× |
| `test.cli` | 618.01 s | 87.68 s | 7.1× |
| `build.workspace` | 1174.95 s | 257.46 s | 4.6× |

The CPU-dominated family is why a per-process `RLIMIT_CPU` would be wrong and the
aggregate-subtree cgroup mechanism is right — a 15.6× fan-out is invisible to per-process
accounting.

---

## 4. Which nodes a load spike would kill spuriously

Applying the measured 1.79× load inflation (§2) to each node's observed `max_wall` and
comparing against its configured wall timeout:

| node | wall timeout | max_wall | max_cpu | CPU util | ×1.79 | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `e2e.manifest_bin_c` | 600 | 497.1 | 12.2 | 2.5% | 890 | **breaches** |
| `e2e.manifest_determinism_stress` | 600 | 506.5 | 25.7 | 5.1% | 907 | **breaches** |
| `e2e.manifest_language_runtimes` | 600 | 532.8 | 107.1 | 20.1% | 954 | **breaches** |
| `e2e.manifest_system_utils` | 600 | 518.5 | 41.0 | 7.9% | 928 | **breaches** |
| `test.rr_suite_contract` | 300 | 239.1 | 602.3 | — | 428 | **breaches** |
| `build.manifest_guests` | 120 | 266.8 | 18.6 | 7.0% | 477 | **breaches** |
| `e2e.manifest_applications` | 130 | 183.2 | 13.4 | 7.3% | 328 | **breaches** |
| `e2e.manifest_backend_parity_c` | 120 | 287.0 | 17.0 | 5.9% | 514 | **breaches** |
| `build.privileged_tests` | 120 | 106.7 | 809.3 | — | 191 | **breaches** |
| `doc.rustdoc` | 900 | 468.3 | 720.1 | — | 838 | near |
| `test.regular_crates` | 900 | 502.2 | 45.1 | 9.0% | 899 | near |

The four portable `e2e.manifest_*` nodes are the exact failure mode: **2.5–20% CPU
utilised, and a busy box pushes them past a 600 s wall.** Killing them reports a timeout
where nothing hung.

**Separate anomaly worth a look:** `build.manifest_guests` (266.8 s), `e2e.manifest_applications`
(183.2 s) and `e2e.manifest_backend_parity_c` (287.0 s) have an observed `max_wall`
**already above their configured wall timeout** (120/130/120 s). Three readings: the
samples predate a tightening (the privileged walls were recently changed — a 20 s
`e2e.metadata` wall was loosened to 120 s around #1620), the timeout is not enforced on
that lane, or the max includes a killed run's wall. The discriminator is whether those
rows carry `timed_out=True`; I did not resolve it.

---

## 5. CPU budgets and the platform multiplier — already built, and wired to nothing

**The deliverable my dispatch asked for already exists.** `ci-hub/history/query.py
node-cpu-budgets` emits per node: `n_samples`, `n_excluded_kill`, `max_cpu_s`, `p95_cpu_s`,
`p50_cpu_s`, `max_wall_s`, `suggested_cpu_timeout` = `round(max_cpu_s × 1.5)`, and
`suggested_cpu_timeout_hosted` = `× 2.0`. Kill samples are excluded so a truncated run
cannot depress a budget. Thin nodes emit no budget rather than a guessed one.

**Adoption is zero.** Measured on hermit `main`:

| manifest | steps | with `timeout` (wall) | with `cpu_timeout` |
| --- | --- | --- | --- |
| `ci/dag/portable.json` | 47 | 47 (default 600) | **0** |
| `ci/dag/privileged.json` | 8 | 8 (default 120) | **0** |

So: budgets are derivable for 54 nodes, the runner enforces `cpu_timeout` when set, and
**not one node sets it**. CPU-timeout enforcement on hermit CI is currently inert for lack
of a value, not for lack of a mechanism. (#1620 proposed exactly this for 3 nodes; it is
not on `main`, and with egress down I could not check whether it is still open.)

### On the ×2.0 hosted multiplier — endorse it, and do not try to "measure" it

The constant is already honestly documented in `query.py:331-346`: hosted `step_profiles`
carry **wall only** (`user_s`/`sys_s` empty — the GitHub producer emits no CPU-seconds), so
no hosted CPU distribution exists; deriving a multiplier from hosted-wall ÷ local-CPU would
pair two different quantities, which is the proxy-binding error the store exists to avoid.
×2.0 is chosen as the safer of a ~1.5–2 range, erring toward *not* killing a healthy-but-slow
hosted run.

I tried to bound it empirically with a legitimate same-quantity comparison (hosted wall vs
local wall, paired per node). **It is not checkable from this store:** the hosted side holds
**34 rows across 28 files — roughly one sample per node**, and no node has ≥3 hosted
samples against ≥5 local ones. The local side is healthy (4 606 rows, 53 nodes, from
`worktrees/*/hermit/.safe-ci-dag-runner/profiles/`).

An early attempt of mine produced ratios of 43×–361×; those are **artifacts** of dividing
by a ~0.02 s elapsed from a single aborted local run and must not be quoted. Reporting them
would have been exactly the kind of unqualified number this project keeps getting burned by.

**To make the multiplier measurable, two things are needed, in order:** hosted CPU-second
emission in the GitHub producer, and enough hosted samples per node (≥5) to derive a
distribution. Until then ×2.0 stands as a declared, conservative, unmeasured constant —
which is the right way to hold it.

---

## 6. Proposal

**A. Adopt the derived CPU budgets across the 54 usable nodes, not 3.** Values verbatim
from the deriver: `suggested_cpu_timeout` on `privileged.json`, `suggested_cpu_timeout_hosted`
on `portable.json`. Thin nodes get none. This is config, not code — the enforcement
mechanism already exists.

**B. Decouple wall from CPU per family.** For the wall-dominated family (CPU util <10%),
wall is a liveness backstop for an *idle*-stuck node and should stay generous — raise the
four portable `e2e.manifest_*` nodes above their 1.79×-inflated observed max (≥960 s) so a
busy box cannot kill them, and let the tight CPU budget do the real work. For the
CPU-dominated family the CPU budget is the primary control and wall can stay as-is.

**C. Ratchet the median on the QUIET-BOX cohort, not the blend.** The owner wants a
regression to alarm *at the commit that caused it* (deliverable 3). A blended median moves
1.79× with fleet load, so it would alarm on the neighbours' work and stay silent on a real
regression landed during a quiet hour. Ratchet the **concurrency==1 median** (today 414 s,
n=44), report the blended median and the concurrency histogram as context, and require a
minimum cohort size before the alarm is allowed to fire. Concurrency is derivable from the
ledger by interval overlap, so this needs no new instrumentation.

**D. Do not treat p90/max as targets.** Per the owner. They belong in the report as context
so a reader can see the load distribution, and nowhere in the gate.

**E. Verify bar for any of the above.** A budget change is a kill-criterion change: bracket
both directions. A planted CPU-burning hang must be killed by the CPU budget within its
budget +1 sampling interval; a planted merely-slow-but-healthy run under artificial load
must **not** be killed. State both counts. A budget that has only ever been observed not
firing is unbracketed.

---

## 7. Limitations

- **No new validate run.** Everything is read from the existing ledger (585 rows) and the
  history store; nothing here was produced by running the thing being measured.
- **Concurrency is derived, not recorded.** Interval overlap over full-profile ledger rows
  is a proxy for machine load: it counts sibling validates, not the other ~18 agents'
  builds, so it understates true load. A `load1`/PSI reading at run start would be better
  and the profile schema already carries those columns for DAG steps (not for ledger rows).
- The concurrency cohorts at 3, 4 and 5 have n=16/19/6. Only the 1 vs 6+ endpoints are
  worth leaning on.
- `max_wall_s` from the deriver may include killed runs (kill exclusion is applied to the
  CPU columns); the §4 anomaly is unresolved for that reason.
- The 1.79× inflation is a whole-run median ratio applied per node in §4. Nodes will not
  inflate uniformly — a CPU-bound node inflates less than a wait-bound one — so §4 is a
  screening estimate, not a per-node prediction.
- 58% of nodes are thin. Adoption per (A) covers 42% of nodes; the rest need samples first.
- I could not check #1620's live state (egress down).
