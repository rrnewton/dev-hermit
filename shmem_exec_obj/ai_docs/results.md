# Feasibility Results

## Versioned SDK, Futex, And Injection Checkpoint

The standalone `v2` SDK, executable-image harness, and injection demo were
validated on 2026-07-26 at implementation commit
`a6be8551747a0935987578d2bcc4a9cfd352fee8`.

### New Claims And Evidence

| Hypothesis | Result | Evidence |
| --- | --- | --- |
| A process-shared lock must busy-spin | False on Linux | `ProcessFutexMutex` spins briefly, then uses non-private `FUTEX_WAIT`/`FUTEX_WAKE`; an exec test maps one memfd at a different VA and observes the waiter sleeping in the kernel before wakeup |
| The futex slow path needs a libc relocation | False on tested LP64 targets | The copied `no_std` pod issues the raw syscall on x86-64/AArch64; the image closure has no undefined libc symbol |
| An unaware executable can call the pod | Proven with `LD_PRELOAD` | A dependency-free guest calls ordinary `getuid`; the shim authenticates and maps inherited sealed FDs, then updates shared pod counters across a recursive exec tree |
| `#[repr(C)]` is required for exact-build Rust peers | False | Default `repr(Rust)` state works when the loader authenticates the build and validates the generated size/alignment/field-offset/type fingerprint |
| Public packaging must include the private image harness | False | Independent crates.io package verification includes the SDK, macros, docs, tests, and examples while excluding `poc/` and `demos/` |
| Rustdoc alone is enough to use the core API | Proven for the tested surface | A third source-blind consumer built a default-layout state with atomics, a futex mutex, and a checked offset on its first compile |

The full v2 harness produced an 8,216-byte authenticated image containing
7,704 bytes of linked code:

```text
artifact sha256:   f87e73d2b8bde7ef74c441c5ebde1217246f7d2fef29e3218576dc07a27acd8c
processes:         3 (host plus two independent exec workers)
calls:             4,000 exact
SNZI cycles:       2,000
final query:       false
final quiescent:   true
```

The default preload tree reported exactly 1,407 intercepted calls and seven
attachments for seven processes, two threads per process, and 100 calls per
thread. The extra seven calls are deliberate per-process initialization
preflights. The legacy v1 harness also reproduced exact coarse, fine, and
atomic totals after the directory split.

### Validation

```bash
cargo +1.85.0 check --locked --workspace --all-targets --all-features
cargo +1.85.0 test --locked -p shmem-pod --no-default-features
cargo test --locked --workspace --all-features
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
RUSTDOCFLAGS='-D warnings' \
  cargo +1.85.0 doc --locked --workspace --all-features --no-deps
cargo package --locked -p shmem-pod-macros --allow-dirty
cargo package --locked -p shmem-pod --allow-dirty \
  --config 'patch.crates-io.shmem-pod-macros.path="crates/macros"'
./v2/scripts/run-poc.sh
./v2/scripts/run-preload-demo.sh
```

The macro package contained 8 files (9.3 KiB compressed). The main package
contained 44 files (62.4 KiB compressed). Both verified independently; the
local patch only simulates publishing `shmem-pod-macros` first.

The futex mutex deliberately is not robust: a process killed while holding it
can wedge peers just as the spin mutex can. Ptrace bootstrap and binary-patch
trampolines are specified against the same C ABI but remain designs rather
than implemented proofs. Fixed-address Talc remains experimental because the
cross-process typed-pointer strict-provenance argument is unresolved.

## SDK And V2 Checkpoint

The publishable SDK and linked executable-object implementation were validated
on 2026-07-26 at commit
`7165198cb82dc47d0853b814c3de7aaf44cd07d6`.

```text
Host:       devbig030
Kernel:     Linux 6.17.13 x86_64
Page size:  4096
MSRV:       rustc 1.85.0, LLVM 19.1.7
Current:    rustc 1.96.0, LLVM 22.1.2
```

### New Claims And Evidence

| Hypothesis | Result | Evidence |
| --- | --- | --- |
| User state requires `#[repr(C)]` | False for exact-build peers | Default `repr(Rust)` derives fingerprint actual field offsets; descriptor mismatch tests pass |
| A `no_std` SDK can express admissible state | Proven for structural capabilities | `PodValue`, `FixedAddressPodValue`, and `PodSync`; 12 negative compile cases |
| General allocation can be confined to known pages | Proven with explicit deallocation | Talc/allocator-api2 vectors remained inside a caller-supplied 2 MiB arena |
| Absolute allocator metadata can survive independent exec | Proven at a required VA | Five processes remapped one memfd at `0x500000000000`, validated bootstrap/layout, and completed 750 vector rounds |
| A real hierarchical SNZI works in shared pages | Proven under process/thread contention | Four-way `HALF` helping tree completed 160,000 fork operations and deterministic helper scheduling |
| The SNZI can execute inside the copied pod | Proven | Three exec'd processes completed 400 to 1,000 tested arrival/departure cycles and ended false/quiescent |
| Rust code may use internal calls in one copied closure | Proven for audited x86 PC32/PLT32 | V2 linked 6,608 bytes into one RX `.pod` section and ran at distinct VAs |
| Section membership alone bounds relocations | False | A `.pod` section-symbol relocation with an escaping addend is rejected using its effective target |
| Build provenance can cover transitive Rust inputs | Proven for rustc dep-info scope | Manifest records hashes for rustc, rust-lld, linker script, rlib/object, and every reported source dependency |

The traits are representation capabilities, not a general code verifier.
Integers can still be misused as pointers or file descriptors by unsafe methods,
and a writable guest can corrupt shared bytes directly.

### Validation

The exact implementation tree passed both the declared MSRV and current stable:

```bash
cargo +1.85.0 test --locked --workspace --all-features
cargo +1.85.0 test -p shmem-pod --no-default-features
cargo +1.85.0 clippy --locked --workspace --all-targets --all-features -- -D warnings
RUSTDOCFLAGS='-D warnings' \
  cargo +1.85.0 doc --locked -p shmem-pod -p shmem-pod-macros \
  --no-deps --all-features
cargo test --locked --workspace --all-features
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
```

The SDK process examples reported:

```text
PASS process_locks workers=8 iterations=25000 updates=400000
PASS snzi workers=8 operations=160000 leaves=64
PASS fixed_allocator processes=7 vector_rounds=1400 arena_bytes=2097152
PASS fixed_allocator_exec processes=5 vector_rounds=750
     fixed_address=0x500000000000 arena_bytes=2097152
```

The complete harness selected Rust 1.85 for freestanding code generation:

```bash
POD_DEPTH=1 POD_FANOUT=2 POD_THREADS=2 POD_ITERATIONS=200 \
./v1/scripts/run-poc.sh

POD_WORKERS=2 POD_THREADS=2 POD_ITERATIONS=100 \
POD_RUSTC="$(rustup which --toolchain 1.85.0 rustc)" \
./v2/scripts/run-poc.sh
```

V1 again produced exact coarse, fine, and atomic totals in three independently
exec'd processes. V2 rejected the outside-addend, absolute, undefined-symbol,
and mismatched-SDK-provenance fixtures, then produced:

```text
image bytes:       7120
linked code bytes: 6608
artifact sha256:   324e9c9454ef28ba590142effe5cba5f9e7a72182496bb15ef6ace40ae069fa7
processes:         3 (host plus two independent exec workers)
SNZI leaves:       16
final query:       false
final quiescent:   true
code VAs:          0x300000000000, 0x300010000000, 0x300020000000
state VAs:         0x400000000000, 0x400010000000, 0x400020000000
```

Both crates packaged and verified using Rust 1.85, including license texts and
the four SDK examples. Initial publication must be sequential:
`shmem-pod-macros` first, then `shmem-pod` after the registry indexes version
0.1.0.

### Blind Consumer Reviews

Two fresh agents were limited to generated rustdoc and a dependency stanza.
Both built working programs on their first compile without reading source. The
first used a default-layout state, process mutex, and checked atomic offset
across four processes. The second independently built descriptor/bootstrap and
fixed-allocator attachment with allocator-aware vectors. Their feedback
directly produced `LayoutDescriptor`, complete mapping docs, standard errors,
and the independent-exec allocator example. See [`reviews/`](reviews/) for the durable
reports.

### Remaining Restrictions

- Native participants remain mutually trusted; RW state is not isolation.
- `repr(Rust)` compatibility requires the exact authenticated build and layout
  fingerprint. It is not a stable ABI.
- Talc stores absolute pointers, so allocator users require collision-safe
  identical-VA mappings and the same complete SDK build.
- Spin locks are non-fair and not robust; owner death can wedge them. Process
  death can leak an SNZI arrival.
- Fork duplicates Rust ownership capabilities; live guards, tokens, and
  allocation owners require the documented exec/`_exit` discipline.
- The linked executable audit is x86-64-specific and still trusts reviewed
  source. It is not a machine-code sandbox.
- Mark/sweep remains future work because roots, mutation coordination, and
  crash recovery are not yet specified.
- Current Detcore `GlobalState` cannot be moved wholesale. Incremental atomic,
  counter, presence, and fixed-arena fast paths are the practical integration.

## Original V1 Test Point

The results below were collected on 2026-07-25. The final reviewed
implementation was validated at commit
`d9bd9e27730b8e9e0754ddaa4fadc21e152f9356`.

```text
Host:       devbig030
Kernel:     Linux 6.17.13 x86_64
CPU:        AMD EPYC 9D85, 316 logical CPUs
Page size:  4096
glibc:      2.34
Stable:     rustc 1.96.0, LLVM 22.1.2
Nightly:    rustc 1.99.0-nightly (be8e82435), LLVM 22.1.8
Reverie:    62e7593c96aa2e7b42189e80de326528b52133c7 (main)
Hermit:     16c47870f6b1a349f2e0f8a656eb2074bea36d02 (main)
```

Both product repositories were inspected read-only and remained on `main`.

At that exact implementation commit, the default stable-toolchain harness
passed with 13 independently exec'd processes per mode and 260,000 exact
updates per counter. The full workspace clippy run with warnings denied also
passed. A separate project-nightly run passed both artifact counterexamples
and all three modes with 3 processes and 2 threads per process.

## Claims And Evidence

| Hypothesis | Result | Evidence |
| --- | --- | --- |
| Rust can emit callable copied code | Proven for the restricted subset | Four entries executed from a 599-byte raw image |
| `PIC` alone makes arbitrary Rust text copyable | False | Unrelated `no_std` methods emitted `memset`, rodata, and unwind relocations |
| No relocations imply safe/self-contained code | False | An absolute-write fixture passes relocation checks and would fault if invoked |
| Selected entries need no common code VA | Proven | Every exec used a distinct `MAP_FIXED_NOREPLACE` address |
| Shared state needs no common data VA | Proven | Every exec passed a distinct explicit state pointer |
| Code and state can obey W^X | Proven for VMAs | `r-xs` code, `rw-s` state, no RWX trace; writes to code faulted |
| Coarse locking preserves exact totals | Proven under normal completion | Stress and repeated process/thread tests passed |
| Fine locking preserves exact totals | Proven under normal completion | Stress and repeated process/thread tests passed |
| Atomic increments preserve exact totals | Proven on this x86-64 target | Stress and repeated process/thread tests passed |
| Spinlocks recover from owner death | False by construction | No owner identity or recovery protocol exists |
| This can hold current Detcore `GlobalState` | False | State contains heap, `Arc`, Tokio, wakers, and dynamic collections |
| This isolates state from the guest | False | The guest has an RW mapping and can mutate bytes directly |

## Compiler Results

The exact comparison commands were:

```bash
target/release/pod-compiler \
  --rustc "$(rustup which --toolchain stable rustc)" \
  --source pod-code/src/lib.rs \
  --output target/pod/stable.bin \
  --object target/pod/stable.o \
  --manifest target/pod/stable.manifest

target/release/pod-compiler \
  --rustc "$(rustup which --toolchain nightly rustc)" \
  --source pod-code/src/lib.rs \
  --output target/pod/nightly.bin \
  --object target/pod/nightly.o \
  --manifest target/pod/nightly.manifest

sha256sum target/pod/stable.bin target/pod/nightly.bin
cmp target/pod/stable.bin target/pod/nightly.bin
```

Both compilers emitted a 599-byte image. In this run the images were
byte-identical:

```text
33c9434ef4af0b6393dcdcefbcf9e25a885b8d90200821a0c1c36c5157e5d8ce
```

This equality is observational, not an ABI promise. Future compiler output is
allowed to differ if each required entry independently passes the artifact
gate.

The image entries were:

```text
pod_register:   image offset 0x080, 127 bytes
pod_coarse_add: image offset 0x100, 121 bytes
pod_fine_add:   image offset 0x180, 131 bytes
pod_atomic_add: image offset 0x210,  71 bytes
```

The selected function sections had no relocations. The intermediate object was
not globally relocation-free. It contained relocations from unused
`PodState::new`/`Default` code to `memset` and read-only constants, plus
`.eh_frame` relocations. Because the compiler copies exact symbol ranges rather
than whole sections or the whole object, none entered the image.

The external-call fixture produced the required refusal:

```text
pod-compiler: symbol "pod_register" contains a relocation at +0x5
targeting Symbol(...)
```

An independent adversarial review supplied the opposite counterexample: a
correctly shaped, relocation-free entry that stores to absolute address 1.
The compiler accepted it, and invocation faulted. The equivalent source is
retained as `fixtures/unsafe-absolute-pod.rs` but is never executed by the
harness. This establishes that the compiler is a relocation gate for trusted
source, not a safe-code validator. Loader method invocation is therefore
marked unsafe.

Nightly disassembly of the atomic body ended in the expected instruction:

```text
lock add %rdx,0x200(%rdi,%rax,1)
```

Coarse and fine bodies contained `lock cmpxchg` acquisition loops, `pause`,
plain protected increments, and release stores. No selected entry contained a
call instruction or external access.

## W^X And Address Independence

The traced command was:

```bash
strace -ff -yy -o target/pod/trace \
  -e trace=mmap,mprotect,clone,clone3,execve,futex,wait4 \
  target/release/pod-host \
  --image target/pod/pod.bin \
  --instance target/pod/trace.instance \
  --preload target/release/libpod_preload.so \
  --guest target/release/pod-guest \
  --mode atomic --depth 1 --fanout 2 --threads 2 --iterations 100
```

Three independent execs mapped the same inode and offsets at these example
addresses:

```text
0x201d55e00000 RX code    0x401d55e00000 RW state
0x20170ee00000 RX code    0x40170ee00000 RW state
0x200afb400000 RX code    0x400afb400000 RW state
```

Representative syscalls were:

```text
mmap(0x201d55e00000, 4096,  PROT_READ|PROT_EXEC,
     MAP_SHARED|MAP_FIXED_NOREPLACE, fd, 0) = 0x201d55e00000
mmap(0x401d55e00000, 36864, PROT_READ|PROT_WRITE,
     MAP_SHARED|MAP_FIXED_NOREPLACE, fd, 0x1000) = 0x401d55e00000
```

No pod mapping combined `PROT_WRITE` and `PROT_EXEC`. In addition, each host
run forked a child that attempted a volatile byte write to the code mapping;
the required result was `SIGSEGV` or `SIGBUS`.

The kernel on this host does permit some anonymous RWX mappings, so W^X is not
an ambient kernel guarantee here. It is an invariant enforced by this loader.

## Concurrency Results

The largest recorded run used:

```text
depth=2, fanout=3       13 independently exec'd processes
threads/process=4       52 worker threads
iterations/thread=10000
expected/counter=520000
```

Output:

```text
PASS mode=coarse processes=13 threads/process=4 calls/counter=520000 elapsed_ms=2039
PASS mode=fine   processes=13 threads/process=4 calls/counter=520000 elapsed_ms=236
PASS mode=atomic processes=13 threads/process=4 calls/counter=520000 elapsed_ms=49
```

The verifier checked all four counters in each run, so these lines represent
2,080,000 successful pod updates per mode. It also required 13 unique PIDs, 13
unique code addresses, 13 unique state addresses, zero failed calls, and zero
updates in the inactive mode tables.

Five additional repetitions used 7 processes, 3 threads per process, and 2,000
iterations. All 15 mode runs produced exactly 42,000 updates in each of four
counters.

The timing includes four real credential syscalls per loop, process creation,
thread creation, mapping, and validation. It is useful only as a contention
shape: the global lock was slowest, sharding helped, and the atomic path was
fastest. It is not a pod-call microbenchmark and is not a comparison with the
current Reverie RPC path.

## Negative And Boundary Tests

Automated tests cover:

- shared ABI sizes and cache-line alignments;
- wrong ABI version rejection;
- truncated header rejection;
- overlapping entry rejection;
- out-of-bounds counter rejection;
- external relocation rejection;
- explicit acceptance (without execution) of the unsafe absolute-write fixture;
- compile-time function-signature assertions for the built-in pod;
- RX mapping write fault;
- exact inactive-table zeros;
- duplicate PID/code/state address rejection.

Both stable and project-nightly pod code generation were also exercised
manually with the exact commands above.

## Not Yet Proven

The PoC does not establish production support for:

- owner-death recovery for coarse or fine locks;
- fair blocking without CPU-consuming spins;
- CET/IBT enforcement (the default entries do not begin with `endbr64`);
- AArch64 or any non-x86-64 memory/ABI/instruction-cache behavior;
- SELinux, noexec mounts, `memfd_noexec`, or restrictive seccomp policies;
- safe reentry from a signal handler or a hook nested during initialization;
- fork-without-exec attachment after preload initialization;
- static binaries, secure-execution binaries, or direct-syscall interception;
- unload, live ABI upgrade, state migration, or multi-version attachment;
- protection against a malicious guest with direct RW state access;
- code immutability against `pwrite` through another descriptor;
- performance relative to Reverie's current local/RPC calls.

Those are engineering gates for adoption, not contradictions of the narrow
feasibility result.
