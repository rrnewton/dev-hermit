# Support and release verification

This document separates API availability from runtime evidence. A target listed
as "implemented" has an applicable code path, but is not release-supported
until the relevant multi-process tests have run on that target.

## Support matrix

| Component | x86-64 Linux | AArch64 Linux | Other Linux | Non-Linux |
| --- | --- | --- | --- | --- |
| `PodValue`, layout descriptors, and checked offsets | Runtime tested | Portable design; not runtime tested | Portable design; not runtime tested | `no_std` design; not runtime tested |
| Typed mapping lifecycle | Runtime tested across fork and different-address exec attachment | Implemented with lock-free 64-bit atomics; not runtime tested | Requires `target_has_atomic = "64"`; not runtime tested | Host mapping and cross-process atomic behavior are not supplied or validated |
| Core atomics and `ProcessSpinMutex` | Runtime tested | Implemented when the target exposes the required atomic width | Implemented when the target exposes the required atomic width | Requires an OS and mapping implementation with genuinely process-shared coherent memory |
| `ProcessFutexMutex` | Runtime tested, including different-address exec attachment and bounded wait cancellation | Inline syscall implemented; not runtime tested here | `libc::syscall` fallback; not runtime tested | Unavailable |
| `Snzi` | Runtime tested | Implemented when `target_has_atomic = "64"`; not runtime tested here | Same requirement; not runtime tested | Same requirement; cross-process behavior is not validated |
| `CloseableSnzi` admission barrier | Runtime tested across close races, stopped participants, and killed participants | Implemented when `target_has_atomic = "64"`; not runtime tested here | Same requirement; not runtime tested | Same requirement; cross-process behavior is not validated |
| `Csnzi` scalable admission barrier | Runtime tested across close races, different-address mappings, stopped/killed participants, capacity boundaries, and freestanding RX execution | Implemented when `target_has_atomic = "64"`; Rust 1.85 PIC inspected but not runtime tested here | Same requirement; not runtime tested | Same requirement; cross-process behavior is not validated |
| `RelocAllocator`, `SharedBox`, and `SharedVec` | Runtime tested across different-address exec, concurrent allocation/free, and killed transactions | Implemented when `target_has_atomic = "64"`; not runtime tested here | Same requirement; not runtime tested | Same requirement; cross-process behavior is not validated |
| Talc `FixedRegionAllocator` | Experimental runtime evidence across fork and fixed-address exec | Experimental design only | Experimental design only | Experimental design only |
| Executable pod compiler and loader | Runtime tested | Unavailable: the compiler and linker script currently emit x86-64 ELF | Unavailable | Unavailable |
| `LD_PRELOAD` unaware-guest demonstration | Runtime tested with a dynamically linked ELF guest | Shim syscall path is implemented, but the complete demo is not release-tested | Not release-tested | Unavailable |

"Runtime tested" currently means the release gate ran on x86-64 Linux with
the same backing object mapped into multiple processes. It is not a promise
that every kernel, C library, CPU model, container policy, or security module
permits the required mappings and executable memfds.

The layout descriptor is little-endian wire data, but the shared Rust payload
uses the native target ABI. All peers must agree on endianness, pointer width,
atomic widths, target ABI, compiler-selected layout, feature set, and the
authenticated build identity. A matching layout fingerprint is a compatibility
check, not code authentication and not proof that arbitrary bytes are a valid
Rust value.

## Linux facilities

| Facility | Minimum upstream kernel | Use in this repository |
| --- | --- | --- |
| Shared futex wait/wake | Linux 2.6 | `ProcessFutexMutex`; the futex word must be in a shared mapping and private futex operations are not used |
| `FUTEX_WAIT_BITSET` | Linux 2.6.25 | Monotonic absolute deadlines for `ProcessFutexMutex::try_lock_for`; timeout cancels waiting and never steals ownership |
| `memfd_create` and sealing | Linux 3.17 | File-backed state/code transport and immutable code artifacts |
| `MAP_FIXED_NOREPLACE` | Linux 4.17 | Collision-safe fixed-address allocator example and guarded image placement |
| `MFD_EXEC`, `MFD_NOEXEC_SEAL`, and `F_SEAL_EXEC` | Linux 6.3 | Current executable-image and preload runtime; there is no older-kernel fallback yet |

The executable proofs also require executable memfds to be allowed by kernel
policy, `/proc/self/maps` to be readable, and the process security policy to
permit the requested mappings and `LD_PRELOAD`. Code is mapped read-execute and
state read-write; the runtime does not require writable-executable pages.

`try_lock_for` bounds how long one caller waits. It is cancellation, not
owner-death recovery: timeout never steals a lock, and a process that exits
inside a critical section can still leave `ProcessFutexMutex` locked forever.

## One-command release gate

From the `v2` checkout, run:

```text
./scripts/release-check.sh quick
./scripts/release-check.sh full
```

`quick` is the complete developer gate. It runs reduced executable-pod and
preload workloads while retaining format, current-toolchain tests, isolated
feature checks, MSRV checks, clippy, rustdoc, and package verification.

`full` is the release-candidate gate. It adds the full MSRV all-feature test and
feature matrix, MSRV rustdoc, every public process example, and larger pod and
preload workloads. The script requires Rust 1.85.0 to be installed as the
`1.85.0` rustup toolchain. It also requires Bash, GNU `timeout`, rustup, and
`jq`; the image scripts require the ordinary LLVM/binutils tools used by the pod
compiler. Invoke both check scripts directly so their `#!/bin/bash -p` shebang
can reject `BASH_ENV` startup files and imported shell functions before line 1.
Invoking them as `bash scripts/...` selects the caller's interpreter instead and
is not a validated entry path.

Every build, test, package, and process command has a deadline, and the complete
run has an overall deadline. Defaults are one hour for `quick` and three hours
for `full`. They can be adjusted without editing the script:

```text
RELEASE_CHECK_TOTAL_TIMEOUT=7200 \
  RELEASE_CHECK_COMMAND_TIMEOUT=600 \
  RELEASE_CHECK_LONG_TIMEOUT=1800 \
  RELEASE_CHECK_ADVERSARIAL_TIMEOUT=1200 \
  ./scripts/release-check.sh quick
```

`RELEASE_CHECK_SKIP_PROCESS=1` permits compile and package checks on another
host, but such a run exits 2 with `INCOMPLETE` and is explicitly not
release-green. It never prints the final release `PASS`. Set
`RELEASE_CHECK_REQUIRE_CLEAN=1` for the final candidate. Use
`RELEASE_CHECK_DRY_RUN=1` to inspect the selected commands.

The gate covers:

- formatting;
- current Rust all-feature workspace checks and tests;
- current Rust no-default and isolated `derive`, `fixed-allocator`, and
  `linux-futex` configurations;
- Rust 1.85.0 all-feature checks and no-default tests, with the full test and
  feature matrix in `full` mode;
- clippy with warnings denied and rustdoc with warnings denied;
- crate packaging, verification, and an allowlist/denylist audit of package
  contents; and
- real multi-process layout, executable-pod, preload, synchronization, SNZI,
  closeable admission, offset-remapping, and allocator evidence according to
  the selected mode.

When process evidence is enabled, the release gate also invokes the bounded
adversarial suite:

```text
./scripts/adversarial-check.sh quick
./scripts/adversarial-check.sh full
```

The adversarial suite runs production-linked Loom models, serial Linux
SIGSTOP/SIGKILL cuts at selected transition boundaries, existing
mapping/allocator/migration crash regressions, five parser and offset fuzz
targets, a dedicated fork-, thread-, FFI-, and mmap-free Miri target, and
thread-only ASan/TSan tests. `full` increases the Loom and fuzz bounds and adds
connector fail-closed recovery. Its default whole-run deadlines are 20 minutes
for `quick` and 90 minutes for `full`.

Each selected gate, including an unavailable or unsupported gate, receives a
stable ID and ordinal and ends in `PASS`, `FAIL`, `UNAVAILABLE`, or
`UNSUPPORTED`.
The script exits 0 only when every selected gate passes, 1 for a product or
test failure, 2 when required tooling/platform support is unavailable, and 124
for a deadline. Missing `cargo-fuzz`, the pinned nightly, Miri, rust-src, or a
supported sanitizer host is therefore visible and never release-green.

The runners use literal, root-owned, non-group/other-writable `/usr/bin`
executables as their control-tool trust root. They do not discover Cargo or
rustc through `PATH`. Instead, they find rustup at the account database's home
directory (`.cargo/bin/rustup`, with `/usr/bin` and `/usr/local/bin` fallbacks),
ask it for each toolchain's actual Cargo, rustc, rustdoc, Miri, rustfmt, and
Clippy executables, canonicalize those paths, and invoke the actual binaries.
Every selected binary is owner/mode checked, SHA-256 recorded, and revalidated
before success. A mutable rustup proxy hash is not used as a substitute for the
dispatched toolchain hashes.

Portable installations can set `SHMEM_POD_RUSTUP_BIN` to a trusted absolute
rustup path, `SHMEM_POD_RUSTUP_HOME` to an absolute toolchain store,
`SHMEM_POD_CURRENT_TOOLCHAIN` and `SHMEM_POD_MSRV_TOOLCHAIN` to installed
toolchain names, and `SHMEM_POD_CARGO_FUZZ_BIN` to a trusted absolute
`cargo-fuzz`. `SHMEM_POD_CARGO_HOME` names a cache source only. Each run creates
a private, config-free active Cargo home and exposes only the source's
`registry/` and `git/` cache directories. Cargo runs from a private source view
whose ancestors are checked for `.cargo/config{,.toml}`, so user or repository
configs cannot inject wrappers, rustflags, linkers, or runners. Validation Cargo
commands are offline; a missing locked dependency is an explicit prerequisite
failure rather than an opportunity to consult ambient network configuration.

The adversarial gate manifest is opened once into a private snapshot, hashed,
validated, and parsed into read-only in-memory tables. All gates use that
snapshot; a transient manifest rename cannot change later evidence, while a
persistent change fails final revalidation. Test gates must emit the exact test
names and count in `scripts/adversarial-gates.tsv`; a successful Cargo
invocation with zero matching tests fails. Command descriptors and fuzz target
names are checked against their manifest fields. Nested connector scripts must
emit every expected marker exactly once and no additional marker-shaped line.
`./scripts/adversarial-check.sh self-test` attacks both entry points with
`BASH_ENV`, an exported `exit` function, hostile `PATH` Cargo/rustc/timeout
binaries, and a cache-side `rustc-wrapper` config, in addition to probing zero,
duplicate, missing, and unexpected gate evidence.

These checks have deliberate limits. A bounded Loom search is not a proof over
all executions. Most crash cuts stop immediately after a named production RMW;
`CloseableDrainScanned` instead stops after the read-only drain scan and before
the terminal seal. These cuts verify fail-stop states, not leases or owner-death
recovery. Miri checks parser, layout, and provenance-sensitive offset code
rather than cross-process behavior. ASan and TSan use threads because sanitizers
do not model shared mappings across exec; TSan also validates the
implementation's atomic synchronization, not kernel or hardware conformance.
Fuzzing is time-bounded and its generated corpus and artifacts are disposable.

Loom is a dev-dependency and the fuzz harness is a separate unpublished
workspace. Neither changes the library's `no_std` surface or Rust 1.85 MSRV;
the ordinary current/MSRV release matrix remains authoritative for those
contracts.

Both scripts print the source revision, host kernel and architecture, selected
toolchain versions and actual executable hashes, every command, its timeout,
and a final gate count. Exit 124 is a confirmed deadline; status 137 is reported
as ambiguous `SIGKILL` rather than assumed to be a timeout. A timeout or skipped
process suite is never reported as a passing full release gate.

## Crates.io package boundary

Only these crates are publication artifacts:

1. `shmem-pod-macros`
2. `shmem-pod`

Publish `shmem-pod-macros` first and wait until crates.io serves that exact
version. Publish `shmem-pod` second. The release check models this sequence by
packaging and verifying the macros crate first, then verifying the library with
a local crates.io patch for the not-yet-published macro version.

The public library package contains its source, examples, tests, documentation,
README, licenses, and lockfile. It must not contain the private `poc/`,
`demos/`, `fuzz/`, `scripts/`, `target/`, `crates/`, `ai_docs/`, or `.minibeads/`
trees.
The release gate fails if one of those paths enters the main package.

The executable image harness, preload guest/shim/host, and release automation
remain repository validation tools. They are not supported public crates or
runtime APIs in the 0.1 package. Their workspace manifests set
`publish = false`, and the release gate verifies that exactly the two named
public crates remain publishable.
