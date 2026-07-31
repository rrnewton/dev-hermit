# Hermit determinizes concurrent GHC compilation (DRB "GHC concurrency" frontier)

**Task:** `rb-drb-haskell-determinize` · **Date:** 2026-07-29 · **Agent:** hermit-230 (impl, opus-4.8)

## Question

The 2024 Reproducible Builds summit lists *"GHC produces nondeterministic output
when concurrency is enabled"* as an **unsolved frontier class**: Debian/Haskell
packages built with a parallel GHC (`ghc --make -jN`, threaded RTS) can produce
byte-differing `.o` objects run-to-run even with `SOURCE_DATE_EPOCH` and a fixed
build path. GHC's determinism project fixed `.hi`/ABI hashes but not the local
object-code symbol names generated under concurrency.

Can Hermit's deterministic execution engine close this gap — turn a
natively-nonreproducible parallel GHC build into a bitwise-reproducible one?

## Answer (headline)

**Yes, for the concurrency Hermit determinizes.** For the same 46-module package
built `ghc --make -j8 -O0 +RTS -C0`:

| Execution context | Result (3 runs) |
|---|---|
| **native** `+RTS -C0` | **NON-REPRODUCIBLE** — 3/3 distinct aggregate hashes |
| **`hermit run --strict`** `+RTS -C0` | **REPRODUCIBLE** — 3/3 identical (`e6037a6c…`) |

This is the flagship result: native parallel GHC is non-reproducible; the *same*
build under `hermit run --strict` is bitwise-identical every time.

Two honest caveats, both localized precisely below:

1. The win requires `+RTS -C0`, which disables GHC's RTS **context-switch
   ticker** — a green-thread preemption *timer* that Hermit does not yet
   determinize. `-C0` is **semantics-preserving** (cooperative scheduling of the
   same green threads); it does not change what GHC computes, only that it stops
   being preempted by a wall-clock timer Hermit can't yet virtualize.
2. Multi-capability (`+RTS -N8`) parallel `genSym` ordering is **not**
   determinized even under `--strict -C0`. Single-capability (default `-N1`)
   `-jN` job concurrency *is*.

## Full matrix (`results.csv`, `-j8 -O0`, 3 runs each)

| mode | RTS flags | verdict |
|---|---|---|
| native | default | NON-REPRODUCIBLE (3 distinct) |
| native | `-C0` | NON-REPRODUCIBLE (3 distinct) |
| native | `-N8` | NON-REPRODUCIBLE (3 distinct) |
| hermit `--strict` | default | NON-REPRODUCIBLE (3 distinct) — RTS ticker still fires |
| **hermit `--strict`** | **`-C0`** | **REPRODUCIBLE (1 distinct, `e6037a6c…`)** |
| hermit `--strict` | `-N8` | NON-REPRODUCIBLE (3 distinct) — multi-capability genSym |

Per-run aggregate SHA-256 fingerprints are in `results.csv`.

## The product blocker fixed along the way (PR #1164)

GHC's threaded RTS could not even *start* under Hermit: it aborted with
`getCurrentThreadCPUTime: no supported`. Root cause in
`detcore/src/syscalls/time.rs`: `handle_clock_getres` rejected a **NULL `res`
pointer** with `EFAULT`. That is kernel-incorrect — for `clock_getres` the
kernel validates the `clockid` and returns `0` **without** storing the
resolution when `res` is NULL. GHC's RTS probes the per-thread CPU clock exactly
that way (`clock_getres(clockid, NULL)` in `getCurrentThreadCPUTime`), so the
spurious `EFAULT` aborted every guest.

Fix: only write the resolution when the caller supplied a destination; otherwise
validate and return `0`. A regression test (`clock_getres_null_res_is_ok`) was
added. This is a bug fix to an existing handler — no new syscall, strategy, or
DetCore scheduling change — so no `post-facto-human-review` trigger applies.

- **Hermit SHA:** `32f004cd63f0942d05818d900af2ea5780b9f87d`
- **Branch:** `claude/fix-clock-getres-null-res` · **PR:** https://github.com/rrnewton/hermit/pull/1164
- **Validation:** `tests_time` 15/15 pass; `cargo fmt`/`clippy` clean.

## Mechanism (evidenced, not asserted)

GHC's global `Unique` supply (a `genSym` atomic counter) is consumed in
thread-interleaving order under concurrency. That order leaks into **local
info-table symbol names** of the form `c<base62>_str`. `symbol_nondeterminism_sample.txt`
captures **20** such names differing in a single leaf module's `.o` across two
native `-C0` runs (e.g. `c7hN_str`, `c7jE_str`, `c7m1_str`, `c8yF_str`, …),
while the object bytes differ (`dc9fb850…` vs `181b0d4b…`). Hermit serializes
the guest threads onto one virtual CPU with a deterministic schedule, so the
`genSym` consumption order — and therefore the symbol names — is fixed run to
run, once the RTS's own wall-clock preemption ticker is out of the picture.

## Residual Hermit determinization gaps (follow-up)

1. **RTS context-switch ticker (`-C`, default 20ms).** A green-thread preemption
   timer; the default `hermit --strict` build (ticker on) is still
   non-reproducible. Determinizing this timer under virtual time would remove the
   `-C0` requirement and make *stock* parallel GHC reproducible under Hermit.
2. **Multi-capability RTS (`-N>1`).** Parallel `genSym` across OS-thread
   capabilities is not determinized even under `--strict -C0`.
3. **`--strict --sequentialize-threads` livelocks** with the GHC threaded RTS
   (consistent with the known min-vtime blocking-via-polling livelock).

## Reproduction

```bash
# In the podman container 'ghcbw' (haskell:9.8.4), with the host-built hermit
# binary staged at /work/hermit-bin and host glibc in /work/hostlibs:
bash harness/gen_pkg.sh                 # generate the 46-module package at /work/pkg/src
HERMIT_BIN=/work/hermit-bin bash harness/results_csv.sh   # writes /work/results.csv
```

`harness/hermit.sh` runs the host hermit binary inside the container via
`ld-linux --library-path /work/hostlibs`. Fingerprints are computed by a plain
container shell reading the persistent `/work` bind mount, so they are
independent of hermit. Builds use a fixed canonical output path (`/work/out`) to
eliminate GHC's `-o`-path hashing confound.

## Files

- `results.csv` — authoritative 6-config × 3-run matrix with per-run aggregate SHAs.
- `symbol_nondeterminism_sample.txt` — the `c<base62>_str` Unique-symbol evidence.
- `metadata.json` — SHAs, toolchain, host, workload, method.
- `harness/` — `gen_pkg.sh`, `results_csv.sh`, `repro_matrix.sh`, `hermit.sh`.
