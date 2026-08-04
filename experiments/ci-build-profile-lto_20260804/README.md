# CI build profile: full-LTO vs thin-LTO vs no-LTO (release semantics)

**Task:** `ci-build-profile-release-no-lto-or-fast-lto` — owner asked for a CI mode with
release semantics but LTO off / thin, replacing release in the DAG, because "full LTO is a
link-time serial phase" bottlenecking compile. Report three rows (compile wall · test wall ·
total · runtime delta); **only the total decides**; determinism-relevant knobs must be unchanged.

## Headline (the answer)

**The premise is already satisfied — there is no full LTO to remove.** The Hermit workspace has
**no `[profile.*]` stanzas anywhere**, no `.cargo/config`, and no `CARGO_PROFILE_RELEASE_LTO` /
LTO `RUSTFLAGS` in any of the 9 CI workflows. So the CI "release" profile **is cargo's default
release profile, whose `lto = false`.** CI already builds release with **no LTO** — the
compile-cheapest of the three settings. The owner's *mechanism* is correct (full LTO = serial
link phase; measured below it collapses build parallelism 9.6×→4.23×), it just isn't enabled here.

Measured, adding LTO only makes the total **worse**: it raises compile wall by +35% (thin) / +164%
(fat) and changes runtime by ≤2% — a delta that lives entirely in hermit's small user-space
fraction, because ~83% of the supervisor's CPU on the syscall-hot workload is kernel ptrace `sys`
time that LTO cannot touch.

## Three rows — only the TOTAL decides

Unit under test = exactly what `validate.sh` builds for the release-consuming jobs:
`cargo build --release -p hermit --features third-party-backends`. Only knob varied:
`CARGO_PROFILE_RELEASE_LTO ∈ {false, thin, fat}`. Clean (cold) build per row.

| profile | compile wall | compile parallelism | runtime Δ (supervisor-hot) | verdict on TOTAL |
|---|---|---|---|---|
| **no-lto (current)** | **93.6 s** | 9.6× | baseline (cpu 3.44 s) | **lowest total — already optimal** |
| thin-lto | 126.6 s (+35%) | 9.9× | −0.9% (cpu 3.41 s) | loses: +33 s compile for ≤1% runtime |
| full-lto | 247.4 s (+164%) | 4.23× (serial link) | −2.0% (cpu 3.37 s) | loses badly: +154 s compile |

TOTAL = compile + test. LTO changes test wall by at most the runtime Δ (≤2%, and only on the most
supervisor-hot guest; real programs are more mixed → less). For **thin** to beat **no-lto** on
total, the release-consuming test wall would need `0.02·T > 33 s` ⇒ **T > 27 min of pure
supervisor-hot test time**; for **full**, `T > 128 min`. Neither is remotely plausible. **no-lto
wins the total at every realistic test wall.**

## Semantic guard (determinism unchanged) — holds by construction

`CARGO_PROFILE_RELEASE_LTO` overrides **only** `profile.release.lto`. `debug-assertions=false`,
`overflow-checks=false`, `panic="unwind"` are the release-profile defaults and are **not** touched
by this env var, so they are byte-identical across all three rows. LTO is a codegen/link-time
optimization and by definition cannot change these. Empirically corroborated: all three binaries
produced identical guest output with rc=0 across 42 runs. A hypothetical LTO adoption would keep
these knobs; the tests would still measure the same determinism.

## Related but separate knob: opt-level (this is where the real compile win is)

Sibling experiment `hermit-build-profile-compute-vs-syscall_20260804` measured `release@opt0` as
semantics-safe and ~3.4× cheaper to compile, runtime-neutral for the ptrace supervisor (see memory
`hermit-opt-level-runtime-neutral-for-supervisor`). **opt-level, not LTO, is the release-compile
lever with headroom.** That is a separate task; LTO is already at its cheapest setting.

## Which CI jobs consume DEBUG vs RELEASE, and why (also owed)

The split is a **build-cost / artifact-pairing optimization, not a correctness requirement.**
Full file:line map in `debug-release-consumer-map.md`. Summary:

- **DEBUG** (`target/debug/hermit`, `validate.sh:733`): the entire bulk deterministic suite — all
  `cargo test`/`nextest` targets, ptrace run/verify/record-replay smoke, envelope levels L1–L4,
  and **every e2e manifest bucket incl. KVM/DBI/SaBRe via the manifest harness**
  (`ci/test_harness.sh:15`). CI builds the debug tree once (`ci-portable.yml:14-17`) and fans it to
  every shard. Standardizing these onto release would force a full release workspace compile (the
  path CI explicitly avoids) and drop the dev-profile debug-assertions/overflow-checks.
- **RELEASE** (`target/release/hermit`, `validate.sh:736`): exists for **exactly one hard reason** —
  the three third-party backend runtimes (DynamoRIO/DBI, SaBRe, LiteInst) are compiled/staged **only
  in release** (`build.dbi_release`, `build.sabre_release`, `build.liteinst_runtime_release`), and
  `test.dbi_parity` / `test.sabre_examples` / `test.liteinst_strict` point `hermit` at the release
  binary **to match the profile of the `.so` runtime it loads.** That pairing (hermit profile ==
  backend `.so` profile) is the one non-negotiable constraint.
- **Profile-agnostic** (could standardize, with a tradeoff): `strict_compat`, `rr`, `e9patch`, and
  even `dbi_parity` read the overridable `STRICT_COMPAT_HERMIT_BIN` and actually run on **debug** in
  the DAG lane (`ci-dag.yml:27`, `validation-levels.yml:41`) yet **release** when `validate.sh` runs
  standalone; `Makefile:199-203` runs the same DBI/KVM parity matrix on the debug binary. The cost of
  moving them fully to debug is rebuilding the backend `.so`s in debug too and losing release timeout
  headroom on the heavy `--strict --verify` sweeps.

So "the determinism guard requires the split" is **not** the reason for the debug/release split.
The determinism guard (debug-assertions/overflow-checks) is a *side benefit* of debug being the
default build; the actual driver is (a) build once, reuse for the test matrix, and (b) the
hard hermit-profile==backend-`.so`-profile pairing rule for the third-party backends.

## Provenance

- UTC date: 2026-08-04. Host (short): `devbig014`. Kernel `6.18.39-0_fbk0_hardened_0`. 316 cores, 754 GB.
- Hermit SHA `b384187efd725c504d69281f043d442325d4fcb2`; Reverie `114b3dfcf5fc5fb8cee10877944b0ed7be529522`;
  parent `472d74d1190c94d2de55d9907f6224687ed06dba`. Toolchain: nightly; `cargo/rustc 1.99.0-nightly`.
- Full machine-readable facts in `metadata.json`.

## Methods

- **Compile** (`measure_lto_compile.sh` → `compile-lto.csv`): one clean (`rm -rf` target) build per
  LTO level, `CARGO_BUILD_JOBS=32`, boxed via `systemd-run --user --wait` (CPUQuota 3200%), timed
  with `/usr/bin/time -v`. Serial (one at a time) to be a good citizen during the concurrent reverie
  coalesce. Single sample per row; the deltas (+35%, +164%) are far larger than build-to-build noise.
- **Runtime** (`measure_lto_runtime.sh` → `runtime-lto.csv`): CPU time (user+sys) is the LTO-sensitive,
  load-insensitive metric. `/usr/bin/time -v hermit run -- <guest>`, unpinned, 7 measured reps
  (+1 warmup) per (variant,guest), variant order interleaved, medians reported. Guests scaled from the
  sibling experiment via `-DITERS`: `syscall_bound_100k` (100k trapped `getpid` — supervisor-hot),
  `compute_bound_80m` (guest-compute control). All 42 measured runs rc=0.
- **Boxing caveat:** `systemd-run --user --property=AllowedCPUs=…` makes hermit exit 101 silently
  (transient-scope/ptrace-setup interaction); plain `/usr/bin/time` as a direct parent works. Runtime
  placement therefore left to the kernel — fine because the compared metric (CPU time) is placement-
  independent. Do NOT wrap hermit in `/usr/bin/time` *inside* systemd-run.

## Results (raw)

- `compile-lto.csv` — per-row compile wall/cpu/parallelism.
- `runtime-lto.csv` — 42 raw runtime samples (user/sys/cpu/wall, rc).
- `results.csv` — the three-row headline table.
- `debug-release-consumer-map.md` — full file:line debug/release consumer map.

## Recommendation

**Do not add an LTO-toggling CI profile.** CI release is already `lto=false` (the compile-cheapest
setting); thin/fat LTO would raise compile 35%/164% for ≤2% runtime that cannot help the total. If
the goal is a cheaper release compile node on the critical path, pursue **`release@opt0`** (separate
task, ~3.4× cheaper compile, measured semantics-safe/runtime-neutral) — that is the real lever.
