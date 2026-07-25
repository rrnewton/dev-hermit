# Feasibility Results

## Test Point

The results below were collected on 2026-07-25. The final reviewed
implementation was validated at commit
`d9bd9e27730b8e9e0754ddaa4fadc21e152f9356`.

```text
Host:       devbig030.atn3.facebook.com
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
