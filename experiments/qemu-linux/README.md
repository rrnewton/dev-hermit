# QEMU + Linux under Hermit — experiment harness

Self-contained, version-controlled harness for booting a minimal x86_64 Linux
guest with QEMU, both **bare** (baseline) and **under Hermit** (deterministic-
compatibility profile). Consolidates the prior scattered material into one place.

- **Text artifacts** (this dir, committed): `setup.sh`, `boot.sh`, `qemu_init.c`,
  `README.md`.
- **Large binaries** (`../../ignored/qemu-linux/`, gitignored): `bzImage`,
  `initramfs*.cpio.gz`. Regenerate anytime with `./setup.sh`.

## Quick start

```bash
cd ~/work/dev-hermit/experiments/qemu-linux
./setup.sh                 # copies kernel + builds both initramfs images into ignored/
./boot.sh bare             # QEMU only (TCG), no hermit  -> HERMIT-QEMU-BASELINE-BOOT-OK
./boot.sh bare kvm         # QEMU only, KVM acceleration
./boot.sh hermit           # QEMU under hermit           -> SHARED_FUTEX_QEMU_KERNEL_OK
```

All tools (`qemu-system-x86_64` 10.1.0, static `busybox`, `gcc`, `cpio`, `gzip`)
are pre-installed on the devserver — nothing is downloaded. If you ever need to
fetch, prefix commands with `with-proxy`.

## The two profiles

| profile | initramfs | init | marker | determinism |
|---------|-----------|------|--------|-------------|
| **bare**   | busybox (`initramfs.cpio.gz`)        | busybox `/init` (shell or autotest) | `HERMIT-QEMU-BASELINE-BOOT-OK` | n/a — QEMU straight on host |
| **hermit** | freestanding (`initramfs-hermit.cpio.gz`) | static `qemu_init.c` (marker + poweroff) | `SHARED_FUTEX_QEMU_KERNEL_OK` | **compat, not full** VM determinism |

The hermit profile is a **compatibility** profile: `--no-sequentialize-threads`
lets Linux schedule QEMU's host threads concurrently, so their interleavings are
**not** controlled by Hermit. It combines Hermit's virtual time with QEMU's
fixed instruction-count clock and boots to the marker.

## Hermit flag profiles (why each flag)

The verified working `hermit run` invocation (in `boot.sh hermit`):

```bash
hermit --log error run \
  --no-sequentialize-threads \
  --preemption-timeout disabled \
  --no-virtualize-cpuid -- \
  qemu-system-x86_64 -m 256M \
  -accel tcg,thread=single -smp 1 \
  -icount shift=0,sleep=off \
  -kernel <bzImage> -initrd <initramfs-hermit.cpio.gz> \
  -display none -serial stdio -monitor none -no-reboot \
  -append 'console=ttyS0 panic=-1 rdinit=/init'
```

| flag | why |
|------|-----|
| `--no-sequentialize-threads` | QEMU has a CPU-bound TCG vCPU thread plus main-loop/helper threads that must service timers, I/O, and wakeups. Full Hermit sequentialization made a bounded boot too slow to reach firmware serial output. This lets QEMU's host threads run concurrently. **Cost:** QEMU host-thread scheduling is no longer deterministic. |
| `--preemption-timeout disabled` | Disables Hermit PMU retired-conditional-branch preemption for this run. Keeping preemption while sequentialized let the TCG vCPU starve QEMU's other threads. |
| `--no-virtualize-cpuid` | Required on the evidence host, which lacked usable CPUID faulting. Exposes host CPUID; independent of scheduling/clock. A host where Hermit's CPUID virtualization works may omit this. |
| `-accel tcg,thread=single -smp 1` | Keeps the emulated guest to a single TCG vCPU. Does **not** serialize QEMU's host-side support threads. |
| `-icount shift=0,sleep=off` | **Critical.** Makes QEMU derive guest TSC *and* device timers (PIT/APIC/PM) from one instruction-derived virtual clock. `shift=0` = 1 ns per guest instruction; `sleep=off` disables pacing to host wall time. Without it, Linux compares divergent clock domains and fails calibration (see below). |
| `panic=-1 rdinit=/init` | Kernel reboots immediately on panic; runs our freestanding `/init` directly from initramfs. |

### Clock calibration: why `-icount` (or a Hermit-side workaround) is needed

Under Hermit, the emulated TSC observes a per-thread synthetic `rdtsc`, while
PIT/APIC/PM timers observe virtualized `CLOCK_MONOTONIC`. Those two Hermit time
bases advance **independently** (and, under `--no-sequentialize-threads`, are
per-thread), so the nested Linux guest compares mutually inconsistent clock
domains during calibration and, without `-icount`, fails:

```text
tsc: Unable to calibrate against PIT
clocksource: timekeeping watchdog ... 'tsc-early' skewed ... ns
clocksource: No current clocksource.
tsc: Marking TSC unstable due to clocksource watchdog
```

Two independent fixes (pick one):
1. **QEMU side (used here):** `-icount shift=0,sleep=off` — one instruction-
   derived virtual clock for both guest TSC and device timers.
2. **Hermit side:** `--no-virtualize-time --no-virtualize-metadata` — QEMU reads
   real, mutually consistent host clocks. Sacrifices time determinism for the
   whole run but calibrates normally.

`hermit run` prints a one-line advisory when it launches a `qemu-system-*`
program while virtual time is enabled, pointing at both workarounds (issue #6).

## Expected output

**bare:**
```text
==========================================
HERMIT-QEMU-BASELINE-BOOT-OK
kernel: <kernel-release>
==========================================
```
(With `hermit_autotest` on the cmdline — as `boot.sh bare` sets — it also prints
`HERMIT-QEMU-AUTOTEST-DONE` and powers off instead of dropping to a shell.)

**hermit:** boot reaches the initramfs marker and powers off (~13.25 s wall on
the evidence host), with a coherent 1000.031 MHz TSC and none of the PIT /
watchdog / no-clocksource warnings:
```text
SHARED_FUTEX_QEMU_KERNEL_OK release=<kernel-release> machine=x86_64
reboot: Power down
```

## Mode-comparison matrix (evidence)

Six-mode sweep from the preserved debug experiment. The only complete boot is
`virtual_minimal_fixed_icount` (virtual time on, no sequentialization, no
preemption, fixed icount):

| mode | virtual_time | seq_threads | preemption | qemu_icount | wall_s | exit | serial | clock | conclusion |
|------|:-:|:-:|:-:|:-:|--:|:-:|--|--|--|
| default_virtual_trace | yes | yes | 200 ms | none | 20.0 | timeout | none | not_reached | scheduler+time polling bottleneck |
| no_virtual_time_control | no | yes | 200 ms | none | 90 | timeout | none | host_time | virtual time not sole cause |
| virtual_no_sequentialization | yes | no | 200 ms | none | 30 | timeout | SeaBIOS | not_reached | disabling scheduler restores serial |
| virtual_seq_no_preemption | yes | yes | disabled | shift0,sleepoff | 30 | SIGKILL | none | not_reached | CPU-bound vCPU starves other threads |
| virtual_minimal_no_icount | yes | no | disabled | none | 30 | timeout | kernel console | no_current_clocksource | local/global time domains diverge |
| **virtual_minimal_fixed_icount** | **yes** | **no** | **disabled** | **shift0,sleepoff** | **13.25** | **0** | **initramfs marker** | **coherent 1000.031 MHz** | **complete boot** |

## sched_ext (SCX) notes

`sched_ext` is the in-kernel BPF-programmable scheduler framework (`SCHED_EXT`).
It is **not** used by this harness, and its relationship to Hermit is worth
being explicit about:

- **Guest side:** the minimal busybox/freestanding initramfs runs no BPF
  scheduler, and the profile pins the guest to a single vCPU (`-smp 1`), so
  `sched_ext` inside the guest is irrelevant to reaching the boot marker. To
  *experiment* with an SCX guest scheduler you would need a kernel built with
  `CONFIG_SCHED_CLASS_EXT=y`, `>1` vCPU, and a userspace BPF scheduler in the
  initramfs — out of scope for the boot smoke test.
- **Host side:** Hermit's own determinism comes from *user-space* scheduling
  (thread sequentialization + PMU retired-branch preemption), which this profile
  deliberately **disables** for QEMU. That is orthogonal to host `sched_ext`.
  However, a host BPF scheduler changes how the Linux CFS/EXT scheduler
  interleaves QEMU's now-concurrent host threads — precisely the interleavings
  this compat profile leaves *un*controlled — so an active host `sched_ext`
  policy is a potential source of run-to-run variation in the hermit profile's
  QEMU thread timing. Note it when reproducing timing-sensitive results.
- **Reference:** the project's preemption record/replay design draws on the
  `scx-sim` prototype (PMU-slice recording/replay); see
  `ai_docs/nondeterministic-preemption-record-replay.md`. That is about Hermit's
  replay backend, not about running SCX in this QEMU guest.

A fully coherent multi-clock Hermit model (single time base shared by `rdtsc`,
`clock_gettime`, and derived device clocks, coordinated across threads) would
remove the need for `-icount`/host-clock workarounds but is out of scope here.

## Troubleshooting

- **No serial output before timeout:** confirm both `--no-sequentialize-threads`
  and `--preemption-timeout disabled` are present. Default sequentialization is
  functionally live but too slow for the bounded boot.
- **PIT calibration / TSC watchdog errors:** confirm exact
  `-icount shift=0,sleep=off`; do not replace with host-clock pacing.
- **CPUID faulting error:** retain `--no-virtualize-cpuid`.
- **Immediate QEMU futex rejection:** use a Hermit revision with deterministic
  process-shared futex support.
- **Timeout cleanup:** keep `timeout --signal=KILL`; a sequentialized negative
  control may not process `SIGTERM` while a tracee is stopped.

## Provenance

Consolidated 2026-07-23 from (originals left intact in the `hermit/` submodule):
- `hermit/docs/QEMU_BOOT.md` — flag rationale, clock analysis, issue #6.
- `hermit/experiments/qemu-boot-debug/{smoke_test.sh,results.csv,metadata.json,README.md}`
  — six-mode comparison and host/binary metadata.
- `hermit/experiments/shared-futex-verify_20260722/qemu_init.c` — freestanding init (copied here).
- `ignored/qemu-linux/` — the busybox baseline artifacts (from impl-qemu-setup-environment).
