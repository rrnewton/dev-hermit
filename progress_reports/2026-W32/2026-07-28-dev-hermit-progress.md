# Progress - Tuesday, July 28, 2026

**Headline:** CI gained a centralized, load-bearing test manifest, record/replay reached an honest 139-program green set, and QEMU booted BusyBox userspace under Hermit.

## What shipped
- **Made one manifest define what CI actually runs.** The schema-v2 E2E manifest now drives build buckets, run nodes, generated plans, backend arguments, and DAG correspondence checks. Unregistered or stale test definitions fail validation instead of disappearing between scripts.
- **Recorded an honest 139-program record/replay envelope.** The ratchet includes a replay `lseek` fix and reports remaining gaps rather than counting skipped programs as passes.
- **Booted BusyBox userspace through QEMU under strict Hermit execution.** QEMU models and PMU calibration are pinned, and the resulting Linux path has explicit L2 diagnostics.
- **Expanded backend-specific determinism.** DBI/DynamoRIO qualified 44 additional C cases and stabilized child identity, stdin, clocks, and random streams. KVM gained sockets, timers, robust lists, random-device seeding, and fork coordination. SaBRe and LiteInst gained lifecycle and random-stream fixes; e9patch gained its shared in-guest preload/tool path.

## What it means
The test inventory is now executable configuration rather than documentation. KVM, DBI/DynamoRIO, SaBRe, LiteInst, and e9patch cannot claim coverage unless each claimed cell is registered, built, run, and represented in the generated plan.

## What's stuck
The CI overhaul still had four failing checks out of 25 at the day's measured checkpoint. KVM, DBI/DynamoRIO, SaBRe, LiteInst, and e9patch each had different remaining lifecycle and output-parity gaps.
