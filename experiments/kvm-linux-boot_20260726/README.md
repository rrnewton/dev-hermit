# KVM Linux Boot Experiment (2026-07-26)

## Result

Hermit's KVM backend booted the supplied Linux kernel to `/init` under
QEMU/TCG, printed the expected shared-futex marker, and powered down with exit
status 0. A subsequent `--strict --verify` run executed the complete boot
twice and found identical captured stdout, stderr, and exit status.

The continuation phase also assembled a reproducible static-BusyBox initramfs and
ran shell pipelines, `uname`, filesystem traversal, arbitrary-precision `bc`,
and SHA-256 inside nested Linux. Its two executions matched guest-visible
output/status and powered down. Snapshot and record/replay were probed but did
not pass; their exact blockers are recorded below.

The final console contained 311 lines (21,609 bytes):

```text
[    0.000000] Linux version 6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79 ...
...
SHARED_FUTEX_QEMU_KERNEL_OK release=6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79 machine=x86_64
[    1.300433] reboot: Power down
```

This result has one important qualification: KVM guest threads run
concurrently on host threads, outside Detcore's single-thread scheduler.
`--verify` therefore compares captured guest stdout, stderr, and exit status
for KVM, but does not compare internal Detcore log ordering. The CLI announces
that scope before reporting success. Strict unsupported-syscall handling
remains enabled.

The implementation is present as uncommitted source changes in the allocated
worktrees. The task protocol prohibited commit, submit, amend, and rebase, so
there is no publication SHA or PR.

## Inputs

| Input | Size | SHA-256 |
|---|---:|---|
| `ignored/qemu-linux/bzImage` | 12,742,656 | `e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2` |
| `ignored/qemu-linux/initramfs-hermit.cpio.gz` | 887 | `33c545d416edafec0b5ae42afc457f7e2c61363e090ed2ad7af90882cd3d2eb0` |
| `/usr/local/bin/qemu-system-x86_64` | QEMU 10.1.0 | `qemu-kvm-10.1.0-21.el9` |
| reproducible userspace initramfs | 765,030 | `02f78aadb68d593974807a218a168007f7e768d2cc5393b6e508888b914559fb` |
| static BusyBox | 1,306,976 | `e35db14651077c08598fbc3259609b2db398e5b7dcf07b28f1f3156118bcc081` |
| patched release `hermit` | 54,712,584 | `9bcc91dd23130743f7fd34f5fa27327c37a077b91eada853430156fe40eac58c` |

Source state used for the successful run:

- Hermit worktree: branch `kvm-linux-boot`, base
  `c1c3eb2de826abe7c66a3ae8e7fd218036a9beb3`, uncommitted diff SHA-256
  `6eb5da47eaf9a3d60aa06b5bcc6f8b73768485929dc9da90438569f3f526cf2b`.
- Reverie worktree: branch `kvm-linux-boot`, base
  `e3300b20ae4901620d21f66dbd33699cd30da687`, uncommitted diff SHA-256
  `49f47710aa949e80d558234607087784d29e964d58a3d42aa784965faffb4722`.
- Hermit's manifest pins Reverie at
  `4c6e9a0b73376ad18b7c5d4ad6e365ccd3d964ac`. For this experiment only,
  Cargo patched `reverie-kvm` to a temporary copy of the modified worktree
  while leaving the lockfile and manifest unchanged.

The raw logs are intentionally ignored workspace data under
`scratch/kvm-linux-boot-20260726/`. Their hashes are recorded in
`metadata.json` and `results.csv`.

## Reproduction

The native control and Hermit run use one QEMU TCG CPU and an
instruction-derived QEMU clock. `-icount` avoids presenting the nested Linux
guest with Hermit's synthetic RDTSC alongside a different virtual host clock.

```bash
/usr/local/bin/qemu-system-x86_64 \
  -m 256M -accel tcg,thread=single -smp 1 \
  -icount shift=0,sleep=off \
  -kernel /home/newton/work/dev-hermit/ignored/qemu-linux/bzImage \
  -initrd /home/newton/work/dev-hermit/ignored/qemu-linux/initramfs-hermit.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

The successful Hermit verification command was:

```bash
target/release/hermit --log warn run --backend kvm --strict --verify -- \
  /usr/local/bin/qemu-system-x86_64 \
  -m 256M -accel tcg,thread=single -smp 1 \
  -icount shift=0,sleep=off \
  -kernel /home/newton/work/dev-hermit/ignored/qemu-linux/bzImage \
  -initrd /home/newton/work/dev-hermit/ignored/qemu-linux/initramfs-hermit.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

It reported:

```text
:: Run1...
:: Run2...
:: KVM concurrent mode: comparing guest output and exit status; internal syscall trace order is not deterministic
:: Success: KVM guest output and exit status matched.
:: Backend: KVM (reverie-kvm KvmGuest<Detcore>)
```

Build the durable userspace fixture with:

```bash
experiments/kvm-linux-boot_20260726/build-userspace-initramfs.sh
```

Then substitute
`scratch/kvm-linux-boot-20260726/userspace-initramfs.cpio.gz` for the minimal
initramfs in the verification command above.

## Implementation

### Reverie KVM

- `CLONE_THREAD` now creates a concurrent host thread with its own KVM VM,
  vCPU, long-mode/bootstrap state, syscall frame, stack, TLS, and guest TID.
  The VMs map the same guest-memory object, preserving `CLONE_VM` semantics.
- Thread executors share process output, PID allocation, the program break,
  and the anonymous-mapping allocation cursor. Forked processes receive a
  private address-space state.
- Futex operations translate guest addresses to shared host-memory addresses,
  allowing QEMU's pthreads to synchronize through the host kernel.
- Root-thread futex calls use the KVM personality directly. Worker syscalls do
  not enter the Detcore scheduler, so routing only root futex calls through
  Detcore would deadlock the process.
- KVM personality `ppoll` translates guest descriptors and relative timeouts
  to host polling. Root `ppoll` and the following `readv` use that descriptor
  domain because KVM syscall injection cannot execute the event-loop wait.
- `rseq` returns `ENOSYS`, allowing glibc/QEMU to use their fallback instead
  of falsely claiming registration.
- Root teardown cancels worker vCPUs and uses `SIGURG` to interrupt blocking
  KVM/futex calls. The worker registry stays locked while signals consume its
  pthread IDs, and dropping a root backend also cancels workers.
- Thread exit implements `CLONE_CHILD_CLEARTID` as both a zero store and the
  required non-private futex wake. Captured output is drained only by root.
- The private bootstrap reservation provides syscall-frame and trampoline
  pairs for guest TIDs 2 through 65. Tool scratch remains at its old boundary.

### Hermit

- The sparse `MAP_NORESERVE` KVM personality address space increased from
  256 MiB to 1 GiB. QEMU needs its ELF/runtime mappings plus the nested 256 MiB
  machine RAM mapping within that identity-mapped range.
- `seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, NULL)` is
  deterministically classified and returns Linux's validation error `EFAULT`.
  Requests that supply a real filter return `EOPNOTSUPP`; Hermit does not claim
  to enforce a BPF policy that its backends cannot honor.
- KVM verification explicitly compares output and exit status without
  comparing concurrent internal trace order. Other backends retain complete
  log comparison.
- `--backend kvm record` and replay-related subcommands now fail immediately;
  they previously ignored the backend selection and silently used ptrace.

## Blocker Ladder

1. The baseline stopped at QEMU's first glibc `clone3(CLONE_THREAD)` because
   the KVM personality executed the child synchronously.
2. Once threads ran concurrently, strict mode rejected QEMU's seccomp
   capability probe. Deterministic Linux-compatible validation unblocked it.
3. QEMU could not map `pc.ram` inside the 256 MiB personality address space.
   A sparse 1 GiB range provided enough virtual address space.
4. QEMU deadlocked in a root futex because Detcore did not know about worker
   threads. Host-backed futexes for the complete KVM thread group fixed this.
5. Faking successful `rseq` registration and applying short futex timeouts
   caused invalid runtime behavior. Returning `ENOSYS` and using signal-based
   teardown restored normal blocking semantics.
6. Per-thread `mmap_next` cursors allocated overlapping mappings. Sharing the
   address-space allocator fixed QEMU heap corruption and an ensuing guest
   invalid-opcode fault.
7. The first complete `--verify` boot differed only in concurrent internal
   syscall log order. The KVM verification contract was narrowed explicitly
   to guest-visible output and status.
8. A qcow2 snapshot probe found QEMU spinning because KVM injection returned
   `ENOSYS` for root `ppoll`; routing `ppoll` and EOF-triggered `readv` through
   the personality repaired that event-loop gap without changing boot output.

## Expansion Results

### Nested userspace

The checked-in `userspace-init` and `build-userspace-initramfs.sh` reproducibly
assemble an image around the host's hash-pinned static BusyBox. The final
KVM command used `--log warn --strict --verify`; both executions produced 321
console lines (22,121 bytes), output SHA-256
`1096932938934e18135de64959caed276df5712dd669faef30e2735e235d1a64`,
printed `3.1415926532`, verified the BusyBox hash, printed
`HERMIT-QEMU-AUTOTEST-PASS`, and powered down. The verification relaxation is
unchanged: internal concurrent Detcore log ordering is excluded.

### Snapshot/savevm

`snapshot-probe.py` creates a fresh qcow2, starts a fixed idle `/init`, waits
for a serial marker, then intends to issue `savevm`, `info snapshots`,
`loadvm`, and `info status` over HMP. The final bounded KVM strict probe did
not reach the marker: after 30.172 seconds it had zero serial bytes, so no
snapshot was created or loaded. The structured result is
`scratch/kvm-linux-boot-20260726/snapshot-probe-final/snapshot-result.json`
(SHA-256 `6f3eb905ee799e0b4863dbccb826d63ee33ac52fd2f10e293eb1aedebf38eb7a`).

Native per-thread tracing showed qcow2 introduces an AIO worker that performs
`pread64`, eventfd writes, futexes, and `ppoll`. The implemented root event-loop
fixes removed two concrete `ENOSYS`/injection gaps, but drive startup still
stalls before firmware output. The likely remaining boundary is QEMU AIO plus
incomplete thread-group file-table/lifecycle semantics. This is a functional
snapshot blocker, not an L1/L2 result.

### Record/replay

Before the CLI guard, `--backend kvm record start --record-timeout 20` silently
entered ptrace recording. It emitted ptrace PMU overshoot telemetry, produced
zero console bytes, and terminated at the 20-second recording deadline. Its
stderr SHA-256 is
`40b45d53e1bbf47aece58d9f9c1d5088692b6fdbc1607f5ca19988838edb584e`.

Hermit record/replay requires `sequentialize_threads=true` because schedule
events are defined by its one-thread-at-a-time scheduler. The current KVM QEMU
boot instead depends on concurrent host threads whose worker syscalls bypass
Detcore, so it cannot generate a replayable schedule. The CLI now rejects the
misleading KVM record form with:

```text
the KVM backend is available only through `hermit --backend kvm run`; record and replay require the ptrace runtime's sequentialized scheduler
```

## Validation

| Command | Result |
|---|---|
| Native QEMU/TCG control | exit 0; 311 console lines; marker present |
| Hermit KVM single strict boot | exit 0; 311 console lines; marker present |
| Hermit KVM `--strict --verify` | exit 0; both complete boots matched output/status |
| BusyBox userspace KVM `--strict --verify` | exit 0; 321 lines; userspace marker/hash/math present |
| KVM qcow2 snapshot probe | blocked before savevm; 30.172 s; zero serial bytes |
| Pre-guard `--backend kvm record` probe | actually ptrace; timeout 20 s; zero serial bytes |
| Post-guard `--backend kvm record` | rejected immediately with accurate runtime requirement |
| `cargo test -p reverie-kvm` | 127 tests passed |
| `cargo check -p reverie-kvm` | passed |
| `cargo clippy -p reverie-kvm --all-targets -- -D warnings` | passed |
| `cargo clippy -p hermit --all-targets -- -D warnings` | passed |
| `cargo test -p detcore seccomp` | passed |
| `cargo test -p detcore every_pinned_sysno_has_an_explicit_classification` | passed |
| `cargo test -p hermit verify::tests` | 4 tests passed |
| `git diff --check` in both product worktrees | passed |

The native and Hermit console hashes differ because the kernel prints timing
and calibration details. The relevant determinism check is the two Hermit KVM
runs made by the same `--verify` invocation; those captured outputs matched.

## Remaining Gaps

- Guest worker syscalls and root futexes bypass Detcore's tool callbacks. The
  result is guest-visible output determinism for this workload, not a totally
  ordered replayable syscall trace.
- `CLONE_FILES` currently clones the descriptor table. Existing duplicated
  host file descriptions retain kernel sharing, but later table additions and
  closes are not fully shared between threads.
- Worker-originated `exit_group` propagation is incomplete, and detached
  worker handles are cancelled rather than joined during root teardown.
- The exception stack/TSS region is not yet private per guest thread.
- The bootstrap transport supports guest TIDs 2 through 65. Because PID/TID
  allocation is monotonic and shared, forks and exited threads consume that
  range rather than allowing 64 reusable simultaneous slots.
- Snapshot/savevm is blocked before serial output when its required qcow2 AIO
  path is present; `loadvm` was therefore not reached.
- KVM record/replay is architecturally unavailable until worker threads run
  through a sequentializable Detcore schedule; the CLI now says so explicitly.
- Nested QEMU KVM acceleration and SMP nested guests were not validated.
- The implementation must be rebased or ported onto current Hermit and
  Reverie main, committed, reviewed, and published before it can land.

## Relationship to gVisor

gVisor already provides a complete userspace kernel model for thread groups,
futexes, shared address spaces, signals, file tables, and process teardown.
This prototype implements only the minimum equivalent process-personality
semantics inside Reverie KVM needed by QEMU/TCG. The successful boot is useful
evidence that those primitives are sufficient for this workload, but it is
not parity with gVisor's lifecycle, signal, namespace, or file-table model.

The architectural lesson is to keep these semantics explicit and shared at
the thread-group level. Per-vCPU copies of allocation, futex, and teardown
state appeared simpler but produced real overlap and deadlock failures. Future
work should either continue converging the KVM personality toward a coherent
userspace-kernel model or reuse an established model rather than accumulating
one-off QEMU exceptions.
