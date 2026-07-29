# Fresh-Machine Bring-Up Audit — 2026-07-29

## Scope and evidence quality

This audit follows the documented `clone -> make install-deps -> make -> demo`
path on the replacement devserver and checks the live parent, Hermit, Reverie,
and LiteInst2 trees for owner- or host-specific assumptions.

The host is new, but the workspace volume was restored rather than cloned onto
an empty filesystem: the parent checkout contained prior untracked reports and
21 physical worktree directories, and the shared Cargo cache already contained
older Reverie checkouts. To recover fresh-cache evidence without deleting
shared state, the P0 build test used an isolated `CARGO_HOME` and
`CARGO_TARGET_DIR`, fetched the exact lockfile inputs, moved DynamoRIO completely
out of that temporary checkout, enabled Cargo offline mode, and ran the real
parent `make` recipe.

## Host snapshot

| Item | Observed |
|---|---|
| OS / architecture | CentOS Stream 9, x86_64 |
| Kernel | `6.18.39-0_fbk0_hardened_0_ga43d5727b443` |
| Hermit toolchain | `rustc 1.99.0-nightly (26ae60a9e 2026-07-28)`; `cargo 1.99.0-nightly` |
| Native compiler | GCC/G++ 11.5.0 |
| PMU | `perf stat -e branches true` passed; `perf_event_paranoid=1` |
| Namespaces | unprivileged user+PID namespace probe passed |
| KVM | `/dev/kvm` absent; not required by the QEMU demos because they use TCG |
| QEMU | `qemu-system-x86_64` and `qemu-img` absent |

No live build or run path requires kernel 6.18 specifically. Older kernel
numbers found under reports and benchmark records are measurement provenance,
not executable constraints.

## Dependency inventory

`make install-deps` was executed on this host. On CentOS it installs exactly
`libunwind-devel`, `xz-devel`, and `pkgconf`, then checks the
`libunwind-ptrace` and `liblzma` pkg-config modules. It does not provision the
rest of the documented build or demo stack.

| Profile | Required inputs | Covered by `install-deps` | Fresh-host result |
|---|---|---|---|
| Clone | Git plus GitHub authentication suitable for submodules | No | Git existed; SSH-only submodule URLs remain a clean-clone risk |
| Core ptrace build | make, GCC/G++, pkg-config, libunwind-devel, xz-devel, rustup, nightly Rust, Cargo, network/proxy | Only the three native library/pkg-config packages | Most tools were preinstalled outside the repo; Rust is not an RPM on this host |
| Demos 1–4 | Core build, Python 3, common POSIX/coreutils; GDB for interactive replay; PMU for Demo 1 verification | No demo tools or capability checks | Demo 1 passed after the P0 no-DBI target fix |
| Full/default DBI build | Core build, CMake, Perl, DynamoRIO Git submodule and nested sources | No CMake | Exact initial failure was missing `cmake`; installed ad hoc, then debug full check passed |
| QEMU demos 5–6 | Python 3, qemu-system-x86_64, qemu-img, static BusyBox, cpio, gzip, file, sha256sum, and a kernel source | None | QEMU/qemu-img and BusyBox absent; asset helper stops first on BusyBox |
| Contribution workflow | GitHub CLI and external proxy configuration | No | `git push` failed because configured credential helper `/usr/bin/gh` was absent; installed ad hoc |
| Agent harness | bubblewrap and rust-script | No | patch/task tooling failed until bubblewrap and rust-script were installed ad hoc |
| Portable/full validation | CI additionally installs Go, Clang, Java, jq, Lua, nginx, Node, Redis, Ruby, socat, SQLite, zstd, and other fixture tools | No | This host lacks several optional validation programs; this is broader than demo bring-up |

Linux kernel headers are not required by the current Hermit or QEMU demo path;
the QEMU demo consumes a prebuilt guest kernel.

## Walkthrough results

1. **Clone (static audit):** README uses an HTTPS parent clone, but `.gitmodules`
   uses `git@github.com:` for Hermit and Reverie. A developer without GitHub SSH
   keys can clone the parent successfully and then fail during recursive
   submodule initialization.
2. **Install:** `with-proxy make install-deps` exited 0 but installed/checked
   only libunwind, LZMA, and pkg-config.
3. **Build before fix:** the default DBI path reached `reverie-dbi/build.rs` and
   printed `failed to configure DynamoRIO: No such file or directory`. The
   missing executable was CMake. `build.rs` checks
   `third-party/dynamorio/CMakeLists.txt` before spawning CMake and would print a
   different uninitialized-submodule error. A fresh isolated Cargo fetch
   automatically initialized DynamoRIO and explicitly skipped only e9patch and
   SaBRe.
4. **Build after landed fix:** Hermit PR #1150 landed on `main` as
   `eef2d7d073d08f26318b1fb30fa1a8c990d50915` and makes Hermit's DBI
   dependencies a default-on feature. The parent build selects the no-default
   Hermit binary.
   With DynamoRIO physically absent and Cargo offline, `make` completed a fresh
   release build in 57.26 seconds. The ordinary/default Cargo graph still
   contains both `detcore-dbi` and `reverie-dbi`.
5. **Demo target audit:** feature flags alone were insufficient for
   workspace-wide Cargo builds because `detcore-dbi` and `detcore-sabre` are
   standalone workspace members. `demos/common.sh` therefore must select the
   Hermit binary and the two required guest binaries explicitly. With that
   parent change, Demo 1 exited 0 and its two-run verification compared 9,783
   messages with no substantive differences.
6. **QEMU preflight:** `demos/lib/qemu-assets.sh` immediately failed with
   `a statically linked BusyBox is required`; QEMU and qemu-img are also absent.
   The host has PMU and namespaces, so these are package/bootstrap gaps rather
   than kernel capability failures.

## Findings and fixes

### F1 — P0 — Demo builds entered the full DBI backend graph

**Evidence:** top-level `make`, `demos/common.sh`, and Cargo dependency graphs;
fresh offline build with DynamoRIO removed.

**Fix:** Landed Hermit PR [#1150](https://github.com/rrnewton/hermit/pull/1150)
(`main` `eef2d7d073d08f26318b1fb30fa1a8c990d50915`) introduces a default-on
`dbi` feature and a clear disabled-backend error. Parent
`Makefile` and `demos/common.sh` select `-p hermit --bin hermit
--no-default-features`; the demo helper separately builds only `hello_race` and
`rustbin_heap_ptrs`. Default Cargo/`validate.sh --full` builds retain DBI.

### F2 — P0 — The exact initial failure was missing CMake, not missing source

**Evidence:** `Command::new("cmake").status()` produced the reported os-error-2
text; the preceding source check passed. `command -v cmake` was empty before
the audit, and the default DBI check passed after installing CMake.

**Fix:** include CMake in a documented full-backend dependency profile and make
the preflight distinguish a missing executable from a missing submodule. The
lightweight demo profile should continue not to require either.

### F3 — High — `install-deps` is not a complete build/demo installer

**Evidence:** the successful CentOS transaction named only libunwind-devel,
xz-devel, and pkgconf. Rust, Cargo, compiler tools, CMake, Python, GDB, QEMU,
qemu-img, BusyBox, and contribution tools came from the host or ad-hoc installs.

**Fix:** split dependency provisioning into explicit `core`, `full-backend`,
and `qemu-demo` profiles, plus a non-mutating `make doctor` that prints every
missing tool, capability, and environment variable before building. Keep the
minimal ptrace/demo profile independent of DBI sources.

### F4 — High — HTTPS clone instructions conflict with SSH submodule URLs

**Evidence:** README says `git clone --recurse-submodules https://...`, while
Hermit and Reverie URLs in `.gitmodules` are `git@github.com:...`.

**Fix:** use HTTPS or relative submodule URLs, or explicitly document and test
the SSH-key prerequisite before recommending recursive clone.

### F5 — High — The demo quick start selects a deeply stale branch

**Evidence:** `demos/README.md` says `git checkout demo`; the local `demo`
branch is 312 commits behind current `main` and has 9 unique commits.

**Fix:** make `main` the sole supported quick-start path and remove the checkout
step, or continuously merge and test a release-tagged demo branch.

### F6 — High — QEMU bootstrap has no default portable kernel source

**Evidence:** the README says the kernel is downloaded automatically from
Manifold, but current `qemu-assets.sh` defaults `QEMU_KERNEL_URL` and
`QEMU_KERNEL_MANIFOLD_PATH` to empty and fails unless the user supplies
`KERNEL_IMAGE`, `QEMU_KERNEL_URL`, or `QEMU_KERNEL_MANIFOLD_PATH`.

**Fix:** publish a content-addressed HTTPS artifact usable outside Meta and set
it as the documented default, retaining SHA-256 verification and local-image
override support. Treat Manifold as an optional internal mirror.

### F7 — High — QEMU prerequisites are missing and checked too late

**Evidence:** this host lacks qemu-system-x86_64, qemu-img, and static BusyBox.
The QEMU script runs a Hermit build before complete QEMU/asset preflight, and
the shared dependency check validates only two pkg-config modules.

**Fix:** add a zero-build QEMU doctor that checks QEMU, qemu-img, static
BusyBox, cpio, gzip, file, sha256sum, Python, and kernel-source configuration in
one diagnostic. Document Debian (`qemu-system-x86`, `qemu-utils`,
`busybox-static`) and supported Red Hat equivalents or explicit overrides.

### F8 — Medium — The Rust nightly is floating and not bootstrapped

**Evidence:** `rust-toolchain.toml` says `channel = "nightly"`; the replacement
host selected 2026-07-28 nightly. `install-deps` neither installs rustup nor
checks components.

**Fix:** pin a dated nightly, document rustup installation, and have `doctor`
verify Cargo, rustc, rustfmt, and Clippy at the selected toolchain.

### F9 — Medium — Full DynamoRIO release build is blocked by this host policy

**Evidence:** after installing CMake, a release/full workspace build reached
DynamoRIO C compilation and was denied by the Meta BPF jailer (`FILE_OPEN` in
`cc1`). The default debug all-target check passed; this is host/policy-specific,
not a missing Rust dependency.

**Fix:** document the supported unrestricted build environment or obtain the
appropriate jailer policy; keep lightweight demos out of this path. Do not
weaken the DBI default/full validation requirement.

### F10 — Medium — The worktree guide linked by README is obsolete

**Evidence:** `WORKTREES.md` references `./slot-init.sh`, `devbig-lead`, and the
old outer-worktree protocol, while current policy requires
`scripts/allocate-worktree.rs` and nested product slots.

**Fix:** replace it with a short link to the canonical `AGENTS.md` allocator
workflow and remove legacy host/branch examples.

### F11 — Low — Portability scrub held for live surfaces, but not as a whole-tree gate

**Evidence:** parent `make check-portability` passed; direct greps found no
`devbig030`, `/home/newton`, or `/Users/newton` in live parent build/run files
or in tracked Hermit/Reverie/LiteInst2 content. Historical experiments and
measurement reports intentionally retain host/path provenance. Running the
portability script explicitly across product repos fails on legitimate
`/usr/local/bin` fallback probes and author surname `Newton`, so it cannot yet
serve as the requested whole-tree gate.

**Fix:** preserve historical evidence, but teach the checker to distinguish
runtime hardcoding from test fallback lists, author headers, and durable
measurement metadata. Add the three product repos to a CI invocation after
that refinement.

### F12 — Medium — The replacement host did not provide a literal clean clone

**Evidence:** restored untracked artifacts, Cargo caches, and 21 physical slot
directories were present. This can conceal clone and dependency failures.

**Fix:** add a disposable clean-room smoke test in CI or a temporary directory:
HTTPS recursive clone, dependency doctor, isolated Cargo home/target, `make`,
and Demo 1. Never use the shared primaries or caches for that gate.

## Follow-up task groups

Filed follow-ups:

- `add_build_full_and` — dependency profiles and `make doctor`;
- `make_recursive_clone_and` — clone URLs, quick start, and worktree docs;
- `publish_portable_qemu_demo` — portable assets and complete QEMU preflight;
- `extend_portability_gate_across` — product-aware whole-tree portability gate.

The P0 feature and parent entrypoint changes are tracked by
`fix-make-demo-build-without-backend-deps`.
