# Linux VM roadmap snapshot, 2026-07-26

## Question

What is the current state of Linux/QEMU testing under Hermit, and what should
be done next for strict verification, snapshots, userspace, sched_ext, chaos,
and record/replay?

## Inputs

- Current strict probe Hermit: `fb5f201487d2a135ad455a4d5b08c86718359c8a`
- Current strict probe binary SHA-256:
  `21ff6e012b1d357582d892822d4ff3befb6f938eaeeca5756114b47717469480`
- Fail-open control Hermit: `54ff993753a62dd174f4af8aff4ed306c2589766`
- Fail-open control binary SHA-256:
  `05e4cf08bd6c01fea40bd32ba5507fba0a26276f155b4552c710a83e3c7ce95a`
- QEMU: `10.1.0 (qemu-kvm-10.1.0-21.el9)`
- Kernel SHA-256:
  `e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2`
- Current strict probe initramfs SHA-256:
  `b54056eab4a2c939e946b9c15eeadf45fb3cff8f9b5e75f5f884657802543292`
- Fail-open control initramfs SHA-256:
  `e430910ceb247c9111215a75e0fe2c38381a86382ad4878183b0a29343d5249d`
- Host: Linux `6.17.13-0_fbk0_crackerjackhost_0_g2b4321c50d79`, x86_64
- `perf_event_paranoid`: `1`

The primary checkouts were dirty with unrelated work, and the worktree
registry was already above its documented active-slot limit. No checkout was
modified. Each exact `origin/main` tree was exported with `git archive` into
temporary storage, built there, and all run artifacts were written under
ignored `scratch/linux-vm-roadmap-20260726/`. This is a limitation relative to
the requested canonical worktree procedure, not a relaxation of the measured
Hermit command.

## Live current-main result

The maintained literal strict gate does not reproduce its July 24 result on
current main. It exits before any QEMU serial output at:

```text
seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, NULL)
```

`bf1cab33` (PR #644) changed explicit `--strict` to fail immediately on every
syscall classified as unsupported. `seccomp` remains unsupported, so Detcore
stops at QEMU startup syscall 640. A native `strace` control confirms this is a
capability probe and Linux deterministically returns `EFAULT`:

```text
seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, NULL) = -1 EFAULT
```

The current result is therefore **FAIL before L1** for the ptrace backend,
INFO log, `--strict`, no relaxations.

The matched fail-open control at the immediately preceding `54ff993` keeps Hermit's default sequentialized scheduler,
deterministic I/O, virtual time, and PMU preemption, but does not pass the
explicit `--strict` flag. It booted Linux, ran `/init`, printed
`SHARED_FUTEX_QEMU_KERNEL_OK`, and powered down. The console was 21,626 bytes.
A subsequent `run --verify` control completed both executions and reported:

```text
Logs contain 1130696 | 1130696 messages total
Logs contain 852621 | 852621 DETLOG & scheduler COMMIT messages
Done processing logs, no substantive differences found.
WARNING: syscalls readv,seccomp used but not yet supported
:: Success: deterministic. Determinism verified.
```

This isolates the regression to strict unsupported-syscall policy. It is useful
diagnostic evidence, but it is **not an L2 claim** because `--strict` was not
present.

The fail-open oracle also emitted 656 `PMU RCB overshoot` ERROR lines. Many
reported identical actual and target counts. The implementation tests
`delta_rcbs >= last_timer`, so an exact on-target trap is currently mislabeled
as an overshoot. The comparison still matched, but this telemetry cannot be
used to quantify real skid until the equality boundary is corrected.

## Roadmap status

| Area | Best evidence | Current assessment |
| --- | --- | --- |
| Cold boot | L2 passed at `fe97efd` on 2026-07-24 | Regressed on current main by strict rejection of QEMU's seccomp probe. |
| Snapshot/resume | Bare and relaxed resume worked; old strict probes stopped at clone and then `ppoll` | Stale. Clone ordering and deterministic `ppoll` later landed, so retest after the seccomp fix. Use immutable qcow2 input plus per-run copies because `loadvm` writes metadata. |
| Userspace app | Rich BusyBox/non-root workload ran only in the relaxed profile; minimal strict `/init` reached L2 | Add a durable, source-built nontrivial userspace fixture and run it at strict L2. |
| sched_ext | `scx_rlfifo` plus four CPU workers passed strict L2 at `0c419bf` with 1,340,266 messages/run | Strong historical result, but its harness changes were uncommitted and its initramfs is machine-local. Reconstruct and land reproducible coverage after the strict fix. |
| Chaos/preemption | Seeds 1, 2, and 7 produced distinct, per-seed-repeatable schedules; boot remained correct but took roughly 48-360x more turns | Expand to userspace/sched_ext and timeslice settings after fixing overshoot telemetry. Throughput starvation, not correctness, was the observed limit. |
| Hermit R/R | Full QEMU boot records successfully; landed fd-reuse fix advances replay | End-to-end replay still fails. The validated zero-length read/pread fix was not committed; after that, replay reached an `mprotect` length divergence at thread 7 event 56647. |

## Follow-up tasks

- `p0_restore_qemu_strict`: determinize the narrow QEMU seccomp TSYNC probe and
  restore current-main strict L2.
- `p1_retest_qemu_snapshot`: rerun real VM-state resume under strict after the
  seccomp fix.
- `p1_add_a_durable`: add a reproducible strict-L2 Linux userspace workload.
- `p1_land_reproducible_strict`: land reproducible strict-L2 sched_ext coverage.
- `p1_resume_qemu_linux`: continue QEMU R/R through zero-read and `mprotect`
  divergences.
- `p1_correct_pmu_overshoot`: correct overshoot telemetry and measure seeded
  chaos/timeslice behavior through userspace and sched_ext.

## Reproduction

Build exact current main in an isolated checkout or source snapshot:

```bash
with-proxy cargo build --release -p hermit --bin hermit
```

Strict gate that currently fails before Linux boot:

```bash
HERMIT_BIN=target/release/hermit \
KERNEL_IMAGE=/home/newton/work/dev-hermit/ignored/qemu-linux/bzImage \
QEMU_L2_PHASE_TIMEOUT_SECONDS=420 \
  ./experiments/qemu-boot-debug/strict_l2_test.sh
```

Fail-open diagnostic comparison that still verifies:

```bash
target/release/hermit --log warn run --verify -- \
  qemu-system-x86_64 -m 256M -accel tcg,thread=single -smp 1 \
  -icount shift=0,sleep=off \
  -kernel /home/newton/work/dev-hermit/ignored/qemu-linux/bzImage \
  -initrd /absolute/path/to/initramfs.cpio.gz \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

Raw logs are intentionally ignored under
`scratch/linux-vm-roadmap-20260726/`.
