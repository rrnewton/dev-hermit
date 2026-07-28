# Userspace programs run deterministically inside a QEMU Linux VM under Hermit

**Task:** `frontier-linux-hermit-parallel` — parallel Linux-hermit milestone.
**Milestone chosen:** #1 *Userspace programs inside the QEMU VM under Hermit*
(chosen because a concurrent agent already owns the shared
`ignored/qemu-linux/initramfs` tree for the network/multi-VM milestones 2–3;
this experiment is fully isolated and touches no shared file).

## Question

Do ordinary userspace programs run to completion **inside** a QEMU Linux guest
that is itself executing as a userspace process under Hermit, and is their
output **deterministic** across independent Hermit runs?

## Method

A self-contained busybox initramfs (`build-initramfs.sh`) whose `/init` is a
program battery (`userspace-battery.sh`). The battery runs a spread of
userspace programs inside the guest and prints their output between
`USERSPACE-BATTERY-BEGIN` / `USERSPACE-BATTERY-END`, then powers off. The
programs cover:

- shell integer arithmetic (`sum_1_100`, `pow2_16`, `mod`)
- `seq` + `awk` numeric pipelines (`seq_sum`, `seq_sq`)
- text processing: `sort -u`, `uniq -c`, `wc`
- checksums of fixed content: `md5sum`, `sha256sum`, `cksum`
- deterministic data generation + `md5sum` of the result
- Hermit-virtualized identity syscalls: `getpid` (`$$`), `id -u`, `date -u`,
  `hostname`
- filesystem round-trip: create/read/list files

QEMU runs single-vCPU TCG with a fixed instruction-count clock
(`-icount shift=0,sleep=off -rtc base=utc,clock=vm`), the configuration
`docs/QEMU_BOOT.md` establishes as required for coherent guest clock
calibration. Two Hermit profiles were exercised (see `run-boot.sh`):

- **compat** — `--no-sequentialize-threads --max-timeslice disabled
  --no-virtualize-cpuid` (fast, ~18-20s wall).
- **strict** — default serialized scheduler, `--no-virtualize-cpuid` only
  (Hermit's real determinism guarantee; ~3 min wall, guest powered down at
  guest-time 1.633s).

`--no-virtualize-cpuid` is the host-specific workaround from `docs/QEMU_BOOT.md`
(this host lacks usable CPUID faulting); it is orthogonal to scheduling/clock.

## Result — PASS (determinism level L1)

Three independent boots — **compat-run1, compat-run2, strict-run1** — each
exited 0 and produced a payload with the **identical SHA-256**:

```
91d93f3e5887a5133cd877b530d2cd5197dc26099aa1f0b6a03a70c5bbbe46c6
```

`results.csv` (generated from the captured logs, not hand-written) lists all 19
recorded output keys; every key is identical across all three runs (0
mismatches). Highlights:

- Computation is correct: `sum_1_100=5050`, `seq_sum=500500`,
  `md5_fox=9e107d9d372bb6826bd81d3542a419d6`,
  `sha256_fox=d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592`.
- Hermit virtualization is visible and stable **inside the nested guest**:
  `pid=1`, `uid=0`, and especially `date_utc=2026-01-01T00:00:06` — Hermit's
  virtual clock (epoch 2026-01-01) reproduces to the second across all runs,
  including across the compat/strict profile boundary. On bare metal `date`
  would vary run-to-run; here it is deterministic.

The compat profile does **not** control QEMU's host-thread interleaving, yet
the payload still matches the strict profile, because the payload is a
deterministic function of guest computation plus Hermit-virtualized syscalls —
independent of QEMU host-thread scheduling.

**Milestone #1 achieved:** userspace programs execute to completion inside the
QEMU VM under Hermit, deterministically (L1).

### Scope / honest limits

- **L1, not L2.** Determinism was shown by comparing independent runs, not with
  Hermit `--verify` record/replay. QEMU-boot `--verify` is a known-divergent
  path (thread-3 replay divergence), so L2 was not attempted here.
- Strict run wall time was not captured to the second (it was polled in the
  background); the guest-internal power-down was at guest-time 1.633s.
- Single guest kernel (host's 6.17.13 bzImage) and single busybox userland.

## Reproduce

```bash
cd experiments/qemu_userspace_determinism_20260728
./build-initramfs.sh                       # builds userspace-initramfs.cpio.gz
./run-boot.sh compat-run1.log compat
./run-boot.sh compat-run2.log compat
./run-boot.sh strict-run1.log strict       # ~3 min
# payload determinism check:
P(){ sed -n '/USERSPACE-BATTERY-BEGIN/,/USERSPACE-BATTERY-END/p' "$1"; }
for f in compat-run1 compat-run2 strict-run1; do P $f.log | sha256sum; done
```

All three SHA-256 values must match.

## Files

- `userspace-battery.sh` — the guest `/init` program battery.
- `build-initramfs.sh` — builds the isolated initramfs (output gitignored).
- `run-boot.sh` — boots QEMU-under-Hermit; `compat` | `strict`.
- `compat-run1.log`, `compat-run2.log`, `strict-run1.log` — full serial consoles.
- `results.csv` — per-key cross-run comparison (generated).
- `metadata.json` — SHAs, host facts, commands, result.
- `.gitignore` — excludes regenerable binaries (`rootfs/`, `*.cpio.gz`).
