# demo5 Wedge Rate + Skid Characterization

**Role:** metrics evidence agent → lead scientist **hermit-226**.
**Task:** `demo5-baseline-metrics-table` (wedge-rate extension). **Feeds:**
[`ledger.md`](ledger.md) H1 (wedge mechanism), H4/Q3 (load-independence),
H6 (controller-specific). Companion to [`metrics.md`](metrics.md).
**Date:** 2026-07-31. **Host:** devbig014 (316 cores). **Backend:** ptrace.

## What "wedge" and "skid" mean here

- **Wedge** = a demo5 boot that never reaches the guest power-down marker
  (`HERMIT-QEMU-BUSYBOX-PASS`) within budget; the guest console freezes (for the
  controller harness, at HPET calibration ≈0.72–0.74 s) while the hermit
  scheduler keeps taking turns.
- **Skid** = how far the deterministic **committed virtual time** overshoots the
  guest's actual progress during a wedge. This is a **vtime skid**, and it is
  **distinct from PMU skid** (the reverie-ptrace "Clock perf counter exceeds
  target" overshoot). **Zero** PMU-skid lines appear in any demo5 log measured
  (wedge or green) — see the table below — so the demo5 wedge is **not** a
  perf-counter phenomenon.

## Skid characterization (from existing logs)

The single mechanism behind the skid is the **unproductive-poller micro-yield**:
a guest thread issues `SleepUntil(LogicalTime(0))` (wake me again *now*, zero
future deadline). Each such yield keeps the run queue non-empty, so the
scheduler's step-2d **virtual-time jump never fires** (it only jumps when the
run queue is empty); instead committed time **creeps** by a tiny per-turn
quantum. The guest makes no progress, but committed vtime skids arbitrarily far.

| signal | GREEN busybox (boots) | WEDGE controller `ae2565be` (hangs) | ratio |
|---|---:|---:|---:|
| `SleepUntil(LogicalTime(0))` poll-yields | **868** | **817,814** | ~942× |
| future-deadline waiters | 53 | ~a handful | — |
| guest console reached | `[1.903217]` (power-down) | `[0.741917]` (frozen, HPET) | — |
| committed vtime advanced | 220.9 s (productive) | 3,684.5 s (skid) | ~17× |
| guest vtime actually reached | ~1.9 s | ~0.74 s | — |
| **vtime skid (committed − guest)** | ~0 (tracks guest) | **≈3,683 s overshoot** | — |
| PMU "exceeds target" skid lines | 0 | 0 | — |

**Per-turn creep is the skid's fuel.** In the wedge log committed time advances
in ~500–625 µs steps per turn (`…600.000500s` → `…229284.508s` = +3,684.5 s over
978,552 turns), with `--no-rcb-time` in force. So the wedge's virtual-time
advance is:

- **~0 % RCB-driven** (branch retirement is off under `--no-rcb-time`),
- **~0 % real-timer / syscall-driven** (only a handful of future-deadline
  waakeups fire),
- **~100 % per-turn scheduler creep** (the ~500–625 µs minimum quantum applied
  to each of ~10⁶ unproductive poll-yields).

This directly answers hermit-226's source-bucket question (H1 vs H2): the wedge
vtime is pure scheduler creep with no productive source, whereas the green boot's
vtime is RCB-driven productive advance across ~1,000 real timeslices.

## Wedge rate (repeated trials)

**Method (corrected from v1).** Same busybox guest, same binary `670209ba`,
config `--strict` (rcb ON — the shipped demos-green path), run **serially** (one
boot at a time = controlled low added load) under a **generous 900 s timeout**,
and classified by **guest progress**, not by wall-clock:

- `BOOT_OK` — reached `HERMIT-QEMU-BUSYBOX-PASS`;
- `SLOW_BOOT` — no PASS but guest console ts advanced past ~1.0 s (censored, still progressing);
- `HARD_WEDGE` — no PASS **and** guest console ts frozen < 0.9 s (the livelock signature).

> **v1 correction (pivotal).** The first runner used a *fixed 430 s* timeout and
> called every over-budget run a "wedge". That mis-classifies a **load-slowed but
> still-progressing** boot as a wedge: a v1 "wedge" trial had climbed to guest
> `[1.454141]` with green-trajectory metrics — it was a slow boot, not a livelock.
> A true demo5 hard wedge is a **guest freeze** (console ts stuck < 0.9 s while
> turns climb, `SleepUntil(0)` explosion), so the classifier keys on guest
> progress. The apparent "~20 % wedge rate" on the busybox `--strict` path is this
> artifact — the wall-time tail crossing a fixed CI timeout — **not** a hard wedge.

Raw (private, data-hygiene compliant):
[`ignored/hermit-231-private/wedge-rate-strict.csv`](ignored/hermit-231-private/wedge-rate-strict.csv)
+ [`determinism-proof.txt`](ignored/hermit-231-private/determinism-proof.txt).
Host load during the batch ≈ 57–205 / 316 cores.

| metric | result across 7 serial trials |
|---|---|
| `BOOT_OK` | **7 / 7** |
| `SLOW_BOOT` | 0 / 7 |
| `HARD_WEDGE` | **0 / 7** |
| hard-wedge rate | **0 %** (busybox `--strict`) |
| wall time (range) | 328–345 s (varies with host load only) |
| turns | **41,411** (identical every trial) |
| elapsed vtime | **310 s** (identical) |
| timeslices | **874** (identical) |
| syscalls | **257,379** (identical) |
| guest console reached | **`[1.903217]`** power-down (identical) |
| `SleepUntil(LogicalTime(0))` | **868** (identical — green regime, not 10⁵–10⁶) |
| guest console md5 | **byte-identical across all 7** (`e477a852…`); info-log 81,982,708 B ×7 |

**Reading.** The busybox `--strict` path **never hard-wedges** (0/7; guest always
reaches power-down at `[1.903217]`). Every deterministic quantity —
turns/vtime/timeslices/syscalls/`SleepUntil(0)` and the **entire guest console
byte-stream** — is **identical across all 7 runs**; only wall time moves
(328–345 s), and only with host load. So the only run-to-run variable on this
path is wall-clock, and the "~20 % wedge rate" attributed to it is a
**load-driven timeout FLAKE**, not a scheduler livelock. The **true** hard wedge
(guest frozen < 1 s, `SleepUntil(0)` ~10⁵–10⁶, committed vtime skidding hours)
is a property of the **controller harness** (Table A / [`metrics.md`](metrics.md)),
which wedges essentially every time — not this bare-busybox path.

## Interpretation → ledger

- The skid is a **vtime skid via unproductive-poller creep**, not PMU skid —
  confirms the `scheduler-vtime-jump-unproductive-pollers` foundation bug: the
  step-2d vtime jump is starved because `SleepUntil(LogicalTime(0))` yields keep
  the run queue non-empty.
- The `SleepUntil(LogicalTime(0))` **count is the cleanest wedge discriminator**
  (green ~10², wedge ~10⁵–10⁶) — sharper than raw turn/vtime magnitudes.
- Load sensitivity (Q3/H4): the 7-trial serial batch **isolates the intrinsic
  rate from host load** and settles it — the busybox `--strict` hard-wedge rate is
  **0 %** (7/7 boot; byte-identical guest console every run), and the *only*
  run-to-run variable is wall time (328–345 s, load-driven). The demo5 hard wedge
  is therefore a **scheduling-topology** property of the controller harness
  (poller density → `SleepUntil(0)` explosion), not a whole-machine-load property
  and not a property of the shipped bare-busybox path.
- The apparent "~20 % wedge rate" folklore for the busybox path is a **fixed-CI-
  timeout FLAKE** (wall-time tail crossing the budget on a loaded host), not a
  livelock — see the v1 correction above. Any CI gate on this path should classify
  by **guest progress** (PASS marker / guest-ts advance), never by wall-clock.
