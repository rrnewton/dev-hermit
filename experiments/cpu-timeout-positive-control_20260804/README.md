# cpu_timeout positive control — no node false-kills at declared budgets

**Question.** The `cpu_timeout` declarations added to the CI DAG manifests
(PR rrnewton/hermit#1555, branch `ci/cpu-timeout-declarations-231b` @ `fe86e4a6`)
are a *runaway catcher*: generous by intent. The risk of a generous catcher is
nil; the risk of a *too-tight* catcher is a **false kill** — a healthy step
reaped mid-run. This artifact is the positive control that answers: **does any
declared budget sit at or below the real CPU-seconds a healthy run of that node
actually consumes?** (The negative control — that a genuine runaway *is* caught —
is `../cpu-timeout-enforcement-verified_20260803`.)

**Why this is the analog of the mem-cap denominator.** `dag-mem-caps` PR #1583
shipped a "10/10 caps sit above recorded peak" denominator so the caps were
demonstrably non-throttling. This is the same move for CPU-seconds: join every
node's *declared* `cpu_timeout` against the *measured* CPU-seconds of a real,
passing in-lane run, and show the budget is strictly above observed work.

## Method

- **Declared budgets:** read `cpu_timeout` (step-level) from `ci/dag/portable.json`
  and `ci/dag/privileged.json` on branch `ci/cpu-timeout-declarations-231b`
  @ `fe86e4a6`.
- **Measured CPU-seconds:** the runner-native per-node step-profile CSV from a
  full `validate` run at hermit `85626e18` on `devbig014` (2026-08-04). `cpu_s` is
  the runner cgroup `cpu.stat` `user_s + sys_s` for the step's own child cgroup —
  additive work, sibling-independent, contamination-proof under co-tenant load
  (see memory `safe-ci-dag-runner-boxing-cpuquota-not-cpuset`). `wall_s` is runner
  `elapsed_s`.
- **Join + verdict:** `headroom_ratio = cpu_timeout / cpu_s`; a passing node is a
  **false-kill risk** iff `cpu_s >= cpu_timeout`.

`results.csv` is the full per-node join (51 rows). Regenerate by re-joining the
05:11 in-lane table recorded in task
`enable-cgroups-and-cpu-timeouts-across-dag-nodes` against the branch manifests.

## Results

- **Portable: 42 of 42 passing nodes that carry a declared budget ran strictly
  below it — ZERO false-kills.** (`build.dbi_release`, the one intentional bare
  node, is excluded; it has no budget to violate.)
- **Privileged: 7 of 7 passing nodes ran below budget.**
- **Tightest headroom** (`cpu_timeout / real_cpu_s`, smaller = tighter):

  | node | real cpu_s | cpu_timeout | headroom |
  | --- | --- | --- | --- |
  | doc.doctests | 14.42 | 21 | 1.46× |
  | e2e.manifest_c_programs | 16.51 | 30 | 1.82× |
  | build.manifest_guests | 10.19 | 21 | 2.06× |
  | e2e.metadata | 7.92 | 18 | 2.27× |
  | e2e.manifest_determinism_stress_c | 8.19 | 30 | 3.66× |

  Even the tightest budget (`doc.doctests`, 1.46×) leaves ~46% headroom over a
  real passing run — consistent with a generous catcher, never a throttle.

- **The one non-pass node is `test.detcore_misc`** (599.99 CPU-s, wall-caught at
  600 s). Its budget is `810` (formula `max(30, ceil(90·3·3))`, **not** derived
  from the livelock), so `810 > 600 s wall > 599.99 livelock CPU-s`: on the
  one-core livelock the **wall** timeout fires first and `cpu_timeout` never
  fires. This is the deliberate exclusion — the budget is calibrated on the
  formula, not on a defect. (`reverie#355` landed at `79517704`; once hermit's pin
  bumps and detcore_misc is no longer livelocked, it becomes a real ≥5-sample
  measurement candidate for a tighter budget.)

## Interpretation

Every declared `cpu_timeout` is a true runaway catcher: at no node does a healthy
run come within its budget, so no node is at risk of a false kill from this
change. This is a single in-lane run (n=1) and therefore a **positive control**,
not a ≥5-sample override — it confirms non-throttling, it does not re-derive the
budgets.

## Reproduction

Measured CPU-seconds: run the portable + privileged DAGs boxed (default) via the
safe-ci-dag-runner **without** `--allow-cgroup-failure` and read the step-profile
CSV `cpu_s = user_s + sys_s`. Join against `cpu_timeout` in the two branch
manifests. `metadata.json` records the exact SHAs, host, and commands.
