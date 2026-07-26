# Syscall gap analysis: next syscalls blocking strict determinism

Task: `impl-syscall-analysis-batch` (P1). Date: 2026-07-26.
Method: source-grounded classification + empirical run/strace measurement.
Repo: primary checkout `~/work/dev-hermit/hermit`, binary
`hermit/target/release/hermit` (read-only; research task, no code changes).

## TL;DR

The determinism engine's syscall table has **116 `Unclassified` syscalls**
(vs 183 `Determinized`, 74 `PassThrough`; counts asserted in
`detcore/src/syscall_classification.rs:640-654`). `Unclassified` means the
syscall is **silently forwarded to the host kernel with no determinism
guarantee** (or panics only under the debug flag
`--panic-on-unsupported-syscalls`). These are the real gaps.

**Key structural finding:** `--strict` does **not** gate the syscall
classification at all. `--strict` only sets `sequentialize_threads` and
`deterministic_io` (`hermit-cli/src/bin/hermit/run.rs:1483-1484`). An
unhandled/passthrough syscall is *equally silent* in strict and relaxed mode —
there is no "unsupported syscall" warning in a normal run. The only way to see a
gap is `--panic-on-unsupported-syscalls` (panics naming the syscall) or
cross-referencing `DETLOG inbound syscall:` names against the Unclassified list.

**Honest impact caveat:** most single-process passthrough gaps still pass
`--strict --verify` (L2) *in isolation* on this host, because the host reading
happens to repeat run-to-run and log-diff normalizes numeric noise. The gaps are
**latent correctness risks**, not trivial L2 breakers:
1. **Record/replay**: passthrough data-movement syscalls are not captured, so
   replay diverges when host state changes (this is exactly why `read`/`write`
   are modeled and `readv`/`pwritev`/`sendfile` must be too).
2. **Cross-process / concurrency**: zero-copy and shared-memory syscalls escape
   the deterministic-IO scheduler (cf. memory notes on concurrent-pipe hangs).
3. **Host variance**: time/timer syscalls leak host-derived values (demonstrated
   below) that merely happen to repeat on one host/schedule.

## How the dispatcher treats a gap (ground truth)

`detcore/src/lib.rs`:
- Dispatch match on `classify_syscall(...)` at `lib.rs:1243` (unconditional on
  strict).
- `handle_unclassified_syscall` (`lib.rs:185-201`): if
  `panic_on_unsupported_syscalls` → `error!` then
  `panic!("unsupported syscall: {:?}")`, else `self.passthrough(...)` →
  `record_or_replay` to the real kernel. **Default = silent passthrough.**
- Three explicit deterministic-refusal sets already exist and are the template
  for cheap hardening (`syscall_classification.rs`):
  - `is_unimplemented_enosys_syscall` (13 syscalls → ENOSYS),
  - `is_privileged_admin_refused_syscall` (16 → EPERM),
  - `is_mount_ns_admin_refused_syscall` (15 → EPERM).

## Empirical measurement

Native `strace -f -c` over a 39-program corpus (coreutils, tar, gzip, grep,
python/perl/ruby/node/lua, sqlite3, openssl, git, make, gcc, ps/df/ss, gpg,
ssh-keygen, ...) cross-referenced against the Unclassified list. Gap syscalls
actually observed:

| gap syscall       | progs | source programs        |
|-------------------|-------|------------------------|
| copy_file_range   | 2     | cp, install            |
| splice            | 1     | grep (pipe)            |
| openat2           | 1     | tar                    |
| setresuid/setresgid | 1   | make                   |
| flock             | 1     | flock                  |
| mlock             | 1     | gpg                    |

Confirmed reachable **under hermit** via `--panic-on-unsupported-syscalls`:
- `tar` → panics on **Openat2**; `echo|grep` → panics on **Splice**.
- Hand-written C probes → panic on **Readv, Pwritev(preadv), Sendfile,
  close_range, Times**.
- `select()` probe → **no panic**: modern glibc routes `select()` to the
  `pselect6` syscall, which is Determinized. So the `select` gap is
  low-priority (only legacy/static/non-glibc binaries emit raw `select`).
- `cp /etc/hostname` under hermit → no panic (coreutils fell back off
  `copy_file_range`); it appears only on some copy paths.

`--strict --verify` on the confirmed-reachable set (tar, grep-pipe, readv,
pwritev, sendfile, close_range, times) all reported **L2 deterministic** in
isolation — reinforcing the caveat above. Concrete leak demonstration
(`times()` after a fixed CPU burn):

```
native, run1/run2:  utime=1 stime=0
hermit --strict x3: utime=1 stime=5   (stable across 3 runs, but != native)
```

`times()` returns a **host-derived** tick count (hermit `stime=5` ≠ native
`stime=0`); it is *not* virtualized to logical time. It is deterministic here
only by luck of a fixed workload; on a busier host or different schedule it
would diverge. Same class as `getitimer`/`adjtimex`/`clock_adjtime`.

## Prioritized implementation order

Ordered by (correctness-criticality × real-world frequency ÷ effort). Categories
cover all 116 Unclassified syscalls.

### Tier 1 — Deterministic-IO data-movement siblings (do first)
`readv, preadv, preadv2, pwritev, pwritev2, sendfile, splice, tee, vmsplice,
copy_file_range, openat2, close_range, recvmmsg, epoll_pwait2, select`

Why first: direct siblings of already-modeled syscalls (`read`/`write`/`pread`/
`pwrite`/`openat`/`close`/`poll`/`epoll_wait`), so the handler pattern already
exists; increasingly emitted by default by modern glibc/coreutils/tar/systemd;
correctness-critical for record/replay (unmodeled data movement is uncaptured).
Suggested sub-order (low effort, high value first):
1. `readv`/`preadv`/`preadv2` and `pwritev`/`pwritev2` — reduce to the existing
   read/write model by looping over `iovec`s. **Lowest effort, highest value.**
2. `copy_file_range` — coreutils `cp`/`install` default; either model as
   read+write or return a deterministic fallback errno (e.g. `ENOSYS`/`EXDEV`)
   so glibc falls back to the modeled read/write path.
3. `openat2` — model like `openat` (validate/ignore `RESOLVE_*`); tar/glibc.
4. `close_range` — model like `close` over `[first,last]`; glibc post-fork.
5. `splice`/`tee`/`vmsplice`, `sendfile` — zero-copy; model via a read/write
   bounce buffer or gate; watch for blocking-pipe hang risk.
6. `epoll_pwait2`/`recvmmsg` — multiplexing siblings. `select` last (glibc
   already avoids it via `pselect6`).

### Tier 2 — Deterministic-refusal hardening (near-zero risk, do alongside Tier 1)
`ptrace, bpf, seccomp, perf_event_open, keyctl, add_key, request_key, kcmp,
acct, chroot, personality, modify_ldt, get_thread_area, set_thread_area,
ioprio_get, ioprio_set, sysfs, ustat, syslog, landlock_*, lsm_*,
map_shadow_stack, memfd_secret, remap_file_pages, process_vm_readv,
process_vm_writev, process_mrelease, name_to_handle_at, statmount, listmount,
cachestat`

Why: extend the existing `is_privileged_admin_refused_syscall` pattern to route
these to a deterministic `EPERM`/`ENOSYS`. Most already fail in a container;
making the refusal deterministic removes host-variance and shrinks the silent
passthrough surface with almost no risk. `ptrace`/`bpf`/`seccomp`/
`perf_event_open` are actively dangerous to pass through under an instrumented
guest.

### Tier 3 — Host time/timer leaks (virtualize)
`times, getitimer, adjtimex, clock_adjtime`

Why: unvirtualized host-time leaks (demonstrated). Virtualize to logical time
using the existing `clock_gettime`/`gettimeofday`/`nanosleep` handlers in
`detcore/src/syscalls/time.rs`. Medium effort; fixes benchmarking / time-
observing programs and cross-host determinism. (`getitimer` pairs with the
already-noted `setitimer` gap in memory.)

### Tier 4 — Identity/credential setters (consistency)
`setuid, setgid, setresuid, setresgid, setreuid, setregid, setfsuid, setfsgid,
setgroups`

Why: the *getters* (`getuid`/`getgid`/...) are virtualized, but the setters are
passthrough — an inconsistency (a program that drops privileges then reads them
back can desync). Make setters accept-and-track against the virtualized identity
or deterministically refuse.

### Tier 5 — SysV IPC + POSIX mqueue, and async IO (real-world binaries)
IPC: `shmat, shmctl, shmdt, shmget, semctl, semget, semop, semtimedop, msgctl,
msgget, msgrcv, msgsnd, mq_open, mq_unlink, mq_getsetattr, mq_notify,
mq_timedreceive, mq_timedsend`
Async IO: `io_setup, io_submit, io_getevents, io_cancel, io_destroy,
io_pgetevents`

Why: shared-memory and completion-queue semantics are genuine determinism
hazards and need real modeling (higher effort). Required for databases (Postgres
SysV shm/sem) and modern async servers (io_uring). High value for
`goal-hermit-v2` arbitrary-binary support, but larger than Tier 1-4.

### Tier 6 — Lower-frequency / no-op-able tail
Mem locking & pkeys (`mlock`, `mlock2`, `mlockall`, `mincore`, `pkey_alloc`,
`pkey_free`, `pkey_mprotect`) — mostly safe no-ops.
pidfd (`pidfd_open`, `pidfd_getfd`, `pidfd_send_signal`).
Newer futex ops / robust list / scheduling misc (`futex_wait`, `futex_wake`,
`futex_waitv`, `futex_requeue`, `get_robust_list`, `restart_syscall`,
`sched_get_priority_max/min`, `sched_getattr`, `sched_setattr`, `tkill`,
`rt_sigqueueinfo`, `rt_tgsigqueueinfo`).
FS mutation / misc (`mknod`, `mknodat`, `flock`, `sync`, `syncfs`, `shutdown`).

## Reproduction

```bash
H=hermit/target/release/hermit
# Enumerate a gap under hermit (panics naming the syscall):
$H run --strict --panic-on-unsupported-syscalls -- tar cf /tmp/t.tar /etc/hostname   # -> unsupported syscall: Openat2
$H run --strict --panic-on-unsupported-syscalls -- bash -c 'echo x|grep x'           # -> unsupported syscall: Splice
# Ground-truth lists come straight from source:
sed -n '414,529p' hermit/detcore/src/syscall_classification.rs   # Unclassified (116)
sed -n '278,407p' hermit/detcore/src/syscall_classification.rs   # PassThrough (74)
```

Supporting scratch data (ignored, machine-local):
`scratch/syscall-analysis/` (corpus, strace outputs, probe sources, run logs).
