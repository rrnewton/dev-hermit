# DBI full-corpus L1-parity / L2-det sweep — real data + root-caused B4 gaps

**Date:** 2026-08-01
**Lane:** `dbt-corpus-round-nongated-3` (impl agent opus-4.8)
**Hermit:** `13f3ee680d81353a53e019fc2c92101a010a2357` (branch `dbt/pidfd-open-self`
= origin/main `82a8e853` + pidfd fixture), DBI backend (release + DynamoRIO).
**Producer:** `compat-envelope/collect-dbi-corpus.rs`
**Data:** `compat-envelope/ignored/dbi-corpus-scorecard.csv` (202 dbi + 202
ptrace-ref rows). Ingest handoff: `compat-envelope/DBI-CORPUS-INGEST.md`.

## Question

How does the DBI backend do against the golden ptrace reference across the
**entire** e2e corpus (202 tests, 13 buckets) — not just the manifest cells that
already enabled DBI — and where is the real work toward B4 (100% parity)?

## Method

Reconstruct every corpus guest and run ptrace + DBI directly, independent of the
manifest enable flags. **Parity measured at L1** (`--strict`, both backends pass
stdout through — a valid cross-backend byte comparison); **determinism measured
separately at L2** (`--strict --verify`, per-backend self-check). Under `--verify`
ptrace consumes guest stdout internally while DBI passes it through, so
cross-backend `--verify` stdout diffs are a plumbing artifact — hence the split.
Portable profile `--strict --no-virtualize-cpuid --max-timeslice=disabled`,
`LC_ALL=C TZ=UTC`, 150s timeout, process-group-reaped children.

## Headline (bind to SHA `13f3ee68`, DBI backend)

- **L1 parity vs ptrace golden: 155/202 = 76.7%** (robust, single-run signal).
- **B4 (L1 parity + L2 det): 135/202 = 66.8%.**
- Strongest buckets: `c-programs` 129/159 L1-parity (118 B4), `system-utils`,
  `backend-parity-c`, `applications`, `chaos-c`, `debugger-c` at/near full.
- Weakest: `shared-futex-c` (0/4 B4 — threaded/QEMU), `data-handling` (0/2 B4),
  `language-runtimes` (1/6 — subprocess/time), `util-c` (pmu-skid).

**Host caveat:** sweep ran on loaded devbig014. `--verify` is timing-sensitive;
`bin-c/posix-timer-test` recorded det=1 in the sweep but took >120s to `--verify`
afterward. Treat L2/det as host-sensitive; L1 parity is the trustworthy number.
See `demo5-*` / `stress-load-guardrail` memories for the shared-host load regime.

## Gap taxonomy (honest — no false parity, #152)

### A. Structural DBI L1 hangs (no-preemption / subprocess) — 7 `gap` cells

`bin-c/robust-futex-test`, `c-programs/sigtimedwait-no-timeout`,
`c-programs/writev-determinism`, `determinism-stress-c/mmap-fork-shared`,
`language-runtimes/{bash-loop-pipe-time,python-io-subprocess-time}`,
`system-utils/date-nanoseconds`. These L1-timeout at 150s. Root cause is the
known DBI no-working-timer-preemption limitation (shipped default-off, in-process
re-entrancy blocker) plus multiprocess/subprocess handling — see memories
`dbi-preemption-in-process-reentrancy-blocker`,
`dbi-corpus-hangs-preemption-ceiling-exit-group-teardown`. Not new; not a quick
fix.

### B. DBI L1 diverges-in-error from ptrace — 17 `gap` cells

Mostly `rc=1` mismatches: `get-robust-list-child`, `nanosleep-threads-simple`,
`pidfd-waitid-child`, `pselect6-simulation`, `ptrace-{attach,seize}-eperm`,
`resource-determinism`, `sigpipe-siginfo`, `tcp-info-{accept4,accept6,client4}`,
`pipe-chain`, `thread-output`, `perl-io-subprocess-time`, `qemu-net-init`; plus
`arch-prctl-determinism` (rc=40) and `dbi-unsupported-syscall` (rc=101, expected
— that guest asserts the lone Unsupported syscall). Network/ptrace/robust-list
under DBI are the recurring themes.

### C. DBI deterministic but DIVERGES from ptrace golden — 23 `parity-gap` cells

**This is the real B4 backend-parity work.** All are det=1, dbi_rc=0, but guest
output differs from ptrace. Concrete L1 diffs captured:

1. **`c-programs/uname` — real host FQDN leak (ROOT-CAUSED, fixable).**
   - ptrace: `Node name: hermetic-container.local`
   - DBI:    `Node name: devbig014`  ← real host leaks
   - DBI *does* route uname through shared Detcore `handle_uname`
     (`detcore/src/syscalls/misc.rs:580`): `release`=5.2.0 and `version` are
     rewritten identically to ptrace. But the nodename/domainname rewrite is
     gated `if !guest.config().has_uts_namespace` (misc.rs:592). With namespaces
     on (default `has_uts_namespace = !no_namespace`, run.rs:1598), Detcore trusts
     the UTS namespace's hostname — which hermit sets to `hermetic-container.local`
     for ptrace but which under the DBI/DynamoRIO execution path is **not**
     applied to DBI's namespace, so the host FQDN survives.
   - **Fix scope:** ensure the DBI backend inherits/sets the UTS-namespace
     hostname the ptrace path sets (`.hostname("hermetic-container.local")`,
     run.rs:2113 / container.rs:109), preserving guest `sethostname` semantics.
     Do NOT unconditionally force DEFAULT_HOSTNAME in Detcore — that would break
     legitimate guest `sethostname` inside the namespace (Linux Semantics). This
     is a reverie-dbi / DynamoRIO namespace-inheritance fix, needs a quiet host
     for L2 cross-backend validation. **Next-batch B4 candidate.**

2. **`backend-parity-c/cpuid-probe` — DBI passes CPUID through.**
   ptrace refuses/empties (rc=1, no output); DBI prints
   `CPUID-SUCCESS vendor=GenuineIntel signature=00000663`. DBI is not
   intercepting/virtualizing CPUID the way ptrace does under
   `--no-virtualize-cpuid`. (Consistent with the DBI-vs-ptrace CPUID interception
   gap; distinct interception surface per backend.)

3. **Time/clock/sysinfo family** (`sysinfo`, `sysinfo-uptime`, `proc-uptime`,
   `setitimer-determinism`, `clock-determinism`, `socket-timestamp-{edge-cases,
   timespec,timeval}`, `system-utils/{clock-determinism,proc-uptime}`): e.g.
   `sysinfo-uptime` shows off-by-one virtual uptime (121 vs 120s) and
   `freeram` 913332736 (ptrace) vs 0 (DBI) — DBI's virtual-time base and sysinfo
   field synthesis differ from ptrace's.

4. **Randomness** (`random-sources`, `python-random`, `python-hash-determinism`):
   DBI's deterministic PRNG stream differs from ptrace's for the same seed path.

5. **Memory-layout / pid-virtualization (inherent/hard)**: `print-memaddrs`
   (stack/heap and libc-mmap base differ — DBI injects its own mappings:
   `0x7ffd…` vs `0x7fff…`), `dbi-pid-virtualization`, `proc-fd-link-aliases`,
   `dbi-execveat-unsupported`, `vforkexec`, `wait-on-child`, `pid-tid`,
   `syscall-quick-wins`, `kvm-python-examples`, `openssl-passwd`. Memory-address
   divergence is an intrinsic backend difference, not obviously fixable.

### D. L1 parity confirmed, L2 verify inconclusive — 20 `parity-not-det` cells

L1 byte-identical to ptrace (parity holds!) but `--verify` did not return clean.
Includes threaded/heavy tests that structurally can't `--verify` under DBI
(`determinism-stress{,-c}/thread-contention`, `process-chains`,
`thread-sync-determinism`, `shared-futex-c/qemu-{hello,init,exec-init}`,
`util-c/pmu-skid`) and likely sweep-time verify-timeouts under load (`epoll-`,
`mmap-`, `signal-`, `ipc-determinism`, `archive-roundtrip`, `shell-pipeline`,
etc.). **Not parity failures.** Re-measure L2 on a quiet host to split structural
from load-flaky.

## Recommended next batches (toward B4, in priority order)

1. **`uname` nodename leak** (target C.1): highest-value, root-caused, narrow
   determinism defect (real host FQDN in guest output). reverie-dbi
   UTS-namespace-hostname inheritance. Validate L2 cross-backend on a quiet host.
2. **CPUID interception under DBI** (target C.2): align DBI CPUID handling with
   the ptrace `--no-virtualize-cpuid` contract.
3. **Time/sysinfo synthesis parity** (target C.3): reconcile DBI virtual-time
   base + sysinfo `freeram`/uptime with ptrace.
4. **Re-run L2 on a quiet host** to reclassify the 20 `parity-not-det` cells and
   the load-flaky subset of `gap`.

Structural no-preemption hangs (A) and memory-layout divergence (C.5) are known,
deep, and out of scope for a quick parity batch.
