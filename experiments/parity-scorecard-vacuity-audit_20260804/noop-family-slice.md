# Parity-scorecard vacuity audit — no-op / error-canonicalization family slice

Date: 2026-08-04T13:49Z
Auditor: subagent (P0 audit slice: cross-backend "no-op semantics asserted as
correct" / error-canonicalization family + verify confirmed instance #1544).
Repo state audited: `hermit/` primary checkout on `main`; fixtures under
`hermit/tests/backend-parity/fixtures/`, manifests under
`hermit/tests/e2e/manifests/`, legacy ledger `hermit/tests/backend-parity/matrix.tsv`.
Host kernel `6.18.39` (local) — CI runner kernel version is the box-blocked variable below.

## The one question, per cell

Does the cell FAIL if the backend does NOTHING? Operationalized as the cheapest
possible inert backend: **forward every syscall to the host kernel unmodified.**
- If the host natively produces the *asserted* result, the inert backend reproduces
  it → the cell cannot distinguish a real determinization from a passthrough →
  **VACUOUSLY-GREEN**.
- If the host natively produces a *different* result (the fixture arranges the host
  to succeed, or to return a different value/errno) → a passthrough diverges from the
  golden → **GENUINELY-COVERED**.
- If whether the host produces the asserted result depends on host configuration we
  cannot observe from the sandbox (chiefly: kernel version, for a syscall newer than
  the CI runner's kernel, where the ENOSYS is host-native not Hermit-gated) →
  **BOX-BLOCKED**.

This slice is scoped to cells whose asserted result is "syscall returns error X" or
"operation succeeds / is a no-op with no observable side effect." Real-behavior
fixtures (readdir, statx, rename, symlink, mkdir, ...) are out of scope: they also
match a passthrough, but they assert genuine host semantics, not a no-op/refusal.

## CONFIRMED-INSTANCE verdict: #1544 flock

**Verdict: REFUTED as stated (PR attribution + literal encoding), but the underlying
VACUITY is CONFIRMED in a cleaner form.**

1. **#1544 does not touch flock at all.** PR #1544 "Promote high-value e9patch
   syscall contracts" (`codex/e9patch-ptw-agreed-subset`, OPEN/DRAFT) file list:
   `ci/expected-e2e-plan.json`, `tests/backend-parity/fixtures/pid_probe.c`,
   `tests/c/pidfd_open_self.c`, `tests/c/scheduler_policy_queries.c`,
   `tests/c/syscall_file_metadata.c`, `tests/e2e/manifests/{backend-parity-c,c-programs}.toml`,
   `tests/e2e/manifests/inventory/test-files.json`. No flock. (Confirms memory
   `e9patch-coalesce-share-empty-1544-mislabeled`: #1544 is manifest-family, "e9patch"
   in the title is PTW provenance.) The flock fixture was added by commit
   `d57991e1` ("backend-parity: retarget 19 legacy matrix PRs into schema-v2 manifest").

2. **No fixture anywhere encodes "a second exclusive flock succeeding while the first
   is held" as expected.** The flock fixture
   `hermit/tests/backend-parity/fixtures/flock_lifecycle.c` is **single-descriptor**:
   it opens ONE fd (`mkstemp`, line 29) and never opens a second. Its sequence
   (lines 35-49) is LOCK_EX → LOCK_SH → LOCK_UN → LOCK_EX|LOCK_NB → LOCK_UN, all on
   the same fd, so the non-blocking re-acquire at line 44 is of an **already-released**
   lock, not a contended one. Lines 8-14 EXPLICITLY disclaim the contention case:
   > "This fixture deliberately does NOT assert cross-descriptor contention. Detcore
   > ... does not model flock contention ... the host returns EWOULDBLOCK on a
   > conflicting non-blocking lock; Hermit does not ... a contention check is not a
   > portable cross-backend contract."

3. **But the flock cell IS VACUOUSLY-GREEN, and it encodes Hermit's non-Linux
   no-op-for-contention flock semantics as correct.** All five single-fd ops return 0
   on the host (`flock ok=5`). A backend that no-ops flock (returns 0) OR forwards to
   host produces `ok=5` identically → the cell cannot detect a backend that implements
   nothing for flock. The comment itself documents WHY it only asserts the
   non-distinguishing subset: because Hermit's flock does not model the contention that
   would distinguish a real implementation from a no-op. So the spirit of the flagged
   instance ("asserts non-Linux no-op semantics as correct") is CONFIRMED — the
   correction is only *which* call sequence and *which* PR.
   - Cell: `backend-parity-c/flock-lifecycle`
     (`hermit/tests/e2e/manifests/backend-parity-c.toml:782`), program
     `flock_lifecycle.c:35-49`. Currently enabled `backends_enabled = ["ptrace"]`
     only (DBI disabled: "L2 --verify witness was not recorded"; no KVM row).

## Sweep: the two sub-families

The backend-parity contract asserts a single golden stdout compared **identically
across ptrace / dbi / kvm** (and, in the fullcorpus scorecard, the featured
sabre / e9patch / liteinst). **Vacuity is a property of the fixture, so every vacuous
cell is vacuous in every backend column that runs it** — the whole family "spans
multiple backends" by construction. (Manifest-enabled backend subsets vary per cell,
e.g. flock=ptrace-only, thp_disable/pdeathsig=ptrace+dbi with KVM refusing ENOSYS,
but the *asserted golden* is backend-independent.)

### Family A — error-canonicalization / refusal (asserts syscall returns error X)

The anti-vacuity property is whether the fixture arranges the host to SUCCEED (so the
error is Hermit-gated) vs. lets the host return the same error natively.

| cell / fixture | asserted | host-native? | verdict |
|---|---|---|---|
| aio_refusal (io_setup/submit/getevents/destroy) | ENOSYS + ctx untouched | host SUCCEEDS | GENUINELY-COVERED |
| child_subreaper_refusal (PR_*_CHILD_SUBREAPER) | ENOSYS | host succeeds (≥3.4) | GENUINELY-COVERED |
| copy_file_range_refusal | ENOSYS + dst stays empty + src intact | host succeeds+copies (≥4.5) | GENUINELY-COVERED (double-bracketed) |
| kcmp_refusal (self-vs-self) | EPERM | host returns **0** for self | GENUINELY-COVERED |
| mce_kill_policy (PR_MCE_KILL[_GET]) | ENOSYS | host succeeds | GENUINELY-COVERED |
| no_new_privs_refusal (PR_*_NO_NEW_PRIVS) | ENOSYS | host succeeds (≥3.5) | GENUINELY-COVERED |
| seccomp_refusal (PR_GET_SECCOMP + seccomp) | ENOSYS + EOPNOTSUPP | host succeeds | GENUINELY-COVERED |
| sysv_ipc_refusal (semget/shmget/msgget) | ENOSYS | host succeeds | GENUINELY-COVERED |
| io_uring_fallback (matrix.tsv) | refuse/fallback | host has io_uring (≥5.1, universal on CI) | GENUINELY-COVERED (low box risk) |
| **process_vm_readv_refusal** (matrix.tsv) | EPERM | **host-native EPERM** — kcmp_refusal comment: "faithful Linux behavior for a caller without ptrace access to the target" | **VACUOUSLY-GREEN** |
| **process_vm_writev_refusal** (matrix.tsv) | EPERM | **host-native EPERM** (same) | **VACUOUSLY-GREEN** |
| cachestat_refusal | ENOSYS | host succeeds ONLY on kernel ≥6.5 (2023); on older kernel ENOSYS is host-native | **BOX-BLOCKED** (CI kernel unknown) |
| openat2_refusal | ENOSYS | host succeeds on kernel ≥5.6 (2020) | **BOX-BLOCKED** (lower risk) |
| listmount_unavailable (matrix.tsv) | unavailable/ENOSYS | listmount is kernel ≥6.8 (2024) — ENOSYS host-native on most CI today | **BOX-BLOCKED** (high risk) |

Family A: **Y=14 → 9 GENUINELY-COVERED, 2 VACUOUSLY-GREEN, 3 BOX-BLOCKED.**

Worked contrast (the whole audit in one line): **kcmp_refusal and
process_vm_*_refusal both assert EPERM, yet kcmp is COVERED and process_vm_* is
VACUOUS** — because kcmp probes self (host returns 0) while process_vm_* is written as
"faithful Linux behavior" (host returns EPERM). Same errno, opposite verdict; the
binding is "does the fixture make the host diverge," not "does it name an errno."

### Family B — no-op / success asserted as correct (asserts op accepted, no observable divergence from raw host)

Every one of these asserts a result the raw host also produces, so a passthrough
reproduces the golden.

| cell / fixture | asserted | verdict |
|---|---|---|
| flock_lifecycle | single-fd LOCK_* all return 0 (ok=5) | **VACUOUSLY-GREEN** (the confirmed instance) |
| fadvise_hints | 5× POSIX_FADV_* return 0 (pure hint) | VACUOUSLY-GREEN |
| thp_disable | PR_*_THP_DISABLE register round-trip | VACUOUSLY-GREEN (ptrace+dbi; KVM refuses ENOSYS) |
| prctl_pdeathsig | PR_*_PDEATHSIG register round-trip | VACUOUSLY-GREEN (ptrace+dbi; KVM refuses) |
| mempolicy_default | get/set MPOL_DEFAULT round-trip | VACUOUSLY-GREEN (every process starts MPOL_DEFAULT) |
| sync_file_range | barrier returns 0 + EBADF on -1 | VACUOUSLY-GREEN (both host-native) |
| robust_list | set/get_robust_list pointer round-trip | VACUOUSLY-GREEN |
| umask_mode | umask round-trip + mode&~umask bits | VACUOUSLY-GREEN (host-native) |
| sigprocmask_state | rt_sigprocmask block/query/unblock round-trip | VACUOUSLY-GREEN |
| sigaltstack_state | sigaltstack register round-trip | VACUOUSLY-GREEN |
| sigaction_state | rt_sigaction disposition round-trip | VACUOUSLY-GREEN |
| socket_options | setsockopt/getsockopt boolean round-trip | VACUOUSLY-GREEN (host-native) |
| set_tid_address | return == gettid, stable across calls | VACUOUSLY-GREEN (checks internal *consistency*, not that the TID is virtualized; passthrough host-tid is self-consistent too) |
| ioctl_fionread | FIONREAD == bytes written + FIONBIO round-trip | VACUOUSLY-GREEN |
| msync_writeback | msync 0 + pread sees bytes + EINVAL misaligned | **PARTIAL** — brackets a *fake-mmap* (in-memory) backend via pread-sees-bytes (KVM's model), but under raw passthrough MAP_SHARED is coherent WITHOUT msync, so it is vacuous re msync-being-a-no-op. Non-vacuous for the mmap-model dimension only. |

Family B: **Y=15 → 14 VACUOUSLY-GREEN, 1 PARTIAL (msync_writeback).**

Note on Family B honesty: these fixtures are internally rigorous (round-trips, not
bare "returns 0"; socket_options explicitly rejects "accepts setsockopt but drops the
value"). Their vacuity is not sloppiness — it is that the *asserted property* (a
register/attribute round-trips, a hint is accepted) is one the host performs, so no
backend action is required to pass. They verify *self-consistency*, not *deterministic
divergence from the host*.

## Denominator

| bucket | Y (audited) | GENUINELY-COVERED | VACUOUSLY-GREEN | BOX-BLOCKED | PARTIAL |
|---|---|---|---|---|---|
| A: error-canonicalization/refusal | 14 | 9 | 2 | 3 | 0 |
| B: no-op / success-asserted-correct | 15 | 0 | 14 | 0 | 1 |
| **combined family** | **29** | **9** | **16** | **3** | **1** |

- **16 of 29 (55%) audited no-op/error cells are VACUOUSLY-GREEN** — an inert
  passthrough backend passes them.
- **3 of 29 BOX-BLOCKED** on CI kernel version (cachestat ≥6.5, openat2 ≥5.6,
  listmount ≥6.8): each becomes VACUOUS if the CI runner kernel predates the syscall
  (ENOSYS host-native rather than Hermit-gated) — verify against the actual CI runner
  kernel, do not assume.
- **9 of 29 GENUINELY-COVERED** — all in Family A, all refusal cells that deliberately
  arrange the host to SUCCEED so the Hermit error is a real divergence.
- **1 PARTIAL** (msync_writeback): non-vacuous only for a fake-mmap backend.
- **Backend span:** every cell in this family is cross-backend by construction (one
  golden stdout diffed across ptrace/dbi/kvm + featured sabre/e9patch/liteinst).
  All 16 vacuous cells are vacuous in **every backend column** that runs them — the
  vacuity spans the full backend set, it is never backend-specific.

Denominator context: ~29 family cells sit within the ~57 `backend-parity-c.toml`
fixtures / ~200-cell full corpus (compat-envelope), i.e. the no-op/error family is
~15% of the corpus and its vacuous subset ~8% of the corpus.

## Confidence labels

- **ESTABLISHED** (file-cited, read directly): #1544 file list (gh pr view 1544);
  flock single-fd + no-contention disclaimer (`flock_lifecycle.c:8-14,29,35-49`);
  flock manifest cell ptrace-only (`backend-parity-c.toml:782-795`); all Family A
  refusal fixtures' "host succeeds" documentation (read in full); Family B
  round-trip/hint assertions (read in full or by identical register-round-trip shape
  for sigaltstack/sigaction); kcmp-vs-process_vm EPERM contrast
  (kcmp_refusal.c comment).
- **HYPOTHESIS** (inference from documented behavior, not run on a backend): the
  passthrough-inert-backend model and its verdicts assume Hermit's non-refusal
  disposition for these syscalls is host-faithful; not re-executed under each backend
  in this slice (no boxed run performed — MEASUREMENT/ENUMERATION only per charter).
- **BOX-BLOCKED**: whether cachestat/openat2/listmount ENOSYS is host-native depends
  on the CI runner kernel, unobservable from this sandbox. process_vm_* EPERM taken as
  host-native from the kcmp_refusal source comment ("faithful Linux behavior"); the
  process_vm_* fixture source itself is a legacy matrix.tsv row with no file in
  `fixtures/` — confirm target-is-another-process before treating as ESTABLISHED.
