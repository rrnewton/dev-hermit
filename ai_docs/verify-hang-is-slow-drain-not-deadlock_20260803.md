# `hermit run --strict --verify` "hang": slow-drain under saturation, not a deadlock

**Date:** 2026-08-03
**Agent:** hermit-ci (opus-4.8)
**Task:** `hermit_run_verify_hangs` (P1)
**Verdict:** The reported ptrace `--verify` "hang" is **CPU-saturation slow-drain**,
not a permanent hang or scheduler deadlock. Every run completes. It is **not** the
KVM startup busy-spin livelock. Abandoning a slow run does **not** create a
permanent namespace-holding corpse. **No hermit/reverie code change is warranted.**

This closes the incident thread that began with the (refuted) tracing-appender-leak
premise — see `ai_docs/tracing-appender-leak-premise-refuted_20260803.md`.

## Method

Controlled batches of `hermit/target/release/hermit run --strict --verify` at rising
concurrency, each characterized with `ci-hub load-probe` (executing-CPU %, not load
average — load average is the reading that produced the day's wrong premises).
Harness: `scratch/verify-hang-repro/batch.sh` records a **drain curve** (how many of
N are still alive at each 3 s checkpoint) plus a completion summary, so a *slow drain*
cannot be misrecorded as a *hang*. Per-run hard cap 240 s (>> any plausible slow
drain, so a genuine hang would surface as exit 124).

## Results

| batch | box at launch | drain | completions | max wall |
|---|---|---|---|---|
| 1× `/bin/true` | idle | — | rc=0 | 0.04 s |
| 60× `/bin/true` | 12% exec, SUITABLE | all in 3 s | 60/60 rc=0 | 0.32 s |
| 200× `awk BEGIN{print 1}` (real Run1/Run2/compare) | idle | all in 3 s | 200/200 rc=0 | 1.64 s |
| 300× compute-`awk` (5 M-iter loop) | **75.93% exec (239.94/316 cores), R=145, NOT SUITABLE** | flat at 300 for ~36 s, then monotonic to 0 at **90.6 s** | **300/300 rc=0, 0 timeouts** | 90.5 s (mean 56.6 s) |

The 300-way curve is the decisive one: driven into genuine CPU saturation, the batch
holds flat then drains convexly to zero — the signature of **throughput under
saturation**, not deadlock. No run hit the 240 s cap.

### State census during saturation (300 live verify processes)

- **229 `S anon_pipe_read`** — outer `hermit` awaiting the inner supervisor's result over a pipe.
- **204 `Sl do_epoll_wait`** (+21 `Sl -`) — tokio reactor genuinely **asleep/blocked**.
- **4 `Rl do_epoll_wait` + 1 `R`** — a handful actively running.

Overwhelmingly **S (blocked)**, not **R (busy-spin)**. (strace cannot attach to a
ptrace-backend supervisor — it is already the guest's tracer; this is expected, not a
finding.)

## Not the KVM livelock

| | KVM startup livelock | ptrace `--verify` |
|---|---|---|
| reproduces at | a **single** run, idle box | never at single/low concurrency; only *slow* under real saturation |
| main state | **R** busy-spin | **S** blocked (`anon_pipe_read` / asleep `do_epoll_wait`) |
| strace | `epoll_wait(fd,[],1024,0)=0` ~115k/s | reactor asleep |
| outcome | exit 124 (never completes) | always completes |

Different bug class; behavior alone separates them, so no frame comparison is needed.
KVM detail: `memory/kvm-startup-epoll-busyspin-livelock.md`.

## Abandonment event, reproduced under load

The incident's abandonment modes (agent recycle / 120 s tool-cap kill / detached run
outliving its launcher) reduce to: **hermit orphaned to ppid=1 mid-run**.
`scratch/verify-hang-repro/abandon_test.sh` started 15 slow verify runs plus a
150-run saturation load, then SIGKILLed their launcher mid-run. All 15 reparented to
ppid=1 (confirmed state S, mid-run); **all 15 then completed and reaped within ~60 s;
0/15 persisted; 0 hermit zombies system-wide; no retained namespace.** Orphaning a
slow verify run does not create a permanent corpse. (Phase 1 showed the same on an
idle box; this adds the under-saturation case.)

## 1-core boxed test (owner's definitive slow-vs-deadlock design)

The owner proposed a sharper test than the concurrency sweep: box hermit onto **one
core** and saturate that same core, using **CPU-time (not wall) as the discriminator** —
a merely-slow run completes once it accrues its bounded CPU-seconds even on a starved
core; a deadlock never completes at any budget. Harness
`scratch/verify-hang-repro/box2.sh` (raw `taskset -c 0`; guest = CPU-bound awk 5 M-iter
loop; competing load = N infinite burners pinned to the same core; `/usr/bin/time -v`
captures whole-tree User+System even on timeout).

| burners | core share | CPU-time (U+S) | wall | exit |
|---|---|---|---|---|
| 0 | full | 56.66 s | 82.6 s | 0 |
| 1 | ~1/2 | 57.56 s | 157 s | 0 |
| 2 | ~1/3 | 58.02 s | 188 s | 0 |
| 3 | ~1/4 | 72.59 s | 270 s | 0 |

Baseline unboxed = **3.55 CPU-s**. **Every** contention level completes. The
CPU-budget-to-complete is **bounded and ≈constant** (~57 s through 2 burners; a mild,
sub-linear creep to ~73 s at 4× oversubscription), while **wall scales with contention
(82→270 s)** — the wall growth is exactly what an observer under mass-parallel load
misreads as a "hang." A deadlock's CPU-budget would be unbounded; this is not that.

(An earlier run with 6 burners hit a **wall** cap of 300 s at only 28.8 CPU-s, EXIT 124
— a pure wall-cap artifact: at ~1/7 core it needed ~400 s wall to accrue its 57 CPU-s
and was still climbing. This is why the productionized box must budget on CPU-time, not
wall — see `ai_docs/box-requirements-for-verify-hang-repro_20260803.md`.)

### Mechanism of the ~16× 1-core CPU inflation (3.55 → ~57 CPU-s)

`strace -c` on the **supervisor** (a tracer, not itself traced, so strace attaches —
unlike the guest, which returns EPERM "already traced"), boxed to one core, 20 s window:

```
% time  calls   errors  syscall
 67.69   9317    1692    epoll_wait     (466/s; EINTR)
 32.31   8045            ptrace         (400/s)
```

In-syscall time is only **0.08 s** — so the ~56 System-seconds are **not** syscall
execution; they are **context-switch / PMU-preemption churn**. The pure-arithmetic awk
guest (≈0 syscalls of its own) is preempted thousands of times by hermit's RCB/PMU
deterministic timeslice; each preemption is a ptrace stop that, on **one** core, forces
a full supervisor↔guest reschedule. On multiple cores those overlap cheaply (3.55
CPU-s); serialized onto one core they become ~16× System-time overhead. Note
`epoll_wait` blocks with a **real timeout** (466/s), **not** timeout=0 — decisively
**not** the KVM startup busy-spin (~115 k/s); the 1692 EINTR are the reactor being
interrupted by PMU signals.

### Scope claim (kept honest)

This shows verify **does not hang — it completes with a bounded CPU-budget — even when
the supervisor and guest are forced to contend for one saturated core.** That is the
strongest form of the "under load" question a 1-core box can answer, and it comes out
**negative for deadlock**. Combined with the 300/300 concurrency drain, "hangs under
load generally" (the task title, authored on a since-refuted premise) is refuted at both
ends. The residual at 1 core is a **performance** pathology (ptrace/PMU context-switch
amplification), not a liveness or correctness bug.

## Where the incident's persistent corpses actually come from

Not ptrace verify. Keep these separate — do not conflate:

1. **Established single-run livelocks in other backends:** KVM startup busy-spin
   (established); likely the SaBRe timed-progress busy-wait
   (`memory/sabre-timed-progress-bar-verify-hard-divergence.md`, verify+sabre fails
   2/2). These *do* hang at a single run and can leave hours-old ppid=1 orphans.
2. **Over-subscription itself:** launching hundreds of concurrent verify runs is the
   problem. Remedy = admission control / serialize concurrency
   (`memory/mass-parallel-drain-saturates-github-ci.md`,
   `memory/parallel-experiment-runner-implemented-pr8.md` resource containment) — a
   **harness/orchestration** fix, not a hermit code change.

## Recommendation

Research-outcome task, no PR. Deliverable met: proved no orphaned supervisor survives
a mass-parallel verify sweep (300/300 complete; 15/15 abandoned orphans reap). No
detcore scheduler change → no core-review trigger. Coordinator to close as
research-outcome (no-fix).

## Reproduce

```bash
cd scratch/verify-hang-repro
./batch.sh 300 awk 'BEGIN{for(i=0;i<5000000;i++)s+=i; print s}'   # drain curve
./abandon_test.sh    # then SIGKILL its pid to orphan the tracked runs mid-load
ci-hub/ci-hub load-probe   # characterize box conditions (not load average)
```
