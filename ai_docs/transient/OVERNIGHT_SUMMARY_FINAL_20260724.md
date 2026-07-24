# Final Overnight Summary - 2026-07-24

## Scope and evidence cutoff

This is the final morning handoff for work completed from **2026-07-23
18:00 UTC through 2026-07-24 08:16 UTC**. It consolidates the earlier
`OVERNIGHT_SUMMARY_20260724.md` snapshot with all later landings, validation
results, backend measurements, active investigations, and known blockers.

Live revisions at the cutoff, before this report commit:

- Hermit fork `main`: `13ea56777078d47dfd6260f9d9c65dc7326ffe65`
  (PR #552).
- Reverie fork `main`: `c93d31f3ebd4b1af5487a2004bdcfeb5903a16f5`
  (PR #54).
- dev-hermit parent `main`: `2698f92268eafb838de648451796d4d6cfa4774d`.
- Latest full-validator SHA: Hermit
  `3c49d197b4734a068860cb30954bc657b90abf09` (PR #549).
- Latest strict-compatibility PR head tested with the 57-program gate:
  `45fbb45ee1830358422d6d398f0f815d4c3c0127` (PR #550).

TaskGraph contained 1,508 closed tasks overall at the cutoff. Applying the
same final-modification query used for the overnight window returned **328
closed tasks since 18:00 UTC**, up from the 299-task 04:48 UTC accounting
snapshot. The detailed mutually exclusive area breakdown remains the published
299-task snapshot; 29 later closures are reported by concrete results below
rather than retroactively reclassified.

Assurance terms follow the repository policy:

- **L0**: build and applicable unit/integration tests exit 0.
- **L1**: one strict execution completes.
- **L2**: `--strict --verify` completes with matching normalized logs.
- **L3**: L2 plus deterministic heap and stack logs.
- **L4**: L2/L3 stress repeated 20 times without divergence.
- **R/R**: recording and replay both complete with matching visible output.

Unless a row says otherwise, Hermit strict results use the ptrace backend,
default log level, and no relaxations. Historical matrices span multiple
explicitly recorded SHAs; they are not claims about an untested branch tip.

## Executive summary

- **45 product PRs landed in the evidence window: 36 Hermit and 9 Reverie.**
  The final Hermit tip is PR #552; the final Reverie tip is PR #54.
- Ptrace remains the broadest backend. The historical 611-case strict matrix is
  **520 pass, 80 fail, 11 unresolved/not run**. The landed nonblocking gate
  grew from 38 to **57/57 L2** through PR #550.
- A full validation run at Hermit `3c49d19` passed **15/15 gates** and
  **328/328 workspace/integration tests**, with the optional Meta rr suite
  skipped because `third-party/rr` is absent. This is the strongest complete
  validator result of the window.
- QEMU completed four strict Linux-boot **L2** verifier runs: the initial
  848,391-message milestone and three post-fix validation runs after procfs
  affinity normalization. The latest matched 1,085,768 messages on current
  main. The optional CI gate is open as draft PR #553.
- QEMU recording reaches `SHARED_FUTEX_QEMU_KERNEL_OK` and poweroff. PR #552
  removed replay's late-`PT_INTERP` ELF parsing panic. Replay now reaches
  Detcore and stops at a separate fd-number divergence at syscall event 633.
- Post-envp R/R history remains **166 pass / 53 fail across 219 cases**.
  PR #551 adds direct `record --strict` compatibility syntax. A developed,
  not-yet-published Rustup exec/epoll fix makes `cargo --version` and
  `rustc --version` replay; the `yes | head` SIGPIPE fix and the new
  57-program R/R matrix remain active work.
- KVM pipe/pipe2 and supplementary-group support landed through Reverie #54
  and Hermit #544. Targeted KVM L2 now passes `id` and pipe round trips.
  Multi-process `bc` and `yes | head` next stop at unimplemented KVM
  `fork`, tracked by Reverie issue #55.
- DBI's last complete 21-row matrix is **15 pass / 6 fail**. The application
  syscall-result fix landed through Reverie #53 and Hermit #543. A newer
  38-program run reports **20 pass / 18 timeout** and is under active
  log-first investigation; do not treat the old 15/21 or the new partial
  20/38 as final parity.
- Syscall dispatch is explicit for all **373/373** pinned x86_64 syscalls.
  The window landed reviewed pass-through promotion plus deterministic
  `ppoll`, `waitid`, `prlimit64`, `arch_prctl`, `getrandom`,
  affinity, `writev`, `madvise`, and notification-fd coverage.
- SaBRe is **not a Hermit execution backend on main**. Current main rejects
  `--backend sabre` before guest execution. Draft Hermit #267 and Reverie #1
  provide a tracer prototype only and do not reach L1-L4.

## Milestones reached

### Ptrace, scheduling, and compatibility

- `sched_yield` now cedes a scheduler turn (#258), clone children receive a
  startup turn (#250), and timeslice distribution is observable (#252).
- `pipe_basics` no longer hangs under strict execution (#255).
- The release dispatcher fails closed and explicitly classifies every pinned
  x86_64 syscall (#244, #275). Twenty-four reviewed calls moved from temporary
  unclassified handling to pass-through (#503).
- The strict validation envelope grew in three steps (#521, #542, #550) and
  now contains exactly 57 probes. PR #550's exact head passed all **57/57 at
  L2**, ptrace/default log/no relaxations.

### Record/replay

- The clone-stack envp corruption fix from PR #238 was the base for all
  post-envp measurements.
- Internal-pipe polling, NULL `getsockopt` buffers, deterministic
  `SIOCETHTOOL` failure, and record format handling were hardened.
- Direct `hermit record --strict -- PROGRAM` and the existing
  `record start --strict` form now parse and record successfully (#551);
  `--strict` is a compatibility flag because R/R already uses one serialized
  configuration.
- PR #552 now reads the declared ELF program-header table and seeks directly to
  `PT_INTERP`, bounded by `PATH_MAX`; it covers QEMU's offset `0x7c7000`,
  offsets beyond 16 MiB, and malformed interpreter paths.
- The developed Rustup-proxy fix records an `EpollWaitCancelled` marker when
  exec kills an epoll-blocked sibling. Fresh `cargo --version` and
  `rustc --version` record/replay controls both exit 0 with byte-identical
  stdout, but this work was not committed or opened as a PR by the cutoff.

### QEMU

- QEMU 10.1 TCG, one vCPU, fixed `-icount`, Linux 6.17.13, and the shared
  futex initramfs reached L1 in 166.486 seconds.
- Initial L2 at `be0ad74` completed in 425.82 seconds and matched
  848,391/848,391 messages with no substantive differences.
- An attempted CI gate exposed a real host-placement divergence in
  `/proc/self/status`. Procfs affinity/context-switch normalization removed
  it. A later final-base run matched 1,079,927/1,079,927 messages and
  813,073/813,073 DETLOG+COMMIT messages at L2, ptrace/INFO/no relaxations.
- After rebasing to current `main`, the PR candidate matched
  1,085,768/1,085,768 messages and 817,137/817,137 DETLOG+COMMIT messages
  under the same assurance context.
- The optional `validate.sh --qemu-l2-only` gate has per-phase 300-second
  guards, a positive boot marker, process-group cleanup, and negative tests.
  Draft [#553](https://github.com/rrnewton/hermit/pull/553) was mergeable but
  had not completed the required landing checks at the cutoff.
- Fresh QEMU R/R recording exits 0 after the kernel marker and poweroff.
  Replay clears ELF staging after #552, then diverges because record assigned
  event fds 3/4/5 while replay assigned 4/5/6.

### KVM, DBI, and SaBRe

- KVM now supports filesystem programs, stdin flag checks, legacy pipes,
  `pipe2`, and deterministic supplementary groups (#272, #277, Reverie
  #50/#52/#54, Hermit #544).
- The DynamoRIO raw application-syscall result is now stored before
  post-syscall processing (Reverie #53, Hermit pin #543), removing the known
  mmap-address-as-syscall-number failure family.
- SaBRe's supported draft path is only
  `hermit --backend sabre strace`. Smoke runs for echo/cat/hostname/exec
  complete with prebuilt external artifacts, but strict/verify are unsupported
  and the general run path intentionally fails closed.

### CI and validation

- CI gained stale-run cancellation (#266), a merge gate (#276), stable local
  validation (#269), and zlib headers for self-hosted DynamoRIO builds (#541).
- Definitive full validation at `3c49d19`:
  - `./validate.sh`: **15 passed, 0 failed**.
  - workspace/integrations: **328 run, 328 passed, 257 skipped**.
  - strict gate: **38/38 L2** at that SHA.
  - true/echo/date envelope: L1 3/3, L2 3/3, L3 3/3, L4x20 3/3, R/R 3/3.
  - PMU schedule search: 1/1.
  - docs, Clippy, rustfmt: all exit 0.
- #550 then raised the strict gate to 57/57, and #551/#552 passed their own
  workspace or full validation gates plus required hosted CI. A single full
  validator run on the combined final tip `13ea567` had not completed at the
  cutoff, so no such claim is made.

## Compatibility matrices

The denominators are intentionally different. Each row reports its prescribed
suite and exact evidence boundary.

| Mode / evidence set | Pass | Fail | Other | Evidence |
|---|---:|---:|---:|---|
| Ptrace strict batches 1-69 | 520 | 80 | 11 | Historical 611-case, multi-SHA scenario matrix. |
| Ptrace validation gate at #550 | 57 | 0 | 0 | L2, ptrace, default log, no relaxations. |
| Full validator at `3c49d19` | 15 gates | 0 | rr suite skipped | 328/328 workspace tests; strict gate was then 38/38. |
| Working envelope at `3c49d19` | 3 | 0 | 0 | Each of L1/L2/L3/L4x20/R/R passed for true, echo, date. |
| Post-envp R/R batches 1-21 | 166 | 53 | 0 | 219 historical prescribed cases. |
| Fresh R/R core/interpreter envelope | 14 | 3 | 0 | Before the unlanded Rustup epoll and SIGPIPE fixes. |
| DBI complete 21-row matrix | 15 | 6 | 0 | Historical verifier result; app-syscall fix landed later. |
| DBI 38-program expansion | 20 | 18 | 0 | Current investigation baseline; failures are 60-second timeouts. |
| KVM complete matrix | 10 | 4 | 1 | Historical pre-#544 matrix. |
| KVM targeted post-#544 | 2 | 0 | 0 | `id` and pipe/pipe2 round trip pass L2; full matrix not rerun. |
| QEMU strict Linux boot | 4 verifier runs | 1 pre-fix divergence | PR #553 draft | Passing rows are L2; latest matched 1,085,768 messages. |
| SaBRe current-main run | 0 | 1 | 0 | CLI rejects backend before guest exec; no assurance reached. |

### Matrix interpretation

- Ptrace failures cluster around cross-process rendezvous, live procfs/NSS
  state, nested ptrace/network inputs, persistent output state, and complex
  multi-process scheduling.
- R/R trails ptrace most on pipelines, fd allocation/close ordering,
  worker-thread epoll, and concurrent filesystem workloads.
- DBI's 20/38 expansion is not a semantic failure matrix yet: the 18 rows are
  timeout observations awaiting trace attribution.
- KVM's post-fix targeted results prove the two repaired paths. They do not
  retroactively turn the historical 10/4/1 matrix into an unmeasured 11/3/1
  claim.
- QEMU's L2 evidence is ptrace/INFO/no relaxations. The fast
  `--no-sequentialize-threads` profile remains a non-strict compatibility
  mode and is not counted as determinism assurance.

## PR inventory

### Hermit PRs merged in the window (36)

| PR | Change |
|---:|---|
| [#244](https://github.com/rrnewton/hermit/pull/244) | Fail-closed subscriptions and optional passthrough optimization |
| [#250](https://github.com/rrnewton/hermit/pull/250) | Clone child startup turn |
| [#252](https://github.com/rrnewton/hermit/pull/252) | Per-thread timeslice statistics |
| [#253](https://github.com/rrnewton/hermit/pull/253) | Post-fork configuration Clippy repair |
| [#255](https://github.com/rrnewton/hermit/pull/255) | Strict `pipe_basics` hang fix |
| [#256](https://github.com/rrnewton/hermit/pull/256) | Standard-command strict CI coverage |
| [#258](https://github.com/rrnewton/hermit/pull/258) | `sched_yield` cedes a turn |
| [#260](https://github.com/rrnewton/hermit/pull/260) | Deterministic `SIOCETHTOOL` ENODEV policy |
| [#261](https://github.com/rrnewton/hermit/pull/261) | NULL `getsockopt` R/R buffers |
| [#266](https://github.com/rrnewton/hermit/pull/266) | CI concurrency cancellation |
| [#269](https://github.com/rrnewton/hermit/pull/269) | Stabilized local validation |
| [#272](https://github.com/rrnewton/hermit/pull/272) | KVM filesystem and multi-program support |
| [#273](https://github.com/rrnewton/hermit/pull/273) | `ppoll` determinization |
| [#274](https://github.com/rrnewton/hermit/pull/274) | `waitid` determinization |
| [#275](https://github.com/rrnewton/hermit/pull/275) | Exhaustive x86_64 syscall classification |
| [#276](https://github.com/rrnewton/hermit/pull/276) | Merge queue gate |
| [#277](https://github.com/rrnewton/hermit/pull/277) | KVM stdin/F_GETFL validation and Reverie pin |
| [#329](https://github.com/rrnewton/hermit/pull/329) | Strict QEMU boot evidence and syscall analysis |
| [#503](https://github.com/rrnewton/hermit/pull/503) | Promote 24 reviewed pass-through syscalls |
| [#521](https://github.com/rrnewton/hermit/pull/521) | Strict compatibility validation envelope |
| [#533](https://github.com/rrnewton/hermit/pull/533) | Targeted backend performance benchmarks |
| [#534](https://github.com/rrnewton/hermit/pull/534) | Deterministic self `prlimit64` |
| [#537](https://github.com/rrnewton/hermit/pull/537) | Strict application matrix expansion |
| [#539](https://github.com/rrnewton/hermit/pull/539) | Deterministic `arch_prctl` |
| [#541](https://github.com/rrnewton/hermit/pull/541) | Self-hosted CI zlib headers |
| [#542](https://github.com/rrnewton/hermit/pull/542) | Strict command gate expanded to 38 |
| [#543](https://github.com/rrnewton/hermit/pull/543) | Reverie DBI application-syscall fix pin |
| [#544](https://github.com/rrnewton/hermit/pull/544) | KVM pipes and supplementary groups pin |
| [#545](https://github.com/rrnewton/hermit/pull/545) | Harden deterministic `getrandom` |
| [#546](https://github.com/rrnewton/hermit/pull/546) | Deterministic scheduler affinity |
| [#547](https://github.com/rrnewton/hermit/pull/547) | Deterministic `writev` |
| [#548](https://github.com/rrnewton/hermit/pull/548) | Deterministic `madvise` policy |
| [#549](https://github.com/rrnewton/hermit/pull/549) | Notification-fd determinism contract and tests |
| [#550](https://github.com/rrnewton/hermit/pull/550) | Strict compatibility gate expanded to 57 |
| [#551](https://github.com/rrnewton/hermit/pull/551) | Direct strict recording CLI compatibility |
| [#552](https://github.com/rrnewton/hermit/pull/552) | Read late ELF interpreters during replay |

### Reverie PRs merged in the window (9)

| PR | Change |
|---:|---|
| [#45](https://github.com/rrnewton/reverie/pull/45) | Unknown ioctl output handling |
| [#47](https://github.com/rrnewton/reverie/pull/47) | Backend-provided KVM auxiliary vectors |
| [#48](https://github.com/rrnewton/reverie/pull/48) | External Reverie tools for DBI |
| [#49](https://github.com/rrnewton/reverie/pull/49) | Correct `ppoll` ABI |
| [#50](https://github.com/rrnewton/reverie/pull/50) | KVM filesystem and multi-program runtime |
| [#51](https://github.com/rrnewton/reverie/pull/51) | Merge queue setup |
| [#52](https://github.com/rrnewton/reverie/pull/52) | KVM `F_GETFL` support |
| [#53](https://github.com/rrnewton/reverie/pull/53) | DynamoRIO application-syscall result fix |
| [#54](https://github.com/rrnewton/reverie/pull/54) | KVM pipes and supplementary groups |

No dev-hermit or liteinst2 PR merged during the 18:00-08:16 evidence window.
Hermit #532, #271, #265, #248, and #243 plus Reverie #46 were closed or
superseded rather than landed.

### Open PRs at the cutoff

Hermit had **21 open PRs**:

| PRs | State / required disposition |
|---|---|
| [#553](https://github.com/rrnewton/hermit/pull/553) | Draft, mergeable; optional QEMU strict L2 gate, required landing checks incomplete at cutoff. |
| [#278](https://github.com/rrnewton/hermit/pull/278) | Draft, dirty; DBI mmap-heavy coreutils follow-up, now partly superseded by #543. |
| [#270](https://github.com/rrnewton/hermit/pull/270), [#268](https://github.com/rrnewton/hermit/pull/268) | Draft, dirty; `rt_sigsuspend` and select/pselect6 determinization. |
| [#267](https://github.com/rrnewton/hermit/pull/267) | Draft, dirty; SaBRe tracer prototype, not a deterministic run backend. |
| [#264](https://github.com/rrnewton/hermit/pull/264), [#263](https://github.com/rrnewton/hermit/pull/263), [#262](https://github.com/rrnewton/hermit/pull/262) | Draft, dirty; chaos/R/R coverage, PID/signal tests, and remaining syscall classification. |
| [#259](https://github.com/rrnewton/hermit/pull/259) | Draft, clean; intentionally nondeterministic PMU-skid experiment. |
| [#257](https://github.com/rrnewton/hermit/pull/257), [#254](https://github.com/rrnewton/hermit/pull/254) | Draft, dirty; timeslice naming/target and post-fork policy. |
| [#251](https://github.com/rrnewton/hermit/pull/251) | Non-draft, unstable; syscall logical-timeslice checks. |
| [#249](https://github.com/rrnewton/hermit/pull/249), [#247](https://github.com/rrnewton/hermit/pull/247), [#246](https://github.com/rrnewton/hermit/pull/246), [#245](https://github.com/rrnewton/hermit/pull/245) | Draft/unstable; robust-futex and virtual timer probes. |
| [#242](https://github.com/rrnewton/hermit/pull/242), [#241](https://github.com/rrnewton/hermit/pull/241) | Draft, dirty; syscall-time table and old `sched_yield` livelock work. |
| [#240](https://github.com/rrnewton/hermit/pull/240) | Draft, dirty, human-review; replay kernel-fd close ordering. |
| [#239](https://github.com/rrnewton/hermit/pull/239) | Non-draft, unstable, human-review; vfork child registration/scheduling. |
| [#236](https://github.com/rrnewton/hermit/pull/236) | Draft, dirty, human-review; replay stdout writer leakage. |

Reverie had two open draft human-review PRs:
[#35](https://github.com/rrnewton/reverie/pull/35) for DBI multiprocess
coordination and [#1](https://github.com/rrnewton/reverie/pull/1) for the
experimental SaBRe runtime. DynamoRIO
[#8024](https://github.com/DynamoRIO/dynamorio/pull/8024), liteinst2
[#1](https://github.com/rrnewton/liteinst2/pull/1), and upstream Hermit
[#85](https://github.com/facebookexperimental/hermit/pull/85) also remained
open; no landing is claimed.

## Remaining blockers and active work

1. **Combined final-tip validation:** the last full validator is green at
   `3c49d19`, but #550-#552 landed afterward. Each PR has focused/full local
   evidence and hosted CI, yet `13ea567` still needs one complete
   `./validate.sh` run.
2. **QEMU CI gate:** the gate passes L2 after procfs affinity normalization
   and draft PR #553 is based on current main. Its required hosted landing
   checks were incomplete and the merge gate was red at the cutoff.
3. **QEMU R/R fd allocation:** #552 fixes ELF staging only. Replay diverges at
   event 633 because epoll/eventfd/signalfd descriptors shift by one. PR #240
   is related fd-ordering work but is stale, dirty, and human-review.
4. **R/R parity:** the 57-program measurement is in progress. The Rustup
   exec/epoll fix is validated but uncommitted; the `yes | head` SIGPIPE,
   output-prefix, OFD-offset, and pipe-EOF fix is still in multi-round review.
5. **DBI timeouts:** the current 38-program expansion reports 20 pass and 18
   60-second timeouts. Log-first attribution on `sort --version` is active.
6. **KVM processes:** `fork` remains ENOSYS, blocking `bc` and
   `yes | head` after their pipe setup succeeds. Reverie issue #55 tracks it.
7. **SaBRe contract/security:** current main has no SaBRe backend. Draft #267
   depends on unmerged Reverie artifacts and an external loader; it lacks
   namespaces, Detcore scheduling, strict/verify, complete exec semantics,
   static-binary support, and a secure artifact/plugin trust boundary.
8. **Fail-closed syscall debt:** the last exhaustive snapshot was 107
   determinized, 39 reviewed pass-through, 227 unclassified of 373. Later
   handler PRs reduce that debt, but no fresh exhaustive recount exists.
   Priority policies remain `prctl`, signal/PID translation, socket
   names/options, logical timers, `openat2`/pidfd bookkeeping, and procfs
   normalization.
9. **Host/environment boundaries:** external networks, changing filesystems,
   live NSS/procfs counters, PMU skid, and absent optional `third-party/rr`
   remain outside or conditional within the measured assurance envelope.

Active TaskGraph work at the cutoff included the QEMU L2 gate, QEMU
snapshot/resume research, DBI timeout investigation, the 57-program R/R
matrix, and the R/R SIGPIPE fix. The refreshed SaBRe status check had closed.

## Recommended next sequence

1. Finish PR #553's required checks and land the optional QEMU L2 gate.
2. Run the complete 15-gate validator on current Hermit `main`.
3. Complete the 57-program R/R matrix, then fix the QEMU/event-fd descriptor
   divergence with an explicit replay-fd allocation contract.
4. Publish and land the reviewed Rustup epoll-cancellation fix and finish the
   `yes | head` SIGPIPE series.
5. Attribute the 18 DBI timeouts and rerun the full DBI matrix after the landed
   application-syscall fix.
6. Implement KVM process creation before claiming broader multi-process parity.
7. Treat SaBRe as a tracer milestone unless a separate reviewed design funds
   the Detcore, isolation, artifact-trust, and exec-semantics work.
8. Recount all 373 syscall classifications after the late handler landings and
   continue the fail-closed queue from the measured remaining priorities.

## Primary evidence sources

- `ai_docs/transient/OVERNIGHT_SUMMARY_20260724.md`
- `ai_docs/transient/strict-compat-matrix.md`
- `ai_docs/transient/coverage-matrix-20260723.md`
- TaskGraph notes for `impl-validate-post-landing`,
  `impl-enshrine-qemu-l2-ci`, `impl-fix-elf-buffer-qemu-rr`,
  `impl-record-strict-cli-fix`, `impl-rr-compat-expansion`,
  `impl-fix-rr-yes-sigpipe`, `impl-rr-cargo-rustc-replay-fix`,
  `impl-dbi-timeout-investigation`, `impl-kvm-fix-failures`,
  `impl-madvise-handler`, `impl-new-syscall-handlers-batch2`, and
  `impl-assess-land-sabre-pr`
- Live GitHub merged/open PR and branch-head queries at the stated cutoff
