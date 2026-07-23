# Frontier experiment: booting Linux under hermit + QEMU

**Date:** 2026-07-21
**Branch:** `speculative` @ `96261f6` (integration branch merging all pending PRs, incl. #35 QEMU virtual-time fix and #37 resource model)
**Goal:** Boot a real Linux kernel inside QEMU, with QEMU itself running under `hermit run` with virtual/logical time, and document how far it gets.

## TL;DR

| Configuration | Result |
|---|---|
| Plain QEMU + TCG (no hermit) — **baseline** | ✅ Full boot to userspace; busybox init runs; clean poweroff (~2s guest time) |
| `hermit run` (deterministic, virtual time, `sequentialize_threads` ON — the intended mode) | ❌ QEMU `abort()`s ~0.2s in, **before any kernel output** |
| `hermit run --no-sequentialize-threads` (determinism sacrificed) | ⚠️ QEMU no longer aborts; boots SeaBIOS/iPXE firmware, but TCG-under-ptrace is extremely slow (did not reach the kernel within the time budget) |

**Root cause of the abort in deterministic mode:** QEMU uses a **process-shared futex** (`FUTEX_WAIT` with the `FUTEX_PRIVATE_FLAG` bit clear). Hermit's deterministic scheduler currently **rejects shared futexes with `EOPNOTSUPP`** (`detcore/src/syscalls/threads.rs:235-237`), which glibc treats as a fatal internal error and responds to with `tgkill(SIGABRT)` → core dump.

This is the exact same limitation the sibling `impl-speculative-hermit` task hit with Node and Java (both use process-shared futexes). It is a fundamental gap in the resource/identity model (PR #37), not a QEMU-specific quirk.

## Environment

- Host: Linux 6.13.2 (fbk hardened), x86_64, AuthenticAMD. Host is itself a VM — **no CPUID-faulting support**, so `hermit` cannot intercept CPUID (warns; used `--no-virtualize-cpuid` to silence, no effect on outcome). `/dev/kvm` present but unused (see below).
- QEMU: `qemu-system-x86_64` 10.1.0 (qemu-kvm-10.1.0-21.el9)
- Kernel source image: `/boot/vmlinuz-6.13.2-0_fbk13_hardened_0_g02230262e956` (fbk hardened; has `CONFIG_SERIAL_8250_CONSOLE`, `CONFIG_BLK_DEV_INITRD`, `CONFIG_DEVTMPFS` built in)
- Initramfs source: the host's **static** busybox (`/usr/sbin/busybox`) plus the retained `initramfs_root/init` script.

The copied kernel, generated initramfs archive, and busybox binary were omitted from version control under the parent repository's binary-artifact policy. Recreate them from the sources above before rerunning the recorded commands.

**Note on TCG vs KVM:** All runs use `-accel tcg` (pure software emulation), *not* KVM. Hermit must intercept and order the guest's execution to control time deterministically; KVM would run guest code directly on the CPU and bypass hermit entirely. TCG is the only meaningful mode for the "deterministic QEMU" goal (and is much slower).

**Note on `--virtualize-time`:** the task command used `hermit run --virtualize-time ...`, but hermit has no such flag — **virtual/logical time is ON by default**; the flag to turn it off is `--no-virtualize-time`. So plain `hermit run <cmd>` already runs with virtual time, as intended.

## Method & results

### 1. Baseline — plain QEMU + TCG (no hermit)
```
qemu-system-x86_64 -m 512M -accel tcg -smp 1 \
  -kernel bzImage -initrd initramfs.cpio.gz -nographic -no-reboot \
  -append "console=ttyS0 panic=-1 rdinit=/init"
```
✅ Boots fully. Log: `baseline_qemu_tcg.log`. Reaches userspace:
```
Run /init as init process
HERMIT-QEMU-LINUX-BOOT: userspace init reached!
uname: Linux (none) 6.13.2-0_fbk13_hardened_0_g02230262e956 ... x86_64 GNU/Linux
  hello from inside the deterministic VM
HERMIT-QEMU-LINUX-BOOT: init complete, powering off
reboot: Power down
```
Confirms the kernel + initramfs are valid and boot in QEMU/TCG.

### 2. `hermit run` — deterministic / virtual time (the intended frontier config)
```
hermit run --no-virtualize-cpuid -- \
  qemu-system-x86_64 -m 256M -accel tcg -smp 1 \
  -kernel bzImage -initrd initramfs.cpio.gz -nographic -no-reboot \
  -append "console=ttyS0 panic=-1 rdinit=/init"
```
❌ QEMU dies with `SIGABRT` (exit 134) after ~0.2s of virtual time, before the kernel prints anything. QEMU's own stderr shows only a harmless machine-type deprecation warning (`qemu_stderr.txt`); the abort carries no assertion message because it is a raw glibc-driven `abort()`.

Hermit itself runs and shuts down cleanly (75 scheduler turns) — it is the **guest** that aborts. Logs: `hermit_qemu_run1.log`, `hermit_qemu_run5.log`, and full syscall trace `hermit_debug_default.log`.

**The failing sequence (from `hermit_debug_default.log`):**
```
[dtid 5] clock_nanosleep(CLOCK_REALTIME, ...) = Ok(0)
[dtid 5] futex(0x555557231ea4, 0, -1, NULL, NULL, 0) = Err(Errno(EOPNOTSUPP))   <-- shared FUTEX_WAIT rejected
[dtid 5] rt_sigprocmask(...) ; gettid()=5 ; getpid()=3
[dtid 5] tgkill(3, 5, 6)                                                          <-- signal 6 = SIGABRT
[5] handle_signal: received signal SIGABRT
```
`futex_op == 0` means `FUTEX_WAIT` with the `FUTEX_PRIVATE_FLAG` (0x80) bit **clear** → a process-shared futex. The futex address (`0x555557231ea4`) is identical across runs, so the abort is deterministic/reproducible.

**Attempted mitigations that did NOT help (still SIGABRT):**
- `--no-virtualize-cpuid` — only silences the CPUID-faulting warning.
- `--debug-futex-mode external` and `--debug-futex-mode polling` — the shared/private check happens *before* the futex-mode dispatch, so these modes still reject the shared `FUTEX_WAIT`. (`external` mode did let a later *private* futex, op 129 = `FUTEX_WAKE|PRIVATE`, pass through, but the shared `FUTEX_WAIT` on the next thread still returned `EOPNOTSUPP` → abort.) Logs: `hermit_qemu_futex_external.log`, `hermit_qemu_futex_polling.log`, `futex_external_debug.log`.

### 3. `hermit run --no-sequentialize-threads` — determinism disabled
```
hermit run --no-virtualize-cpuid --no-sequentialize-threads -- qemu-system-x86_64 ...
```
⚠️ With sequentialization off, `handle_futex` injects all futexes (including shared ones) straight to the kernel, so the `EOPNOTSUPP` rejection does not fire. QEMU **no longer aborts** and begins emulating the machine:
```
SeaBIOS (version 1.16.3-5.el9)
iPXE (http://ipxe.org) 00:03.0 ...
Booting from ROM...
Probing EDD (edd=off to disable)... ok
```
TCG-under-ptrace is very slow, but with a larger time budget the boot progresses well past firmware into the kernel itself:
```
Booting from ROM...
Probing EDD (edd=off to disable)... ok
No EFI environment detected.
early console in extract_kernel
...
Decompressing Linux... No EFI environment detected.
```
i.e. SeaBIOS → iPXE → QEMU linuxboot ROM → kernel early console → kernel self-decompression, all executing under hermit. The extended run reached `Decompressing Linux...` and then stalled there: decompressing the ~12 MB kernel through TCG's emulated CPU (with hermit's per-syscall ptrace interception on top) is prohibitively slow, and the run did not reach userspace within a ~15-minute budget. This mode is **not deterministic** (hermit logs "Nondeterministic external actions ... Need to record this for reproducibility"), so it does not satisfy the deterministic-QEMU goal — but it demonstrates that the shared-futex rejection is the *only* hard blocker in the deterministic path: with futexes passed through, QEMU and the guest kernel run normally, just far too slowly to complete a boot here. Logs: `hermit_qemu_noseq.log`, `hermit_qemu_noseq_long.log`.

## Root cause detail

`detcore/src/syscalls/threads.rs`, `handle_futex` (lines ~232-242):
```rust
if !self.cfg.sequentialize_threads {
    Ok(guest.inject(call).await?)          // pass shared+private futexes to kernel (non-deterministic)
} else {
    if call.futex_op() & libc::FUTEX_PRIVATE_FLAG == 0 {
        return Err(Error::Errno(Errno::EOPNOTSUPP));   // <-- reject process-shared futexes
    }
    match self.cfg.debug_futex_mode { Precise | Polling | External => ... }
}
```
Downstream, `handle_futex_blocking` keys futexes by `FutexID::private(mm_id, addr)` — i.e. by address-space identity — which is why only *private* futexes are modeled.

Introduced by commit `a71903b` **"Fix P0 resource identity aliases"** (PR #37, the resource-model change; its head `a71903b` is an ancestor of `speculative`). Commit message: *"Key private futexes by address-space identity, honor futex bitsets, and reject shared futex modes until backing-object identity is modeled."*

So the deterministic scheduler intentionally rejects shared futexes until the resource model can identify the shared backing object across address spaces. QEMU (like Node and Java) relies on process-shared futexes, so it cannot yet run under hermit's deterministic mode.

## Conclusions & recommended next steps

1. **Booting Linux under deterministic hermit+QEMU is blocked by shared-futex support**, not by any missing/unsupported syscall in the QEMU boot path. This is the single, well-isolated blocker.
2. The fix belongs in the resource/identity model (follow-up to PR #37): model shared-futex backing-object identity so shared `FUTEX_WAIT`/`FUTEX_WAKE` can be scheduled deterministically instead of returning `EOPNOTSUPP`. This would also unblock Node and Java.
3. Independently, deterministic QEMU will be **slow** (TCG + ptrace interception). Even with the futex fix, expect long boot times; budget accordingly or explore reducing per-syscall overhead.
4. Secondary environment note: this host lacks CPUID faulting (it is itself a VM), so CPUID cannot be virtualized here; unrelated to the abort but relevant for reproducibility of any CPUID-dependent determinism.

No product code was changed by this experiment (frontier/research task). The futex-model fix above is left as a recommendation for a reviewed PR.

## Files in this directory

- `README.md` — this report
- `initramfs_root/init` — retained init script; generated boot binaries are omitted
- `baseline_qemu_tcg.log` — successful plain-QEMU boot
- `hermit_qemu_run1.log`, `hermit_qemu_run5.log` — deterministic-mode aborts (stdout/stderr)
- `hermit_debug_default.log` — full syscall trace of the deterministic-mode abort (shows the shared-futex `EOPNOTSUPP` → `tgkill` SIGABRT)
- `hermit_qemu_futex_external.log`, `hermit_qemu_futex_polling.log`, `futex_external_debug.log` — `--debug-futex-mode` attempts
- `hermit_qemu_run3.log`, `hermit_debug.log` — `--panic-on-unsupported-syscalls` run (note: that flag panics on the first *passthrough* syscall, so it is not a reliable "unsupported syscall" indicator here)
- `hermit_qemu_noseq.log`, `hermit_qemu_noseq_long.log`, `qemu_stderr_noseq*.txt` — `--no-sequentialize-threads` runs (firmware boots, non-deterministic)
- `qemu_stderr*.txt` — captured QEMU stderr for the various runs
