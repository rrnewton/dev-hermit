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
`1.85.0` rustup toolchain. It also requires Bash, GNU `timeout`, Cargo, rustc,
and `jq`; the image scripts require the ordinary LLVM/binutils tools used by the
pod compiler.

Every build, test, package, and process command has a deadline, and the complete
run has an overall deadline. Defaults are one hour for `quick` and three hours
for `full`. They can be adjusted without editing the script:

```text
RELEASE_CHECK_TOTAL_TIMEOUT=7200 \
RELEASE_CHECK_COMMAND_TIMEOUT=600 \
RELEASE_CHECK_LONG_TIMEOUT=1800 \
./scripts/release-check.sh quick
```

`RELEASE_CHECK_SKIP_PROCESS=1` permits compile and package checks on another
host, but such a run is explicitly not release-green. Set
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

The script prints the source revision, host kernel and architecture, tool
versions, every command, its timeout, and a final gate count. A timeout or
skipped process suite is never reported as a passing full release gate.

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
`demos/`, `scripts/`, `target/`, `crates/`, `ai_docs/`, or `.minibeads/` trees.
The release gate fails if one of those paths enters the main package.

The executable image harness, preload guest/shim/host, and release automation
remain repository validation tools. They are not supported public crates or
runtime APIs in the 0.1 package. Their workspace manifests set
`publish = false`, and the release gate verifies that exactly the two named
public crates remain publishable.
