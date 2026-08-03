# DBI compat RATCHET — hermit-side non-gated lane exhaustion (fresh full re-sweep)

**Date:** 2026-08-01
**Question:** Of the DBI compat cells that were failing at the last full corpus
sweep (@13f3ee68, 46 parity/gap cells), which can still be closed by a
**hermit-side, non-owner-gated** change from the hermit-only `dbt-compat` slot —
i.e. by making the DBI backend match the existing golden ptrace output without a
determinization-strategy / scheduler / signal-model / new-syscall / reverie-core
change?

**Answer:** Effectively none remain. Re-running the 47 previously-failing cells
at current main + the perf branch yields **1 pass (uname), 22 parity-gap, 22 gap,
2 parity-not-det = 46 still failing**. Every one of the 46 roots to a
reverie-dbi-side cause (container/userns, pid allocation, address-space layout,
stdout-pipe plumbing) or an owner-gated cause (guest-clock/vtime, no-preemption
threading model, restart_syscall/execveat classification). The hermit-side
non-gated DBI parity lane is exhausted, corroborating the earlier
`dbt-nongated-fd-hygiene-lane-exhausted` finding.

## Method

- Host: devbig014 (~316 logical CPUs, shared; load-noisy — outcomes are
  categorical pass/fail, not timing, so load does not affect the conclusion).
- hermit @ `b1867d8b2f85935b40527ebaaaccf654abc2f05d`
  (branch `codex/detcore-per-syscall-config-clone` = base `origin/main`
  `0fdf9740` + the PR #1455 Config-clone perf commit; origin/main has since
  advanced to `55fd251b`). The Config-clone change is perf-only and does not
  affect compat outcomes.
- Portable profile: `--strict --no-virtualize-cpuid --max-timeslice=disabled`,
  `LC_ALL=C TZ=UTC`. Parity measured at L1 (both backends pass stdout through);
  determinism at L2 (`--verify`).
- Driver: `compat-envelope/collect-dbi-corpus.rs --tests <47 previously-failing
  ids> --timeout 45 --run-id dbi-ratchet-recheck --emit-ptrace-ref`.
- Raw per-cell results: `dbi-ratchet-recheck.csv` (this dir).

## Results — 47 previously-failing cells

| outcome | count | meaning |
|---|---|---|
| pass | 1 | now byte-identical to ptrace golden at L1 + deterministic at L2 |
| parity-gap | 22 | DBI deterministic (L2) but stdout DIVERGES from ptrace golden |
| gap | 22 | DBI nondeterministic and/or hangs (timeout) |
| parity-not-det | 2 | parity holds but L2 --verify not deterministic |

**Flipped to PASS since @13f3ee68:** `c-programs/uname` — the host-FQDN leak
(`has_uts_namespace` gate, `misc.rs:592`) was fixed on main; DBI now reports
`hermetic-container.local` identically to ptrace.

### parity-gap (22) — DBI deterministic but output ≠ ptrace golden — root causes

- **reverie-dbi userns / credential passthrough** (host uid/gid leaks; ptrace
  sets up a user namespace, DBI does not; `getuid/getgid/getres*` are classified
  PassThrough, deterministic only under the fixed-container assumption):
  `syscall-quick-wins`, `openssl-passwd`, and downstream seeds
  `python-hash-determinism`, `python-random`.
- **DBI pid/tid allocation** (guest sees different virtual pid/tid than ptrace):
  `dbi-pid-virtualization`, `wait-on-child`, `vforkexec`, `pid-tid`.
- **DBI low-VA address-space layout** (brk/stack/mmap vs ptrace high-canonical):
  `print-memaddrs`, `random-sources`.
- **guest-clock / time quantization (OWNER-GATED — virtual-time family):**
  `clock-determinism`, `setitimer-determinism`, `sysinfo`, `sysinfo-uptime`,
  `proc-uptime`, `socket-timestamp-{edge-cases,timespec,timeval}`.
- **DBI stdout-pipe plumbing:** `proc-fd-link-aliases`.
- **cpuid** (expected under `--no-virtualize-cpuid`): `cpuid-probe`.
- **KVM path, not DBI:** `kvm-python-examples`.
- **execveat backend asymmetry:** `dbi-execveat-unsupported` — under ptrace
  `execveat(/bin/true)` SUCCEEDS (image replaced → exits 0, no stdout); under DBI
  it returns ENOSYS so the test prints its success line. Closing this requires
  **adding execveat support to the DBI backend** (reverie-dbi + from-guest-exec
  bootstrap — the #1147 deadlock territory), not a hermit classification tweak.

### gap (22) — nondeterministic and/or hang — root causes

- **DBI no-preemption / threading-model hangs (OWNER-GATED)** — timed out at 45s:
  `robust-futex-test`, `nanosleep-threads-simple`, `pselect6-simulation`,
  `sigtimedwait-no-timeout`, `writev-determinism`, `mmap-fork-shared`,
  `bash-loop-pipe-time`, `python-io-subprocess-time`, `date-nanoseconds`.
  (See `dbi-preemption-in-process-reentrancy-blocker`,
  `dbi-corpus-hangs-preemption-ceiling-exit-group-teardown`.)
- **DBI L1-divergent AND --verify nondeterministic (rc=1/40/101)** — downstream
  of the same signal / pid / time / exec model or backend-of-ptrace semantics:
  `arch-prctl-determinism`, `dbi-unsupported-syscall` (restart_syscall abort —
  see below), `get-robust-list-child`, `pidfd-waitid-child`,
  `ptrace-attach-eperm`, `ptrace-seize-eperm`, `resource-determinism`,
  `sigpipe-siginfo`, `tcp-info-client4`, `pipe-chain`, `thread-output`,
  `perl-io-subprocess-time`, `qemu-net-init`.

### The `restart_syscall` abort (dbi-unsupported-syscall) — investigated, owner-gated

DBI aborts (rc=101) with `detcore-dbi: unsupported syscall: restart_syscall`
where ptrace tolerates it (rc=0, prints `dbi-unsupported-ok`). Root cause: DBI
intercepts **every** syscall instruction, so `restart_syscall` reaches Detcore's
shared `Unsupported` classification → `UnsupportedSyscallError` →
`detcore-dbi/src/lib.rs:1513` emits the diagnostic and returns -1. Under the
ptrace backend, seccomp does **not** trap `restart_syscall`, so it goes to the
kernel natively and deterministically returns EINTR. Closing the gap means
reclassifying `restart_syscall` (currently `Unsupported`, carrying
`TODO-HUMAN-REVIEW(PR-644)`) — a **determinization-strategy change**, owner-gated.
Not filed.

## Interpretation

The only cell that improved since the last sweep (`uname`) was fixed by a
change that had already landed on main. The 46 remaining failures partition
cleanly into **reverie-dbi container/pid/layout/plumbing** work (needs a
reverie-child slot + repin, not editable from this hermit-only slot) and
**owner-gated families** (virtual-time, no-preemption threading model, syscall
reclassification). No clean hermit-side, non-gated ratchet remains in this batch.

The productive OPTIMIZE lever this cycle was perf, not parity: PR #1455 removed
the per-syscall whole-`Config` clone in `Detcore::handle_syscall_event`
(shared-Detcore, benefits all backends).

## Reproduction

```bash
cd ~/work/dev-hermit/compat-envelope
set -a; source ../worktrees/dbt-compat/hermit/.env.dbt.slot; set +a
./collect-dbi-corpus.rs --repo ../worktrees/dbt-compat/hermit \
  --manifest ignored/manifest-harness.json \
  --tests "<the 47 ids listed in dbi-ratchet-recheck.csv>" \
  --timeout 45 --run-id dbi-ratchet-recheck --emit-ptrace-ref \
  --csv ignored/dbi-ratchet-recheck.csv
```
