# btrfs f6a6c280 repro — the HB blocker is cleared, and what that actually buys

**Task:** `goal-repro-btrfs-f6a6c280` (P1) · **Date:** 2026-08-06 · **Author:** hermit-design
**Status:** blocker re-assessed and **cleared**; no new sweep run (deliberately — §4). Local only, no egress.
**Bound to:** hermit `f89c69766` (primary `main`) · release binary `target/release/hermit`

---

## 0. The one-line result

The task is tagged **blocked-on-hb-edges**. **That blocker is cleared.** Happens-before edges have
landed — spec, resolver, CLI, *and scheduler enforcement* — and I verified it two independent ways.
The CLI help text still says otherwise, which is exactly what would have kept the next agent believing
the task was blocked.

What the cleared blocker does **not** buy is a drop-in repro: the race's two endpoints live in the
**guest kernel**, and HB anchors resolve against the **hermit guest's** debug info — which, under
QEMU-under-Hermit, is QEMU. §3 is the consequence and the way through it.

---

## 1. Establishing what we have (not re-deriving it)

**Artifacts: all still staged**, 8 days after the last session, in the gitignored tree:

| Artifact | State |
| --- | --- |
| `ignored/btrfs-kernel-build/artifacts/bzImage-btrfs-buggy-9786531` | present, 14,656,512 B |
| `ignored/btrfs-kernel-build/artifacts/bzImage-btrfs-fix-f6a6c280` | present, 14,656,512 B |
| `kernel.config`, `hermit-btrfs.fragment`, `MANIFEST.md` | present |
| `ignored/btrfs-f6a6c280-repro_20260729/` (harness, reproducer, sweeps) | present |
| `experiments/btrfs_f6a6c280_subvol_race_repro_20260729/` (durable negative result) | present |

Nothing needs fetching. The "record what's needed and stop" branch of the directive does not apply.

**Prior result, which stands:** ~4448 amplified deterministic QEMU-under-Hermit chaos attempts across
four sweeps, **zero repros**; the fixed kernel passes identically; native full-speed also misses the
window. The recorded verdict was that seed-sweeping permutes guest thread scheduling but cannot pin a
happens-before edge across the few-instruction window (CPU-A `remove_inode_hash`→`__xa_erase` vs
CPU-B `btrfs_add_inode_to_root`).

---

## 2. The blocker is cleared — verified twice, because the docs disagree

### Evidence A — source

* `detcore/src/scheduler.rs:283` — *"Runtime state for **enforcing** a `HappensBeforeProgram` inside
  the scheduler"*; `:297` holds the `program`; `:322` constructs it; `:599` — *"Holds AFTER anchors
  until their BEFORE anchors fire."*
* `detcore/src/lib.rs:1512-1521` — the checkpoint is a real scheduler resource,
  `ResourceID::HappensBeforeCheckpoint(new_count)`.
* `detcore-model/src/happens_before.rs` — full model: `HappensBeforeSpec`, `EventSpec`, `EdgeSpec`,
  `Strength::{Hard, Soft}`, `Anchor`, `HappensBeforeProgram`. `Hard` is documented as *"Park the sink
  thread in a true gate until the source fires."*

### Evidence B — the running binary

Prebuilt binaries could not run earlier in this session because the system has no libunwind; `/tmp/lu`
holds the extracted RPMs, so:

```
$ LD_LIBRARY_PATH=/tmp/lu/usr/lib64 ./target/release/hermit run --help | grep -i hb
      --happens-before <filepath>
      --hb-list-events
```

Both flags are live in the shipped release binary.

### The stale doc that would have blocked the next reader

`hermit-cli/src/bin/hermit/run.rs:144` still reads:

> *"**Scheduler enforcement is not yet wired**; combine with `--hb-list-events` to preview how the spec
> resolves against the binary."*

That sentence was true when the negative result was written and is false now. Anyone auditing this task
by reading the CLI — the natural move — would conclude HB is still preview-only and that the task
remains blocked. **Fixing that comment is the cheapest useful change in this whole area** and it is a
hermit one-liner.

---

## 3. What the cleared blocker actually buys — and the constraint nobody has written down

The strategic note says: *"the agent KNOWS the race it wants, so it should place the correct
happens-before EDGES between the key events (the delete vs the xarray access) and the race falls out
deterministically."* That is right in principle. It is not directly executable here, for a reason that
is structural rather than incidental.

**Under QEMU-under-Hermit, the hermit guest is QEMU.** Therefore:

| HB addressing mode (`EventSpec`) | Works for this race? |
| --- | --- |
| `func` / `file` / `line` — *"resolves to a RIP via debug info"* | **No.** Debug info is **QEMU's**. `func: "remove_inode_hash"` does not exist in QEMU's binary; the symbol lives in the guest kernel, which hermit cannot see. |
| `syscall` name + `phase` + `nth` | **No.** These are QEMU's host syscalls, not the guest's `ioctl(BTRFS_IOC_SNAP_DESTROY)`. |
| `thread` (DetTid or named) | **Partially.** A hermit thread is a **QEMU vCPU thread**, not a guest-kernel task. You can name "the thread running vCPU 0", not "the btrfs cleaner kthread". |
| **`rcbs`** — *"after the thread has retired this many conditional branches (absolute value)"* | **Yes — this is the one that works.** Backend-agnostic, no debug info, and under `-icount` guest execution is deterministic, so a given guest-kernel instant maps to a fixed RCB value on a fixed vCPU thread, reproducibly. |

So the HB-directed repro is expressible, but **only through RCB-addressed anchors on vCPU threads**,
and it needs a calibration step that the "just place the edges" framing hides:

1. **Locate** the two guest-kernel instants — CPU-A entering the `remove_inode_hash`→`__xa_erase`
   window, CPU-B entering `btrfs_add_inode_to_root` — using a guest-side mechanism (QEMU gdbstub
   breakpoints, a kernel tracepoint, or a `ktime`/printk marker compiled into the differential
   kernels).
2. **Correlate** each to a `(vCPU thread, RCB)` coordinate on a deterministic `-icount` run under
   hermit. This is the step that does not exist yet and is the real remaining work.
3. **Place the edge**: `before` = CPU-B's anchor, `after` = CPU-A's anchor (or the inverse, depending
   on which order exposes the lost inode), `strength: Hard` so the sink parks in a true gate.
4. **Assert** with the existing oracle (`BTRFS_F6A6C280_ORACLE_{ARMED,PASS,FAIL}` +
   `DETECT_HUNG_TASK`/`SOFTLOCKUP`), and confirm the **fixed** kernel does not hang under the *same*
   edge — the differential is what makes it a repro rather than a hang.

**Precondition to check before step 3:** `detcore/src/lib.rs:1512` notes the HB checkpoint *"requires
sequentialized"* threads. The prior sweeps ran with hermit's default sequentialization, so this is
probably satisfied, but it should be confirmed rather than assumed for an `-smp 2` QEMU guest — a
sequentialization requirement interacts directly with whether two vCPUs can be held at chosen points
simultaneously.

---

## 4. Why I did not run another sweep

The directive offers a detached-sweep recipe, and I deliberately did not use it:

* **The seed lottery is a known negative** at ~4448 attempts across four sweeps with progressively
  amplified parameters (finer timeslices, 32 racers, 32 evict cycles, `ONEDIR=1` dilution reduction,
  per-thread slowdowns, `--fuzz-futexes`, `--chaos-target-races`). Re-running it would spend hours to
  reconfirm a result already recorded with its methodology.
* The recorded diagnosis is not "we got unlucky", it is **structural**: chaos permutes thread
  scheduling; it does not place an edge inside a few-instruction inter-CPU window. More seeds do not
  address that.
* A QEMU-under-Hermit sweep is exactly the heavy concurrent load the standing directives in this
  session say to avoid.

The honest move is to attack the calibration step in §3, not to buy more lottery tickets.

---

## 5. Recommended next increments

1. **Fix the stale CLI comment** (`run.rs:144`) — a one-line hermit change that stops this task being
   re-blocked by its own documentation. Cheapest item here by a wide margin.
2. **Build the RCB-calibration probe** (§3 steps 1-2). This is the substantive work and it is what
   "adopt HB as it lands" actually means for a *guest-kernel* race: a way to turn a kernel instant into
   a `(vCPU thread, RCB)` coordinate. Without it, `--happens-before` is unusable for anything inside a
   VM, which is a general limitation worth recording beyond this bug.
3. **Then** place the edge and run the differential — buggy must hang, fixed must not, under the same
   spec.
4. Consider whether the anchor model should grow a **guest-kernel-symbol** addressing mode for the
   QEMU-under-Hermit configuration. That is an RFC-#1146-scale question, not a task-level one, and it
   is the difference between HB being usable for `goal-qemu-linux-under-hermit` or only for
   ordinary userspace guests.

---

## 6. Not established

* **No repro.** Nothing was reproduced this session; the standing result remains the recorded negative.
* **No sweep, no QEMU boot, no kernel run.** The only thing executed was `hermit run --help` under
  `LD_LIBRARY_PATH=/tmp/lu/usr/lib64`.
* **The RCB-addressing claim in §3 is reasoned, not demonstrated.** `rcbs` is documented as an absolute
  per-thread retired-conditional-branch count and is backend-agnostic; I did not verify that an RCB
  coordinate taken on one `-icount` QEMU run reproduces on the next. **That reproducibility is the
  premise the whole §3 plan rests on and it should be measured first** — if RCB counts on a vCPU thread
  are not stable across runs of the same seed, the plan fails at step 2.
* **The "requires sequentialized" precondition (§3) is quoted from a source comment**, not tested
  against an `-smp 2` guest.
* I did not re-verify the four prior sweeps; §1's summary is from the recorded task notes and the
  durable experiment directory, not re-measured.
