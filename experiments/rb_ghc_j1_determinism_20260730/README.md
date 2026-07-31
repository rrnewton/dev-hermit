# ghc --make -j1 determinism under Hermit (confirm-ghc-j1-determinism)

**Date:** 2026-07-30 · **Host:** devbig014 · **Container:** ghcbw (haskell:9.8.4, GHC 9.8.4)
**Workload:** 46-module Haskell package, `ghc --make -jN -O0`, fixed output path.
**Fingerprint:** aggregate SHA-256 over sorted `*.o` + linked binary, computed **outside** hermit.

## Question

Hermit sequentializes threads, so `-j8` under hermit buys **zero** parallelism — it only
wastes memory and context switches. The realistic target is `-j1`. Is `ghc --make -j1`
bitwise-reproducible under `hermit run --strict` and relaxed? Does the GHC `-C` RTS
context-switch ticker still cause nondeterminism at `-j1`, or is `-j1` clean by default
(no `+RTS -C0`, no timerfd-virtualization fix)?

## Result matrix (3 runs each; all hashes = `0010dfe3…294122`)

| binary | timerfd fix | mode | ticker | runs | reproducible |
|---|---|---|---|---|---|
| native (no hermit) | n/a | native | on | 3/3 | ✅ yes |
| unfixed `379feaac` | no | `--strict` | on | 3/3 | ✅ yes |
| unfixed `379feaac` | no | relaxed (`run`) | on | 3/3 | ✅ yes |
| unfixed `379feaac` | no | `--strict` | **off** (`+RTS -C0`) | 3/3 | ✅ yes |
| fixed `64661f45` | yes | `--strict` | on | 3/3 | ✅ yes |
| fixed `64661f45` | yes | relaxed (`run`) | on | 3/3 | ✅ yes |

**All six `-j1` configurations produce the identical aggregate hash, equal to the native
baseline.** At `-j1`, hermit output is byte-identical to native.

Probe sensitivity (sanity): the `-j8` hash differs — fixed `-j8` = `10e37923…`, and
unfixed/oldstock `-j8` was 3/3 **distinct**. So the fingerprint detects real ordering
differences; the uniform `-j1` result is genuine, not a stuck probe.

## Answers

- **`ghc --make -j1` is bitwise-reproducible** under hermit `--strict`, relaxed, with and
  without the timerfd fix, with and without `+RTS -C0`. 3/3 every time.
- **`-j1` is clean by default.** The `-C` ticker does **not** cause nondeterminism at `-j1`.
  No `+RTS -C0` needed. No timerfd-virtualization fix needed.
- The `clock_getres` NULL-`res` fix (`32f004cd`, PR #1164/#1187 lineage) is still required
  for GHC to start under hermit at all (both modes); it is not yet on `origin/main`.

## Why

GHC info-table symbol (`genSym`/`Unique`) nondeterminism arises only from **concurrent**
symbol generation across multiple parallel compile workers (`-j>1`). At `-j1` a single
worker compiles modules sequentially, so symbol order is deterministic regardless of when
the `-C` ticker fires. The ticker perturbs ordering only when threads race.

## Implication

The realistic hermit target — `-j1`, since hermit gains no parallelism — is **already
bitwise-reproducible with stock/default hermit today** (only the `clock_getres` startup fix
is required). The `-C` ticker determinization (PR #1187) is **not needed for `-j1`**; it
only matters for parallel `-j>1` builds under hermit, which hermit gains no speed from.

## Honest caveats

- For this workload **native `-j1` is already reproducible**, so hermit adds nothing for
  reproducibility at `-j1` *here*. Hermit's `-j1` value would be for other host-dependent
  nondeterminism (timestamps, absolute paths, `/proc`, RNG) not exercised by this `-O0`,
  fixed-output-path, pure-compile workload.
- Single package, GHC 9.8.4, `-O0`, one host. `-O2`, TH/plugins, or larger builds may
  exercise additional nondeterminism; not tested here.

## Reproduction

Container `ghcbw`, harness `/work/probe_j1.sh <tag> <strict|relaxed> <jobs> [extra ghc args]`
(fingerprints outside hermit). Select binary via `HERMIT_BIN` env in `/work/hermit.sh`:

```bash
# unfixed (clock_getres only): HERMIT_BIN=/hostrepo/scratch/ghc-j1-unfixed/hermit/target/release/hermit
# fixed  (timerfd virt):       HERMIT_BIN=/work/hermit-bin
export HERMIT_BIN=<binary>
/work/probe_j1.sh strict1 strict 1              # hermit run --strict -- ghc --make -j1 -O0 ...
/work/probe_j1.sh relaxed1 relaxed 1            # hermit run          -- ghc --make -j1 -O0 ...
/work/probe_j1.sh c0-1 strict 1 +RTS -C0 -RTS  # ticker disabled
```

Raw logs: `scratch/ghc-j1-{fixed,unfixed}-matrix.log`, `scratch/ghc-j1-native.log`.
