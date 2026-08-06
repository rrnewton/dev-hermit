# RDRAND is hidden, not determinized — and when the guest doesn't print it, nothing notices

**Date:** 2026-08-06 · **Agent:** hermit-regress · Local only.
**Bound to:** hermit `4c70658e785834737cbe1524f77330c781a6f5ea`, reverie `dd3c178e`, ptrace backend,
host devbig014, kernel `6.18.39-0_fbk0_hardened_0`.

## Question

Hermit masks RDRAND out of CPUID. Masking protects code that *asks* CPUID and believes the answer.
What does a guest that issues the instruction anyway observe — and would anything catch it?

## Answer

**Only hidden.** The sole RDRAND handling anywhere in Detcore is the CPUID mask in
`detcore/src/cpuid.rs:39-40` ("some features like RDRAND masked off to prevent non-determinism").
There is no instruction-level determinization. A guest compiled `-mrdrnd` that skips the CPUID check
reads **host entropy under `--strict`**.

And the part that matters most: **when the value is consumed internally rather than printed, hermit
reports the run as deterministic.**

| leg | case | mode | result |
| --- | --- | --- | --- |
| 1 — non-vacuity | `rdrand_direct` | native ×3 | 3 distinct values — the probe can fail |
| 2 — contract | `rdrand_direct` | `--strict` ×3 | **3 distinct values — contract violated** |
| 2 — contract | `rdrand_direct` | `--strict --verify` | rc=1, stdout mismatch — **but DETLOG 318\|318 and INFO 153\|153 byte-identical** |
| 2 — contract | `rd_silent` (consumes, never prints) | `--strict --verify` | **rc=0 — reported deterministic** |
| 3 — control | `random_sources` unmodified | `--strict` ×3 | byte-identical — unaffected |

## Why the existing coverage cannot see this

`detcore/tests/misc/mod.rs::rdrand_rdseed_is_masked` asserts only that the *virtual CPUID* reports
the feature absent. It never executes RDRAND, so it passes whether or not the instruction is
determinized. No guest under `tests/` executes RDRAND at all — `grep` for it returns only two
comments, one of which (`tests/e2e/language-runtimes/cpp-stl-determinism.sh:56-58`) notes that
libstdc++ "would prefer the RDRAND CPU instruction … so RDRAND would be visible and executed
directly" and works around it with flags.

## The shape of the exposure

The entropy enters **entirely outside the traced surface**. The syscall and scheduling traces are
byte-identical between two runs that produced different random values. So:

* a guest that *prints* RDRAND output is caught, by the stdout comparator only;
* a guest that seeds a PRNG, picks a hash seed, or jitters a retry from it is **invisible** —
  DETLOG identical, INFO identical, `--verify` green.

The second is the realistic case and the reason masking-only is a thin defence. This is the same
class as the `__vdso_getrandom` finding: an entropy path that defeats interception by construction
rather than by a bug. It differs in one important way — the vDSO path turned out to be
syscall-seeded and therefore already determinized on ptrace/DBI, whereas this one is not
determinized anywhere.

## What this artifact deliberately does NOT do

**It does not land a contract fixture in `hermit`, and that is the correct outcome, not a shortfall.**
I wrote the guest and registered it in `tests/e2e/manifests/c-programs.toml` with every backend
disabled and a written reason. `ci/test_harness.sh validate` refused it:

```
manifest-plan: matrix symmetry manifest ptrace-front-door debt changed;
unexpected=["c-programs/rdrand-direct"] … New compatibility coverage must enter a shared schema-v2
TOML manifest, establish ptrace first, and declare every backend/mode cell
```

That guard is right: a new compatibility contract must establish ptrace first, and ptrace cannot
satisfy this one today. The neighbouring escape hatch, `ci/matrix-symmetry-baseline.json`, holds two
legacy KVM entries and is meant to *shrink* — adding new debt to it would route around the guard.
So the hermit tree was reverted to clean (`test_harness.sh validate` passes) and the guest lives
here instead, ready to land the moment the instruction is determinized.

**Whether masking-only is the intended product boundary or a gap is an owner call, not mine.** If it
is the boundary, it should be written down, because the current comment says "to prevent
non-determinism" and that is not what masking achieves for CPUID-ignoring code.

## Reproduction

```sh
cc -O1 -mrdrnd -o rdrand_direct rdrand_direct.c
cc -O1 -mrdrnd -o rd_silent     rd_silent.c
for i in 1 2 3; do hermit run --strict -- ./rdrand_direct; done   # 3 distinct values
hermit run --strict --verify -- ./rdrand_direct                   # rc=1, stdout mismatch
hermit run --strict --verify -- ./rd_silent                       # rc=0, "deterministic"
```

`--verify-strict` timed out on this host for both guests (the known verify-hang), so plain
`--verify` was used for the exit-code comparison; that limitation is recorded in `metadata.json`
rather than papered over.
