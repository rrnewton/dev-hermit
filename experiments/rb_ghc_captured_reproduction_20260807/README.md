# Capturing the GHC reproducible-build experiment — and what the capture found

**Date:** 2026-08-07 · **Agent:** claude-coord-176 (impl, claude-opus-5) · **Track:** reproducible builds with Hermit

## Question

The owner asked a specific question about the prior GHC work:

> *"I know they made some progress on that, but not if we have a captured demo/experiment scripting setup."*

So: for `experiments/rb_drb_haskell_ghc_concurrency_20260729` — the experiment
that reported *"native parallel GHC is non-reproducible; the same build under
`hermit run --strict` is bitwise-identical every time"* — **is there a captured,
re-runnable harness, or only recorded results?**

## Answer

**Only partially captured — and the gap mattered.** Two findings, the second
unexpected:

1. **There was no re-runnable setup.** The prior experiment committed four
   useful scripts, but nothing that could produce the environment they run in.
   Its own reproduction section begins *"In the podman container 'ghcbw'
   (haskell:9.8.4), with the host-built hermit binary staged at
   `/work/hermit-bin` and host glibc in `/work/hostlibs`"* — a container someone
   built by hand, now gone. `podman images` on this host had no `haskell` image
   at all. Concretely missing: container creation, hermit staging, host-glibc
   closure staging, and any single entrypoint. `harness/hermit.sh` even defaulted
   to `/hostrepo/worktrees/makedet/hermit/target/release/hermit` — a worktree
   slot that no longer exists.

2. **With the capture built, the headline result no longer reproduces.** Running
   the *same* measurement script, on the *same* image, with the *same* generated
   package, the flagship configuration is now **non-reproducible**:

   | configuration | 2026-07-29 (recorded) | 2026-08-07 (this run) |
   |---|---|---|
   | native `-j8` default | NON-REPRODUCIBLE (3 distinct) | NON-REPRODUCIBLE (3 distinct) |
   | native `-j8 +RTS -C0` | NON-REPRODUCIBLE (3 distinct) | NON-REPRODUCIBLE (3 distinct) |
   | native `-j8 +RTS -N8` | NON-REPRODUCIBLE (3 distinct) | NON-REPRODUCIBLE (3 distinct) |
   | hermit `--strict -j8` default | NON-REPRODUCIBLE (3 distinct) | NON-REPRODUCIBLE (3 distinct) |
   | **hermit `--strict -j8 +RTS -C0`** | **REPRODUCIBLE (1 distinct, `e6037a6c…`)** | **NON-REPRODUCIBLE (3 distinct)** |
   | hermit `--strict -j8 +RTS -N8` | NON-REPRODUCIBLE (3 distinct) | NON-REPRODUCIBLE (3 distinct) |

   Five of six rows replicate exactly. The one that does not is the result the
   experiment was named for. This run's three `-C0` aggregates are
   `d8e7b996…`, `d06adcbe…`, `34fefae8…`; none equals the recorded
   `e6037a6c158a39bd0ca90587e864fba22a7440d4c67dfc4e549ba1279797589f`.

**This is the argument for capture, stated by example.** A result with recorded
numbers but no runnable harness cannot tell you when it stops being true. This
one stopped being true and nothing noticed for nine days.

## Method

Identical measurement to the prior experiment, deliberately: `harness/gen_pkg.sh`
and `harness/results_csv.sh` are byte-for-byte the 2026-07-29 scripts, so the
comparison above is like-for-like and any difference is in the environment or
the product, not in the measurement.

- **Workload:** generated 46-module Haskell package (40 independent leaves + 5
  group aggregators + Main), built `ghc --make -j8 -O0`.
- **Fingerprint:** SHA-256 over all `.o` files sorted by name, plus the final
  binary, computed *outside* the Hermit boundary from the bind-mounted `/work`.
- **Runs:** 3 per configuration, 6 configurations.
- **Canonical output path** `/work/out` every run, so GHC's `-o`-path hashing
  cannot confound the comparison.

What this experiment adds is `run.sh`: it pulls the image, stages the hermit
binary plus its full shared-library closure *and the host loader* (the container
is Debian 11/glibc 2.31, older than the build host, so the host binary must run
under the host loader via `--library-path`), generates the package, runs the
matrix, and copies `results.csv` back out — then records the exact binary,
version string, SHA-256, image, and toolchain in `run_provenance.txt` beside the
numbers.

## Results

`results.csv` — 18 rows (6 configurations × 3 runs) with per-run aggregate
SHA-256. `run_provenance.txt` — what produced them.

Headline, stated with its scope: **for this 46-module package built
`ghc --make -j8 -O0` in `haskell:9.8.4`, native parallel GHC is non-reproducible
in all three RTS configurations tested (9/9 runs distinct within configuration),
and `hermit run --strict` did not make any of them reproducible at hermit
`1fadc03779f2a246a9b5af5d4a93533511c837df`.**

## Interpretation

The honest reading is **non-replication with attribution unknown**. I did not
isolate the cause, and there are at least three candidates:

1. **A Hermit regression** between `32f004cd63f0942d05818d900af2ea5780b9f87d`
   (the recorded run) and `1fadc03779f2` (this run).
2. **Host dependence.** The recorded run was on `devbig014`; this is a different
   machine — an AMD EPYC 9D85 where PMU validation fails
   (`AmdSpecLockMapShouldBeDisabled`) and `ARCH_SET_CPUID` returns `ENODEV`, so
   Hermit runs without CPUID interception and with unreliable RCB timers. Since
   the mechanism at issue is scheduling-order determinism, degraded RCB timing is
   a plausible contributor.
3. **The original result was not robust** — three runs is a small sample for a
   claim of determinism, and `-C0` narrows but does not eliminate RTS timer
   effects.

Deciding between these is a bisect plus a same-host A/B, which is exactly the
work the capture now makes cheap. **Do not cite the `-C0` reproducibility result
as current** until that is done. The mechanism write-up in the prior experiment
(GHC's `genSym` Unique supply leaking thread-interleaving order into
`c<base62>_str` local symbol names) is unaffected — that is an explanation of
*why* parallel GHC is nondeterministic natively, and the native rows still
confirm it.

## Reproduction

One command, from a clean host with podman:

```bash
./run.sh --hermit-bin /path/to/hermit/target/release/hermit
```

Requires a **release** hermit (a debug build is far too slow for 18 GHC builds)
and `podman`. Pulls `docker.io/library/haskell:9.8.4` (~3.4 GB) on first use via
`with-proxy` when available. Writes `results.csv` and `run_provenance.txt` beside
the script. Nothing is left behind unless `--keep` is passed; no image or build
tree enters the repository.

Runtime on this host: ~11 minutes wall clock (native builds seconds each; hermit
builds ~40-90 s each).

## Files

- `run.sh` — the missing capture: environment, staging, execution, provenance.
- `harness/gen_pkg.sh`, `harness/results_csv.sh` — unchanged from
  `rb_drb_haskell_ghc_concurrency_20260729`, so results are comparable.
- `harness/hermit.sh` — host-loader launcher; unlike the original it has no
  dead default path and documents why the indirection is needed.
- `results.csv`, `run_provenance.txt`, `metadata.json`.
