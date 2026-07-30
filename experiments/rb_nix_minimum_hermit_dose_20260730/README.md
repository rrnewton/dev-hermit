# Minimum hermit dose for a serial nix build (rootless podman)

**Question.** For a small nix build run serially (`nix -j1 --cores 1`) under hermit
inside the validated rootless-podman `nixos/nix` path, what is the **minimum**
hermit relaxation/determinization flag-set (“dose”) that **robustly** reproduces
byte-identical output — and what determinism level is that, honestly?

## Method
- Container path: the validated recipe (`../nix-hermit-container-approach_20260730/recipe`)
  — hostlibs loader + `--security-opt seccomp=unconfined` + `hermit run --no-namespace`,
  image `nixos/nix:2.3.16` (pre-pidfd; avoids the `pidfd_send_signal` `--strict` blocker).
- Probe: a real nix derivation whose builder emits hermit-determinized
  nondeterminism sources using **bash builtins only** (the build sandbox clears
  PATH): `read` two values from `/proc/sys/kernel/random/uuid` + `read /proc/uptime`.
  `$RANDOM` is deliberately excluded — it stays nondeterministic under
  `--no-namespace` because bash seeds it from the real host `getpid()` (see the
  random-leaks finding).
- Witness: `sha256` of the built output content, compared across **N=3** runs per
  dose. Each `podman run --rm` has a fresh `/nix/store`, so identical hash ==
  genuine determinization, not caching. `native` (no hermit) is the control.
- Scripts: `dose-run.sh` (one build under a given `HERMIT_ARGS`), `dose-matrix.sh`
  (the full sweep → `results.csv`).

## Results (`results.csv`, N=3)

| Dose | Reproducible | wall time |
|------|--------------|-----------|
| `native` (no hermit) | **NO** (3 distinct hashes) | 10–11 s |
| `--no-namespace --strict --sequentialize-threads` | YES | 2–3 s |
| `--no-namespace --strict` | YES | 2–3 s |
| **`--no-namespace`** (default determinization) | **YES** | **2–3 s** |
| `--no-namespace --no-sequentialize-threads` | YES | 13–14 s |
| `--no-namespace --no-sequentialize-threads --no-rcb-time --no-deterministic-io` | YES | 13 s |

Two internally-stable hash equivalence classes: `0a4943cf…` (default/sequentialized
scheduling) and `abce6b2d…` (RCB path). Each is reproducible under its own fixed
dose; the value differs because the scheduling/PRNG sequence differs.

## Findings
1. **Every hermit dose reproduces this serial build; native does not.** The
   probe genuinely captures nondeterminism (native → 3 different hashes).
2. **Minimum dose = plain `--no-namespace`** (default deterministic scheduling).
   It is also the **fastest** (~2–3 s). `--strict` is *not required* for
   reproducibility (it only adds fail-closed on unsupported syscalls) — but it is
   free here (same speed) and a safe add-on when the build’s syscalls are all
   supported.
3. **`--no-sequentialize-threads` is a *pessimization* for serial builds** (~5×
   slower: 13 s vs 2–3 s). Disabling sequentialization forces RCB/PMU-preemption
   determinism (PMU counting + single-step skid correction). For a `-j1 --cores 1`
   build there is ~no concurrency, so sequentialization is nearly free and the
   RCB path is pure overhead. This **inverts** the “sequentialization is the slow
   part” assumption *for serial builds*.
4. **Determinism level — honest label:** `reproducible-output-under-no-namespace`.
   This is **not** a strict-sandbox (full-namespace) victory: `--no-namespace`
   delegates isolation to podman and “is not a sandbox” (hermit’s own warning);
   hermit only *determinizes*. The output is byte-identical across runs, which is
   what reproducible-builds needs, but the sandboxing guarantee is podman’s.
5. Caveat: `$RANDOM`/`getpid()` remain nondeterministic under `--no-namespace`
   (real host pid); builds that seed on `getpid` would need full-namespace hermit
   or getpid virtualization. Not exercised by typical nix builds.

## Manifest proposal
`manifest.example.toml` — `hermit-dose/v1`: one `[[build]]` per build recording the
minimum `dose`, honest `determinism` label, `strict`/`sequentialize` choices,
`witness_sha256`, and `runs_verified`. An outer minimum-dose finder fills it in;
the reproducer replays `dose`.

## Reproduce
```
cd experiments/rb_nix_minimum_hermit_dose_20260730
N=3 ./dose-matrix.sh   # needs rootless podman + fwdproxy; ~2–3 min
```
