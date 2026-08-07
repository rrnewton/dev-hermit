# Detlog parity on ptrace, SaBRe and LiteInst — 2026-08-07

> ## CONTAINMENT — read before quoting anything here
>
> **This is ONE dimension's result.** Detlog self-determinism passing on three
> backends is not a statement about those backends being deterministic, and it
> must not be laundered into one.
>
> Specifically, **nothing here supports any claim about**:
> - **stack** determinism — those baselines remain BLOCKED and FAILING
>   (LiteInst 303/413, SaBRe 121/121), which by itself refutes a backend-wide reading;
> - **heap** determinism — heap was not measured, not sampled, and detlog
>   carries no heap-domain evidence;
> - **backend-wide** determinism — three streams agreeing with themselves on one
>   dimension is exactly one dimension.
>
> The inference is wrong in **both** directions. The reverse one (stack failure ⇒
> detlog unmeasurable) was nearly published as a false finding, which is why this
> dimension was blocked wholesale; the forward one (detlog PASS ⇒ backend clean)
> would be the same error with the sign flipped.

## Denominators — not shared, and that matters

The three backends emit **different numbers of detlog records for the same
guest**: ptrace 141, SaBRe 368, LiteInst 1245. There is no common denominator,
so a single "parity %" is undefined. The same pair reads **2.8% or 1.1%**
depending on which side you divide by. Every cross-backend row below prints
both, and no normalization or alignment was invented to force a shared one.

## 1. Self-determinism on detlog (run 1 vs run 2, whole stream)

| backend | differing | records | status |
| --- | ---: | ---: | --- |
| ptrace | 0 | 141 | **PASS** |
| SaBRe | 0 | 368 | **PASS** |
| LiteInst | 0 | 1245 | **PASS** |

This is what makes the dimension scorable: a cross-backend number is only
meaningful once each side is stable against itself.

## 2. Cross-backend detlog parity, per pair

| pair | records A | records B | identical? | common prefix | prefix/A | prefix/B | self-det A/B |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| ptrace vs SaBRe | 141 | 368 | NO | 4 | 2.8% | 1.1% | PASS/PASS |
| ptrace vs LiteInst | 141 | 1245 | NO | 7 | 5.0% | 0.6% | PASS/PASS |
| SaBRe vs LiteInst | 368 | 1245 | NO | 4 | 1.1% | 0.3% | PASS/PASS |

**Whole-stream parity is 0 of 3 pairs.** Every cell records its self-determinism
status, which is PASS on both sides throughout.

## 3. First divergence, per pair

- **ptrace vs SaBRe @ index 4** — SaBRe issues a syscall the golden does not:
  `brk(NULL)` vs `getpid()`.
- **SaBRe vs LiteInst @ index 4** — the mirror of that same fact.
- **ptrace vs LiteInst @ index 7** — same syscall, different address argument:
  `arch_prctl(12289, 0x7fffffffec00)` vs `0x7fffffffeb70`, Δ = 0x90 = 144 bytes.

On the third: that is an address **appearing inside a detlog record**. It is a
detlog fact and nothing more — see the containment box. It is not evidence about
the stack dimension, LiteInst's `/proc/self/maps` self-scan, or SaBRe's
Reverie-side baseline, all of which remain separately blocked.

## 4. What the low prefixes do and do not mean

Both causes are single-record events near the start of the stream. That is why
every prefix is small (4–7 records out of 141–1245) even though all three
streams are individually deterministic and structurally similar.

**A low prefix means the streams part EARLY, not that they are broadly unalike.**
Whether the bulk agrees after the divergence is a different measurement —
alignment-based rather than prefix-based — and it was deliberately not run,
because choosing an alignment would be defining a new metric rather than
reporting this one.

## 5. Planted-divergence detection

- **Self-determinism:** perturbing one record of the run-2 log at the first,
  middle and last position flips PASS → FAIL in **9 of 9** cases (3 backends ×
  3 positions). No misses.
- **Cross-backend prefix:** planting earlier than the real divergence strictly
  lowers the prefix in **6 of 6** cases (e.g. ptrace vs LiteInst 7 → 0 planted at
  0, → 6 planted at 6). Planting *at* the already-diverging index leaves it
  unchanged; that is recorded explicitly rather than counted as a pass, because a
  control planted where the streams already differ cannot lower anything and
  proves nothing.

## Reproducing

```bash
./derive.py            # regenerate results.csv
./derive.py --check    # => REPRODUCIBLE: results.csv is byte-identical
```

`raw/dl-<backend>-<rep>.d` are the six source logs, committed here because they
were produced into gitignored `scratch/` and would otherwise have been lost.
