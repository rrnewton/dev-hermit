# QEMU-Linux-under-Hermit: userspace program determinism verified (L2)

- **Date:** 2026-07-27
- **Task:** linux-hermit frontier continuation — run a simple userspace program
  (busybox sh, hello world) inside the deterministic QEMU VM.
- **Milestone:** **L2 (`hermit run --strict --verify`, bitwise-identical repeat
  run)** for an ordinary **userspace program** executed inside a QEMU-emulated
  Linux VM running under Hermit.
- **Backend:** `ptrace` (default). **Relaxations:** none (strict, sequentialized).
- **PR:** [rrnewton/hermit#1026](https://github.com/rrnewton/hermit/pull/1026)

## Question

The strict QEMU-Linux boot (`qemu_strict_l2_boot_20260727`, PR #992) and the
in-VM network work (`qemu_in_vm_network_l2_20260727`, PR #1019) both run a single
freestanding raw-syscall `init`. This task asks the next question: can a real
**userspace program** — a statically linked glibc binary, and busybox running a
shell script — execute **inside** the deterministic QEMU VM and be proven at L2?

## Method

New guest launcher + hello program + harness (PR #1026), on the same proven
freestanding boot path as `strict_l2_test.sh`:

- `hermit/tests/shared-futex-verify/qemu_exec_init.c` — a freestanding
  (raw-syscall, no libc) launcher `init` that, after the kernel boots,
  `fork()`/`execve()`s a target program, `wait4()`s it, prints the captured exit
  status, and powers off. The launcher is freestanding so it adds no loader
  nondeterminism; the child is a normal libc / busybox binary. Two scenarios are
  selected at compile time.
- `hermit/tests/shared-futex-verify/qemu_hello.c` — a statically linked glibc
  program that prints `QEMU_USERSPACE_HELLO_OK pid=<virtualized pid>` and exits
  7. It avoids `getaddrinfo`/NSS/`dlopen` and any wall-clock output so its stdout
  is bitwise-stable.
- `hermit/tests/qemu-boot/strict_l2_userspace_test.sh` — boots each scenario once
  under strict mode asserting every marker (including the exact captured child
  exit code), then reruns the identical command under `--verify` for the L2
  comparison.

Scenarios:

1. **hello** — launcher execs `/hello` (static glibc), expect exit 7.
2. **busybox** — launcher execs `/bin/busybox` with
   `sh -c 'echo QEMU_BUSYBOX_HELLO from $(busybox uname -s); exit 5'`. This forks
   a real `sh` **and** a `$(...)` command-substitution subprocess inside the VM,
   exercising multi-process fork+exec in the guest, and exits with the captured
   status 5.

### CPU-model requirement (root cause of the initial SIGILL)

The host glibc is built for the **x86-64-v2** baseline and its IFUNC resolvers
select SSSE3/SSE4.1/SSE4.2 string+startup routines (`pmaxud` in `__tls_init_tp`,
`palignr`+`pcmpistri` in `__strcmp_sse42`). QEMU's default `qemu64` CPU model
advertises only up to SSE3, so a static glibc program **SIGILLs (invalid opcode)
at exec**, before `main`. The harness enables exactly the x86-64-v2 feature set:

```
-cpu qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt
```

— nothing above it (no AVX, no RDRAND) so no new nondeterminism source is
introduced. `--verify` self-checks that this remains L2.

Run:

```bash
cd ~/work/dev-hermit/worktrees/275/hermit   # (or any hermit checkout at the SHA)
env HERMIT_BIN="$PWD/target/release/hermit" \
    KERNEL_IMAGE=/boot/vmlinuz \
    QEMU_BIN=/usr/local/bin/qemu-system-x86_64 \
    QEMU_L2_PHASE_TIMEOUT_SECONDS=480 \
    bash tests/qemu-boot/strict_l2_userspace_test.sh
```

Exact guest command (from the harness):

```
qemu-system-x86_64 -nodefaults -nic none -m 256M -accel tcg,thread=single \
  -smp 1 -icount shift=0,sleep=off -rtc base=utc,clock=vm \
  -cpu qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt \
  -kernel /boot/vmlinuz -initrd <run>/initramfs.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

## Results

**PASS at L2 (ptrace backend).** See `*_boot_console.txt` and
`*_verify_summary.txt`.

Boot oracle (userspace console):

```
hello:   QEMU_USERSPACE_HELLO_OK pid=70
         QEMU_USERSPACE_EXIT prog=hello exited=1 status=7
busybox: QEMU_BUSYBOX_HELLO from Linux
         QEMU_USERSPACE_EXIT prog=busybox-sh exited=1 status=5
```

L2 verify (`:: Success: deterministic. Determinism verified.` in both):

| scenario | messages total | detcore-specific | DETLOG & COMMIT | substantive diffs |
| --- | --- | --- | --- | --- |
| hello   | 526185 \| 526185 | 469067 \| 469067 | 371393 \| 371393 | 0 |
| busybox | 516241 \| 516241 | 459172 \| 459172 | 363577 \| 363577 | 0 |

Run1 and Run2 agree on every counter with no substantive DETLOG difference: the
whole userspace execution — process creation (`fork`/`execve`/`wait4`), the
glibc startup path, busybox's shell + command-substitution subprocess, the
virtualized pid, and the captured exit code — is **bitwise-deterministic** at L2.

## Interpretation

- **Userspace programs are L2-deterministic inside the QEMU-under-Hermit VM.**
  This is the userspace-process analogue of the boot milestone (kernel bring-up
  is L2) and the network milestone (the guest loopback stack is L2): now an
  ordinary glibc binary and busybox shell script, run as guest userspace
  processes, reproduce bitwise across two runs.
- The exact captured child exit code (7 for hello, 5 for busybox) is part of the
  asserted, reproduced state, not just stdout bytes.

## Scope / caveats

- **ptrace only.** KVM/DBI parity for in-VM userspace programs is not claimed.
- **Hardware-dependent:** requires the host kernel image (`/boot/vmlinuz`) and a
  QEMU that supports the deterministic knobs (`-icount`, single-thread TCG).
- **x86-64-v2 CPU model is required** for static glibc guests; the default
  `qemu64` model is insufficient (see root-cause note above).

## Recommended follow-ups

1. Wire `strict_l2_userspace_test.sh` into a hardware-dependent CI lane
   alongside `strict_l2_test.sh` and `strict_l2_network_test.sh`.
2. KVM-backend parity for an in-VM userspace program.
3. A larger userspace workload (e.g. busybox running a multi-command pipeline, or
   a small static application) to probe determinism under heavier guest activity.

## Reproduction

See `metadata.json` for SHAs/host. Rerun the exact `env ... bash
tests/qemu-boot/strict_l2_userspace_test.sh` command above from a hermit checkout
at the recorded SHA. The full `--log info` DETLOG dumps (tens of MB) are
intentionally **not** committed (>2 MiB); regenerate by rerunning.
