# Progress - Wednesday, July 29, 2026

**Headline:** Deterministic chaos schedules became replayable at exact branch boundaries, while SaBRe gained enough process-tree correctness to run `make` and the dynamic linker.

## What shipped
- **Added deterministic chaos slowdown epochs.** A chosen slowdown schedule can now be replayed at exact retired-branch boundaries, making a discovered concurrency schedule reproducible instead of a seed-only lottery.
- **Improved child-exit behavior for parallel builds.** SIGCHLD admission and `pselect6` masks were determinized for `make -jN`, then SaBRe's child-exit timer ordering, inherited pipes, fork/exec pipe state, and threaded lifecycle were fixed.
- **Raised SaBRe application coverage to real build tools.** Required tests now include `make` and the dynamic linker, not only short utilities.
- **Closed CI-overhaul audit gaps.** The centralized test plan and demo builds no longer depend on DBI source being present when that backend is not under test.

## What it means
Hermit can preserve a specific concurrency perturbation and replay it, while SaBRe is being tested on programs whose correctness depends on child processes, signals, and inherited descriptors.

## What's stuck
The Linux/QEMU demo still had a scheduler and virtual-time regression under load. SaBRe's improved application results did not establish that every syscall stayed on its injected fast path rather than falling back to ptrace.
