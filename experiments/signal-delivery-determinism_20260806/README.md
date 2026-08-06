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
- `sig_alarm` was **not** run under e9patch (the run exceeded a 10-minute wall); the hole is
  established on ptrace only, though there is no reason to expect e9patch to differ since it
  runs the ptrace runtime.
- Only `ITIMER_REAL` was tested — `ITIMER_VIRTUAL`, `ITIMER_PROF`, `timer_create`,
  `timerfd`, and `alarm()` were not.
- No detlog comparison for the signal guests; the ordering evidence here is guest stdout.
- The first version of `sig_alarm` was itself vacuous (0 ticks **natively** too — the loop
  outran its own 20 ms timer). Recorded because it is the same failure mode as the finding:
  a signal test that never delivers a signal looks like a pass.
