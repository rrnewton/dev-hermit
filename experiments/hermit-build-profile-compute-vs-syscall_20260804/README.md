# hermit build profile × guest shape: does an unoptimized hermit run materially slower?

**Task:** `build-profile/ci` (with hermit-220). Owner hypothesis: *"LTO can be VERY
SLOW for release. No LTO or fast LTO for CI. But the FASTEST POSSIBLE MINIMAL DEBUG
COMPILE may be best — HERMIT ITSELF DOESN'T DO THAT MUCH COMPUTE."*

This experiment owns the **last clause**: does hermit's own code do enough compute
that an unoptimized (debug) build runs guests materially slower than an optimized
(release) build — and **does that depend on the guest's shape**?

## Why shape should matter (prior finding)

The predecessor attributed LiteInst's ~14.5× slowdown to **per-syscall ptrace host
round-trips**. Consequence:

- **Syscall-bound guest** → every guest syscall traps into hermit's supervisor, so
  **hermit's own code is the hot path**. An unoptimized hermit build should be
  materially slower here.
- **Compute-bound guest** → the guest executes its own instructions; hermit is
  barely on the hot path. Debug vs release hermit should be ~equal here.

If the two shapes diverge, "hermit doesn't do much compute" is **true only for
compute-bound guests**, which argues for **per-node-class CI profiles**, not one.

## Method

- **Guests** (`guests/`, always `-O2`, identical across hermit profiles):
  - `compute_bound.c` — FNV-style integer-mix loop, ~1.6s native user CPU, ~1 syscall.
  - `syscall_bound.c` — tight loop of raw `syscall(SYS_getpid)` (not libc-cached, so
    every iteration traps), 1M iters, ~0.07s native.
- **Profiles (three):**
  - `release` — shipped: `opt-level=3`, `debug-assertions=off`, `overflow-checks=off`.
  - `release-o0` — `--release` with `CARGO_PROFILE_RELEASE_OPT_LEVEL=0` (env only). The
    **semantics-preserving fast-compile candidate**: only opt-level differs from
    release, which cannot change well-defined behaviour → a **valid CI profile** for
    determinism tests.
  - `debug` — cargo default `dev`: `opt-level=0` **and** `debug-assertions=on` **and**
    `overflow-checks=on` → **behaviour-changing** (panic/overflow control-flow, unwinding).
    Measured for the cost curve but **NOT** a valid determinism-test CI profile.
  All built from primary `hermit` @ main into experiment-local `target-*/` dirs with
  `--locked` (never touches a slot/primary `target/` or `Cargo.lock`); all profile
  knobs are env overrides — **no `Cargo.toml` edit**.
- **Semantic guard:** `semantics.txt` records guest-observable (exit, stdout hash) per
  profile; `release` vs `release-o0` **must** match (asserted). A faster DAG that tests
  *different* behaviour is worthless — hence `debug` is excluded from any recommendation.
- **Decision metric:** report **compile wall/CPU** and **runtime (test-proxy) wall/CPU**
  per profile, then **TOTAL = compile + K·runtime**. `analyze.py` prints the **break-even
  test-count K\*** where a cheaper-compile profile stops paying off — "only the total decides".
- **Boxing:** every measured command runs in a `systemd-run --user` unit at
  `CPUQuota=100%` — a **1-CPU box, not pinned** (one CPU of bandwidth, scheduler-placed,
  no cpuset). Native and hermit share the **same box**, so any hermit slowdown is
  **instrumentation cost, not lost parallelism** (hermit's threads sequentialize onto the
  one CPU). Pinning is the *separate* `stateful-cpuset-allocator-…` task, not this one.
- **Statistics:** N=7 runs per cell, **medians**; **wall AND cpu-seconds** (u+s). CPU-seconds
  is the contamination-proof metric when the box is shared with other agents' work.

## Run

```
make -C guests                       # build guests (dynamic; static libc absent)
bash build_and_run.sh 7              # AFTER the drain: builds both profiles, measures
python3 analyze.py                   # medians.csv + verdict table
```

## Files
- `guests/` — guest sources + Makefile (`bin/` is generated, git-ignored).
- `harness.sh` — boxed measurement loop → `results.csv`.
- `build_and_run.sh` — builds both hermit profiles + drives the harness.
- `analyze.py` — medians + the debug/release and hermit/native ratios.
- `results.csv` / `medians.csv` — raw + aggregated (generated).
- `metadata.json` — SHAs, host, toolchain.

## Results

_Pending build_and_run.sh completion (runs after the PR drain to preserve SOLO)._
