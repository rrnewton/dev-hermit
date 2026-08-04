# Non-vacuous templates: what each vacuous cell family SHOULD assert

**Task:** `parity-scorecard-cells-may-pass-on-tests-that-cannot-fail` (P0). **Date:** 2026-08-04.
**Scope of this file:** the liteinst parity slice (108 of 200 cells @ hermit `82a8e853`). Cross-backend
totals (KVM, no-op family, no-impl-change) assembled separately under this dir.
**Directive:** count + templates only — DO NOT fix the cells yet.

## The discriminator that makes this audit trustworthy

The single question per cell: **does the test FAIL if the backend does nothing?** A trustworthy audit
must also exhibit a **clean negative case** — a cell that CANNOT pass unless the backend actually works —
or it reads as a detector that flags everything.

- **CLEAN NEGATIVE (proves the detector discriminates):** `meminfo-*-deterministic` asserts
  `MemTotal == 976562 KB`, far below host RAM. An inert backend forwards the host's real MemTotal →
  hash diverges → cell FAILS. Only a backend that actually virtualises /proc/meminfo passes. GENUINE.
- **CLEAN POSITIVE (a real ratchet, not vacuous):** `#1397 arch-prctl` — pre-#1397 liteinst reports
  `gs_base=0`, failing the changed-GS assertion → empty stdout → parity diverges; #1397 flips det AND
  parity 0→1 together. Causal binding. GENUINE.

Both templates share one property: **the compared channel (stdout, in the parity hash) carries a value
that is (i) host-specific / nondeterministic if the backend does nothing, and (ii) canonicalised to a
fixed value only a working determinizer produces.** Every non-vacuous rewrite below restores that property.

## The liteinst denominator (materiality)

| bucket | count | of 108 wins | of 200 corpus |
|---|---|---|---|
| GENUINELY bracketed (fails if backend inert) | ~22 (firm floor ~18) | 20% | 11% |
| VACUOUS-suspect (passes with inert backend) | 86 | 80% | 43% |
| — error-canonicalization family (subset) | 43 | 40% | 22% |
| — pure constant-string / signal (subset) | ~4 | 4% | 2% |
| — weak data-checksum value-emitters (subset) | ~4 | 4% | 2% |

Materiality verdict: **the scorecard is materially wrong for liteinst parity, not "four bad cells."**
~80% of claimed parity wins cannot distinguish an inert backend.

## Templates — what a NON-vacuous version of each family would assert

### Family A — error-canonicalization (43 cells: `*-enosys`, `*-eopnotsupp`, `*-eperm`, `*-refusal`)
- **Vacuous now:** program calls a syscall, gets an errno, prints a constant `"…-ok"`; where the host
  returns that same errno natively, an inert backend produces identical stdout → green. (This IS the
  #1544 pattern: host errno asserted as the expected result.)
- **Non-vacuous template (meminfo-style — emit a value only the determinizer produces):** emit the
  *actual errno integer* to stdout AND target a syscall the **host IMPLEMENTS** (would succeed or return
  a host-nondeterministic value) so that the *expected* result is Hermit's canonical refusal/value, which
  an inert host-forwarding backend CANNOT produce (host success ≠ expected refusal → diverge).
- **Negative bracket (arch-prctl-style):** add a control invocation that WOULD pass on an inert backend
  (host-native errno) and assert it is *rejected* by the harness, so the cell has both a positive
  (mechanism fires) and negative (mechanism refuses the planted host-native case) side. Today parity has
  no negative side by construction.
- **Caveat:** which of the 43 are host-native-errno (vacuous) vs Hermit-gated (bracketed) is BOX-BLOCKED
  — needs the per-syscall native host errno from a boxed run.

### Family B — pure constant-string / signal cells (~4: `hello-alarm`, `hello-signals`, `sigpipe-siginfo`, `dbi-self-sigqueue`)
- **Vacuous now:** prints a fixed string regardless of signal delivery; the determinized observable is
  checked only to stderr (not in the parity hash), or not checked at all.
- **Non-vacuous template:** emit the *determinized signal observable* to stdout — the canonical
  `siginfo` fields (`si_pid`, `si_uid`, `si_code`), or the deterministic delivery order/count — so an
  inert backend (real host pid/timing) diverges from ptrace's canonicalised values. (arch-prctl template:
  round-trip a value only the determinizer sets.)

### Family C — weak data-checksum value-emitters (~4: `io-uring-ring`, `mmap-stress`, `rcx`)
- **Vacuous-ish now:** emits a deterministic checksum of *self-generated* data, not of the canonicalised
  nondeterministic source the cell claims to cover — so it passes even if that source isn't determinized.
- **Non-vacuous template:** emit the *actual nondeterministic quantity claimed*: the io_uring completion
  ordering, the mmap ADDRESS, the `rcx` register value — so stdout must match ptrace's canonicalised value
  (meminfo-style: a value only virtualisation/determinisation pins).

## General rule (for the owner)

A cell is non-vacuous iff, with the backend replaced by a no-op/host-forward, its **compared output
changes**. Enforce structurally: (1) put the determinized value in stdout (the hashed channel), not
stderr; (2) prefer a host-IMPLEMENTED target so "expected" ≠ "host-native"; (3) add a negative control
that an inert backend would pass and require its rejection. meminfo and arch-prctl already satisfy (1)+(2).
