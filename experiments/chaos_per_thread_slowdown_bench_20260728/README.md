# Benchmark — RR-style stable per-thread slowdown vs plain chaos

**Task:** `chaos-feature-per-thread-slowdown` (P1). Date: 2026-07-28.
Feature-benchmark for the new `--chaos-per-thread-slowdown` /
`--chaos-slowdown-max-factor` flags. Extends the framework sweep
(`experiments/chaos_experimentation_framework_20260728/`).

## The feature under test

Default hermit chaos redraws a fresh scheduling priority at **every** timeslice
boundary, so over a long run each thread's advantage averages out (law of large
numbers). RR's insight is to instead give each thread a slowdown factor that is
**stable for the whole run** — some threads consistently slower, some faster —
which biases interleavings persistently and exposes more races.

`--chaos-per-thread-slowdown` implements this. Each thread's factor is a pure,
replayable function of `(sched_seed, dettid)`, drawn log-uniformly from
`[1/R, R]` where `R = --chaos-slowdown-max-factor` (default 10). The factor
scales only the **mean chaos timeslice length** (an out-of-band scheduling
budget) — it does **not** scale guest-visible virtual time, so determinism is
preserved (L1/L2) and the mode is `--strict`-safe.

## Method

Same oracle/harness as the framework sweep: `btrfs-progs v7.1
rescue chunk-recover -y -v <dev>` on fuzz image `bko-161811`; a scan worker
races a progress-reporter thread. Per-run **signature** = sha256 of the
hermit-filtered guest output; distinct signatures = distinct interleavings.
`N = 40` seeds/config, scratch device path **pinned** constant, SCOPED pgid
kills via `run_scoped.sh` (`--timeout 120`). `hermit` `d3cf69a4` @ branch
`codex/chaos-per-thread-slowdown` (`0b84a234`).

Metrics (`analyze.py`): `distinct` = # distinct signatures; `hit%` = fraction of
seeds whose signature differs from the modal `chaos_plain` (un-flipped)
schedule; `stf` = seeds-to-first-repro (median / p90 over rotated seed orders,
censored to `never`).

## Results

`hermit` sha256 `d3cf69a4` @ `0b84a234`. Raw per-run: `results.tsv`;
machine-readable `summary.tsv`; rendered below (`TABLE.txt`).

| config | N | distinct | hit% | stf_med | stf_p90 | mean_ms |
|---|---|---|---|---|---|---|
| chaos_plain | 40 | 2 | 45% | 2 | 4 | 347 |
| chaos_pts_r10 | 40 | 2 | 50% | 1 | 4 | 365 |
| chaos_pts_r100 | 40 | 2 | 52% | 1 | 3 | 368 |
| chaos_plain_ts1e4 | 40 | 36 | 100% | 1 | 1 | 423 |
| chaos_pts_r10_ts1e4 | 40 | 26 | 100% | 1 | 1 | 405 |

(`pts_r10` = `--chaos-per-thread-slowdown --chaos-slowdown-max-factor 10`;
`ts1e4` = additionally `--target-timeslice 10000`.)

## Interpretation (honest)

- **Coarse (default) timeslice, the binary regime.** Per-thread slowdown gives
  a **slight edge**: seeds-to-first-repro median 2→1 and hit-rate 45%→50–52%.
  But it stays in the **2-state binary** — this workload has only two reachable
  interleavings at coarse timeslice, and a stable per-thread factor cannot
  manufacture interleavings that do not exist; it only reaches the existing
  alternate slightly faster. The 45%→52% difference is ~3 seeds out of 40,
  **within noise** for a single 40-seed realization.
- **Fine timeslice (1e4).** The `--target-timeslice` knob dominates either way
  (100% hit, first seed) — consistent with the framework finding. Adding
  slowdown here slightly **lowers** diversity (36→26 distinct) because a longer
  mean slice on the "slow" threads inserts *fewer* preemption points. So the two
  knobs are not simply additive; on a saturated fine-slice regime, slowdown is a
  mild net negative for raw diversity.
- **No regression, correct + deterministic + L2-safe.** Across all configs the
  feature runs cleanly and (separately verified) `hermit run --strict --verify
  --chaos --chaos-per-thread-slowdown` is bitwise-identical (L2) on
  `hello_race` and `cas_sequence_easy_bin`. Factors are stable per thread
  (one factor per thread across all its timeslices) and identical across repeat
  runs at a fixed seed.

### Why this oracle understates the feature

The chunk-recover race is **benign** (v7.1 hardened; only a progress-tick
differs) and, critically, has a **tiny interleaving space** (2 states at coarse
slice) and **short thread runtimes**. RR-style stable slowdown is designed for
the opposite regime — **long-running, many-thread** programs where per-timeslice
priority redraw averages out over thousands of scheduling decisions. This
harness cannot exercise that thesis; it demonstrates correctness, determinism,
and L2-safety, and shows a small non-harmful coarse-regime effect, but is a
**weak** test of the feature's headline benefit. An unhardened many-thread
crasher with a large interleaving space would let the same harness measure
seeds-to-crash and is the right follow-up.

## Reproduce

```bash
cd ~/work/dev-hermit
# inputs: worktrees/chaos/hermit release binary (feature branch);
# ignored/btrfs-progs-v7.1-bin/btrfs.box.static; ignored/bench-seeds/bko-161811.raw
bash experiments/chaos_per_thread_slowdown_bench_20260728/sweep.sh      # -> results.tsv
python3 experiments/chaos_per_thread_slowdown_bench_20260728/analyze.py # -> summary.tsv + TABLE.txt
```

Files: `configs.tsv` (5-config grid), `sweep.sh` (grid×40-seed sweep, idempotent
skip of done configs), `analyze.py` (metrics), `results.tsv` (200 rows),
`summary.tsv` / `TABLE.txt`, `metadata.json`, `outputs/` (one transcript/config).
