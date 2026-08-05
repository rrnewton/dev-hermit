# Progress - Monday, July 27, 2026

**Headline:** Real Linux userspace ran inside QEMU under Hermit strict verification, while LiteInst, KVM, DBI/DynamoRIO, and SaBRe expanded their required behavior substantially.

## What shipped
- **Ran a real userspace program inside the strict QEMU-under-Hermit VM.** The QEMU path also gained an in-VM network determinism test and a smaller default device surface, turning the Linux-boot goal into executable coverage.
- **Removed host randomness and telemetry from guest-visible behavior.** Hermit now determinizes `/proc/sys/kernel/random/uuid`, Unix socket identities, scheduler accounting, RTC attributes, Btrfs counters, and DBI root random sources.
- **Advanced all major alternative backends.** LiteInst's required compatibility floor rose from 230 to 855 cases; KVM gained concurrent guest workers and shared-tool process-tree support; DBI/DynamoRIO reconnected coordinator state after fork; SaBRe improved signal, exit, `exec`, and inherited-descriptor behavior.
- **Added bounded CI and dependency checks.** Layered timeouts, runner health checks, compatibility summaries, and pinned Reverie revisions made failures more attributable and builds reproducible.

## What it means
Hermit is now testing its long-term target - a Linux system under QEMU - using the same strict determinism machinery as ordinary binaries. At the same time, each named backend has a growing, enforceable floor rather than an informal feature list.

## What's stuck
Self-hosted CI remained unreliable, with cancelled jobs and dependency-pin churn. KVM and LiteInst breadth was growing quickly, but exact cross-backend DETLOG equivalence was not yet established.
