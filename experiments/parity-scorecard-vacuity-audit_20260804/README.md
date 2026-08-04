# Parity-scorecard vacuity audit — synthesis (P0)

**Task:** `parity-scorecard-cells-may-pass-on-tests-that-cannot-fail`. **Date:** 2026-08-04.
**Charter:** measurement/enumeration only — the ONE question per cell is **"does the test FAIL if the
backend does NOTHING?"** No fixes applied. B-levels NOT re-derived. Compat expansion PAUSED.

**Why this is trustworthy (the audit discriminates, it does not flag everything):** clean negative
controls exist and pass the covered test — `meminfo-*-deterministic` (MemTotal==976562KB << host RAM,
only real virtualisation passes), `#1397 arch-prctl` (flips det+parity 0→1 causally), `kcmp_refusal`
(host returns 0 so asserted EPERM is Hermit-gated). The audit clears 75/112 KVM cells and 9/29 no-op
fixtures as genuine. It is a discriminator, not a blanket.

## Two independent vacuity axes

### Axis 1 — cannot-fail test: the cell runs but passes with an inert backend
Per-backend, cell-by-cell (ESTABLISHED unless noted). NOTE the "do nothing" default differs per backend,
so the SAME shared fixture can be vacuous for one backend and genuine for another:
- **KVM:** inert = `negative_errno(ENOSYS)` fall-through. **37 vacuous of 112 parity wins (33%), 0 box-blocked.**
  Vacuous = assert-ENOSYS (29) + copy-file-range-refusal + 7 empty-stdout cells. GENUINE = 75 (any
  non-ENOSYS errno or real output).
- **Liteinst:** inert = host passthrough. **≥22 firm-covered of 108 wins; up to 86 suspect.** Exact
  vacuous count is **BOX-BLOCKED** on per-syscall host-native errno. Parity is stdout-SHA-256 only ⇒
  **no negative control by construction.**
- **Shared no-op / error-canonicalization family (cross-backend fixtures):** **16 vacuous of 29 (55%)**;
  9 genuine (fixtures that arrange the host to succeed so Hermit's error is a real divergence); 3
  box-blocked on CI kernel version; 1 partial. Worked example — `kcmp_refusal` (GENUINE) vs
  `process_vm_*_refusal` (VACUOUS) both assert EPERM, opposite verdicts: **binding is "does the fixture
  make the host diverge," not "does it name an errno."**
- **flock-lifecycle:** vacuous — single-descriptor, all 5 ops return 0 on host; encodes Hermit's
  no-op-for-contention flock as correct. (#1544 REFUTED as the source — flock added by `d57991e1` —
  vacuity confirmed in cleaner form.)

### Axis 2 — never-exercised gate: the cell is asserted green in the contract but no gate runs it
Target = `hermit/tests/backend-parity/matrix.tsv` (hand-authored golden CONTRACT). The
`compat-envelope/*.csv` are collector OUTPUT (regenerated per run) — flips there are vacuously true and
uninformative; matrix.tsv is the correct object.
- **18 of 21 (86%) matrix green-flip commits flipped a cell green with NO backend impl change**
  (test/matrix/run_matrix.py/fixture only). Only 3 impl-backed.
- **Structural gap:** only always-on gate is `--backend dbi --strict` (DBI/L1). No workflow runs
  `--backend kvm`; none passes `--verify` (L2). So **~36 cells** (`kvm=pass` ×19 + `dbi_l2` ×19 +
  `kvm_l2` ×17, added test-only in `82a8e853`) are asserted green but run by no recurring gate.
- **Concrete proof it bites:** `8a7803cf` flipped pthread_lifecycle DBI gap→pass with no impl; later
  REVERTED by `967abd99`. A no-impl flip that did not hold.
- Reassuring boundary: for cells the DBI/L1 gate DOES run, `run_case` does real
  exit-status/exact-stdout/run1==run2 comparison ⇒ a no-op backend fails there.

### #338 (structural metric blindness)
Exit-stats collector is per-instance (`reverie-kvm/src/vm.rs backend_stats` returns only the caller's
snapshot; no cross-child sum). A backend dropping all children scores a self-consistent green ⇒ the
metric cannot detect missing children. CONFIRMED. Caveat: #338 is OPEN DRAFT, not pinned, consumer is
task #1412 — real design flaw, not yet a load-bearing scored cell.

## Owner-facing bottom line

Cannot-fail vacuity is **at least ~1/3 of scored parity cells where cleanly measurable (KVM 37/112)**,
plausibly higher for stdout-only backends (liteinst, BOX-BLOCKED). The never-run structural gap adds
**~36 cells** asserted green with no gate, and **86% of contract green-flips carried no implementation.**
**Before 0.2, every compat percentage must be restated as genuinely-covered = "passes that FAIL if the
backend does nothing."** The templates for repair (per family) are in `nonvacuous-templates.md`; the
general rule: value in stdout (hashed channel) not stderr; host-implemented target so expected ≠
host-native; a negative control an inert backend would pass must be rejected.

## Slice artifacts
- `nonvacuous-templates.md` — liteinst denominator + per-family repair templates
- `kvm-slice.md` — KVM 112-cell table + #338 verdict
- `noop-family-slice.md` — 29-cell no-op/error-canon family + #1544 verdict
- `no-impl-change-slice.md` — matrix.tsv green-flip history + structural gap
- (liteinst detail: `../liteinst-ptrace-frontier-gap_20260804/README.md` "## Vacuity audit")
