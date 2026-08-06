# A parity cell can read GREEN on a test that cannot fail — 62 of 82 do

**Date:** 2026-08-06 · **Task:** `parity-scorecard-cells-may-pass-on-tests-that-cannot-fail`

## The denominator

| verdict | count | share |
|---|---:|---:|
| **NOT-EXERCISED** | **62** | **76%** |
| EXERCISED | 19 | 23% |
| NO-PAYLOAD | 1 | 1% |
| **checked** | **82** | |

Population: every `tests/backend-parity/fixtures/*.c` at hermit `4c70658e7`. All 62 NOT-EXERCISED
fixtures ran **successfully** bare on the host (exit 0, verified separately) and produced
**byte-identical stdout to the ptrace golden**. So for those cells the parity metric cannot
distinguish a determinizing backend from one that does nothing, and `parity=1` asserts coverage
that does not exist — on every backend at once, which is what makes the row look confident.

## The discriminator, and why it is a bare exec

A cell is **EXERCISED** when the ptrace-golden stdout differs from the **bare-host** stdout. If the
bare host already produces the golden bytes, hermit changed nothing observable.

The control has to bypass hermit entirely. A prior design note proposed reusing `run_and_hash` with
`"native"`, asserting `backend_available("native")==true`. **That is refuted:** hermit has no
`native` backend — `--backend` accepts only `ptrace, dbi, liteinst, sabre, kvm, e9patch` — and
`backend_available` does not exist in `collect-envelope.rs`. Implementing that note as written would
have produced a control that never runs.

## The fix: NOT-EXERCISED is a third bucket, carried with its evidence

`compat-envelope/collect-envelope.rs` now runs the bare-host control alongside the golden and the
candidate, and the row carries the evidence rather than just the verdict:

```
… stdout_parity, parity_exercised, native_output_hash, output_hash, ref_output_hash …
```

* `parity_exercised = 0` reclassifies `outcome` to **`not-exercised`** — a distinct bucket from
  `pass` and `fail`, because such a cell is neither evidence of parity nor evidence of a defect.
* **Reclassified, never deleted:** the measured `parity` value is still recorded, and
  `native_output_hash` is written next to the other two hashes, so a reader can re-derive the
  verdict instead of trusting it. That is the denominator rule applied to the cell: the count now
  travels with the size of the thing it counted.
* A bare run that *fails* yields `exercised = UNKNOWN`, not `false` — the guest may legitimately
  require the container, and asserting vacuity from a failed control would be the same
  unfounded-inference error this audit exists to catch.

## Verified both ways

| cell | parity | exercised | outcome |
|---|---:|---:|---|
| `getpriority_identity` — hermit determinizes nice | 1 | **1** | **scores normally** |
| `/bin/echo hello` — hermit changes nothing observable | 1 | **0** | **reclassified NOT-EXERCISED** |

ptrace `f63cf7053ac18dde` == dbi `f63cf7053ac18dde` != native `69e25376e4ea25ae` for the first;
all three hashes equal (`5891b5b522d5df08`) for the second. The discriminator separates them rather
than flagging everything — a genuinely-exercising cell is untouched.

## Limitations, stated

* `cpuid_probe`'s NO-PAYLOAD is **my** flag artifact: `--no-virtualize-cpuid` defeats the identity
  that fixture asserts, so it exits nonzero and emits nothing. It is not evidence about that cell.
* NOT-EXERCISED is a statement about the **stdout-parity metric**, not a claim that a fixture is
  worthless — several branch internally and would exit nonzero on a wrong answer. The parity *cell*
  still cannot tell a determinizing backend from an inert one.
* Swept scope is the 82-guest backend-parity-c family, not all 618 scorecard rows. The mechanism is
  wired for every cell; only this family was measured.
