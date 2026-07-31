# GHC `--make -j1` is bitwise-reproducible under Hermit today — no ticker fix, no `-C0`

**Date:** 2026-07-30 · **Task:** `confirm-ghc-j1-determinism` · **Agent:** impl, opus-4.8
**Evidence:** `experiments/rb_ghc_j1_determinism_20260730/` (README, metadata.json, results.csv)

## Headline

On the 46-module Haskell workload (GHC 9.8.4, `ghc --make -j1 -O0`, fixed output path,
fingerprint hashed outside hermit), **`-j1` is bitwise-reproducible in every configuration
tested — 3/3 runs each — and all produce the identical aggregate hash `0010dfe3…`, equal to
the native (no-hermit) baseline:**

| config (all `-j1`, 3 runs) | reproducible |
|---|---|
| native, no hermit | ✅ |
| unfixed hermit `--strict` (RTS ticker ON) | ✅ |
| unfixed hermit relaxed `run` (ticker ON) | ✅ |
| unfixed hermit `--strict +RTS -C0` (ticker OFF) | ✅ |
| fixed (timerfd) hermit `--strict` | ✅ |
| fixed (timerfd) hermit relaxed | ✅ |

"unfixed" = binary `379feaac` built at `32f004cd` = **clock_getres NULL fix only, NO timerfd
virtualization** (i.e. the default/main ticker behavior). "fixed" = `64661f45` (PR #1187).

Probe is sensitive: `-j8` hashes differ (fixed `-j8`=`10e37923`; unfixed `-j8`=3/3 distinct),
so the uniform `-j1` result is real.

## Answers to the owner's questions

- **Is `ghc --make -j1` reproducible under hermit `--strict` (3 runs)? relaxed?** Yes — 3/3
  in both, and byte-identical to native.
- **Does the `-C` ticker still cause nondeterminism at `-j1`?** No. `-j1` is **clean by
  default**. No `+RTS -C0` needed. No timerfd-virtualization fix needed.
- **Mechanism:** GHC info-table symbol nondeterminism comes only from *concurrent* genSym
  across parallel compile workers (`-j>1`). At `-j1` a single worker compiles sequentially,
  so symbol order is deterministic no matter when the ticker fires.

## Bottom line for the roadmap

The realistic hermit target — **`-j1`, since hermit sequentializes threads and gets zero
parallelism benefit** — is **already reproducible with stock/default hermit** (the only
prerequisite is the `clock_getres` NULL-`res` startup fix, still not on `origin/main`; see
PR #1164/#1187 lineage). The `-C` **ticker determinization (PR #1187) is NOT needed for the
`-j1` target** — it only helps parallel `-j>1` builds, which hermit gains no speed from.
So `determinize-ghc-rts-ticker` (`-j8`) is correctly demoted to forward-looking; `-j1` is
the actionable, already-working path.

## Honest caveats

- For this workload native `-j1` is already reproducible, so hermit adds nothing for
  reproducibility *here*; hermit's `-j1` value is for other host-dependent nondeterminism
  (timestamps, paths, `/proc`, RNG) not exercised by this `-O0` fixed-path pure-compile job.
- Single package, GHC 9.8.4, `-O0`, one host; `-O2`/TH/plugins/larger builds untested.

Related: [[nix-minimum-hermit-dose]] (analogous "minimum dose" finding for nix builds).
