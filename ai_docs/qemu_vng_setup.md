# QEMU and virtme-ng setup

This host was validated on 2026-07-21 with CentOS Stream 9 and kernel
`6.13.2-0_fbk13_hardened_0_g02230262e956`.

## Installation

Install QEMU, virtme-ng, and BusyBox from the configured RPM repositories:

```sh
sudo dnf install -y qemu-kvm virtme-ng busybox
```

CentOS installs the restricted QEMU system binary as
`/usr/libexec/qemu-kvm`. virtme-ng searches for `qemu-system-x86_64` or
`qemu-kvm` on `PATH`, so expose the conventional name:

```sh
sudo ln -s /usr/libexec/qemu-kvm /usr/local/bin/qemu-system-x86_64
```

Alternatively, pass `--qemu /usr/libexec/qemu-kvm` to every `vng` command.
BusyBox is required because virtme-ng uses it to construct the minimal guest
initramfs.

Installed versions at validation time:

- QEMU 10.1.0 (`qemu-kvm-10.1.0-21.el9`)
- virtme-ng 1.41 (`virtme-ng-1.41-2.el9`)
- BusyBox 1.35.0 (`busybox-1.35.0-2.el9`)

## KVM verification

The host CPU exposes AMD SVM, `/dev/kvm` exists, and the current user has read
and write access to it. Check those prerequisites with:

```sh
grep -m1 -Eo '(^| )(vmx|svm)( |$)' /proc/cpuinfo
ls -l /dev/kvm
test -r /dev/kvm && test -w /dev/kvm
```

Verify QEMU can initialize KVM and then cleanly exit:

```sh
printf 'info kvm\nquit\n' | \
  qemu-system-x86_64 -machine none,accel=kvm -nodefaults \
  -display none -monitor stdio
```

The monitor must report `kvm support: enabled`.

## VM smoke test

The CentOS QEMU build does not provide the `microvm` machine type, so include
`--disable-microvm` when invoking virtme-ng:

```sh
timeout 180 vng --run --disable-microvm \
  --exec 'sh -c "echo VNG_GUEST_OK; uname -r; cat /proc/1/comm"' \
  --verbose
```

The validated boot reported `Hypervisor detected: KVM`, printed:

```text
VNG_GUEST_OK
6.13.2-0_fbk13_hardened_0_g02230262e956
virtme-init
```

It then returned status 0 and logged `Powering off` and `reboot: Power down`.
The default virtme-ng overlays keep host system directories effectively
read-only for the smoke test. Use `--disable-kvm` only when testing a host
without KVM; it falls back to slower software emulation.

## Troubleshooting

- `cannot find qemu for x86_64`: add the symlink above or pass `--qemu`.
- `initramfs is needed, and no busybox was found`: install `busybox` or pass
  `--busybox /usr/sbin/busybox`.
- `unsupported machine type: "microvm"`: add `--disable-microvm`.
- KVM initialization failure: confirm CPU virtualization flags, `/dev/kvm`
  existence, and user permissions before trying `--disable-kvm`.
