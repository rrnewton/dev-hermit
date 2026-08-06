# Register-file hashing: the domain is defined by a numeric window, and that window is a proxy

**Task:** `register_file_hashing_verify` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress, no validate run.

## What I found already built

The feature is **implemented and independently reviewed** — PR #1618, branch
`codex/register-file-hashing-verify`, head `f16d173b49654d3beaf0263eca43c04aab62e824` (present in the
local object store; **not** on `origin/main`). It adds `detcore/src/regdigest.rs` (294 lines), the
`[regs]` DETLOG emitter in `detcore/src/lib.rs:770-806`, a both-directions integration test
(`hermit-cli/tests/register_file_hashing.rs`), the `tests/c/register_marker.c` fixture, and a
`ci/dag/portable.json` wiring so the test actually executes in CI. A coordinator review on 2026-08-05
confirmed both directions non-vacuous from source at that exact head.

So I did not rebuild it. I audited the one thing a review of the *diff* does not naturally ask:
**what does the hash domain actually cover, and where does it silently not?**

## Method

`regdigest::canonicalize_and_hash_pairs` is a pure function, and the emitted digest is
`sha256(summary)` — so summary equality is exactly digest equality. I extracted it verbatim into a
standalone Rust probe (`probe.rs`, `rustc --edition=2021 -O`) and ran 11 planted cases. This tests the
real decision logic without a hermit build, and it is where the finding lives.

**What I did not do:** I did not run the end-to-end `register_file_hashing` integration test — that
needs a hermit build and the task rules out a concurrent validate. My verification is at the
pure-function level.

## The four advertised properties reproduce exactly

| case | result |
| --- | --- |
| address-only difference (shifted/patching backend) → **equal** | as designed |
| non-address value difference (42 vs 43) → **unequal** | as designed |
| aliasing change (two regs share an address vs not) → **unequal** | as designed |
| pure swap of two independent addresses → **equal** | as designed (the module's own third test says so) |

The canonicalization is genuinely a *hard* catch for values outside the address window, not a softer
strip. The design — ordinals by first appearance, preserving order and aliasing — is right, and the
docs define the domain positively rather than as an exclusion list, which is what the task asked for.

## The finding: `is_address()` is a value-range proxy for "this is a pointer"

```rust
const USER_VA_MIN: u64 = 0x1000;
const USER_VA_MAX: u64 = 0x0000_7fff_ffff_ffff;
fn is_address(value: u64) -> bool { (USER_VA_MIN..=USER_VA_MAX).contains(&value) }
```

Anything numerically inside that window is canonicalized to an ordinal — **including values that are
not pointers at all**. A divergence in such a value compares **equal**:

| planted divergence | canonical A | canonical B | caught? |
| --- | --- | --- | --- |
| `read()` byte count in `%rdx`: 4096 vs 8192 | `rdx=a2` | `rdx=a2` | **NO** |
| `lseek` offset in `%rsi`: 65536 vs 131072 | `rsi=a1` | `rsi=a1` | **NO** |
| computed value in `%r12`: 1700000000 vs +1 | `r12=a1` | `r12=a1` | **NO** |
| *control* — just below the window: 4095 vs 4094 | `rdx=v4095` | `rdx=v4094` | yes |
| *control* — above `USER_VA_MAX`: −1 vs −2 | `rdx=v18446744073709551615` | `…614` | yes |

Both controls confirm the boundary behaves exactly as coded, so this is the window's definition doing
its job — not a bug in the implementation.

The module doc says *"Non-address values are emitted verbatim (`v<N>`), never stripped."* That is true
of the code, but `is_address()` decides what counts as a non-address value **by numeric range, not by
provenance**. So the accurate coverage claim is narrower than the stated one:

> ~~non-address values are never stripped~~ → **values outside `[0x1000, 0x7fff_ffff_ffff]` are never stripped.**

### The sharpest form: the shipped test's own fixture sits one bit away from the hole

`tests/c/register_marker.c` pins the marker in `%r15` and, per the review note, deliberately keeps it
**below `0x1000`** so it is classified as a plain value. That is what makes the negative direction
work. Move the same marker, in the same register, in the same test shape, above the window floor:

```
AS SHIPPED  r15 marker 101 vs 202            -> r15=v101 / r15=v202   -> UNEQUAL, caught
PLANT       r15 marker 0x1000+101 vs +202    -> r15=a1   / r15=a1     -> EQUAL,  NOT caught
```

**The feature's headline guarantee — "a register divergence between run1/run2 is caught" — holds for
the marker value the fixture happens to use, and fails at +0x1000 on the same register.** The test is
not vacuous (it does catch a real register-only divergence that stdout, exit code and sample count all
miss). Its fixture just sits on the safe side of the exact boundary that defines the hole, so it
cannot surface it.

### How much of this is actually uncovered

Being fair about impact, because the raw hole overstates it:

* For the **syscall-argument registers** (`%rdi %rsi %rdx %r10 %r8 %r9`), a count/offset divergence is
  plausibly double-covered: the module's own docs state that *"the `[syscall]` DETLOG line records
  syscall inputs/outputs"*. I am citing the author's claim — **I did not independently verify the exact
  field list of that line.**
* For the **callee-saved and control registers** (`%rbx %rbp %r12 %r13 %r14 %r15`, and `%rsp`/`%rip`),
  nothing else records them. The register digest is the only coverage, and that is exactly where
  HOLE-3 (`%r12`) and the PLANT (`%r15`) live. **This class is genuinely uncovered.**

This is a design tradeoff, not an oversight: you cannot distinguish a pointer from an integer by value
alone without provenance. The available refinement is to make canonicalization **provenance-aware for
the argument registers** — at a syscall boundary the syscall number tells you which arguments are
pointers and which are counts — and to leave the value-range heuristic only where provenance is
genuinely unknown.

## Second observation: the SaBRe suppression is right, and creates an absence

`97eb2c75f` adds `registers_nondeterministic_at_syscall_boundary`, set for `Backend::Sabre`, and
suppresses the whole `[regs]` emitter for that backend. The in-code comment gets the reasoning exactly
right — *"suppress the emitter rather than strip the offending register, which would hide a genuine
backend defect"* — which is the correct call and avoids the make-it-pass anti-pattern.

But the consequence is that **SaBRe now has zero register coverage, and the underlying defect (SaBRe
leaves `%rdx` carrying run-to-run varying values at the syscall boundary) is recorded only as a config
flag in the fix that hides its symptom.** That is an absence that will read as health: SaBRe emits no
`[regs]` line, so nothing ever reports a gap. It should be paired with a tracked ratchet item for the
SaBRe `%rdx` nondeterminism, otherwise the suppression becomes permanent by default.

## Recommendations

1. **Narrow the documented claim** to "values outside the user-VA window", so the coverage statement
   matches the code. One doc edit; removes a claim the implementation does not make.
2. **Add the boundary case to the test matrix**: a second marker at `0x1000 + N`, asserted as a
   *known* limitation (expected-equal) rather than left unstated. That turns an invisible hole into a
   recorded one, and it fails loudly the day canonicalization becomes provenance-aware.
3. **Consider provenance-aware canonicalization for argument registers** using the syscall number,
   keeping the value-range heuristic only for registers whose provenance is unknown.
4. **File the SaBRe `%rdx` syscall-boundary nondeterminism as its own ratchet item**, so the emitter
   suppression is a tracked exception rather than a silent permanent gap.
5. None of these block #1618. It is a genuine net-new instrument that catches a real class of
   divergence nothing else covers; these sharpen its claim and its test matrix.

## Reproduction

```bash
cd experiments/regdigest_address_window_20260806
rustc --edition=2021 -O probe.rs -o /tmp/probe && /tmp/probe
```

## Files

| file | what |
| --- | --- |
| `probe.rs` | verbatim extraction of `canonicalize_and_hash_pairs` at `f16d173b4` + 11 planted cases |
| `probe-output.txt` | the run output, including the two boundary controls |
| `results.csv` | every case with its result and verdict |
