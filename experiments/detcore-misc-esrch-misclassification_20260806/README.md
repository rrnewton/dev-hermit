# safeptrace classifies a live tracee as dead: ESRCH carries no liveness information

**Task:** `detcore-misc-residual-passive-block-1-in-2760`
**Date:** 2026-08-06 (PT)
**Kind:** measurement only. No product code was changed, and no oracle was weakened.

## Question

`safeptrace/src/lib.rs` `map_err` turns **every** `ESRCH` into
`Error::Died(Zombie::…)`. Its own doc comment lists three causes of `ESRCH` —

1. the process was stopped and died unexpectedly,
2. the process is not currently being traced by the caller,
3. **the process is not in a stopped state** —

and then folds all three into "died", on the argument that (2) and (3) "only
occur due to programmer errors that this API is designed to prevent".

The residual `detcore_misc` hang is filed at **~1 hang per 2,760 runs**, and the
task's stated acceptance bar is a stress lottery: *"at 1/2760 you need on the
order of thousands of runs to distinguish 'fixed' from 'got lucky'."*

Two questions, then:

- **Q1.** Is that argument true? Can `ESRCH` be observed for a tracee that is
  provably **alive**?
- **Q2.** If it can, does `ESRCH` still carry *any* information about liveness —
  i.e. is the `Died` classification merely imprecise, or is it unfounded?
- **Q3.** Which candidate discriminator actually separates the cases?

## Method

Three freestanding C probes using `ptrace(2)` directly. **No Hermit, no Reverie,
no Detcore** — the claim under test is about the kernel interface `map_err`
consumes, so testing it through the whole stack would only add confounders.

Each trial establishes a tracee in a known state, records ground truth
independently of the syscall under test, then measures.

| case | construction | ground truth |
| --- | --- | --- |
| `running` | `fork`, `PTRACE_TRACEME`+`SIGSTOP`, parent waits the initial stop, `PTRACE_SETOPTIONS`, `PTRACE_CONT`, child spins in a loop | **alive** |
| `dead` | same, but the child `_exit(0)`s and the parent `waitpid`s it to completion | **dead** (reaped) |
| `unreaped_zombie` | `fork`, child `_exit(0)`, parent deliberately does **not** `waitpid` | **dead** (zombie) |

Measured per trial: `ptrace(PTRACE_GETEVENTMSG)` return and `errno`;
`kill(pid, 0)` as a candidate discriminator; the state character from
`/proc/<pid>/stat` as the other candidate discriminator.

Ground truth is fixed by construction, never inferred from the values being
tested — that is what makes this a bracket rather than a description.

- `esrch_matrix.c` — the `running` and `dead` cases, 40 trials each
- `zombie_probe.c` — the `unreaped_zombie` case, 40 trials

## Results

**n = 120 trials** (40 per case). `results.csv` holds every row.

| case | ground truth | `ESRCH` | `kill(pid,0)` says alive | `/proc` state | `/proc` correct |
| --- | --- | --- | --- | --- | --- |
| `running` | alive | **40/40** | 40/40 ✓ | `R` ×40 | **40/40** |
| `dead` | dead | **40/40** | 0/40 ✓ | gone ×40 | **40/40** |
| `unreaped_zombie` | dead | — | **40/40 ✗** | `Z` ×40 | **40/40** |

### A1 — Yes, and deterministically

`ESRCH` on a provably-alive tracee reproduced **40 out of 40**. Cause (3) is not
a programmer error the API prevents; it is ordinary kernel behaviour for any
tracee that is running rather than sitting in a ptrace-stop.

### A2 — `ESRCH` carries *zero* liveness information

This is the load-bearing number. `ESRCH` occurred **40/40 when the tracee was
alive and 40/40 when it was dead**. The rates are identical, so observing
`ESRCH` does not shift the probability that the tracee died. The `Died`
classification is therefore not an approximation that is usually right — it is
**unfounded on the evidence it keys on**.

### A3 — `kill(pid,0)` is disqualified; `/proc/<pid>/stat` discriminates

`kill(pid, 0)` reports a dead process as alive in **40/40** unreaped-zombie
trials, because a zombie is still a signalable PID. The `/proc/<pid>/stat` state
character gave the correct dead/alive answer in **120/120** trials across all
three cases (`R` alive, `Z` dead, absent dead).

## Interpretation

**The misclassification is deterministic; only reaching it is rare.** The
~1/2,760 figure measures how often Hermit's scheduler drives a tracee into the
window where `map_err` is consulted on a live tracee. The classification defect
underneath it does not have a 1/2,760 rate — it has a 40/40 rate whenever the
tracee is running.

**That retires the stated acceptance blocker.** The stress-lottery bar is the
right regression sweep for the end-to-end hang, but it is the wrong *acceptance*
gate for this defect, because the defect is bracketable deterministically at the
unit layer. A both-direction test belongs in `safeptrace`'s existing fork-based
`#[cfg(test)]` module and should be **red against current `main`** on the alive
direction. That is a strictly stronger oracle than a ratio, not a weaker one.

**Why the hang follows.** `Zombie::reap` (`lib.rs:1133`) awaits `next_state()`
in a loop. `Zombie` wraps a `Running`, and `resume()` exists only on `Stopped`,
so once a live tracee has been classified `Died` there is no capability to drive
it to the exit `reap` is waiting for. It blocks forever. This is why the
drive-to-exit approach was correctly abandoned: the wrong site was the resume,
not the classification.

**Discriminator recommendation, now measured rather than proposed.** Use the
`/proc/<pid>/stat` state character. `kill(pid,0)` is disqualified above.
`waitpid(pid, WNOHANG)` must not be used here: it would consume a wait status
the notifier layer owns. The `/proc` read has no side effects and its race
resolves in the safe direction — if it reads "alive" and the tracee dies an
instant later, the caller gets an `Errno` instead of `Died`, retries, and
observes the real death; the converse (reading zombie/absent for a live process)
cannot occur.

## Scope and limits

- Measures the **kernel interface**, not `safeptrace` itself. It establishes that
  `map_err`'s premise is false; it does not by itself prove every path into
  `map_err` can reach the live case.
- One host, one kernel (see `metadata.json`). `ESRCH`-on-running is specified
  behaviour, not host-specific, but the rate columns are from this host.
- The `running` case exercises doc-comment cause **(3)**. Cause (2),
  not-traced-by-caller, was not constructed.
- **No fix is implemented and no fix is validated here.** Changing what `ESRCH`
  means is a core-semantics change: `map_err`/`map_nix_err` has 25 call sites in
  `safeptrace` alone and `Error::Died` has 13 consumers across the Reverie
  workspace (`reverie-ptrace/src/task.rs` 4, `safeptrace/src/lib.rs` 3,
  `reverie-ptrace/src/tracer.rs` 3, `safeptrace/src/notifier.rs` 2,
  `reverie-ptrace/src/gdbstub/response.rs` 1). It requires the full core-change
  protocol plus dual claude+codex adversarial review.

## Reproduction

```bash
cc -O1 -std=gnu11 -Wall -Wextra -Werror -o esrch_matrix esrch_matrix.c
cc -O1 -std=gnu11 -Wall -Wextra -Werror -o zombie_probe zombie_probe.c
./esrch_matrix 40      # csv: case,ptrace_rc,errno,alive_by_kill,proc_state,discriminated
./zombie_probe 40      # csv: case,alive_by_kill,proc_state,kill_would_misclassify
```

Expected: `running` and `dead` both report `errno=3` (`ESRCH`) on every trial;
`unreaped_zombie` reports `alive_by_kill=1` with `proc_state=Z` on every trial.

## Next steps for the implementer

1. Land the both-direction unit test **first**, red against `main` on the alive
   direction. This converts the item from a 1/2,760 lottery into an ordinary
   failing test.
2. Then implement the `/proc`-state discriminator at `map_err`, auditing all 25
   `map_err`/`map_nix_err` sites and all 13 `Error::Died` consumers for callers
   that depend on `Died` being returned for a live tracee.
3. Keep the 60-round matched-load stress from
   `experiments/multisect_detcore_misc_20260803/` as the end-to-end regression
   sweep and **report the ratio**, but it is no longer the acceptance gate.
4. Full core-change PR sections plus dual claude+codex review.
5. `detcore-wait4-nondelivery-sigkilled-child` ("Face B") may share this root
   cause; check whether it routes through `map_err` or `Error::Died`.
