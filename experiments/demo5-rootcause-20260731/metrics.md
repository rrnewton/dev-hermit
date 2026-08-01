# demo5 Metrics — Wedge vs Green, Quantified

**Role:** metrics evidence agent (hermit-231 slot) → lead scientist **hermit-226**.
**Task:** `demo5-baseline-metrics-table`. **Feeds:** [`ledger.md`](ledger.md)
H1 (wedge mechanism), H2 (greening), **H5** (perf parity), Q3 (load-independence).
**Date:** 2026-07-31. **Host:** devbig014. **Backend:** ptrace.

## Headline

The demo5 hard **wedge** is **not** caused by `--no-rcb-time` alone, and it is
**not** caused by the 3-knob config stack alone either. A bare QEMU/busybox boot
*survives* both: `--no-rcb-time` alone boots (racing vtime ~4.5× further), and
even the **full 3-knob stack** (`--no-rcb-time` + `--target-timeslice 100000` +
`--max-timeslice disabled`) boots a bare busybox guest — it just *crawls*
(~493 k turns, ~12× a green boot). The **permanent** wedge appears **only** when
that 3-knob stack drives the full-Linux **controller** guest, where the scheduler
burns **millions of unproductive micro-timeslices** and commits *hours* of
virtual time while the guest makes **zero** progress (frozen at HPET calibration
≈0.7 s). ⇒ config stack **and** controller topology are **both required**;
neither alone suffices.

The single sharpest discriminator is the **timeslice count**: a green boot uses
~1,000 long *productive* vCPU slices; a wedge fragments into **hundreds of
thousands to millions** of `SleepUntil(LogicalTime(0))` micro-yields.

---

## Definitions — two harnesses (do NOT conflate; per ledger)

| Harness | Path | Guest | Hermit flags |
|---|---|---|---|
| **WEDGE** (owner's subject) | parent `demos/05-qemu-boot.py` + `demos/lib/qemu_controller.py` | full Linux + QMP + `savevm` snapshot | `run --strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled` |
| **GREEN/crawl** | hermit `demos/05-qemu-busybox.sh` → `boot_qemu.sh` | bare busybox, `-icount shift=0,sleep=off`, `-serial stdio` | `run --strict` (rcb-time **ON**) |

Metric sources (hermit "run report" in the info log): `turns` = "ran N turns";
`elapsed_vt` = "Elapsed virtual global (cpu) time"; `timeslices` =
"Timeslice stats … count=N"; `syscalls` = count of DETLOG `finish syscall`
lines; `wall` = harness wall delta and/or first→last info-log timestamp.

---

## Table A — WEDGE harness (full-Linux controller, 3-knob), across the culprit range

All rows HANG; **no commit boots green under the wedge harness** (the commit
bisect is invalid — the good→bad axis is the *config*, not the commit). Guest
frozen at HPET (`hpet0 [0.72…]`) or earlier the entire run.

| Hermit SHA | window | wall | detcore turns | elapsed vtime | timeslices | syscalls | outcome |
|---|---|---:|---:|---:|---:|---:|---|
| [`f6c836b1`](https://github.com/rrnewton/hermit/commit/f6c836b18dac) | start (tag `demo-20260729`, "last-green") | 360 s (HANG, rc124) | **7,841,279** | **25,013.8 s** | **5,536,337** | 5,536,278 | HANG — violent spin, guest never progresses |
| [`61d8df39`](https://github.com/rrnewton/hermit/commit/61d8df393b88) | mid (near #1190) | 188 s (SIGKILL) | 262,026 *(killed, no report)* | ≈867 s | *(no report)* | 223,210 | HANG at `hpet0 [0.724]` |
| [`ae2565be`](https://github.com/rrnewton/hermit/commit/ae2565be5697) | end | 562 s | 978,552 | 3,684.5 s | 817,823 | 817,795 | HANG at `hpet0 [0.724]` |

**Signature (all rows):** elapsed **virtual** time races far past wall-clock
while the guest is frozen. `f6c836b1` committed **25,013 virtual seconds (~7 h)**
in 360 real seconds with the guest stuck below 1 s. Turn/vtime magnitudes scale
with how long each run spun before its kill; the invariant is
*vtime ≫ wall, guest progress ≈ 0*. (Epoch base 1 767 225 600 s.)

---

## Table B — GREEN/crawl harness (bare busybox), controlled single-variable

**Same binary** `670209ba` (current main + kernel-fetch fix; hermit-220 anchor),
**same busybox guest** (pinned bzImage sha256 `e4b1c024…`), flipping only the
scheduler knobs. This is the clean single-variable measurement.

| config (knobs) | wall | detcore turns | elapsed vtime | timeslices | syscalls | outcome |
|---|---:|---:|---:|---:|---:|---|
| `--strict` (rcb ON) — hermit-220 anchor | 345 s | 39,087 | 220.9 s | 1,012 | 252,163 | **BOOT_OK** (PASS) |
| `--strict` (rcb ON) — my reproduce | 329 s | 41,411 | 194.5 s | 874 | 257,382 | **BOOT_OK** (PASS) |
| `--strict --no-rcb-time` (rcb OFF, default max-timeslice) | 281 s | 45,751 | 984.5 s | 4,607 | 257,456 | **BOOT_OK** (PASS) |
| `--strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled` (full 3-knob wedgekit) | 382 s | 493,613 | 1,450.1 s | ~85,640 | 386,841 | **BOOT_OK** (PASS, slow crawl) |

**Finding:** flipping *only* `--no-rcb-time` (row 3) does **not** wedge a bare
boot. It still crosses the HPET deadline and boots, but virtual time races
**~4.5× further** (984.5 s vs ~207 s) and timeslices fragment **~5×**
(4,607 vs ~940) — because with a default `--max-timeslice` the timeslice
preemption deadline remains as the forward-progress event even when
branch-retirement vtime is off. Syscall totals are essentially identical across
configs (252–257 k — same guest, same work).

**Decisive result (row 4, wedgekit):** even the **full 3-knob wedge stack**
applied to a bare busybox guest does **not** permanently wedge. It **boots to
completion** (`HERMIT-QEMU-BUSYBOX-PASS`, guest reaches `[1.903278] reboot:
Power down`) — but only by *crawling*: **~493 k turns (~12× green), ~1,450 s
committed vtime (~7× green), ~85 k timeslices (~85× green), 387 k syscalls**.
So the 3-knob config makes forward progress **pathologically slow** yet still
progresses in bare busybox. The **permanent** hard wedge (Table A: millions of
turns, guest frozen below 1 s, never crosses HPET) appears **only** when that
same config drives the full-Linux **controller** guest. ⇒ the config stack is
*necessary but not sufficient*; the permanent livelock **requires the wedge
harness's controller topology** (QEMU BQL/iothread + the python controller's
deadline-less QMP/futex handshake) on top of the config.

> Note on run-to-run variation (rows 1–2): 39,087 vs 41,411 turns reflects a
> **harness-wrapper** difference (`05-qemu-busybox.sh` tee/build vs direct
> `boot_qemu.sh` → different `execve`/argv), **not** a determinism claim.
> Byte-identical DETLOG determinism is hermit-237/210's remit.

---

## Reference — native QEMU busybox boot (no hermit), strace (hermit-238)

Total syscalls **910,679** in a 30 s native boot window
(`futex` 525,703 / `ppoll` 112,742 dominate — see [`syscall-counts.csv`](syscall-counts.csv)).
Native reaches an interactive shell; provides the syscall-volume baseline.

---

## Derived regression

- **Cross-harness magnitude** (wedge `f6c836b1` vs green busybox `670209ba`):
  turns **~200×**, elapsed vtime **~113×**, timeslices **~5,500×** — all
  producing **zero** boot vs a full boot. (Different guests; magnitude-
  indicative, not single-variable.)
- **Single-variable** (busybox, only `--no-rcb-time` flipped): still boots;
  vtime ~4.5× further, timeslices ~5× more. ⇒ `--no-rcb-time` alone is **not**
  sufficient to wedge.
- **Single-variable, full stack** (busybox, all 3 knobs): still boots; turns
  ~12×, vtime ~7×, timeslices ~85× a green boot. ⇒ the 3-knob config stack alone
  is **not** sufficient to *permanently* wedge — it only makes progress ~12×
  slower. The permanent wedge needs the controller guest on top.
- **Timeslice count is the cleanest wedge signature**: ~1,000 (green) → ~85 k
  (busybox + full config, still boots) → 10⁵–10⁶ (controller + full config,
  permanent wedge).

## Interpretation → ledger hypotheses

- **H1 / H6 (now decided by the wedgekit row).** The **permanent** wedge
  requires **both** the stacked config **and** the controller's unproductive-
  poller topology — *neither alone is sufficient*. The controlled busybox pair
  proves it in two steps: (a) `--no-rcb-time` alone → boots (~5× cost); (b) the
  full 3-knob stack in bare busybox → **still boots**, just crawls ~12× slower
  (493 k turns) and never permanently freezes. Only when that 3-knob stack drives
  the full-Linux controller does the guest freeze below 1 s forever (Table A).
  This **kills** the naive "`--no-rcb-time` ⇒ wedge" reading **and** the "config
  stack alone ⇒ wedge" reading, and **supports H6**: the controller topology is
  the required final ingredient. The config's role is to remove every bounded
  forward-progress event (`--max-timeslice disabled` drops the preemption
  deadline; `--no-rcb-time` drops branch-retirement vtime), which turns the
  controller's deadline-less poller handshake from slow into *permanent* — the
  vCPU cond-var starvation the ledger's forensics identified.
- **H2.** rcb-time ON keeps committed_time advancing from branch retirement in
  bounded productive slices (~1,000 slices, ~200 s vtime) → deadline crossed →
  boot.
- **H5 / Q4 (perf parity).** Even green rcb-ON burns ~195–221 s vtime / ~39–41 k
  turns — the "crawl," ~5× the pre-regression sub-minute. rcb-time is a
  **workaround, not a perf restore**; the genuine fix must boot sub-minute
  *and* avoid the wedge.
- **Q3 (load-independence, SACRED).** Decision-level counts (turns/vtime/
  syscalls) are stable in magnitude across runs/configs of the same guest;
  observed variation tracks harness-wrapper/argv, consistent with wall-only load
  sensitivity. Byte-identical DETLOG proof remains 237/210's deliverable.

## Reproduction

```bash
# Wedge harness (per SHA): experiments/demo5_bisect_20260731/ignored/metrics_boot.sh
#   metrics_boot.sh <hermit-bin> <label> norcb 300
# Controlled busybox pair (single-variable):
#   busybox_pair.sh <hermit-bin> {strict|norcb|wedgekit} <label> 360
```
Raw run dirs (gitignored): `experiments/demo5_bisect_20260731/ignored/run-*`,
`…/run-bbx-*`. See [`metadata.json`](metadata.json) for SHAs, assets, sources.
