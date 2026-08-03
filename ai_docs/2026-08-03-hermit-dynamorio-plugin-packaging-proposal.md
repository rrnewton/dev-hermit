# Hermit DynamoRIO plugin packaging proposal

- **Date:** 2026-08-03
- **Task:** `hermit-plugin-packaging-proposal-dynamorio`
- **Status:** proposal only; no package names have been published and no runtime
  code has been changed

## Decision

Ship the flagship Cargo package as `hermit-run`, installing a `hermit` binary
whose built-in execution paths are ptrace, LiteInst, and KVM. Keep the DBT
command-line, configuration, validation, coordinator, and launch-policy glue in
that binary, but remove DynamoRIO, `reverie-dbi`, the DBT native client, and the
DBT Detcore shared object from its dependency and installation closure.

Ship one additional Cargo package, `hermit-dynamorio`, which installs a helper
executable named `hermit-dynamorio`. For the 0.2 Linux x86-64 target, its build
script compiles the vendored, pinned DynamoRIO source in `OUT_DIR`, strips and
packs the required runtime files with the DBT Detcore tool, and embeds that
compressed archive in the helper. The helper owns validation and atomic
materialization of:

- the pinned DynamoRIO runtime, including `drrun` and its required libraries;
- the native Reverie DBT client;
- the DBT Detcore tool as a shared object; and
- their licenses, hashes, build provenance, and compatibility manifest.

The boundary is therefore **not the Hermit Rust crate**. Backend-specific code
may remain in the static executable. The boundary is the third-party dependency
and runtime payload. This is materially smaller than extracting every backend
adapter into a new Rust crate or designing a stable Rust plugin ABI.

Use an executable control protocol between `hermit` and `hermit-dynamorio`, not
`dlopen` of arbitrary Rust code into the flagship process. The core process
continues to own Hermit's CLI contract and the coordinator-side Detcore state.
The helper locates and validates the payload and returns an exact artifact
manifest; the existing DBT launch glue then starts the pinned `drrun` and native
client from those paths.

## User contract

The successful installation path is:

```text
cargo install hermit-run
cargo install hermit-dynamorio
hermit --backend dbt run -- PROGRAM ARGS...
```

No feature flag, environment override, manual copy, or separate activation
command is required. The canonical backend spelling is `dbt`; an existing
`dbi` spelling may remain as a deprecated alias during migration, but must
resolve through the same plugin and compatibility checks.

Without the plugin, Hermit performs no guest setup and exits with status 69
(`EX_UNAVAILABLE`). The exact diagnostic is:

```text
error: backend 'dbt' is unavailable: hermit-dynamorio was not found
install it with:
  cargo install hermit-dynamorio
searched:
  <resolved HERMIT_DIR>/bin/hermit-dynamorio
  <resolved CARGO_HOME>/bin/hermit-dynamorio
  PATH
```

The angle-bracketed fields are replaced with resolved absolute paths in actual
output; they are not printed as literal environment-variable references.

This is deliberately fail-closed and actionable. It must never silently use
ptrace, silently omit DBT instrumentation, or report that DBT ran when only a
preprocessor or fallback ran. Every plugin-absent, incomplete, corrupt, and
incompatible path follows the same rule: stop before guest execution, state the
failed invariant, and give the specific repair command.

## End-user Hermit root

Resolve the Hermit root once:

```text
HERMIT_DIR=${HERMIT_DIR:-$HOME/.hermit}
```

All end-user installation, runtime, and global-configuration discovery uses that
resolved path. The proposed layout is:

```text
$HERMIT_DIR/
  config.toml                        # optional future user-global configuration
  bin/
    hermit-dynamorio                 # optional explicit/root-local install
  plugins/
    dynamorio/
      current -> releases/0.2.0/<target>/<abi-tag>/<build-id>/
      releases/0.2.0/<target>/<abi-tag>/<build-id>/
        plugin.json
        bin/drrun
        lib/libdetcore_dbt.so
        lib/libreverie_dbt_client.so
        lib/dynamorio/...
        licenses/...
  recordings -> <cache recording directory>
```

The existing recording store may remain under
`${XDG_CACHE_HOME:-$HOME/.cache}/hermit/recordings`. Hermit creates
`$HERMIT_DIR/recordings` as a symlink to it. With the default root this is the
required `~/.hermit/recordings` entry. Creation is idempotent and never
overwrites an existing non-symlink or a symlink to a different location; such a
conflict fails with the two paths and a repair instruction.

This root belongs to people who install and run Hermit. Developer build output,
including validation ledgers and logs, must never appear here. A user who never
checks out the repository or runs its developer validation must not acquire
developer validation artifacts beneath `$HERMIT_DIR`.

The flagship package still has a first-party LiteInst runtime requirement in
today's architecture. Calling the product a single static executable must not
hide that fact: either the first-party LiteInst DSO remains part of the core
Hermit distribution under this root, or a separate effort embeds and
materializes it. The DynamoRIO split does not classify that Hermit-owned runtime
as a third-party plugin.

## Developer validation state

Validation has a disjoint audience and lifetime. `$HERMIT_DIR` is what a user
who installed Hermit has; `$DEV_HERMIT_PARENT` is what a developer working on
Hermit has. A user never runs developer validation. Every primary and worktree
validation writes to the existing developer parent root:

```text
$DEV_HERMIT_PARENT/ignored/
  validate-run-ledger.jsonl          # the only durable ledger write target
  validate-runs/<run-id>/validate.log
  validate-run-global.jsonl          # rebuildable aggregate, never a source
```

`DEV_HERMIT_PARENT` is required for developer validation and must name a valid
developer parent repository. Missing or invalid configuration is an actionable
error before validation begins, never permission to run without recording.
There is no fallback write destination. The two environment variables remain
separate because they serve separate audiences; neither is inferred from or
redirected into the other.

This replaces the current split controlled by `DEV_HERMIT_PARENT`: the primary
ledger under `<parent>/ignored`, per-worktree `ignored` ledgers, ad hoc ledgers
under both `$TMPDIR` and `/tmp`, and a separate global output under the parent.
That design already loses direct evidence. `aggregate.py` states that an unset
`DEV_HERMIT_PARENT` skips append entirely, so it reconstructs otherwise missing
runs from temporary logs; in the reported 112-record sample, 23 records were
reconstructions rather than recorded ledger rows. Scattered write destinations,
not multiple audiences, caused the loss.

After migration, temporary directories and per-worktree ledgers are never
durable sources. `validate.sh` creates its run directory under the canonical
developer root, writes a start event before executing any gate, streams the raw
log there, and writes a terminal event from its exit trap. A missing terminal
event truthfully means an interrupted run; it is not reconstructed into a
guessed result. Existing files from legacy path shapes are imported once with
their original source and a `reconstructed` marker. Future aggregation reads
only the one canonical ledger for that developer workspace.

## Discovery

When `--backend dbt` is selected, search for the helper in this order:

1. `$HERMIT_DIR/bin/hermit-dynamorio`;
2. `${CARGO_HOME:-$HOME/.cargo}/bin/hermit-dynamorio`; then
3. `hermit-dynamorio` on `PATH`.

The explicit Hermit root wins, followed by Cargo's actual default install
location. `PATH` is the compatibility fallback, not the source of payload
paths. Hermit never searches `PATH` independently for `drrun` or a `.so`.
Those paths come only from a validated plugin manifest rooted under
`$HERMIT_DIR/plugins/dynamorio`.

If two helpers exist, Hermit reports the selected absolute path at debug level.
An incompatible helper does not cause search to continue to a different one;
that would make behavior depend on installation order and could conceal a
broken explicit installation. It fails and tells the user which helper was
selected.

## What `cargo install` can actually install

**`cargo install` is the source-install channel.** The recommended design builds
DynamoRIO and the DBT payload from the source carried by the Cargo package. It
does not fetch a prebuilt payload at install time or runtime. Users choosing
this channel need the declared native build prerequisites; users who want a
prebuilt installation belong on a separate binary-package channel.

Cargo has no `data_files` installation mechanism. Its
[`cargo install` documentation](https://doc.rust-lang.org/cargo/commands/cargo-install.html#description)
says that only packages with executable targets can be installed and that all
installed executables go in the installation root's `bin` directory. It does
not copy arbitrary package resources beside them. crates.io also has a
[10 MB `.crate` limit](https://doc.rust-lang.org/cargo/reference/publishing.html#packaging-a-crate),
which the source package must satisfy.

Measurements on 2026-08-03 distinguish the developer install tree from the
runtime payload that `hermit-install/build.rs` already selects:

| Artifact or operation | Measured result | Basis |
| --- | ---: | --- |
| Vendored DynamoRIO source | 128,694,015 apparent bytes | Pinned source tree including submodules |
| Vendored source as gzip tar | 34,658,452 bytes | Same tree; exceeds crates.io limit |
| Curated, build-proven source subset | 24,686,088 apparent bytes | Core, tools, API, build support, licenses, and only the five required extension directories |
| Curated subset as deterministic gzip tar | 5,234,435 bytes | Clean configure, build, install, and DBT-client smoke test passed |
| Full current CMake install | 511,470,114 bytes | Clients, extensions, and tools enabled |
| Full install as gzip tar | 142,467,790 bytes | Existing equivalent release install |
| Minimal viable CMake install | 155,312,369 bytes | Clients off, extensions and tools on; includes build-only files |

Uncompressed source and runtime figures use apparent byte size, not allocated
blocks from `du`; btrfs compression therefore does not understate what a crate,
embedded payload, or network transfer must carry. Compressed tar figures are the
actual regular-file byte lengths. The minimal runtime was measured file by file
with `stat`:

| Runtime file | Unstripped | `strip --strip-unneeded` |
| --- | ---: | ---: |
| `bin64/drrun` | 737,680 | 699,896 |
| `lib64/release/libdynamorio.so` | 2,106,832 | 2,069,816 |
| `lib64/release/libdrpreload.so` | 43,696 | 42,728 |
| `ext/lib64/release/libdrx.so` | 78,600 | 68,608 |
| `ext/lib64/release/libdrmgr.so` | 88,000 | 76,320 |
| `ext/lib64/release/libdrreg.so` | 58,040 | 51,632 |
| `ext/lib64/release/libdrwrap.so` | 58,656 | 51,648 |
| **DynamoRIO runtime subtotal** | **3,171,504** | **3,060,648** |
| `libdetcore_dbi.so` | 7,405,576 | 5,452,128 |
| `libreverie_dbi_client.so` | 62,904 | 52,312 |
| **Runtime total** | **10,639,984** | **8,565,088** |
| Runtime plus required licenses | 10,685,488 | 8,610,592 |
| Same set as gzip tar | 3,542,511 | 3,228,825 |

The complete runtime bundle was not inferred from filenames: the existing
packager names the seven DynamoRIO files, and both the unstripped and stripped
bundles successfully ran `/bin/true` through `drrun` and the packaged native
client. The stripped runtime plus licenses is 8.61 MB unpacked and 3.23 MB as an
actual gzip archive.

**Correction to the earlier packaging premise:** the roughly 134 MB planning
figure was the full DynamoRIO source submodule, not a payload that would ever be
shipped or embedded. This pinned checkout measures 128,694,015 apparent bytes.
The working runtime is 10,639,984 bytes unstripped, 8,565,088 bytes stripped,
and 3,228,825 bytes as the gzip archive with required licenses. Separately, the
full developer CMake install compresses to 142,467,790 bytes because it contains
static archives, debug companions, headers, and unrelated tools; that is also
not shipping weight. Reasoning about embedding a 134 MB runtime was based on a
category error.

Clean CMake timings used pinned DynamoRIO
`929840ad9190e5086775e8debc0f0b79b4208d59`, CMake 3.31.8, GCC 11.5.0, and
an explicit 16-job cap on this 316-logical-CPU host. The current full
configuration took 5.07 seconds to configure and 44.06 seconds to build and
install, or 49.13 seconds total. The minimal viable configuration with
DynamoRIO clients disabled took 4.74 plus 14.13 seconds, or 18.87 seconds total.
Thus local compilation is measurable overhead but not the deciding problem.

Source distribution has one measured blocker that must not be hidden: the
unpruned vendored source compresses to 34.66 MB, above crates.io's 10 MB
`.crate` limit. A concrete source-level reduction is viable: a curated tree
containing top-level CMake and license files, `core`, `tools`, `api`, `libutil`,
`make`, required configuration helpers, and only `drcontainers`, `drmgr`,
`drx`, `drwrap`, and `drreg` compresses to 5,234,435 bytes. Its extension CMake
list was reduced to those five directories. From a fresh build directory it
configured, built, and installed successfully, and its `drrun` ran the existing
DBT client through `/bin/true`.

**Measured source-install cost:** the clean, build-proven subset took 14.54
seconds from configure through install. That observed cost removes the premise
that avoiding a long local DynamoRIO build justifies runtime fetching.

That 5.23 MB result proves the DynamoRIO source subset, not the complete
`hermit-dynamorio` `.crate`: Rust source, build glue, and licenses still count
toward crates.io's limit. The packaging gate remains `cargo package` below 10 MB
plus a clean source build of the exact runtime set. If the complete crate cannot
meet the limit, an explicit crates.io limit exception or a different source
registry is required; silently fetching the submodule is not an air-gapped
source install.

The build script then compiles entirely within `OUT_DIR`, strips the measured
runtime set, creates a deterministic compressed archive there, and exposes it to
the Rust compilation with `include_bytes!`. This turns Cargo's one installed
artifact, the helper executable, into the carrier for the source-built payload.

Writing the CMake outputs directly into `$HERMIT_DIR` during `cargo install` is
technically possible but rejected. Cargo's documented build-script contract says
scripts should not modify files outside
[`OUT_DIR`](https://doc.rust-lang.org/cargo/reference/build-scripts.html#outputs-of-the-build-script),
and registry install artifacts default to a
[temporary target directory](https://doc.rust-lang.org/cargo/commands/cargo-install.html#option-cargo-install---target-dir).
A local probe confirmed that Cargo does not sandbox a build script: it could
write a 16-byte marker directly to an externally supplied `$HERMIT_DIR`.
However, Cargo did not install or track that file. Such a write is
non-transactional, survives a later compilation failure, is not removed by
Cargo, and can target the wrong user's runtime root under privileged builds.
“Can write” is not “Cargo installs.”

**Recommendation: source-build in `OUT_DIR`, embed the measured 3.23 MB stripped
archive, and atomically self-extract it.** This follows Cargo's source-build and
single-executable installation model, has no runtime network or permanent
artifact-hosting requirement, works from a fully pre-staged Cargo dependency
set in an air-gapped environment, and meets “install it and it just works.” The
native prerequisites are CMake, a C/C++ toolchain, Perl, and the libraries
identified by the clean configure; `make install-deps` should expose them as a
separate DynamoRIO/source-install dependency group.

On first DBT use, the helper performs an implicit `ensure` operation:

1. derive the exact version, target, ABI tag, and build ID embedded by its own
   source build;
2. use an already-materialized exact payload when every check passes;
3. take a per-payload extraction lock;
4. unpack the embedded archive into a sibling temporary directory beneath
   `$HERMIT_DIR/plugins/dynamorio/releases`;
5. verify file type, permissions, hashes, provenance, ABI descriptors, and
   required paths;
6. atomically rename the complete directory to its content-addressed release
   path; and
7. update `current` last with an atomic symlink replacement.

Concurrent first runs either perform the extraction under the lock or wait and
validate the winner's completed directory. The helper never trusts `current` by
pathname alone: it compares the selected manifest and file hashes with the exact
version, target, ABI tag, and build ID embedded by its source build. Any identity
or hash mismatch invalidates `current` for that invocation. An upgrade therefore
selects a new content-addressed path, validates it completely, and only then
atomically replaces the symlink; it never overwrites or executes the older
extraction. Old versions remain available for explicit garbage collection.

A read-only or full `$HERMIT_DIR` is the remaining first-use failure. Hermit
exits 73 (`EX_CANTCREAT`) before guest execution with the resolved path and an
actionable remedy:

```text
error: cannot materialize backend 'dbt' payload under <resolved HERMIT_DIR>: <os error>
repair: set HERMIT_DIR to a writable directory or have an administrator materialize this exact hermit-dynamorio version
```

There is no network path and no normal setup command. After `cargo install
hermit-dynamorio`, the first `hermit --backend dbt run` transparently extracts
the embedded payload and runs; subsequent invocations only validate and reuse
it.

Prebuilt distribution is separate future work, not part of the Cargo design.
An apt, dnf, Homebrew, Conda, or release-artifact installer may later install
the same versioned payload and helper without compiling, while preserving the
same ABI/build-ID handshake and `$HERMIT_DIR` layout. That channel is additive
and does not gate the source package.

## Detcore compatibility handshake

There are intentionally two Detcore copies:

- coordinator-side Detcore compiled into the flagship `hermit`; and
- guest/DBT-side Detcore in `libdetcore_dbt.so`.

Presence is not compatibility. Before `drrun` starts, the host and plugin must
complete all of these checks:

1. **Control protocol:** the helper and host agree on a small versioned JSON
   protocol supplied by a dependency-light, first-party
   `hermit-plugin-protocol` crate. Unknown major versions fail.
2. **Exact release:** for the 0.2 line, the plugin's Hermit package version must
   exactly equal the host `CARGO_PKG_VERSION`. A semver range is not accepted.
3. **Exact Detcore ABI tag:** both sides embed the same opaque ABI tag and must
   compare equal. The tag covers the coordinator RPC wire schema, serialized
   Detcore configuration, native DBT callback table, shared tool/thread-state
   contract, Reverie pin, and target ABI. It is not manually chosen and is not
   inferred from semver.
4. **Payload identity:** the manifest's hashes must match the files on disk.
5. **Shared-object truth:** `libdetcore_dbt.so` exports a fixed C descriptor
   symbol such as `hermit_detcore_plugin_descriptor_v1`. The helper reads that
   descriptor and verifies that its version and ABI tag match the manifest.
   The native client repeats the comparison before invoking any Detcore
   callback, so a stale or substituted `.so` cannot pass merely because
   `plugin.json` looks current.

Hashes prove that installed bytes match a manifest; they do **not** prove that
the manifest truthfully describes the source used to build those bytes. A
mislabelled bundle could otherwise carry a matching version and ABI tag. Close
that gap with an exact `detcore_build_id`, generated independently from the
actual build inputs rather than supplied as release metadata. Its canonical
input set includes the Detcore and plugin-protocol source-tree hashes,
`Cargo.lock`, enabled features, generated RPC/schema and callback descriptors,
Reverie revision, target triple, compiler identity, and code-generation flags.

Both the static coordinator and `libdetcore_dbt.so` embed the build ID computed
by their own build. For the Cargo source channel, the registry checksum
authenticates the source package and the build script generates a build record
binding the package version, ABI tag, build ID, source revisions, lockfile
digest, target, and SHA-256 of every output. The helper verifies the embedded
record and artifact hashes, reads the build ID from the actual `.so` descriptor,
and compares it with both the record and host. The native client repeats the
descriptor-to-host comparison. An operator cannot make mismatched code
compatible by copying labels into `plugin.json`. A future prebuilt binary
channel must add signed provenance binding the same inputs and outputs.

These invariants are mechanical gates, not documentation promises:

| Invariant | Mechanical check | Failure |
| --- | --- | --- |
| Helper protocol is understood | Parse and compare protocol major | Exit 78 before payload use |
| Host and plugin are one release | Exact runtime comparison of `CARGO_PKG_VERSION` | Exit 78 |
| Detcore interfaces agree | Exact runtime comparison of generated ABI tags | Exit 78 |
| Detcore implementations correspond | Exact comparison of independently generated build IDs embedded in host and `.so` | Exit 78 |
| Build record describes the source build | Compare generated input identity, build ID, descriptors, and output hashes | Reject extraction or exit 78 |
| Installed payload is the released payload | Hash every file before activation and on probe | Reject installation or exit 78 |
| `.so` agrees with its manifest | Read exported descriptor in both helper and native client | Exit 78 |
| Core excludes third-party DBT dependencies | CI inspects the packaged `cargo tree` and archive contents | Block publication |
| Absence is actionable | Clean-home integration test asserts exact stderr, exit 69, and no guest start | Block publication |
| Installation needs no activation | Clean-home test installs both crates and runs DBT without extra configuration | Block publication |
| Embedded distribution fits crates.io | `cargo package` verifies the complete `.crate` is below 10 MB | Block publication |
| First use is offline | Clean-home integration test disables network and runs DBT after `cargo install` | Block publication |
| Extraction and upgrade are atomic | Concurrent first-use, interrupted-extraction, and stale-upgrade tests expose only the exact complete hashed payload | Block publication |
| Recordings remain discoverable | Filesystem test checks `$HERMIT_DIR/recordings` resolves to the configured cache | Block publication |
| End-user state excludes developer artifacts | Install and developer-validation tests assert no validation output is written beneath `$HERMIT_DIR` | Block publication or developer-tooling change |

For 0.2 the policy is **exact package version plus exact ABI tag plus exact
Detcore build ID**. Git SHAs and build dates are printed for diagnosis, but do
not substitute for these generated identities. Semver compatibility may be
considered only after the plugin boundary has an intentionally stable ABI and
cross-version tests; it must not be assumed for internal Detcore structures
today.

On a mismatch Hermit exits with status 78 (`EX_CONFIG`) before guest execution:

```text
error: incompatible hermit-dynamorio plugin; refusing backend 'dbt'
host:   hermit-run 0.2.0, detcore ABI hdt1:<host-tag>, build <host-build-id>
plugin: hermit-dynamorio 0.2.0, detcore ABI hdt1:<plugin-tag>, build <plugin-build-id>
selected plugin: <absolute helper path>
repair with:
  cargo install --force --locked hermit-dynamorio@=0.2.0
```

Missing descriptors, malformed responses, timeouts, corrupt files, and helper
crashes are incompatibility/configuration failures, not permission to continue.
Diagnostics distinguish them, but all stop before the guest runs.

## Protocol and execution flow

The normal flow is:

```text
user
  |
  v
hermit --backend dbt
  | resolve helper
  | send host version + ABI tag + target + HERMIT_DIR
  v
hermit-dynamorio __hermit_plugin_probe_v1
  | atomically ensure payload
  | verify provenance, hashes, and libdetcore_dbt.so descriptor
  | return versioned manifest with absolute payload paths
  v
hermit
  | compare exact version + ABI tag + Detcore build ID
  | create coordinator state
  | launch only the returned drrun/client/tool paths
  v
DynamoRIO client
  | repeat .so descriptor check
  | connect to coordinator
  v
guest execution
```

The private helper subcommand is not a human configuration step. Its response
has a size limit and timeout, uses a closed schema, and contains no arbitrary
shell fragments. Paths are canonicalized and required to remain beneath the
selected versioned payload directory.

## Designing for N plugins while shipping one

The core keeps a static table of external backend specifications:

```text
backend id | helper executable | Cargo remedy | protocol major
dbt        | hermit-dynamorio   | cargo install hermit-dynamorio | 1
```

Discovery, diagnostics, manifest validation, atomic materialization, and
version checks are generic. Backend-specific launch policy remains ordinary
compiled Rust code in `hermit`; this proposal does not impose one universal
runtime ABI on unrelated instrumentation systems.

SaBRe may later use the same packaging mechanism with `hermit-sabre`, its own
payload descriptor, and an ABI tag covering its actual RPC/plugin boundary.
That is optional for 0.2. e9patch is explicitly excluded from 0.2 and must not
be listed as an installable plugin, placeholder, or silent fallback. Adding it
later requires a truthful production architecture and its own acceptance
evidence, not only a package bearing the name.

## Why feature flags were insufficient

The current feature-gate approach improves the default build but does not
create a product boundary. Optional dependency metadata, a combined installer,
combined resources, and CI artifact ownership still couple the flagship and
third-party backend.

The proposed split requires all of the following even though much of the Rust
dispatch code stays in the core binary:

- remove DBT/DynamoRIO dependencies and build scripts from `hermit-run`'s
  publication graph;
- make `hermit-dynamorio` own its embedded payload, licenses, provenance,
  hashes, and target support;
- split the combined install artifact into a core layout and a versioned DBT
  payload;
- replace feature-dependent availability with runtime helper discovery and a
  fail-closed handshake;
- build and publish core and plugin artifacts independently; and
- run positive exact-pair and negative skew tests in CI.

Feature flags can remain developer conveniences for workspace builds, but they
are not the installation or compatibility contract.

## Acceptance gates

The packaging change is not complete until CI proves each of these at exact
release artifacts:

1. `cargo tree -p hermit-run` contains no DynamoRIO, `reverie-dbi`, DBT tool,
   or DBT installer dependency.
2. `cargo package -p hermit-dynamorio` stays below crates.io's 10 MB limit and
   contains the minimal vendored source, licenses, and build-record generator;
   a clean install builds the measured runtime without fetching another source.
3. A clean `cargo install hermit-run` produces the flagship `hermit` and its
   ptrace, LiteInst, and KVM paths retain their existing tests.
4. On a clean home, `hermit --backend dbt run -- /bin/true` emits the exact
   absent-plugin diagnostic and exits 69 without starting the guest.
5. After `cargo install hermit-dynamorio`, the same command automatically
   materializes the source-built embedded payload and runs without another user
   step or network access.
6. A host/plugin exact-version, ABI-tag, or build-ID mismatch fails with the
   exact mismatch diagnostic and exits 78.
7. A plugin built from a different Detcore revision is rejected even if its
   manifest is edited to copy the host's version and ABI tag.
8. A forged manifest, replaced `.so`, missing descriptor, truncated embedded
   archive, missing `drrun`, and helper timeout each fail before guest execution.
9. The DBT native client independently refuses an ABI-tag or build-ID mismatch
   even when a manifest is edited to claim compatibility.
10. An interrupted or concurrent extraction leaves the prior `current` payload
    usable and never exposes the partial replacement; an upgrade with a different
    version, target, ABI tag, or build ID cannot reuse the stale extraction.
11. `$HERMIT_DIR` relocation works without consulting unrelated host paths, the
    recordings symlink resolves to the actual cache store, and neither package
    installation nor developer validation writes developer artifacts there.
12. Validation runs from the primary checkout and two worktrees append to
    `$DEV_HERMIT_PARENT/ignored/validate-run-ledger.jsonl`; an
    interrupted run leaves an explicit start event and durable log, with no
    `/tmp` reconstruction.
13. Strict and verify coverage runs against the packaged DBT path, not a
    workspace-relative `target/` tree.

## Rollout sequence

1. Define the dependency-light plugin protocol, generated Detcore ABI and build
   identities, source-build record, and exported shared-object descriptor.
2. Produce the crates.io-sized minimal source distribution and deterministic
   source-build pipeline that strips and embeds the runtime-pruned payload in
   `hermit-dynamorio`.
3. Change the compiled DBT adapter to discover the helper and consume only its
   validated manifest; remove third-party dependencies from `hermit-run`.
4. Add absent, mismatch, corruption, interruption, and packaged end-to-end CI
   gates before publishing either crate.
5. Publish the exact-version pair together. Do not publish a host version until
   its matching plugin artifact is available.

This sequence makes absence and skew explicit states. A component that is
missing or incompatible cannot appear to work, which is the required inverse
of silent no-op flags, non-enforcing checks, and present-but-disconnected
infrastructure.
