# Hermit / Detcore log-analysis tooling — inventory (from source)

Owner asked "remind me what's there." This enumerates every log-analysis
capability in the hermit tree today, read from source at hermit
`1ece0654` (2026-07-31), with the exact file, what it answers, and its gaps.
Produced by hermit-237 (log-science) for `demo5-log-science-diff` /
`demo5-rigorous-rootcause` (lead hermit-226).

## 0. What produces the logs

- **`detlog!` / `detlog_debug!`** macros — `detcore/src/detlog.rs`. Emit
  `DETLOG <msg>` at INFO / DEBUG respectively. Everything meant to be
  deterministic (syscalls, results, RNG seeds, memory hashes, auxv) is wrapped in
  these, so `grep ' DETLOG '` is the deterministic-facts stream.
- **`tracing` events** from `detcore` / `detcore::scheduler` — the COMMIT turns
  (the serialized schedule), `inbound syscall` / `finish syscall`, `new logical
  time: DetTime{...}`, `ending timeslice TN`, `advancing committed_time`,
  quiescence/park/unpark lifecycle.
- **Capture flags** (global, before the subcommand): `-l/--log <level>`
  (`HERMIT_LOG`), `--log-file <f>` (`HERMIT_LOG_FILE`, keeps guest stdout clean),
  per-target `RUST_LOG`/`tracing` filtering e.g.
  `HERMIT_LOG='info,detcore::scheduler=trace'`. #113's "no stream flood" rule =
  always redirect the trace to a file, never interleave with guest stdout.

## 1. `hermit log-diff <a> <b>` — the differential comparator

- CLI: `hermit-cli/src/bin/hermit/logdiff.rs`; engine:
  `detcore/src/logdiff.rs`.
- Splits each log into (possibly multi-line) messages, strips timestamps,
  compares **matched pairs by position** (`v1.iter().zip(v2)`), reports per-line
  mismatches, and at the end reports "run N contains K extra messages."
- Two comparison sets: **Deterministic** (default: DETLOG + scheduler COMMIT,
  minus host-timing noise) and **FullTrace** (`comparison` field; every message).
- Noise normalization it already does: hex addresses → `<ADDR>`, durations/nums →
  `<NUM>`/`<NANOSECONDS>`, `/tmp/…"` → `/tmp/<somewhere>`, `/proc/<pid>/` →
  `/proc/<PID>/` (`strip_log_entry`, behind `--strip-lines`); and it *drops*
  host-timing-sensitive bookkeeping: `{InternalIOPolling: …}` COMMITs,
  `[sabre-internal-pipe-io]` / `[sabre-loopback-poll-zero-timeout]` turns, and
  `advancing committed_time from …` lines.
- Flags: `--strip-lines`, `--limit N` (0 = all), `--ignore-lines <substr>`
  (repeatable), `--syscall-history N` (print the N completed syscalls before each
  divergence), `--no-color`, `--skip-commit` / `--skip-detlog` (isolate a
  *scheduling* vs *data* divergence), `--git-diff`, `--include-detlogs
  syscall,syscallresult,other`.
- Answers: **"what is the first line-content divergence, and what led up to it?"**

## 2. `hermit analyze` — chaos race localizer

- `hermit-cli/src/bin/hermit/analyze/` (`AnalyzeOpts` in `analyze/types.rs`).
- Repeats `hermit run` under a controlled chaos search. Given a **target
  property** (`--target-exit-code` [default nonzero], `--target-stdout`,
  `--target-stderr` regexes) and a baseline, it minimizes the chaos interventions
  that flip the outcome and binary-searches for the **critical events**
  (instructions whose reordering causes/removes the target), printing their stack
  traces. Inputs: `--run1-seed` / `--run1-preemptions` / `--run1-schedule`
  (+ run2 variants), `--search`, `--minimize`, `--needleman`, `--selfcheck`.
- Answers: **"which event ordering causes this failure?"** (a race, not a
  first-line diff).

## 3. `hermit bisect --good <sched> --bad <sched>` — schedule bisector

- `hermit-cli/src/bin/hermit/bisect.rs` (wraps `AnalyzeOpts`).
- Bisects two **recorded schedules** (`PreemptionRecord` / `SchedEvent`, from
  `hermit run --record-schedule-to` / `--record-preemptions-to`) to localize the
  event ordering that turns pass→fail, with edit-distance / Needleman-Wunsch
  alignment (`common/edit-distance`). `--execution-context N`, `--report-file`,
  `--jitter-dist`.
- Answers: **"between a known-good and known-bad schedule, which event flips it?"**

## 4. `hermit verify` — two-run determinism check

- `hermit-cli/src/bin/hermit/verify.rs` drives `run --verify` and calls the same
  `logdiff::LogDiffOpts` engine internally to report the first divergence between
  the two runs.

## 5. `scripts/log_timeslice.rs` — timeslice / virtual-time structure (rust-script)

- Reads a `--log info` stream on stdin. Reconstructs each **timeslice** and
  reports per slice: owning dtid, wall duration, **committed virtual-time
  advance**, syscall count, COMMIT turns, RCB delta; then the **turn-taking
  pattern** (run-length-encoded dtid sequence), context-switch count, and
  anomalies: `LONG-WALL`, `VT0` (≥5 syscalls with 0 virtual advance), `VT-JUMP`
  (≥20 ms virtual in one slice), longest slices, virtual-time-STUCK,
  virtual-time-JUMP totals.
- Answers: **"where does wall time go, where does virtual time jump/stall, and
  what is the interleaving shape?"**

## 6. Adjacent / supporting

- `hermit strace` (`strace.rs`), `hermit schedule-search` (`schedule_search.rs`),
  `hermit instruction-map` — syscall trace, schedule fuzzing, instruction
  mapping.
- record/replay (`hermit record start --verify`) diffs record-vs-replay logs.
- `common/edit-distance`, `common/digest` — schedule/log comparison utilities.

## Gaps this task found (demo5 wedge exposed them)

1. **Path normalization gap.** `--strip-lines` normalizes `/tmp/` and
   `/proc/<pid>/` but **not per-run working/output dirs**. demo5 runs write to
   `ignored/bisect-demo5/<sha>-good-r…` vs `…-bad-re…`, so every path mention is a
   spurious mismatch; you must hand-feed `--ignore-lines`. → add a
   `--strip-run-paths` / broaden `strip_log_entry`.
2. **Alignment gap (already a TODO in `logdiff.rs`).** The comparator zips matched
   vectors, so a *benign early reorder* (e.g. a parent/child swap around
   `clone3`) derails alignment and floods the rest with mismatches — it never
   reaches a *much-later* real divergence, and it cannot express "thread X
   disappears" (only "run N has K extra messages"). demo5's real event is a
   **disappearance**, invisible to a first-line-content diff.
3. **No per-dtid starvation lens.** Nothing answers *"which dtid stopped getting
   scheduled, at what committed vtime, and how big is the silent tail?"* —
   the central demo5 question. `log_timeslice.rs` has the raw turn-taking but only
   as one RLE string to eyeball. → filled by the new `dtid_activity.rs` query
   (see `experiments/demo5-rootcause-20260731/log-science/`), which prints a
   per-dtid table + flags `STARVED-TAIL` (alive thread never rescheduled while the
   clock races ahead) vs `EXITED` (clean exit) vs `BUSY-POLLER`.
