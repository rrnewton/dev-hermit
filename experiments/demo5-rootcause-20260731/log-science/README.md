# demo5 log-science: good-vs-bad INFO-log diff + tooling extension

**Task:** `demo5-log-science-diff` (hermit-237, log-science evidence agent).
**Feeds:** `demo5-rigorous-rootcause` (lead hermit-226; see `../ledger.md`, H1).
**Date:** 2026-07-31. **Host:** devbig014.atn7.facebook.com (316 CPU).

This is the log-science slot of the demo5 evidence fleet. It (a) reproduces the
good-vs-bad demo5 first divergence with the built-in tooling, (b) quantifies the
wedge with a **new per-dtid starvation query**, and (c) enumerates the
log-analysis infra (owner's "remind me what's there" — see
`../../../ai_docs/hermit-log-analysis-tooling-inventory_20260731.md`).

## Inputs (reused, not re-run — demo5 boots are load-sensitive, ~300–600 s)

Full `hermit --log info` DETLOG captures, from the earlier bisect (hermit-238):

| Run | hermit SHA | outcome | size | QEMU (dtid 9) |
|---|---|---|---|---|
| GOOD | `2a7ca98f` (#1077) | exit 0, boots | 736 MB / 7.44 M lines | 112,506 syscalls, EXITED |
| BAD | `aa5258b6` (#1186) | "timed out waiting for qmp.sock" | 37 MB / 347 k lines | **34 syscalls, STARVED** |

Symlinks: `ignored/demo5-good-trace.log`, `ignored/demo5-broken-trace.log`
(parent repo). `--no-rcb-time` wedge config per `../ledger.md`.

## Method + results

### 1. Built-in `hermit log-diff` (first-divergence) — and its limits

```
hermit log-diff --no-color --strip-lines --limit 8 \
  --ignore-lines bisect-demo5 --ignore-lines execve \
  <good_head45k> <broken_head45k>          # first 45k lines of each (covers ~L21k)
```
`logdiff_head45k.txt` (raw) / `logdiff_head45k_filtered.txt` (path-noise filtered).

- **Un-filtered, the first reported divergence is NOISE**: the per-run output dir
  (`…/2a7ca98-good-r…` vs `…/aa5258b-bad-re…`) leaks into the `execve` argv, and
  `--strip-lines` does not normalize run dirs (only `/tmp`, `/proc/<pid>`). →
  **tooling gap #1** (path normalization).
- **Filtered, the real first schedule divergence is turn 645**, a *benign
  parent/child reorder* around `clone3`:
  - GOOD: `COMMIT turn 645, dettid 3 … clone3(…) = Ok(7)` (parent runs, spawns 7)
  - BAD:  `COMMIT turn 645, dettid 7 … SleepUntil(LogicalTime(0))` (child runs first)
  The zip-based comparator then derails on the downstream cascade and **never
  reaches the actual wedge** (~L21k, committed ~639 s). → **tooling gap #2**
  (no insertion-tolerant alignment; already a TODO in `logdiff.rs`). A wedge is a
  *disappearance*, which a first-line-content diff cannot express.

### 2. `scripts/log_timeslice.rs` on the BAD trace

```
hermit/scripts/log_timeslice.rs < ignored/demo5-broken-trace.log
```
`timeslice_broken.excerpt.txt`. 50,958 slices; committed vtime advances
**913,241 ms over 35.7 s wall (virt/wall ≈ 25.6×)**; `rcbs: 0` (=`--no-rcb-time`,
so vtime comes only from the per-turn scheduler tick + syscalls). The turn-taking
RLE shows the signature `… 3x301 -> 7x2889 -> 9 … 9x23 -> 7x24710 -> 3x63 …` — a
long dtid-7 busy-poll run, dtid 9 briefly, then dtid 9 gone. Correct data, but
you must eyeball a 600-char string. → **tooling gap #3**.

### 3. NEW query `dtid_activity.rs` (fills gap #3)

```
./dtid_activity.rs < ignored/demo5-broken-trace.log   # dtid_activity_broken.txt
./dtid_activity.rs < ignored/demo5-good-trace.log     # dtid_activity_good.txt
```
Per-dtid table (turns, syscalls, first/last turn, first/last committed vtime,
tail-turns, tail-vtime) + flags `STARVED-TAIL` (alive thread never rescheduled
while the clock races ahead), `EXITED` (clean exit — *not* starved), `BUSY-POLLER`.

**BAD — one wedge witness:**
```
dtid 9: last ran at turn 3594 (1767225638.857 s committed), then 59583 turns
(94.3% of run) with the committed clock advancing +874.384 s and this thread
NEVER scheduled again.
```
dtid 9 = `/bin/qemu-system-x86_64` (cloned by dtid 7 = python `qemu_controller.py`).
Its last act is `read(3, .., 832)` = syscall #34 while loading `libpixman-1.so`
in ld.so — the read **completes** (QEMU is runnable), but it is never scheduled
again. dtids 11–47 are the controller-side process tree (QEMU never got far
enough to spawn its own BQL/vCPU threads).

**GOOD — no starvation:** `none`. dtid 9 ran **112,506 syscalls** to turn
1,355,692 of 1,373,030 and `EXITED`. dtid 13's early exit is correctly labeled
`EXITED`, not starved (this discriminator was added after it first false-flagged).

## Interpretation (contributes to ledger H1)

The bad run is the **deadline-less unproductive-poller wedge**: QEMU (dtid 9)
completes a syscall and is runnable, but under `--no-rcb-time` committed vtime is
driven by the busy-poller (dtid 7, 27,597 syscalls) and races **+874 s past
QEMU's last observed committed time** while QEMU is never re-selected. This is the
log-science evidence H1 predicted (SleepUntil(0) commits dominate, committed vtime
races ahead, guest starves). It does **not** distinguish "latent bug vs step-back"
(Q2) or load-independence (Q3) — those are 210/231's slots. It confirms the
*mechanism shape*: a starved-but-runnable guest thread, not a crashed one.

## Tooling gaps → recommended extensions (for an impl agent, in a slot)

1. `strip_log_entry`: add run-dir path normalization (or a `--strip-run-paths`).
2. `logdiff.rs`: insertion-tolerant alignment (its own TODO); and a
   "thread disappeared after turn N / committed T" summary when one run stops
   scheduling a dtid the other keeps running.
3. Land `dtid_activity.rs` as `hermit/scripts/log_dtid_activity.rs` (rust-script,
   no build; validated here on 736 MB good + 37 MB bad traces).

Per this task's protocol (no commit/submit without explicit instruction, no
feature dev in the primary checkout) these are **not** committed/PR'd here; the
validated script + patch-ready source live in this dir for the coordinator/impl
agent to land.

## Reproduction

All commands above are exact. Head samples were `head -45000` of the source
traces (regenerable; dropped to keep the artifact lean). `dtid_activity.rs` is
self-contained rust-script (`rust-script 0.36.0`). See `metadata.json`.
