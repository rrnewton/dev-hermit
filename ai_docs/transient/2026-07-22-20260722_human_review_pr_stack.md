# Human-Review PR Stacking Plan — rrnewton/hermit

- **Date:** 2026-07-22
- **Author:** hermit-020 (task `impl-stack-human-review-prs`)
- **Scope:** Research + documentation only. Determine a linear stacking order
  for the open **human-review** PRs so they form a clean path
  `main → speculative frontier`, and record conflict/dependency rationale.
  Rebuilding the speculative branch and editing PR descriptions are follow-up
  implementation steps (not performed here).
- **Base of record:** `origin/main` = `5c5c73f` ("Fail-closed syscall handlers:
  3→69 tests enabled (Pread64+Rseq+batch2) (#129)").
- **Method:** `with-proxy gh pr list/view` for the inventory and per-PR changed
  files; file-overlap matrix + existing PR base branches for dependency order.

Open PRs total: **70**. Human-review labeled: **17** (all authored by
`rrnewton`). The sibling task `p0-land-non-review-prs` has been landing the
non-review PRs to `main`; only human-review PRs should remain for stacking.

---

## 1. Inventory (17 human-review PRs)

| PR | Title | Head branch | Base | Mergeable* |
|----|-------|-------------|------|-----------|
| #25 | Add deterministic QEMU syscall handling | impl-qemu-syscall-fixes | main | CONFLICTING |
| #27 | Support CLONE_VFORK scheduling | feature/clone-vfork | main | CONFLICTING |
| #35 | Fix coherent virtual time for concurrent guests | codex/coherent-virtual-time | main | CONFLICTING |
| #41 | Make unsupported syscalls fail closed | impl-fail-closed-syscalls-slot04 | main | CONFLICTING |
| #42 | Add deterministic select and pselect6 handling | codex/select-pselect6-pr25 | **impl-qemu-syscall-fixes (#25)** | MERGEABLE |
| #77 | Add LULESH OpenMP strict-mode determinism test | impl-oss-lulesh-test-slot06 | main | MERGEABLE |
| #78 | Fix unsupported syscall panic enforcement | impl-fix-panic-unsupported | main | MERGEABLE |
| #79 | Handle blocking rt_sigsuspend calls | impl-rt-sigsuspend-handler-slot09 | main | MERGEABLE |
| #80 | Fix concurrent connect and F_SETFL regressions | codex/pr66-network-blockers | main | MERGEABLE |
| #81 | Port rr syscall test suite to run under Hermit | port-rr-test-suite-slot06 | main | CONFLICTING |
| #83 | Add focused LevelDB strict integration coverage | impl-oss-leveldb-test-slot03-v2 | main | CONFLICTING |
| #84 | Simulate a minimal deterministic procfs | impl-fix-proc-minimal-v2-slot12 | main | CONFLICTING |
| #85 | Add reproducible Ninja strict-mode test | impl-oss-ninja-test-current-slot06 | main | MERGEABLE |
| #87 | Fail loudly instead of silently skipping tests | impl-no-silent-skips | main | CONFLICTING |
| #88 | Harden record/replay failure handling | impl-record-replay-hardening-slot02 | main | MERGEABLE |
| #89 | fix(replay): ignore unused syscall arg registers in desync detection | fix-statfs-desync-arity | **port-rr-test-suite-slot06 (#81)** | MERGEABLE |
| #90 | Fix stale scratch files in RR mmap tests | impl-fix-rr-mmap-stale-files | **port-rr-test-suite-slot06 (#81)** | MERGEABLE→see note |

\* Mergeable is GitHub's status against the PR's **current base**, not against
the proposed stack. A `MERGEABLE` PR based on a stale branch can still conflict
once its base is rebased onto current `main`. Treat this column as a hint only.

**Pre-existing stacks (base ≠ main):** #42→#25, #89→#81, #90→#81.

---

## 2. ⚠️ Overlap with already-landed non-review work

`main` has advanced well past where these branches were cut. Several
human-review PRs touch subsystems that **already landed** via non-review PRs and
therefore need reconciliation/de-duplication, not clean application:

- **Fail-closed / unsupported-syscall enforcement is already on `main`.**
  `main` (5c5c73f, and #119/#125/#129) added fail-closed handlers plus ratchet
  infra (`hermit-cli/tests/fail_closed_known_failures.tsv`,
  `fail_closed_allowed_ignores.tsv`) and `panic_on_unsupported_syscalls`
  gating in `detcore/src/lib.rs`, `hermit-cli/src/bin/hermit/run.rs`,
  `hermit-cli/src/metadata.rs`, `detcore/tests/testutils/src/lib.rs`.
  → **#41** ("Make unsupported syscalls fail closed") and **#78** ("Fix
  unsupported syscall panic enforcement") overlap this heavily. #41 is very
  likely **largely superseded**; #78 may be a residual fix. Both need a human
  decision on what remains unique before stacking.
- **`detcore/src/procfs.rs` already exists on `main`.** → **#84** ("Simulate a
  minimal deterministic procfs", +1048/−359) will conflict substantially and
  must be reconciled against the landed procfs implementation.

These three (#41, #78, #84) are the highest-risk items and are flagged inline
in the order below.

---

## 3. File-overlap conflict matrix (hot files → PRs)

Ordering is driven mainly by shared-file edits. The near-universal hub is
`detcore/src/lib.rs`.

| File | PRs touching it |
|------|-----------------|
| `detcore/src/lib.rs` | #25, #27, #35, #41, #42, #78, #79, #84 |
| `detcore/tests/misc/mod.rs` | #25, #27, #42, #80, #87 |
| `detcore/src/syscalls/helpers.rs` | #25, #42, #80 |
| `detcore/src/syscalls/threads.rs` | #25, #27 |
| `detcore/src/syscalls/io.rs` | #25, #42 |
| `detcore/src/syscalls/signal.rs` | #25, #79 |
| `detcore/src/syscalls/files.rs` | #25, #84 |
| `detcore/src/tool_global.rs` | #27, #35 |
| `detcore/src/fd.rs` | #80, #84 |
| `hermit-cli/src/bin/hermit/run.rs` | #41, #78 |
| `hermit-cli/tests/hermit_modes.rs` | #41, #78, #87 |
| `tests/c/simple/keyctl_syscall_nostdlib.c` | #41, #78 |
| `tests/c/simple/unsupported_syscall_nostdlib.c` | #41, #78 |
| `.github/workflows/ci.yml` | #81, #83, #87 |
| `validate.sh` | #81, #87 |
| `hermit-cli/tests/rr_suite.rs` | #81, #90 |
| `docs/rr-test-suite.md` | #81, #90 |
| `hermit-cli/src/lib.rs` | #88, #89 |
| `hermit-cli/src/replayer/mod.rs` | #88, #89 |

**No-code-overlap leaves** (experiments/docs only, safe anywhere):
- **#77** — `experiments/lulesh-openmp/**` only.
- **#85** — `experiments/ninja-strict/**` only.

---

## 4. Dependency clusters

- **A. rr test suite:** #81 (foundation) → #90 (rr_suite.rs, docs) → #89
  (replay arity; bridges into record/replay). #83 (LevelDB) shares only
  `ci.yml` with #81.
- **B. record/replay:** #88 shares `hermit-cli/src/lib.rs` +
  `replayer/mod.rs` with #89 → order #89 then #88.
- **C. detcore syscall core (lib.rs hub):** #25 is the broad foundation;
  #42 (on #25), #27, #35, #79, #80, #84 layer on top by shared file.
- **D. fail-closed:** #78 then #41 (near-identical file set) — but see §2;
  both overlap landed work.
- **E. test-infra sweep:** #87 touches `ci.yml` + `validate.sh` +
  `hermit_modes.rs` + `misc/mod.rs` — the widest surface; goes late.
- **F. leaves:** #77, #85 — anywhere.

---

## 5. Proposed linear stacking order (main → frontier)

Applied bottom-first on top of `origin/main` (5c5c73f). Each PR is annotated
with the "applies after" predecessor to record in its description (step 6).
Every branch must first be **rebased onto current `main`** before it is layered,
because `main` moved substantially after these branches were cut.

| # | PR | Applies after | Why here / conflict notes |
|---|----|---------------|---------------------------|
| 1 | **#81** Port rr test suite | main | rr tooling foundation: `ci.yml`, `validate.sh`, `.gitmodules`, `third-party/rr`, `rr_suite.rs`. CONFLICTING vs main (ci.yml/validate.sh moved by landed CI PRs) — rebase first. |
| 2 | **#90** RR mmap stale files | #81 | Edits `rr_suite.rs` + `docs/rr-test-suite.md`, both introduced by #81. Already based on `port-rr-test-suite-slot06`; re-target after #81 rebases. |
| 3 | **#83** LevelDB coverage | #81 | Shares only `ci.yml` with #81; land after so the ci.yml block composes. CONFLICTING vs main (ci.yml). |
| 4 | **#89** replay desync arity | #81 | Based on #81. Edits `hermit-cli/src/lib.rs` + `replayer/mod.rs` — shared with #88; place before #88 (narrow fix first). |
| 5 | **#88** record/replay hardening | #89 | Broad `hermit-cli` replayer/lib.rs/main.rs changes; rebases over #89's narrow arity fix on the shared files. |
| 6 | **#25** QEMU syscall handling | #88 | Broadest `detcore/src/syscalls/*` + `lib.rs` foundation; anchors the detcore cluster. CONFLICTING vs main (lib.rs dispatch changed by landed fail-closed handlers) — rebase/reconcile lib.rs. |
| 7 | **#42** select/pselect6 | #25 | Already based on `impl-qemu-syscall-fixes`; shares `lib.rs`, `helpers.rs`, `io.rs`, `misc/mod.rs` with #25. |
| 8 | **#27** CLONE_VFORK | #25 | Shares `threads.rs`, `misc/mod.rs`, `lib.rs` with #25; introduces `tool_global.rs`/`tool_local.rs`. |
| 9 | **#35** coherent virtual time | #27 | Shares `tool_global.rs` with #27, plus `lib.rs`; adds `detcore-model/src/time.rs`. |
| 10 | **#79** rt_sigsuspend | #25 | Shares `syscalls/signal.rs` + `lib.rs` with #25. |
| 11 | **#80** connect / F_SETFL | #42 | Shares `syscalls/helpers.rs` (with #25/#42) and `misc/mod.rs`; adds `fd.rs` (shared with #84 below). |
| 12 | **#84** minimal procfs | #80 | Shares `fd.rs` with #80 and `files.rs` with #25, plus `lib.rs`. ⚠️ `procfs.rs` **already on main** — reconcile against landed procfs (see §2). |
| 13 | **#78** fix panic enforcement | #84 | `lib.rs`, `run.rs`, `hermit_modes.rs`, keyctl/unsupported `.c`. ⚠️ Overlaps landed fail-closed work (§2) — confirm residual delta first. |
| 14 | **#41** make syscalls fail closed | #78 | Near-identical file set to #78 (`run.rs`, `hermit_modes.rs`, both `.c`, `lib.rs`) + `config.rs`, `metadata.rs`, `testutils`. ⚠️ Likely **largely superseded** by landed #129 — strong de-dup candidate. |
| 15 | **#87** fail loudly, no silent skips | #83 | Widest test-infra surface: `ci.yml` (#81/#83), `validate.sh` (#81), `hermit_modes.rs` (#41/#78), `misc/mod.rs` (#25/#27/#42/#80). Land last so it rebases over everything. |
| 16 | **#77** LULESH experiment | main (independent) | `experiments/lulesh-openmp/**` only — zero code overlap; position flexible, placed near the top. |
| 17 | **#85** Ninja experiment | main (independent) | `experiments/ninja-strict/**` only — zero code overlap; position flexible, placed near the top. |

Linear chain (predecessor → successor), leaves attachable anywhere:

```
main(5c5c73f)
  → #81 → #90 → #83 → #89 → #88
  → #25 → #42 → #27 → #35 → #79 → #80 → #84
  → #78 → #41
  → #87
  → #77 → #85            (independent experiment leaves)
```

---

## 6. Per-adjacency conflict-resolution notes

- **#81 vs main:** `ci.yml` and `validate.sh` were modified by landed CI/test
  PRs (#7, #76, #86, #93, #103, #110). Expect additive merges in the test-job
  matrix; keep the landed nextest/max-threads groupings from #93/#110.
- **#89 ↔ #88** (`hermit-cli/src/lib.rs`, `replayer/mod.rs`): apply #89's narrow
  "ignore unused arg registers in desync" first, then reconcile #88's broader
  replayer hardening around it.
- **detcore `lib.rs` hub (#25→#42→#27→#35→#79→#84, plus #78/#41):** conflicts
  are almost entirely **additive** (new syscall handlers / match arms /
  registrations). Resolve by keeping all arms; watch for duplicate handler
  registration now that fail-closed handlers already live in `lib.rs` on main.
- **#80 ↔ #84** (`detcore/src/fd.rs`): sequence #80 before #84; both extend fd
  bookkeeping.
- **#41/#78 vs landed fail-closed (§2):** before stacking, diff each against
  `main` to extract only the still-unique delta. If empty, close as superseded.
- **#84 vs landed `procfs.rs`:** treat as a reconcile, not an apply.
- **#87 (`ci.yml`, `validate.sh`, `hermit_modes.rs`, `misc/mod.rs`):** last in
  the code stack so it absorbs every prior edit to those files.

---

## 7. Open questions / recommended human decisions

1. **#41 vs #78 vs landed fail-closed:** Are #41/#78 still needed after #129
   landed? Recommend the author confirm the residual delta or close #41 (and
   possibly #78) as superseded. This is the single biggest ambiguity.
2. **#84 procfs:** Does the landed `procfs.rs` already cover #84's intent, or is
   #84 a distinct/fuller implementation to replace it?
3. **Leaf placement (#77, #85):** experiments-only; can land independently at
   any time — they do not need to be in the linear code stack at all.

---

## 8. Next steps (implementation — require approval; not done in this task)

1. Rebase #81 onto `main`; then layer the chain in §5 order, rebasing each
   branch onto the newly-built tip and resolving the additive conflicts above.
2. Resolve the §2/§7 supersession questions for #41/#78/#84 first — they gate a
   clean stack.
3. Record "applies after PR #X" in each PR description per the §5 column.
4. Rebuild the speculative frontier branch as the top of this chain and verify
   it builds (`cargo build --workspace`) + validates (`./validate.sh`).
