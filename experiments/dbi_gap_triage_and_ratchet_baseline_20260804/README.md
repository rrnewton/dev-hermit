# DBI/DBT compat: 22-gap triage + 86-cell ratchet baseline

**Question (owner):** of the manifest-DBI-disabled c-programs cells, which are
disabled for a **real reason** vs **never re-tried**? State the denominator.

## Denominators (measured, `c-programs.toml`, 159 cells)
- manifest-DBI-**enabled** = 44 (all pass; manifest does not overclaim)
- manifest-DBI-**disabled** = **115** ← the population the question is about
  - 7 ptrace itself does not pass `--strict --verify` → not DBI's problem, excluded
  - **108** meaningful disabled cells (ptrace passes, DBI could in principle)
    - **86 PASS** under DBI when force-probed  → *disabled-but-passing*
    - **22 FAIL** under DBI when force-probed  → *the gaps*

## Answer
**Of the 22 gap cells: 22 disabled for a real reason, 0 never re-tried.**
Every one of the 22 was re-run **isolated serial** at current main (removing the
B3 sweep's parallelism=8 self-contention confound) and **reproduced its exact
failure** — including the 4 status-124 cells, which hang even isolated (real DBI
in-process no-timer-preemption limit, not load). See `gap-triage-22.json`.

The "**never re-tried**" population is a *different* set: the **86**
disabled-but-passing cells. They were disabled conservatively in the manifest and
never re-enabled. **All 86 re-confirmed PASS at current main** (`e8a0d8d3`,
isolated serial, WITHOUT PR #1200) → the ratchet is real and **not #1200-gated**.
See `ratchet-candidates-86-atmain.json`. This is the landable compat ratchet:
DBI manifest coverage 44 → up to 130 enabled (nearly 3×).

## The 22 real-reason gaps, classified (all reproduce isolated at main)
| Class | N | Cells | Disposition |
|---|---|---|---|
| Structural / by-design | 2 | dbi-unsupported-syscall (status 101, correct fail-closed negative test), record-replay-lseek-seek-cur (record/replay unsupported by DBI) | never counts; fold out |
| Preemption-ceiling hang (status 124, hangs isolated) | 4 | fp-reduction-nondeterminism, pselect6-simulation, sigtimedwait-no-timeout, writev-determinism | known DBI no-timer-preemption limit; safe-point branch-count work (reverie #294) partial |
| KVM-parity socket canonicalization (status 1) | 5 | so-incoming-cpu-tcp4, so-incoming-cpu-tcp6, tcp-info-accept4, tcp-info-accept6, tcp-info-client4 | PORT from KVM #345/#350 (mirror settled semantics; shared-vs-per-backend under investigation) |
| ptrace-on-guest (status 1) | 2 | ptrace-attach-eperm, ptrace-seize-eperm | DBI cannot emulate ptrace-on-guest EPERM parity |
| Execution error (status 2) | 3 | epoll-determinism, mmap-determinism, thread-sync-determinism | real exec error; needs per-cell root-cause |
| Misc verify divergence (status 40/1) | 6 | arch-prctl-determinism (40), get-robust-list-child, pidfd-waitid-child, proc-locks, resource-determinism, sigpipe-siginfo | real determinism gap; per-cell root-cause |

## Caveats
- **CI-load flakiness:** several of the 86 are timing-sensitive under load
  (netns-cookie-udp4 15.7s, sysinfo-uptime 11.7s, netns-cookie-tcp6 10.3s in B3).
  A blind manifest flip of all 86 into the loaded e2e gate risks flakes; ratchet
  must stage robust cells first and hold/mark-nongating the timing-sensitive tail.
- Ratchet numbers are `verify --strict` (the unrelaxed bar). At-main = e8a0d8d3.
