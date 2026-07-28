# Final Overnight Engineering Report - 2026-07-24

## Evidence boundary

This report covers **2026-07-23 18:00 UTC through 2026-07-24 11:26 UTC**.
Counts were reconciled from live GitHub state and exact TaskGraph handoffs at
the cutoff. Report generation did not rerun product suites.

- Hermit fork `main`: `349fc6d7d7118778f9ae6fd39aafec6a057bec5c`
  ([#564](https://github.com/rrnewton/hermit/pull/564)).
- Reverie fork `main`: `c93d31f3ebd4b1af5487a2004bdcfeb5903a16f5`
  ([#54](https://github.com/rrnewton/reverie/pull/54)).
- Host: Linux `6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79`, AMD EPYC
  9D85, `perf_event_paranoid=1`.
- Toolchain: `rustc 1.96.0`, `cargo 1.96.0`, `cargo-nextest 0.9.140`.
- Present runtimes: Meta `python3` and `node` wrappers, direct `/bin/node`,
  Redis, SQLite, and Java.

Unless stated otherwise, strict results use the ptrace backend, default log
level, and no relaxations. L2 means `--strict --verify` completed both runs
and Hermit's normalized deterministic logs matched.

## Executive summary

- **57 product PRs landed in the window: 48 Hermit and 9 Reverie.**
- The ptrace compatibility gate reached **119/119 at L2** after adding Java
  and replacing the host Node telemetry wrapper with direct `/bin/node`.
- QEMU Linux boot reached L2 and now has a landed opt-in CI gate. Its final
  validation matched **1,085,768/1,085,768 messages** and
  **817,137/817,137 DETLOG/scheduler commits**.
- A complete 118-command KVM expansion measured **45/118 at L2**. The largest
  blocker is missing process creation: 54 failures require fork/clone.
- The latest completed 118-command record/replay matrix measured
  **95/118 output-correct** at `a2926507`; a newer rerun after the #555 fd
  namespace fix was still active at the cutoff.
- DBI's largest completed matrix remains **20/38**. Its first 118-command run
  was still active at the cutoff, so this report does not invent a total.
- The Python/vfork scheduler fix is validated and hosted-CI green, but remains
  open behind the explicit human-review gate in #239.
- The upstream sync candidate reached hosted green, but
  facebookexperimental/hermit #85 is currently **closed without merge**.

## Backend and mode matrix

The denominators differ because the expanded backend runs did not all finish
on one common tip. Each row names its exact evidence boundary.

| Backend or mode | Result | Exact evidence and qualification |
|---|---:|---|
| Ptrace strict gate | **119/119 PASS L2** | PR #564 head `e25fb9ca`; full strict gate, default log, no relaxations. Three focused direct-Node runs also passed 3/3. |
| Record/replay | **95/118 output-correct** | Hermit `a2926507`; 115 recordings completed, 95 replays exited zero with byte-identical stdout, 3 record failures, 20 replay failures, and zero zero-exit output mismatches. |
| KVM | **45/118 PASS L2** | Hermit `a2926507`; 73 failures, zero timeouts, default log, no relaxations. |
| DBI | **20/38 complete baseline** | Hermit `3c49d19`; 17 lifecycle timeouts plus one non-ELF wrapper. The first 118-row run at `a2926507` was incomplete at cutoff. |
| QEMU on ptrace | **Linux boot PASS L2** | PR #553 head `69854b4`; one-vCPU QEMU boot marker plus exact verifier trace equality. This is one workload, not an app-matrix denominator. |

### Ptrace: 119/119

The landed gate grew through #521, #537, #542, #550, #558, #562, and #563.
PR #564 then pinned the Node row to `/bin/node`, avoiding the host
`/usr/local/bin/nodejs-wrapper` telemetry process. At #564's exact head:

- `./validate.sh --strict-compat-only --verbose`: **119/119 PASS L2**.
- Direct `/bin/node -e 'console.log(42)'`: **3/3 PASS L2**, each
  42,158/42,158 messages.
- GitHub-hosted Regular tests passed in 8m29s.

This is an L2 gate result, not L4 stress evidence. The excluded
`timeout 1 true` case still hangs while its parent waits in `rt_sigsuspend`.

Targeted complex-app checks on `6cea488c` passed SQLite query, GNU Make
version, and GNU Grep version at L2. The prescribed Meta
`/usr/local/bin/git --version` diverged, while stock `/usr/bin/git --version`
passed L2; the failure is therefore not a claim that all Git binaries fail.

### Record/replay: 95/118 completed baseline

The complete `a2926507` matrix used private data directories and 60-second
process-group deadlines per row:

- **115/118** recordings completed.
- **95/118** replayed with exit 0 and byte-identical stdout.
- **3** record failures: `diff`, `cp`, and `install`, each hitting unsupported
  `ioctl(FICLONE)` before the deadline.
- **20** replay failures: 4 immediate fd/syscall desyncs and 16 cases that
  emitted a panic/divergence before teardown timed out.
- `yes` now records and replays correctly after the SIGPIPE fix.
- All 14 former stdout-routing mismatches pass after #560.

PR #555 landed after this matrix and preserves replay's physical fd namespace.
The exact 118-command latest-main rerun was still diagnosing remaining
filesystem/fd cases at the cutoff. Therefore **95/118 is the latest complete
count, not a claim about current `main`**.

### KVM: 45/118

The exact 118-row KVM run at `a2926507` finished with **45 PASS L2, 73 FAIL,
0 timeouts**:

- 54 failures report `bash: fork: Function not implemented`; these prescribed
  wrappers/pipelines require missing fork/clone and do not independently prove
  the underlying utility is unsupported.
- 4 loader failures: `cargo`/`rustc` overlap the fixed interpreter base, while
  the then-current `node` and `file` paths were top-level scripts rather than
  ELF inputs.
- 15 other rows expose exec, filesystem, process-control, procfs, environment,
  or output-path gaps.

This expands the previous KVM count from 31/57 to 45/118 without extrapolating
the later Java and direct-Node changes.

### DBI: expanded run incomplete

The complete DBI baseline remains **20/38** at `3c49d19`. Seventeen rows timed
out in clone/exec lifecycle paths and the `file` wrapper was non-ELF. Reverie
#53 and Hermit #543 fixed raw application-syscall result propagation, but the
larger lifecycle issue remains.

The first 118-row DBI run was executing at `a2926507` when this report was
cut. It had not posted a final count, so only the completed 20/38 baseline is
reported. Reverie issue #31 tracks the native clone/exec lifecycle work.

## QEMU milestone

PR [#553](https://github.com/rrnewton/hermit/pull/553) landed an optional
`validate.sh --qemu-l2-only` gate with bounded phases, positive boot-marker
checks, process-group cleanup, and negative tests. The landed evidence is:

- L1 Linux boot marker observed.
- L2 matched 1,085,768 messages on both runs.
- L2 matched 817,137 DETLOG/scheduler commits on both runs.
- Detcore unit/misc/parallelism/time tests, procfs determinism, Clippy,
  rustfmt, and ShellCheck passed at the PR head.

QEMU record reaches `SHARED_FUTEX_QEMU_KERNEL_OK` and poweroff. Replay cleared
the former late-`PT_INTERP` loader panic after #552; the latest complete
published evidence still stopped on fd/event ordering. Do not describe QEMU
record/replay as complete.

## Other key achievements

- Explicit dispatch now covers all 373 pinned x86_64 syscalls (#275), and 24
  reviewed calls moved to pass-through (#503).
- Deterministic or explicit policies landed for `ppoll`, `waitid`,
  `prlimit64`, `arch_prctl`, `getrandom`, affinity, `writev`, `madvise`, and
  notification fds.
- Scheduler changes landed for clone child startup, `sched_yield`, pipe EOF,
  and timeslice observability.
- R/R gained strict CLI compatibility, late ELF interpreter loading, SIGPIPE
  preservation, `dup2` fd-table updates, and the broader fd namespace fix.
- KVM gained filesystem programs, stdin flag checks, pipes, and supplementary
  groups across Hermit and Reverie.
- CI gained cancellation, stable local validation, a merge gate, self-hosted
  zlib dependencies, and the optional QEMU L2 workflow.
- Java joined the strict gate (#563); direct Node replaced the host telemetry
  wrapper (#564).

## Python3/vfork status

[PR #239](https://github.com/rrnewton/hermit/pull/239) fixes two vfork
scheduling races: the child-registration window and eager parent requeue. Its
rebased head `ce6f3827` passed:

- Python `print(42)` at L2 on ptrace/default-log/no-relaxations.
- Full workspace build/tests, Clippy, and rustfmt.
- GitHub-hosted Regular tests.
- Author stress evidence: GCC 100/100, Rustc 15/15, and G++ 5/5 at L2.

The PR remains open with `human-review`. These are validated branch results,
not landed `main` behavior.

## Upstream synchronization status

[facebookexperimental/hermit #85](https://github.com/facebookexperimental/hermit/pull/85)
was updated by merging the upstream-only `84674a5` commit into the fork main
without rebasing. Hosted Regular tests, CLA, and Meta Import Checks passed.
At the report cutoff GitHub reports the PR **CLOSED**, `mergedAt=null`, with
Import Status still queued. The upstream sync was prepared and validated but
did not land; no upstream-merge claim is made.

## PR landings

### Hermit: 48

[#244](https://github.com/rrnewton/hermit/pull/244),
[#245](https://github.com/rrnewton/hermit/pull/245),
[#249](https://github.com/rrnewton/hermit/pull/249),
[#250](https://github.com/rrnewton/hermit/pull/250),
[#252](https://github.com/rrnewton/hermit/pull/252),
[#253](https://github.com/rrnewton/hermit/pull/253),
[#255](https://github.com/rrnewton/hermit/pull/255),
[#256](https://github.com/rrnewton/hermit/pull/256),
[#258](https://github.com/rrnewton/hermit/pull/258),
[#260](https://github.com/rrnewton/hermit/pull/260),
[#261](https://github.com/rrnewton/hermit/pull/261),
[#266](https://github.com/rrnewton/hermit/pull/266),
[#269](https://github.com/rrnewton/hermit/pull/269),
[#272](https://github.com/rrnewton/hermit/pull/272),
[#273](https://github.com/rrnewton/hermit/pull/273),
[#274](https://github.com/rrnewton/hermit/pull/274),
[#275](https://github.com/rrnewton/hermit/pull/275),
[#276](https://github.com/rrnewton/hermit/pull/276),
[#277](https://github.com/rrnewton/hermit/pull/277),
[#329](https://github.com/rrnewton/hermit/pull/329),
[#503](https://github.com/rrnewton/hermit/pull/503),
[#521](https://github.com/rrnewton/hermit/pull/521),
[#533](https://github.com/rrnewton/hermit/pull/533),
[#534](https://github.com/rrnewton/hermit/pull/534),
[#537](https://github.com/rrnewton/hermit/pull/537),
[#539](https://github.com/rrnewton/hermit/pull/539),
[#541](https://github.com/rrnewton/hermit/pull/541),
[#542](https://github.com/rrnewton/hermit/pull/542),
[#543](https://github.com/rrnewton/hermit/pull/543),
[#544](https://github.com/rrnewton/hermit/pull/544),
[#545](https://github.com/rrnewton/hermit/pull/545),
[#546](https://github.com/rrnewton/hermit/pull/546),
[#547](https://github.com/rrnewton/hermit/pull/547),
[#548](https://github.com/rrnewton/hermit/pull/548),
[#549](https://github.com/rrnewton/hermit/pull/549),
[#550](https://github.com/rrnewton/hermit/pull/550),
[#551](https://github.com/rrnewton/hermit/pull/551),
[#552](https://github.com/rrnewton/hermit/pull/552),
[#553](https://github.com/rrnewton/hermit/pull/553),
[#554](https://github.com/rrnewton/hermit/pull/554),
[#555](https://github.com/rrnewton/hermit/pull/555),
[#557](https://github.com/rrnewton/hermit/pull/557),
[#558](https://github.com/rrnewton/hermit/pull/558),
[#559](https://github.com/rrnewton/hermit/pull/559),
[#560](https://github.com/rrnewton/hermit/pull/560),
[#562](https://github.com/rrnewton/hermit/pull/562),
[#563](https://github.com/rrnewton/hermit/pull/563), and
[#564](https://github.com/rrnewton/hermit/pull/564).

### Reverie: 9

[#45](https://github.com/rrnewton/reverie/pull/45),
[#47](https://github.com/rrnewton/reverie/pull/47),
[#48](https://github.com/rrnewton/reverie/pull/48),
[#49](https://github.com/rrnewton/reverie/pull/49),
[#50](https://github.com/rrnewton/reverie/pull/50),
[#51](https://github.com/rrnewton/reverie/pull/51),
[#52](https://github.com/rrnewton/reverie/pull/52),
[#53](https://github.com/rrnewton/reverie/pull/53), and
[#54](https://github.com/rrnewton/reverie/pull/54).

## Open review and blockers

- Human-review PRs: [#236](https://github.com/rrnewton/hermit/pull/236),
  [#239](https://github.com/rrnewton/hermit/pull/239),
  [#240](https://github.com/rrnewton/hermit/pull/240), and
  [#246](https://github.com/rrnewton/hermit/pull/246).
- [#565](https://github.com/rrnewton/hermit/pull/565) overlaps the now-landed
  Node path change and is conflict-dirty; it needs rebase/scope reconciliation,
  not an unreviewed merge.
- DBI: Reverie issue #31, clone/exec lifecycle.
- KVM: Reverie issue #55, fork/clone, followed by exec/loader and filesystem
  gaps.
- R/R: issue #536 for Rustup epoll event EOF; latest-main 118-row rerun still
  needed after #555.
- Ptrace: `timeout 1 true` scheduler interaction remains outside the gate.
- Self-hosted CI currently fails dependency setup on this runner because apt
  cannot locate packages including CMake, SQLite, Redis, and zlib development
  files. Hosted Regular tests remain the required landing gate.

## Next steps

1. Finish and publish the post-#555 118-row R/R rerun; classify remaining
   failures against the 95/118 baseline.
2. Finish the 118-row DBI run, then address Reverie lifecycle issue #31 and
   rerun on one current-main SHA.
3. Implement KVM fork/clone issue #55 and rerun the common 119-row corpus,
   including Java and direct Node.
4. Obtain human review for #239 and the three other explicitly gated PRs.
5. Resolve or supersede overlapping #565 after #564.
6. Reopen or replace upstream PR #85 only through the required Meta import
   process; do not claim upstream parity until a merge commit exists.
