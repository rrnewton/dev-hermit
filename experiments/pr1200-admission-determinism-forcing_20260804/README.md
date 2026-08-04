# PR #1200 — forcing the admission-order determinism defect (F1) and reconnect panic (F3)

**Question.** Codex adversarial review of PR #1200 raised two blocking findings
that a quiet-box byte-identity pass cannot settle, because both are latent races
that only manifest when host timing lands a certain way:

- **F1** — run-queue *admission order* on async backends (DBI/KVM) is
  **host-timing-dependent**: whether a racing child-create handler sees the
  daemon's `tentative_pop` window open or closed is decided by host mutex
  timing, and the two branches produce *different* run-queue orders for the same
  logical event. That is a determinism defect in the flagship async path, not a
  robustness nit — identical seeds must produce identical schedules.
- **F3** — an exec **reconnect** that removes a run-queue tid while the daemon
  holds an open `tentative_pop` window trips `remove_tid`'s
  `tentative_selection.is_none()` guard, poisoning the scheduler mutex → hang.

Per the coordinator directive: *force* each defect (vary the timing, show the
behavior changes) on the **unfixed** code, then show it is **stable** on the fix
under the same variation. This is the e9patch #359 template applied to the
scheduler.

## Method

Everything is a scoped `cargo test -p detcore --lib` run in the `kvm` slot
worktree (`worktrees/kvm/hermit`). No wide sweep, no validate capacity consumed.

- **Fix under test:** branch `codex/fix-1200-admission-epoch` @ `1b6c9ef3`
  (always-defer: `admit_to_run_queue` and `deschedule_or_defer` unconditionally
  buffer intent; the daemon resolves+applies the whole buffer at one DetTid-
  ordered step2 drain before step3 opens a tentative window).
- **Buggy code under test (F1):** ancestor `c7667d45`
  (*conditional*-defer — the exact code Codex FAILed: immediate path when
  `!tentative_pop_in_progress()`, deferred path otherwise).
- **Buggy behavior under test (F3):** the fix disabled in place — comment out the
  `undo_tentative_pop()` call in `do_a_turn_blocking`'s `Err(ThreadExited)`
  fizzle arm (`detcore/src/scheduler.rs`).

### F1 — buggy-side bracket (reproduce)

```
cd worktrees/kvm/hermit
git checkout c7667d45
# apply f1_buggy_bracket.rs.snippet into detcore/src/scheduler.rs `mod test`
cargo test -p detcore --lib -- f1_buggy_bracket_admission_order_is_host_timing_dependent --nocapture
git checkout -- . && git checkout codex/fix-1200-admission-epoch
```

The snippet asserts the *same* arrival-/window-independence invariant that the
always-defer fix guarantees. It is the byte-for-byte analogue of the fixed-side
test `deferred_admission_side_is_arrival_order_independent` (already in the PR).

### F1 — fixed-side (already in the PR at 1b6c9ef3)

```
cargo test -p detcore --lib -- deferred_admission_side_is_arrival_order_independent
```

### F3 — real-path bracket (reproduce)

```
cd worktrees/kvm/hermit   # on codex/fix-1200-admission-epoch @ 1b6c9ef3
# comment out the undo_tentative_pop() in the Err(ThreadExited) fizzle arm
cargo test -p detcore --lib -- reconnect_fizzle_closes_window_so_next_removal_drain_is_safe --nocapture
git checkout -- .          # restore the fix
# guard-fires proof (no edit needed):
cargo test -p detcore --lib -- removal_drain_panics_if_tentative_window_left_open
```

## Results (devbig014, rustc 1.99.0-nightly, 2026-08-04)

### F1 — admission order IS host-timing-dependent on the buggy code

At `c7667d45` the forcing test's diagnostics print, then the invariant fails:

```
closed window, arrival [lower,higher] -> [DetPid(21), DetPid(23)]
closed window, arrival [higher,lower] -> [DetPid(23), DetPid(21)]   <-- order flipped by arrival timing
open   window, arrival [lower,higher] -> [DetPid(21), DetPid(23)]
assertion `left == right` failed: arrival order (window closed)
  left: [DetPid(21), DetPid(23)]
 right: [DetPid(23), DetPid(21)]
test ... FAILED
```

Two racing admissions with the window **closed** (a condition decided by host
mutex timing on async backends) land in the run queue in their **host arrival
order** — same DetTids, different schedule. Defect reproduced.

At `1b6c9ef3` the *identical* invariant (`deferred_admission_side_is_arrival_
order_independent`) **passes**: order is arrival-order- AND window-state-
independent. Defect gone.

### F3 — reconnect panic

- Fix disabled (fizzle arm's `undo_tentative_pop()` commented out): the real
  async daemon path (`do_a_turn_blocking`) leaves `tentative_pop_in_progress()`
  **true** after a fizzled reconnect turn — `reconnect_fizzle_closes_window_so_
  next_removal_drain_is_safe` **FAILS** at its pass-1 invariant ("the
  ThreadExited arm must undo the tentative pop"). An open window at that point is
  precisely what the guard forbids.
- Guard fires on an open window: `removal_drain_panics_if_tentative_window_left_
  open` (`#[should_panic(expected = "tentative_selection.is_none()")]`) **passes**
  — i.e. left-open window → next removal drain → `remove_tid` panic.
- Fix enabled (`1b6c9ef3`): `reconnect_fizzle_closes_window_so_next_removal_
  drain_is_safe` **passes** — the fizzle arm undoes the pop, the window is
  closed, and the next real step2 removal drain does not panic.

## Interpretation

Both blocking findings are genuine and now bracketed **both sides** on the real
mechanism, not just a quiet-box pass:

- F1: buggy code exhibits host-arrival-order-dependent admission order; the
  always-defer fix makes admission order a pure function of (DetTid set,
  schedule state, seed).
- F3: the reconnect panic is reachable on the real async daemon path when the
  fizzle arm leaves the tentative window open; the F6 `undo_tentative_pop()`
  closes it, and the deferred-removal path keeps `remove_tid` off the guard.

This is a core DetCore scheduling change (PR-section trigger 4 →
`post-facto-human-review`).
