# Progress - Wednesday, July 22, 2026

**Headline:** CPUID handling made KVM and DynamoRIO necessary rather than optional, while Hermit's strict, record/replay, and fail-closed test coverage expanded across real language runtimes and services.

## What shipped
- **Added two concrete CPUID-capable execution paths.** Reverie KVM gained the shared `Tool` API and host-independent CPUID filtering; Reverie DBI/DynamoRIO gained a working prototype and CPUID interception. The ptrace backend cannot trap CPUID on this AMD host, so these paths close a real strict-mode gap.
- **Expanded strict verification to Python, Ruby, OCaml, Redis, SQLite, Java, Node.js, curl, and nginx.** The work included deterministic Python hash ordering, scheduler tests for Ruby threads, Redis process ownership, SQLite descriptor passing, JVM futex-timeout rebasing, and Node.js epoll/FIONBIO replay handling.
- **Turned fail-closed syscall handling into a tested contract.** The enabled fail-closed test set grew from 3 to 69 cases, and the ported rr syscall suite plus the backend-parity matrix now expose unsupported operations instead of silently passing them through.

## What it means
Hermit's execution modes are now tested with programs that exercise interpreters, services, event loops, and process trees. KVM and DBI/DynamoRIO are specifically named because they cover the CPUID instruction surface that ptrace cannot observe.

## What's stuck
KVM remained an early executor rather than a general ELF backend, and DBI/DynamoRIO still needed lifecycle and application-syscall work. The full rr-derived test corpus had been imported, but many cases were not yet green.
