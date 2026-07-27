# Binary search for optimal `--max-timeslice` (QEMU/TCG Linux boot under Hermit)

**Date:** 2026-07-27
**Task:** `impl-timeslice-binary-search` (agent hermit-274, slot `worktrees/274`)
**Hermit SHA:** `d16b7fc67c190e489cd2f58b0173570d8d39c0ad` (origin/main HEAD, release build)

## Question

`--max-timeslice 2000000000` (2B) was known to boot the QEMU/TCG Linux guest in
~49s (a 3.3x speedup over the ~160s default). Can a smaller `--max-timeslice`
achieve the same result, and where does it degrade? Find the knee of the curve.

## Method

For each `--max-timeslice` value, boot a single-vCPU TCG Linux guest under
`hermit run --strict` with `--target-timeslice 100000`:

```
timeout 300 hermit run --strict --summary --summary-json=<f> \
  --target-timeslice 100000 --max-timeslice <V> -- \
  qemu-system-x86_64 -accel tcg,thread=single -smp 1 \
    -icount shift=0,sleep=off \
    -kernel ignored/qemu-linux/bzImage \
    -initrd ignored/qemu-linux/initramfs-hermit.cpio.gz \
    -append 'console=ttyS0 panic=1' -nographic -no-reboot -m 256
```

Values tested: 10M, 50M, 100M, 500M, 1B, 2B.

Metrics recorded:
- **exit code** (0 = clean power-down; 124 = `timeout` killed it at 300s).
- **wall time** — informative but LOAD-SENSITIVE (many agents ran Hermit
  concurrently during capture); NOT a deterministic signal.
- **PMU events count** = number of `detcore: prehook: PMU RCB overshoot!`
  events, i.e. the number of PMU-driven max-timeslice preemptions. This is
  **deterministic** (identical across the two capture passes: 100M=896,
  500M=71, 1B=7, 2B=8) and machine-load-independent, so it is the reliable
  metric. NOTE: this is distinct from the RunSummary `Timeslice stats: count`
  (which counts ALL timeslice boundaries incl. syscall/HLT yields, e.g. 27636
  for 2B); "PMU events" here means only the boundaries the PMU actually drove.

## Results

See `final_results.csv`. Two capture passes: pass 1 (`run.sh` -> `logs/`,
`results.csv`, all 6 values, wall+exit); pass 2 (`summary_runs.sh` ->
`summary/`, the 4 completing values, adds `--summary-json` + deterministic
PMU-overshoot counts). PMU-overshoot counts matched exactly between passes.

| max-timeslice | exit | boot? | wall (s) | PMU preemptions |
| --- | --- | --- | --- | --- |
| 10,000,000 (10M) | 124 timeout | NO | >300 | 9286* |
| 50,000,000 (50M) | 124 timeout | NO | >300 | 1869* |
| 100,000,000 (100M) | 0 | yes | 192.8 | 896 |
| 500,000,000 (500M) | 0 | yes | 60.9 | 71 |
| 1,000,000,000 (1B) | 0 | yes | 24.1 | **7** |
| 2,000,000,000 (2B) | 0 | yes | 24.9 | 8 |

\* Timeout cases: the boot never completed, so the PMU count is accumulated to
the 300s cutoff, not a whole-boot figure. The trend still holds: smaller
`--max-timeslice` -> far more PMU preemptions -> boot cannot finish in 300s.

## Interpretation

- The PMU-preemption count falls steeply with larger `--max-timeslice`:
  896 (100M) -> 71 (500M) -> 7 (1B), then flattens (8 at 2B).
- **The knee is at `--max-timeslice = 1000000000` (1B).** It is the smallest
  tested value that reaches the single-digit PMU-preemption floor; the guest
  almost never exhausts a >=1B RCB budget before yielding, so raising the value
  to 2B buys nothing (7 vs 8 preemptions; wall 24.1s vs 24.9s, within noise).
- Below the knee, cost climbs fast: 500M is ~10x the PMU preemptions of 1B, and
  100M ~128x; 50M/10M cross into "cannot complete in 300s."
- Recommendation: **use `--max-timeslice 1000000000` (1B)** for this workload.
  It matches 2B's boot performance at half the value, and is comfortably clear
  of the degradation cliff between 100M and 50M.

## Reproduction

```
cd ~/work/dev-hermit
bash experiments/timeslice_binary_search_20260727/run.sh          # pass 1 (all 6)
bash experiments/timeslice_binary_search_20260727/summary_runs.sh  # pass 2 (4 + counts)
# PMU-overshoot count per log:
grep -c 'PMU RCB overshoot!' experiments/timeslice_binary_search_20260727/logs/max_<V>.log
```
