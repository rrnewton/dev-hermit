# Weekly Progress - Monday, July 27 through Sunday, August 2, 2026

**Headline:** Hermit moved from backend bring-up to measured parity: QEMU/BusyBox returned to 46-47 seconds, ptrace reached 23/23 L2 contracts, and the shared manifest began enforcing exactly which programs each backend runs.

## What shipped
- **Made the Linux/QEMU target real and repeatable.** BusyBox userspace and in-VM networking ran under strict Hermit. A ptrace notifier regression that stretched the demo to 345-373 seconds was fixed, restoring 46-47-second boots.
- **Measured backend parity without flattening assurance levels.** ptrace passed 23/23 contracts with bitwise DETLOG agreement; DBI/DynamoRIO passed 21/23 at that level; KVM passed 21/23 at stdout/exit level. SaBRe, LiteInst, and e9patch gained named corpora rather than being folded into those numbers.
- **Made the E2E manifest load-bearing.** One schema now defines build buckets, run nodes, backend arguments, generated plans, and required cells. Four concurrency programs - `lock-free`, `pid-tid`, `signal-order`, and `pipe-prefill` - became required ptrace checks.
- **Improved real backend cost and observability.** e9patch warm preprocessing dropped from 53.4-70.1 ms to 1.9-2.0 ms for a tiny static guest and from about 1.9 seconds to about 0.9 ms for a 24 MiB guest. SaBRe, LiteInst, and DBI/DynamoRIO gained typed statistics providers.
- **Expanded deterministic system behavior.** Kernel UUIDs, socket identities, scheduler/procfs counters, child-exit SIGCHLD timing, random streams, and process-tree virtual time gained explicit deterministic models.

## What it means
The project now separates three claims that had previously blurred together: a program passes strict verification under ptrace; another backend matches stdout/exit; another backend matches the full DETLOG. That vocabulary makes coverage growth auditable.

## What's stuck
DBI/DynamoRIO retained `exit_status` and `pthread_lifecycle` gaps. KVM retained process-wait gaps and lacked bitwise DETLOG evidence. LiteInst multiprocess execution and the Redis blocking-`wait4` liveness case remained open.
