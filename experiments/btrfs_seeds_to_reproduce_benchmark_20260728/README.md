# Seeds/runs-to-reproduce benchmark — targeted chaos vs blind fuzzing

**Task:** `benchmark-seeds-to-reproduce`. Date: 2026-07-28. Research-only.

**Question:** For a scheduling-dependent bug vs a deterministic bug, how many
random seeds does each hermit strategy need to *reproduce a target execution*,
and does chaos scheduling beat blind seed sweeping?

## Method

Two bug classes, five strategies, swept over seeds `1..N` with a **pinned**
scratch device path (chunk-recover's interleaving is sensitive to the device
path *string* — see
[[btrfs-progs-chunk-recover-only-multithreaded-benign-race]] — so the path is
held constant to isolate the seed as the only independent variable).

| strategy | hermit command | schedule |
|---|---|---|
| `plain`  | `run --seed N` | default backend, fixed schedule |
| `strict` | `run --strict --seed N` | deterministic schedule; `--seed` = RNG only |
| `chaos`  | `run --chaos --seed N` | chaos schedule |
| `ctr`    | `run --chaos --chaos-target-races --seed N` | race-targeted chaos |
| `ctr_ts` | `ctr` + `--target-timeslice 100000 --max-timeslice 1000000000` | race-targeted + timeslice |

**Bug 1 — scheduling-dependent (multithreaded).** `btrfs-progs v7.1
rescue chunk-recover -y -v <dev>` on a fuzz image. The scan worker and the
progress-reporter thread race on whether the intermediate `Scanning: 0 in dev0`
tick is printed before the scan signals `DONE`. This yields exactly two benign
interleavings (identical recovery result; only the progress line differs):

- **sigA** = `…Scanning: 0 in dev0   Scanning: DONE…` (tick emitted)
- **sigB** = `…Scanning: DONE…` (tick lost)

The signature is the sha256 of the hermit-filtered guest output. **Baseline** =
the signature of a non-exploring run (`--strict --seed 1`). **Reproducing the
race** = observing *any* signature ≠ baseline (a distinct thread interleaving).
Two images tested for generality: `bko-161811` and
`bko-154021-invalid-drop-level` (both 16 MiB).

**Bug 2 — deterministic (control).** demo-08's `btrfs check` on the issue-#207
crash image → `BUG_ON('eb->refs < 0')` SIGABRT (exit 134). Single-threaded, so
the outcome is seed- and schedule-invariant.

**Stats.** Each strategy is deterministic given a seed, so seeds `1..N` are a
fixed sample of the strategy's reachable outcomes. "Random seed selection" is
modelled by shuffling that sample; **seeds-to-first-repro** is the 1-based index
of the first seed that hits the target, bootstrapped over 5000 shuffles
(without replacement — the finite-pool analog of the geometric `1/p`), censored
at `>N` when a shuffle never hits.

## Results

`N=60` seeds/strategy for chunk-recover (both images), `N=10` for the control.
660 runs total. Full table in `TABLE.txt`; machine-readable in `summary.tsv`.

### chunk_recover — `bko-161811` (target: sigB ≠ baseline)

| strategy | coverage | hit-rate | stf mean | stf median | stf p90 | cover-both |
|---|---|---|---|---|---|---|
| plain  | 1 | **0.0 %** | never | never | never | — |
| strict | 1 | **0.0 %** | never | never | never | — |
| chaos  | 2 | 50.0 % | 1.94 | 1 | 4 | ~2 seeds |
| ctr    | 2 | 35.0 % | 2.80 | 2 | 6 | ~3 seeds |
| ctr_ts | 1 | **0.0 %** | never | never | never | — |

### chunk_recover — `bko-154021` (generality check, target: sigB ≠ baseline)

| strategy | coverage | hit-rate | stf mean | stf median | stf p90 | cover-both |
|---|---|---|---|---|---|---|
| plain  | 1 | **0.0 %** | never | never | never | — |
| strict | 1 | **0.0 %** | never | never | never | — |
| chaos  | 2 | 50.0 % | 1.97 | 1 | 4 | ~2 seeds |
| ctr    | 2 | 35.0 % | 2.78 | 2 | 6 | ~3 seeds |
| ctr_ts | 1 | **0.0 %** | never | never | never | — |

### demo08_check — deterministic control (target: BUG_ON abort)

| strategy | coverage | hit-rate | stf mean/median/p90 |
|---|---|---|---|
| plain / strict / chaos / ctr / ctr_ts | 1 | **100.0 %** | **1 / 1 / 1** |

## Interpretation (headline)

- **Blind seed sweeping under a fixed schedule never finds the race.** `plain`
  and `strict` produce the baseline interleaving on **all 60 seeds** (coverage 1,
  hit-rate 0 %) on **both** images. No number of seeds escapes it — a fixed
  schedule is a fixed schedule; `--seed` only reseeds the guest RNG.
- **Chaos scheduling finds the alternate interleaving in a handful of seeds.**
  `--chaos` and `--chaos-target-races` reach coverage 2 with a finite
  seeds-to-first-repro — **median 1, p90 4** for `--chaos`; and discover *both*
  interleavings within ~2–3 seeds. This is the quantitative statement of
  "targeted chaos beats blind fuzzing": the fixed-schedule strategies are
  *provably* stuck (∞ seeds), chaos is *provably* not.
- **The deterministic bug is trivially 1** for every strategy — the control row
  the task asked for. hermit reproduces it on the first seed, always; chaos buys
  nothing where there is no interleaving to explore.

### Honest caveats

- On **this** benchmark, plain `--chaos` edged `--chaos-target-races` (50 % vs
  35 % hit-rate) and `ctr_ts` collapsed to the baseline (0 %). The *robust*
  cross-image finding is the **fixed-schedule (0 %) vs chaos-family (>0 %,
  coverage 2)** gap, not a strict ranking *among* chaos variants — the exact
  per-variant hit-rate is sensitive to the pinned device-path string and the
  timeslice knob (both perturb chaos decisions). `chaos-target-races` biases
  *toward lock-ordering/wakeup* races; on this benign progress-tick race it holds
  no edge, and the timeslice choice can suppress exploration entirely.
- The chunk-recover race is **benign** (only the progress-tick count differs; no
  crash — v7.1 hardened all historical crashers). So this measures *interleaving-
  discovery power*, a proxy for reproducing a *would-be* scheduling bug, not the
  triggering of a real crash. The real crash in this study is the deterministic
  demo-08 abort (trivially 1). A corpus with an *unhardened* multithreaded
  crasher would let the same harness measure seeds-to-*crash* directly.

## Reproduce

```bash
cd ~/work/dev-hermit
# inputs: hermit release binary; btrfs-progs v7.1 static box in
# ignored/btrfs-progs-v7.1-bin/btrfs.box.static; fuzz images decompressed into
# ignored/bench-seeds/ ; demo-08 buggy btrfs + crash.btrfs (see demo08 README).
bash experiments/btrfs_seeds_to_reproduce_benchmark_20260728/bench.sh   # -> results.tsv
python3 experiments/btrfs_seeds_to_reproduce_benchmark_20260728/stats.py # -> summary.tsv + TABLE.txt
```

Files: `bench.sh` (sweep), `stats.py` (bootstrap stats), `results.tsv` /
`results_img2.tsv` (raw per-run signatures), `summary.tsv` (machine-readable),
`TABLE.txt` (rendered), `metadata.json` (provenance), `outputs/` (one
representative transcript per strategy). SCOPED pgid kills only via
`run_scoped.sh`.
