# Virtual-time slowdown/epoch seeds-to-reproduce rerun

Research-only result for `benchmark-rerun-with-vtime-slowdown`, 2026-07-29.
This reruns the multithreaded `btrfs rescue chunk-recover` benchmark after the
virtual-time slowdown correction on PR #1151.

## Important provenance correction

The task premise said #1151 had merged, but the remote state did not support
that claim. At measurement time, `rrnewton/hermit:main` was
`291679b9ec7cb37a147589d40e5f174c4b40f9f9`; PR #1151 was open and draft with
`mergedAt=null`. These results are therefore bound to the exact PR head
`0a8a6a02ca8970322afb37ae9a7f092d29c074df`, not to landed main.

## Method

The workload is btrfs-progs v7.1 `rescue chunk-recover -y -v` on the pinned
`bko-161811.raw` fuzz image. Its scan worker and progress reporter have a benign
race over whether an intermediate `Scanning:` line appears. The oracle is the
SHA-256 prefix of Hermit-filtered guest output. A hit is any signature different
from blind seed 1 (`a45095499c2b`).

Each strategy ran seeds 1 through 60 against the same pinned scratch-device
path. Seeds-to-first-repro is bootstrapped over 5,000 shuffled orders of that
finite seed pool, matching the earlier benchmark methodology.

| strategy | Hermit arguments |
|---|---|
| blind | `run --seed N` |
| chaos | `run --chaos --seed N` |
| target_races | `run --chaos --chaos-target-races --seed N` |
| slowdown_constant | chaos + `--chaos-per-thread-slowdown --chaos-slowdown-max-factor 10` |
| slowdown_epoch_100us | constant slowdown + `--chaos-epoch-length-ns 100000` |

## Results

| strategy | N | distinct | hits | hit-rate | observed first | stf median | stf p90 | mean ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| blind | 60 | 1 | 0 | 0.0% | never | never | never | 217 |
| chaos | 60 | 2 | 30 | 50.0% | 1 | 1.00 | 4 | 220 |
| target_races | 60 | 2 | 21 | 35.0% | 1 | 2.00 | 6 | 227 |
| slowdown_constant | 60 | 2 | 29 | 48.3% | 1 | 2.00 | 4 | 233 |
| slowdown_epoch_100us | 60 | 2 | 29 | 48.3% | 1 | 2.00 | 4 | 227 |

All 300 benchmark runs exited 0.

The corrected slowdown knobs did **not** lower seeds-to-reproduce versus plain
chaos on this workload. Plain chaos had one additional hit out of 60 and a
bootstrap median of 1 rather than 2. Both slowdown modes did outperform
target-races here, but the robust result remains blind 0% versus the
chaos-family 35–50%.

Constant and 100us epoch slowdown produced identical signatures for every one
of the 60 seeds. Record artifacts explain why: this short coarse-timeslice
workload recorded no priority-change preemptions and only one slowdown factor
per participating thread. The epoch mode selected an epoch-derived initial
factor, but never reached another scheduler boundary at which to redraw it.
Smaller 10us and 1us diagnostic epochs had the same one-factor shape. Thus this
workload exercises virtual-time-weighted slowdown but is too short to exercise
between-epoch redraws; epochs correctly collapse to the single-epoch case here.

## Replay proof

Representative seed-1 record/replay runs for constant, 100us, 10us, and 1us
slowdown all exited 0 and produced byte-identical filtered guest output. The
selected table configuration (`slowdown_epoch_100us`) recorded and replayed
signature `231ff295a410`. See `replay-proof.tsv` and the compact artifact
projection in `replay-epochs.json`; `replay-proof.sh` regenerates full replay
artifacts when needed.

## Limits

- The target is a benign progress-line race, not a crash.
- It has only two observed outcomes and short thread lifetimes, making it a
  weak test of persistent slowdown and a particularly weak test of epoch
  redraws.
- The scratch-device path is schedule-sensitive and was held constant at the
  same path used by the prior slowdown benchmark.
- Results describe the ptrace backend at PR head `0a8a6a02`, not merged main.

## Reproduce

```bash
bash experiments/vtime_slowdown_seeds_benchmark_20260729/sweep.sh
python3 experiments/vtime_slowdown_seeds_benchmark_20260729/analyze.py
bash experiments/vtime_slowdown_seeds_benchmark_20260729/replay-proof.sh
```

Raw rows are in `results.tsv`; machine-readable aggregates are in
`summary.tsv`; `TABLE.md` is the requested compact table.
