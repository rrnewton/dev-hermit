# Reverie Backend Architecture Report

Date: 2026-07-27 (measurements completed and adversarial-review corrections
incorporated 2026-07-28 UTC)

## Executive conclusions

1. The exact `counter1` and `counter2` tools run on ptrace, SaBRe, LiteInst, and
   DBI. Ptrace, SaBRe, and LiteInst run the shared strace source; DBI runs its
   adapted strace mirror. KVM was blocked during the original collection, then
   all three KVM tools passed during a later, transient `/dev/kvm` capability
   window. e9patch exposes no example-tool binaries at all.
2. Cross-backend counter totals do **not** match. A workload validated by normal
   `strace` at exactly 40,051 syscalls produced 40,055 under ptrace, 40,042
   under SaBRe, 40,035 under KVM, and 10,042 under DBI; LiteInst failed at
   `fork`. These are backend coverage/lifecycle boundaries, not workload
   randomness.
3. Reverie's logical RPC contract is shared (`GlobalRPC::send_rpc` to
   `GlobalTool::receive_rpc`). Ptrace and KVM dispatch in-process. LiteInst,
   SaBRe, and coordinated DBI use a coordinator over UDS. SaBRe reuses the
   shared blocking client; LiteInst and DBI each duplicate the wire-compatible
   client for their trusted-gate/private-loader constraints.
4. A direct microbenchmark of `reverie-rpc-transport` produced historical
   samples of 6,528/6,397/6,529 ns and a review rerun of
   6,414/6,571/6,942 ns per same-process UDS+bincode round trip. This is an
   order-of-magnitude baseline, not a stable interval or a backend RPC proxy.
5. LiteInst uses LD_PRELOAD and SIGSYS to discover and install hooks, but the
   generic Tool and its blocking RPC run after `sigreturn` in normal guest
   context. Standalone Reverie e9patch replaces syscalls with an `int3` payload
   handled by `reverie-ptrace`; Hermit's `--backend e9patch` is a different
   no-op preprocessing/overlay path followed by ordinary ptrace Detcore.
   Standalone SaBRe rewrites in process and mediates signals centrally; Hermit
   additionally runs a custom ptrace supervisor that converts missed raw
   syscall sites into SaBRe's SIGILL marker after coordinator readiness.
6. Among the standalone SaBRe/LiteInst/e9patch alternatives, only e9patch uses
   the Rust `reverie-ptrace` wrapper. The normal ptrace backend also uses it.
   Hermit's SaBRe safety net instead uses a custom `nix::sys::ptrace`/libc
   supervisor, while LiteInst has no active ptrace path; there is no existing
   common three-way fallback implementation.

## Provenance and host

Command:

```text
printf 'reverie_sha='; git rev-parse HEAD; printf 'hermit_sha='; git -C ../hermit rev-parse HEAD; uname -a; rustc --version; cargo --version; ls -l /dev/kvm 2>&1 || true; git submodule status third-party/e9patch third-party/sabre third-party/dynamorio
```

Full output:

```text
reverie_sha=f0d043620bda85d081306c376e08872e8a7782c0
hermit_sha=a62258e58544e33c8978f4ae2afa08b80f1dea87
Linux development host 6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79 #1 SMP Thu Dec 18 12:27:13 PST 2025 x86_64 x86_64 x86_64 GNU/Linux
rustc 1.99.0-nightly (be8e82435 2026-07-11)
cargo 1.99.0-nightly (59800466c 2026-07-07)
ls: cannot access '/dev/kvm': No such file or directory
 929840ad9190e5086775e8debc0f0b79b4208d59 third-party/dynamorio (929840a)
-6c2c03c1da74b14daf1788a9f8dccfa354ce04a6 third-party/e9patch
 34065e7ddae6f1c90db7e0bf5c22a9aa89f9d605 third-party/sabre (heads/master)
```

The e9patch source was not initialized. That does not explain the missing
example binaries: `reverie-e9patch/Cargo.toml` defines a library and tests, not
counter/strace binaries.

### Tool build commands and artifact provenance

All original matrix artifacts came from Reverie
`f0d043620bda85d081306c376e08872e8a7782c0` in the same release target
directory. A clean checkout can recreate them with the pinned submodules and
commands below (all networked commands use `with-proxy`):

```text
# Shared ptrace tools, KVM adapters, and LiteInst launcher/preload.
with-proxy cargo build --release -p reverie-examples \
  --bin counter1 --bin counter2 --bin strace \
  --bin reverie-kvm-counter1 --bin reverie-kvm-counter2 \
  --bin reverie-liteinst-examples

# SaBRe loader, host, and plugin.
with-proxy scripts/backend-submodule.sh activate sabre
cmake -S third-party/sabre -B target/sabre
cmake --build target/sabre
with-proxy cargo build --release -p reverie-sabre-strace

# DynamoRIO, Rust coordinator/path helper, and native client.
with-proxy scripts/backend-submodule.sh activate dynamorio
PROFILE=release with-proxy reverie-dbi/scripts/build-client.sh
```

The first command produces `target/release/{counter1,counter2,strace}`,
`target/release/reverie-kvm-{counter1,counter2}`, and
`target/release/reverie-liteinst-examples` plus the adjacent
`libreverie_examples.so` preload. The SaBRe commands produce
`target/sabre/sabre`, `target/release/reverie-sabre-strace`, and
`target/release/libreverie_sabre_strace_plugin.so`. The DBI script first builds
`reverie-dbi` (including `reverie-dbi-counter2` and
`reverie-dbi-dynamorio-path`), then CMake-builds
`target/release/reverie-dbi-native/libreverie_dbi_client.so` against the pinned
DynamoRIO install reported by the path helper. The source gitlinks were
DynamoRIO `929840ad9190e5086775e8debc0f0b79b4208d59`, SaBRe
`34065e7ddae6f1c90db7e0bf5c22a9aa89f9d605`, and inactive e9patch
`6c2c03c1da74b14daf1788a9f8dccfa354ce04a6`. The two C fixtures and their
compiler commands/digests are recorded in the benchmark section.

## Tools x backends matrix

| Backend | counter1 | counter2 | strace | Qualification |
| --- | --- | --- | --- | --- |
| ptrace | PASS | PASS | PASS | Exact shared tools; `/bin/echo` counters both 40. |
| KVM | PASS (later rerun) | PASS (later rerun) | PASS (later rerun) | Originally blocked; reviewer later observed `/dev/kvm` and all three passed on the same static guest. |
| DBI | PASS | PASS | ADAPTED PASS | Exact counter selectors use a `bash` guest; strace is a DBI-specific mirror, not the shared strace source. |
| SaBRe | PASS | PASS | PASS | Exact selectors are `counter1-exact` and `counter2-exact`; startup coverage differs from ptrace. |
| LiteInst | PASS | PASS | PASS plus one observed teardown-owner failure (N=2) | Compact strace passed once; one preceding identical run failed during coordinator teardown. This sample does not establish a flake rate. |
| e9patch | FAIL (missing) | FAIL (missing) | FAIL (missing) | No example-tool CLI exists. The backend API can host arbitrary `Tool`, but no requested runner is published. |

### Ptrace raw evidence

```text
$ timeout 30 target/release/counter1 --no-host-envs /bin/echo hello 2>&1
hello
counter1-global syscalls=40

$ timeout 30 target/release/counter2 --no-host-envs /bin/echo hello 2>&1
hello
counter2-local thread=1012604 syscalls=40
 [counter tool] Total system calls in process tree: 40, from 1 processes, 1 thread(s).

$ timeout 30 target/release/strace --runner ptrace --no-host-envs --trace write /tmp/backend-architecture-report/hello-minimal-dynamic 2>&1
hello\[pid 284287] write(1, 0x402000, 6) = 6
Thread 284287 exited with status Exited(0)
Process 284287 exited with status Exited(0)
```

All commands exited 0. The compact assembly guest intentionally wrote the six
bytes `hello\` (the source contained a literal backslash before `n`).

### KVM raw evidence

The guest was a statically linked x86-64 ELF, SHA-256
`1bdf95ee26a9206cae3c9cf3f9c8d338ab1f8951ec1eacac6e2ea02b0d87f21b`.

```text
$ timeout 30 target/release/reverie-kvm-counter1 /tmp/backend-architecture-report/hello-static 2>&1
reverie-kvm-counter1: KVM operation failed: No such file or directory (os error 2)

$ timeout 30 target/release/reverie-kvm-counter2 /tmp/backend-architecture-report/hello-static 2>&1
reverie-kvm-counter2: KVM operation failed: No such file or directory (os error 2)

$ timeout 30 target/release/strace --runner kvm --no-host-envs --trace write /tmp/backend-architecture-report/hello-static 2>&1
Error: KVM operation failed: No such file or directory (os error 2)

Caused by:
    No such file or directory (os error 2)
```

All three exited 1. This is an environment BLOCKED result, not evidence that
the tool adapters fail on a KVM-capable host.

That result is historical rather than the final capability observation. At
2026-07-28 03:34 UTC the adversarial reviewer observed `/dev/kvm`, reused the
same Reverie SHA, release binaries, and static fixture, and reran:

```text
timeout 30 target/release/reverie-kvm-counter1 /tmp/backend-architecture-report/hello-static
timeout 30 target/release/reverie-kvm-counter2 /tmp/backend-architecture-report/hello-static
timeout 30 target/release/strace --runner kvm --no-host-envs --trace write /tmp/backend-architecture-report/hello-static
```

The review task records exit 0 for all three, with 17 observed syscalls for
`counter1`, 17 syscalls from one process/one thread for `counter2`, and a
successful strace run. This is a dated capability-window rerun, not a claim
that KVM remained available: `/dev/kvm` was absent again when checked at
2026-07-28 03:38 UTC. The original failure output remains above so host
capability changes are not hidden.

### LiteInst raw evidence

```text
$ timeout 30 target/release/reverie-liteinst-examples --tool counter1 -- /bin/echo hello 2>&1
hello
 [counter tool] Total system calls in process tree: 79

$ timeout 30 target/release/reverie-liteinst-examples --tool counter2 -- /bin/echo hello 2>&1
hello
 [counter tool] Total system calls in process tree: 79, from 1 processes, 1 thread(s).

$ timeout 30 target/release/reverie-liteinst-examples --tool strace --trace write -- /tmp/backend-architecture-report/hello-minimal-dynamic 2>&1
[pid 306743] write(1, 0x402000, 6) = 6
Thread 306743 exited with status Exited(0)
Process 306743 exited with status Exited(0)
hello\
```

These exited 0. One preceding identical `/bin/echo` strace run exited 1 with:

```text
Error: LiteInst coordinator state still has owners after connection shutdown
```

An immediate repeat exited 0 and printed the expected write. The failure text
comes directly from `reverie-liteinst/src/backend.rs:355-369`. The observation
is therefore one pass plus one teardown-owner failure (`N=2`); two trials do
not quantify a flake rate.

### SaBRe raw evidence

```text
$ timeout 30 target/release/reverie-sabre-strace --sabre target/sabre/sabre --plugin target/release/libreverie_sabre_strace_plugin.so --tool counter1-exact -- /bin/echo hello 2>&1
hello
counter1-global syscalls=100

$ timeout 30 target/release/reverie-sabre-strace --sabre target/sabre/sabre --plugin target/release/libreverie_sabre_strace_plugin.so --tool counter2-exact -- /bin/echo hello 2>&1
hello
counter2-local thread=1352761 syscalls=101
counter2-global syscalls=101 processes=1 threads=1

$ timeout 30 target/release/reverie-sabre-strace --sabre target/sabre/sabre --plugin target/release/libreverie_sabre_strace_plugin.so --tool strace -- /tmp/backend-architecture-report/hello-minimal-dynamic 2>&1
[318450] getrandom(0x7ffec62eb710, 32, 1) = Ok(32)
hello\[318450] write(1, 0x402000, 6) = Ok(6)
[318450] exit(0)
```

All exited 0. SaBRe sees its own plugin initialization `getrandom`, and begins
guest interception after its loader is established, so its totals are not
numerically comparable to ptrace startup totals.

### DBI raw evidence

The path helper reported:

```text
drrun=$HOME/work/dev-hermit/worktrees/270/reverie/target/release/build/reverie-dbi-fa86f460bfce05f3/out/dynamorio-install/bin64/drrun
home=$HOME/work/dev-hermit/worktrees/270/reverie/target/release/build/reverie-dbi-fa86f460bfce05f3/out/dynamorio-install
client=$HOME/work/dev-hermit/worktrees/270/reverie/target/release/reverie-dbi-native/libreverie_dbi_client.so
```

```text
$ drrun=$(target/release/reverie-dbi-dynamorio-path drrun); client=$PWD/target/release/reverie-dbi-native/libreverie_dbi_client.so; timeout 30 env HERMIT_DBI_COUNTER1_EXACT=1 "$drrun" -quiet -disable_rseq -stack_size 2M -c "$client" -- /bin/bash -c 'echo hello; true' 2>&1
hello
counter1-global syscalls=177

$ drrun=$(target/release/reverie-dbi-dynamorio-path drrun); client=$PWD/target/release/reverie-dbi-native/libreverie_dbi_client.so; timeout 30 env HERMIT_DBI_COUNTER2_EXACT=1 "$drrun" -quiet -disable_rseq -stack_size 2M -c "$client" -- /bin/bash -c 'echo hello; true' 2>&1
hello
counter2-local thread=35 syscalls=177
 [counter2 exact] Process-local system calls: 177, exited threads: 1
```

Both exact shared-tool selectors exited 0. The production counter2 benchmark
below uses the separate `reverie-dbi-counter2` coordinator host; it requires
`DYNAMORIO_HOME` and `REVERIE_DBI_CLIENT`, and omitting either produced an exact
`NotFound` error.

Compact strace command:

```text
$ drrun=$(target/release/reverie-dbi-dynamorio-path drrun); client=$PWD/target/release/reverie-dbi-native/libreverie_dbi_client.so; timeout 30 env HERMIT_DBI_STRACE=1 "$drrun" -quiet -disable_rseq -stack_size 2M -c "$client" -- /tmp/backend-architecture-report/hello-minimal-dynamic 2>&1
[dbi strace pid 35] brk(NULL) = ?
[dbi strace pid 35] arch_prctl(12289, 0x7ffdfc48d6f0) = ?
[dbi strace pid 35] mmap(NULL, 8192, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = ?
[dbi strace pid 35] access(0x7fe7f64dfe50 -> "/etc/ld.so.preload", Mode(S_IROTH)) = ?
[dbi strace pid 35] openat(-100, 0x7fe7f64de266 -> "/etc/ld.so.cache", OFlag(O_CLOEXEC)) = ?
[dbi strace pid 35] fstat(3, 0x7ffdfc48c920) = ?
[dbi strace pid 35] mmap(NULL, 30779, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE), 3, 0) = ?
[dbi strace pid 35] close(3) = ?
[dbi strace pid 35] openat(-100, 0x7fe7f6433750 -> "/lib64/libc.so.6", OFlag(O_CLOEXEC)) = ?
[dbi strace pid 35] read(3, 0x7ffdfc48ca88, 832) = ?
[dbi strace pid 35] pread64(3, 0x7ffdfc48c680, 784, 64) = ?
[dbi strace pid 35] pread64(3, 0x7ffdfc48c640, 48, 848) = ?
[dbi strace pid 35] pread64(3, 0x7ffdfc48c5f0, 68, 896) = ?
[dbi strace pid 35] fstat(3, 0x7ffdfc48c920) = ?
[dbi strace pid 35] pread64(3, 0x7ffdfc48c570, 784, 64) = ?
[dbi strace pid 35] mmap(NULL, 2138064, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_DENYWRITE), 3, 0) = ?
[dbi strace pid 35] mmap(0x7fe5f5829000, 1527808, ProtFlags(PROT_READ | PROT_EXEC), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 167936) = ?
[dbi strace pid 35] mmap(0x7fe5f599e000, 364544, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 1695744) = ?
[dbi strace pid 35] mmap(0x7fe5f59f7000, 24576, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 2056192) = ?
[dbi strace pid 35] mmap(0x7fe5f59fd000, 53200, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_ANON), -1, 0) = ?
[dbi strace pid 35] close(3) = ?
[dbi strace pid 35] mmap(NULL, 12288, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = ?
[dbi strace pid 35] arch_prctl(ARCH_SET_FS, 140634245760832) = ?
[dbi strace pid 35] set_tid_address(0x7fe7f6430a10) = ?
[dbi strace pid 35] set_robust_list(0x7fe7f6430a20, 24) = ?
[dbi strace pid 35] rseq(140634245763296, 32, 0, 1392848979, 0, 0) = ?
[dbi strace pid 35] mprotect(0x7fe5f59f7000, 16384, ProtFlags(PROT_READ)) = ?
[dbi strace pid 35] mprotect(0x403000, 4096, ProtFlags(PROT_READ)) = ?
[dbi strace pid 35] mprotect(0x7fe7f64e8000, 8192, ProtFlags(PROT_READ)) = ?
[dbi strace pid 35] prlimit64(0, 3, NULL, 0x7ffdfc48d460) = ?
[dbi strace pid 35] munmap(0x7fe5f60f8000, 30779) = ?
[dbi strace pid 35] write(1, 0x402000, 6) = ?
hello\[dbi strace pid 35] exit(0) = ?
```

Exit 0. DBI currently prints entry-side `?` rather than syscall return values.

### e9patch raw evidence

```text
$ cargo run -p reverie-e9patch --bin counter1 -- /bin/echo hello 2>&1
error: no bin target named `counter1` in `reverie-e9patch` package
help: available bin in `reverie-examples` package:
    counter1

$ cargo run -p reverie-e9patch --bin counter2 -- /bin/echo hello 2>&1
error: no bin target named `counter2` in `reverie-e9patch` package
help: available bin in `reverie-examples` package:
    counter2

$ cargo run -p reverie-e9patch --bin strace -- /bin/echo hello 2>&1
error: no bin target named `strace` in `reverie-e9patch` package
help: available bin in `reverie-examples` package:
    strace
```

Each exited 101. This is a missing integration surface, not proof that the
generic `E9patchBackend::run<T>` cannot host those tools after a runner is added.

## Counter2 fork-tree benchmark

### Workload design

The dynamic fixture SHA-256 is
`64cda892fd1505808aba43d88a0775df719f16defd5151b98bd11bd8cce0fd57`.
It creates three children. Parent and children each issue exactly 10,000 raw
`SYS_getpid` calls. `SIGCHLD` is ignored and a pipe barrier uses exactly three
one-byte writes and three one-byte reads, avoiding interruptible `waitpid`
retries. Source:

```c
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <sys/syscall.h>
#include <unistd.h>

enum { CHILDREN = 3, ITERATIONS = 10000 };

static int spin(void) {
    for (int i = 0; i < ITERATIONS; ++i)
        if (syscall(SYS_getpid) <= 0) return 1;
    return 0;
}

int main(void) {
    struct sigaction action = {.sa_handler = SIG_IGN};
    int barrier[2];
    if (sigaction(SIGCHLD, &action, NULL) < 0 || pipe2(barrier, O_CLOEXEC) < 0)
        return 2;
    for (int i = 0; i < CHILDREN; ++i) {
        pid_t child = fork();
        if (child == 0) {
            close(barrier[0]);
            int failed = spin();
            char done = 1;
            if (write(barrier[1], &done, 1) != 1) failed = 1;
            _exit(failed);
        }
        if (child < 0) return 2;
    }
    int failed = spin();
    close(barrier[1]);
    for (int i = 0; i < CHILDREN; ++i) {
        char done = 0;
        if (read(barrier[0], &done, 1) != 1 || done != 1) failed = 1;
    }
    close(barrier[0]);
    return failed;
}
```

Fixture build commands and output:

```text
$ cc -O2 -Wall -Wextra -Werror fork_spin.c -o fork-spin-dynamic && cc -O2 -static -Wall -Wextra -Werror fork_spin.c -o fork-spin-static && sha256sum fork-spin-dynamic fork-spin-static
64cda892fd1505808aba43d88a0775df719f16defd5151b98bd11bd8cce0fd57  fork-spin-dynamic
a39320898c6cea64c8a6bbb40c7d122d5d5af60af831bed6b3c1da3f0b90e311  fork-spin-static
```

Validation command:

```text
for i in 1 2 3; do echo "trial=$i"; strace -f -qq -c /tmp/backend-architecture-report/fork-spin-dynamic; done 2>&1
```

Every trial reported exactly `40051 ... total`, including exactly 40,000
`getpid`, 3 `clone`, 3 `write`, 4 `read`, and 7 `close` calls. Thus the workload
meets the requested normal-strace stability criterion.

Concise raw output (the command filters only the named rows from normal
`strace -f -c` output):

```text
$ for i in 1 2 3; do echo "trial=$i"; strace -f -qq -c /tmp/backend-architecture-report/fork-spin-dynamic 2>&1 | awk '$NF == "getpid" || $NF == "clone" || $NF == "read" || $NF == "write" || $NF == "close" || $NF == "total"'; done
trial=1
 99.09    0.089079           2     40000           getpid
  0.87    0.000778         259         3           write
  0.04    0.000034           8         4           read
  0.01    0.000009           1         7           close
  0.00    0.000000           0         3           clone
100.00    0.089900           2     40051         2 total
trial=2
 98.82    0.098519           2     40000           getpid
  0.24    0.000235          78         3           clone
  0.10    0.000095          31         3           write
  0.02    0.000022           5         4           read
  0.01    0.000013           1         7           close
100.00    0.099695           2     40051         2 total
trial=3
 98.66    0.087947           2     40000           getpid
  0.78    0.000695         231         3           clone
  0.36    0.000325         108         3           write
  0.03    0.000024           6         4           read
  0.02    0.000019           2         7           close
100.00    0.089138           2     40051         2 total
```

The unfiltered command used first was identical without the `awk` stage. Its
three total rows were also `40051`; the filtered rerun above preserves every
count used by this report while omitting only unrelated fixed startup rows and
timing columns. No count claim depends on omitted output.

Full unfiltered output is retained here for auditability:

```text
trial=1
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 99.74    0.090430           2     40000           getpid
  0.20    0.000183          61         3           clone
  0.02    0.000021           3         7           close
  0.02    0.000017           4         4           read
  0.02    0.000014           4         3           write
  0.00    0.000000           0         2           fstat
  0.00    0.000000           0         8           mmap
  0.00    0.000000           0         3           mprotect
  0.00    0.000000           0         1           munmap
  0.00    0.000000           0         1           brk
  0.00    0.000000           0         1           rt_sigaction
  0.00    0.000000           0         4           pread64
  0.00    0.000000           0         1         1 access
  0.00    0.000000           0         1           execve
  0.00    0.000000           0         2         1 arch_prctl
  0.00    0.000000           0         1           set_tid_address
  0.00    0.000000           0         2           openat
  0.00    0.000000           0         4           set_robust_list
  0.00    0.000000           0         1           pipe2
  0.00    0.000000           0         1           prlimit64
  0.00    0.000000           0         1           rseq
------ ----------- ----------- --------- --------- ----------------
100.00    0.090665           2     40051         2 total
trial=2
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 99.23    0.072155           1     40000           getpid
  0.54    0.000391         391         1           execve
  0.08    0.000060           7         8           mmap
  0.04    0.000032          16         2           openat
  0.03    0.000022           7         3           write
  0.02    0.000017           4         4           read
  0.02    0.000017           2         7           close
  0.02    0.000011           2         4           pread64
  0.01    0.000004           2         2           fstat
  0.01    0.000004           4         1           brk
  0.00    0.000003           3         1         1 access
  0.00    0.000002           1         2         1 arch_prctl
  0.00    0.000000           0         3           mprotect
  0.00    0.000000           0         1           munmap
  0.00    0.000000           0         1           rt_sigaction
  0.00    0.000000           0         3           clone
  0.00    0.000000           0         1           set_tid_address
  0.00    0.000000           0         4           set_robust_list
  0.00    0.000000           0         1           pipe2
  0.00    0.000000           0         1           prlimit64
  0.00    0.000000           0         1           rseq
------ ----------- ----------- --------- --------- ----------------
100.00    0.072718           1     40051         2 total
trial=3
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 99.95    0.074881           1     40000           getpid
  0.02    0.000018           4         4           read
  0.02    0.000013           4         3           write
  0.01    0.000006           0         7           close
  0.00    0.000000           0         2           fstat
  0.00    0.000000           0         8           mmap
  0.00    0.000000           0         3           mprotect
  0.00    0.000000           0         1           munmap
  0.00    0.000000           0         1           brk
  0.00    0.000000           0         1           rt_sigaction
  0.00    0.000000           0         4           pread64
  0.00    0.000000           0         1         1 access
  0.00    0.000000           0         3           clone
  0.00    0.000000           0         1           execve
  0.00    0.000000           0         2         1 arch_prctl
  0.00    0.000000           0         1           set_tid_address
  0.00    0.000000           0         2           openat
  0.00    0.000000           0         4           set_robust_list
  0.00    0.000000           0         1           pipe2
  0.00    0.000000           0         1           prlimit64
  0.00    0.000000           0         1           rseq
------ ----------- ----------- --------- --------- ----------------
100.00    0.074918           1     40051         2 total
```

### Results

Exact benchmark commands:

```text
# ptrace
for i in 1 2 3; do echo "trial=$i"; /usr/bin/time -f 'wall_seconds=%e' timeout 120 target/release/counter2 --no-host-envs /tmp/backend-architecture-report/fork-spin-dynamic; done 2>&1

# SaBRe
for i in 1 2 3; do echo "trial=$i"; /usr/bin/time -f 'wall_seconds=%e' timeout 120 target/release/reverie-sabre-strace --sabre target/sabre/sabre --plugin target/release/libreverie_sabre_strace_plugin.so --tool counter2-exact -- /tmp/backend-architecture-report/fork-spin-dynamic; done 2>&1

# DBI
home=$(target/release/reverie-dbi-dynamorio-path home); client=$PWD/target/release/reverie-dbi-native/libreverie_dbi_client.so; for i in 1 2 3; do echo "trial=$i"; /usr/bin/time -f 'wall_seconds=%e' timeout 120 env DYNAMORIO_HOME="$home" REVERIE_DBI_CLIENT="$client" target/release/reverie-dbi-counter2 -- /tmp/backend-architecture-report/fork-spin-dynamic; done 2>&1

# LiteInst
/usr/bin/time -f 'wall_seconds=%e' timeout 120 target/release/reverie-liteinst-examples --tool counter2 -- /tmp/backend-architecture-report/fork-spin-dynamic 2>&1

# KVM
/usr/bin/time -f 'wall_seconds=%e' timeout 120 target/release/reverie-kvm-counter2 /tmp/backend-architecture-report/fork-spin-static 2>&1

# e9patch
/usr/bin/time -f 'wall_seconds=%e' cargo run -p reverie-e9patch --bin counter2 -- /tmp/backend-architecture-report/fork-spin-dynamic 2>&1
```

| Backend | Totals (3 trials) | Wall seconds | Result |
| --- | --- | --- | --- |
| ptrace | 40,055 / 40,055 / 40,055; 4 proc, 4 threads | 0.22 / 0.23 / 0.24 | Complete tree; historical launcher samples |
| SaBRe | 40,042 / 40,042 / 40,042; 4 proc, 4 threads | 0.16 / 0.07 / 0.07 | Complete tree; first-trial warmup outlier; historical launcher samples |
| DBI | 10,042 / 10,042 / 10,042; 1 proc, 1 thread | 0.06 / 0.06 / 0.06 | Root only; children not delivered to Rust tool |
| LiteInst | 4; 1 proc, 1 thread, exit 2 | 0.02 | `fork` failed |
| KVM | 40,035; 4 proc, 4 threads (later reviewer rerun) | not recorded | Complete tree during transient `/dev/kvm` window; one aggregate sample |
| e9patch | no run | n/a | no counter2 runner |

Raw ptrace output:

```text
trial=1
counter2-local thread=2715512 syscalls=10004
counter2-local thread=2715516 syscalls=10004
counter2-local thread=2715520 syscalls=10004
counter2-local thread=2715470 syscalls=10043
 [counter tool] Total system calls in process tree: 40055, from 4 processes, 4 thread(s).
wall_seconds=0.22
trial=2
counter2-local thread=2717117 syscalls=10004
counter2-local thread=2717114 syscalls=10004
counter2-local thread=2717111 syscalls=10004
counter2-local thread=2717073 syscalls=10043
 [counter tool] Total system calls in process tree: 40055, from 4 processes, 4 thread(s).
wall_seconds=0.23
trial=3
counter2-local thread=2718834 syscalls=10004
counter2-local thread=2718842 syscalls=10004
counter2-local thread=2718839 syscalls=10004
counter2-local thread=2718790 syscalls=10043
 [counter tool] Total system calls in process tree: 40055, from 4 processes, 4 thread(s).
wall_seconds=0.24
```

Raw SaBRe output:

```text
trial=1
counter2-local thread=2748182 syscalls=10004
counter2-local thread=2748185 syscalls=10004
counter2-local thread=2748181 syscalls=10004
counter2-local thread=2747862 syscalls=10030
counter2-global syscalls=40042 processes=4 threads=4
wall_seconds=0.16
trial=2
counter2-local thread=2748712 syscalls=10004
counter2-local thread=2748714 syscalls=10004
counter2-local thread=2748717 syscalls=10004
counter2-local thread=2748381 syscalls=10030
counter2-global syscalls=40042 processes=4 threads=4
wall_seconds=0.07
trial=3
counter2-local thread=2749097 syscalls=10004
counter2-local thread=2749098 syscalls=10004
counter2-local thread=2749102 syscalls=10004
counter2-local thread=2748789 syscalls=10030
counter2-global syscalls=40042 processes=4 threads=4
wall_seconds=0.07
```

Raw DBI, LiteInst, and original KVM output:

```text
trial=1
reverie-dbi: counter2 global system calls: 10042, from 1 processes, 1 thread(s)
wall_seconds=0.06
trial=2
reverie-dbi: counter2 global system calls: 10042, from 1 processes, 1 thread(s)
wall_seconds=0.06
trial=3
reverie-dbi: counter2 global system calls: 10042, from 1 processes, 1 thread(s)
wall_seconds=0.06

counter2-local thread=2857020 syscalls=4
 [counter tool] Total system calls in process tree: 4, from 1 processes, 1 thread(s).
Command exited with non-zero status 2
wall_seconds=0.02

reverie-kvm-counter2: KVM operation failed: No such file or directory (os error 2)
Command exited with non-zero status 1
wall_seconds=0.00

error: no bin target named `counter2` in `reverie-e9patch` package
help: available bin in `reverie-examples` package:
    counter2
Command exited with non-zero status 101
wall_seconds=0.10
```

The later KVM rerun used the same command shown above and the static fixture
with SHA-256
`a39320898c6cea64c8a6bbb40c7d122d5d5af60af831bed6b3c1da3f0b90e311`.
The review task recorded this aggregate result (the per-process local rows and
wall time were not retained, so they are not reconstructed here):

```text
review-task aggregate: 40035 syscalls; 4 processes; 4 threads; exit 0
```

LiteInst failure attribution was checked with:

```text
$ timeout 30 target/release/reverie-liteinst-examples --tool strace --trace rt_sigaction --trace pipe2 --trace clone -- /tmp/backend-architecture-report/fork-spin-dynamic 2>&1
[pid 3666637] rt_sigaction(17, 0x7ffe2befa630, NULL, 8) = 0
[pid 3666637] pipe2(0x7ffe2befa788, OFlag(O_CLOEXEC)) = 0
reverie-liteinst: clone/fork injection is unsupported
[pid 3666637] clone(CloneFlags(0x1200011), NULL, NULL, 0x7f8236d4b150, 0) = -95
Thread 3666637 exited with status Exited(2)
Process 3666637 exited with status Exited(2)
```

Thus `sigaction` and `pipe2` succeeded; `clone` returned `-EOPNOTSUPP` and
caused the fixture's exit 2.

The wall-time figures are historical, exploratory launcher-level observations,
not a backend throughput ranking or a latency interval. The runs used only
three trials, `/usr/bin/time` at 0.01 s display precision, no CPU pinning, and
no host-load control; SaBRe's first sample is an evident warmup outlier. DBI and
LiteInst did less work, e9patch did none, and the later KVM rerun did not retain
a wall time. Even ptrace, SaBRe, and KVM, which covered all four processes and
40,000 spin calls, observed different total boundaries (40,055, 40,042, and
40,035). The numbers support coverage-boundary auditing only; they do not
support relative throughput or latency claims.

## Local to global Tool RPC

### Contract and ptrace/KVM path

`reverie/src/tool.rs:39-63` defines `GlobalTool::receive_rpc`; lines 334-338
define `GlobalRPC::send_rpc`. Ptrace does not use IPC:
`reverie-ptrace/src/task.rs:2765-2799` serializes in debug builds, then calls
the shared `gs_ref.receive_rpc` in the tracer address space. KVM likewise calls
`global_state.receive_rpc` directly (`reverie-kvm/src/runtime.rs:255-257` and
318-323). These backends share the interface, not a transport.

### Shared UDS transport

`reverie-rpc-transport/src/lib.rs:31-50` specifies raw UDS, big-endian
length-prefixed bincode legacy frames, and one request in flight per
connection. `server.rs:90-112` binds the UDS; `server.rs:214-227` decodes
`{from, request}`, invokes the one coordinator-owned `GlobalTool`, and encodes
the response. `client.rs:61-92` receives the config handshake and performs the
async request/response. `blocking_client.rs:30-41,73-86` provides the same wire
protocol synchronously for callbacks that must complete on their first poll.

- LiteInst: host creates the `RpcServer` and injects its socket path in a sealed
  memfd (`reverie-liteinst/src/backend.rs:45-185,395-443`). The preload connects
  before seccomp (`reverie-liteinst/src/rpc.rs:67-109`). It duplicates the
  request envelope, bincode codec, and framing in
  `reverie-preload/src/rpc.rs:62-72,170-180,246-267`, then uses trusted-gate
  send/read syscalls at lines 137-167 and 183-244. Generic Tool callbacks and
  their blocking RPC do **not** run in the SIGSYS handler: the handler installs
  or selects a hook and rewrites saved RIP, then `tool_trampoline` executes the
  Tool after `sigreturn` in normal guest context
  (`reverie-liteinst/src/runtime.rs:746-792,851-888`).
- SaBRe has two UDS transport generations. The exact shared tools in this
  report use `RemoteReverieAdapter`: each guest thread lazily creates a
  `BlockingRpcClient` keyed by TID/socket path
  (`experimental/reverie-sabre/src/reverie_adapter.rs:350-390`). The separate
  legacy `BaseChannel` transport uses `REVERIE_SOCK`, protects fd 100 across
  exec, and reconnects after fork
  (`experimental/reverie-sabre/src/rpc.rs:28-120`). The fd-100 behavior must not
  be attributed to the exact remote-tool transport.
- DBI: the coordinator server is shared, but `reverie-dbi/src/sync_rpc.rs:1-32`
  documents a wire-compatible duplicate blocking client. Lines 122-171 keep a
  per-thread/process connection; 175-239 use injected guest syscalls because
  ordinary Rust sockets/TLS are unsafe under DynamoRIO's private loader.
- e9patch: no special UDS path. `E9patchBackend` is a `reverie-ptrace` tracer,
  so Tool RPC uses ptrace's in-process global state.

There is deliberate duplication at both the LiteInst preload and DBI guest
boundaries. Factoring the envelope/codec/framing into a no-Tokio core crate
would reduce drift, but their I/O must remain backend-specific: LiteInst must
use its trusted gate from normal post-signal Tool context, while DBI uses
injected guest syscalls inside DynamoRIO. SaBRe's remote tools can continue
using the shared `BlockingRpcClient`; its legacy channel is a separate
compatibility surface.

Requested UDS/shared-memory grep:

```text
$ rg -n -i 'UnixStream|UnixListener|memfd_create|shared memory|\bshm\b|\bmmap\b' reverie-rpc-transport reverie-liteinst/src/backend.rs reverie-preload/src/rpc.rs reverie-dbi/src/sync_rpc.rs experimental/reverie-sabre/src/rpc.rs
reverie-preload/src/rpc.rs:51:use std::os::unix::net::UnixStream;
reverie-preload/src/rpc.rs:85:        let mut stream = UnixStream::connect(path)?;
reverie-liteinst/src/backend.rs:171:        libc::memfd_create(
reverie-dbi/src/sync_rpc.rs:38:use std::os::unix::net::UnixStream;
reverie-dbi/src/sync_rpc.rs:107:        let mut stream = UnixStream::connect(path).unwrap_or_else(|error| {
experimental/reverie-sabre/src/rpc.rs:13:use std::os::unix::net::UnixStream;
experimental/reverie-sabre/src/rpc.rs:96:                Some(path) => (UnixStream::connect(path)?, false),
reverie-rpc-transport/src/server.rs:26:use tokio::net::UnixListener;
reverie-rpc-transport/src/server.rs:27:use tokio::net::UnixStream;
reverie-rpc-transport/src/server.rs:104:        let listener = UnixListener::bind(&path)?;
reverie-rpc-transport/src/client.rs:25:use tokio::net::UnixStream;
reverie-rpc-transport/src/client.rs:62:        let mut stream = UnixStream::connect(path.as_ref()).await?;
reverie-rpc-transport/src/blocking_client.rs:15:use std::os::unix::net::UnixStream;
reverie-rpc-transport/src/blocking_client.rs:57:        let mut stream = UnixStream::connect(path)?;
```

The complete grep produced more `UnixStream` test/framing matches but no `shm`
or `mmap` match in these RPC paths. The sole `memfd_create` is LiteInst's sealed
bootstrap containing the UDS path and tool configuration; RPC messages
themselves travel over UDS, not shared memory.

### RPC microbenchmark

A disposable release binary used the actual `RpcServer` and `RpcClient`, one
connection, 1,000 warmups, then 100,000 sequential `u64 -> u64` round trips.
It was built with:

```toml
[package]
name = "reverie-rpc-bench"
version = "0.1.0"
edition = "2021"

[dependencies]
async-trait = "0.1"
reverie = { path = "$HOME/work/dev-hermit/worktrees/270/reverie/reverie" }
reverie-rpc-transport = { path = "$HOME/work/dev-hermit/worktrees/270/reverie/reverie-rpc-transport" }
tokio = { version = "1.52.4", features = ["macros", "rt", "time"] }

[workspace]
```

Complete benchmark source:

```rust
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;
use async_trait::async_trait;
use reverie::{GlobalRPC, GlobalTool, Tid};
use reverie_rpc_transport::{RpcClient, RpcServer};

const WARMUP: u64 = 1_000;
const MEASURED: u64 = 100_000;

#[derive(Default)]
struct Counter(AtomicU64);

#[async_trait]
impl GlobalTool for Counter {
    type Request = u64;
    type Response = u64;
    type Config = ();
    async fn receive_rpc(&self, _from: Tid, increment: u64) -> u64 {
        self.0.fetch_add(increment, Ordering::Relaxed) + increment
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let path = std::env::temp_dir().join(format!(
        "reverie-rpc-bench-{}.sock", std::process::id()
    ));
    let global = Arc::new(Counter::default());
    let server = RpcServer::bind(&path, global.clone(), ()).unwrap();
    let server_path = server.path().to_path_buf();
    let server_task = tokio::spawn(async move { server.serve().await });
    let client = RpcClient::<Counter>::connect(&server_path, Tid::from_raw(7))
        .await.unwrap();
    for _ in 0..WARMUP { client.send_rpc(1).await; }
    let start = Instant::now();
    for _ in 0..MEASURED { client.send_rpc(1).await; }
    let elapsed = start.elapsed();
    println!("rpc_round_trips={MEASURED}");
    println!("elapsed_ns={}", elapsed.as_nanos());
    println!("ns_per_rpc={}", elapsed.as_nanos() / u128::from(MEASURED));
    println!("final_total={}", global.0.load(Ordering::Relaxed));
    server_task.abort();
}
```

Build command and full terminal summary:

```text
$ with-proxy cargo build --release
   Compiling reverie v0.1.0 ($HOME/work/dev-hermit/worktrees/270/reverie/reverie)
   Compiling reverie-rpc-transport v0.1.0 ($HOME/work/dev-hermit/worktrees/270/reverie/reverie-rpc-transport)
   Compiling reverie-rpc-bench v0.1.0 (/tmp/backend-architecture-report/rpc-bench)
    Finished `release` profile [optimized] target(s) in 11.27s
```

```text
$ for i in 1 2 3; do echo "trial=$i"; target/release/reverie-rpc-bench; done
trial=1
rpc_round_trips=100000
elapsed_ns=652811268
ns_per_rpc=6528
final_total=101000
trial=2
rpc_round_trips=100000
elapsed_ns=639796447
ns_per_rpc=6397
final_total=101000
trial=3
rpc_round_trips=100000
elapsed_ns=652991641
ns_per_rpc=6529
final_total=101000
```

These are three historical samples from one run, not endpoints of a stable
6,397-6,529 ns latency interval. The adversarial reviewer reran the same
100,000-call benchmark and recorded:

```text
trial=1 ns_per_rpc=6414 final_total=101000
trial=2 ns_per_rpc=6571 final_total=101000
trial=3 ns_per_rpc=6942 final_total=101000
```

The rerun is the same order of magnitude but exceeds the original narrow
range. Neither set used CPU pinning or host-load control, and six total samples
are insufficient for a latency distribution. This benchmark measures a
same-process `RpcServer`/`RpcClient` UDS+bincode request/response baseline; it
does not measure fork contention, Tool handler work, or any backend's complete
RPC path (KVM/ptrace are direct calls, while SaBRe/LiteInst/DBI have distinct
guest-side I/O constraints).

## In-guest signal handling

### LiteInst (LD_PRELOAD)

The host sets `LD_PRELOAD`, binds a coordinator UDS, and passes the socket/tool
bootstrap in a sealed memfd (`reverie-liteinst/src/backend.rs:372-443`). The
preload connects RPC before installing a classic-BPF seccomp filter. The
generic Tool path is split deliberately across signal and normal contexts:

1. Seccomp returns `SECCOMP_RET_TRAP` for every syscall except
   `rt_sigreturn` and the trusted syscall gate
   (`reverie-preload/src/seccomp.rs:9-23,76-100`).
2. Linux delivers thread-directed `SIGSYS`.
3. `sigsys_handler` validates provenance and a reentrancy guard, decodes the
   syscall from `ucontext`, and invokes the registered dispatcher
   (`reverie-preload/src/trap.rs:139-188`).
4. On the first recoverable site, `LiteinstDispatcher` asks `liteinst2` to
   install a replacement hook. If the hook is active, `defer_to` rewrites the
   saved RIP to its generated trampoline
   (`reverie-liteinst/src/runtime.rs:486-555,851-869`). The signal handler then
   returns; it does not run the arbitrary generic Tool or blocking RPC.
5. After kernel `sigreturn`, `tool_trampoline` invokes the Tool in normal guest
   context, where allocation, locking, blocking coordinator RPC, and trusted
   gate syscalls are permitted (`runtime.rs:746-792,879-936`). Later executions
   enter the installed hook directly rather than taking SIGSYS first.
6. `SIGSYS` is reserved. Guest attempts to replace/block it or install callable
   handlers fail closed (`reverie-preload/src/signal.rs:9-29` and
   `reverie-liteinst/src/runtime.rs:595-738`).

If live patch installation fails, the site becomes `SITE_FALLBACK`; generic
Tool mode cannot safely execute the callback in SIGSYS, so the syscall returns
`EOPNOTSUPP` (`runtime.rs:851-875`). This is not a ptrace fallback. Likewise,
the `ForkHook`/`PassthroughDispatcher` code is compatibility scaffolding, not
the active generic Tool lifecycle: `LiteinstGuest` rejects injected
`clone`/`clone3`/`fork`/`vfork` with `EOPNOTSUPP`
(`reverie-liteinst/src/tool_host.rs:395-419,527-543`), and the README records
fork reconnect as unimplemented. The benchmark's exit 2 is direct evidence of
that boundary; no child reconnects RPC from signal context.

### SaBRe (in-process rewriting plus Hermit safety net)

Standalone Reverie SaBRe scans executable code for x86-64 `0f 05` syscall
sites. A normal site is replaced with a jump to `handle_syscall`; when the
rewriter cannot fit that detour, it writes SaBRe's `0f ff` (UD0) marker. The
loader's SIGILL path recognizes the marker, reconstructs the original syscall
frame, and calls the plugin. This is an in-process loader fallback, not ptrace
(`third-party/sabre/arch/x86_64/rewriter.c:112-338` and
`third-party/sabre/loader/loader.c`).

SaBRe also virtualizes mediated guest dispositions. `rt_sigaction` stores a
guest-facing action while retaining an internal action; the installed central
handler maps the signal, enters the signal guard, then dispatches the stored
guest action or the emulated default behavior
(`experimental/reverie-sabre/src/signal/mod.rs:30-73,140-159,203-382`). Catchable
standard signals use this central path; the implementation preserves default
ignore/stop/continue/terminate behavior but does not reproduce every Linux
signal detail. Synchronous faults such as SIGILL/SIGSEGV are excluded from the
generic central multiplexer; SIGILL is separately owned by the loader's UD0
fallback. This is why "central signal handling" does not mean every signal is
interchangeable or backend-neutral.

Hermit adds a separate safety net around that standalone runtime. At Hermit
`a62258e58544e33c8978f4ae2afa08b80f1dea87`, `run_sabre` invokes
`sabre_ptrace::run` (`hermit-cli/src/lib.rs:728-787`). The custom supervisor
uses `PTRACE_TRACEME`/`PTRACE_SYSCALL`, follows clone/fork/vfork/exec/exit, and
examines syscall-entry RIP (`hermit-cli/src/sabre_ptrace.rs:81-247,413-432`).
Only after the first complete coordinator RPC marks the plugin ready, a raw
`0f 05` in an untrusted mapping is replaced with `0f ff`, the current kernel
entry is suppressed, and RIP/result state is restored so the next execution
enters SaBRe's SIGILL handler (`sabre_ptrace.rs:180-225,250-270`). Ptrace does
not itself invoke Detcore or emulate the syscall; it converts a truly missed
raw site into the normal in-guest SaBRe path. Loader/plugin/shared-library and
bracket mappings are treated as trusted to avoid recursion.

### Reverie E9patchBackend (not LD_PRELOAD)

The task premise is incorrect for current code. e9patch rewrites recovered
root-ELF syscall sites to a C trampoline that loads magic RAX and executes
`int3` (`reverie-e9patch/runtime/syscall_trap.c:9-19`). The external
`reverie-ptrace::TracerBuilder` is configured with that marker/RIP
(`reverie-e9patch/src/backend.rs:151-162`). Ptrace remains attached for
lifecycle, shared-library syscalls, signals, timers, and arbitrary Guest
operations (`backend.rs:165-171`). There is no in-guest e9patch signal handler.

Evidence that the fallback is live:

```text
$ with-proxy cargo test -p reverie-e9patch non_elf_script_uses_ptrace_fallback -- --ignored --nocapture
running 1 test
:: Backend: e9patch hybrid; recovered_sites=0; patched_sites=0; b0_sites=0; event_source=ptrace; controller=ptrace; main_executable=non-ELF
test non_elf_script_uses_ptrace_fallback ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 6 filtered out; finished in 0.01s
```

Hermit's product CLI does **not** dispatch this backend. At Hermit `a62258e`,
`RunOpts::runtime_backend` maps selected e9patch to ptrace, performs its own
content-addressed preprocessing and read-only executable overlay, then calls
the ordinary `reverie_ptrace::TracerBuilder<Detcore>` path
(`hermit-cli/src/bin/hermit/run.rs:1418-1474,1969-2010,2188-2203,2278-2284`;
`hermit-cli/src/lib.rs:1167-1225`). Hermit's rewriter selects cached instruction
offsets and asks e9tool for `-P "before empty"`; it does not install Reverie's
magic-int3 payload (`hermit-cli/src/e9patch.rs:105-309`). Thus Reverie's
`E9patchBackend` demonstrates rewritten event provenance through injected
ptrace traps, while `hermit --backend e9patch` means validated preprocessing
plus ordinary ptrace Detcore. Neither is LD_PRELOAD, but they are not the same
execution path.

## Ptrace of last resort and sharing recommendation

Source audit command:

```text
rg -n "ptrace|PTRACE_|nix::sys::ptrace|reverie_ptrace|TracerBuilder" experimental/reverie-sabre reverie-liteinst reverie-e9patch third-party/sabre ../hermit/hermit-cli/src/sabre_ptrace.rs -g '*.rs' -g '*.c' -g '*.h' -g '*.S'
```

Relevant complete matches outside third-party syscall-name tables:

```text
reverie-e9patch/tests/backend.rs:226:#[ignore = "requires a ptrace-capable host"]
reverie-e9patch/src/backend.rs:29:use reverie_ptrace::Tracer;
reverie-e9patch/src/backend.rs:30:use reverie_ptrace::TracerBuilder;
reverie-e9patch/src/backend.rs:158:    TracerBuilder::<T>::new(command)
reverie-e9patch/src/backend.rs:165:/// Hybrid e9patch backend with ptrace lifecycle and full `Guest` semantics.
reverie-e9patch/src/backend.rs:171:/// e9patch event path, but it is not yet the planned ptrace-free fast path.
../hermit/hermit-cli/src/sabre_ptrace.rs:29:use nix::sys::ptrace;
../hermit/hermit-cli/src/sabre_ptrace.rs:98:        ptrace::syscall(self.root, None)
../hermit/hermit-cli/src/sabre_ptrace.rs:160:            ptrace::Options::PTRACE_O_EXITKILL
../hermit/hermit-cli/src/sabre_ptrace.rs:289:        libc::ptrace(libc::PTRACE_GET_SYSCALL_INFO, ...)
```

- The normal ptrace backend and standalone Reverie `E9patchBackend` use the
  production Rust `reverie-ptrace`/`safeptrace` stack. E9patch adds its
  magic-int3 register-frame classifier while that tracer owns lifecycle and
  non-rewritten events.
- Standalone Reverie SaBRe has no external tracer: its loader rewrites syscall
  instructions to `handle_syscall`, with its SIGILL/UD0 path for sites that the
  loader discovered but could not detour. Hermit SaBRe is different: the
  product wraps the loader in the custom `sabre_ptrace` supervisor described
  above, so truly missed raw sites in untrusted mappings are caught and
  converted to UD0 after RPC readiness.
- Hermit's supervisor uses `nix::sys::ptrace` for options, register access,
  memory access, resume, and events. It calls `libc::ptrace` directly only for
  `PTRACE_GET_SYSCALL_INFO`, because the documented nix 0.30.1 wrapper passes a
  null address (`sabre_ptrace.rs:282-301`). It does not use
  `reverie-ptrace`/`safeptrace` today.
- LiteInst has no active ptrace controller. Live patch failure returns
  `EOPNOTSUPP`; clone/fork is rejected in generic Tool mode. Its
  `HybridPtrace` type is unsupported scaffolding, not a fallback.

There is therefore no existing three-way ptrace component to factor. The
Hermit SaBRe loop and `reverie-ptrace` overlap in syscall-stop decoding,
clone/fork/exec/exit following, register/memory access, and resume mechanics;
those mechanics could be shared if `reverie-ptrace` exposed a raw-stop adapter
for SaBRe's readiness/mapping/site-patch classifier. The policy itself should
remain backend-specific: SaBRe suppresses one kernel entry and rewrites guest
opcodes, whereas e9patch reconstructs an injected register frame at a known
`int3`. LiteInst is not part of that refactor unless it gains a real lifecycle
ptracer.

## Priority gaps

1. Publish counter1/counter2/strace runners for e9patch or add a common example
   runner over the `Backend` trait.
2. Fix LiteInst generic Tool fork handling (benchmark exits 2 after four root
   syscalls), then run enough teardown repetitions to quantify the single
   owner-leak failure observed in two strace attempts.
3. Make DBI deliver the Rust Tool in copied fork children. Its advertised UDS
   reconnect path exists, but this benchmark observed only root process/tool
   state.
4. Repeat the KVM matrix on a stable `/dev/kvm` host and retain full raw output
   and timing metadata. The transient reviewer rerun establishes functional
   PASS for the three tools and full-tree counter2 coverage, but not persistent
   host availability or comparative performance.
5. Define a backend-neutral counting boundary (for example, marker-delimited
   subscribed syscalls) before using totals for cross-backend performance. The
   current launchers intentionally begin observation at different points.

## Adversarial review disposition

The 2026-07-28 review supplied eleven numbered corrections (the iteration task
described them as ten). This revision addresses all eleven:

1. Standalone SaBRe and Hermit's ptrace-assisted SaBRe are separated in the
   executive, signal, and ptrace-sharing sections.
2. LiteInst hook installation remains in SIGSYS, while generic Tool/RPC work is
   correctly placed after `sigreturn`; inactive fork scaffolding is identified.
3. SaBRe's remote `BlockingRpcClient` and legacy fd-100 `BaseChannel` transports
   are documented as separate generations.
4. Reverie's injected-trap `E9patchBackend` and Hermit's `before empty` plus
   ptrace CLI path are separated.
5. SaBRe central dispositions, the loader's SIGILL/UD0 fallback, and Hermit's
   missed-raw-site conversion are source-cited.
6. `reverie-ptrace` usage is qualified to include the normal ptrace backend and
   exclude Hermit's custom SaBRe supervisor.
7. Historical KVM-blocked output is preserved alongside the later dated KVM
   PASS window and the subsequent loss of `/dev/kvm`.
8. Counter2 timings are explicitly non-comparative, with missing controls and
   the KVM 40,035 aggregate stated.
9. RPC measurements are historical samples plus a rerun, not a stable interval
   or backend proxy.
10. Exact build commands, gitlinks, and artifact paths are included.
11. LiteInst strace is labeled one PASS plus one teardown-owner failure
    (`N=2`), without an inferred flake rate.
