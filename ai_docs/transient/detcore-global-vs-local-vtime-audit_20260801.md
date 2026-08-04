# Detcore global (committed) vs local (per-thread) virtual-time audit — re-verified at current main

- **Task:** `vtime-global-vs-local-audit-overnight` (owner #161). Read-only audit;
  **no code changes** (virtual time is SACRED).
- **Ground SHA:** hermit `origin/main` = `09207d80258323c27ca345a0f41b5c555db96caa`
  (2026-08-01). Time-model source at this SHA is byte-identical to the primary
  checkout tip used for reading.
- **Supersedes / re-verifies:** the 2026-07-31 audit
  (`ai_docs/detcore-global-vs-local-vtime-audit_20260731.md`, ground `0ca0dec2`).
  All structural findings below were re-confirmed against current line numbers;
  the three time-touching commits landed since `0ca0dec2` were newly audited.
- **GitHub blob prefix (all links pinned to ground SHA):**
  `https://github.com/rrnewton/hermit/blob/09207d80258323c27ca345a0f41b5c555db96caa/`

---

## 0. Owner's question and the one-line answer

**Owner invariant (#161):** *any local-thread ↔ scheduler interaction should BUMP
the thread's local time forward to global; a large global-racing-ahead-of-local
discrepancy is FISHY (a broken invariant).*

**Verdict (re-confirmed at `09207d80`): the bump exists, but it is NOT the bump
the invariant describes.** The scheduler → thread bump raises a thread's local
clock to *its own committed slice* (`threads_time(dtid)`), never to the global
total (`as_nanos()`/`sum_up()`). The quantity that races ahead —
`global_total − threads_time(dtid) = Σ(other threads' time) + extra_time` — is
**never** folded into any thread's local clock. So a large global-over-local gap
is **expected and structural, not a bug**: it is exactly `extra_time` plus other
threads' retired work. It has existed since the initial commit (2022) and is
independent of the recent time-policy churn (#1095/#1190).

The gap is *guest-observable* only through the clock-read path, and there #1190
pins it monotonic (max-floor), so it can never move backwards — the property that
actually matters for determinism.

---

## 1. The two clocks (re-verified line refs)

| | LOCAL (per-thread) | GLOBAL (committed) |
|---|---|---|
| Type | `DetTime` | `GlobalTime` |
| Home | `ThreadState.thread_logical_time` — `detcore/src/tool_local.rs:1278` | `GlobalState.global_time` (a `Mutex<GlobalTime>`) |
| Value | this thread's own retired work only | `starting_nanos + Σ(all threads) + extra_time` |
| Mirror | — | `Scheduler.committed_time`, refreshed each turn |

- **Local total:** `DetTime::as_nanos` — `detcore-model/src/time.rs:604`
  (`starting_micros·1000 + extra_nanos + syscall/rcb/nondet terms`).
- **Global total:** `GlobalTime::as_nanos` — `detcore-model/src/time.rs:861`;
  the "expensive way" `sum_up` — `detcore-model/src/time.rs:782`:

  ```rust
  fn sum_up(&self) -> LogicalTime {
      let mut sum = self.starting_nanos;
      for tm in self.time_vector.values() { sum = sum + *tm; }
      sum + self.extra_time            // <-- global-only term, in no thread's slice
  }
  ```
- **Own slice:** `GlobalTime::threads_time` — `detcore-model/src/time.rs:813`:

  ```rust
  pub fn threads_time(&self, dtid: DetTid) -> LogicalTime {
      self.starting_nanos + self.threads_duration(dtid)   // own work only
  }
  ```

## 2. The bump exists — and targets the OWN slice, not the total

Scheduler → thread payload, `detcore/src/tool_global.rs:1034-1044`
(re-verified verbatim):

```rust
let time_from_sched = self.global_time.lock().unwrap().threads_time(dtid);  // OWN slice
let time_update = match time_from_sched.cmp(&time_from_guest) {
    Ordering::Equal   => None,
    Ordering::Less    => panic!("thread time should never go down ..."),
    Ordering::Greater => Some(time_from_sched),                             // bump target
};
```

- The bump value is `threads_time(dtid)` (line 1034), **never** `as_nanos()`.
- It fires only when the scheduler's record of the thread's own slice already
  exceeds the guest-reported local time (`Ordering::Greater`), and it is `None`
  for a normal RPC — so the common path performs no bump at all.
- Round-trip plumbing: `send_and_update_time` — `tool_global.rs:2066`; local→global
  ingest `update_global_time(dtid, time_from_guest)` — `tool_global.rs:692` →
  `detcore-model/src/time.rs:741`; global→local fold `advance_to` —
  `detcore-model/src/time.rs:592`:

  ```rust
  pub fn advance_to(&mut self, deadline: LogicalTime) {
      let current = self.as_nanos();
      assert!(deadline >= current);
      self.extra_nanos += (deadline - current).as_nanos();  // folds gap into OWN extra_nanos
  }
  ```

  `advance_to` only carries a thread up to *its own* deadline (e.g. a
  `SleepUntil` catch-up); it never imports other threads' time or `extra_time`.

**Invariant conclusion:** the owner's expected "bump local→global(total)" does not
exist and, by design, cannot — importing the global total into every thread would
double-count `Σ(other threads)` and destroy the vector-clock model. The bump that
*does* exist keeps each thread's local clock consistent with its own committed
slice.

## 3. Why global races ahead of local — the `extra_time` term (root, re-confirmed)

`extra_time` is a **global-only** accumulator that lives in no thread's slice:

- `add_scheduler_time` — `detcore-model/src/time.rs:793`:

  ```rust
  pub fn add_scheduler_time(&mut self) -> LogicalTime {
      let delta = Duration::from_nanos((NANOS_PER_SCHED * self.multiplier) as u64);
      self.add_extra_time(delta)   // -> self.extra_time += delta   (time.rs:804)
  }
  ```
- `NANOS_PER_SCHED = 500_000.0` — `detcore-model/src/time.rs:98` — i.e. **0.5 ms of
  global time charged per scheduler turn**, added to `extra_time` only.
- A thread that does almost no work per turn (fast poll ≈ 250 ns of own time) still
  advances the global clock by ~500_000 ns/turn. The steady-state divergence ratio
  is ≈ `500_000 / 250 ≈ 2000×` — this is the "~1000× race" seen empirically.
- Amplifier: unproductive polling. `SleepUntil(0)` / `sched_yield` keep the run
  queue non-empty, so the step2d vtime-jump (which only fires on an *empty* run
  queue) never triggers to close the gap. See memory
  `scheduler-vtime-jump-unproductive-pollers` and the demo5/QEMU `-icount` wedge
  (`demo5-wedge-clock-skew-past-deadline-poller`).

This is **foundational** (present since the 2022 initial commit), **not** a
regression from any recent change.

## 4. Guest-visible behavior — where the gap is pinned monotonic (#1190)

The gap is only *observable* to a guest via a clock read. Default config reads the
**global** clock; both routes pass through a monotonic max-floor:

- `guest_clock_time` — `detcore/src/syscalls/time.rs:96-107`:

  ```rust
  let raw = if guest.config().use_thread_local_clock_reads {
      guest.thread_state().thread_logical_time.as_nanos()   // LOCAL (SaBRe, PR-845)
  } else {
      thread_observe_time(guest).await                      // GLOBAL as_nanos()  [default]
  };
  guest.thread_state().observe_guest_clock(raw)             // monotonic max-floor
  ```
- `use_thread_local_clock_reads` default = **false**:
  `detcore-model/src/config.rs:67` (`#[clap(skip)] bool`); `Config::default()` =
  `Config::parse_from([])` (`config.rs:1096`) leaves it `false`; it is **never set
  true anywhere in-tree**. So by default guests see the *global* (racing-ahead)
  clock.
- Monotone floor: `GuestClock::observe` — `detcore/src/tool_local.rs:1187`:
  `self.now = self.now.max(raw)`; the process-tree shares one `Arc` clock (#1190),
  so a read can never regress across threads/execs. This is the property #1095
  broke (per-exec origin subtraction) and #1190 restored — see §5.

## 5. Recent time-policy changes reviewed (owner-named + new since prior audit)

| change | SHA(s) | vtime impact | verdict |
|---|---|---|---|
| #1095 clock "freeze"/domain split | `c4a4bba2` (07-28) | guest reads subtracted a **per-exec origin** → first-sample-parity "fake determinism" | **was a real regression** (clock-domain, not the structural gap) |
| #1190 fix-forward | `cc3730fd` + `3ac51e11` (07-31) | removes origin subtraction; routes all reads through shared-`Arc` `GuestClock::observe` max-floor | **fix; live and correct at ground SHA** — does NOT touch `add_scheduler_time`/`extra_time`/`threads_time`/`as_nanos` |
| step2d vtime-jump | (foundational) | jumps `committed_time` only when run queue is empty; never fires under a `SleepUntil(0)` storm | unchanged; the gap-closer that polling defeats |
| `--no-rcb-time` / `--rcb-time` | (config) | selects whether retired-branch counts feed local time; orthogonal to the global `extra_time` term | not implicated in the gap |
| **child-exit SIGCHLD via timed_waiters** | `9c233ed0` (**new**) | schedules a one-shot SIGCHLD at `deadline = committed_time + 1ns` (`scheduler.rs:2558`) — a deadline in the **global/committed** domain | **no accounting change**; reinforces that scheduler deadlines live in the committed domain |
| **happens-before edges (M2)** | `33939744` (**new**) | uses **spawn ordinals + syscall-count anchors**, not virtual time; scheduler additions contain zero `committed_time`/`LogicalTime`/`threads_time`/`as_nanos` references | **orthogonal to vtime** |
| config-clone hot-path refactor | `84f3155d` (**new**) | touches `lib.rs` only; no `add_syscall_with_cost`/`thread_logical_time`/`advance_to`/`extra_time` edits | **no time-accounting change** |

The three commits landed since the prior audit's ground (`0ca0dec2`) leave the
global-vs-local structure exactly as documented; none folds the gap into a local
clock, and none introduces a backwards-movable guest read.

## 6. Discrepancies flagged (all structural / expected; none is a broken invariant)

1. **Global total over-estimates every thread's local clock by
   `Σ(other threads) + extra_time`** and this gap is never reconciled into a local
   clock. Expected under the vector-clock model; only guest-visible via the pinned
   monotonic read.
2. **`extra_time` charges 0.5 ms/turn regardless of work done** (`NANOS_PER_SCHED`,
   `time.rs:98`), so unproductive pollers inflate the global clock ~1000–2000× vs
   their own slice.
3. **step2d vtime-jump is defeated by non-empty run queues** (`SleepUntil(0)` /
   `sched_yield` storms), so the gap has no automatic closer on the hot path.
4. **Default guests read the global clock** (`use_thread_local_clock_reads=false`),
   so they see the racing-ahead value directly; correctness rests entirely on the
   #1190 max-floor, not on the gap being small.

## 7. Non-blunting recommendations (for a future, human-reviewed change — NOT done here)

Virtual time is SACRED; this audit changes nothing. If the gap is ever addressed,
prefer options that preserve continuous logical time and determinism:

- Make step2d's vtime-jump target the true next-deadline distance even when the run
  queue is non-empty-but-unproductive (owner trigger #4 — core DetCore scheduling
  change, requires `post-facto-human-review`).
- Suppress `add_scheduler_time` on turns that are pure unproductive re-polls (retry
  with no state change), so `extra_time` tracks real scheduling work.
- Watch the SaBRe thread-local-read path (`use_thread_local_clock_reads=true`): it
  reads the *local* clock, so any future fix that changes local/global reconciliation
  must keep that route monotonic too.

See `ai_docs/scheduler-time-model-fairness-aging-design_20260801.md` for the
bounded-service-lead fairness design that is compatible with continuous logical time.

## 8. Evidence

- Code: all file:line references above were read at ground SHA `09207d80`
  (GitHub links via the blob prefix in the header). Source reading needs no
  compilation, so the structural audit stands at `09207d80`.
- Test binding the #1190 mechanism: `cargo test -p detcore --lib guest_clock` →
  **4/4 PASS at `0ca0dec2`** (the prior audit's ground, and the most recent SHA at
  which `detcore` compiles). **Current main `09207d80` does NOT compile**: it
  carries a *vtime-unrelated* `E0423` break at `detcore/src/lib.rs:1512`
  (`cannot find value 'config' in this scope`) from the `84f3155d` config-clone
  hot-path refactor vs the `#1162` happens-before merge — one-line fix
  `config` → `guest.config()`, tracked in memory
  `main-compile-break-1162-semantic-merge`. That break touches no time code and
  does not affect any finding in this audit; it only prevents re-running the unit
  test at the exact ground SHA.
- Prior audit (foundation): `ai_docs/detcore-global-vs-local-vtime-audit_20260731.md`.
- Related memories: `scheduler-vtime-jump-unproductive-pollers`,
  `pr1095-fake-determinism-clock-review-lesson`,
  `demo5-wedge-clock-skew-past-deadline-poller`,
  `scheduler-time-model-fairness-aging-design_20260801` (design doc).
