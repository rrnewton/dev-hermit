# Active worktree slots

This is the host-local source of truth for live work under
`/home/newton/work/dev-hermit/worktrees`. Add one row before the first edit in
a slot. Remove it only after the final state is recorded in `ARCHIVED.md` and
the slot is clean enough to park or reclaim.

**Policy:** at most twelve active slot rows may exist. New work must use a
canonical top-level path from `worktrees/slot01` through `worktrees/slot12`. No
more than five additional clean slots may be parked outside this registry. The
legacy named paths in the second table predate this policy; do not move or
remove them while their agents are active. Reclaim each one (archive + `git -C
hermit worktree remove`) as soon as its task goes idle. Do not assign new work
to a named path.

Branches shown are the *nested* Hermit/Reverie worktree branches, captured
2026-07-22 from `git -C worktrees/slotNN/<sub> rev-parse --abbrev-ref HEAD`.
The registry had drifted from disk; this table was reconciled to the real
worktree state during task `impl-worktree-cleanup`.

## Canonical slots (active)

| Slot | Owner / task | Hermit branch | Reverie branch | Started | Purpose |
| --- | --- | --- | --- | --- | --- |
| `worktrees/slot01` | `hermit-sabre` / `impl-sabre-runtime-stabilize` | `impl-test-port-batch-a` *(dirty)* | `detached:075d1ef` | 2026-07-21 | Stabilize the in-process SaBRe runtime. |
| `worktrees/slot02` | `hermit-sabre` / `impl-green-speculative` | `impl-green-speculative-slot02` *(dirty)* | `detached:9669339` | 2026-07-22 | Rebuild frontier from current main plus the review PR stack; make CI green. |
| `worktrees/slot03` | `hermit-ci` / `impl-fix-pr88-adversarial` | `impl-fix-pr88-adversarial-slot03` | `detached:9669339` | 2026-07-21 | Record/replay + adversarial PR88 fix work (re-tasked from record-replay-matrix). |
| `worktrees/slot04` | `hermit-ci` / `impl-integration-test-suite` | `codex/integration-test-suite` | `detached:81a092c` | 2026-07-21 | Build comprehensive hermit run compatibility matrix. |
| `worktrees/slot05` | `hermit-ci` / `impl-nondet-jvm-threading` | `impl-nondet-jvm-threading-slot05` *(dirty)* | `detached:075d1ef` | 2026-07-22 | Native JVM thread-order nondeterminism vs. strict Hermit reproducibility. |
| `worktrees/slot06` | `hermit-ci` / `impl-nondet-nodejs-async` | `impl-nondet-nodejs-async-slot06` *(dirty)* | `detached:075d1ef` | 2026-07-22 | Native Node.js worker completion-order nondeterminism vs. strict Hermit. |
| `worktrees/slot07` | `hermit-port` / `research-replay-robustness` | `research-replay-robustness-slot07` | `detached:9669339` | 2026-07-21 | Test record and replay robustness with real programs. |
| `worktrees/slot08` | `hermit-buck` / `impl-fail-closed-batch4` | `impl-fail-closed-batch4-slot08` *(dirty)* | `detached:9669339` | 2026-07-22 | Next fail-closed syscall batch (progressed from batch3). |
| `worktrees/slot09` | `hermit-port` / `impl-test-record-replay-stress` (Hermit); `hermit-clippy` / `impl-kvm-backend-expand` (Reverie KVM) | `impl-test-record-replay-stress-slot09` | `impl-kvm-backend-expand` | 2026-07-21 | Shared disjoint slot: record/replay stress testing; expand the Reverie KVM backend. |
| `worktrees/slot10` | `hermit-cargo` / `impl-arb-binary-wave2` | `codex/arb-binary-wave2-results` | `detached:9669339` | 2026-07-21 | Test complex binaries on speculative; owns `experiments/arbitrary-binaries-wave2_20260721/`. |
| `worktrees/slot11` | `hermit-docs` / `impl-docs-update-session` (parent docs incl. `ai_docs/*` research deliverables) | `feature/no-namespace-mode` | `detached:9669339` | 2026-07-21 | Update session docs; integrate completed parent research documents. |
| `worktrees/slot12` | `hermit-issues` / `impl-dbi-parity-79` | `impl-dbi-parity-79-hermit` | `impl-dbi-parity-79-reverie` | 2026-07-22 | Wire merged reverie-dbi into Hermit; DBI parity (progressed from dbi-basic-binaries). |

## Legacy named worktrees (reclaim when idle — do NOT assign new work)

These are pre-policy **Hermit-only** worktrees registered in the Hermit
submodule (`git -C hermit worktree list`), not canonical slot pairs. They must
be archived and removed once their agents finish. They were NOT removed during
`impl-worktree-cleanup` because agents cycle in and out of them (verified live
processes and recent mtimes); removing an in-use worktree would destroy
in-flight work.

| Path | Hermit branch | State (2026-07-22) | Note |
| --- | --- | --- | --- |
| `worktrees/dbi-groundtruth` | `detached:7030127` | clean | Detached HEAD; SHA also live at `/tmp/hermit-dbi-groundtruth`. |
| `worktrees/demo-content-main` | `main` (behind 32) | clean | Plain main checkout for demo content. |
| `worktrees/demo-content-origin-main` | `demo` (behind 17) | **DIRTY** (`M README.md`) | Active as of 11:09; do NOT touch (invariant 14). |
| `worktrees/determinism-argument-rubric` | `impl-determinism-argument-rubric` | clean (pushed) | Recoverable from origin. |
| `worktrees/fix-rr-mmap-stale-files` | `impl-fix-rr-mmap-stale-files` | clean (pushed) | Recoverable from origin. |
| `worktrees/land-batch2-104` | `land-batch2-104` | clean, **ACTIVE** (live procs) | Landing task in flight. |
| `worktrees/no-silent-skips` | `impl-no-silent-skips` | clean (pushed) | Recoverable from origin. |
| `worktrees/p0-ci-candidate` | `fix/selfhosted-arbitrary-timeouts` (ahead 18) | clean, **unpushed commits** | Branch has 18 local-only commits. |
| `worktrees/panic-unsupported-fix` | `impl-fix-panic-unsupported` | clean (pushed) | Recoverable from origin. |
| `worktrees/pr81-adversarial` | `port-rr-test-suite-slot06` | clean (pushed) | Recoverable from origin. |
| `worktrees/pr93-adversarial-fix` | `impl-cargo-nextest-slot09` | clean (local only) | Branch not pushed. |
| `worktrees/pr95-backend-fix` | `impl-backend-selector` | clean (local only) | Branch not pushed. |
| `worktrees/pr96-adversarial-fix` | `docs-architecture-expand` | clean (pushed) | Recoverable from origin. |
| `worktrees/rr-test-regression-tracking` | `impl-rr-test-regression-tracking` | clean (pushed) | Recoverable from origin. |
| `worktrees/progress-frontier-final` | `detached:e7fbcc9` | clean | Created 2026-07-22 mid-cleanup by another agent. |
| `worktrees/progress-main` | `detached:aabc0f6` | clean | Created 2026-07-22 mid-cleanup by another agent. |
| `worktrees/progress-main-final` | `detached:bf00a97` | clean | Created 2026-07-22 mid-cleanup by another agent. |

## Anomalies outside `worktrees/` (Hermit submodule worktrees — needs coordinator)

Recorded for visibility; reclaiming these is a coordinator action, not part of
`impl-worktree-cleanup` (they sit outside `worktrees/`).

- **Primary checkout on a feature branch:** `hermit/` is on `stress-test-framework`
  (`57b963d`), which is why the parent shows `M hermit`. ~64 live processes have
  cwd in the primary checkout — multiple agents doing feature work in the
  integration surface (invariant 1 violation). Not reset here; owned elsewhere.
- `hermit-wave7-safe/` (parent root) — Hermit worktree on `impl-fix-proc-minimal-v2-slot12`.
- `landing-nonreview/` (parent root) — Hermit worktree on `land-pr-111`.
- `hermit/worktrees/slot-notif-fd` — Hermit worktree nested *inside* the primary
  checkout, on `fix-notification-fd-wakeup`.
- Numerous `/tmp/hermit-*` and `~/work/hermit/*` Hermit worktrees (outside the
  `dev-hermit` tree; out of scope for this task).
