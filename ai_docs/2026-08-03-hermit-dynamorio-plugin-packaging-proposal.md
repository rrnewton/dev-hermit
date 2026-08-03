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
executable named `hermit-dynamorio`. The helper owns acquisition and validation
of the heavy DBT payload:

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

## One Hermit root

Resolve the Hermit root once:

```text
HERMIT_DIR=${HERMIT_DIR:-$HOME/.hermit}
```

All Hermit-owned discovery uses that resolved path. The proposed layout is:

```text
$HERMIT_DIR/
  bin/
    hermit-dynamorio                 # optional explicit/root-local install
  plugins/
    dynamorio/
      current -> releases/0.2.0/<target>/<abi-tag>/
      releases/0.2.0/<target>/<abi-tag>/
        plugin.json
        bin/drrun
        lib/libdetcore_dbt.so
        lib/libreverie_dbt_client.so
        lib/dynamorio/...
        licenses/...
  downloads/                         # content-addressed temporary/cache data
  recordings -> <cache recording directory>
```

The existing recording store may remain under
`${XDG_CACHE_HOME:-$HOME/.cache}/hermit/recordings`. Hermit creates
`$HERMIT_DIR/recordings` as a symlink to it. With the default root this is the
required `~/.hermit/recordings` entry. Creation is idempotent and never
overwrites an existing non-symlink or a symlink to a different location; such a
conflict fails with the two paths and a repair instruction.

The flagship package still has a first-party LiteInst runtime requirement in
today's architecture. Calling the product a single static executable must not
hide that fact: either the first-party LiteInst DSO remains part of the core
Hermit distribution under this root, or a separate effort embeds and
materializes it. The DynamoRIO split does not classify that Hermit-owned runtime
as a third-party plugin.

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

Cargo installs executable targets into its root's `bin` directory; it does not
install an arbitrary DynamoRIO resource tree. Embedding the full payload in the
published crate is also a poor default because the payload is large and
platform-specific.

Therefore `cargo install hermit-dynamorio` installs the small helper executable.
The Cargo package and the exact signed release bundle it names are one plugin
distribution owned by `hermit-dynamorio`; moving the large platform payload out
of the `.crate` does not transfer its versioning, provenance, or support
responsibility elsewhere.
On the first DBT invocation, the helper performs an implicit `ensure` operation:

1. derive the one allowed payload release from its own immutable package
   version and target triple;
2. use an already-materialized exact payload when it is valid;
3. otherwise download the versioned release bundle named by the helper;
4. verify the signed manifest and every declared SHA-256 before extraction;
5. unpack into a new temporary directory beneath `$HERMIT_DIR/plugins`;
6. verify file type, permissions, hashes, ABI descriptors, and required paths;
7. atomically rename the complete directory into `releases/...`; and
8. update `current` last with an atomic symlink replacement.

Interrupted downloads and partial directories are never eligible for
discovery. A site that cannot download at runtime can pre-seed the same signed
bundle under `$HERMIT_DIR`; the validation and handshake are identical. The
helper must never download `latest`, follow a mutable branch, or accept an
unhashed library from the host.

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

For 0.2 the policy is **exact package version plus exact ABI tag**. Git SHAs and
build dates are printed for diagnosis, but do not substitute for the generated
ABI tag. Semver compatibility may be considered only after the plugin boundary
has an intentionally stable ABI and cross-version tests; it must not be assumed
for internal Detcore structures today.

On a mismatch Hermit exits with status 78 (`EX_CONFIG`) before guest execution:

```text
error: incompatible hermit-dynamorio plugin; refusing backend 'dbt'
host:   hermit-run 0.2.0, detcore ABI hdt1:<host-tag>
plugin: hermit-dynamorio 0.2.0, detcore ABI hdt1:<plugin-tag>
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
  | verify manifest, hashes, and libdetcore_dbt.so descriptor
  | return versioned manifest with absolute payload paths
  v
hermit
  | compare exact version + ABI tag
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
- make `hermit-dynamorio` own its payload acquisition, licenses, provenance,
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
2. A clean `cargo install hermit-run` produces the flagship `hermit` and its
   ptrace, LiteInst, and KVM paths retain their existing tests.
3. On a clean home, `hermit --backend dbt run -- /bin/true` emits the exact
   absent-plugin diagnostic and exits 69 without starting the guest.
4. After `cargo install hermit-dynamorio`, the same command automatically
   materializes the pinned payload and runs without another user step.
5. A host/plugin exact-version mismatch fails with the exact mismatch
   diagnostic and exits 78.
6. A forged manifest, replaced `.so`, missing descriptor, truncated download,
   missing `drrun`, and helper timeout each fail before guest execution.
7. The DBT native client independently refuses an ABI-tag mismatch even when a
   manifest is edited to claim compatibility.
8. An interrupted install leaves the prior `current` payload usable and never
   exposes the partial replacement.
9. `$HERMIT_DIR` relocation works without consulting unrelated host paths, and
   the recordings symlink resolves to the actual cache store.
10. Strict and verify coverage runs against the packaged DBT path, not a
    workspace-relative `target/` tree.

## Rollout sequence

1. Define the dependency-light plugin protocol, generated Detcore ABI tag, and
   exported shared-object descriptor.
2. Produce the signed, content-addressed DynamoRIO release payload and the
   `hermit-dynamorio` helper that validates/materializes it.
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
