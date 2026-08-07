# e2e.metadata node budget: where its wall time actually goes

**Question.** The `e2e.metadata` DAG node (`./ci/test_harness.sh validate`, `timeout: 60`,
`est_duration_s: 30`) was observed exceeding 60s under fleet load and, separately,
OOM-killed against a 1 GiB `hard_mem_max_bytes`. Are those the same problem, and is
the fix a bigger budget or a shorter critical path?

**Answer.** They are not the same problem. The wall time is real, compressible CPU
work dominated by process-creation overhead, and **43x of one phase is removable
without changing any semantics**. The OOM did not reproduce under the exact declared
cap in any condition tested, so no memory-cap change is justified yet.

## Method

`measure.py` runs one command inside a transient `systemd-run --user --scope` and reads
`cpu.stat`, `memory.peak`, `memory.events`, and `pids.peak` from **the live process's own
cgroup**, resolved out of `/proc/self/cgroup` — not from the flags passed to `systemd-run`.
That distinction is load-bearing here: in some contexts on this host a scope is created
successfully while `cpu.max`/`memory.max` silently fail to bind, so the applied caps are
read back and recorded alongside every measurement.

CPU and wall are reported separately because they answer different questions. `cpu/wall`
above 1 means the node is genuinely burning parallel CPU; well below 1 would mean it is
waiting, and only the waiting case is what a larger timeout papers over.

Counts travel with their denominator: this host has **316 cores**, so a load average of
175 is 55% utilisation, not overload.

Hot lines were located by tracing, not inspection:
`PS4='+ ${EPOCHREALTIME} ${BASH_SOURCE##*/}:${LINENO}' bash -x ./ci/test_harness.sh audit-ci`,
then attributing elapsed time to the line that preceded each traced command.

## Results

Low load (load1 43–61, i.e. ~19% of 316 cores), warm cache, 3 runs:

| run | wall | cpu total | user | system | cpu/wall | peak RSS | peak pids |
|---|---|---|---|---|---|---|---|
| 1 | 24.831s | 41.607s | 13.248s | 28.360s | 1.676 | 315 MiB | 866 |
| 2 | 24.741s | 44.653s | 13.502s | 31.151s | 1.805 | 306 MiB | 778 |
| 3 | 23.299s | 40.700s | 12.833s | 27.867s | 1.747 | 308 MiB | 836 |

System time is **2.2x user time** across 800+ processes. That ratio is the signature of
fork/exec overhead rather than computation.

Phase decomposition (every subcommand also pays the `load_tests` floor, so subtract it):

| phase | wall | cpu | peak pids | marginal wall |
|---|---|---|---|---|
| `plan` (= the floor, i.e. `load_tests`) | 6.088s | 6.322s | 18 | — |
| `audit-test-footprints` | 6.328s | 6.577s | 19 | +0.24s |
| `audit-inventory` | 5.832s | 6.045s | 18 | ~0 (noise) |
| `audit-ci` | 20.292s | 30.143s | **947** | **+14.2s** |
| `validate` (total) | 28.345s | 45.230s | 921 | — |

Memory, under the node's exact declared cap (`MemoryMax=1073741824`, verified applied):

| condition | wall | cpu | peak RSS | oom_kill |
|---|---|---|---|---|
| warm | 23.996s | 43.313s | 309 MiB | 0 |
| warm | 23.214s | 39.291s | 308 MiB | 0 |
| **cold cargo cache** | 30.623s | 56.216s | **452 MiB** | 0 |

## Interpretation

**Two hot spots, only one of them waste.**

1. `load_tests` (`ci/test_harness.sh:131-142`) already explodes the manifest into 313
   JSONL documents with one `jq` at line 151, then spawns **four more `jq` processes per
   document** — `.id`, `.program`, `normalize_metadata`, `.program_kind` — for ~1,252
   invocations whose cost is almost entirely fork+exec. Folding all four into the explode
   `jq` that already runs is semantically identical. Measured on the real 313-document
   input: **4.282s -> 0.100s, a 43x reduction.** Because `load_tests` runs *before* the
   subcommand dispatch, this cost is paid by every `test_harness.sh` invocation in the
   DAG, not just this node.

2. `ci/test_harness.sh:471-472` is a deliberate 4-way concurrent `rust-script` checker
   race, `taskset`-pinned to a single CPU so `rustc` cannot infer the 316-CPU host. Four
   compiles serialised on one core cost ~10s **by design**. This is intentional coverage
   and should not be "optimised" away; it should be budgeted for.

**The node is the DAG root**, one of 8 dep-free steps with 16 of 47 steps depending on it,
so time removed here shortens the whole lane's critical path. It also means the 71.2s
observation cannot be DAG self-contention: when this node runs, every other dependent node
is still blocked on it. That load came from other tenants of the shared host.

**The declared `rss_baseline_bytes` is too low.** 268435456 (256 MiB) against a measured
warm peak of 306–315 MiB and a cold peak of 452 MiB, so the scheduler under-reserves for
this node even in the good case.

**The OOM is unexplained and must stay that way for now.** It did not reproduce warm, cold,
or under the exact 1 GiB cap; peak stayed at 30–44% of the cap. Raising `hard_mem_max_bytes`
would be treating a symptom of something not yet observed.

## Reproduction

```bash
SLOT=<a hermit checkout at the SHA in metadata.json>
python3 measure.py --label lowload --cwd "$SLOT" --outdir runs \
  --cmd './ci/test_harness.sh validate' --repeat 3 --results results.jsonl

# phase decomposition
for sub in plan audit-test-footprints audit-inventory audit-ci; do
  python3 measure.py --label "$sub" --cwd "$SLOT" --outdir runs \
    --cmd "./ci/test_harness.sh $sub" --results phases.jsonl
done

# under the node's declared cap
python3 measure.py --label cap-1GiB --cwd "$SLOT" --outdir runs \
  --cmd './ci/test_harness.sh validate' --mem-max 1073741824 --repeat 2 --results oom.jsonl
```

`results.csv` is the flattened union of the three JSONL files. Per-run stdout/stderr and
raw cgroup counter files are under `runs/<tag>/`.

## Limitations

- The negative control at load >150 was **not** captured. The fleet was at load1 30–60
  during this session. Manufacturing load on a box shared with ~18 agents was rejected as
  the wrong trade; the window should be caught opportunistically instead.
- Commands were run directly, not through `ci/run-node.sh`, which sources
  `ci/configure-build-jobs.sh` and may set a different `CARGO_BUILD_JOBS` than the default
  used here. The cold-cache memory figure in particular could move under the real runner.
- One host, one checkout. No cross-host claim is made.
