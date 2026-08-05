# Progress - Tuesday, July 21, 2026

**Headline:** Hermit moved onto the maintained `rrnewton/hermit` fork, restored record/replay and strict-mode command coverage, and split ordinary CI from the CPUID/PMU checks that require real hardware.

## What shipped
- **Made public CI useful on both hosted and self-hosted machines.** Hosted jobs now cover the workspace, while focused CPUID and PMU checks run on the trusted hardware runner. Mount-namespace-dependent tests detect when that capability is absent instead of quietly reporting full coverage.
- **Ported major behavior checks into the Cargo test suite.** Record/replay, chaos scheduling, signals, pthread synchronization, deterministic `mmap` addresses, CLI parsing, and verifier behavior gained permanent tests. The replay fix for `find` also repaired an `fchdir` desynchronization.
- **Established concrete alternatives to ptrace.** Reverie gained its first KVM syscall-interception prototype, and the SaBRe backend received a written determinism-gap analysis. These are named alternatives to the ptrace golden reference, not generic "faster engines."

## What it means
Hermit can now distinguish portable regressions from hardware-only failures, and record/replay behavior is checked by the same public workflow as ordinary execution. KVM and SaBRe also have explicit starting points for work that ptrace cannot do, especially CPUID interception.

## What's stuck
The hardware runner and mount namespaces are still required for the complete suite. KVM was only a syscall-interception prototype, and SaBRe's synchronous execution model still needed architectural review before it could claim parity with ptrace.
