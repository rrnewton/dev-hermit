# e9patch corpus power-to-weight — re-validation of the agreed subset

**Task:** `e9patch-corpus-power-to-weight-selection` (P1)
**Date:** 2026-08-05
**Bound to:** hermit main **`b64d893ae9ea6404472eae9cb86102d91ec642ef`**
**Mode:** local read of existing manifests + sources. **No validate launched** (livelock risk), no egress, nothing mutated. PR #1516 untouched.

**Status: NOT PROMOTABLE AS AGREED. The subset needs a fresh adversarial review — it shrank.**

---

## What already existed (established, not re-derived)

Both halves of this task's deliverable were already produced on 2026-08-03:

| Artifact | Content |
|---|---|
| `ai_docs/transient/e9patch-corpus-power-to-weight-selection_20260803.md` | codex proposal: 23/370 inputs → 5 existing shared files, reject 347, 17 contracts / 24 entry points, +0.71 s |
| `ai_docs/transient/e9patch-corpus-power-to-weight-review_20260803.md` | **cross-model adversarial review** (claude vs codex proposal) @ hermit `f356148f` |

The review already did the case-by-case gate the task requires: **DISAGREE-drop on KEEP-1**
(`clock_getres` gap claim false — `tests/rust/clock_gettime.rs:42` already calls it in a manifested
program), **AGREE-conditional on KEEPs 2–5**, **AGREE on the 347 rejects**. Agreed subset:
**16 contracts / 23 entry points, ~0.70 s ptrace-first.**

I did not re-derive the ranking. I re-verified it — and that is where the problem is.

## The finding: the agreed subset is stale

The review ran at hermit `f356148f`. At `b64d893a`, **five of the syscalls the review confirmed as
genuine gaps are now covered by manifested shared fixtures.** Every one is wired into
`tests/e2e/manifests/backend-parity-c.toml`, so this is real shared-suite coverage, not dead code:

| Syscall | Review verdict @ `f356148f` | Now @ `b64d893a` | Covering fixture (all manifested) |
|---|---|---|---|
| `statx` | gap real (0 hits) | **COVERED** | `tests/backend-parity/fixtures/statx_metadata.c` |
| `statfs` | gap real | **COVERED** | `tests/backend-parity/fixtures/statfs_free_determinism.c` |
| `fstatfs` | gap real | **COVERED** | same fixture (`:49`, fd-based path) |
| `faccessat2` | gap real | **COVERED** | `tests/backend-parity/fixtures/faccessat2_flags.c` |
| `utimensat` | gap real | **COVERED** | `tests/backend-parity/fixtures/utimensat_determinism.c` |
| `flock(2)` | gap real (reviewer explicitly ruled out the `struct flock` false positive) | **COVERED** | `tests/backend-parity/fixtures/flock_lifecycle.c:35` (`flock(fd, LOCK_EX)`) |
| `getpriority` | gap real | **COVERED** | `tests/c/getpriority_identity.c` (raw syscall, not the glibc wrapper) |

I checked these the way the reviewer checked `flock` — by reading the call sites, not counting grep
hits — precisely because that reviewer caught a false positive there. These are real calls in real
manifested fixtures.

**Consequence: KEEP-4 is now almost entirely redundant.** Of its named surface — `statx`,
`newfstatat`, `statfs`, `fstatfs`, `faccessat2`, `flock`, `utimensat` — only **`newfstatat`**
(0 files) is still uncovered. Promoting KEEP-4 as agreed would spend 0.20 validate-seconds to
re-cover six syscalls the suite already asserts.

**And KEEP-2 shrinks by one:** `getpriority` is covered, leaving 5 contracts / 7 entry points.

Still genuinely uncovered, re-verified at `b64d893a` (0 files each):
`sched_getparam`, `sched_getscheduler`, `sched_getattr`, `sched_rr_get_interval`, `sched_getaffinity`
(KEEP-2) · `geteuid`, `getegid`, `getgroups`, `getpgid`, `getpgrp`, `getsid` (KEEP-3) ·
`pidfd_getfd`, `pidfd_send_signal` (KEEP-5) · `newfstatat` (KEEP-4 residue).

## Revised ranking — power-to-weight

Costs are the proposal's five-run medians plus its implementation buffer. **Provenance: these are
inherited estimates, not measured by me** — no validate was launched. They are the only cost data
that exists; treat them as the proposal's numbers carried forward.

| Rank | Target | Contracts (entry pts) | Added ptrace s | Contracts/s | Disposition |
|---:|---|---:|---:|---:|---|
| 1 | `backend-parity-c/pid-probe` (KEEP-3) | 4 (6) | 0.16 | **25.0** | **promote** — gap fully intact |
| 2 | `c-programs/scheduler-policy-queries` (KEEP-2, reduced) | 5 (7) | 0.17 | **29.4** | **promote reduced** — drop `getpriority` |
| 3 | `c-programs/pidfd-open-self` (KEEP-5) | 2 (2) | 0.17 | **11.8** | **promote** — gap intact |
| — | `c-programs/syscall-file-metadata` (KEEP-4) | 1 (1) residual | 0.20 | **5.0** | **DROP** — 6 of 7 now covered |
| — | `system-utils/clock-determinism` (KEEP-1) | 0 | ≤0.01 | — | **DROP** — already dropped by review |

**Revised proposal: 11 contracts / 15 entry points at ~0.50 ptrace-first validate seconds**
(22.0 contracts/s), down from the review-agreed 16 / 23 at ~0.70 s.

`newfstatat` alone does not justify a 0.20 s row. If wanted, fold it into KEEP-3's already-paid
`pid_probe` cell as a single assertion rather than reopening the metadata row — that is a
zero-marginal-cell add, and is the "collapse families, don't add files" instruction applied.

## The blocking condition also changed

The review's **Check 4** — "ptrace-first ⇒ non-ptrace costs zero" is false, because these programs
already have non-ptrace cells enabled — still stands, but its shape moved:

| Program | `backends_enabled` @ review (`f356148f`) | @ `b64d893a` | Change |
|---|---|---|---|
| `scheduler_policy_queries` | `["ptrace","sabre"]` | `["ptrace","sabre"]` | same |
| `pid_probe` | `["ptrace","sabre","liteinst"]` | `["ptrace","sabre","liteinst"]` | same |
| `syscall_file_metadata` | `["ptrace","sabre"]` | `["ptrace","sabre"]` | same (moot — dropped) |
| `clock_determinism` | `["ptrace","liteinst"]`, `ci = true` | **`[]`, `ci = false`** | **changed** (moot — dropped) |
| `pidfd_open_self` | `["ptrace","sabre"]` | **`["ptrace","dbi","kvm"]`** | **changed — and worse** |

`pidfd_open_self` now fans out to **DBI and KVM** instead of SaBRe. That raises the risk on KEEP-5
specifically: per `hermit/AGENTS.md`, KVM's output-only fallback reports `bitwise_parity: false` and
**cannot establish L2**. So extending `pidfd_open_self.c` with `pidfd_getfd` / `pidfd_send_signal`
lands new syscalls directly into a KVM cell that cannot verify them at L2, and a DBI cell whose
parity for those calls is unmeasured. KEEP-5 is the lowest power-to-weight of the three promotables
(11.8 contracts/s) **and** now carries the highest backend risk — a reviewer may reasonably drop it.

## Explicit rejects — unchanged

The 347 rejects stand; the reviewer spot-checked the taxonomy and agreed, and nothing in this
re-verification disturbs it. Constant/option grids (162), already-represented (102), and
unique-but-weak (83). The reviewer's one follow-up note also stands: `getcwd` is genuinely uncovered
and would merit a *virtualized-cwd determinism* oracle as a separately-costed future test — not as
part of this promotion.

## What I am asking for — this is NOT promotable as-is

**Nothing is promoted, and nothing here is self-approved.** The prior adversarial agreement covered
a subset that no longer exists: two of its four AGREE verdicts (KEEP-2, KEEP-4) rest on gap claims
that have since closed, and KEEP-5's backend exposure changed underneath it.

Required before any promotion:

1. **A fresh cross-model adversarial review of the revised subset** — KEEP-3 full, KEEP-2 reduced,
   KEEP-5 conditional-on-KVM. The 2026-08-03 agreement must not be carried over; it was given on
   different facts.
2. **Resolve the KVM/DBI exposure on KEEP-5** before it lands: either measure parity on those cells
   or declare an explicit per-cell gap reason. Do not assume zero.
3. **Re-measure the four cost medians** at current main if they are to be load-bearing. Mine are
   inherited from the proposal, and this re-verification just demonstrated how fast the underlying
   corpus moves.
4. **Preserve every existing assertion** in the extended files; additive only.

Promotion path is unchanged and proper: shared schema-v2 TOML manifest, ptrace L2 first with JSONL
duration evidence, then each backend cell ratcheted with its own measurement. Never a
backend-private side-matrix. **Do not modify PR #1516** — the owner is winnowing it personally.

## Provenance

| Number | Source | Status |
|---|---|---|
| 370 corpus inputs, 23 selected, 347 rejected | proposal @ parent `272367e7` | inherited |
| Per-row medians (0.143 / 0.151 / 0.182 / 0.155 s) + buffers | proposal, five-run medians | **inherited, not re-measured** |
| Gap claims (0-hit / covered) | `grep` over `tests/**/*.{c,rs}` @ hermit `b64d893a`, call sites read individually | **verified this session** |
| Manifest wiring of new fixtures | `tests/e2e/manifests/backend-parity-c.toml` @ `b64d893a` | **verified this session** |
| `backends_enabled` per program | `tests/e2e/manifests/*.toml` @ `b64d893a` | **verified this session** |
| Prior adversarial verdicts | review artifact @ hermit `f356148f` | inherited; **two now invalidated by drift** |
