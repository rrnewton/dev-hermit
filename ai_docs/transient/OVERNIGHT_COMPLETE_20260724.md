# Overnight Completion Status - 2026-07-24

## Evidence boundary

Status was reconciled at **2026-07-24T10:29:10Z** from live GitHub PR data,
the exact PR validation records, and the committed overnight report. This is
an aggregation report; this task did not rerun product tests.

- dev-hermit `main` before this report: `571ec7284faddc7a959cd40a8a7aa0047da5a799`
  (0 ahead, 0 behind `origin/main`).
- Hermit remote `main`: `a2926507aafb9c922cbe230490f1cee5ebcea586`
  ([#562](https://github.com/rrnewton/hermit/pull/562)).
- Reverie remote `main`: `c93d31f3ebd4b1af5487a2004bdcfeb5903a16f5`
  ([#54](https://github.com/rrnewton/reverie/pull/54)).
- Reporting window: 2026-07-23 18:00 UTC through the reconciliation time
  above.

The parent commit before this report pins Hermit at `4a52eeb63e995c6bd56497dc528d722b81c203f7`
and Reverie at `e4ff635f1661bde90eb0ea28e257c0f5e38a3323`. Those pins lag the product
heads above and were not advanced by this documentation-only task because the
primary Hermit checkout contains unrelated concurrent state.

## Final summary

- **54 product PRs landed:** 45 in Hermit and 9 in Reverie. This is the exact
  live count for the reporting window, replacing the earlier "20+" estimate.
- The ptrace strict gate reached **118/118 at L2** on PR #562's exact head
  `84d50e8d066adb319b223ba480fd1bae8ab1f451`: `./validate.sh --no-label-pr`
  passed 15/15 gates and all 118 strict probes with default logging and no
  relaxations.
- The 118-probe result is not an L4 claim. A separate strict-only repetition
  passed 117/118 because the pre-existing Node probe intermittently changed
  shared-library open order; standalone Node repetitions were pass/fail/pass.
- QEMU Linux boot reached L2 under ptrace/INFO/no relaxations. The landed CI
  gate matched **1,085,768/1,085,768 messages** and
  **817,137/817,137 DETLOG/scheduler commits**.
- Record/replay improved from a complete 36/57 output-correct baseline to an
  accounted 50/57 after #560 repaired all 14 prior stdout-mismatch rows. This
  is not a single full 57-row rerun on merged `main`.

## Backend and mode matrices

The denominators differ because no same-SHA all-backend run over the complete
118-program ptrace gate exists. Each row retains its actual evidence set.

| Backend or mode | Result | Evidence and qualification |
|---|---:|---|
| Ptrace strict gate | **118/118 L2** | PR #562 exact head `84d50e8`; `--strict --verify`, default log, no relaxations. |
| DBI expansion | **20/38** | Hermit `3c49d19`; two-run DBI verifier. Seventeen lifecycle timeouts and one non-ELF wrapper account for the other 18 rows. |
| KVM matrix | **31/57 L2** | Hermit `2df293b`; default log, no relaxations, zero timeouts. Nineteen failures require clone/fork; seven expose exec, loader, fd, directory, or mount gaps. |
| Record/replay baseline | **36/57** | Complete output-correct baseline at `2df293b`; all 57 recorded and 50 replays exited zero. |
| Record/replay after #560 | **50/57 accounted** | Baseline 36 plus targeted 14/14 repair validation at #560 head `380e042`; no full merged-tip rerun. |
| Historical post-envp R/R | **166/219** | Prescribed batches 1-21; 53 failures. Separate corpus from the common 57-program matrix. |
| QEMU Linux boot | **L2** | PR #553 head `69854b4`; positive kernel boot marker and exact trace equality under `--strict --verify`. |

DBI remains blocked on native clone/exec lifecycle handling. KVM's largest
cluster is missing fork/clone support. R/R's seven remaining common-matrix
rows are `cargo`, `rustc`, `xz`, `paste`, `comm`, `join`, and `mktemp`; PR
[#555](https://github.com/rrnewton/hermit/pull/555) is active work on the fd
namespace/close-ordering portion.

## QEMU status

Four strict Linux-boot L2 verifier runs completed during the overnight work.
The final landed gate is [#553](https://github.com/rrnewton/hermit/pull/553),
merged as `2df293bde92bded0893fbe5eb83a633453dabcb0`.

QEMU recording reaches `SHARED_FUTEX_QEMU_KERNEL_OK` and poweroff. Replay gets
past the late-`PT_INTERP` ELF staging failure fixed by #552, then stops at an
fd-number divergence around syscall event 633. QEMU record/replay therefore
does not yet have an L2-equivalent completion claim.

## PR landings

Hermit merged 45 PRs in the window:

`#244`, `#245`, `#249`, `#250`, `#252`, `#253`, `#255`, `#256`, `#258`,
`#260`, `#261`, `#266`, `#269`, `#272`, `#273`, `#274`, `#275`, `#276`,
`#277`, `#329`, `#503`, `#521`, `#533`, `#534`, `#537`, `#539`, `#541`,
`#542`, `#543`, `#544`, `#545`, `#546`, `#547`, `#548`, `#549`, `#550`,
`#551`, `#552`, `#553`, `#554`, `#557`, `#558`, `#559`, `#560`, and `#562`.

Reverie merged 9 PRs in the window: `#45`, `#47`, `#48`, `#49`, `#50`,
`#51`, `#52`, `#53`, and `#54`.

## Review and follow-up state

- [#239](https://github.com/rrnewton/hermit/pull/239) remains open,
  non-draft, mergeable, CI-unstable, and explicitly labeled `human-review`.
- [#553](https://github.com/rrnewton/hermit/pull/553) is **merged**, not
  awaiting review. It carries `locally-validated` and `post-facto-review`.
- [#560](https://github.com/rrnewton/hermit/pull/560) is **merged**, not
  awaiting review. It carries `locally-validated` and `post-facto-review`.
- Other open PRs carrying `human-review` at reconciliation time are #236,
  #240, and #246. PR #555 remains draft/conflicting while its corrected fd
  namespace implementation is validated in its owned worktree.

## Next measurements

1. Land or supersede #555, then rerun the complete 57-row R/R matrix on one
   merged `main` SHA.
2. Fix DBI clone/exec lifecycle handling and rerun the 38-row set before
   expanding it to all 118 ptrace-gate programs.
3. Implement KVM fork/clone, then rerun the common corpus.
4. Stress the intermittent Node row and fix the excluded `timeout 1 true`
   scheduler interaction before making any L4 compatibility claim.
5. Advance the parent submodule pins only after the coordinator validates and
   intentionally stages the exact product SHAs.
