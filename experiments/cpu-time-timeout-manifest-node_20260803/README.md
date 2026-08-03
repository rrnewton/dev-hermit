# CPU-time vs wall-time timeout for the "Centralized test manifest and inventory" node

**Task:** `ci-timeout-audit-cpu-time-manifest-node` (owner-requested; subsystem
safe-ci-dag-runner). **Date:** 2026-08-03. **Host:** devvm, Linux
6.18.39 x86_64, 316 logical CPUs (shared box, other agents active).

## Question

The gate `Centralized test manifest and inventory` (validate.sh `run_check`,
`GATE_TIMEOUT_SECONDS`=600, WALL) / DAG node `e2e/metadata`
(`cmd: ./ci/test_harness.sh validate`, `timeout: 60`, WALL) has a timeout wildly
larger than its runtime. Can we replace the loose *wall* bound with a much
tighter **CPU-time** bound (user+system) that is immune to machine load, so it
does not flake on this shared 300-core box?

## Method

Two independent sample sets of `./ci/test_harness.sh validate` run from the
Hermit primary checkout (`4a97ec36…`), measured with `/usr/bin/time -v`
(user/system CPU come from `getrusage(RUSAGE_CHILDREN)` — the same quantity a
cgroup `cpu.stat` reports, and the quantity the new runner guard enforces):

1. **ambient** (`generate-ambient.sh`, `ambient-samples.csv`): 20 sequential
   runs on the full box under whatever ambient load other agents produced. One
   run (seq,20) hit an organic load spike.
2. **controlled-load** (`generate-controlled-load.sh`,
   `controlled-load-samples.csv`): the node **pinned to a 4-CPU cpuset** (`0-3`),
   run 5× with no competition (`base`) then 10× while **8 CPU burners saturate
   the same 4 CPUs** (`load`, 2× oversubscription). This is the decisive test:
   contention inflates WALL but must not inflate CPU time.

Note: an earlier attempt ran 8 copies of `validate` concurrently in one shared
checkout; that corrupted the CSV (shared-checkout races + interleaved
`time --append`) and those rows were discarded. The cpuset+burner method
isolates the node while inducing pure CPU contention.

## Results (min / median / p90 / p99 / max, seconds)

| set | metric | n | min | med | p90 | p99 | max |
|-----|--------|---|-----|-----|-----|-----|-----|
| ambient       | WALL | 20 | 6.57 | 7.71 |  9.65 | 19.32 | 21.03 |
| ambient       | CPU  | 20 | 7.08 | 8.01 |  9.81 | 11.80 | 12.24 |
| cpuset base   | WALL |  5 | 6.11 | 6.18 |  6.27 |  6.29 |  6.29 |
| cpuset base   | CPU  |  5 | 6.50 | 6.57 |  6.68 |  6.69 |  6.69 |
| cpuset LOADED | WALL | 10 | 8.44 | 9.57 | 11.39 | 16.71 | 17.30 |
| cpuset LOADED | CPU  | 10 | 7.26 | 7.48 |  7.90 |  7.97 |  7.98 |
| **CPU pooled**| CPU  | 35 | 6.50 | 7.79 |  8.84 | 11.45 | **12.24** |

Independent cross-check: hermit-ci observed the same node complete in **8s wall**
during a batch validate at hermit `2dec5db0`
(`/tmp/hermit-groupA-batch-validate-2dec5db0.log`) — lands on the wall
distribution's shoulder, consistent with these samples (sample of one, used only
as a sanity anchor, not as the basis for the number).

Historical WALL for this gate (106 samples harvested from the validate-run
ledger + raw `/tmp/hermit-validate.*.log`): min 4 / median 6 / p90 11 / p99 17 /
**max 57s**.

## Interpretation

- **WALL inflates under load; CPU does not.** Baseline→loaded: WALL median
  6.18→9.57s (1.55×), max 6.29→17.30s (2.80×). CPU median 6.57→7.48s (1.14×),
  max 6.69→7.98s (1.19×). CPU time is essentially load-invariant, exactly the
  owner's thesis.
- A tight *wall* timeout of ~18s would flake: WALL already reached 17.3s
  (controlled) / 21.0s (ambient) / 57s (historical) purely from contention while
  the node did the same ~7–8s of work.
- The same ~18s expressed as a **CPU** budget cannot flake from load: the worst
  CPU observation across all 35 runs is 12.24s, and under 2× oversubscription CPU
  never exceeded 7.98s.

## Proposed number

**`cpu_timeout: 18s`** for `e2e/metadata`.
Arithmetic (owner rule = "≤50% above what it normally takes"):
worst observed CPU = 12.24s; 12.24 × 1.5 = 18.36 → round to **18s**
(= 1.47× the worst observed CPU, within the ≤50% margin; = 2.25× the worst
*under-load* CPU). The wall `timeout` stays as a loose hang backstop — CPU is now
the tight, load-immune bound. This is a 33× reduction from the 600s gate and
well under the owner's 30s target, with the arithmetic anchored on the honest
distribution max rather than the median.

## Reproduction

```
cd ~/work/dev-hermit/hermit           # primary checkout at 4a97ec36…
bash <this dir>/generate-ambient.sh          # -> /tmp/vtimeout-samples.csv
bash <this dir>/generate-controlled-load.sh  # -> /tmp/vtimeout-load.csv
```
