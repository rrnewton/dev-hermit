# Progress - Sunday, July 26, 2026

**Headline:** SaBRe and LiteInst began running real Reverie tools through Detcore, while KVM and DBI/DynamoRIO expanded process-tree and tool coverage.

## What shipped
- **Connected SaBRe to Detcore rather than a separate counter-only path.** Hermit now dispatches SaBRe runs through Detcore, routes time events and RDTSC through shared tools, preserves coordinator state across fork, and fails closed on RDTSC/RPC errors. Reverie SaBRe gained `noop`, `counter1`, `counter2`, strace registers, and process-tree aggregation.
- **Made LiteInst run production Reverie tools.** LiteInst now hosts tools inside the guest, supports exact example programs including `counter2`, `chunky_print`, ChromeTrace, and chaos, and isolates callback allocations. Its required compatibility floor advanced through 203 cases during the day.
- **Expanded KVM and DBI/DynamoRIO process behavior.** KVM gained more process/filesystem syscalls and production example tools. DBI/DynamoRIO registers child threads, reconnects global RPC after fork, preserves lifecycle syscalls, and runs `counter2`, ChromeTrace, chaos, and register-writing tools.
- **Moved CI execution onto the safe DAG scheduler.** Validation jobs now have explicit scheduling and bounded cleanup rather than one unstructured shell run.

## What it means
The alternative backends are no longer judged by whether they merely start a program. They are being tested against the same Reverie `Tool`, process-tree, and global-state contracts as ptrace.

## What's stuck
PMU crashes and terminal-related hangs still blocked complete validation. An audit also found tasks marked complete without landed implementation, so adversarial review and coordinator-only closure became necessary safeguards.
