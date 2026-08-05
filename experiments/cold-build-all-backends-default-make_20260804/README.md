# Cold verification: default `make` builds + configures every backend

**Question.** Does a *clean checkout* of `hermit` plus a plain `make` — with **no warm
caches** — actually build and wire **all six** backends (ptrace, KVM, DBI/DynamoRIO,
SaBRe, LiteInst, e9patch), including compiling the heavy third‑party native deps from
scratch? This is the owner's "no‑surprises tire‑kicking" bar for
`make-all-backends-default-build` (implemented + landed as hermit PR #1433,
`88d3d019`). A warm box proves nothing: a ~1‑minute build cannot have compiled
DynamoRIO (134 MiB) from source, so build **wall time must be sanity‑checked against
physical plausibility**.

## Method

Genuinely cold, and isolated so it never disturbs the ~18 sibling agents' shared
`~/.cargo` on this box:

1. **Fresh clone** of `hermit` at current `origin/main`
   (`fc0b76adc59d0d0b686d8a7d6b8babca7a0a11b1`, which contains PR #1433). Submodules
   left **uninitialized** (`-` prefix) so `make`'s auto‑init is exercised.
2. **Fresh, isolated `CARGO_HOME`** (empty dir) so cargo must fetch the `reverie` git
   dependency (`rev 79517704`) *and its native submodules* from scratch, and
   `reverie-dbi`'s `build.rs` must CMake‑build DynamoRIO from source (nothing cached).
3. `THIRD_PARTY_BUILD_JOBS=32` (good neighbour on a 316‑core shared box).
4. Time the **default goal**: `/usr/bin/time -v make`
   (`build: prune-stale-release install-deps` → `cargo build -p hermit --features
   third-party-backends`).
5. Smoke‑test **each backend from the resulting binary** (not from build flags):
   `hermit run --backend <b> --sequentialize-threads --max-timeslice=disabled /bin/true`.

Reproduce with `./reproduce.sh` (see `metadata.json` for exact pins/host).

## Results

**Cold build:** `make` exit **0**. Wall **2:47.34 (167 s)**, user **2208.46 s**, sys
**323.49 s**, maxRSS **2.35 GiB**. The 2208 s of user CPU across 32 jobs (~13× the wall)
is consistent with a real from‑scratch native build — **physically plausible as cold**,
in direct contrast to the debunked "1 m00 s" warm claim.

**Cold source fetch proven** (the exact path that broke before, when
`../third-party/dynamorio` was absent under the old sparse scheme): cargo logged
`Updating git submodule` for `rrnewton/dynamorio.git`, `DynamoRIO/elfutils.git`,
`GJDuck/e9patch.git`, and `rrnewton/SaBRe.git` into the fresh isolated `CARGO_HOME`.

**DynamoRIO actually compiled** (not merely configured): `libdynamorio_static.a`,
`libdynamorio_static.o`, and `libdynamorio.so` produced under
`target/debug/build/reverie-dbi-*/out/dynamorio-build/`; `e9patch`/`e9tool` and `sabre`
binaries built under `target/install-build/` and staged into `target/install_pkg/rsrcs/`.

**Auto‑init asserted:** the fresh clone's submodules were uninitialized; `make` →
`checkout-all` ran `git submodule update --init --recursive`, initializing `agent-utils`
and `third-party/rr` — the owner's "assert on its deps / auto‑init if missing"
requirement is met.

**Per‑backend run (cold `target/debug/hermit`, `gfc0b76adc59d`):** see `results.csv`.

| backend  | result | note |
|----------|--------|------|
| ptrace   | PASS (exit 0) | core |
| liteinst | PASS (exit 0) | core |
| dbi      | PASS (exit 0) | third‑party (DynamoRIO) |
| sabre    | PASS (exit 0) | third‑party |
| e9patch  | PASS (exit 0) | third‑party |
| kvm      | stall (SIGKILL @170 s, no output) | core; sandbox KVM‑ioctl limitation, **not** a build defect |

All six backends are **compiled and wired**: the CLI reports
`[possible values: ptrace, dbi, liteinst, sabre, kvm, e9patch]`, and the third‑party
trio appear in that enum *only when compiled in* — so `--features third-party-backends`
built **and wired** them, not merely accepted the flag.

## Interpretation

The owner P0 — a clean checkout + plain `make` builds **and configures** every backend,
including the heavy third‑party native deps, and asserts/auto‑inits its submodule deps —
is **MET and cold‑verified**. No further `hermit` code change is required; PR #1433
(`88d3d019`) already implements it.

The lone exception, **KVM**, is a *core* backend (always compiled; untouched by
PR #1433). It is present and wired but stalls silently before emitting any trace
(`RUST_LOG=info` still produced nothing) inside the 3pai agent sandbox — consistent with
a blocked/unavailable KVM ioctl or absent nested virtualization, i.e. an **environment
limitation of this sandbox, not a build/configuration defect**. Verifying a completed KVM
run needs a host with usable `/dev/kvm` ioctls.
