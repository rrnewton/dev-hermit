# demo5 QEMU `-icount sleep=on` idle-warp vs baseline `sleep=off`

**Task:** `demo5-fix-qemu-icount-idlewarp` (SPECULATIVE fork #1, branch-only, no-land).
**Date:** 2026-07-31. **Agent:** hermit-226 (opus-4.8).

## Question

Does passing `-icount shift=0,sleep=on` (idle-warp) instead of `sleep=off`
(busy-warp) to the demo5 QEMU busybox boot, run under `hermit run --strict`,
resolve the deadline-less QEMU BQL/iothread futex-poll livelock and boot demo5
green (multi-run)?

Hypothesized mechanism: under `sleep=on`, an idle vCPU issues a real
`clock_nanosleep`, which under Hermit registers a **future** `SleepUntil`
deadline. Detcore's `step2d` forward-jump could then advance virtual time to
that deadline, breaking the poller storm that leaves demo5 crawling at guest
HPET init.

## Method

- Bare QEMU busybox boot only (`demos/boot_qemu.sh`), `-serial stdio`, **no**
  python controller and **no** QMP socket — isolates the QEMU/Hermit scheduler
  interaction from the controller poll loop.
- Two invocations differ by exactly one word: `sleep=off` (baseline, =current
  main) vs `sleep=on` (treatment).
- `hermit/target/release/hermit @ origin/main 0ca0dec2`, ptrace backend, plain
  `hermit run --strict` (RCB-time ON; NOT `--no-rcb-time`).
- PASS = guest emits `HERMIT-QEMU-BUSYBOX-PASS` then `reboot: Power down`
  (guest serial ts ≈ 1.90).
- **Clean sequential runs, one at a time.** An early concurrent pair was
  discarded as a CPU-starvation artifact (a `sleep=on` run under contention
  showed anomalously few scheduler turns and empty console). Heavily-shared
  host (316 cores), load recorded per run (~35–55).
- Scheduler diagnostics from `--log info` (`COMMIT turn` count, `Skipping
  global time ahead` = the step2d forward-jump count).

See `metadata.json` for exact SHAs, kernel/busybox hashes, QEMU version, and
the runner. Raw per-run logs are in `scratch/demo5-icount-sleep/out/`.

## Results

See `results.csv`. Adequately-budgeted clean sequential runs:

| Variant  | Verdict | Wall | Last guest serial ts | step2d jumps |
|----------|---------|------|----------------------|--------------|
| sleep=on | PASS    | 325s | 1.905795 (power down) | 0 |
| sleep=on | PASS    | 323s | 1.905795 (power down) | 0 |
| sleep=on | PASS    | 323s | 1.905795 (power down) | 0 |
| sleep=off (baseline) | PASS | 323s | 1.903274 (power down) | 0 |
| sleep=off (baseline) | PASS | 328s | 1.903274 (power down) | 0 |

Multi-run: `sleep=on` 3/3 PASS (325/323/323s); baseline `sleep=off` 2/2 PASS
(323/328s). Both boot reliably; wall times overlap.

(Under-budgeted 300s runs of both variants timed out mid-crawl at ts ≈ 1.5,
and are recorded as such — they were not hard-wedged.)

## Interpretation

**`-icount sleep=on` is NEUTRAL under `hermit run --strict` — not a fix, and
not needed.**

1. Under `--strict`, RCB-time creeps forward every scheduler turn, so demo5 is
   **not** hard-deadlocked — it crawls to a successful boot in ~320–325s given
   enough wall budget. Both variants boot in indistinguishable wall time; the
   baseline booted under *higher* load. Earlier 300s "wedge" reports were
   under-budgeted, not deadlocked.
2. The always-runnable-vCPU conflict is real but moot: `sleep=on` does register
   future `SleepUntil` deadlines that `sleep=off` never produces, yet `Skipping
   global time ahead` = **0** in both — a QEMU thread's per-turn
   `SleepUntil(LogicalTime(0))` keeps the run queue non-empty so `step2d` never
   consumes the deadline. RCB creep completes the boot regardless.
3. The hard, deadline-less BQL/iothread futex livelock that `sleep=on` could
   help is a **`--no-rcb-time`** regime phenomenon, not a `--strict` one.

**Recommendation:** do not land. The demo5 residual is a scheduler-side problem
(unproductive-poller / vCPU-wakeup starvation — see sibling fork
`demo5-fix-detcore-deadlineless` and `scheduler-vtime-jump-unproductive-pollers`),
not a guest `-icount` flag. Retain `sleep=on` only as a possible operational
fallback for `--no-rcb-time`.

## Reproduction

```bash
cd ~/work/dev-hermit/scratch/demo5-icount-sleep
# runner args: <variant off|on> <run-index> <timeout-secs>; runs ALONE (no concurrency)
./run_variant.sh off 1 700   # baseline
./run_variant.sh on  1 700   # treatment
# PASS = grep -F HERMIT-QEMU-BUSYBOX-PASS out/<variant>-run<N>/console.log
```

`run_variant.sh` invokes `hermit --log info --log-file … run --strict --
boot_qemu_<variant>.sh $KERNEL $INITRD $QEMU`. Requires the prebuilt release
hermit at SHA `0ca0dec2`, the kernel/initramfs in `metadata.json`, and system
`qemu-system-x86_64` 10.1.2. Boot wall time is load-sensitive (~320s under
load ~40; faster on a quiet host).
