# The register hash can fail — and mutation-testing it found a defect in its own cost tier

**Date:** 2026-08-06 · **Task:** `verify-register-hash-catches-a-planted-register-divergence`
**Branch:** `feat/detlog-register-hash-at-control-points` @ `9f351a2011b40f2a50b0fdd08954f457d656465c`

## Result: all four legs pass, at both cost tiers

| tier | 1 · plant FAILs | 2 · clean PASSes | 3 · non-vacuous | 4 · handler NOT reported |
|---|---|---|---|---|
| **full** (cadence 1) | ✅ FAIL | ✅ PASS | ✅ 1 and 1 lines | ✅ PASS, 1 sample / 1 control point |
| **spot-1/10** (cadence 10) | ✅ FAIL | ✅ PASS | ✅ 1 and 1 lines | ✅ PASS |

## First: the comparator really consumes the register lines

Before planting anything, confirm the hash is *in* the compared set rather than merely printed —
otherwise every later leg would be theatre. `--verify --verify-strict` on `/bin/true` compares
**141** INFO messages without `--detlog-regs` and **190** with it; the flag emits exactly **49**
register lines. Delta 49 = 49. Every register line enters the comparison.

## Leg 1 — a planted divergence AT a control point is caught, and nothing else catches it

The plant uses **RDRAND**, which Detcore masks in CPUID but never determinizes, parked in a
callee-saved register held across a syscall and **never printed**. So the guest's stdout is
byte-identical run to run while its register state is not.

Result: `--verify` **FAILS**, and the attribution is exact —

* the mismatch is at log message 13, and the differing line **is** the `[registers]` line;
* `Mismatch in stdout` count is **0**.

So stdout, INFO syscall arguments, stack and heap all reported parity on a run that genuinely
diverged. The register hash was the only thing that saw it. That is the whole case for the feature,
demonstrated rather than asserted.

## Leg 4 — the boundary: handler interiors are NOT reported

A guest driving **200 CPUID + 200 RDTSC** traps through the Detcore handler verifies **PASS**, and
emits exactly **one** sample for its **one** control point — zero extra. Sampling does not leak
into handler interiors, so a backend running its handler in-guest cannot be charged with a
divergence for code the ptrace reference never executes.

## The defect this test found in the tier it was testing

**At `--detlog-regs-cadence=10`, a one-control-point guest emitted ZERO register samples and the
planted divergence PASSED.** A spot-tier green backed by no samples at all — precisely the vacuity
the register hash exists to expose, reproduced inside the register hash.

Cause: the cadence was keyed on a syscall counter that is not a zero-based count of control points
(the logged ordinal starts at 2, and `syscall_count` also advances at points this sampler never
reaches). Measured before the fix — a one-syscall guest sampled at cadence 1 and 2, and **not** at
5, 10 or 100:

| cadence | 1 | 2 | 5 | 10 | 100 |
|---|---:|---:|---:|---:|---:|
| before — 1-syscall guest | 1 | 1 | **0** | **0** | **0** |
| after — 1-syscall guest | 1 | 1 | 1 | 1 | 1 |
| after — `/bin/true` (49 points) | 49 | 25 | 10 | 5 | 1 |

Fixed by counting the samples themselves (`ThreadStats::regs_sample_index`), per thread, so index 0
is the first control point of every thread. Leg 3 is what caught it: without a non-vacuity leg the
spot tier would have shipped reporting confident greens on zero evidence.

## What register coverage ACTUALLY exists — and what does not

"Register hashing was added" must not be read as "register state is certified".

**Exists:** a SHA-256 over the guest-observable GP registers, `rip`, `rsp`, `rflags`, `orig_rax`
and the TLS bases, sampled at **syscall-exit control points**, compared by the product's own
`--verify --verify-strict` path, proven to catch a real divergence and proven not to fire inside
handlers. Off by default.

**Does NOT exist — stated so nobody infers it:**
* **Cross-backend register parity is unmeasured.** Everything here is **ptrace only**. The boundary
  claim is verified against handler traffic *within* ptrace, not against an in-guest-handler
  backend (sabre / liteinst / e9patch) — which is the case the boundary was designed for.
* **Only syscall-exit is sampled.** Signal delivery to the guest and thread start are plausible
  guest-logical-control points and are not covered.
* **`rcx`, `r11` and the segment selectors are excluded by design**, so a divergence confined to
  them is invisible to this hash.
* No scorecard cell consumes the register hash yet; this is an instrument, not a gate.

## Reproduction

```sh
HERMIT=<hermit with --detlog-regs> ./regmut.sh              # full tier
HERMIT=<...> CADENCE=10 ./regmut.sh                          # spot tier
```

Every leg prints its own evidence (verdict, compared-message count, register-line count) so the
verdict can be re-derived rather than trusted, and leg 3 fails the run if either side emitted no
register output.
