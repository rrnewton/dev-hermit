# Open PR Review Plan (2026-07-22)

Status snapshot: **2026-07-22 12:47 UTC**. GitHub state and checks are live and
may have changed after this timestamp. `GH` means the GitHub-hosted regular
job; `HW` means the self-hosted host-dependent job.

## Executive Summary

There are **47 open pull requests**: 43 in `rrnewton/hermit` and four in
`rrnewton/reverie`.

- **19 human-review-needed:** 17 Hermit PRs and two Reverie PRs carry the
  explicit `human-review` label.
- **28 CI-broken:** these do not carry the explicit review label, but their
  required checks are failed, cancelled, absent, or their branch conflicts.
- **0 free-to-land:** no open PR has a complete green required check set.
- 44 PRs are drafts. The only ready PRs are Hermit `#7`, `#8`, and `#80`;
  all three are CI-broken.
- Check outcomes across both repositories are: 21 `GH pass; HW fail`, 15
  `GH pass; HW cancelled`, two `GH fail; HW fail`, and nine with no checks.
- Thirteen branches conflict with their base: Hermit `#7`, `#8`, `#25`,
  `#27`, `#33`, `#35`, `#41`, `#70`, `#90`, `#101`; Reverie
  `#8`, `#12`, `#15`.
- Reverie `#11`, `#13`, and `#14` merged at 12:42-12:45 UTC. Their open
  dependents should now rebase on Reverie `main` and rerun CI.

The primary category below is action-oriented and mutually exclusive:
`human-review-needed` takes precedence when the repository label is present;
otherwise any non-green, missing, or conflicted check state is `CI-broken`.
A PR is `free-to-land` only when it has no human-review gate, is mergeable,
and all required checks pass. Drafts without the label still need normal review
after CI is repaired.

## Hermit PR Matrix

| PR | Title | Labels | CI and merge state | Dependency or next action | Primary category |
| --- | --- | --- | --- | --- | --- |
| [#101](https://github.com/rrnewton/hermit/pull/101) | SQLite strict-mode compatibility probe | - | No checks; conflict | Rebase; validate the known 13 deterministic failures and lock-test stop | CI-broken |
| [#100](https://github.com/rrnewton/hermit/pull/100) | Baseline strict determinization overhead | - | GH pass; HW cancelled | Rerun HW; refresh stand-ins after `#77/#85` if those land | CI-broken |
| [#99](https://github.com/rrnewton/hermit/pull/99) | Strict Redis integration coverage | - | GH pass; HW cancelled | Ownership/restart fixes are pushed; rerun HW | CI-broken |
| [#98](https://github.com/rrnewton/hermit/pull/98) | Strict compression determinism experiment | - | GH pass; HW cancelled | Independent; rerun HW | CI-broken |
| [#97](https://github.com/rrnewton/hermit/pull/97) | Python stdlib strict-mode coverage | - | GH pass; HW cancelled | Count/timeout/CI fixes are pushed; rerun HW | CI-broken |
| [#96](https://github.com/rrnewton/hermit/pull/96) | Expand architecture documentation | - | GH pass; HW cancelled | Corrections are pushed; review after implementation PRs stabilize | CI-broken |
| [#95](https://github.com/rrnewton/hermit/pull/95) | Backend selector for `hermit run` | - | GH pass; HW cancelled | Availability fixes are pushed; DBI still has no Detcore launcher | CI-broken |
| [#93](https://github.com/rrnewton/hermit/pull/93) | Run validation with cargo-nextest | - | GH pass; HW cancelled | Reconcile workflow edits, then rerun HW | CI-broken |
| [#92](https://github.com/rrnewton/hermit/pull/92) | Fail strict mode without CPUID interception | - | GH pass; HW cancelled | Reverie `#14` is merged; update/rebase and rerun | CI-broken |
| [#91](https://github.com/rrnewton/hermit/pull/91) | Fix signal interruption of blocking retries | - | GH pass; HW cancelled | Review/land after `#79`, then rerun signal tests | CI-broken |
| [#90](https://github.com/rrnewton/hermit/pull/90) | Fix stale scratch files in RR mmap tests | human-review | No checks; conflict | Stacked on `#81`; rebase after `#89` | human-review-needed |
| [#89](https://github.com/rrnewton/hermit/pull/89) | Ignore unused syscall registers in replay | human-review | No checks; mergeable | Stacked on `#81`; integrate before `#90` | human-review-needed |
| [#88](https://github.com/rrnewton/hermit/pull/88) | Harden record/replay failure handling | human-review | GH pass; HW cancelled | Rebase after the `#81/#89/#90` replay stack | human-review-needed |
| [#87](https://github.com/rrnewton/hermit/pull/87) | Fail loudly instead of silently skipping tests | human-review | GH fail; HW fail | Decide host/optional-tool policy before retrying | human-review-needed |
| [#86](https://github.com/rrnewton/hermit/pull/86) | Quiet `validate.sh` output | - | GH pass; HW fail | Repair HW; land before reconciling `#93` | CI-broken |
| [#85](https://github.com/rrnewton/hermit/pull/85) | Reproducible Ninja strict-mode test | human-review | GH pass; HW cancelled | Rerun HW | human-review-needed |
| [#84](https://github.com/rrnewton/hermit/pull/84) | Minimal deterministic procfs | human-review | GH pass; HW cancelled | Adversarial fixes are pushed; rerun HW | human-review-needed |
| [#83](https://github.com/rrnewton/hermit/pull/83) | Focused LevelDB strict integration coverage | human-review | GH pass; HW fail | Repair HW and reconcile CI workflow edits | human-review-needed |
| [#81](https://github.com/rrnewton/hermit/pull/81) | Port rr syscall suite under Hermit | human-review | GH pass; HW cancelled | Stack root for `#89/#90`; validate combined stack | human-review-needed |
| [#80](https://github.com/rrnewton/hermit/pull/80) | Fix concurrent connect and `F_SETFL` regressions | human-review | GH pass; HW fail | Follow-up to merged `#66`; repair HW | human-review-needed |
| [#79](https://github.com/rrnewton/hermit/pull/79) | Handle blocking `rt_sigsuspend` | human-review | GH pass; HW fail | Signal foundation for `#91`; repair HW | human-review-needed |
| [#78](https://github.com/rrnewton/hermit/pull/78) | Fix unsupported syscall panic enforcement | human-review | GH pass; HW cancelled | Reconcile overlapping `run.rs` changes with `#92/#95` | human-review-needed |
| [#77](https://github.com/rrnewton/hermit/pull/77) | LULESH OpenMP strict-mode determinism test | human-review | GH pass; HW fail | Repair HW; informs the real workload for `#100` | human-review-needed |
| [#76](https://github.com/rrnewton/hermit/pull/76) | Port Hermit analyze scenarios | - | GH pass; HW fail | Repair HW | CI-broken |
| [#75](https://github.com/rrnewton/hermit/pull/75) | Block io_uring syscalls with `ENOSYS` | - | GH pass; HW fail | Repair HW | CI-broken |
| [#74](https://github.com/rrnewton/hermit/pull/74) | Document syscall coverage and gaps | - | GH pass; HW fail | Repair HW; refresh after syscall PRs land | CI-broken |
| [#73](https://github.com/rrnewton/hermit/pull/73) | Test default thread scheduling fairness | - | GH pass; HW fail | Repair HW | CI-broken |
| [#72](https://github.com/rrnewton/hermit/pull/72) | Harden verify-mode diagnostics and coverage | - | GH pass; HW fail | Repair HW | CI-broken |
| [#71](https://github.com/rrnewton/hermit/pull/71) | Hermit run compatibility matrix | - | GH pass; HW fail | Repair HW | CI-broken |
| [#70](https://github.com/rrnewton/hermit/pull/70) | Document working QEMU Linux boot profile | - | GH pass; HW fail; conflict | Rebase and refresh against current QEMU behavior | CI-broken |
| [#62](https://github.com/rrnewton/hermit/pull/62) | Improve unsupported syscall diagnostics | - | No checks; mergeable | Stacked on `#41`; validate after root is repaired | CI-broken |
| [#54](https://github.com/rrnewton/hermit/pull/54) | Record speculative determinism stress evidence | - | GH pass; HW fail | Repair HW | CI-broken |
| [#50](https://github.com/rrnewton/hermit/pull/50) | Add fork and exec determinism tests | - | No checks; mergeable | Stacked on `#27`; validate after root rebase | CI-broken |
| [#48](https://github.com/rrnewton/hermit/pull/48) | Document arbitrary binary compatibility | - | GH pass; HW fail | Repair HW; refresh against current syscall support | CI-broken |
| [#47](https://github.com/rrnewton/hermit/pull/47) | Schedule bisection for race localization | - | GH pass; HW fail | Repair HW | CI-broken |
| [#42](https://github.com/rrnewton/hermit/pull/42) | Deterministic `select` and `pselect6` | human-review | No checks; mergeable | Stacked on `#25`; validate combined QEMU stack | human-review-needed |
| [#41](https://github.com/rrnewton/hermit/pull/41) | Make unsupported syscalls fail closed | human-review | GH fail; HW fail; conflict | Root for `#62`; rebase and repair both jobs | human-review-needed |
| [#35](https://github.com/rrnewton/hermit/pull/35) | Coherent virtual time for concurrent guests | human-review | GH pass; HW fail; conflict | Rebase and rerun QEMU/time coverage | human-review-needed |
| [#33](https://github.com/rrnewton/hermit/pull/33) | Add no-namespace execution mode | - | GH pass; HW fail; conflict | Rebase and rerun reduced-isolation coverage | CI-broken |
| [#27](https://github.com/rrnewton/hermit/pull/27) | Support `CLONE_VFORK` scheduling | human-review | GH pass; HW fail; conflict | Root for `#50`; rebase first | human-review-needed |
| [#25](https://github.com/rrnewton/hermit/pull/25) | Deterministic QEMU syscall handling | human-review | No checks; conflict | Root for `#42`; rebase and establish CI | human-review-needed |
| [#8](https://github.com/rrnewton/hermit/pull/8) | Portable setup for expanded PMU CI | - | GH pass; HW fail; conflict | Decide whether current workflow supersedes it | CI-broken |
| [#7](https://github.com/rrnewton/hermit/pull/7) | Schedule-search E2E tests in CI | - | GH pass; HW cancelled; conflict | Rebase and rerun the PMU gate | CI-broken |

## Reverie PR Matrix

| PR | Title | Labels | CI and merge state | Dependency or next action | Primary category |
| --- | --- | --- | --- | --- | --- |
| [#15](https://github.com/rrnewton/reverie/pull/15) | Intercept CPUID in DynamoRIO | - | No checks; conflict | `#13` merged; rebase on `main` and establish CI | CI-broken |
| [#12](https://github.com/rrnewton/reverie/pull/12) | Install filtered CPUID table on KVM vCPUs | human-review | No checks; conflict | `#11` merged; rebase on `main` and establish CI | human-review-needed |
| [#8](https://github.com/rrnewton/reverie/pull/8) | Extend KVM syscall interception | human-review | GH pass; HW fail; conflict | Rebase against merged shared KVM API and repair HW | human-review-needed |
| [#1](https://github.com/rrnewton/reverie/pull/1) | Restore experimental SaBRe backend | - | GH pass; HW fail | Repair HW; add a human-review label before landing native backend work | CI-broken |

## Human Review Focus

These are the 19 PRs carrying the explicit `human-review` label. CI remains a
separate blocker for every item.

- **Hermit #25:** Validate the shared logical-time polling design, timeout ABI
  handling, and fd-resource release for QEMU's `ppoll/readv/writev` traffic.
- **Hermit #27:** Check that `CLONE_VFORK` blocks and releases the parent with
  correct scheduler registration, pedigree, chaos, and replay semantics.
- **Hermit #35:** Decide whether the concurrent wall-clock model and RDTSC
  mapping preserve the intended semantics in both sequential and parallel modes.
- **Hermit #41:** Review the fail-closed syscall policy, compatibility escape
  hatch, and corrected `keyctl`/strict-mode behavior before its child `#62`.
- **Hermit #42:** Review `select/pselect6` timeout conversion, guest memory
  writeback, temporary signal masks, and deterministic ready-fd ordering.
- **Hermit #77:** Confirm the test proves real OpenMP parallelism and compares
  anchored completion markers plus complete state fingerprints across runs.
- **Hermit #78:** Confirm strict unsupported-syscall enforcement cannot be
  bypassed through namespace-only, strace, or passthrough flag combinations.
- **Hermit #79:** Validate pending/masked signal behavior and atomic
  `rt_sigsuspend` interruption; this is the semantic base for `#91`.
- **Hermit #80:** Check the interaction among logical nonblocking state,
  physical `F_SETFL`, concurrent connect completion, and replay.
- **Hermit #81:** Review test admission, timeouts, skip behavior, and failure
  reporting for the broad rr port before accepting child fixes `#89/#90`.
- **Hermit #83:** Confirm the LevelDB workload fails closed, exercises intended
  concurrency/recovery behavior, and belongs in the selected CI tier.
- **Hermit #84:** Review procfs path coverage, `openat2` resolve semantics,
  metadata fidelity, and the boundary between emulation and denial.
- **Hermit #85:** Confirm the Ninja suite actually runs the claimed test count,
  isolates cleanup, and compares strong completion and state evidence.
- **Hermit #87:** Make the policy decision on which unavailable hardware,
  namespaces, TTYs, and optional tools are hard failures rather than skips.
- **Hermit #88:** Validate fail-closed record/replay behavior across signal
  masks, nonblocking timeouts, descendants, and terminal-event accounting.
- **Hermit #89:** Verify the syscall arity table is complete enough that ignored
  registers remove false desyncs without masking meaningful argument changes.
- **Hermit #90:** Check scratch-file ownership and cleanup across repeated rr
  mmap cases, including stale artifacts and parallel invocation.
- **Reverie #8:** Review KVM syscall interception contracts, guest memory
  access, lifecycle/error propagation, and the host-dependent validation plan.
- **Reverie #12:** Review CPUID filtering and per-vCPU installation against the
  shared KVM API now merged in `#11`.

## Prioritized Review Plan

1. **Review dependency roots first.** Start with Hermit `#25` before `#42`,
   `#27` before `#50`, `#41` before `#62`, and the `#81/#89/#90` rr
   stack in that order. Reviewing leaves first would be invalidated by root
   changes.
2. **Prioritize the QEMU/backend lane.** After `#25`, review Reverie `#8`
   and `#12`; both must rebase onto the shared KVM API merged in `#11`.
   Reverie `#15` is unlabelled but is native DBI instruction-rewriting code:
   add the review gate, rebase it onto merged `#13`, and review it before any
   claim that Hermit DBI execution is usable.
3. **Review core determinism correctness.** Next take Hermit `#35` virtual
   time, `#79` then `#91` signals, `#80` network state, `#84` procfs,
   and `#88` record/replay. These affect broad guest behavior and deserve
   attention before test-only additions.
4. **Review enforcement boundaries.** Review Hermit `#41` and `#78`
   together for fail-closed behavior, then inspect the fixed `#92` CPUID
   capability path and `#95` backend availability path. Reverie `#14` is
   already merged; `#95` remains deliberately fail-closed because the DBI
   smoke test found no Detcore process launcher.
5. **Review workload and CI policy last.** Take `#77`, `#83`, and `#85`
   after core behavior is stable. Hold `#87` until the desired runner
   capabilities and skip policy are explicit; it currently fails both jobs.
6. **Finish with documentation and evidence.** Refresh `#74`, `#96`,
   `#100`, and `#101` only after the implementation and workload PRs they
   describe settle. This avoids approving claims or baselines that immediately
   become stale.

## CI Repair Queue

No merge should occur from this snapshot. First repair shared branch state,
then rerun checks at the exact rebased heads.

1. Rebase the 13 conflicted PRs, starting with stack roots rather than leaves.
2. Establish checks for the nine no-check PRs: Hermit `#25`, `#42`, `#50`,
   `#62`, `#89`, `#90`, `#101`; Reverie `#12`, `#15`.
3. Rerun the 15 cancelled HW jobs; cancellation is not evidence of a pass.
4. Triage the two dual failures first (`#41/#87`), then the 21 HW-only
   failures. Several failures are old enough that they may disappear only
   after rebasing onto current `main`, but that must be demonstrated by CI.
5. Change drafts to ready only after their category-specific review and current
   CI both pass. At this snapshot, the free-to-land queue is empty.
