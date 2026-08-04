# Minimum hermit "dose" for reproducing nix builds (rootless podman)

Status: deliverable for task `rb-nix-minimum-hermit-dose` (2026-07-30).
Author: impl agent (opus-4.8). Builds on the validated nix-under-hermit container
recipe and the random-leaks / getpid findings.

## TL;DR

For a small nix build run **serially** (`nix -j1 --cores 1`) under hermit in the
validated rootless-podman `nixos/nix` path, the **minimum dose that robustly (N=3)
reproduces byte-identical output is plain `--no-namespace`** (hermit's default
deterministic scheduling + I/O). It is also the **fastest** dose (~2–3 s).

Honest determinism label: **`reproducible-output-under-no-namespace`** — *not* a
strict full-sandbox victory. `--no-namespace` delegates isolation to podman
("not a sandbox," per hermit's own warning); hermit only *determinizes*. The
output is byte-identical run-to-run (what reproducible-builds needs), but the
sandbox guarantee is podman's, not hermit's.

**Surprise:** `--no-sequentialize-threads` — the relaxation the owner expected to
be the perf win — is a ~5× **pessimization** for serial builds (13 s vs 2–3 s),
because disabling sequentialization forces the RCB/PMU-preemption path (counting +
single-step skid correction) that a `-j1` build doesn't need. Sequentialization is
nearly free when there's one build thread.

## Evidence (dose matrix, N=3 each; `experiments/rb_nix_minimum_hermit_dose_20260730/results.csv`)

| Dose (all rootless via podman) | Reproducible | wall |
|--------------------------------|--------------|------|
| `native` (no hermit) — control | **NO** (3 distinct hashes) | 10–11 s |
| `--no-namespace --strict --sequentialize-threads` | YES | 2–3 s |
| `--no-namespace --strict` | YES | 2–3 s |
| **`--no-namespace`** (minimum) | **YES** | **2–3 s** |
| `--no-namespace --no-sequentialize-threads` | YES | 13–14 s |
| `--no-namespace --no-sequentialize-threads --no-rcb-time --no-deterministic-io` | YES | 13 s |

- Native produces **3 different** output hashes → the probe genuinely captures
  nondeterminism. Every hermit dose produces an **identical** hash across its 3
  runs (each `podman run --rm` has a fresh `/nix/store`, so identical == genuine
  determinization, not caching).
- Method: a real nix derivation whose builder emits hermit-determinized entropy
  via **bash builtins only** (nix build sandboxes clear PATH) — two `read`s from
  `/proc/sys/kernel/random/uuid` + `read /proc/uptime`; witness = `sha256` of the
  built output. `$RANDOM` excluded (getpid-leak under `--no-namespace`).
- Setup: devbig014, podman 5.8.3 rootless, `nixos/nix:2.3.16` (pre-pidfd, avoids
  the `pidfd_send_signal` `--strict` blocker), hermit release binary ~main
  `0321a015`-era, reverie `4cee948e`.

## Recommendation (dose policy)

1. **Default/minimum dose for serial nix builds: `--no-namespace`** — reproduces,
   fastest, simplest. Serialize nix itself (`--cores 1 --max-jobs 1`) to shrink
   the concurrency hermit would otherwise have to determinize.
2. **Add `--strict` for a free safety margin** when the build's syscalls are all
   supported: same speed here, and it fails *closed* on unsupported syscalls
   instead of silently forwarding a potential nondeterminism leak. Drop it for
   builds that need an unsupported syscall (e.g. modern nix's `pidfd_send_signal`).
3. **Do NOT add `--no-sequentialize-threads` for serial builds** — it's pure RCB
   overhead (~5× slower) with no reproducibility benefit. It (and its perf
   tradeoff) only becomes relevant for genuinely multi-threaded builds where
   full sequentialization would collapse parallelism — that regime is *not*
   exercised here and should be measured separately before relying on the RCB
   path (which depends on PMU and is skid-prone).
4. **Label honestly.** This is determinized output under `--no-namespace`, not a
   strict full-namespace sandbox result. Reserve "strict" claims for
   full-namespace runs.

## Per-build dose manifest (proposal: `hermit-dose/v1`)

Sample: `experiments/rb_nix_minimum_hermit_dose_20260730/manifest.example.toml`.
One `[[build]]` per build records the minimum `dose`, an honest `determinism`
label, `strict`/`sequentialize` choices, the `witness_sha256`, and
`runs_verified`. An outer "minimum-dose finder" (start heavy, relax while the
witness stays stable across N runs) fills it in; the reproducer just replays
`dose`. Defaults capture the shared container path so per-build entries stay small.

```toml
schema = "hermit-dose/v1"
[defaults]
runner    = "…/recipe/nix-under-hermit.sh"
image     = "docker.io/nixos/nix:2.3.16"
nix_flags = ["--cores","1","--max-jobs","1"]
seccomp   = "unconfined"
[[build]]
attr           = "hermit-dose-probe"
dose           = ["--no-namespace"]
determinism    = "reproducible-output-under-no-namespace"
strict         = false
sequentialize  = "default"       # --no-sequentialize-threads is SLOWER for serial builds
witness_sha256 = "0a4943cfb295…"
runs_verified  = 3
```

## Limits / future work
- One synthetic-but-real nix derivation (serial). Extending to real packages
  (`hello`, etc.) and to genuinely multi-threaded builds (where the
  sequentialize-vs-RCB tradeoff flips) is the next step; the RCB path should be
  robustness-checked (PMU skid) before trusting `--no-sequentialize-threads`.
- `--no-namespace` is required by the rootless path and caps the determinism
  label; full-namespace hermit (deterministic getpid, true sandbox) is a separate
  regime blocked here by rootless mount/UTS caps (see the container recipe).

## References
- Experiment: `experiments/rb_nix_minimum_hermit_dose_20260730/`
  (`README.md`, `metadata.json`, `results.csv`, `dose-run.sh`, `dose-matrix.sh`,
  `manifest.example.toml`).
- Container recipe: `experiments/nix-hermit-container-approach_20260730/recipe/`.
- Related memory: nix-under-hermit-rootless-podman;
  atrandom-uuid-already-determinized-real-leak-getpid.
