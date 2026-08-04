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

### Compile cost per profile (`compile.csv`, `cargo build -p hermit` class)

| profile | compile wall (s) | compile CPU-s |
|---|---:|---:|
| `release` (opt3, shipped) | 83.9 | 437.5 |
| `release-o0` | 29.1 | **129.1** (−70% CPU) |
| `debug` | 47.1 | 191.9 |

**`release-o0` compiles cheaper than `debug`** (129 vs 192 CPU-s): `debug` carries
debuginfo + debug-assertions + overflow-checks. The fastest-compiling *semantics-safe*
profile is `release-o0`, not `debug`.

### Runtime cost per profile (`medians.csv`, CPU-s = contention-proof)

| profile | compute_bound CPU-s | syscall_bound CPU-s |
|---|---:|---:|
| `release` (opt3) | 107.3 (N=7) | 510.7 (N=5) |
| `release-o0` | 107.3 (N=3) — **identical** | 614.7 (N=1, +20% — weak) |
| `debug` | (compute ≈ neutral, unmeasured) | (firming) |

- **opt-level is runtime-NEUTRAL for compute-bound guests** → confirms the owner's
  "hermit doesn't do much compute" clause **for compute guests** (hermit not the hot path).
- **opt-level COSTS ~20% for syscall-bound guests** → confirms the owner's **exception**
  (hermit *is* the hot path per-syscall). `release-o0` syscall is N=1; being firmed by
  `firm_axis_syscall.sh` → `results.axis-firm.csv` (o0 + debug syscall, N=3). *The DAG-wall
  verdict below does not depend on this magnitude.*
- Semantics byte-identical across all three profiles for both guests (`semantics.txt`).
  `debug` still flips debug-assertions/overflow-checks → **excluded** from any determinism
  recommendation; `release-o0` keeps release's assertion settings (only opt-level differs).

### DAG-wall verdict — the profile lever is a dead end for THIS DAG

The compile+test **sum** is decided by **DAG topology**, not the CPU-s arithmetic:

- **Release compile is OFF the critical path.** The release hermit binary is built by
  `build.dbi_release` (240s) + `build.sabre_release` (90s), which hang off `build.workspace`
  and run **in parallel** with the `clippy(300) → strict_compat(600)` critical-path chain
  (verified: `ci/dag/portable.json` deps). Speeding that compile up (`release-o0`) buys
  **~0 DAG wall** — compile is parallelizable on a 316-core box.
- **Release runtime is ON the critical path.** `strict_compat` (600s = **47% of the
  critical path**) runs `validate.sh --portable-strict-compat-only`, executing short
  exec/syscall-heavy utilities under `target/release/hermit` → hermit is partly the hot
  path → an `o0` release binary would run those **slower**, lengthening the serial tail.
- **Net:** `release-o0` trades a parallel/off-path compile saving for a serial/on-path
  runtime penalty = **strict loss**. Amdahl, grounded in the real topology.

**We DO build both** debug (`build.workspace` → `target/debug/hermit`, `HERMIT_BIN`,
`validate.sh:735`) and release (`build.{dbi,sabre,liteinst_runtime}_release` →
`target/release/hermit`, `validate.sh:738-739,4138`). They are **separate shared build
nodes** feeding distinct consumer sets — compiled twice total, **not** duplicated per node.
The current debug/release split already implements the compile+test-sum optimum:
compile-fast (debug) on the compile-bound critical node, run-fast (release) on the
guest-execution nodes. **The real levers are de-serializing the strict_compat tail and the
`hermit_guest` cap — not the build profile.**

_Provenance: `compile.csv`, `medians.csv`, `semantics.txt` (measured, hermit
`8f656b4d` / reverie `9e7af7df`, devbig014); topology from `hermit/ci/dag/portable.json`
+ `validate.sh` @ main. `release-o0`/`debug` syscall cells being firmed (N=1→N=3) by
`firm_axis_syscall.sh`; verdict is topology-bound and independent of that magnitude._
