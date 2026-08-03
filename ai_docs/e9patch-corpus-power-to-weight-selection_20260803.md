# E9patch corpus power-to-weight selection

**Task:** `e9patch-corpus-power-to-weight-selection`

**Status:** proposal only; adversarial agreement is still required

**Corpus:** Hermit PR #1516 at `cb7c72bc12c13771c5f4e50d7b00a62f149c038e`

**Shared-suite baseline:** `origin/main` at `9e6b4ea388d0b8e61041d98eb719e9164dceae1c`

## Recommendation

Promote the semantics from **23 of the 370 restored corpus entries (6.2%)**,
collapsed into **five existing shared tests and zero new test files**. Reject the
other **347 entries**. Do not merge or edit PR #1516 as part of this work.

The proposed ptrace-first blocking increment is **0.71 seconds per validate**:

- four newly blocking ptrace `verify` cells: estimated 0.16 + 0.17 + 0.20 +
  0.17 = **0.70 s**;
- one in-place extension of the already-blocking clock row: conservatively
  **<=0.01 s** incremental across its existing cells.

The subset adds 17 independent semantic contracts spanning 24 previously
unasserted syscall entry points, or **23.9 contracts per added ptrace-first
validate second**. This deliberately counts contracts, not constants: 72
`getsockopt` constants are not 72 units of useful coverage.

## Method

1. Parsed the exact `CORPUS` dictionaries on #1516 and current main: 390 total
   entries on the PR, 20 already on main, **370 restored entries under review**.
2. Parsed all 211 schema-v2 shared TOML rows and their program sources. The 370
   entries call 199 distinct primary syscall names. Of those names, 107 already
   have an explicit shared-program witness (90 only in manual rows and 17 in
   blocking rows).
3. Treated a coverage unit as an independently asserted semantic contract, not
   merely a syscall number or an option constant. Success-only/no-op probes were
   rejected unless they could be folded into a stronger state or agreement
   assertion.
4. Measured the existing target programs under ptrace L2 with the same Hermit
   flags as the manifest harness: `--log=info run --backend ptrace --strict
   --verify --no-virtualize-cpuid --max-timeslice=disabled`.

Measurement binary: SHA-256
`5f94339d2cd860227379eb99df5d163d51dbb4e3002c949ab470342c2497e05f`,
recorded by the harness at Hermit `36ee7e70a6e7bbb348d72419b109f5de33cf7e8c`.
Five warm repetitions all passed L2. The later main changes through the stated
baseline are CI/test-registration changes; none changes these programs or the
runtime path.

## Ranked subset

| Rank | Shared target (corpus inputs) | Unique contracts (entry points) | Added ptrace validate | Contracts/s |
|---:|---|---:|---:|---:|
| 1 | `system-utils/clock-determinism` (`clock_getres_monotonic`) | 1 (1) | <=0.01 s | >=100.0 |
| 2 | `c-programs/scheduler-policy-queries` (8 query cases) | 6 (8) | 0.17 s | 35.3 |
| 3 | `backend-parity-c/pid-probe` (5 identity cases) | 4 (6) | 0.16 s | 25.0 |
| 4 | `c-programs/syscall-file-metadata` (7 metadata cases) | 4 (7) | 0.20 s | 20.0 |
| 5 | `c-programs/pidfd-open-self` (2 pidfd cases) | 2 (2) | 0.17 s | 11.8 |
| **Total** | **23 corpus entries -> 5 existing files** | **17 (24)** | **0.71 s** | **23.9** |

The four new row costs include a small implementation buffer over measured
five-run medians: PID probe 0.143 s -> 0.16 s; scheduler 0.151 s -> 0.17 s;
metadata 0.182 s -> 0.20 s; pidfd 0.155 s -> 0.17 s. The clock program measured
0.161 s median, but it is already run by validate; only the added calls count.

### 1. Clock resolution table

**Input:** `clock_getres_monotonic`.

**Gap:** the active clock table asserts `clock_gettime`, `clock_nanosleep`, and
`gettimeofday`, but never calls `clock_getres` (`tests/c/clock_determinism.c`,
current `check_clock`). No other shared manifest program explicitly calls
`clock_getres`.

**Promotion:** extend the existing clock table, not a new file. For every clock
ID already in the table, call `clock_getres`, validate a normalized nonzero
resolution, and include the resolution in the deterministic output. This is
stronger than #1516's success-only probe.

**Cost:** no new cell. At most 70 short calls across the row's current ptrace
and LiteInst verify/custom executions; budget <=0.01 s total incremental.

### 2. Scheduler query table

**Inputs:** `sched_getparam_check`, `sched_get_priority_min_check`,
`sched_priority_max`, `sched_getscheduler_check`, `sched_getaffinity_check`,
`sched_getattr_self`, `sched_rr_get_interval_check`, `getpriority_self`.

**Gap:** the existing `scheduler_policy_queries.c` tests `getitimer`,
`ioprio_get`, and `sched_setattr`; it does not exercise any of the eight query
entry points above. The shared suite therefore has no explicit agreement check
between the legacy scheduler APIs and `sched_getattr`.

**Promotion:** add one table/helper block to the existing program. Assert:

- `sched_getscheduler`, `sched_getparam`, and `sched_getattr` agree on
  SCHED_OTHER/priority zero;
- min/max for SCHED_OTHER are both zero;
- `sched_getaffinity` returns a nonempty normalized mask;
- `sched_rr_get_interval` returns a valid timespec;
- `getpriority` is in its valid raw range and repeats.

This converts eight mostly success-only corpus files into six cross-checked
contracts in one process.

**Cost:** base median 0.151 s; budget 0.17 s for the modified ptrace cell.

### 3. Process identity table

**Inputs:** `getid_identity`, `getgroups_identity`, `getpgid_check`,
`getpgrp_check`, `getsid_check`.

**Gap:** the shared `pid_probe.c` prints only `getpid`. Other shared tests cover
some PID/TID behavior, but no shared program explicitly asserts `geteuid`,
`getegid`, `getgroups`, `getpgid`, `getpgrp`, or `getsid`.

**Promotion:** extend `pid_probe.c`. Assert real/effective UID/GID coherence,
supplementary-group count/list coherence, equality of `getpgid(0)` and
`getpgrp()`, and a valid session ID. Do not copy host-specific values from the
corpus as golden constants; the row's L2 output is the determinism oracle.

**Cost:** base median 0.143 s; budget 0.16 s for the modified ptrace cell.

### 4. Metadata agreement table

**Inputs:** `statx_devnull`, `newfstatat_devnull`, `statfs_root`,
`fstatfs_memfd`, `faccessat2_devnull`, `flock_memfd`, `utimensat_memfd`.

**Gap:** `syscall_file_metadata.c` already owns metadata/xattr/readahead work,
but has no explicit `statx`, `newfstatat`, `statfs`, `fstatfs`, `faccessat2`,
`flock`, or `utimensat` calls.

**Promotion:** extend that existing program with four strong contracts:

- `statx` and `newfstatat` agree on type/mode for the same file;
- `statfs` and `fstatfs` agree on filesystem type for path and fd;
- `faccessat2` accepts the created readable file and rejects an invalid flag;
- fixed `utimensat` timestamps are observed through `statx`, and a child cannot
  take a conflicting nonblocking `flock` until the parent unlocks.

Do not copy #1516's `statfs`/`fstatfs`/`utimensat` success-only assertions.

**Cost:** base median 0.182 s; budget 0.20 s for the modified ptrace cell.

### 5. Pidfd operations

**Inputs:** `pidfd_getfd_self`, `pidfd_send_signal_self`.

**Gap:** three shared pidfd rows establish `pidfd_open`, polling, and
`waitid(P_PIDFD)`, but none calls `pidfd_getfd` or `pidfd_send_signal`.

**Promotion:** extend `pidfd_open_self.c`. Duplicate a controlled memfd through
`pidfd_getfd` and verify the same contents/open-file behavior; exercise
`pidfd_send_signal` with signal 0 plus an invalid-flags error case. This is
stronger than checking only `newfd >= 0` and return zero.

**Cost:** base median 0.155 s; budget 0.17 s for the modified ptrace cell.

## Validate cost and backend policy

The **0.71 s total** is the proposed change to blocking validate: establish the
four currently-manual rows on ptrace first and add the clock assertions to its
existing cells. It is not an estimate that silently assumes every backend
works.

Every row must remain in the schema-v2 shared TOML manifests and declare every
mode x backend cell. In the first promotion, unqualified DBI/KVM/SaBRe/LiteInst
cells remain explicit gaps and therefore add zero blocking seconds. Each later
backend ratchet must attach its own L2 measurement and update the total. Current
observations show why this matters: tiny C verify cells median 0.210 s on
ptrace, 0.188 s on DBI, and 0.429 s on SaBRe; a simple LiteInst verify cell was
0.899 s. There is no honest KVM estimate from the current host, and the latest
KVM application cells timed out at 120 s. Enabling all backend cells without
measurement would defeat this task's purpose.

## Explicit rejects

### Constant/option grids: reject 162

Reject these complete prefix families, except the seven scheduler-query inputs
selected above:

| Prefix family | Added | Selected | Rejected | Reason |
|---|---:|---:|---:|---|
| `getsockopt_*` | 72 | 0 | 72 | one syscall and mostly one option constant; existing shared socket/TCP tests are stronger |
| `setsockopt_*` | 25 | 0 | 25 | same; set/get constants do not justify cells |
| `madvise_*` | 18 | 0 | 18 | existing `madvise-determinism` asserts behavior and errors |
| `fcntl_*` | 17 | 0 | 17 | existing file/fd/lock tests exercise `fcntl`; command grid is low signal |
| `mmap_*` | 7 | 0 | 7 | existing mmap/stress/shared-map tests cover behavior |
| `mprotect_*` | 3 | 0 | 3 | existing executable/protection tests cover behavior |
| `prctl_*` | 8 | 0 | 8 | option-query grid, mostly return-only |
| `sched_*` | 9 | 7 | 2 | keep only the cross-checked query table; reject yield/setaffinity duplicates |
| `arch_prctl_*` | 3 | 0 | 3 | shared `arch-prctl-determinism` is substantially stronger |
| `membarrier_*` | 3 | 0 | 3 | no concurrent ordering assertion; return-zero only |
| `ioprio_*` | 2 | 0 | 2 | shared scheduler row already covers `ioprio_get`; set is no-op |
| `pkey_*` | 2 | 0 | 2 | return-only; no access-fault semantic oracle |

### Already represented in shared programs: reject 102

These non-grid entries add no new explicit syscall surface or are weaker than
an existing shared semantic test:

`accept_abstract`, `accept_nonlisten`, `bind_abstract`, `brk_grow`,
`clock_nanosleep_relative`, `close_range_high`, `connect_abstract`,
`dup_lowest`, `epoll_ctl_add`, `epoll_wait_timeout_zero`, `eventfd_legacy`,
`faccessat_devnull`, `fallocate_memfd`, `fchmod_memfd`, `fchown_memfd`,
`fchownat_devnull`, `fgetxattr_devnull`, `flistxattr_memfd`,
`fremovexattr_devnull`, `fstat_devnull`, `fstat_size_memfd`, `fsync_memfd`,
`ftruncate_memfd`, `get_robust_list_ok`, `getcpu_check`, `getitimer_prof`,
`getitimer_real`, `getitimer_virtual`, `getppid_check`, `getrandom_bytes`,
`getrandom_nonblock`, `getresuid_check`, `getrlimit_nofile`, `getrusage_self`,
`getrusage_thread`, `getsockname_family`, `getsockname_unix`,
`gettimeofday_check`, `getxattr_devnull`, `ioctl_enotty`,
`ioctl_fioclex_pipe`, `ioctl_fionbio_pipe`, `ioctl_fionread_pipe`,
`kill_self_sig0`, `lgetxattr_devnull`, `listen_abstract`, `listen_dgram`,
`listxattr_devnull`, `llistxattr_devnull`, `lremovexattr_devnull`,
`lseek_devnull_end`, `lseek_pipe`, `lseek_seekcur_memfd`, `memfd_seal`,
`memfd_seek`, `mremap_grow`, `msync_anon`, `munlockall_ok`, `openat_devnull`,
`pidfd_open_self`, `pipe2_direct`, `pipe_nonblock_eagain`, `pipe_rw`,
`poll_timeout_zero`, `ppoll_timeout_zero`, `pread_past_eof`, `preadv_memfd`,
`prlimit_nofile`, `pselect6_timeout_zero`, `pwritev2_memfd`,
`read_devnull_eof`, `read_devzero`, `readahead_memfd`, `readlinkat_exe`,
`readv_zero`, `recvmsg_socketpair`, `removexattr_devnull`,
`rt_sigqueueinfo_self`, `rt_tgsigqueueinfo_self`, `sendfile_memfd`,
`sendmsg_socketpair`, `set_robust_list_ok`, `setrlimit_nofile`, `setsid_check`,
`shutdown_socketpair`, `shutdown_unconnected`, `sigaltstack_query`,
`signalfd_legacy`, `socketpair_dgram`, `socketpair_rw`, `socketpair_seqpacket`,
`stat_devnull`, `sync_all`, `sync_file_range_memfd`, `sysinfo_ok`,
`tgkill_self_sig0`, `timer_create_gettime`, `timerfd_create_check`,
`times_check`, `uname_sysname`, `wait4_nochild`, `waitid_nochild`.

### Unique number but insufficient semantic value: reject 83

These are not promoted because they are success-only/no-op probes, legacy
ENOSYS constants, zero-timeout aliases of an already-covered readiness class,
or thin variants that do not justify another blocking contract:

`accept4_abstract`, `access_devnull`, `capget_ok`, `capset_noop`, `chdir_root`,
`create_module_enosys`, `dup2_high`, `dup2_same_fd`, `epoll_create_legacy`,
`epoll_pwait2_timeout_zero`, `epoll_pwait_timeout_zero`, `eventfd_rw`,
`eventfd_semaphore`, `fadvise_memfd`, `fchdir_root`, `fdatasync_memfd`,
`file_mmap_zero`, `futex_wait_mismatch`, `futex_wake_empty`,
`get_kernel_syms_enosys`, `get_mempolicy_default`, `getcwd_check`,
`getdents_legacy`, `getdents_root`, `getpeername_unconnected`,
`getpeername_unix`, `gettid_check`, `inotify_init_legacy`, `inotify_rm_watch`,
`inotify_watch_root`, `lstat_devnull`, `mbind_default`, `memfd_create_check`,
`mincore_resident`, `mlock2_page`, `mlock_page`, `mlockall_all`,
`mlockall_onfault`, `modify_ldt_read`, `munlock_page`, `nfsservctl_enosys`,
`open_directory`, `open_enoent`, `personality_query`, `pipe_legacy`,
`preadv2_memfd`, `pwrite_pread_memfd`, `pwritev_memfd`,
`query_module_enosys`, `recvfrom_socketpair`, `recvmmsg_socketpair`,
`rt_sigpending_empty`, `rt_sigprocmask_query`, `rt_sigtimedwait_empty`,
`select_timeout_zero`, `sendmmsg_socketpair`, `sendto_socketpair`,
`set_mempolicy_default`, `set_tid_address_ok`, `setfsgid_noop`,
`setfsuid_noop`, `setgid_noop`, `setpgid_self`, `setpriority_self`,
`setregid_noop`, `setresgid_noop`, `setresuid_noop`, `setreuid_noop`,
`setuid_noop`, `sigaction_query`, `signalfd_create`, `socket_dgram`,
`socket_inet6`, `socket_inet_dgram`, `socket_inet_stream`, `socket_netlink`,
`socket_stream`, `syncfs_memfd`, `sysctl_enosys`,
`timerfd_gettime_unarmed`, `tkill_self_sig0`, `umask_set`, `write_badfd`.

This rejection is about the #1516 versions. A future test with a stronger
semantic oracle can be reconsidered on its own measured power-to-weight.

## Promotion gate

No case is approved for promotion yet. A different-model adversarial reviewer
must challenge each of the five rows, the claimed gap, assertion strength, and
cost. Only the intersection of this proposal and that review proceeds.

For an agreed row:

1. modify the existing shared test source; do not copy the freestanding
   e9patch-only file and do not add a backend-private driver;
2. establish ptrace L2 first with exact JSONL duration evidence;
3. keep the schema-v2 TOML row as the single test identity, with every
   backend x mode cell enabled or carrying a concrete gap reason;
4. enable only measured green cells and update `ci/expected-e2e-plan.json`;
5. report the measured increase to validate seconds before landing.
