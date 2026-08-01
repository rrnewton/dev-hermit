# Native QEMU boot syscall baseline (demo5 reference)

Task: `demo5-qemu-strace-baseline` (evidence agent hermit-238, feeds hermit-226).
Question: characterize the **normal host-side syscall profile of a native QEMU
boot** of the demo5 image, so it can be diffed against what Hermit
sees/intercepts during demo5. This is the *reference*, no Hermit in the loop.

## What was run

Exactly the command `demos/05-qemu-busybox.sh` wraps, minus Hermit — i.e.
`demos/boot_qemu.sh <kernel> <initramfs> <qemu>` directly:

```
qemu-system-x86_64 -nodefaults -nic none -machine q35 -cpu max -m 256M \
  -accel tcg,thread=single -smp 1 -icount shift=0,sleep=off \
  -rtc base=utc,clock=vm -kernel <bzImage> -initrd <initramfs.cpio.gz> \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

- Host: devbig014. `qemu-system-x86_64` 10.1.2, `strace` present, `busybox`
  static (`/usr/sbin/busybox`, sha256 e35db146…).
- Kernel: `/var/tmp/pr1190-demo5.k8lPDC/bzImage`, Linux 6.17.13,
  sha256 `e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2`
  (the same bzImage hermit-220 uses for the demo5 wedge runs).
- initramfs: rebuilt with the repo's `demos/qemu-busybox/build-initramfs.sh`
  (self-terminating init: mounts proc/sys/dev, runs `uname`/`ls`/a 4-stage
  pipeline/`bc`/`sha256sum`, prints `HERMIT-QEMU-BUSYBOX-PASS`, then
  `poweroff -f`). Local file `initramfs-busybox.cpio.gz`
  sha256 `894fac1679a6410902adb8159a1d54bda12c0a1d9baf5b4bf9551d2a35351cc1`
  (a build product — reproducible from the script; **do not commit**).

The pr1190-demo5 asset `initramfs.cpio.gz` used in 220's wedge runs is a
*different, interactive* init (prints `HERMIT-QEMU-BASELINE-BOOT-OK` then drops
to a busybox shell and never powers off). For a clean, bounded reference with a
natural endpoint this baseline uses the repo's self-terminating init instead;
the guest kernel + QEMU host-side machine model are identical, so the host
syscall profile is the relevant, faithful reference.

## Timing (native, no Hermit, no strace)

`poweroff` reached at guest time **1.903s**; QEMU exits 0. Wall clock **17.64s**
(User 17.40 / System 0.40). The wall/guest gap is pure single-thread TCG
`-icount` emulation cost, not host syscall cost (system time is only 0.40s).

## Syscall count profile (`strace -f -c`, self-terminating boot)

Full data: `syscall-counts.csv`, raw `native-strace-count.txt`.
**910,679 host syscalls total, 80,296 errors** for one full boot+shutdown.

| syscall          | calls   | errors | %host-syscall-time |
|------------------|---------|--------|--------------------|
| futex            | 525,703 | 80,150 | 52.40 |
| ppoll            | 112,742 | 1      | 29.25 |
| write            | 130,886 | 0      | 7.89  |
| read             | 112,856 | 0      | 7.70  |
| writev           | 25,531  | 0      | 1.67  |
| clock_nanosleep  | 36      | 0      | 0.84  |
| openat           | 185     | 43     | 0.05  |
| (all others)     | <500 ea | —      | <0.1 each |

Five syscalls — **futex, ppoll, write, read, writev — are 98.9% of all host
syscalls** (907,718 / 910,679). Everything else (mmap/mprotect/openat/clone/
io_uring_setup/…) is one-time process and machine setup, <0.2% of the total.
The 80,150 futex errors are `EAGAIN` on `FUTEX_WAKE`/contended `FUTEX_WAIT` —
normal lock ping-pong, not failures.

## Threads (6 pids under `-f`)

- **2241974 — QEMU main/BQL-holder + event loop.** ppoll on the fd set +
  read(eventfd) + futex. 484k trace lines.
- **2241990 — vCPU / TCG execution thread.** futex + write(eventfd) + all the
  `write`/`writev` console output. 573k trace lines.
- **2241988 — RCU thread.** 645 lines: periodic `madvise` (call_rcu reclaim) +
  `clock_nanosleep` + a little futex.
- **2241977/2241978/2241979** — short-lived `boot_qemu.sh` shell/pipeline
  helpers (rt_sig*, wait4, execve); not QEMU.

## Ordering / phases

Excerpts: `excerpt-startup.txt` (QEMU execve → first ppoll),
`excerpt-steadystate.txt` (active phase), `excerpt-shutdown.txt` (poweroff→exit).

1. **Process + machine setup** (~29.62→29.70, one-time): `execve` qemu →
   ld.so/libc + locale/gconv opens → `clone3` RCU thread (2241988) →
   `memfd_create` (guest RAM) ×2 → `io_uring_setup` ×2 + `eventfd2` ×3 +
   `signalfd4([BUS ALRM IO])` (block-dev/aio + notifiers) → `clone3` vCPU
   thread (2241990) → first `ppoll([fd0,4,5,6,7])` = **main-loop entry**.
2. **Guest kernel boot** (~+0.1s → +8s wall here): low host-syscall rate
   (~60/s) — QEMU is executing guest kernel code in TCG; few host syscalls
   until userspace starts producing console output.
3. **Userspace / busybox workload** (the 11k–18k syscalls/s buckets): the
   steady-state loop below runs flat-out. This is where 98% of the syscalls are.
4. **Shutdown**: guest `poweroff -f` → S5 → main thread drains fd7, final
   `ppoll` timeout, `close(0)`/`close(1)`, `exit_group(0)`; vCPU + RCU threads
   exit.

## The steady-state loop (the thing Hermit must intercept ~900k times)

Per iteration (~150 µs of the loop), the **main thread and vCPU thread
ping-pong across an eventfd (fd=7) guarded by a futex = the Big QEMU Lock**:

```
vCPU(2241990):  write(7, "\1\0\0\0\0\0\0\0", 8)          # kick main loop
main(2241974):  ppoll([4,5,6,7], ...) = 1 ([fd7 POLLIN]) # wake
main(2241974):  read(7, ...) = 8                         # drain eventfd
both:           futex WAKE / futex WAIT (2, ...) EAGAIN  # BQL handoff
```

This eventfd-kick + BQL-futex handshake, repeated once per vCPU↔iothread
rendezvous under single-thread `-icount`, generates essentially all of the
525k futex / 112k ppoll / 112k read / 130k write. There is **no busy-poll and
no CPU-bound spin in the host syscall stream** — every wait is a real blocking
`ppoll`/`futex`, and the timers show up as `clock_nanosleep` (36) on the RCU
thread, not as tight polling.

## Why this is the reference hermit-226 needs

- Hermit's ptrace backend takes a **ptrace stop on each of these ~910k
  syscalls**. futex+ppoll dominate, so per-stop cost is what governs demo5
  wall time — directly why the a8195cfc per-`assume_stopped` procfs storm
  (`fix-reverie-a8195cfc-hpet-perf-regression`, PR #305) multiplied demo5 cost.
- The demo5 wedge (#1095/#1190 clock-domain) lives in **exactly this loop**:
  the main-thread `ppoll` (and the guest's timer deadlines behind it) is what
  Hermit must satisfy against committed virtual time. A native boot shows the
  `ppoll` always makes progress (returns POLLIN from the eventfd kick); the
  wedge is when, under Hermit, that main-loop `ppoll`/deadline can no longer be
  satisfied because guest CLOCK_MONOTONIC lags committed vtime → QEMU starves.
- Compare-against for hermit: a native boot is **910,679 syscalls, 5 of them
  98.9%**. Any Hermit trace showing the boot stalling far below the shutdown
  `exit_group(0)` with the main thread parked in `ppoll`/`futex` and no
  matching vCPU `write(7)` kick is the wedge, not slowness.

## Files

- `syscall-counts.csv` — machine-readable count profile.
- `native-strace-count.txt` — raw `strace -f -c` summary.
- `excerpt-startup.txt` / `excerpt-steadystate.txt` / `excerpt-shutdown.txt` —
  ordering excerpts (addresses as-is).
- `native-selfterm-console.log` / `native-selfterm-time.log` — clean boot +
  `/usr/bin/time -v`.
- Raw full `-tt -T` trace (84 MB, 1,058,329 lines) kept OUT of the repo at
  `/var/tmp/demo5-native-strace-tt.txt(.gz)` (6.2 MB gz) — too large to commit;
  reproducible via the command above.

## Reproduce

```bash
cd ~/work/dev-hermit/hermit
BUSYBOX=/usr/sbin/busybox demos/qemu-busybox/build-initramfs.sh /tmp/initramfs.cpio.gz
strace -f -c -o count.txt \
  demos/boot_qemu.sh /var/tmp/pr1190-demo5.k8lPDC/bzImage /tmp/initramfs.cpio.gz \
  "$(command -v qemu-system-x86_64)"
```
