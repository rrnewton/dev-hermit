# Progress - Thursday, July 23, 2026

**Headline:** Hermit's KVM and DBI/DynamoRIO backends began running through the same Reverie `Tool` interface as ptrace, and a blocking `ppoll` design bug was fixed at the subscription layer.

## What shipped
- **Connected DBI/DynamoRIO and KVM to real Hermit execution.** DBI dispatch now runs through `reverie_dbi::DbiRunner`, while KVM drives a `KvmGuest` through `run_with_tool` and supports filesystems, multiple programs, pipes, and process execution.
- **Fixed blocking-I/O determinism at its source.** Reverie subscriptions now default to `Subscription::all`, and `ppoll` gained a deterministic handler. Internal pipes are classified as `InternalIOPolling`, including record/replay data capture, instead of being mistaken for host-blocking I/O.
- **Improved process and replay correctness.** The day fixed record-mode pipe deadlock, clone-stack environment corruption, `getsockopt` null buffers, and explicit syscall classification. Standard-command strict verification and KVM stdin-flag coverage were added alongside the fixes.

## What it means
The faster backends are starting to share Hermit's real determinization logic rather than running disconnected demos. The `Subscription::all` change is especially important: a forgotten syscall now fails closed instead of bypassing the scheduler's model.

## What's stuck
KVM and DBI/DynamoRIO still covered only a subset of ptrace behavior. SaBRe and LiteInst were not yet integrated deeply enough to run the same tool and lifecycle contracts.
