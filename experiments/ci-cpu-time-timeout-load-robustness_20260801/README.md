# CPU-time vs wall-only timeout robustness under load (safe-ci-dag-runner)

**Task:** `timeout-headroom-and-load-relative` · **Agent:** hermit-ci · **Date:** 2026-08-01

## Question

Owner directive: is a **generous wall timeout + aggressive `RLIMIT_CPU` CPU-time budget** *more
robust* against **spurious** timeouts under host CPU contention than today's tight **wall-only**
timeout in `safe-ci-dag-runner`? Report a flake-rate comparison per load level, and confirm the CPU
budget still catches a genuine runaway (robustness, not mere leniency).

## Method

- **Victim:** one step doing a fixed ~2.0 CPU-seconds of legit work (`awk` summing 64M ints,
  ~2.25 s idle boxed wall). Any non-PASS on the victim == a **spurious timeout == a flake**.
- **Load model — *inside* the DAG.** The runner boxes every step in its own cgroup-v2 scope, so
  external-slice host load barely slows a step (a genuinely useful CI finding: boxing already
  isolates a step from *foreign* processes). The wall flakes that actually bite CI therefore come
  from **contention among the runner's own concurrent steps** (sibling `step-*.cpu` cgroups splitting
  a core). So load is modeled as *N* extra concurrent CPU-bound steps in the same DAG, with the whole
  runner pinned to **one core** (`taskset -c 0`) — *K* concurrent equal steps each get ~1/*K* of the
  core, so wall inflates ~*K*x while each step's CPU-seconds stay fixed.
- **Policies compared:**
  - `wall-tight`  — `timeout=4s` (~1.5× the 2.25 s idle wall), `cpu_timeout=0` → **wall-only (today)**.
  - `cpu-generous` — `timeout=120s` (generous wall backstop), `cpu_timeout=6` CPU-s (~3× the ~2 CPU-s).
- **Load levels:** `idle`=0, `moderate`=2, `swamped`=5 concurrent load steps. **8 reps** per cell.

## Results

```
policy/load                  reps   flakes     flake%   max_wall_s
wall-tight/idle                 8        0         0%         4.26
wall-tight/moderate             8        8       100%         4.43
wall-tight/swamped              8        8       100%         4.73
cpu-generous/idle               8        0         0%         3.13
cpu-generous/moderate           8        0         0%         6.51
cpu-generous/swamped            8        0         0%        12.57
```

**Runaway control:** an infinite busy-loop (`while : ; do : ; done`) under the `cpu-generous` policy
(6 CPU-s budget, 120 s wall backstop) is **killed at ~6 CPU-s** via `CPU-TIMEOUT (RLIMIT_CPU)` —
*not* allowed to run to the 120 s wall. A wall-only policy generous enough to never flake (120 s)
would let this run 120 s.

## Interpretation

The load-invariant CPU-time budget **eliminates the wall-timeout flake** (100% → 0% at moderate and
swamped) *while still bounding a genuine runaway*. A CPU-second is physical work, so the same
legitimate step costs the same CPU-seconds whether the host is idle or 6-way contended, whereas its
wall time inflates and trips a tight wall timeout. This confirms the owner's CPU-time approach:
pair a **generous `timeout`** (wall backstop for multi-process / truly-stuck cases) with an
**aggressive `cpu_timeout`** (the real limit).

Implemented in `safe-ci-dag-runner` via `prlimit --cpu=soft:hard` (RLIMIT_CPU, inherited across the
step's whole process tree; no `unsafe`/`libc`). See rrnewton/agent-utils PR #4.

## Reproduction

```bash
RUNNER=/path/to/rs/target/release/safe-ci-dag-runner ./run.sh
```

Tracked outputs: `results/results.csv` (per-rep), `results/summary.txt` (flake table). Raw per-rep
logs and generated DAGs land in the gitignored `ignored/` subdir. Exact SHAs, host facts, and knobs
are in `metadata.json`.
