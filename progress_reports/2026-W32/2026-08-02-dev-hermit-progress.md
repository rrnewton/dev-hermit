# Progress - Sunday, August 2, 2026

**Headline:** e9patch warm preprocessing fell from 53.4-70.1 ms to 1.9-2.0 ms on a tiny guest, while LiteInst and KVM gained named parity cells and SaBRe, LiteInst, and DBI/DynamoRIO gained typed statistics paths.

## What shipped
- **Made e9patch warm starts measurably cheaper.** Memoizing the 935 KiB `e9tool` and 159 KiB `e9patch` digests reduced a tiny static guest's warm preprocessing from 53.4-70.1 ms to 1.9-2.0 ms (about 28x), with identical artifact SHA-256, 12/12 L2 corpus cells, and 15/15 unit tests. Extending the memo to a 24 MiB guest and rewrite artifact reduced that warm path from about 1.9 seconds to about 0.9 ms (about 2,000x).
- **Expanded LiteInst parity by exact contract.** Required cells now cover socket timestamps, socket cookies, netlink and Unix autobind, clocks, POSIX timers, incoming CPU identity, filesystem-handle refusals, and record/getpid. The ratchet explicitly targets green single-process cells rather than implying multiprocess support.
- **Added typed backend statistics.** SaBRe, LiteInst, and DBI/DynamoRIO now report backend-specific measurements through typed providers; Hermit exposes DBI native statistics through `hermit run --summary`.
- **Added KVM process and descriptor parity.** KVM gained deterministic root `getppid`, terminal-query ioctls, and `close_range`, each with end-to-end tests or audit tags.
- **Made child-exit scheduling reproducible for `make -j8`.** SIGCHLD delivery is tied to scheduler-ordered child exit time. Three runs produced identical 38,582-line DETLOGs with 80 gcc and 27 cc1 executions and 43 deterministic child reaps.

## What it means
The e9patch result is tied to exact files and absolute times, not an anonymous "backend warm-up." The typed statistics work also gives SaBRe, LiteInst, and DBI/DynamoRIO a common way to explain where instrumentation time and coverage go.

## What's stuck
The child-exit change does not fix the separate Redis blocking-`wait4` liveness problem. KVM still had stdout differences from the ptrace/DBI/SaBRe reference group, and LiteInst's multiprocess architecture remained unfinished.
