# Differential repro attempt: btrfs subvolume-deletion inodes-xarray race (f6a6c280 / CVE-2025-39884) under QEMU-under-Hermit chaos

**Task:** `goal-repro-btrfs-f6a6c280`. Date: 2026-07-29. Research (no product PR).

## Question

Can Hermit's deterministic **chaos scheduler**, applied to a full Linux kernel
running as a userspace `qemu-system-x86_64` guest, reproduce the btrfs
subvolume-deletion **inodes-xarray ABA race** (bug `9786531`, fixed by
`f6a6c280`, CVE-2025-39884) — a narrow inter-CPU race that upstream only
triggers via an *unbounded* shell loop — and if so, produce a **bitwise
witness seed** for it?

## The bug (from the fix commit message)

```
Thread 1 (evict, driven by drop_caches)     Thread 2 (iget, driven by stat)
evict()
  remove_inode_hash()
                                             btrfs_iget_path()
                                               btrfs_read_locked_inode()
                                                 btrfs_add_inode_to_root()  [inserts B]
  destroy_inode()
    btrfs_destroy_inode()
      btrfs_del_inode_from_root()
        __xa_erase   [buggy: erases B *by key* => live inode B lost from xarray]
```

If the lost inode B owns a `delayed_node`, subvolume cleanup loops forever in
`btrfs_kill_all_delayed_nodes()` → soft lockup. The buggy `__xa_erase(key)` is
replaced by `__xa_cmpxchg(key, expected_inode, NULL)` (identity check) in the
fix — a +11/-1 change to `fs/btrfs/inode.c`, the ONLY source difference between
the two kernels below.

## Method

**Owner scope (honored):** kernels + btrfs image built NATIVE + full-speed
**once** and snapshotted as the deterministic starting point; the iterate loop
is only *boot-from-artifacts + controlled RUN* under Hermit — no heavy builds in
the loop.

### Differential kernels (built once; see `../../ignored/btrfs-kernel-build/artifacts/MANIFEST.md`)

| kernel | commit | describe | md5 |
|---|---|---|---|
| buggy | `9786531399a679fc2f4630d2c0a186205282ab2f` | `6.16.0-rc7-00239-g9786531399a6` | `8615440722b02318e92df9c4523c003b` |
| fix   | `f6a6c280059c4ddc23e12e3de1b01098e240036f` | `6.16.0-rc7-00240-gf6a6c280059c` | `4bc5f1a869c26b60b114fbe6096c0ce8` |

Identical config (`x86_64_defconfig` + `hermit-btrfs.fragment`), `md5
7d39f6ac67317130800a76624da814e3`. **`CONFIG_PREEMPT_NONE=y`** (upstream
reproducer requirement) + `SOFTLOCKUP_DETECTOR` + `DETECT_HUNG_TASK` (oracle).

### In-guest reproducer + oracle

`subvol-race.c` (in `../../ignored/btrfs-f6a6c280-repro_20260729/`) is a faithful
C translation of the upstream shell reproducer
(https://www.spinics.net/lists/linux-btrfs/msg157605.html), wired to a marker
oracle on the serial console:

- `BTRFS_F6A6C280_ORACLE_ARMED iteration=N` — per iteration, at the trigger boundary
- `BTRFS_F6A6C280_ORACLE_PASS iterations=N` — once, only after a **clean umount**
- `BTRFS_F6A6C280_ORACLE_FAIL reason=TEXT` — on setup/workload error

Per iteration: create subvolume → target file + N hard links (each under its own
dir, upstream-faithful; or all in one dir in `onedir` mode) → fork N `lstat`
busy-spin racers (Thread-2 iget drivers) → `drop_caches=2` ×M (Thread-1 evict
driver) → pin the target via an `inotify` watch with no dentry (the "lost inode"
candidate) → hold a dummy fd (last other tracked inode) → delete subvolume →
reap racers → second drop → close dummy (arm) → `MS_REMOUNT` (kick cleaner) →
release watch → settle drop. **Terminal gate:** `umount()` requires
`kthread_stop(cleaner)`; if any iteration reproduced the race the cleaner spins
in `btrfs_kill_all_delayed_nodes()` and umount blocks forever → the whole run
hangs after ARMED → oracle **BUG_HANG** (bitwise-reproducible witness seed). On
the fixed kernel the cleaner completes, umount succeeds → **PASS**.

Initramfs: busybox-static + bundled glibc; a 128 MB btrfs image `dd`'d into a
`brd` `/dev/ram0` ramdisk. `root/init` parses `race.iters/budget/nlinks/ndrops/
onedir` from the kernel cmdline so a sweep varies amplification without
rebuilding.

### Hermit chaos invocation (one (rng,sched) point; bitwise reproducible)

```
RUST_LOG=warn hermit run --chaos --rng-seed R --sched-seed S \
  --no-rcb-time --target-timeslice <TS> --max-timeslice 1000000000 \
  <WIDEN...> -- \
  qemu-system-x86_64 -machine q35 -accel tcg -cpu max -smp <N> -m 512M \
  -display none -monitor none -serial mon:stdio -no-reboot \
  -icount shift=0,sleep=off -rtc base=2022-01-01T00:00:00,clock=vm \
  -kernel <bzImage-btrfs-{buggy-9786531,fix-f6a6c280}> \
  -initrd initramfs-subvol-race.cpio.gz \
  -append "console=ttyS0 panic=-1 rdinit=/init race.iters=I race.budget=B race.nlinks=L race.ndrops=D race.onedir=O"
```

`WIDEN` = `--chaos-per-thread-slowdown --chaos-slowdown-max-factor F
--chaos-epoch-length-ns E --fuzz-futexes --chaos-target-races` — epoch-based
per-thread slowdown re-draws slowdown factors periodically to widen the
evict-vs-iget window; `--chaos-target-races` biases the scheduler toward
racy memory access; a **smaller** `--target-timeslice` switches guest vCPUs more
often, raising the chance a switch lands inside the short evict critical section.

## Progressive sweeps (each a fresh detached run; poll-only, never streamed)

| sweep | knobs | seeds × iters | attempts | result |
|---|---|---|---|---|
| sweep1 | SMP=2, TS=100k, NLINKS=4/NDROPS=8, F=100/E=2ms | 15 pairs × ~24 | ~360 | all PASS; one boot-livelock (F=100 starved SMP bringup) → OTHER |
| sweep2 | SMP=2, TS=10k, NLINKS=16/NDROPS=16, F=12/E=1ms | 12 pairs × ~24 | ~288 | all PASS |
| sweep3 | SMP=4, TS=4k, NLINKS=32/NDROPS=32, F=12/E=1ms | 4 pairs × 200 | 800 | all PASS |
| sweep4 | sweep3 + **ONEDIR=1** (dilution-reduced) | 6 pairs × 500 | 3000 | all PASS |

**Total: ~4448 amplified deterministic attempts across all four sweeps — zero
reproductions.** Every buggy-kernel run reached the terminal `umount` cleanly
(`BTRFS_F6A6C280_ORACLE_PASS`), i.e. the cleaner never got stuck in
`btrfs_kill_all_delayed_nodes()`. The one non-PASS (sweep1 point #7) was a boot
livelock from an over-aggressive slowdown factor (100), correctly classified
`OTHER` (armed=0), not a bug hit.

`onedir` mode packs all hard links into one directory so each `drop_caches` pass
has far fewer competing inodes, making the target's unhash→erase window a larger
temporal fraction of the eviction — the cheapest remaining lever to land a chaos
preemption inside it.

## Validation of the harness itself (not the bug)

- **Fixed kernel** `f6a6c280` boots + PASSes cleanly under the identical chaos
  config (cleaner completes, umount returns) — confirms the oracle's PASS arm
  and that the harness is not spuriously hanging.
- **Buggy kernel** `9786531` boots + runs the reproducer to completion every
  attempt (ARMED fires each iteration; PASS at end) — confirms the workload
  executes the full evict/iget/delete sequence deterministically under `-icount`.
- **Native full-speed** (KVM, `-cpu host`, no icount): buggy kernel PASSed 200
  iters with sustained racers — i.e. even at native speed the narrow inter-CPU
  window did not fall out, motivating the chaos lever.

## Results / interpretation

**FINAL: negative result.** Across all four sweeps — ~4448 amplified
deterministic QEMU-under-Hermit chaos attempts spanning broad seed coverage
(15+12 widely-spread (rng,sched) pairs), deep grinding (up to 500 iters/seed),
maximum practical concurrency (SMP=4), the finest cheap scheduling granularity
(target-timeslice 4k), heavy per-iteration amplification (32 links / 32 drops),
epoch-based per-thread slowdown + fuzz-futexes + target-races, and a
dilution-reduced single-directory layout — **zero reproductions.** This is
consistent with the task's STRATEGIC
premise: **blind chaos seed-sweeping is the wrong tool for localizing a race
this narrow.** The upstream reproducer itself loops *unbounded*; the window
(between `remove_inode_hash()` and `__xa_erase` on one CPU, versus
`btrfs_add_inode_to_root()` on another) is a handful of instructions, and Hermit
chaos perturbs the *guest thread* interleaving but does not (yet) have a
mechanism to *pin a happens-before edge* across that exact pair of kernel code
points. That capability is **RFC #1146 (happens-before edges), which is not
landed** — the intended, non-lottery lever named in the task's coordinator note.

If a witness is found, this file records the exact `(rng, sched, iteration)`,
confirms the fixed kernel PASSes at the same config, and captures a
`--record-preemptions-to` replay log; GitHub #1130 is then updated.

## Reproduction

All scripts + artifacts are gitignored (heavy/binary) under
`../../ignored/btrfs-f6a6c280-repro_20260729/` (reproducer, initramfs, `build.sh`,
`run-hermit.sh`, `run-native.sh`, `sweep{1,2,3,4}-hermit.sh`) and
`../../ignored/btrfs-kernel-build/` (kernels + MANIFEST). `metadata.json` here
pins every SHA, and `logs/` holds the per-sweep summary logs (small, textual).
Re-run e.g.: `cd ignored/btrfs-f6a6c280-repro_20260729 && ./build.sh &&
./sweep4-hermit.sh buggy 500 6000 sweep4`.
