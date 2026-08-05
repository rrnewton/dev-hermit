# Progress - Saturday, August 1, 2026

**Headline:** The ptrace notifier regression that slowed the QEMU/BusyBox demo from 46-47 seconds to 345-373 seconds was fixed, and Hermit added measured L2 comparisons across ptrace, DBI/DynamoRIO, and KVM.

## What shipped
- **Restored the QEMU/BusyBox demo to 46-47 seconds.** Reverie's ptrace notifier had begun rereading `/proc/<pid>/stat`, `/proc/<pid>/status`, and pidfd identity on every stop. Reusing the already-live pidfd reduced a 100,000-`getpid` workload from 30.5-32.5 seconds to 3.43 seconds (about 8.9x) and restored the demo from 345-373 seconds to 46-47 seconds. The notifier suite passed 93 tests and the LiteInst hybrid race suite passed 20.
- **Added an honest L2 backend matrix.** ptrace, the golden reference, passed 23/23 contracts with bitwise DETLOG agreement. DBI/DynamoRIO passed 21/23 with bitwise DETLOG agreement; its recorded gaps were `exit_status` and `pthread_lifecycle`. KVM passed 21/23 at the weaker guest-visible stdout/exit level; its recorded gaps were `process_wait_accounting` and `process_wait_lifecycle`.
- **Promoted four named concurrency tests to required status.** `determinism-stress-c/lock-free`, `pid-tid`, `signal-order`, and `pipe-prefill` now block CI on ptrace strict verification. The generated E2E plan grew from 47 to 51 required verify/ptrace cells.
- **Fixed concrete backend identity leaks.** DBI/DynamoRIO no longer exposes the host FQDN through `uname`; SaBRe aligns root process identity with ptrace; socket cookie identity is stable across backends; and LiteInst reports patch-site instruction statistics.

## What it means
Hermit's execution mechanisms are named and measured separately: ptrace is the golden reference; DBI/DynamoRIO and KVM are compared against it; SaBRe, LiteInst, and e9patch have their own explicit corpora. A pass at stdout/exit level is not reported as full DETLOG parity.

## What's stuck
DBI/DynamoRIO still had two L2 gaps, and KVM's 21/23 result was guest-visible rather than bitwise DETLOG parity. SaBRe, LiteInst, and e9patch were not part of this 23-contract L2 comparison, so no broader parity claim is made.
