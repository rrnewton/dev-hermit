# QEMU-Linux-under-Hermit: L2 determinism verified (strict boot)

- **Date:** 2026-07-27
- **Task:** `frontier-linux-hermit-next` (P1 frontier — post-demo Linux-hermit push)
- **Milestone:** **L2 (`--strict --verify`, bitwise-identical repeat run)** for a
  full QEMU-emulated Linux kernel boot running under Hermit.
- **Backend:** `ptrace` (default). **Relaxations:** none (strict, sequentialized).

## Question

`hermit/docs/QEMU_BOOT.md` recorded the strict QEMU-Linux boot as **L1 only**:

> "reached the initramfs marker and powered off ... This is an L1 result; it has
> not yet been repeated with `--verify` for L2 assurance."

Does the strict QEMU-Linux boot in fact reproduce **bitwise-identically** across
two runs (L2)? The demo (deterministic Python controller, snapshot byte-identity)
answered qcow2 nondeterminism but did not close the L2-of-the-boot question.

## Method

Ran the L2 harness already landed on Hermit `main`,
`hermit/tests/qemu-boot/strict_l2_test.sh` (added via PR #992), unmodified:

```bash
cd ~/work/dev-hermit/hermit
env HERMIT_BIN="$PWD/target/release/hermit" \
    KERNEL_IMAGE=/boot/vmlinuz \
    QEMU_BIN=/usr/local/bin/qemu-system-x86_64 \
    QEMU_L2_PHASE_TIMEOUT_SECONDS=360 \
    bash tests/qemu-boot/strict_l2_test.sh
```

The harness builds a minimal static `init` from
`hermit/tests/shared-futex-verify/qemu_init.c` into an initramfs, then:

1. **Boot oracle** — runs the guest once under `hermit --log info run --strict`
   and asserts the kernel prints `SHARED_FUTEX_QEMU_KERNEL_OK` and hits no
   clock-calibration failure lines.
2. **L2 verify** — runs the *same* command under
   `hermit --log info run --strict --verify`, which executes the boot twice and
   diffs the two DETLOG streams (`:: Run1 / :: Run2 / :: Comparing`).

Exact guest command (from the harness):

```
qemu-system-x86_64 -nodefaults -nic none -m 256M -accel tcg,thread=single \
  -smp 1 -icount shift=0,sleep=off -rtc base=utc,clock=vm \
  -kernel /boot/vmlinuz -initrd <run>/initramfs.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

## Results

**PASS at L2 (ptrace backend).**

Boot oracle (`boot_oracle_console.stdout`): full kernel boot
(Linux 6.17.13-0_fbk0), `Run /init as init process`,
`SHARED_FUTEX_QEMU_KERNEL_OK release=6.17.13-... machine=x86_64`, then
`reboot: Power down`. Virtual RTC deterministically set to
`2026-01-01T00:00:06 UTC (1767225606)`. 31353 scheduler COMMIT turns. No
`Unable to calibrate against PIT` / `Clocksource ... skewed` /
`Marking TSC unstable` / `No current clocksource` lines.

L2 verify (`verify_comparison_summary.txt`, `verify_phase_markers.stderr`):

```
Logs contain 517064 | 517064 messages total
Logs contain 460566 | 460566 detcore-specific messages
Logs contain 183018 | 183018 INFO messages
Logs contain 364557 | 364557 DETLOG & scheduler COMMIT messages
Done processing logs, no substantive differences found.
:: Success: deterministic. Determinism verified.
QEMU strict L2 boot passed.
```

Run1 and Run2 agree on every counter and produce no substantive DETLOG
difference — i.e. the boot is **bitwise-deterministic** at L2.

## Interpretation

- The strict QEMU-Linux boot is now **L2-verified**, one rung above the
  L1 claim in `hermit/docs/QEMU_BOOT.md`. This is a real doc/reality gap: the
  capability (the `strict_l2_test.sh` harness) is on `main`, but the prose still
  says "L1 only / not yet ... `--verify`".
- Scope: this covers the minimal `qemu_init.c` guest (kernel boot to a userspace
  marker + poweroff), **not** a full userspace/busybox boot to a shell, and
  **not** L3 (memory determinism via `--detlog-heap/--detlog-stack`, which is far
  too slow to reach userspace — see the parent memory note
  `qemu-boots-under-hermit-relaxed-not-strict`).
- Backend scope: ptrace only. KVM/DBI parity for the QEMU boot is not claimed
  here.

## Recommended follow-ups

1. **Doc PR (owner: `hermit-linux` / coordinator).** Update
   `hermit/docs/QEMU_BOOT.md` from "L1 only" to "L2 verified" with this exact
   command + evidence. Not done here to avoid colliding with the `linux` slot
   (`hermit-linux`, task `impl-kvm-linux-boot`), which owns QEMU boot files.
2. **Wire `strict_l2_test.sh` into a CI lane** (hardware-dependent: needs a
   kernel image, QEMU, and PMU) so L2 is continuously guarded, not one-shot.
3. **Next rungs:** L2 of a *full userspace* boot (busybox shell); KVM-backend
   parity for the boot; then L3 memory determinism if feasible.

## Reproduction

See `metadata.json` for SHAs/host. Rerun the exact `env ... bash
tests/qemu-boot/strict_l2_test.sh` command above from the Hermit checkout at the
recorded SHA. The full 28 MB boot-oracle `--log info` DETLOG dump
(`boot.stderr`) is intentionally **not** committed (>2 MiB binary-ish log);
regenerate it by rerunning.
