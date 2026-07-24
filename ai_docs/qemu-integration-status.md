# QEMU Integration Status

Status snapshot: 2026-07-21. "Landed" means present on
`rrnewton/hermit:main`; branch and PR results are identified separately.

## Executive status

QEMU 10.1 TCG can boot Linux and exit successfully while running as a process
under Hermit's ptrace interception. The proven configuration disables precise
preemption, thread sequentialization, virtual time, virtual metadata, and
virtual CPUID. It can also use `--panic-on-unsupported-syscalls`, showing that
the minimal direct boot reaches no unsupported syscall.

This is an interception proof, not deterministic VM execution. Default Hermit
mode and record mode time out or progress too slowly. The principal blockers
are coherent virtual time, practical precise-preemption cost, deterministic
event-loop and vectored-I/O semantics, reproducible VM inputs, and vhost-user
file-descriptor state.

## Validated environment

- Host: x86-64 Linux 6.13.2 on AMD hardware.
- QEMU: 10.1.0 from CentOS Stream 9 `qemu-kvm`.
- virtme-ng: 1.41.
- Direct KVM access: available and independently smoke-tested.
- Hermit target: QEMU TCG first; KVM passthrough is not the deterministic
  target.

The CentOS package installs `/usr/libexec/qemu-kvm`; virtme-ng needs either
`--qemu /usr/libexec/qemu-kvm` or a conventional
`qemu-system-x86_64` path. It also requires `--disable-microvm` because this
package omits the `microvm` machine type. See
[qemu_vng_setup.md](qemu_vng_setup.md).

## Proven direct TCG path

Use an explicit TCG accelerator and a pinned kernel/initramfs or disk image.
The successful Hermit option set was:

```bash
./target/release/hermit run \
  --preemption-timeout=disabled \
  --no-sequentialize-threads \
  --no-virtualize-time \
  --no-virtualize-metadata \
  --no-virtualize-cpuid \
  --panic-on-unsupported-syscalls \
  -- qemu-system-x86_64 \
  -machine accel=tcg \
  -nographic \
  -m 256 \
  -kernel /absolute/path/to/vmlinuz \
  -initrd /absolute/path/to/initramfs \
  -append 'console=ttyS0 panic=-1'
```

The exact kernel command line and root input depend on the fixture. Keep every
input path immutable and bind-visible inside Hermit's mount namespace. The
session's durable command/result matrix is under
`experiments/qemu_under_hermit_20260721/` when that experiment directory is
present in the parent checkout.

Observed modes:

| Mode | Result | Interpretation |
| --- | --- | --- |
| Native/direct TCG | Boots to expected terminal state | QEMU/kernel fixture is valid |
| Hermit namespace-only | Boots | Namespace wrapper is not the blocker |
| Relaxed Hermit interception above | Boots and exits 0 | Ptrace/syscall interception reaches a complete minimal boot |
| Default `hermit run` | Timeout/extreme slowdown | Sequentialization and precise PMU scheduling dominate progress |
| Virtual time enabled | Guest TSC/PIT/APIC skew | Two intercepted time sources use incoherent Detcore domains |
| `hermit record` | Timeout | Record/replay is not ready for this workload |
| virtme-ng wrapper | Progresses to separate launch/helper blockers | Launcher and vhost-user state need a reproducible model |

## Measured syscall surface

One verified TCG boot made 147,679 calls across 54 syscall names:

| Syscall | Calls | Status at audit |
| --- | ---: | --- |
| `futex` | 62,456 | Dedicated but incorrect absolute realtime bitset timeout semantics |
| `ppoll` | 33,589 | Fallback; QEMU event-loop critical |
| `write` | 22,390 | Dedicated |
| `read` | 19,856 | Dedicated with untested device/fd variants |
| `writev` | 2,889 | Fallback with incomplete resource scheduling |
| `mprotect` | 2,351 | Passthrough; TCG JIT coverage required |
| `madvise` | 2,003 | Passthrough; memory-effect replay coverage required |

At that baseline, 26 names representing 72.09% of calls had dedicated Detcore
dispatch, 11 names representing 3.17% were explicit passthroughs, and 17 names
representing 24.73% were fallbacks. `ppoll` plus `writev` accounted for 99.87%
of fallback call volume.

Draft Hermit PR #25 implements dedicated `ppoll`, `readv`, and `writev`
handling and fixes futex and poll timeout conversions. Its focused tests pass,
but it remains a human-review draft and is not part of `main` at this snapshot.
Related follow-ups include recorder/replayer `ppoll`/`readv` outputs and
vhost-user `recvmsg` ancillary data/SCM_RIGHTS reconstruction.

## Virtual-time root cause

Hermit does intercept both QEMU time sources:

- QEMU TCG's guest RDTSC helper eventually calls a real host RDTSC on x86-64.
  Reverie's `PR_SET_TSC` path traps it.
- QEMU virtual device clocks use host `CLOCK_MONOTONIC`, which Reverie patches
  and Detcore intercepts.

The failure is not an interception bypass. Detcore currently synthesizes
RDTSC from the calling thread's local `DetTime`, while `clock_gettime` exposes
`GlobalTime`, which includes published progress from all QEMU threads. QEMU
compares guest TSC progress with PIT, ACPI PM timer, and APIC reference clocks.
Those domains advance at different rates, so calibration reports skew.

A trace captured 23,774 intercepted RDTSC events and 66,314 intercepted
`CLOCK_MONOTONIC` reads. In one adjacent example, a QEMU thread's local TSC
represented epoch plus 400,275 ns while the following global clock read was
epoch plus 2,770,525 ns.

QEMU `-icount shift=0,sleep=off` is a useful experiment: it makes QEMU derive
both guest elapsed ticks and virtual device clocks from its instruction count,
and the test calibrated without the skew warning. It is not a substitute for
coherent Hermit time because it changes QEMU's execution model and does not
resolve other Hermit scheduling/replay requirements.

## Coherent-time fix plan

The smallest prototype is active on a feature branch, not landed:

1. After Detcore increments the RDTSC nondeterministic-instruction count,
   publish/observe that thread's time through the existing
   `GlobalTimeLowerBound` RPC.
2. Return the resulting process-wide `GlobalTime` nanoseconds as RDTSC.
3. Add a multithread regression in which completed child-thread syscall work
   must be visible to a subsequent TSC read.
4. Run focused time, RDTSC, schedule record/replay, and log-diff tests.
5. Re-run the direct QEMU TCG fixture with virtual time enabled and reject the
   change unless PIT/TSC/APIC calibration is stable.
6. Measure monotonicity and determinism across repeated QEMU runs; then test
   the interaction with sequentialization and precise timers.

The prototype uses an existing global observation path, so it is smaller than
inventing a third clock domain. It still needs full test and QEMU evidence
before adoption. `CLOCK_MONOTONIC` currently shares the configured epoch with
realtime; that semantic cleanup is related but is not the calibration split.

## virtme-ng and process lifecycle

The first vng path exposed three independent issues:

- Hermit's virtual `uname -r` caused vng to look for the wrong host kernel.
- vng's process creation used `CLONE_VFORK`, which previously deadlocked the
  deterministic scheduler.
- vng/QEMU uses inherited initrd descriptors and an external vhost-user-fs
  helper. Reopening `/proc/self/fd/5` inside Hermit's container returned
  `EACCES` in one path, and later branch testing reached vhost fd/vring cleanup
  timeouts.

Draft Hermit PR #27 implements native vfork/`CLONE_VFORK` parent-blocking
semantics and passes focused release-on-exec/exit tests. On that branch the
unsupported-vfork error disappears and vng reaches the later virtiofs blocker.
It is not landed at this snapshot.

For deterministic testing, prefer a direct QEMU command with a pinned,
read-only initramfs or disk over a live host-root vhost-user filesystem. If
vhost-user remains necessary, record/reconstruct message payloads, ancillary
data, received fds, helper lifecycle, and immutable filesystem state.

## Why TCG precedes KVM

The KVM baseline made 54,981 ioctls, 83.6% of all calls. `KVM_RUN` mutates a
shared run page and guest memory; other ioctls construct VM/vCPU state,
register memory, inject interrupts, and exchange registers/device state.
Generic ioctl return recording cannot reproduce those effects.

TCG is slower but stays in a normal user-space syscall/JIT surface. It is the
appropriate first target for Hermit-level deterministic VM execution. A
Reverie KVM/Sentry backend is a different architecture described in
[architecture-overview.md](architecture-overview.md).

## Acceptance gates

Call QEMU/TCG support deterministic only after all of these pass at an exact
Hermit/QEMU/kernel/image SHA:

1. strict unsupported-syscall mode boots with virtual time and documented
   thread scheduling;
2. repeated runs produce stable guest output, exit, and clock calibration;
3. precise or explicitly justified alternative preemption completes within a
   practical bound;
4. `ppoll`, futex absolute deadlines, vectored I/O, PID/TID/rseq, JIT mappings,
   and required fd metadata have regression coverage;
5. every kernel, initramfs/disk, firmware, helper, and config input is pinned;
6. record/replay reconstructs received fds/control data or removes that live
   dependency;
7. verify passes repeatedly without schedule/log desynchronization.

## Tracking links

- QEMU slowdown: <https://github.com/rrnewton/hermit/issues/5>
- Incoherent nested clocks: <https://github.com/rrnewton/hermit/issues/6>
- vng virtual `uname`: <https://github.com/rrnewton/hermit/issues/9>
- vng `CLONE_VFORK`: <https://github.com/rrnewton/hermit/issues/10>
- recorder/replayer vectored I/O: <https://github.com/rrnewton/hermit/issues/22>
- vhost-user SCM_RIGHTS: <https://github.com/rrnewton/hermit/issues/23>
- `ppoll` design: <https://github.com/rrnewton/hermit/issues/24>
- `ppoll` tracker: <https://github.com/rrnewton/hermit/issues/26>
