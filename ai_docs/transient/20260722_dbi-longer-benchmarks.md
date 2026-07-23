# DBI (DynamoRIO) backend — amortized (long-workload) benchmarks

Status: prototype evaluation. Date basis: 2026-07-22.

This is a follow-up to `dbi-backend-results.md` (the original short-workload
study). It re-benchmarks `hermit run --backend dbi` on **longer** CPU workloads
(3 s / 10 s / mixed) to test the hypothesis that DBI's overhead is dominated by
one-time startup + JIT compilation and would **amortize** on longer runs.

**Result: it does not amortize.** DBI's slowdown is a flat ~20× across 3 s, 10 s,
and mixed workloads — essentially the same ratio as the original 0.022 s loop
(18.4×). The JIT cost is negligible on hot loops; the entire slowdown is
steady-state per-branch instrumentation that scales linearly with work.

> ⚠️ The canonical `ai_docs/transient/dbi-backend-results.md` (in
> `worktrees/slot74`) was deleted mid-task when that slot's branch was switched
> by a concurrent agent. Its short-workload content is reproduced in the
> "Baseline" section below so this document is self-contained.

## Test environment

- Host: AMD EPYC 9D85 (316 hardware threads), Linux `6.13.2-0_fbk13_hardened`.
  **Machine was under heavy load (load avg ~100–150) from ~dozens of concurrent
  agent `hermit` runs during measurement.** Absolute numbers are therefore
  inflated and noisier than an idle host; the **ratios** (backend ÷ native) are
  the reliable signal and are internally consistent across workloads.
- DynamoRIO: source build 11.91 at `~/dynamorio/install`
  (`DYNAMORIO_HOME`, `HERMIT_DRRUN=<...>/bin64/drrun`). A harmless
  `cannot find .../lib64/debug/libdynamorio.so` warning prints but the release
  lib loads and runs correctly.
- DBI client: `libreverie_dbi_client.so` built from reverie rev **`69f47d9`**
  (`worktrees/slot12/.../reverie-dbi-native/`), via `HERMIT_DBI_CLIENT`. The
  `hermit run --backend dbi` build used is `worktrees/slot74` (reverie pin
  `e3e2c965`); this only matters for the drrun shell-out, since the client `.so`
  revision is independent.
- **ptrace** runs use `hermit-verify-keeplogs` (reverie **`96693397`**), *not*
  slot74/frontier. Frontier's reverie `e3e2c965` **SIGSEGVs in
  `clone_with_stack` on dynamic ELFs**, so it cannot benchmark ordinary
  dynamically-linked test binaries under ptrace at all. The `96693397` build
  runs dynamic ELFs correctly.
- All hermit runs wrapped in `with-proxy`. Workloads are plain dynamic ELFs
  (`gcc -O2`); static linking was unavailable (`-lc` static missing).

## Workloads

Sources in the appendix. Native medians (this loaded host):

| workload | native (s) | description |
|---|---:|---|
| `loop3s`  | 3.09  | `volatile` accumulate, 8.7e9 iters — pure CPU, syscall-free |
| `loop10s` | 10.22 | same, 29e9 iters |
| `mixed`   | 3.15  | 90k rounds × (100k-iter compute chunk + `getpid` + periodic `write`) → ~9e9 compute iters + ~95k syscalls |

## Amortized results: native vs bareDR vs DBI vs ptrace

Medians (reps: native ×3, bareDR ×2, dbi ×2, ptrace ×1). `×nat` = ÷ native.

| workload | native | bareDR (JIT only, **counting OFF**) | DBI (**counting ON**) | ptrace (default, determinism ON) | ptrace (`--preemption-timeout=disabled`) |
|---|---:|---:|---:|---:|---:|
| `loop3s`  | 3.09 (1.0×) | **3.10 (1.0×)** | **61.9 (20.0×)** | **CRASH** | 3.22 (1.04×) |
| `loop10s` | 10.22 (1.0×) | **10.4 (1.0×)** | **206.7 (20.2×)** | **>400 s TIMEOUT** | 10.14 (0.99×) |
| `mixed`   | 3.15 (1.0×) | **3.14 (1.0×)** | **64.5 (20.5×)** | **CRASH** | 8.46 (2.7×) |

- **bareDR** = `drrun -disable_rseq -- <prog>` with **no client** → pure
  DynamoRIO translation/JIT, no instrumentation. This is the cleanest available
  "branch-counting OFF" measurement (the prebuilt client has **no env toggle**
  to disable counting — 0 `getenv` calls; the counter is compiled in).
- **DBI** = `hermit run --backend dbi` = `drrun -c <client> -- <prog>`.
- **ptrace default** = `hermit run` (full Detcore determinism).
- **ptrace `--preemption-timeout=disabled`** = Detcore with deterministic
  preemption of syscall-free code turned OFF (drops a determinism guarantee).

## Interpretation

### 1. DBI overhead does NOT amortize — it is flat ~20×

DBI is 20.0× / 20.2× / 20.5× on 3 s / 10 s / mixed. Compare the original short
loop (50M iters, 0.022 s native): **18.4×**. Longer runs are *not* cheaper — if
anything marginally higher (the short loop's startup was diluted by so little
work it slightly *under*-counts the steady-state cost). The hypothesis that
"startup/JIT dominates and will amortize on a 3 s loop" is **refuted**.

### 2. The JIT fully amortizes; 100% of DBI's cost is per-branch counting

`bareDR` (JIT, no instrumentation) runs at **native speed** (1.0×) on every
workload, including `mixed`. DynamoRIO's translation cost is a one-time,
fully-amortized startup expense. Therefore the entire 20× gap between bareDR and
DBI is the prototype client's instrumentation: a **locked** 64-bit atomic
increment (`drx_insert_counter_update`, `DRX_COUNTER_64BIT | DRX_COUNTER_LOCK`,
`native/client.c:199-201`) inserted before **every** counted branch
(`instr_is_cbr || instr_is_ubr || instr_is_call || instr_is_return`). The tight
loops execute one conditional branch per iteration → ~9e9–29e9 `lock xadd`
operations. That is the whole story.

**Fix path (same as original doc's follow-up #1, now quantified):** making the
counter thread-local + non-locked, or eliding it entirely when RCB-preemption is
not required, should recover ~20×, i.e. bring CPU-bound DBI from 20× down toward
bareDR's ~1×.

### 3. ptrace-with-determinism CANNOT run long CPU loops (the ranking flips)

This is the most important comparison finding. The original doc reported
CPU-bound work as "DBI 1.5–2.5× slower than ptrace" (short 50M loop: ptrace
12.4× < dbi 18.4×). **That ordering only holds for loops short enough to finish
inside a single scheduling quantum.** Any loop long enough to cross a preemption
boundary forces Detcore to **single-step** the syscall-free code to count RCBs
for deterministic preemption — which is catastrophic:

- `loop3s` / `mixed` under default ptrace → **CRASH** (reverie falls over
  mid-single-step).
- `loop10s` under default ptrace → **>400 s timeout** (>40× and climbing, never
  finished).
- Proof it is the single-stepping: with `--preemption-timeout=disabled` the same
  loops run at **~1× native** (loop3s 3.22, loop10s 10.14). Disabling preemption
  removes deterministic scheduling of syscall-free code — the guarantee whose
  cost we are measuring.

So both backends pay for the *same* fundamental requirement — counting a guest's
progress to preempt syscall-free code deterministically — but by opposite
mechanisms:

| | mechanism to count progress | cost on long CPU loop |
|---|---|---|
| ptrace | single-step under PMU/ptrace | **catastrophic**: crash or >40× |
| DBI | inline per-branch atomic increment | **steady ~20×** |

**On any realistically long CPU burst, DBI's stable ~20× beats deterministic
ptrace, which cannot complete the run at all.** The short-loop table gave the
opposite impression only because 50M iterations never triggered a preemption.

### 4. Mixed workload: DBI's syscall advantage is swamped by compute counting

`mixed` has ~95k syscalls (DBI's strength — in-process interception) but ~9e9
compute iterations. DBI is still 20.5× because branch-counting the compute
dominates; the fast syscall path buys almost nothing here. Meanwhile
`ptrace --preemption-timeout=disabled` shows the *pure* syscall-interception
cost with no single-stepping: **2.7×** (8.46 s) — every syscall still
round-trips to the out-of-process tracer + Detcore. (Default ptrace crashes,
because the compute chunks between syscalls are single-stepped.)

This is consistent with the original doc's syscall-microbench crossover: DBI wins
big on **pure** syscall storms (200k `getpid` = ptrace 13.5 s vs DBI 0.21 s), but
that advantage requires the hot path to be syscall-bound, *not* interleaved with
counted compute.

## Bottom line for the "server apps" motivation

- Reducing/optionalizing the per-branch counter is the single highest-value DBI
  change: it is worth ~20× on CPU-bound code and is the only thing standing
  between DBI and near-native CPU performance (bareDR proves the headroom).
- DBI's structural advantage over ptrace is **not** raw CPU throughput; it is
  (a) syscall interception without a context switch, and (b) not needing to
  single-step syscall-free code to preempt it. On long CPU loops, (b) alone
  makes DBI viable where deterministic ptrace simply crashes/times out.
- Caveat unchanged from the original doc: DBI does not yet drive Detcore, so this
  compares interception *mechanisms*, not equal determinism guarantees.

## Reproduction

```bash
export HERMIT_DRRUN=~/dynamorio/install/bin64/drrun
export HERMIT_DBI_CLIENT=~/work/dev-hermit/worktrees/slot12/reverie/target/reverie-dbi-native/libreverie_dbi_client.so
export DYNAMORIO_HOME=~/dynamorio/install
DRRUN=~/dynamorio/install/bin64/drrun
HDBI=~/work/dev-hermit/worktrees/slot74/target/debug/hermit
HPT=~/work/dev-hermit/hermit-verify-keeplogs/target/debug/hermit   # reverie 96693397 (dynamic-ELF-safe)

native:                  ./loop3s
bareDR (counting OFF):   $DRRUN -disable_rseq -- ./loop3s
DBI    (counting ON):    with-proxy $HDBI run --backend dbi -- ./loop3s
ptrace (determinism ON): with-proxy $HPT run -- ./loop3s              # CRASH/timeout on long loops
ptrace (preempt OFF):    with-proxy $HPT run --preemption-timeout=disabled -- ./loop3s
```

Bench harness + sources: `scratch/dbi-longbench/` (`bench.sh`, `loop3s.c`,
`loop10s.c`, `mixed.c`).

## Appendix: workload sources

`loop3s.c` (8.7e9) / `loop10s.c` (29e9):
```c
#include <stdio.h>
int main(void){ volatile unsigned long s=0;
  for(unsigned long i=0;i<8700000000UL;i++) s+=i;   /* 29000000000UL for loop10s */
  printf("%lu\n", s); return 0; }
```

`mixed.c`:
```c
#include <stdio.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <fcntl.h>
int main(void){
  int fd=open("/dev/null",O_WRONLY);
  volatile unsigned long s=0;
  for(int r=0;r<90000;r++){
    for(unsigned long i=0;i<100000UL;i++) s+=i;   /* compute chunk */
    syscall(SYS_getpid);                          /* syscall */
    if((r&15)==0) write(fd,"x",1);                /* periodic write */
  }
  printf("%lu\n", s); return 0; }
```

## Baseline (reproduced from the deleted short-workload doc)

Short-workload performance table for reference (median of 5, seconds):

| workload | native | ptrace | dbi | pt/nat | dbi/nat | dbi/pt |
|---|---:|---:|---:|---:|---:|---:|
| echo (startup) | 0.005 | 0.032 | 0.078 | 6.8× | 16.6× | 2.43 |
| hello (startup) | 0.004 | 0.023 | 0.047 | 5.3× | 11.1× | 2.09 |
| python compute 1e6 | 0.422 | 4.568 | 6.799 | 10.8× | 16.1× | 1.49 |
| C loop 50M (CPU-bound) | 0.022 | 0.275 | 0.408 | 12.4× | 18.4× | 1.48 |
| sysbench 200k syscalls | 0.024 | 13.546 | 0.209 | 572× | 8.8× | **0.02** |
| redis --test-memory (CPU) | 1.798 | 3.688 | 9.247 | 2.1× | 5.1× | 2.51 |

The `C loop 50M` row (18.4× dbi/nat, 12.4× pt/nat) is the direct predecessor of
this study; the long-workload rows above show that DBI holds ~20× while
deterministic ptrace ceases to complete.
