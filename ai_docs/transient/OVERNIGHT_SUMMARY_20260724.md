# Overnight Summary - 2026-07-24

## Scope and evidence

This report covers TaskGraph work closed from **2026-07-23 18:00 UTC** through
the initial accounting snapshot at **2026-07-24 04:48 UTC**, then adds final
QEMU and landing results through **2026-07-24 06:40 UTC**. The initial query
returned **299 closed tasks**; that mutually exclusive area accounting is
retained rather than silently recomputed. Final achievements are called out
separately. Counts are measurements from task notes, checked-in matrices, and
live GitHub state; they are not estimates.

Snapshot revisions before this report commit:

- dev-hermit parent `main`: `ac79b3bdaab35683dc37366b0090574152491fc1`
- Hermit `main`: `3f073e11654cd12f43e97e212e18b1fcb854d580`
- Reverie `main`: `c93d31f3ebd4b1af5487a2004bdcfeb5903a16f5`
- QEMU L2 tested Hermit commit: `be0ad74cc590f457aa11fd98467c732dbb5f2447`

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
  **520 pass, 80 fail, and 11 unresolved/not run**. The landed validation gate
  now covers **38/38 commands at L2**.
- Post-envp record/replay coverage totals **166 pass and 53 fail across 219
  prescribed cases**. A fresh 17-program current-main-ancestor matrix passed
  14/17; the failures are `yes | head` and Rustup-proxy `cargo`/`rustc` replay.
- KVM is functional for local single-process programs and interpreters. Its
  latest pre-fix matrix is **10 pass, 4 fail, 1 unavailable**. Reverie PR #54
  landed legacy `pipe`/`pipe2` and `getgroups` support; Hermit PR #544 is
  the still-draft consumer pin and integration coverage.
- DBI's measured matrix matches **14/19 ptrace-L2 rows** and passes 15/21
  overall. The mmap-result root fix that makes Lua, Perl, SQLite, and sort pass
  is now on Reverie `main` through PR #53 and on Hermit `main` through pin
  PR #543.
- QEMU reached a genuine **strict L2 boot milestone**. At Hermit `be0ad74c`,
  `--strict --verify` completed two Linux 6.17.13 boots in 425.82 seconds on
  ptrace/INFO with no relaxations and verified 848,391/848,391 messages with no
  substantive differences.
- Syscall dispatch is explicit for all **373/373** pinned x86_64 syscalls.
  The last exhaustive recount before the final handler landings was **107
  determinized, 39 reviewed pass-through, and 227 fail-closed unclassified**;
  current head additionally includes landed `getrandom`, affinity, and
  `writev` work.
- The focused strict compatibility gate is **38/38 L2**. The full validator is
  still not completely green: inherited
  `record_replay_matrix::c_ioctl_siocethtool` issue #540 and host-sensitive
  PMU/timing failures remain. PR #541 fixed the missing-zlib setup blocker, so
  self-hosted CI now advances beyond DynamoRIO configuration.

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
| Ptrace landed validation gate | 38 | 0 | 0 | L2, default ptrace backend, no relaxations, exact landed validation at PR #542. |
| Post-envp R/R batches 1-21 | 166 | 53 | 0 | 75.8% of 219 prescribed cases. |
| Fresh R/R core/interpreter envelope | 14 | 3 | 0 | Tested at Hermit `0a14d47`; all pass rows had byte-identical record/replay stdout. |
| DBI fresh compatibility matrix | 15 | 6 | 0 | Overall verifier result at `39250cd`; 14/19 ptrace-L2 baselines matched. |
| KVM fresh compatibility matrix | 10 | 4 | 1 | Exact prescribed set at `0ad5ad52`; pass rows reached L2 and native stdout parity. |
| QEMU strict boot | 1 | 0 | 0 | Full L2 at `be0ad74c`: two strict boots compared successfully, ptrace/INFO, no relaxations. |

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

PR #542 expanded the strict compatibility gate. Exact landed validation reported
**38/38 L2** on the default ptrace backend, default logging, and no relaxations.
The true/echo/date working envelope remains **3/3** at L1, L2, L3, L4 (20
repetitions), and R/R on its measured lineage.

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

The SIGSEGV family root fix is now in the active fork lineage. DynamoRIO's
`dr_invoke_syscall_as_app` called post-syscall processing before storing the raw
kernel result, so mmap bookkeeping treated syscall number 9 as the mapping
address. With the fix, Lua, Perl, SQLite, sort, md5sum, and wc pass DBI's
two-run verifier with expected output. Reverie PR
[#53](https://github.com/rrnewton/reverie/pull/53) landed as `35dc0af`, pinning
the application-syscall result fix, and Hermit PR
[#543](https://github.com/rrnewton/hermit/pull/543) landed as `a372294`, pinning
that Reverie revision. Upstream DynamoRIO
[#8024](https://github.com/DynamoRIO/dynamorio/pull/8024) and draft Hermit PR
[#278](https://github.com/rrnewton/hermit/pull/278) remain follow-up/reconciliation
work; they are no longer evidence that the fork fix is absent from `main`.

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
stdin forwarding already works at L1. Reverie PR
[#54](https://github.com/rrnewton/reverie/pull/54) then landed deterministic
`pipe`/`pipe2` and supplementary-group handling as `c93d31f`. Draft Hermit
PR [#544](https://github.com/rrnewton/hermit/pull/544) is the consumer pin and
integration gate; until it lands, the 10/4/1 matrix remains the last Hermit-main
measurement rather than a post-fix result.

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
   syscall appeared. This established L1.
4. At `be0ad74c`, the exact prior-L1 profile passed **L2** on ptrace/INFO with
   no relaxations. `target/release/hermit --log info run --strict --verify`
   completed two QEMU 10.1.0 Linux boots and exited 0 in **425.82 seconds**.
   Hermit compared **848,391/848,391 total**, **679,376/679,376
   detcore-specific**, **337,797/337,797 INFO**, and **673,803/673,803 DETLOG
   plus scheduler-COMMIT messages**, reporting no substantive differences and
   `Success: deterministic. Determinism verified.` Kernel SHA-256:
   `e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2`;
   initramfs SHA-256:
   `f88ddaba3fa86a44078d550f92e13f0d23e5a1f0a983aadb24e678e2ef5523cc`.
5. A 60-second DEBUG trace at `0a14d47` emitted no console before the guard,
   but proved time and scheduler progress: 398 turns, 52.667 seconds virtual
   time, 68 monotonic clock samples with zero regressions, and all six ppoll
   calls returned.
6. The compatibility profile using `--no-sequentialize-threads` plus an
   effectively disabled preemption timeout boots in 18-21 seconds and passed
   4/4 demo runs. It is **not strict**. A relaxed `--verify` run completed both
   boots but found divergent post-`clone3` host-thread order.

Hermit PR [#329](https://github.com/rrnewton/hermit/pull/329), now merged,
contains the L1 strict-boot evidence and syscall analysis. The next assurance
work is to enshrine the L2 profile in CI and attempt record/replay/L3; the
performance milestone remains deterministic controlled concurrency for QEMU's
vCPU and helper threads.

## Syscall classification

PR #275 removed the wildcard passthrough and classified all 373 pinned x86_64
syscalls. PR #503 promoted 24 measured, reviewed calls from unclassified to
pass-through. PRs #534 and #539 then determinized `prlimit64` and `arch_prctl`.
The table is the last exhaustive recount before final handler landings; PRs
#545, #546, and #547 subsequently added or hardened deterministic `getrandom`,
scheduler-affinity, and `writev` policy, so these category counts are a pinned
snapshot rather than an assertion about current head:

| Classification | Count |
|---|---:|
| Determinized | 107 |
| Reviewed pass-through | 39 |
| Fail-closed unclassified | 227 |
| **Total** | **373** |

The 50-call local frequency study classified 24 as conditional pass-through and
26 as needing handlers. The highest-priority remaining policies include
`prctl`, `madvise`, socket options/names, PID/group translation, signal target
translation, logical timers, `openat2`/pidfd bookkeeping, and path-aware procfs
normalization. Vectored output moved out of this list with landed PR #547.

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

Initial snapshot subtotal: **22 Hermit PRs and 7 Reverie PRs merged**.

### Additional final landings

| Repository | PR | Change |
|---|---:|---|
| Hermit | [#537](https://github.com/rrnewton/hermit/pull/537) | Expand strict compatibility application matrix |
| Hermit | [#541](https://github.com/rrnewton/hermit/pull/541) | Install zlib headers in self-hosted CI |
| Hermit | [#542](https://github.com/rrnewton/hermit/pull/542) | Expand strict command compatibility gate to 38 L2 probes |
| Hermit | [#543](https://github.com/rrnewton/hermit/pull/543) | Pin merged Reverie DBI application-syscall fix |
| Hermit | [#545](https://github.com/rrnewton/hermit/pull/545) | Deterministic `getrandom` handling |
| Hermit | [#546](https://github.com/rrnewton/hermit/pull/546) | Deterministic scheduler-affinity masks |
| Hermit | [#547](https://github.com/rrnewton/hermit/pull/547) | Deterministic `writev` handling |
| Hermit | [#329](https://github.com/rrnewton/hermit/pull/329) | Strict QEMU boot evidence and syscall analysis |
| Reverie | [#53](https://github.com/rrnewton/reverie/pull/53) | Pin DynamoRIO application-syscall result fix |
| Reverie | [#54](https://github.com/rrnewton/reverie/pull/54) | KVM pipes and supplementary groups |

Final window total: **30 Hermit PRs and 9 Reverie PRs merged**.

### Final open work

| Repository | PRs | Status at snapshot |
|---|---|---|
| Hermit | [#544](https://github.com/rrnewton/hermit/pull/544) | Draft Hermit pin and integration coverage for merged Reverie KVM PR #54; CI in progress at final snapshot |
| Hermit | [#278](https://github.com/rrnewton/hermit/pull/278) | Draft DBI dynamic-mmap follow-up; Reverie #53 and Hermit pin #543 are now merged |
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
| Reverie | [#35](https://github.com/rrnewton/reverie/pull/35), [#1](https://github.com/rrnewton/reverie/pull/1) | Both draft and `human-review`; DBI multiprocess and SaBRe runtime work |
| DynamoRIO | [#8024](https://github.com/DynamoRIO/dynamorio/pull/8024) | Draft upstream raw syscall-result propagation fix |
| liteinst2 | [#1](https://github.com/rrnewton/liteinst2/pull/1) | M1-M5 stack; 46 release tests pass, 3 intentional stress/bench ignores |

Hermit PRs #532, #271, #265, #248, and #243 plus Reverie PR #46 were closed
or superseded rather than landed. Two important earlier PRs remain open: Hermit
[#239](https://github.com/rrnewton/hermit/pull/239) for vfork registration and
[#236](https://github.com/rrnewton/hermit/pull/236) for replay stdout leakage.
Upstream facebookexperimental/hermit
[#85](https://github.com/facebookexperimental/hermit/pull/85) is also still
open; no landing is claimed.

Final GitHub state at 06:40 UTC: **21 Hermit PRs open** (19 drafts; non-draft
#251 has required GitHub-hosted CI red, and non-draft #239 is `human-review`)
and **2 Reverie PRs open** (both draft and `human-review`). The final landing
sweep found no remaining eligible PR.

## CI and validation health

- GitHub-hosted Regular tests and merge gates passed for every final autonomous
  landing: Hermit #537, #541, #542, #543, #545, #546, #547, and #329, plus
  Reverie #53 and #54.
- PR #541 installed `zlib1g-dev`/`zlib-devel`; its self-hosted run confirmed
  successful installation and advanced past DynamoRIO configuration. Later
  self-hosted failures were separate: missing `target/debug/libhermit.so` on
  some branches or issue #540 in the record/replay matrix.
- Exact landed PR #542 validation passed **38/38 strict compatibility probes at
  L2** on ptrace with no relaxations. Full PR #547 validation reported **13/15
  gates**; its inherited failures were issue #540 and host-dependent PMU skid.
- Issue [#540](https://github.com/rrnewton/hermit/issues/540) remains the primary
  reproducible validator blocker: `c_ioctl_siocethtool` exits 1 even though
  comparison reports `Success: replay matched recording.`
- The optional Meta rr syscall suite remains unavailable because the OSS tree
  has no `third-party/rr` submodule/target.

## Morning priorities

1. Enshrine the exact QEMU L2 profile in CI, then attempt QEMU record/replay/L3
   and work on deterministic controlled concurrency for throughput.
2. Fix issue #540 so the full validator and self-hosted record/replay matrix are
   green; keep the newly landed zlib dependency setup.
3. Rebase, validate, and land Hermit PR #544 so current Hermit consumes merged
   Reverie PR #54, then rerun the KVM 10/4/1 compatibility matrix.
4. Reconcile draft Hermit PR #278 and upstream DynamoRIO #8024 with the already
   landed Reverie #53/Hermit #543 fork pin.
5. Address R/R issue #535 (`SIGPIPE`/EPIPE) and #536 (Rustup proxy exec/epoll),
   then rerun the 17-program and real-app matrices.
6. Continue fail-closed syscall work, prioritizing `prctl`, `madvise`,
   PID/signal translation, socket policies, and fd-creating calls.

## Evidence sources

- `ai_docs/transient/strict-compat-matrix.md`
- `ai_docs/transient/coverage-matrix-20260723.md`
- TaskGraph notes for all 299 closed tasks in the stated window, especially
  `impl-expand-rr-envelope`, `impl-dbi-compat-matrix-report`,
  `impl-kvm-compat-expansion`, `impl-qemu-boot-debug-overnight`,
  `impl-qemu-strict-l2-attempt`, `impl-qemu-timer-loop-debug`,
  `impl-targeted-perf-benchmarks`, `impl-expand-validate-sh-programs`,
  `impl-land-reverie-pr53`, `impl-land-getrandom-pr545`,
  `impl-final-pr-sweep`, `impl-exhaustive-syscall-match`,
  `impl-promote-24-passthru-syscalls`, `impl-validate-sh-green-check`, and
  `impl-ci-status-audit`
- Live GitHub PR, issue, Actions, and branch-head queries at the snapshot time
