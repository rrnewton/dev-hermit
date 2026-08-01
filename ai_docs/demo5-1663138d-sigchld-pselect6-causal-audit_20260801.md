# Demo5 causal audit: `1663138d` is not the lost-wakeup regressor

Date: 2026-08-01

Task: `demo5-second-bug-1663138d-writeup`

Commit audited: [`1663138d9cd123a4a880d367593f7a57296d65e2`](https://github.com/rrnewton/hermit/commit/1663138d9cd123a4a880d367593f7a57296d65e2), "Determinize SIGCHLD admission and pselect6 sigmask for make -jN"

Coordination: code audit by hermit-242; build and load-controlled boot evidence from hermit-231's `demo5-post-breakage-bisect-second-regression` task and its helper run at `1663138d`.

## Executive verdict

The proposed causal chain is **refuted**.

`1663138d` does not change which side of an ordinary clone runs first, and it
does not change a child or parent's persistent priority. It changes two other
things:

1. a parent's already-delivered, host-asynchronous `SIGCHLD` turn is deferred
   while another ordinary-priority thread is runnable, then re-admitted at
   scheduler quiescence; and
2. `pselect6` calls with a real temporary signal mask move from host-blocking
   execution to deterministic zero-timeout internal probes that carry the mask.

There is one priority effect: after the deferred-signal gate opens, the **parent
receiving SIGCHLD** is inserted with the pre-existing
`push_eager_io_repoll` helper at priority 0. That happens after a child has
exited and only after ordinary work drains. It is not a clone-time
child-versus-parent priority change.

The causal test also fails empirically:

- `1663138d` itself booted demo5 **3/3** in hermit-231's load-controlled,
  band-rotated comparison.
- Current `HEAD` with `1663138d` cleanly reverted still stopped at the same
  short-budget HPET checkpoint **3/3**.

Finally, the reported lost-futex-wakeup and terminal-wedge interpretation was
itself retracted after adequate-budget validation: the canonical RCB-armed demo
booted **6/6** in 238.3-247.6 seconds with a 600-second budget. The roughly
90-second pause after the HPET line is a slow phase, not proof of a permanent
wedge. See
[`demo5-root-cause-lost-futex-wakeup_20260801.md`](demo5-root-cause-lost-futex-wakeup_20260801.md).

## What the diff actually changes

The commit is three files, 131 insertions and 12 deletions:

```text
detcore/src/scheduler.rs          | 81 ++++++++++++++++++++++++++++++++++++++-
detcore/src/scheduler/runqueue.rs | 11 ++++++
detcore/src/syscalls/io.rs        | 51 ++++++++++++++++++------
```

### 1. Scheduler: park and re-admit a SIGCHLD parent

The first hunk adds two sets to `BlockedPool`:

- `sigchld_deferred`: parents whose physical SIGCHLD arrived while ordinary
  guest work was still runnable; and
- `sigchld_ready`: parents re-admitted by the deterministic gate, preventing
  the same signal turn from being deferred again.

Source: [`scheduler.rs` lines 206-232](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler.rs#L206-L232).

`step2_process_blocked` then calls a new
`step2e_process_signal_deferred` before the empty-queue time-jump step. The gate
returns while any ordinary thread is runnable. Once the queue is empty or its
best priority is `LAST_PRIORITY` (only backoff pollers), it drains deferred
parents in sorted `DetTid` order and runs:

```rust
self.blocked.sigchld_ready.insert(dtid);
self.run_queue.push_eager_io_repoll(dtid);
```

Source: [`scheduler.rs` lines 1410-1447](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler.rs#L1410-L1447).

The `InboundSignal(SIGCHLD)` hunk is the other half. When another ordinary
thread is runnable, it undoes the tentative selection, removes the parent from
the run queue, records it in `sigchld_deferred`, and returns `SkipTurn`. A
`sigchld_ready` parent is granted on its next selection.

Source: [`scheduler.rs` lines 2126-2151](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler.rs#L2126-L2151).

This is a change to **SIGCHLD admission time and delivery order**. It is not a
change to futex wake bookkeeping.

### 2. Run queue: one read-only predicate, no ordering rewrite

The only `runqueue.rs` addition is:

```rust
pub fn has_runnable_besides(&self, exclude: DetTid) -> bool {
    self.queue
        .iter()
        .any(|(k, v)| v.tid != exclude && k.priority < LAST_PRIORITY)
}
```

Source: [`runqueue.rs` lines 236-251](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler/runqueue.rs#L236-L251).

It reads the queue; it does not assign or mutate a priority. The call used to
re-admit the SIGCHLD parent, `push_eager_io_repoll`, already existed in the
parent commit. It inserts at `EAGER_IO_REPOLL_PRIORITY == 0`, one band ahead of
`FIRST_PRIORITY == 1`:

- priority constants: [`runqueue.rs` lines 62-86](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler/runqueue.rs#L62-L86)
- eager insertion: [`runqueue.rs` lines 323-333](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler/runqueue.rs#L323-L333)

That priority boost is real, but its subject is a deferred **signal recipient**
after quiescence. It does not choose child versus parent after clone.

### 3. The clone-time policy is unchanged

At this commit, ordinary queue insertion still uses each thread's persistent
priority. Clone handoff still uses the pre-existing `runqueue_push_front`, and
`child_runs_first_post_fork` still selects `Child`, `Parent`, or deterministic
PRNG according to `RunsPostFork`:

Source: [`scheduler.rs` lines 2537-2568](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/scheduler.rs#L2537-L2568).

None of those lines appears in the `1663138d^..1663138d` diff. The owner's lead
that the commit changed child-versus-parent scheduling priority after clone is
therefore false.

### 4. `pselect6`: move a real sigmask into internal polling

Before this commit, a non-null inner `pselect6` sigmask forced the syscall onto
the host-timed external-blocking path. The new code snapshots and sanitizes the
mask, clears the original guest pointer, and passes the value into
`handle_internal_pselect6`.

Source: [`io.rs` lines 417-447](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/syscalls/io.rs#L417-L447).

Every zero-timeout probe then uses scratch memory containing that mask, so a
pending unmasked signal produces `EINTR` at a scheduler-selected probe rather
than at host arrival time.

Source: [`io.rs` lines 484-528](https://github.com/rrnewton/hermit/blob/1663138d9cd123a4a880d367593f7a57296d65e2/detcore/src/syscalls/io.rs#L484-L528).

This does create more deterministic `InternalIOPolling` turns for the affected
`pselect6` path. It still does not modify `FutexWait`, `wake_futex_waiters`,
`CLONE_CHILD_CLEARTID`, or clone priority.

## Why the proposed lost-wakeup chain does not follow

The proposed story was:

```text
1663138d changes post-clone child/parent priority
  -> wrong side runs first
  -> futex wake is lost
  -> waiter never wakes
  -> SleepUntil(0) pollers monopolize turns
  -> guest freezes at hpet0 [0.724403]
```

Each causal link lacks the required evidence:

1. **No post-clone priority diff.** The actual diff does not touch that code.
2. **No futex hunk.** The commit has no change to futex wait registration,
   clearing, or wake delivery.
3. **Wrong workload-specific path.** The SIGCHLD comments and pselect6 mask
   change target GNU make's child-exit/jobserver sequence. The observed demo5
   short-budget terminal loop was the Python/QMP controller polling for a
   socket while the QEMU vCPU made slow progress; the evidence did not identify
   a make jobserver SIGCHLD/pselect6 sequence.
4. **The causal intervention has the wrong result.** The suspected commit boots;
   reverting it from the later binary does not repair the short-budget stop.
5. **The terminal condition was misclassified.** With enough wall time, the
   HPET pause resumes and the demo boots.

There may be valid, independent lost-futex-wakeup bugs in DetCore. This evidence
does not establish one in demo5 and does not connect one to `1663138d`.

## Provenance of the quoted wedge numbers

The numbers requested for this write-up do not all describe `1663138d`:

| Observation | Actual provenance | Current interpretation |
| --- | --- | --- |
| serial stops after `[    0.724403] hpet0: 3 comparators, 64-bit 100.000000 MHz counter` | short-budget full-Linux runs | a repeatable checkpoint during a slow phase; not by itself a terminal wedge |
| `SleepUntil(LogicalTime(0)) = 102,514` | hermit-231's initial current-HEAD (`2f3689bd`) run with a 180 s budget | later-corrected, under-budget observation; not measured at `1663138d` |
| RCB preemptions = 3 | same current-HEAD 180 s run | confirms few preemptions before the cutoff; does not prove permanent starvation |
| `1663138d = 0/6` with 76k-105k immediate polls and no serial line | helper-238's serial, 175 s cgroup-boxed screen | provisional result confounded by run order/load and short budget |
| `1663138d = 3/3 GREEN`; `HEAD-revert-1663138d = 0/3` at the HPET checkpoint | hermit-231 band-rotated, concurrent A/B/C/D | decisive refutation of `1663138d` causality under the tested short-budget classifier |
| canonical RCB-armed demo = 6/6 boot, median 242.8 s | adequate 600 s validation on quiet host | retracts the permanent-wedge and demo5 lost-wakeup claims |

The full correction history is summarized in the tracked retraction linked
above. In particular, its 238.3-247.6 second runs outlast every 90-200 second
timeout used for the earlier lost-wakeup/fairness claims.

## Exact reproduction

### A. Reproduce the source audit

```bash
cd ~/work/dev-hermit
with-proxy git -C hermit fetch origin main
git -C hermit show --stat 1663138d
git -C hermit diff 1663138d^ 1663138d -- \
  detcore/src/scheduler.rs \
  detcore/src/scheduler/runqueue.rs \
  detcore/src/syscalls/io.rs
git -C hermit show 1663138d:detcore/src/scheduler.rs | nl -ba | \
  sed -n '1410,1447p;2126,2151p;2537,2568p'
```

Expected result: the three hunks described above; no clone-priority or futex
change.

### B. Build the exact Hermit commit

Use an isolated product worktree and private Cargo home; do not rebuild a
primary checkout:

```bash
cd ~/work/dev-hermit
scripts/allocate-worktree.rs --agent <agent> --task demo5-1663-repro \
  --product hermit
git -C worktrees/<slot>/hermit switch --detach \
  1663138d9cd123a4a880d367593f7a57296d65e2
cp -a --reflink=auto "$HOME/.cargo" scratch/demo5-1663-cargo
CARGO_HOME=$PWD/scratch/demo5-1663-cargo \
  cargo build --release -p hermit \
  --manifest-path worktrees/<slot>/hermit/Cargo.toml
```

Hermit-231's build of this exact commit completed successfully in 1m39s. Keep
the resulting `target/release/hermit` and record its SHA-256 before comparing
other revisions.

### C. Reproduce the short-budget HPET classification safely

The historical screen used an RCB-armed config and a cgroup-boxed full-Linux
controller run. The equivalent direct invocation is:

```bash
cd ~/work/dev-hermit
systemd-run --user --scope --wait --collect \
  -p MemoryMax=16G -p MemorySwapMax=0 -p RuntimeMaxSec=295 \
  taskset -c 298-307 env \
    HERMIT_RELEASE=$PWD/worktrees/<slot>/hermit/target/release/hermit \
    QEMU_ASSETS=$PWD/scratch/demo5-1663-assets \
    QEMU_TIMEOUT=175 \
    ./demos/05-qemu-boot.py
```

The demo invokes Hermit as:

```text
hermit run --strict --target-timeslice 100000 --max-timeslice 2000000000 -- \
  python3 demos/lib/qemu_controller.py boot ...
```

This 175-second run is useful only for reproducing the **old classifier**. A
timeout at HPET is not a valid permanent-wedge verdict because the corrected
boot budget exceeds the observed 238-248 second completion time.

### D. Run the corrected completion test

Use the same cgroup/core isolation but raise both the demo and outer runtime
budgets:

```bash
systemd-run --user --scope --wait --collect \
  -p MemoryMax=16G -p MemorySwapMax=0 -p RuntimeMaxSec=720 \
  taskset -c 298-307 env \
    HERMIT_RELEASE=$PWD/worktrees/<slot>/hermit/target/release/hermit \
    QEMU_ASSETS=$PWD/scratch/demo5-1663-assets \
    QEMU_TIMEOUT=600 \
    ./demos/05-qemu-boot.py
```

Classify completion by a post-HPET marker (`Switched to clocksource tsc`, the
interactive shell/RTC marker, or clean exit), not by an intermediate serial
length or virtual timestamp.

### E. Test commit causality, not correlation

Build four exact binaries and run them concurrently on disjoint core bands,
rotating the bands each round:

```text
A = current bad endpoint
B = A with 1663138d cleanly reverted
C = 1663138d
D = 1663138d^ (or the agreed older anchor)
```

For `1663138d` to be causal, `C` must regress relative to `D` and `B` must
recover relative to `A`, under the same adequate budget and matched load. The
observed short-budget rotated result was the opposite of that requirement:

```text
C (1663138d)             3/3 GREEN
B (HEAD minus 1663138d)  0/3, same HPET checkpoint as HEAD
```

Therefore neither `git show` nor the causal A/B supports blaming
`1663138d`.

## Relation to poller starvation and scheduler vtime-jump

The logs do show many `SleepUntil(LogicalTime(0))` turns, and the general
mechanism remains understandable: immediate poll-yields keep the run queue
non-empty, so an empty-queue-only time jump cannot fire. That is a scheduler
substrate worth measuring.

It is not evidence that `1663138d` created a lost futex wakeup, and under the
current adequate-budget result it is not evidence of a terminal demo5 livelock.
The parent retraction explicitly concludes that neither a sticky-futex overlay,
a fairness overlay, nor a vtime-jump prototype is justified by this demo5
investigation. Any future owner-gated scheduling change must be supported by a
reproducer that remains broken beyond the correct wall budget and by a causal
intervention against the actual changed code.

## Bottom line

- The key diff hunks are SIGCHLD parent admission and pselect6 sigmask polling.
- The commit does not alter clone-time child/parent priority and touches no
  futex wake path.
- The exact `102,514 / 3 / 0.724403` observation belongs to a short current-HEAD
  run, not to `1663138d`.
- `1663138d` green plus failed HEAD-revert is a direct causal refutation.
- The lost-wakeup/wedge claim was subsequently retracted by 600-second 6/6
  boots.

Do not revert `1663138d` or use it to justify an owner-gated scheduler change on
the basis of the demo5 evidence summarized here.
