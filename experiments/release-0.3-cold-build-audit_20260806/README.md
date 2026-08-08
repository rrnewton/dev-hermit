# Cold standalone default-build audit for the tree that would become Hermit 0.3

**Date:** 2026-08-06 (UTC 2026-08-07) · **Task:** `release-0.3-audit-cold-build` · **Agent:** hermit-w3

## Question

Does a *true cold* clone of Hermit build and run on its **default** (first-party) configuration —
no warm Cargo registry, no warm git checkouts, no warm `target/` — and are the third-party backends
genuinely optional? Warm primary builds hide missing-submodule and cargo-git dependency failures.

## Premise correction, stated first

**There is no Hermit 0.3.** At audit time `git tag -l '*0.3*'` matched nothing, no `*0.3*` release
branch existed (only dependabot branches matched the glob), and `hermit-cli/Cargo.toml` reads
`version = "0.2.0"`. This audit therefore covers **the current `origin/main` tree that would become
0.3**, not a 0.3 artifact. If 0.3 is meant to be a specific unpushed branch or tag, this audit is
against the wrong tree and must be re-run.

## Exact subject

| field | value |
| --- | --- |
| repo | `https://github.com/rrnewton/hermit.git` |
| branch | `main` (origin default at audit time) |
| **SHA** | **`1fadc03779f2a246a9b5af5d4a93533511c837df`** |
| binary self-report | `hermit 0.2.0 (2026-08-07, g1fadc03779f2)` |
| submodule `agent-utils` | `a6f4232f849271af4c65585f9861769912411222` |
| submodule `third-party/rr` | `39e5c18e7e43236b7ca0fb1eb647fe9c93e3934e` |
| slot | none — throwaway clone under `ignored/`; the audit mutates nothing tracked |

The SHA moved during the audit: an earlier fetch showed `4c70658e…`, but the fresh clone's default
HEAD was one commit ahead (`1fadc037…`, *"backend-parity: emit the observed value in the two fixtures
that stayed blind"*). The freshly cloned origin default is the authority, so the newer tip was
audited.

## Method

Isolation actually used, and its one gap, stated rather than implied:

- clone into a throwaway gitignored path; **not shallow**, 18.76 MiB pack
- `CARGO_HOME` pointed at a directory created empty (0 entries verified before the build)
- `CARGO_TARGET_DIR` left at the default *inside the throwaway clone*
- **`RUSTUP_HOME` was NOT isolated.** The nightly toolchain is treated as a host prerequisite rather
  than a project dependency, so "cold" here means cold *Cargo*, not cold *rustup*. A build on a host
  with no nightly toolchain is not covered by this audit.
- resource accounting via `/usr/bin/time -v`; no cgroup limits (an agent sandbox cannot create its
  own cgroup on this host)

## Results

All commands, exit codes, and measurements: `results.csv`. Raw logs: `*.log`.

| step | rc | wall | CPU (u+s) | maxRSS |
| --- | --- | --- | --- | --- |
| clone | 0 | 1.72 s | 2.02 + 0.35 s | 22 MB |
| submodule init (`--init --recursive`) | 0 | 5.22 s | 4.26 + 0.97 s | 15 MB |
| **`cargo build --locked -p hermit`** (default features) | **0** | **51.08 s** | 184.44 + 40.20 s | 1.44 GB |
| `cargo build --locked -p hermit --features third-party-backends` | 0 | 34.58 s | 123.48 + 22.54 s | 1.37 GB |

Host: 316 cores, 1-minute load average 88.87 during the default build — wall time is not a
single-tenant number and should not be quoted as one.

**Coldness is evidenced, not assumed:** the default build performed **202 `Downloaded`** and **217
`Compiling`** lines, and fetched three cargo-git dependencies:
`rrnewton/reverie`, `facebookexperimental/rust-shed`, `rrnewton/liteinst2`.

**Zero warnings** in both builds (`grep -c '^warning'` = 0).

Runtime, with `LD_LIBRARY_PATH` / `LIBRARY_PATH` / `PKG_CONFIG_PATH` explicitly **unset** — the cold
default binary needs no environment fixups:

- `hermit run --strict -- /bin/echo hermit-0.3-cold-audit` → rc=0, correct output
- `hermit run --strict --verify -- /bin/echo cold-audit` → **588 | 588 DETLOG messages compared,
  "Success: deterministic"**, rc=0

Third-party backends are genuinely optional, and refuse *cleanly* rather than crashing:

```
dbi      rc=1  Error: backend `dbi` is unavailable: DBI support was not included in this build
sabre    rc=1  Error: backend `sabre` is unavailable: SaBRe support was not included in this build
e9patch  rc=1  Error: backend `e9patch` is unavailable: e9patch support was not included in this build
```

## Findings

1. **The default first-party cold build passes every criterion.** Clean clone → submodule init →
   `cargo build` → real strict run → determinism verify, with zero warnings and no environment
   fixups. `hermit-cli/Cargo.toml` declares `default = []`, and the runtime behaviour matches.

2. **`make` — the documented default entry point — contradicts that.** `Makefile` sets
   `.DEFAULT_GOAL := build`, and `build:` runs
   `cargo build --locked -p hermit --features third-party-backends`. So a plain `make` on a fresh
   clone *requires* all three third-party backends even though the crate's default feature set is
   empty. A release described as "third-party backends optional" is only true of the cargo path,
   not of the command a new user is most likely to run.

3. **A plain `make` can install system packages.** `build:` depends on `install-deps`, which sets
   `INSTALL_BUILD_TOOLS := 1`, and the tool-check target runs `sudo -n apt-get install` /
   `dnf install` for missing `cmake`/`build-essential`. **This audit deliberately did not run
   `make`** — auto-installing packages on a shared multi-tenant host is a side effect an audit must
   not cause. The third-party configuration was instead compiled directly, which is why finding 4
   is qualified.

4. **The third-party compile number is NOT independently cold.** It ran after the default build in
   the same `CARGO_HOME`, so its dependencies were already downloaded. Read 34.58 s as *incremental
   third-party compile*, not as *cold third-party build*. An independently cold measurement needs a
   second empty `CARGO_HOME`.

5. **The default build still requires reaching three git forges.** `rrnewton/reverie`,
   `facebookexperimental/rust-shed`, and `rrnewton/liteinst2` are fetched by the *default* build.
   Notably `liteinst2` is pulled even though liteinst is not among the default features, so an
   air-gapped or egress-restricted default build is not currently possible. Worth deciding
   deliberately before a release rather than discovering it downstream.

## Reproduction

```bash
git clone https://github.com/rrnewton/hermit.git hermit && cd hermit
git checkout 1fadc03779f2a246a9b5af5d4a93533511c837df
with-proxy git submodule update --init --recursive
export CARGO_HOME="$PWD/../cold-cargo-home"      # must not already exist
with-proxy cargo build --locked -p hermit        # default features
env -u LD_LIBRARY_PATH ./target/debug/hermit run --strict --verify -- /bin/echo cold-audit
```

Egress on this host requires the `with-proxy` wrapper; without it the crates.io index update and
the three git fetches fail.
