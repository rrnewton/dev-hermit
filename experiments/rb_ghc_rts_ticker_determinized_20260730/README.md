# Hermit determinizes the GHC RTS context-switch ticker (stock parallel GHC)

**Task:** `determinize-ghc-rts-ticker` · **Date:** 2026-07-30 · **Agent:**
hermit-227 (impl, opus-4.8). Follows
`experiments/rb_drb_haskell_ghc_concurrency_20260729/`, which showed stock
parallel GHC was reproducible under Hermit only with `+RTS -C0`.

## Question

Can Hermit make **stock** `ghc --make -jN` (RTS context-switch ticker ON, no
`+RTS -C0`) bitwise-reproducible under `hermit run --strict`, by virtualizing
the ticker under virtual time?

## Answer (headline)

**Yes, for single-capability builds.** The GHC threaded RTS ticker is a periodic
`CLOCK_MONOTONIC` timerfd whose blocking `read()` on a dedicated OS-thread drives
green-thread preemption. Virtualizing timerfd against Detcore's virtual clock
(Hermit PR #1169) makes the same 46-module `ghc --make -j8 -O0` build
bitwise-identical run-to-run with **no `+RTS` flag**.

| hermit | stock `ghc --make -j8 -O0` (ticker ON), 3 runs | verdict |
|---|---|---|
| without PR #1169 | `2d38213e…`, `fbe6573d…`, `9577c519…` | 3/3 distinct — NON-REPRODUCIBLE |
| **with PR #1169** | `0855e32c…` ×3 | 3/3 identical — **REPRODUCIBLE** |

The control (old binary, same host/package/harness) isolates PR #1169 as the
cause. See `results.csv`.

## Mechanism

Previously Detcore passed timerfd straight to the host kernel, so ticks per unit
of virtual work were host-timing-dependent; that leaked into the interleaving of
GHC's global `genSym`/`Unique` supply and thus into local info-table symbol names
(`c<base62>_str`) in `.o` files (evidenced in the 2026-07-29 experiment). PR
#1169 stores per-timerfd virtual arming (`clockid`, virtual deadline, interval),
arms/queries/reads it against `thread_observe_time`, and parks a blocking read as
a timed waiter (`SleepUntil`) until the virtual deadline. The tick count is then
a pure function of the virtual schedule, so genSym order — and the symbols — are
fixed run-to-run.

## Scope / caveats

- Single-capability (`-N1`) job concurrency only. Multi-capability `+RTS -N>1`
  parallel `genSym` ordering is a known separate gap, not addressed here.
- Also confirmed the established `timerfd_virtual_time` lit test (one-shot 10ms)
  XPASSes at L2 (`hermit run --strict --verify`: Determinism verified).

## Reproduction

```bash
# In podman container 'ghcbw' (haskell:9.8.4), host-built hermit staged and run
# via /work/hermit.sh with HERMIT_BIN pointing at the release binary:
NB=/hostrepo/worktrees/slot03/hermit/target/release/hermit   # PR #1169 build
for tag in a b c; do HERMIT_BIN=$NB /work/probe_stock.sh $tag; done
# probe_stock.sh runs: hermit run --strict -- ghc --make -j8 -O0 (fixed out path)
# and prints an aggregate SHA-256 over all .o files + the final binary.
```

## Files

- `results.csv` — 2 variants x 3 runs with aggregate SHAs.
- `metadata.json` — SHAs, toolchain, host, workload, method.
