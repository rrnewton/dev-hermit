# Chaos experimentation framework — parameter grid × seed sweep

**Task:** `chaos-experimentation-framework` (P1). Date: 2026-07-28.
Research-only. Extends the seeds-to-reproduce benchmark
(`experiments/btrfs_seeds_to_reproduce_benchmark_20260728/`).

**Question:** Which hermit chaos *parameters* minimize the seeds-to-reproduce
a scheduling-dependent interleaving? The prior benchmark fixed the strategy and
swept seeds; this adds the **outer parameter-sweep loop** — vary the scheduling
knobs (timeslice granularity, `--sched-heuristic`, `--sched-sticky-random-param`,
`--chaos-target-races`, `--fuzz-futexes`, `--max-timeslice`) and, for each
config, run an inner seed sweep, then find the parameters that reproduce a
distinct schedule in the fewest seeds.

## Knob surface

Per the source-verified `report-hermit-chaos-controls` audit (hermit main
`c1019c4ae7f109ec9fdad7a31781300bbbb25946`): default chaos = per-thread PRNG
streams + exponentially-distributed timeslice lengths + fresh persistent-queue
priorities at each timeslice boundary. There are **no** RR-style stable
per-thread slowdown factors and **no** scheduler epochs (those are future
features), so this framework sweeps the knobs that exist:

- `--target-timeslice <vns>` — cheaper *logical* (syscall-boundary) target
  slice length. No default; preferred over `--max-timeslice` for slice length.
- `--max-timeslice <vns>` — PMU-preemption cap (default 200000000).
- `--sched-heuristic {none,random,stickyrandom,connectbind}`.
- `--sched-sticky-random-param <p>` — global keep-current probability for
  `stickyrandom` (default 0.0).
- `--chaos-target-races` — bias toward lock-order / wakeup races.
- `--fuzz-futexes` — perturb futex wakeup order.

## Method

**Workload (oracle):** `btrfs-progs v7.1 rescue chunk-recover -y -v <dev>` on
fuzz image `bko-161811`. The scan worker races a progress-reporter thread over
whether the intermediate `Scanning: 0 in dev0` tick prints before `DONE`. The
per-run **signature** = sha256 of the hermit-filtered guest output; distinct
signatures = distinct observed interleavings. Benign race (identical recovery;
v7.1 hardened) → this measures **schedule-exploration power**, a proxy for
reproducing a would-be scheduling bug. See
[[btrfs-progs-chunk-recover-only-multithreaded-benign-race]].

**Grid:** 25 configs (`configs.tsv`), inner seed sweep `1..30` each = 750 runs.
Scratch device path **pinned** constant (interleaving is path-string sensitive).
SCOPED pgid kills only, via `run_scoped.sh` (`--timeout 300`).

**Metrics** (`analyze.py`, bootstrap 5000 shuffles):
- `distinct` — # distinct signatures over the 30 seeds (schedule diversity).
- `hit_rate` — fraction of seeds whose signature ≠ blind baseline (plain/seed1).
- `stf_*` — seeds-to-first-repro (first seed ≠ baseline), median / p90.
- `s2K` — seeds to discover K=min(5,distinct) distinct schedules.

## Results

`hermit` sha256 `30998d5aa7f05943` @ `c1019c4a`. Full rendered table in
`TABLE.txt`; machine-readable `summary.tsv`; raw per-run `results.tsv`.

| config | distinct | hit% | stf_med | stf_p90 |
|---|---|---|---|---|
| plain | 1 | 0% | never | never |
| strict | 1 | 0% | never | never |
| chaos_ts-default | 2 | 57% | 1 | 3 |
| chaos_ts-1e6 | 2 | 3% | 16 | 28 |
| chaos_ts-1e5 | 2 | 3% | 15 | 27 |
| **chaos_ts-1e4** | **29** | **100%** | **1** | **1** |
| **chaos_ts-1e3** | **30** | **100%** | **1** | **1** |
| **chaos_ts-1e2** | **30** | **100%** | **1** | **1** |
| ctr_ts-default | 2 | 33% | 2 | 6 |
| ctr_ts-1e6 | 2 | 3% | 16 | 27 |
| ctr_ts-1e5 | 1 | 0% | never | never |
| **ctr_ts-1e4** | **28** | **100%** | **1** | **1** |
| **ctr_ts-1e3** | **30** | **100%** | **1** | **1** |
| **ctr_ts-1e2** | **30** | **100%** | **1** | **1** |
| chaos_maxts-1e6 | 2 | 7% | 9 | 21 |
| chaos_maxts-1e5 | 2 | 3% | 16 | 27 |
| chaos_heur-none | 2 | 57% | 1 | 3 |
| chaos_heur-random | 2 | 27% | 3 | 7 |
| chaos_heur-sticky0.1 | 2 | 17% | 4 | 11 |
| chaos_heur-sticky0.5 | 2 | 17% | 4 | 11 |
| chaos_heur-sticky0.9 | 2 | 3% | 16 | 28 |
| chaos_heur-connectbind | 2 | 57% | 1 | 3 |
| chaos_futex-on | 2 | 57% | 1 | 3 |
| **chaos_futex-on_ts-1e3** | **30** | **100%** | **1** | **1** |
| **chaos_heurrandom_ts-1e3** | **30** | **100%** | **1** | **1** |

## Interpretation (headline)

- **`--target-timeslice` is the single dominant knob.** There is a sharp
  threshold between `1e5` and `1e4` vns: at the default or any slice ≥ 1e5 the
  workload has only **two** reachable interleavings (a binary regime — you
  either observe the alternate schedule or you don't, median 15–16 seeds to
  first find it, or never). At slice **≤ 1e4** the space explodes to **~fully
  distinct per seed** (28–30/30 distinct, 100% hit-rate, first seed always
  hits). Finer logical timeslices insert far more preemption points, so each
  guest-RNG seed lands on a different interleaving.
- **`--max-timeslice` does not substitute.** `chaos_maxts-1e6/1e5` stay in the
  binary regime (2 distinct, 3–7% hit) *and* small max values are pathologically
  slow (`--max-timeslice 10000` ≈ 14 s/seed vs ≈ 0.2 s at default, because the
  PMU cap fires a real preemption on every tiny slice; that config was killed).
  `--target-timeslice` is both the **effective** and the **cheap** slice knob —
  it targets syscall boundaries rather than forcing PMU interrupts.
- **Heuristic / sticky / futex knobs only re-weight within a fixed regime.** At
  the default (coarse) timeslice, `none` / `connectbind` / `fuzz-futexes` /
  plain chaos all sit at the best binary-regime hit-rate (~57%), `random` at
  27%, and **`stickyrandom` actively hurts as its param rises** (0.1/0.5 → 17%,
  0.9 → 3%): sticking to the current thread suppresses the preemption that
  produces the alternate schedule. None of them escape the 2-state binary — only
  cutting the timeslice does. Combining a heuristic with a fine slice
  (`chaos_heurrandom_ts-1e3`, `chaos_futex-on_ts-1e3`) inherits the fine-slice
  full diversity, confirming the timeslice dominates the heuristic.
- **`--chaos-target-races` tracks plain chaos here** (it biases toward
  lock-order/wakeup races; this is a benign progress-tick race, so no edge), and
  is likewise gated by the timeslice: `ctr_ts-1e4/1e3/1e2` reach full diversity,
  coarser `ctr` does not.

**Best parameters for minimizing seeds-to-reproduce a scheduling interleaving
on this workload:** `--chaos --target-timeslice 1000` (or `100`). stf median 1,
p90 1, 30/30 distinct schedules. Equivalently `1e4` is already at the knee.

### Honest caveats

- The race is **benign** (v7.1 hardened; only the progress-tick differs, no
  crash), so the metric is interleaving-**discovery** power, a proxy for
  reproducing a would-be scheduling bug — not seeds-to-crash. An unhardened
  multithreaded crasher would let the same harness measure seeds-to-crash
  directly.
- Absolute hit-rates in the binary regime are sensitive to the pinned
  device-path string and the exact slice value (both perturb chaos decisions);
  the **robust, regime-level** findings are (a) fixed-schedule (plain/strict) =
  0%, (b) coarse-timeslice chaos = 2-state binary, (c) fine-timeslice chaos
  (≤1e4) = full per-seed diversity, (d) sticky-random monotonically suppresses
  diversity. The exact binary-regime ordering among heuristics is not a stable
  ranking.
- The `mean_scan` proxy column is constant (1.0) because chunk-recover emits its
  progress on one line regardless of interleaving; diversity is captured by the
  signature, not the line count. It is retained only as a preemption-density
  smoke check.

## Reproduce

```bash
cd ~/work/dev-hermit
# inputs: hermit release binary; ignored/btrfs-progs-v7.1-bin/btrfs.box.static;
# ignored/bench-seeds/bko-161811.raw (decompressed fuzz image).
bash experiments/chaos_experimentation_framework_20260728/sweep.sh     # full grid -> results.tsv
# or resume/extend without redoing completed configs:
bash experiments/chaos_experimentation_framework_20260728/resume.sh
python3 experiments/chaos_experimentation_framework_20260728/analyze.py # -> summary.tsv + TABLE.txt
```

Files: `configs.tsv` (the 25-config grid), `sweep.sh` (full grid×seed sweep),
`resume.sh` (idempotent resume — runs only configs absent from results.tsv),
`analyze.py` (bootstrap stats + rankings), `results.tsv` (raw per-run
signatures, 750 rows), `summary.tsv` (machine-readable per-config),
`TABLE.txt` (rendered), `metadata.json` (provenance), `outputs/` (one
representative transcript per config).
