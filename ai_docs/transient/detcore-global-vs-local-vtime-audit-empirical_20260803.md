# Detcore global-vs-local virtual-time audit — EMPIRICAL companion

- **Task:** `vtime-global-vs-local-audit-overnight` (owner #161). Read-only audit;
  **no code changes** (virtual time is SACRED).
- **Ground SHA:** hermit `origin/main` =
  `4274144dfe5141178fdd925af7ce623fed6d0d1f` (2026-08-03), ptrace backend.
- **Augments:** the source-level audit
  `ai_docs/detcore-global-vs-local-vtime-audit_20260801.md` (ground `09207d80`),
  which could NOT run tests due to a compile break (E0423 at
  `detcore/src/lib.rs:1512`). That break is fixed on main by `36ee7e70`; the
  clock unit tests now run (below). This doc adds: (1) measured global−local gap
  distributions across 4 workloads, (2) the guest-visible-clock blunting check,
  (3) dated git-blame of the mechanism, (4) L0 unit-test evidence.
- **Experiment dir:** `experiments/vtime-global-vs-local-gap_20260803/`
  (README, metadata.json, results.csv, parse_gap.py, raw traces).
- **GitHub blob prefix (pin links to ground SHA):**
  `https://github.com/rrnewton/hermit/blob/4274144dfe5141178fdd925af7ce623fed6d0d1f/`

---

## 0. One-line answer

The owner's *literal* invariant — "every interaction bumps local time forward to
**global**" — does **not** hold, **by design**: the real forward-bump raises a
thread's local clock to its own committed slice (`threads_time(dtid)`), not to
the global total. The measured global−local gap is therefore large and never
collapses — but it is the *structural* vector-clock quantity Σ(other threads) +
`extra_time`, not a broken invariant. The property that determinism actually
depends on — **guest-visible clock reads reflect the global clock,
monotonically, un-blunted** — HOLDS, and #1190 strengthened it. No recent change
(#1095 / #1190 / vtime-jump / `--no-rcb-time`) blunted or froze virtual time.

---

## 1. Measured global − local gap (evidence)

Method: run under `RUST_LOG=detcore=trace` (no source change); pair each per-dtid
LOCAL `updated rcb clock … logical time` sample with the most recent GLOBAL
`committed_time`; `gap = global − local`. Full per-dtid tables in `results.csv`.

| workload | dtids | committed updates | gap med (ms) | gap p99 (ms) | gap max (ms) |
|---|---|---|---|---|---|
| probe (1 thread) | 1 | 35 | 11.21 | 16.47 | 16.50 |
| w1_multiproc (4 tasks) | 4 | 52 | 15.48 | 94.76 | 94.76 |
| w3_syscall (syscall-heavy) | ~10 | 127 | 65.40 | 93.93 | 95.77 |
| clockread (50+ dtid fork tree) | 53 | 2981 | 1202.29 | 2526.06 | 2585.86 |

Three measured facts nail the mechanism:

1. **Per-turn global advance floor = exactly 500,000 ns = `NANOS_PER_SCHED`**
   (`detcore-model/src/time.rs:98`). The global clock advances every scheduler
   turn by time charged to *no* thread's slice (`extra_time`, folded in
   `sum_up`, `time.rs:782`). This is *why* global outruns local — it is
   deliberate, not drift.
2. **The gap never collapses at an interaction.** Even minimum per-dtid gaps stay
   tens of ms (clockread dtid 9 min 36.3 ms; W1 dtid 9 min 55.9 ms). If the
   literal invariant held, gaps would return to ~0 at each RPC. They do not.
   (The −0.09 ms pooled min is a same-turn pairing artifact.)
3. **The gap grows monotonically with dtid** in clockread (dtid 3 med 15 ms →
   dtid 107 med 2489 ms). dtid order ≈ spawn order, so a late-spawned thread
   legitimately lags the total by Σ(all prior threads' retired work) — the
   textbook vector-clock signature.

## 2. Why (the code path)

- The forward-bump is real: `send_and_update_time`
  (`detcore/src/tool_global.rs:2066-2097`) sends the thread's LOCAL time up,
  and on `Some(lower_bound)` calls `DetTime::advance_to(lower_bound)`
  (`detcore-model/src/time.rs:592`) — a monotonic raise of the LOCAL clock.
- But the target is the thread's **own-slice lower bound**
  (`threads_time(dtid)` = `starting_nanos + threads_duration(dtid)`,
  `time.rs:813`), **never** `GlobalTime::as_nanos` (the total, `time.rs:861`).
  The bump block only fires on `Ordering::Greater` (sched > guest), returns
  `None` on the normal path, and panics if guest > sched
  (`tool_global.rs:1034-1044`). Importing the total would double-count
  Σ(other threads). So a persistent global > local gap is correct.

## 3. Guest-visible clock — the property that matters — HOLDS

- Default `use_thread_local_clock_reads = false`
  (`detcore-model/src/config.rs:62-67`): guest clock reads take the global lower
  bound (`thread_observe_time`), not the raw per-thread clock
  (`detcore/src/syscalls/time.rs:96-107`).
- `observe_guest_clock` applies a monotonic max-floor
  (`self.now = self.now.max(raw)`) over a **shared Arc across the process tree**.
- Empirical confirmation (clockread, two concurrent reader groups): both threads
  observe the same epoch series `1_767_225_600 → …601`; guest stdout is strictly
  monotonic and deterministic (`1767225600.233056510 → 1767225602.571677595`).

## 4. Blunting / "fake determinism" check — PASS

The #1095 debacle was fake determinism (guest clock reset to epoch/origin). This
run shows the opposite:

- First guest-observed read is `1767225600.233056510`, **not** `.000000000` —
  the max-floor carries committed time across `exec`; the #1095 origin-reset is
  gone.
- No exact-integer-second values among guest reads; times are full-precision,
  not quantized/rounded/frozen.

## 5. git-blame — dated provenance

| mechanism | commit | date | change |
|---|---|---|---|
| `NANOS_PER_SCHED`, `add_scheduler_time`, `extra_time`, `threads_time` | `c6d05ef2` | 2022-11-12 | Initial commit (foundational, 4 yrs old) |
| `DetTime::advance_to` (local forward-bump on RPC) | `602a9b2a` | 2026-07-24 | PR #257 target/maximum timeslice controls |
| `observe_guest_clock` max-floor | `c4a4bba2` | 2026-07-28 | #1190 normalize guest clock after exec |
| `use_thread_local_clock_reads` (default FALSE) | `a520b67c` | 2026-07-27 | PR #845 SaBRe exec/descriptor compat |

**Verdict on the suspects:** the global-outruns-local structure predates all of
them by ~4 years. #1190 *strengthened* continuity (monotonic max-floor across the
Arc, superseding the #1095 reset). #257's `advance_to` is a monotonic raise, not
a blunt/reset. `use_thread_local_clock_reads` defaults FALSE, so guest reads use
the global lower bound. None weakened continuity.

## 6. L0 unit tests (previously unrunnable)

At `4274144d`, with the compile break fixed by `36ee7e70`:

- `cargo test -p detcore --lib guest_clock` → 4/4 PASS
- `cargo test -p detcore --lib forked_process_shares_guest_clock_domain` → 1/1 PASS
- `cargo test -p detcore --lib exec_reconnect_retires_siblings_and_reuses_live_scheduler_and_clock_state` → 1/1 PASS

## 7. Recommendation

No action; virtual-time policy is sound. If the "large discrepancy is FISHY"
intuition recurs, the durable clarification is: **the internal per-thread clock
is a vector-clock component and is *supposed* to lag the global total; the
invariant to protect is guest-visible monotonicity via `observe_guest_clock`,
not equality of the internal local clock to the global total.** A one-line doc
comment near `threads_time`/`advance_to` distinguishing "own-slice lower bound"
from "global total" would prevent re-litigation — but that is a doc change for a
future task, not part of this read-only audit.
