# Progress - Thursday, July 30, 2026

**Headline:** Backend work shifted from short utilities to JVMs, compilers, multithreaded `exec`, and KVM thread ownership, with measurements added to show which tests buy the most coverage.

## What shipped
- **Added small, bounded JVM determinism tests.** Java runtime and threading cases now run with RCB preemption without requiring an unbounded application workload.
- **Made SaBRe handle harder process and network behavior.** Fixes cover multithreaded `exec`, compiler workloads, loopback-poller fairness, and loader argument handling.
- **Made KVM thread ownership explicit.** Reverie replaced a boolean with the `ThreadOwnership` model, dispatches `CLONE_THREAD` workers through the selected `Tool`, and asserts that execution ownership matches futex ownership.
- **Hardened DBI/DynamoRIO and LiteInst.** DBI/DynamoRIO now determinizes RDTSC/RDTSCP in its client. LiteInst preserves CPUID/TSC policy around patch helpers, fails closed without runtime activation, and gained Python/semantic command coverage.
- **Added a per-test code-coverage harness.** Tests can now be compared by the Hermit code they exercise, supporting coverage-per-second decisions rather than suite-size guesses.

## What it means
The named backends are being tested against the concurrency and JIT/runtime behavior that distinguishes real applications from command-line smoke tests.

## What's stuck
The QEMU Linux demo remained sensitive to virtual-time and scheduler behavior. LiteInst's temporary ptrace-hosted runtime and KVM's thread-ownership split exposed architecture that still needed consolidation.
