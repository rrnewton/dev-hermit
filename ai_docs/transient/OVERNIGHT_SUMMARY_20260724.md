# Overnight Summary - 2026-07-24

## Scope and evidence

This report covers TaskGraph work closed from **2026-07-23 18:00 UTC** through
the live snapshot at **2026-07-24 04:48 UTC**. The query returned **299 closed
tasks**. Counts below are measurements from task notes, checked-in matrices, and
live GitHub state; they are not estimates. Historical scenario totals and fresh
focused matrices are kept separate because they use different command sets.

Snapshot revisions before this report commit:

- dev-hermit parent `main`: `9015c2939f5c403676c4c94db81b8ef58ab0ac65`
- Hermit `main`: `8592f55aa9e33aceece9ab1906b028bf343bea41`
- Reverie `main`: `07d7ad64e49b75f297581438b213bdd3289d74f1`

Assurance terminology:

- **L1**: one `--strict` execution completes.
- **L2**: `--strict --verify` completes twice with matching normalized logs.
- **R/R**: recording and replay both complete with matching visible output.
- DBI's current two-run verifier compares guest stdout and a memory hash. It is
  useful evidence, but it is not ptrace L2 trace equivalence.

No test was rerun solely to make this report. Each number names the exact tested
SHA or source task where material.

## Executive summary

- Ptrace remains the broadest mode: the consolidated 611-case strict matrix has
  **520 pass, 80 fail, and 11 unresolved/not run**. The current landed validation
  envelope adds a stable 16-command L2 gate.
- Post-envp record/replay coverage totals **166 pass and 53 fail across 219
  prescribed cases**. A fresh 17-program current-main-ancestor matrix passed
  14/17; the failures are `yes | head` and Rustup-proxy `cargo`/`rustc` replay.
- KVM is functional for local single-process programs and interpreters. Its
  latest exact matrix is **10 pass, 4 fail, 1 unavailable**; missing legacy
  `pipe` and `getgroups` behavior are the real backend gaps.
- DBI's current-main matrix matches **14/19 ptrace-L2 rows** and passes 15/21
  overall. The mmap-result root fix makes Lua, Perl, SQLite, and sort pass, but
  that fix remains in open Hermit/Reverie/DynamoRIO PRs.
- QEMU now has a genuine **strict L1 boot**: Linux 6.17.13 booted and powered off
  in 166.486 seconds with no scheduler relaxation. No strict L2 result exists.
  The 18-21 second compatibility profile still disables thread sequencing and
  is correctly rejected by `--verify` as nondeterministic.
- Syscall dispatch is explicit for all **373/373** pinned x86_64 syscalls.
  Current classification is **107 determinized, 39 reviewed pass-through, 227
  fail-closed unclassified**.
- The full validator is not completely green: **14/15 gates** pass; the only
  failure is `record_replay_matrix::c_ioctl_siocethtool` (issue #540). The
  self-hosted CI lane also cannot configure DynamoRIO because zlib development
  files are missing (issue #538).

## Completed-task accounting

The following mutually exclusive classification was applied to all 299 closed
tasks; the rows sum to 299.

| Area | Closed tasks |
|---|---:|
| Ptrace compatibility and general tests | 77 |
| Other engineering and research | 53 |
| Record/replay | 42 |
| Coordination, PR landing, and CI | 34 |
| QEMU | 29 |
| Syscall implementation and classification | 17 |
| DBI | 17 |
| liteinst2 | 12 |
| KVM | 12 |
| SaBRe | 6 |
| **Total** | **299** |

Material completed work includes strict batches through 69, post-envp R/R
batches through 21, the strict compatibility gate, explicit syscall
classification, 24 reviewed pass-through promotions, `ppoll`, `waitid`,
`prlimit64`, and `arch_prctl` determinization, KVM filesystem/stdin/F_GETFL
support, DBI/KVM compatibility expansion, targeted backend benchmarks, QEMU
strict boot analysis, CI/merge-queue setup, and liteinst2 milestones M1-M5.

## Compatibility matrix

The denominators intentionally differ: each row reports its own prescribed
suite and must not be compared as if all modes ran the same 611 scenarios.

| Mode and evidence set | Pass | Fail | Other | Result |
|---|---:|---:|---:|---|
| Ptrace strict batches 1-69 | 520 | 80 | 11 | 86.7% pass among cases with a verdict; historical multi-SHA scenario matrix. |
| Ptrace landed validation gate | 16 | 0 | 0 | L2, default ptrace backend, no relaxations, on the current-main validation lineage. |
| Post-envp R/R batches 1-21 | 166 | 53 | 0 | 75.8% of 219 prescribed cases. |
| Fresh R/R core/interpreter envelope | 14 | 3 | 0 | Tested at Hermit `0a14d47`; all pass rows had byte-identical record/replay stdout. |
| DBI fresh compatibility matrix | 15 | 6 | 0 | Overall verifier result at `39250cd`; 14/19 ptrace-L2 baselines matched. |
| KVM fresh compatibility matrix | 10 | 4 | 1 | Exact prescribed set at `0ad5ad52`; pass rows reached L2 and native stdout parity. |
| QEMU strict boot | 1 | 0 | L2 not run | One full L1 boot at `dd60278`; not a repeatability claim. |

### Ptrace

The broad scenario matrix demonstrates strong local computation, text,
compression, database, signal, timer, and ordinary thread synchronization
coverage. The dominant unresolved classes are:

- cross-process blocking rendezvous such as named FIFOs, process substitution,
  and parallel-make jobserver pipes;
- live NSS/procfs/host counters and persistent output state shared across verify
  runs;
- nested ptrace, external networking, and commands whose expected nonzero exit
  needs an explicit verification policy;
- multi-process compiler/runtime scheduling, including the still-open vfork
  work in PR #239.

A full `validate.sh` run at `39250cd` and the PR #539 exact head reported strict
compatibility **16/16 L2** and the true/echo/date working envelope **3/3** at L1,
L2, L3, L4 (20 repetitions), and R/R. The current Hermit `main` is the squash
of that validated PR head.

### Record/replay

Fresh focused evidence:

| Workload set | Record | Replay | Output parity | Notes |
|---|---:|---:|---:|---|
| echo, seq, cat, Lua, Perl, SQLite | 6/6 | 6/6 | 6/6 | All also passed strict L2 at `efdcfd71`. |
| 17-program core/interpreter envelope | 17/17 | 14/17 | 14/17 | `yes | head` loses recorded SIGPIPE; Rustup `cargo`/`rustc` proxies hang after exec replay. Direct real cargo/rustc binaries pass controls. |
| Cargo, rustc, bzip2, LevelDB concurrent | 4/4 | 1/4 | 1/4 | Only bzip2 replays. Cargo/rustc exhaust a worker EpollWait stream; LevelDB diverges in concurrent syscall order. |

Issues [#535](https://github.com/rrnewton/hermit/issues/535) and
[#536](https://github.com/rrnewton/hermit/issues/536) track the fresh pipeline
SIGPIPE and Rustup-proxy failures. The broader 219-case matrix still shows that
replay trails strict execution most sharply on pipelines, fd allocation/close
ordering, worker-thread epoll, and concurrent filesystem workloads. PR
[#240](https://github.com/rrnewton/hermit/pull/240) and PR
[#236](https://github.com/rrnewton/hermit/pull/236) remain unlanded follow-ups
for fd ordering and console/stdout leakage.

### DBI

At Hermit `39250cd` with Reverie `07d7ad64`, the 21-row matrix produced:

- ptrace L2 baseline: 19/21 rows (`id` had NSS nondeterminism; `false` stops
  verify after expected exit 1);
- DBI match against those baselines: 14/19;
- DBI verifier overall: 15/21;
- gaps: `yes | head` timeout and raw SIGSEGV for Lua, Perl, SQLite, and sort.

The SIGSEGV family has a validated but unlanded root fix. DynamoRIO's
`dr_invoke_syscall_as_app` called post-syscall processing before storing the raw
kernel result, so mmap bookkeeping treated syscall number 9 as the mapping
address. With the fix, Lua, Perl, SQLite, sort, md5sum, and wc all pass DBI's
two-run verifier with expected output. The dependency stack remains open:

- DynamoRIO [#8024](https://github.com/DynamoRIO/dynamorio/pull/8024)
- Reverie [#53](https://github.com/rrnewton/reverie/pull/53)
- Hermit [#278](https://github.com/rrnewton/hermit/pull/278)

### KVM

The corrected exact matrix at `0ad5ad52` is:

- **10 pass L2 with native stdout parity**: echo, seq, cat, wc, head, base64,
  true, Perl, awk, SQLite;
- **2 backend failures**: `yes | head` and `echo 1+1 | bc` because the legacy
  `pipe` path returns ENOSYS;
- **1 semantic failure**: `id` omits supplementary groups because `getgroups`
  returns ENOSYS;
- **1 shared CLI limitation**: `false` exits 1, so verify stops after run 1;
- **1 unavailable exact binary**: `lua5.3`; installed Lua 5.4.4 passes L2.

Landed Hermit PR #277 and Reverie PR #52 fixed KVM `F_GETFL` and confirmed that
stdin forwarding already works at L1. Remaining pipe and supplementary-group
work belongs in the Reverie KVM executor.

## Performance

All short-utility measurements below completed successfully in three native,
ptrace-strict, and DBI-strict runs at Hermit `aa9146fc`. Values are mean wall
milliseconds; the host was not isolated, so these are directional rather than
publication-grade.

| Program | Native ms | Ptrace ms | DBI ms | DBI / ptrace |
|---|---:|---:|---:|---:|
| echo | 1.3 | 16.3 | 206.7 | 12.7x |
| true | 1.0 | 12.7 | 150.7 | 11.9x |
| seq | 1.0 | 15.7 | 212.7 | 13.6x |
| cat | 2.0 | 16.0 | 202.0 | 12.6x |
| wc | 1.7 | 18.7 | 1533.7 | 82.2x, high variance |
| head | 1.7 | 22.7 | 227.0 | 10.0x |
| base64 | 2.0 | 23.3 | 199.3 | 8.5x |
| id | 4.3 | 27.3 | 241.7 | 8.8x |

The landed targeted benchmark suite (PR #533, five measured L1 runs) shows why
backend ranking depends on workload shape:

| Fixture | Native ms | Ptrace slowdown | DBI slowdown | KVM slowdown |
|---|---:|---:|---:|---:|
| CPU loop | 4.022 | 3.94x | 40.82x | 15.98x |
| 100K syscalls | 16.187 | 229.83x | 90.58x | 96.79x |
| 4 MiB executed text | 4.001 | 3.99x | 879.64x | 16.07x |
| Mixed 10K | 8.077 | 51.33x | 26.56x | 26.55x |

DBI helps relative to ptrace on syscall-heavy and mixed workloads, but this
implementation is currently much slower on large translated code and trivial
short commands. KVM is consistently better than DBI for CPU/code-size probes
and roughly tied on the mixed fixture.

## QEMU progress

The headline result changed during the window and older timeout evidence must
not be mistaken for current status.

1. At `a0e3252`, an 1800-second literal strict run made only 0.830 seconds of
   virtual progress, completed 25,274 main-thread syscalls, and emitted zero
   serial bytes. This established the original performance/liveness problem.
2. `ppoll` determinization landed through Hermit PR #273 and Reverie PR #49.
3. At `dd60278`, literal ptrace `--strict`, no relaxations, QEMU 10.1 TCG
   single-thread, `-smp 1`, and fixed `-icount` booted Linux 6.17.13 and powered
   off in **166.486 seconds**. Evidence: 311 console lines, 21,626 bytes,
   `SHARED_FUTEX_QEMU_KERNEL_OK`, 987 reported turns, 165.226 seconds virtual
   time, and 167,521 completed syscalls. No Hermit ERROR, panic, or unsupported
   syscall appeared. This is **L1 only**.
4. A later 60-second DEBUG trace at `0a14d47` emitted no console before the
   guard, but proved time and scheduler progress: 398 turns, 52.667 seconds
   virtual time, 68 monotonic clock samples with zero regressions, and all six
   ppoll calls returned. The remaining issue is throughput, not a stuck ppoll
   or clock deadline.
5. The compatibility profile using `--no-sequentialize-threads` plus an
   effectively disabled preemption timeout boots in 18-21 seconds and passed
   4/4 demo runs. It is **not strict**. A relaxed `--verify` run completed both
   boots but found divergent post-`clone3` host-thread order.

Draft Hermit PR [#329](https://github.com/rrnewton/hermit/pull/329) contains the
strict-boot evidence and syscall analysis. The next assurance milestone is a
repeatable L2 boot; the next performance milestone is deterministic controlled
concurrency for QEMU's vCPU and helper threads.

## Syscall classification

PR #275 removed the wildcard passthrough and classified all 373 pinned x86_64
syscalls. PR #503 promoted 24 measured, reviewed calls from unclassified to
pass-through. PRs #534 and #539 then determinized `prlimit64` and `arch_prctl`.
Current counts on the Hermit `main` lineage are:

| Classification | Count |
|---|---:|
| Determinized | 107 |
| Reviewed pass-through | 39 |
| Fail-closed unclassified | 227 |
| **Total** | **373** |

The 50-call local frequency study classified 24 as conditional pass-through and
26 as needing handlers. The highest-priority remaining policies include
`prctl`, `madvise`, socket options/names, PID/group translation, signal target
translation, logical timers, `openat2`/pidfd bookkeeping, vectored I/O, and
path-aware procfs normalization.

## PR activity

### Landed in the evidence window

| Repository | PR | Change |
|---|---:|---|
| Hermit | [#244](https://github.com/rrnewton/hermit/pull/244) | Fail-closed `Subscription::all` default and passthrough optimization flag |
| Hermit | [#250](https://github.com/rrnewton/hermit/pull/250) | Clone child startup turn |
| Hermit | [#252](https://github.com/rrnewton/hermit/pull/252) | Timeslice distribution statistics |
| Hermit | [#253](https://github.com/rrnewton/hermit/pull/253) | Post-fork configuration Clippy fix |
| Hermit | [#255](https://github.com/rrnewton/hermit/pull/255) | `pipe_basics` strict hang fix |
| Hermit | [#256](https://github.com/rrnewton/hermit/pull/256) | Strict standard-command CI coverage |
| Hermit | [#258](https://github.com/rrnewton/hermit/pull/258) | `sched_yield` cedes a scheduler turn |
| Hermit | [#260](https://github.com/rrnewton/hermit/pull/260) | Deterministic SIOCETHTOOL ENODEV policy |
| Hermit | [#261](https://github.com/rrnewton/hermit/pull/261) | NULL `getsockopt` record/replay buffers |
| Hermit | [#266](https://github.com/rrnewton/hermit/pull/266) | CI concurrency cancellation |
| Hermit | [#269](https://github.com/rrnewton/hermit/pull/269) | Stabilized local validation gates |
| Hermit | [#272](https://github.com/rrnewton/hermit/pull/272) | KVM filesystem and multi-program support |
| Hermit | [#273](https://github.com/rrnewton/hermit/pull/273) | `ppoll` determinization |
| Hermit | [#274](https://github.com/rrnewton/hermit/pull/274) | `waitid` determinization |
| Hermit | [#275](https://github.com/rrnewton/hermit/pull/275) | Exhaustive syscall classification |
| Hermit | [#276](https://github.com/rrnewton/hermit/pull/276) | Merge queue gate |
| Hermit | [#277](https://github.com/rrnewton/hermit/pull/277) | KVM stdin/F_GETFL validation and Reverie pin |
| Hermit | [#503](https://github.com/rrnewton/hermit/pull/503) | Promote 24 reviewed pass-through syscalls |
| Hermit | [#521](https://github.com/rrnewton/hermit/pull/521) | Strict compatibility validation envelope |
| Hermit | [#533](https://github.com/rrnewton/hermit/pull/533) | Targeted backend performance benchmarks |
| Hermit | [#534](https://github.com/rrnewton/hermit/pull/534) | Deterministic self `prlimit64` |
| Hermit | [#539](https://github.com/rrnewton/hermit/pull/539) | Deterministic `arch_prctl` controls |
| Reverie | [#45](https://github.com/rrnewton/reverie/pull/45) | Unknown ioctl output handling |
| Reverie | [#47](https://github.com/rrnewton/reverie/pull/47) | Backend-provided KVM auxiliary vectors |
| Reverie | [#48](https://github.com/rrnewton/reverie/pull/48) | External Reverie tools for DBI |
| Reverie | [#49](https://github.com/rrnewton/reverie/pull/49) | `ppoll` ABI correction |
| Reverie | [#50](https://github.com/rrnewton/reverie/pull/50) | KVM filesystem and multi-program runtime |
| Reverie | [#51](https://github.com/rrnewton/reverie/pull/51) | Merge queue setup |
| Reverie | [#52](https://github.com/rrnewton/reverie/pull/52) | KVM `F_GETFL` support |

Totals: **22 Hermit PRs and 7 Reverie PRs merged** in the window.

### Open work created in the window

| Repository | PRs | Status at snapshot |
|---|---|---|
| Hermit | [#537](https://github.com/rrnewton/hermit/pull/537) | Draft strict application-envelope expansion |
| Hermit | [#329](https://github.com/rrnewton/hermit/pull/329) | Draft QEMU strict-boot evidence |
| Hermit | [#278](https://github.com/rrnewton/hermit/pull/278) | Draft DBI dynamic-mmap fix; depends on Reverie #53 and DynamoRIO #8024 |
| Hermit | [#270](https://github.com/rrnewton/hermit/pull/270), [#268](https://github.com/rrnewton/hermit/pull/268) | Draft `rt_sigsuspend` and select/pselect6 determinization |
| Hermit | [#267](https://github.com/rrnewton/hermit/pull/267) | Draft SaBRe M1/M2 integration |
| Hermit | [#264](https://github.com/rrnewton/hermit/pull/264) | Draft chaos and R/R coverage expansion |
| Hermit | [#263](https://github.com/rrnewton/hermit/pull/263) | Draft deterministic PID namespace/signal tests |
| Hermit | [#262](https://github.com/rrnewton/hermit/pull/262) | Draft fail-closed syscall classification follow-up |
| Hermit | [#259](https://github.com/rrnewton/hermit/pull/259) | Draft nondeterministic PMU-skid experiment |
| Hermit | [#257](https://github.com/rrnewton/hermit/pull/257), [#254](https://github.com/rrnewton/hermit/pull/254), [#251](https://github.com/rrnewton/hermit/pull/251) | Timeslice naming, post-fork policy, and syscall-boundary timeslice work |
| Hermit | [#249](https://github.com/rrnewton/hermit/pull/249), [#247](https://github.com/rrnewton/hermit/pull/247), [#246](https://github.com/rrnewton/hermit/pull/246), [#245](https://github.com/rrnewton/hermit/pull/245) | Robust-futex and timer regression probes |
| Hermit | [#242](https://github.com/rrnewton/hermit/pull/242), [#241](https://github.com/rrnewton/hermit/pull/241) | Virtual-time table and `sched_yield` livelock work |
| Hermit | [#240](https://github.com/rrnewton/hermit/pull/240) | Draft replay kernel-fd close ordering fix |
| Reverie | [#53](https://github.com/rrnewton/reverie/pull/53) | Draft DynamoRIO application-syscall result pin |
| DynamoRIO | [#8024](https://github.com/DynamoRIO/dynamorio/pull/8024) | Draft raw syscall-result propagation fix |
| liteinst2 | [#1](https://github.com/rrnewton/liteinst2/pull/1) | M1-M5 stack; 46 release tests pass, 3 intentional stress/bench ignores |

Hermit PRs #532, #271, #265, #248, and #243 plus Reverie PR #46 were closed
or superseded rather than landed. Two important earlier PRs remain open: Hermit
[#239](https://github.com/rrnewton/hermit/pull/239) for vfork registration and
[#236](https://github.com/rrnewton/hermit/pull/236) for replay stdout leakage.
Upstream facebookexperimental/hermit
[#85](https://github.com/facebookexperimental/hermit/pull/85) is also still
open; no landing is claimed.

## CI and validation health

- Current Hermit `main` Docs CI at `8592f55a` is green. The Rust workflow was
  still in progress at the snapshot.
- Recent GitHub-hosted Regular jobs used to land PRs #533, #534, and #539 were
  green, as were their merge gates.
- The combined Rust workflow is degraded because the self-hosted runner cannot
  configure `reverie-dbi`: CMake reports `Could NOT find ZLIB`. Open issue
  [#538](https://github.com/rrnewton/hermit/issues/538) tracks installing
  `zlib1g-dev`/`zlib-devel` on the runner.
- Full local validation at `39250cd`, repeated on the PR #539 head, passed
  **14/15 gates**. Nextest ran **320 tests: 319 passed, 1 failed, 254 skipped**.
  The sole failure is
  `record_replay_matrix::c_ioctl_siocethtool`: the workload expects the old
  `Request::Other(0x8946)` policy, while pinned Reverie now decodes typed
  `Request::SIOCETHTOOL`. Issue
  [#540](https://github.com/rrnewton/hermit/issues/540) tracks it.
- The optional Meta rr syscall suite remains unavailable because the OSS tree
  has no `third-party/rr` submodule/target.

## Morning priorities

1. Land the DynamoRIO #8024 -> Reverie #53 -> Hermit #278 dependency chain after
   upstream/review gates; it converts four confirmed DBI SIGSEGV cases to passes.
2. Fix issue #540 so `validate.sh` is 15/15, and install zlib development files
   on the self-hosted runner per issue #538.
3. Implement KVM legacy pipe and supplementary-group support in Reverie.
4. Address R/R issue #535 (`SIGPIPE`/EPIPE) and #536 (Rustup proxy exec/epoll),
   then rerun the 17-program and real-app matrices.
5. Repeat the successful QEMU strict boot and attempt L2; use PR #329's syscall
   analysis to separate throughput work from correctness work.
6. Continue the 227 fail-closed syscall classifications, prioritizing `prctl`,
   `madvise`, PID/signal translation, socket policies, and fd-creating calls.

## Evidence sources

- `ai_docs/transient/strict-compat-matrix.md`
- `ai_docs/transient/coverage-matrix-20260723.md`
- TaskGraph notes for all 299 closed tasks in the stated window, especially
  `impl-expand-rr-envelope`, `impl-dbi-compat-matrix-report`,
  `impl-kvm-compat-expansion`, `impl-qemu-boot-debug-overnight`,
  `impl-qemu-timer-loop-debug`, `impl-targeted-perf-benchmarks`,
  `impl-exhaustive-syscall-match`, `impl-promote-24-passthru-syscalls`,
  `impl-validate-sh-green-check`, and `impl-ci-status-audit`
- Live GitHub PR, issue, Actions, and branch-head queries at the snapshot time
