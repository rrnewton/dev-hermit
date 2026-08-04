# Per-step `-j` speedup model + memory model in safe-ci-dag-runner

**Task:** `per-step-parallel-speedup-study-and-j-model` (P0, owner).
**Question (owner):** sweep each DAG step at `-j1,2,4,8…316`, climb until wall
time worsens, back off to the last width before the dip, set that as the step's
max `-j` — PER STEP, not global. AND: "the built-in profiling should RECORD OUR
MEMORY USE at these different `-j` steps and BUILD A MODEL. IF IT DOESN'T, ADD
IT." Build steps expected ≥ `-j64`.

**Host:** devbig014, AMD EPYC 9D85 158-Core ×2 = 316 cores. Date 2026-08-03.
**Code:** `scratch/au-parallel-experiment/py` on agent-utils branch
`codex/dag-runner-core-allocator` (checkout SHA `22a401fe…`, from CSV rows).

## Headline answer

**The owner's per-step `-j` algorithm is ALREADY IMPLEMENTED, and memory IS
recorded per width. The one genuine gap: per-width memory is recorded in the raw
store but NOT surfaced on the speedup curve / plan output — THAT is the "ADD
IT."** No new climb/bisect/median machinery is needed; it exists and was verified
end-to-end.

### What already exists (measured, not inferred)

- **Per-step speedup curve with medians + knee.** `estimates.py:555
  _build_step_speedup` climbs widths ascending and advances the recommendation
  only while the marginal wall gain ≥ `_SPEEDUP_MIN_MARGINAL_GAIN` (1.15×) AND
  CPU-work-growth ≤ `_SPEEDUP_MAX_WORK_GROWTH` (1.5×) AND within the core budget,
  freezing at the first width that fails = the knee. Inputs are **MAD-trimmed
  robust medians** of wall / CPU-seconds / effective-cores over a per-`(step,
  inner_jobs)` reservoir (`SpeedupLevel.wall_s` docstring, `estimates.py:526`;
  `_robust_median` in `step_speedups_from_buckets`, `estimates.py:610`). This is
  exactly "climb until wall worsens, back off to the last good width," with
  medians, PER STEP. CSV and mergeable-summary paths both route through the same
  `_build_step_speedup` (`summary.py:443 step_speedups_from_summary`), so they
  agree by construction.
- **Memory recorded per width.** Each boxed run writes `peak_bytes` tagged with
  `inner_jobs` and `step` to `step_profiles_*.csv` (columns 11=`inner_jobs`,
  12=`elapsed_s`, 18=`peak_bytes`). Verified below.

### The gap (the surgical "ADD IT")

`SpeedupLevel` (`estimates.py:519`) carries wall / cpu / eff_cores / throttled —
**no memory field.** The memory model aggregates `peak_bytes` across ALL of a
step's widths into one high-percentile `rss_estimate_bytes` per step
(`estimates.py:458`, "does not distinguish" widths). So the runner cannot today
answer "how does THIS step's peak RSS grow from `-j1`→`-j316`" as a curve — the
raw data exists but the model flattens it. The CPA moldable allocator does honor
a `--max-mem` budget (`estimates.py:992` stops widening when `_cpa_footprint >
mem_budget`), but off the per-step high-water, not a per-width memory curve.

**Fix (scoped, ~1 file + parity):** add `peak_bytes` (robust median) to
`SpeedupLevel`; thread a `peaks` dict through `step_speedups_from_buckets`
alongside `walls/cpus/effs`; extend the `raw_levels` tuple and `_build_step_speedup`;
surface it in `_speedup_text_lines`/`_speedup_level_json`. Keep CSV↔summary
parity (both call `_build_step_speedup`). NOT done here — this touches agent-utils
code and the SERIALIZE rule holds one agent-utils change (PR#8) in flight; teed
up as a follow-up, not opened as a second change.

## Boxing is REQUIRED for memory (correction to an earlier note)

Run WITHOUT `--allow-cgroup-failure`: the runner re-execs into a systemd
transient scope (`safe-ci-NNNN.scope` under `safe-ci.slice`, CPUQuota 28440% ≈
90%×316, AGGREGATE across concurrent runs) → "cgroup boxing ACTIVE" and
`peak_bytes` populates. WITH `--allow-cgroup-failure` it falls back to UNBOXED
and `peak_bytes` is EMPTY / `rss_hwm` shows `-`. The earlier "memory not
captured / delegability trap" observation was an artifact of that flag; the
systemd transient-scope path sidesteps direct-delegation limits. **Always sweep
WITHOUT the flag so memory is recorded.**

## REAL BUILD STEP curve (clean DEBUG `cargo build -p hermit`) — the owner's ≥j64 question

Sweep of a real clean-debug build of `-p hermit` (default features), boxed, 3
passes, medians. Each run gets a fresh `mktemp` `CARGO_TARGET_DIR` so every width
is a commensurable clean build (no cache carry-over). Full data in
`results-real-build.csv`.

```
 j   wall_s  speedup(vs j1)  CPU-s(user+sys)  peak RSS
 1   198.63     1.00x            192.1         2.53 GiB
 2   102.43     1.94x            188.0         2.54 GiB
 4    63.05     3.15x            191.8         2.70 GiB
 8    42.57     4.67x            191.9         3.24 GiB
16    36.93     5.38x            201.6         3.23 GiB
32    34.98     5.68x            190.1         3.23 GiB
64    34.40     5.77x            194.7         3.28 GiB   <- WALL FLOOR (max-j FOR NOW)
128   34.90     5.69x            196.4         3.19 GiB   <- dip: wall rises
316   36.95     5.38x            191.5         3.29 GiB   <- dip continues
```

**Three findings, measured not inferred:**

1. **Wall-clock dip is at j64.** Wall bottoms at j64 (34.40 s, 5.77× over j1);
   j128 (34.90) and j316 (36.95) are slower. By the owner's "FOR NOW take the
   first thread setting before the dip" rule, **max-`j` for this step = j64.**
   This *meets* the owner's ≥j64 build expectation — it is NOT a below-j64
   finding. (In practice j32/j64/j128 are all ~34–35 s, a plateau within noise;
   the plateau *begins* ~j32. A CPU-thrift alternative — the owner's optional
   "almost as good with less wasted CPU" — is j16: 5.38× at 1/4 the cores, within
   7% of the j64 floor.)

2. **CPU-seconds are ~invariant across width: median 194.8, range 183.7–213.5
   CPU-s.** A clean debug build of `-p hermit` costs ~195 CPU-s of work no matter
   the `-j`. This is the input a `cpu_timeout` should be derived from (relayed to
   hermit-231b) — not the wall time, which collapses 6× from j1→j64.

3. **Memory model: peak RSS ≈ 2.5 GiB at j1–j2, rises to ~3.2–3.3 GiB by j8 and
   is FLAT thereafter (3.2–3.3 GiB, j8→j316).** It does not grow with width past
   j8 because concurrent `rustc` count saturates around the `-p hermit` crate-DAG
   width (~27 threads). **Peak ~3.3 GiB EXCEEDS the 2 GiB validate reserve** — an
   admission-control build node must be sized for ~3.3 GiB, not 2 GiB.

**Runner `plan` knee-pick vs the owner's "for now" rule (a policy gap to flag):**
`plan` returns `rec_inner_jobs=4` (its curve, over a partly-contaminated store:
`1:1.00 2:1.72 4:4.03 8:4.31 16:5.90 32:5.45 64:5.21 …`). That is the *CPU-thrifty
marginal-gain knee* (`_SPEEDUP_MIN_MARGINAL_GAIN` 1.15× stops at j4 because
j4→j8 median gain is <15% in its trimmed view). The owner's stated **"for now =
first setting before the dip" = j64**, which is *more* parallelism than the coded
knee returns. So the tool already implements the owner's OPTIONAL future thrift
policy, but not the "for now" wall-floor policy — a knob to reconcile before this
drives per-step max-`j` caps.

## First step's full curve (build.app, TOY simulated CPU+shell work)

Boxed, `sweep --step build.app --jobs 1..8`. Single-run live table (fastest) plus
the recorded per-width `peak_bytes`:

```
jobs  wall_s  peak_bytes  peak_MiB  speedup(live)
1     3.576   1191936     1.14      1.00x
2     1.722   1441792     1.38      2.08x
3     1.271   2215936     2.11      2.81x
4     1.073   2482176     2.37      3.33x
5     0.772   2928640     2.79      4.63x
6     0.669   3411968     3.25      5.35x
7     0.569   3907584     3.73      6.28x   <- fastest wall (knee)
8     0.689   4468736     4.26      5.19x   <- WALL WORSENS (the dip)
```

- **Wall dip at j8** (0.569→0.689 s): the algorithm's target. Marginal gain
  j7→j8 = 0.83× < 1.15 → stop; max `-j` for this step on this box = **j7**.
- **Memory model:** peak RSS ≈ linear, ~0.45 MiB per added worker (1.14→4.26 MiB
  over j1→j8). This is the per-`-j` memory curve the owner asked for — measured,
  from the existing recorder.
- **Runner's own knee-picker agrees** (`plan`, reads MAD-trimmed medians from the
  store): `build.app rec_inner_jobs=7, speedup@rec=5.96×, curve
  1:1.00 2:2.11 3:2.77 4:3.20 5:4.30 6:5.00 7:5.96 8:5.48`. Median 5.96× vs
  single-run 6.28× — the model correctly uses medians, not the fastest sample.

**Caveat — this is the TOY step**, not a real cargo build. It validates the
METHOD end-to-end (sweep→store→median knee-pick + per-width memory recording).
The `-j64` build expectation must be tested on the REAL hermit build step; that
sweep is expensive (clean-build wall dominates at low `-j`) and is the teed-up
next run.

## Reproduce

```bash
cd ~/work/dev-hermit/scratch/au-parallel-experiment/py
# WITHOUT --allow-cgroup-failure so boxing is active and peak_bytes populates:
env PYTHONPATH=$PWD python3 -m safe_ci_dag_runner sweep \
  --dag ../examples/06-step-sweep.json --step build.app --jobs 1..8
env PYTHONPATH=$PWD python3 -m safe_ci_dag_runner plan \
  --dag ../examples/06-step-sweep.json          # per-step rec_inner_jobs + curve
CSV=$(ls -t .safe-ci-dag-runner/profiles/step_profiles_*.csv | head -1)
awk -F, 'NR>1 && $9=="build.app" && $18!="" {print $11","$12","$18}' "$CSV"
```

## Next (for a successor)

1. Author a real-DAG step: `cargo build`/`test` in `hermit`, `jobs_flag "-j%d"`,
   each run comparable work (clean or fixed target) so widths are commensurable.
2. Sweep `-j 1,2,4,8,16,32,64,128,316` (feed geometric widths as repeated
   single-width sweeps; the store accumulates and `plan` fits the knee over all
   of them). Confirm build knee ≥ j64 — if it dips earlier, report the width +
   numbers (a FINDING, per the owner), do not silently accept.
3. Land the per-width-memory-on-curve "ADD IT" on the existing agent-utils branch
   once the SERIALIZE gate (PR#8) clears.
