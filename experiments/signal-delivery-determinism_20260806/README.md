# Signal-delivery determinism: two real passes and one vacuous one

**Task:** `signal-delivery-determinism` · **Date:** 2026-08-06 · Local, no egress.
Release hermit `worktrees/oci @ 5562161a4`, `--strict --verify` double-run, pinned env.

## Verdict

| guest | signal | native | hermit | double-run verify | cross-backend | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `sig_usr` | SIGUSR1/2 via `raise` | 5 | 5 | deterministic | **IDENTICAL** (ptrace vs e9patch) | **real pass** |
| `sig_chld` | SIGCHLD via fork/wait | 3 | 3 | deterministic | **IDENTICAL** | **real pass** |
| `sig_alarm` | SIGALRM, `ITIMER_REAL` 1 ms | 5 | **0** | "deterministic" — **VACUOUS** | n/a | **HOLE** |

## The hole

**`ITIMER_REAL`/SIGALRM is never delivered under hermit**, and `--verify` reports
`Success: deterministic` anyway — a pass over **zero** signal deliveries. A test that
cannot fail.

Three controls make this attributable:

1. **The guest genuinely exercises the dimension.** Natively the timer fires 5 times, at
   *different* iterations each run (`13328, 26151, 39659, 53374, 66902` vs
   `13485, 26105, 38879, 52384, 65768`) — real host-timing nondeterminism, which is exactly
   what determinization is supposed to remove.
2. **Hermit delivers zero.** Two hermit runs: `final ticks=0`, identical — trivially.
3. **The benign explanation is refuted.** "The loop is too short in virtual time" would
   excuse it. Measured over the *same* 200 000-`getpid` loop:
   **virtual elapsed = 52.011 ms under hermit** (13.576 ms native). A 1 ms *repeating*
   timer was overdue by ~50×; the guest caps at 5 ticks and would have hit the cap easily.

So virtual time is advancing fine — **the defect is that `ITIMER_REAL` expiry is not wired
to it.**

## Explicitly not the fix (#140)

Do **not** address this by coarsening, freezing, or rounding virtual time. Virtual time is
behaving correctly here (52 ms elapsed, monotonic, continuous). The gap is one-directional:
timer expiry must become a deterministic function of the virtual clock that already exists.

## What the two real passes establish

SIGUSR1/2 and SIGCHLD deliver the same *number* of signals as native, in an order that is
identical across a double run **and identical between ptrace and e9patch**. So the
synchronous/`raise` and child-exit signal paths are deterministic and backend-stable. The
hole is specifically the **timer-driven** path.

## Limitations

- Three guests, one timer interval (1 ms), one loop shape.
- ~~`sig_alarm` was **not** run under e9patch (the run exceeded a 10-minute wall)~~ —
  **RETIRED 2026-08-07, see the addendum below.** It runs under e9patch in ~21 s, and the
  hole is now established on e9patch as well as ptrace.
- Only `ITIMER_REAL` was tested — `ITIMER_VIRTUAL`, `ITIMER_PROF`, `timer_create`,
  `timerfd`, and `alarm()` were not.
- No detlog comparison for the signal guests; the ordering evidence here is guest stdout.
- The first version of `sig_alarm` was itself vacuous (0 ticks **natively** too — the loop
  outran its own 20 ms timer). Recorded because it is the same failure mode as the finding:
  a signal test that never delivers a signal looks like a pass.

## Addendum 2026-08-07 — the 10-minute wall retired, and what actually caused it

Re-measured on hermit `0.2.0 (2026-08-06, ga50f5eb1c917)`, release, guest rebuilt from the
`sig_alarm.c` in this directory, `--strict --verify` double-run, `--base-env minimal` under
`env -i`, box at loadavg 49.

**The 10-minute wall does not reproduce.** Both backends finish in ~21 s of wall — roughly 30×
under the limit that was recorded as a blocker:

| backend | wall | user | sys | cpu | CPU total | DETLOG compared |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ptrace | 19.79 s | 4.51 | 15.14 | 99% | 19.65 s | 400090 |
| e9patch | 20.81 s | 4.36 | 16.44 | 99% | 20.80 s | 400090 |

**Classification.** `wall == CPU` at 99% on both, so a core is genuinely burned: this is
CPU-bound, **not** a stalled wait. It terminates and `--verify` succeeds, so it is **not** a
livelock either. `sys` dominates `user` 3.4–3.8 : 1, placing the burn in kernel-side ptrace
stop traffic.

**e9patch is not implicated, by construction.** Its own banner reports `candidate_sites=0`,
`mapped_sites=0`, `b0_sites=0`, `artifact_sha256=none`, `preprocess_us=2763` — **0 of 0 sites
rewritten**, so the "e9patch" run is the ptrace runtime plus 2.8 ms of preprocessing. The two
backends compare an identical 400090 DETLOG messages and their CPU totals differ by 1.06×,
which is contention, not behaviour. (Same family as the SaBRe `patched_sites=0` silent
ptrace-fallback finding.)

**What the wall cost actually is: the hole eating its own tail.** The guest loop is
`for (witness = 0; witness < 200000 && ticks < 5; witness++) getpid();`. Natively SIGALRM
fires, `ticks` reaches 5 and the loop exits early — measured at iteration **74564**, 37.3 % of
the cap. Under hermit SIGALRM is never delivered, `ticks` stays 0, the early-exit predicate
never fires, and the loop runs the **full 200000** iterations, each paying a ptrace syscall
stop, doubled again by the verify double-run.

Recompiling the identical guest with the cap lowered to the native exit point isolates it, and
**three independent measures agree to within 0.5 %**:

| measure | 200000-cap | 77078-cap | ratio | predicted 200000/77078 |
| --- | ---: | ---: | ---: | ---: |
| CPU time (contention-immune) | 19.65 s | 7.60 s | 2.586× | 2.595× |
| DETLOG messages compared | 400090 | 154246 | 2.594× | 2.595× |

So the missing `ITIMER_REAL` delivery is itself responsible for ~2.6× of this test's cost.
Fixing the hole shortens the test by that factor as a side effect. **No timeout was raised.**

**Why the original observation is still credible.** This workload swings widely on load alone,
and one of two attempts at the 77078-cap control exceeded a 600 s wall producing zero output
before it was bounded, while the immediate retry finished in 7.61 s. That one-off was not
root-caused. A ~20 s job needs only ~30× contention to cross a 600 s wall, and the original run
was taken while the full experiment fleet was active.
