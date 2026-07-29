# Conditional backend distribution for `cargo install hermit`

Status: design for owner review, 2026-07-29. No implementation is proposed in
this document.

## Decision summary

Use **option D with versioned dynamic-library plugins** for heavyweight or
niche capabilities. Publish a lean `hermit` whose default install contains the
CLI, Detcore, and ptrace; keep KVM and the self-contained LiteInst path as
compile-time opt-ins. Distribute DBI/DynamoRIO, SaBRe, and e9patch separately.
The e9patch package is a preprocessor used before ptrace, not a backend.

Each plugin must instantiate the same Detcore package as a real Reverie
`Tool`; it must not reimplement deterministic scheduling or syscall policy.
Use a narrow, versioned C ABI rather than exposing Rust traits across a dynamic
link boundary. The DBI plugin may still load a Detcore client into the guest
process, as DBI requires, while its coordinator remains in the Hermit host
process.

Options A and D compose well: a separately distributed plugin can own a small
`*-sys` crate or a pinned native bundle. Keep a PATH-binary plugin as a
bootstrap/fallback, not the final architecture. Do not use B or C as the main
distribution contract.

## Goals and constraints

- A normal `cargo install hermit --locked` must not download or build
  DynamoRIO, SaBRe, or e9patch.
- Selecting a capability must obtain all of its pinned native and Rust inputs.
- The default install must be useful: ptrace remains built in.
- A named backend is real only if it executes Detcore through Reverie's
  `Tool`/`Guest` contract. In particular, e9patch remains preprocessing, and an
  installed SaBRe launcher must not be advertised as deterministic until it
  can run Detcore.
- Full source-tree validation may intentionally fetch and build every backend;
  that workflow must be explicit and separate from the end-user default.

The current Hermit manifest unconditionally depends on `detcore-dbi` and the
DBI, KVM, ptrace, LiteInst, and RPC Reverie crates. The current DynamoRIO
checkout alone is about 134 MiB. This is the coupling the design removes.

## Cargo mechanics that control the design

1. `cargo install` supports `--features`, `--all-features`, and
   `--no-default-features`. An optional dependency referenced with `dep:name`
   is included only when its feature is enabled. This is the Cargo-native lever
   for compile-time backends.
2. A correction to the task premise: Cargo **does recursively fetch submodules
   of a Git dependency**. A `.gitmodules` entry with `update = none` disables
   that fetch. The current Reverie policy therefore fetches DynamoRIO when its
   Git source is cloned, while SaBRe and e9patch are skipped. A Rust feature
   cannot conditionally change submodule traversal, so a submodule is still
   insufficient for pay-for-what-you-use installation.
3. A registry package is a `.crate` source archive, not a Git checkout. A
   submodule gitlink alone contributes no source to that archive.
4. `cargo fetch` ensures every registry and Git package in `Cargo.lock` is
   locally available and, without `--target`, fetches all target dependencies.
   It has no feature-selection flags. Treat it as cache warming, not as proof
   that an install has a minimal network graph. Validate minimality with an
   isolated-cache `cargo install` for each supported feature combination.
5. `cargo install` ignores the packaged lockfile unless `--locked` is passed.
   Release instructions and CI should use `--locked` so native pins and Rust
   dependencies are tested as one reproducible set.
6. A build script runs only after Cargo selects and compiles its package. A
   download performed by `build.rs` is invisible to `cargo fetch`, does not
   benefit from registry checksums, breaks `--offline`, and fails on docs.rs,
   where network access is blocked. Build output belongs in `OUT_DIR`.
7. crates.io limits a `.crate` archive to 10 MB. Vendoring a large native tree
   such as DynamoRIO directly into a sys crate is therefore unlikely to fit.
8. `cargo install` installs executable targets into `bin`; it does not install
   a `cdylib`. A dylib plugin needs `hermit backend install`, a distro package,
   or a small Cargo-installed installer/carrier that materializes a verified
   plugin bundle.

Sources:

- <https://doc.rust-lang.org/cargo/reference/features.html#optional-dependencies>
- <https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#git-submodules>
- <https://doc.rust-lang.org/cargo/commands/cargo-fetch.html>
- <https://doc.rust-lang.org/cargo/commands/cargo-install.html>
- <https://doc.rust-lang.org/cargo/reference/publishing.html#packaging-a-crate>
- <https://docs.rs/about/builds>

## Options

| Option | Fetch behavior | Detcore/Reverie boundary | Assessment |
| --- | --- | --- | --- |
| **A. Optional `*-sys` crate** | `dbi = ["dep:reverie-dbi", "dep:dynamorio-sys"]`; the native dependency enters an install only with the feature. Repeat for `sabre-sys` and `e9patch-sys`. | Monolithic Hermit directly calls the selected backend with Detcore. The sys crate owns native build/link details only. | Best Cargo-native compile-time option. Vendoring is constrained by the 10 MB registry limit; a downloader inside the sys crate inherits B's offline and supply-chain costs. Useful inside a D plugin. |
| **B. Feature-aware `build.rs` pinned download** | The selected backend crate downloads a fixed URL, verifies SHA-256 before use, and builds in `OUT_DIR`. With the feature off, the optional backend crate and script are absent. | Same in-process Detcore/backend linkage as A. | Simple prototype, but the network happens during compilation, not `cargo fetch`; offline/docs.rs/proxy behavior is poor. If used, require HTTPS, exact version and digest, fail closed before extraction, and never use an unpinned latest URL. |
| **C. Git submodule only** | Cargo recursively obtains non-disabled submodules when it clones a Git dependency. That action is per Git source, not per Rust feature. `update = none` skips a source globally. Registry archives have no checkout to recurse into. | Same linkage as A/B after a successful source build. | Keep for contributor checkouts and exact source provenance only. It cannot express default-off conditional fetch and is not a crates.io distribution mechanism. |
| **D. Separately installed extension** | Core install has no heavy backend edge. Installing one extension fetches exactly that extension and its native bundle. | PATH form moves the whole Detcore/backend run behind a process protocol. Dylib form keeps the host coordinator in the Hermit process and calls a plugin entry point that instantiates Detcore with that backend. | Recommended. It gives the cleanest install graph and independent backend release cadence. Dylibs require an explicit stable ABI and installer; PATH binaries are Cargo-native but duplicate more orchestration. |

### Option D discovery and ABI

Prefer a manifest plus dylib under a versioned directory such as:

```text
$XDG_DATA_HOME/hermit/plugins/v1/dbi/<plugin-version>/
  manifest.json
  libhermit_backend_dbi.so
  assets/...
```

Search only explicit user/system plugin roots, never the current directory.
The manifest records plugin ABI, Hermit/Detcore compatibility, target triple,
capabilities, native source versions and digests, and whether the extension is
a backend or a preprocessor. `hermit backend list` must distinguish installed,
compatible, host-usable, and unsupported.

Export one C-ABI descriptor symbol, for example `hermit_backend_v1`. Its table
accepts a versioned run request and host callback table and returns a structured
status. Do not pass `Backend::run<T>`, `Tool`, trait objects, Rust futures, or
Rust-owned strings across the boundary: the generic backend contract is not
object-safe and Rust has no stable dylib ABI. The plugin owns the concrete
`Backend::run::<Detcore>` instantiation and depends on the exact compatible
Detcore/Reverie revision. This preserves one Detcore behavior without a
backend-local policy fork.

For DBI, the host plugin coordinates the run and owns the pinned DynamoRIO
assets; its client `.so` contains the local `Detcore<DbiGuest>` side and reaches
global state over the existing RPC transport. For SaBRe, advertise only the
capabilities actually implemented; installation alone does not promote the
current synchronous adapter to a deterministic backend. For e9patch, expose a
`prepare` capability whose output then runs through ptrace.

A PATH plugin (`hermit-backend-dbi` or Git-style `hermit-dbi`) is a useful
fallback. It must receive a versioned request over stdin/UDS and itself execute
Detcore with the chosen backend; a binary that merely launches `drrun`, SaBRe,
or e9patch is not a Hermit backend. PATH discovery is easier for
`cargo install`, but it duplicates container, signal, stdio, exit-status, and
configuration orchestration across a process boundary, so it is not preferred.

## Install and capability matrix

Proposed core features:

```toml
[features]
default = ["ptrace"]
ptrace = ["dep:reverie-ptrace"]
kvm = ["dep:reverie-kvm"]
liteinst = ["dep:reverie-liteinst", "dep:detcore-liteinst"]
```

DBI, SaBRe, and e9patch are absent from the core feature graph under option D.

| User action | Heavy inputs fetched | Resulting capability |
| --- | --- | --- |
| `cargo install hermit --locked` | None | CLI + Detcore + ptrace; default usable install. |
| `cargo install hermit --locked --no-default-features` | None | CLI/plugin host only; no built-in execution backend. |
| `cargo install hermit --locked --features kvm` | KVM Rust dependencies, no external native source tree | ptrace + KVM when `/dev/kvm` and the required host capabilities exist. |
| `cargo install hermit --locked --features liteinst` | Self-contained LiteInst Rust/runtime inputs | ptrace + experimental LiteInst; no external LiteInst checkout. |
| `hermit backend install dbi` | DBI plugin bundle + pinned DynamoRIO only | DBI appears only if ABI-compatible and its real Detcore tool path is present. |
| `hermit backend install sabre` | SaBRe plugin bundle + pinned SaBRe only | Experimental SaBRe capability; do not claim deterministic backend status until the Detcore contract is complete. |
| `hermit backend install e9patch` | e9patch preprocessor bundle only | e9patch preparation followed by ptrace; never label it a backend. Requires ptrace capability. |
| Source-tree full validation | All plugin crates and all three native inputs | Intentional developer/CI superset, not the published default. |

If owners instead choose A, keep every heavy feature default-off and require
the following graph:

| Alternative-A install | Inputs fetched | Capability |
| --- | --- | --- |
| default features | Core + ptrace only | ptrace |
| `--no-default-features --features dbi` | DBI + `dynamorio-sys` only | DBI, once its full Detcore path is complete |
| `--no-default-features --features sabre` | SaBRe + `sabre-sys` only | Experimental SaBRe; same truthfulness gate |
| `--no-default-features --features ptrace,e9patch` | ptrace + `e9patch-sys` only | e9patch preprocessing over ptrace |
| `--all-features` | Every Rust backend and all native inputs | Full developer build, intentionally heavy |

## Backend-specific packaging

- **DBI/DynamoRIO:** first D plugin and ABI proof. Prefer a release bundle with
  pinned, checksummed DynamoRIO assets. If source builds are required, put the
  logic in `dynamorio-sys` inside the plugin graph, not in core Hermit. Preserve
  the required release-built client `.so` and existing Detcore/RPC split.
- **SaBRe:** separate because of size, niche status, native toolchain, and
  distinct distribution obligations. Package the loader and plugin together,
  but report the current adapter as experimental until it supports pending
  handlers and the full Detcore guest contract.
- **e9patch:** separate preprocessor extension. Package `e9tool`/`e9patch` and
  exact provenance, but dispatch the prepared binary to ptrace Detcore.
- **KVM:** compile-time opt-in in core; its main cost is Rust code and host
  capability, not a giant source download. Revisit as a plugin after the ABI is
  proven.
- **LiteInst:** compile-time opt-in initially because it is self-contained.

## Developer and CI flow

Do not use bare `cargo fetch` as the conditional-fetch test. In isolated Cargo
caches, run every row of the install matrix and assert both the resulting
`hermit backend list --json` output and the absence/presence of native assets.
Run `cargo tree -e features` for each compile-time combination.

Keep the ordinary validation lane on default features. Add explicit lanes for
`--no-default-features`, KVM, LiteInst, and each plugin. A source-tree
`validate.sh --all-backends` (or equivalent script) may activate/build all
native inputs and run the complete workspace; the name and logs must make that
extra download intentional. Plugin ABI conformance tests should cover version
mismatch, missing/corrupt asset, unsafe search paths, and truthful capability
classification before guest execution.

## Incremental migration

1. Make ptrace, KVM, LiteInst, DBI, and their Detcore adapters truly optional in
   generated manifests; set default to ptrace and add compile-only matrix tests.
2. Define the plugin manifest, C ABI, safe search roots, version rejection, and
   `backend list` before moving a backend. Prototype with a tiny no-op plugin.
3. Move DBI first. It exercises the hardest bundle: host plugin, guest client
   `.so`, native runtime, RPC, release-only build, and large pinned assets.
4. Add the separate e9patch preprocessor package and preserve the ptrace handoff.
5. Move SaBRe with an experimental capability label; enable real-backend status
   only after it instantiates Detcore and passes the backend contract.
6. Add release/install tooling, clean-cache matrix CI, asset checksums and
   provenance, then remove automatic heavy-submodule reliance from end-user
   paths. Retain submodules only for explicit contributor source builds.

## Owner decisions before implementation

1. Approve D-dylib as the product boundary and A/sys-crates as an internal
   packaging tool where source builds remain necessary.
2. Choose the supported plugin installer (`hermit backend install`, distro
   package, or Cargo-installed carrier); plain `cargo install` cannot place a
   dylib.
3. Decide whether KVM and LiteInst remain core opt-ins after the first plugin is
   proven.
4. Approve the plugin compatibility policy and native-binary distribution and
   licensing review for DynamoRIO, SaBRe, and e9patch.
