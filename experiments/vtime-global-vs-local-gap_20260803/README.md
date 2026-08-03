# GLOBAL (committed) vs LOCAL (per-thread) virtual-time gap — empirical measurement

Task: `vtime-global-vs-local-audit-overnight` (tg #161, P2, owner-wanted).
Hermit SHA: `4274144dfe5141178fdd925af7ce623fed6d0d1f` (origin/main, ptrace backend).
Companion source-level audit: `ai_docs/detcore-global-vs-local-vtime-audit_20260801.md`
(re-verified here) and the empirical write-up
`ai_docs/detcore-global-vs-local-vtime-audit-empirical_20260803.md`.

## Question

The time model's designer states the invariant: **any interaction between a
local thread and the scheduler bumps that thread's local time FORWARD to the
global time.** A large, persistent discrepancy (global committed racing ahead
while local clocks lag) would be FISHY — a broken invariant. Virtual-time policy
is SACRED; a separate concern is whether any recent change *blunted* time
(rounding, freezing, per-process reset) — the "fake determinism" anti-pattern
behind the #1095 debacle.

This experiment measures the actual global−local gap distribution across four
workloads, dates the mechanism with git-blame, and cross-checks the
guest-visible clock for blunting. **No source was modified** (research/audit
only).

## Method

Run each workload under `RUST_LOG=detcore=trace` (no source change). Two series
are already emitted by the stock build:

- **GLOBAL committed time** — `advancing committed_time from X to Y` and
  `[sched-step3] Stepping scheduler, … committed_time C` (`detcore::scheduler`).
- **LOCAL per-thread time** — `[dtid N] updated rcb clock, new logical time:
  DetTime { … }, i.e. <sec>s` (`detcore`).

`parse_gap.py` pairs each per-dtid LOCAL sample with the most recent GLOBAL
`committed_time` and reports `gap = global − local` (min/med/p90/p99/max) per
dtid and pooled. Raw traces are bulky (5–50 MB) and are NOT committed — they live
in gitignored `scratch/vtime-audit/`; regenerate any of them via the commands in
`metadata.json` / the Reproduction section below.

## Results (see `results.csv`)

Pooled gap (global − local), all dtids:

| workload | dtids | committed updates | gap med (ms) | gap p99 (ms) | gap max (ms) |
|---|---|---|---|---|---|
| probe (1 thread) | 1 | 35 | 11.21 | 16.47 | 16.50 |
| w1_multiproc (4 tasks) | 4 | 52 | 15.48 | 94.76 | 94.76 |
| w3_syscall (syscall-heavy) | ~10 | 127 | 65.40 | 93.93 | 95.77 |
| clockread (50+ dtid fork tree) | 53 | 2981 | 1202.29 | 2526.06 | 2585.86 |

Additional measured facts:

- **Per-turn global advance has an exact 500,000 ns floor** = `NANOS_PER_SCHED`
  (`detcore-model/src/time.rs:98`); median per-turn advance ≈ 604,020 ns. The
  global clock advances every scheduler turn by an amount charged to *no* thread's
  slice (`extra_time`), which is *why* global structurally outruns local.
- **The gap never collapses to ~0 at a thread↔scheduler interaction.** Even the
  minimum per-dtid gap stays tens of ms (e.g. clockread dtid 9 min 36.3 ms; W1
  dtid 9 min 55.9 ms). The small negative pooled min (−0.09 ms) is a
  same-turn pairing artifact (local sampled just after a committed snapshot).
- **The gap grows monotonically with dtid in clockread** (dtid 3 med 15 ms →
  dtid 107 med 2489 ms). dtid order ≈ spawn order, so a late-spawned thread
  legitimately lags the global total by the sum of all prior threads' retired
  work — the vector-clock signature, not a bug.

## Interpretation — two readings of the invariant

1. **Literal reading — internal `thread_logical_time` bumped to the global
   TOTAL: FALSE, and by design.** The real forward-bump exists
   (`send_and_update_time` → `DetTime::advance_to(lower_bound)`,
   `detcore/src/tool_global.rs:2066`), but its target is the thread's own-slice
   lower bound (`threads_time`, `detcore-model/src/time.rs:813`), never
   `GlobalTime::as_nanos` (the total). Importing the total would double-count
   Σ(other threads). The bump block only fires on `Ordering::Greater`
   (sched > guest) and returns `None` on the normal path
   (`tool_global.rs:1034-1044`). So the measured global > local gap is the
   structural, expected vector-clock quantity, not a broken invariant.

2. **Determinism-relevant reading — guest-VISIBLE clock reads reflect the global
   clock monotonically: TRUE.** Default `use_thread_local_clock_reads = false`
   (`detcore-model/src/config.rs:62-67`) routes guest clock reads to the global
   lower bound, then `observe_guest_clock` applies a monotonic max-floor
   (`self.now = self.now.max(raw)`) over a shared Arc across the process tree.
   In the clockread workload both threads observe the same epoch-based series
   (`1_767_225_600 → …601`) and guest stdout is strictly monotonic and
   deterministic. This is the property that actually matters for determinism, and
   it holds.

## Blunting / "fake determinism" check — PASS

- First guest-observed clock read is `1767225600.233056510`, **not**
  `.000000000` — the #1095 origin-reset behavior is gone; the max-floor carries
  committed time across exec.
- No exact-integer-second (round) values among guest reads; times are full-
  precision, not quantized/frozen.
- git-blame dates: the global-outruns-local machinery is foundational
  (`c6d05ef2`, 2022-11-12). `observe_guest_clock` max-floor = `c4a4bba2`
  (#1190, 2026-07-28) supersedes the #1095 reset. `advance_to` (local bump) =
  `602a9b2a` (#257, 2026-07-24). None of the recent suspects
  (#1095 / #1190 / vtime-jump / `--no-rcb-time`) *weakened* continuity; #1190
  strengthened it.

## Reproduction

```bash
cd worktrees/vtime/hermit   # origin/main @ 4274144d, target/debug/hermit built
RUST_LOG=detcore=trace ./target/debug/hermit run --strict -- \
  sh -c '(for i in $(seq 1 8); do date +%s.%N; done) & \
         (for i in $(seq 1 8); do date +%s.%N; done) & wait'  2> clockread.trace
python3 ../../experiments/vtime-global-vs-local-gap_20260803/parse_gap.py clockread.trace
```

`cargo test -p detcore --lib guest_clock` (4/4),
`forked_process_shares_guest_clock_domain` (1/1),
`exec_reconnect_…clock_state` (1/1) all pass at this SHA (L0), establishing the
unit-level guest-clock behavior the earlier compile-broken audit could not run.
