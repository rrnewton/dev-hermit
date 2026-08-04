# Skid margin under load — measurement (2026-08-03)

**Question (owner):** "What is our SKID MARGIN now?" — measured, as a distribution
under varying load, NOT read from the code.

**Skid** = retired-conditional-branch (RCB) *overshoot*: how many extra RCBs the
guest retires between the PMU counter hitting its programmed target and the
tracer actually stopping it. This is the quantity the RBC-fallback preemption
path must tolerate with its `skid_margin` (a fixed constant, believed 1000 RCB).

## Method
- Instrument: `hermit/tests/util/pmu_skid.c` (unmodified) → `/tmp/pmu_skid_h250`.
  Arms a raw RCB PMU counter to overflow at `--period` branches, delivers a
  signal, reads a second free-running RCB counter at the stop; sample =
  `observed - period`. Reports min/max/mean/p99 over `--iterations`.
- `sweep.sh` runs it at `--period 100000 --iterations 1000`, pinned to cpu 8,
  3 reps per load level; load raised with `burner.c` (branch-dense CPU spinners),
  self-cleaning. Levels: baseline, 0.5×, 1×, 2× cores.
- Box: AMD EPYC 9D85, family 0x1a, **316 cores**, `precise_ip=0` (no PEBS →
  larger skid than an Intel PEBS host), `perf_event_paranoid=1`.

## Results (see results.csv; raw in ignored/)
| level | loadavg | skid max (3 reps) | skid p99 |
|---|---|---|---|
| baseline | ~67 | 33500 / 1784 / 1958 | 585 / 872 / 810 |
| 0.5× | ~98 | 977 / 1030 / 567 | 204 / 214 / 212 |
| 1× | ~147 | 1758 / 930 / 1556 | 164 / 183 / 177 |
| 2× | ~204 | 384 / 421 / **15244** | 128 / 157 / 128 |

## Findings
1. **Heavy-tailed with rare catastrophic outliers** (max 15244, 33500 RCB) that
   appear at *every* load level, including near-idle. **No fixed margin can
   cover the tail** — the worst outlier (33500) occurred at BASELINE.
2. **p99 skid is always < 1000** (128–872): >99% of preemptions land within the
   believed 1000 margin; exceedances are the top <1%.
3. **Mean/p99 do NOT rise with load** (mean 173→60→54→58). Skid is a
   heavy-tailed random variable, not a smoothly load-degrading average. This
   reframes the premise: the risk metric is the **tail exceedance rate**
   (fraction of preemptions with skid > margin), not a load-dependent mean.

## Implication
`skid_margin = 1000` is an underived conservative constant that the tail exceeds
occasionally regardless of load. A bigger fixed margin cannot make the tail
safe. The correct mitigation is to **detect overshoot-beyond-target and retry**
— which requires a positive skid signature (actual RCB > intended target). The
`pmu_skid.c` clock-counter read proves that overshoot IS observable in principle;
whether hermit's RBC path exposes it at runtime is the design-deciding question.

## Limitation
`pmu_skid.c` reports only min/max/mean/p99, no full histogram. To compute the
true exceedance-rate-over-margin, patch it to dump all samples.
