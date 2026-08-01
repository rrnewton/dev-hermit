# Q3 — Load-Independence Proof (the P0 gate)

**Question (SACRED invariant):** Are Detcore scheduler *decisions* a pure function
of guest events, independent of host wall-clock / load? Any load-dependent
*decision* is an instant P0.

**Verdict: PASSED — no load-dependent decision exists. No escalation.**

Two independent axes, both agree.

## Axis 1 — SOURCE (decision path has no wall-clock read)

From the authoritative RCB-time/timeslice/step2d source audit
(`source-mechanism.md`, all paths under `hermit/`):

- Next-thread choice is the deterministic `run_queue`/priorities plus seeded
  PRNGs. Virtual time comes from guest-event counters. `guest.read_clock()`
  (`detcore/src/lib.rs:368`) reads the **reverie PMU RCB counter (guest retired
  branches)**, not host time.
- No `Instant::now`, `SystemTime`, `gettimeofday`, or host `clock_gettime` feeds
  any scheduling decision in `scheduler.rs`, `lib.rs`, or `tool_local.rs`.
- The ONLY host-time usage in the scheduler loop is `Backoff` cadence
  (`scheduler.rs:619-654`: `yield_now` / `thread::sleep` / `tokio::time::sleep`) —
  it controls the re-poll RATE of an Ivar, not which thread runs or how much
  virtual time is charged.
- One documented host-timing sensitivity is deliberately quarantined: the *count*
  of nonblocking poll retries is wall-clock dependent (`scheduler.rs:2433-2438`),
  but its time contribution is kept OFF the DETLOG (`bump_global_time`
  `scheduler.rs:2478-2486`) and the `committed_time` line is EXCLUDED from
  `--verify` comparison (`scheduler.rs:2550-2554`). So retry counts perturb only
  the numeric committed_time value, never the ordering/decision.

⇒ By construction, decisions are load-independent; only the numeric committed_time
value can wobble, and that value is excluded from verification.

## Axis 2 — EMPIRICAL (contrasting-load, cross-run decision-trace diff)

Crawl harness, `hermit run --strict` (RCB-time ON), bare busybox boot, ptrace,
hermit @ `origin/main 0ca0dec2`. Raw `--log info` traces:
`scratch/demo5-icount-sleep/out/{on-run3,on-run4,on-run5,off-run3,off-run4}/`.

Method: extract every `COMMIT turn N, dettid D using resources {...}, on
previously committed T` line (the full per-turn decision), then compare under
progressively stricter canonicalization.

| Comparison | host load | turns | decision-ordering diffs | committed_time drift | SleepUntil-target drift |
|---|---|---|---|---|---|
| off-run3 vs off-run4 | 37–54 vs 46–58 | 41043 | **0** | max 238ns, mean 179ns | max 256ns, mean 7.8ns |
| on-run3 vs on-run4 | ~40 vs ~44 | 39803 | **0** | 0 (byte-identical) | 0 |
| on-run3 vs on-run5 | ~40 vs ~41 | 39803 | **0** | 0 (byte-identical) | 0 |

- "decision-ordering diffs" = diff after normalizing the two derived numeric
  values (committed_time tail; `SleepUntil(LogicalTime(N))` targets, which QEMU
  derives from committed_time and which therefore inherit its drift).
- The sleep=off pair ran at genuinely contrasting/rising load and still produced
  a **byte-identical decision sequence over all 41043 turns**; the only variation
  is a sub-256ns wobble in committed_time — exactly the verify-excluded
  poll-retry-count quarantine the source predicts.
- The sleep=on runs were byte-identical *including* vtime values, showing the
  drift is not even always present.

## Conclusion

The earlier "load-sensitive wedge" observation is **confirmed harmless**: host
load changes only (a) wall-clock duration and (b) the verify-excluded
committed_time numeric value. It never changes a scheduling decision. The demo5
wedge is a *deterministic* livelock (see `source-mechanism.md`), not a
load-dependent race. **Q3 P0 gate PASSES; no escalation.**

## Reproduction

```bash
cd ~/work/dev-hermit/scratch/demo5-icount-sleep/out
canon(){ grep -aE ' COMMIT turn [0-9]+, dettid ' "$1" \
  | sed -E 's/^[0-9T:.-]+Z +INFO [^ ]+: //'; }
full(){ sed -E 's/, on previously committed [0-9_]+\.[0-9_]+s$//;
  s/SleepUntil\(LogicalTime\([0-9]+\)\)/SleepUntil(LogicalTime(NORM))/g'; }
diff <(canon off-run3/hermit-info.log | full) \
     <(canon off-run4/hermit-info.log | full) | grep -cE '^[<>]'   # -> 0
```
