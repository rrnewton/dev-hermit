# QEMU TCG under Hermit: first attempt

Status: exploratory results from 2026-07-21. No Hermit source was changed.

## Question

Can the installed QEMU boot a Linux kernel in TCG mode while QEMU itself runs
under Hermit? If not, which Hermit feature or syscall blocks it?

## Environment

- Hermit `8ec63a557af6425cbe8965316b4d5e8e662546b9` (`rrnewton/hermit` main)
- QEMU 10.1.0 (`qemu-kvm-10.1.0-21.el9`)
- virtme-ng 1.41
- Host and guest kernel `6.13.2-0_fbk13_hardened_0_g02230262e956`
- CentOS Stream 9, x86-64, AMD SVM available but deliberately unused

The kernel-only VM has no root filesystem. A successful experiment therefore
boots to the expected `VFS: Unable to mount root fs` panic and exits through
`panic=1` plus QEMU `-no-reboot`.

## Baseline

The direct TCG command boots successfully and exits 0:

```sh
qemu-system-x86_64 -nographic -m 256 \
  -kernel /boot/vmlinuz-$(uname -r) \
  -append 'console=ttyS0 panic=1' \
  -machine accel=tcg -no-reboot
```

Hermit's `--namespace-only` mode also boots the same command and exits 0. The
namespace setup is therefore not the incompatibility.

## Result matrix

| Hermit mode | Limit | Result |
| --- | ---: | --- |
| Default `run`, implicit TCG | 90 s | Timeout 124 after QEMU warnings; no BIOS output |
| Default `run`, explicit TCG | 60 s | Timeout 124 after a partial SeaBIOS banner |
| `--strace-only` | 45 s | Timeout 124 after `Booting from ROM` |
| `--strace-only --preemption-timeout disabled` | 60 s | Exit 0; complete expected boot |
| Full mode, only preemption disabled | 120 s | Timeout 124; no BIOS output |
| Real time/CPUID, sequentialized threads | 90 s | Partial BIOS, then failed to terminate promptly |
| Concurrent threads, virtual time | 75 s | Timeout 124 with nested clock calibration failures |
| Concurrent threads, real time, preemption disabled | 60 s | Exit 0; complete expected boot |
| Previous row plus `--panic-on-unsupported-syscalls` | 30 s | Exit 0; no unsupported syscall |
| `record start` defaults | 120 s | Timeout 124 before BIOS output |

The working syscall-intercepted command is:

```sh
./target/release/hermit run \
  --preemption-timeout disabled \
  --no-sequentialize-threads \
  --no-virtualize-time --no-virtualize-metadata \
  --no-virtualize-cpuid -- \
  qemu-system-x86_64 -nographic -m 256 \
  -kernel /boot/vmlinuz-$(uname -r) \
  -append 'console=ttyS0 panic=1' \
  -machine accel=tcg -no-reboot
```

This is useful compatibility evidence, but it is not a deterministic QEMU
execution because scheduling and host time virtualization are disabled.

## Failure analysis

### Scheduling and preemption overhead

Default `run` and `record start` did not reach a complete BIOS banner within
their time limits. Disabling precise preemption helped, but full deterministic
thread sequentialization remained extremely slow. INFO tracing showed normal
`futex`, `read`, and `write` traffic rather than one blocked syscall. With
`--panic-on-unsupported-syscalls`, the reduced working configuration still
completed, proving the minimal QEMU boot does not require an unmodeled syscall.

### Incoherent nested clocks

Allowing QEMU's threads to run concurrently while retaining Hermit virtual time
made more progress, but the guest reported:

```text
tsc: Unable to calibrate against PIT
clocksource: 'tsc-early' skewed -349080857 ns
clocksource: No current clocksource.
APIC calibration not consistent with PM-Timer: 4202ms instead of 100ms
```

QEMU derives emulated PIT, PM timer, APIC, RTC, and TSC behavior from several
host clocks. The experiment indicates that virtualizing QEMU's host clock
syscalls without coordinating those derived clocks gives the nested kernel an
inconsistent time model.

### Host CPUID limitation

Hermit repeatedly warned that the hardware does not support CPUID faulting.
Disabling Hermit CPUID virtualization removes the warning and is part of the
working command, but CPUID was not the primary boot blocker.

## virtme-ng attempts

Bare `hermit run -- vng` timed out after 30 seconds and was not a valid boot
test because bare vng tries to infer/build from its current directory.

The real TCG attempt failed immediately:

```sh
vng --run --disable-kvm --disable-microvm --exec 'echo VNG_UNDER_HERMIT_OK'
```

Hermit virtualizes `uname -r` as `5.2.0`, so vng reported `5.2.0 does not
exist`. Passing the real `/boot/vmlinuz-*` path bypassed that lookup, but then
Hermit logged:

```text
hermit: clone() with CLONE_VFORK argument. This is not currently supported and will not work.
```

The orchestration timed out after 180 seconds. Forced termination also caused
QEMU vhost-user fd and vring restore errors; these appear to be cleanup effects,
not the initial blocker.

Trying vng's `--force-9p` fallback failed with `virtio-9p-pci is not a valid
device model name`. The restricted CentOS QEMU build has no 9p device, so this
last failure is a host packaging limitation rather than a Hermit issue.

## GitHub issues

- [#5: QEMU TCG boot is unusably slow with default scheduling and preemption](https://github.com/rrnewton/hermit/issues/5)
- [#6: Virtualized host time corrupts QEMU guest clock calibration](https://github.com/rrnewton/hermit/issues/6)
- [#9: vng cannot discover the host kernel because Hermit virtualizes uname -r](https://github.com/rrnewton/hermit/issues/9)
- [#10: vng under Hermit hits unsupported clone(CLONE_VFORK) and stalls](https://github.com/rrnewton/hermit/issues/10)

## Conclusion

QEMU TCG can run under Hermit's ptrace interception and boot Linux. The first
attempt does not achieve deterministic execution: usable progress currently
requires disabling precise preemption, deterministic thread sequentialization,
and time virtualization. vng adds two earlier orchestration blockers before it
can demonstrate a complete root-filesystem boot.
