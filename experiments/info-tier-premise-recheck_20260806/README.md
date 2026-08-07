# INFO-tier comparator: premise recheck on current main

**Task:** `add_an_info_tier` · **Agent:** hermit-w2 (opus-5) · **Date:** 2026-08-06
**Host:** devbig014 (316 cores) · **Backend:** ptrace · **Slot:** `worktrees/w2/hermit`
**Hermit under test:** `1fadc03779f2a246a9b5af5d4a93533511c837df` — **stock `origin/main`, no local edits**
**Prior experiment:** `experiments/strict-certification-mutation-sweep_20260806` @ hermit `f89c69766`

## Question

`add_an_info_tier` asks for a new comparator at *strip_lines=false, full_trace=false, wall-clock
prefix stripped, DEBUG excluded* — "the missing rung between `stripped` (too weak) and
`canonical/full_trace` (too strong)". Before building it: **does the shipped `--verify-strict` on
current main already occupy that rung?**

## Why the question is live: the prior evidence predates the fix

The "too strong" half of the diagnosis is that `--verify-strict` fails a trivially deterministic
control on 14 divergent lines, **all DEBUG**, three classes of hermit's own instrumentation. That is
a *capture-level* symptom, and the capture level changed after that experiment ran:

| | commit | date |
|---|---|---|
| prior experiment's hermit | `f89c69766` | Wed Aug 5 09:49:21 2026 -0700 |
| PR #1661 "verify: keep BitwiseInfoV1 within the INFO envelope" | `38cf5373` | Thu Aug 6 06:04:42 2026 -0400 |

```
git merge-base --is-ancestor 38cf5373 f89c69766   # rc=1 -> #1661 NOT in the prior build
git merge-base --is-ancestor f89c69766 38cf5373   # rc=0 -> the prior build precedes #1661
```

On current main, `hermit-cli/src/bin/hermit/verify.rs:590-605`:

```rust
requested.unwrap_or(match strictness {
    LogCompareStrictness::Stripped => LevelFilter::DEBUG,
    LogCompareStrictness::Canonical => LevelFilter::INFO,
})
```

The producer command passes no `--log`, so `requested = None` and `Canonical` captures at **INFO**.
The three DEBUG instrumentation classes cannot enter the comparison.

## Method

Same six guests as the prior sweep (five plant a defect, one is the positive control). Each mutant
appends a byte to a state file and reads back the new size, so run A and run B differ **by
construction**. Sources in `mutants/*.c`; rebuild with `gen.sh`.

Verdict is read from the **typed `--verify-json` predicate**, never from `rc` and never from the
stderr banner:

```
bitwise_parity == true  AND  compared_log_messages.left > 0  AND  compared_log_messages.right > 0
```

The non-zero denominators are load-bearing: a comparison that selected zero lines is a **no-result**,
not a match. Every row below carries both counts.

**Parallel-safety.** Each guest gets its own `/tmp/w2state_<guest>` (the prior sweep shared one
path). Sharing it across concurrent guests would manufacture divergence and score a **fake
"caught"**.

**Slow-drain — sequential by construction.** Running the six guests concurrently made **5 of 5 hit
the 900 s timeout, including the control that passes standalone in 1 s**. Sequentially every guest
finishes in ≤1 s. This is the known `--verify` slow-drain under load, not a property of any guest;
`sweep.sh` is sequential deliberately. A concurrent run would have produced a control failure and
"confirmed" the stale premise.

## Result — the rung already exists

`clean_ctrl` must certify; the other five must be caught. Denominator = INFO messages compared,
reported per side. Full rows in `results.csv`; raw verdicts in `logs/<guest>.json`.

| guest | verdict | `bitwise_parity` | INFO compared L\|R | `log_scope` | typed predicate | expected | agrees |
|---|---|---|---|---|---|---|---|
| `clean_ctrl` | `matched` | **true** | 56\|56 | `info` | certified | certify | ✅ |
| `mut_stdout` | `diverged` | false | 66\|66 | `info` | rejected | catch | ✅ |
| `mut_exit` | `diverged` | false | 66\|66 | `info` | rejected | catch | ✅ |
| `mut_detlog_only` | `diverged` | false | 74\|74 | `info` | rejected | catch | ✅ |
| `mut_addr` | `diverged` | false | 69\|69 | `info` | rejected | catch | ✅ |
| `mut_path` | `diverged` | false | 72\|72 | `info` | rejected | catch | ✅ |

**6/6 agree. Positive control certified; 5/5 planted defects caught.** Every comparison ran at
`log_scope: "info"` with non-zero counts on both sides. The three defects the `stripped` producer
probe misses — a differing `read()` return length, a differing pointer arg, a differing `openat`
path — are all caught here.

Control verdict, verbatim from `logs/clean_ctrl.json`:

```json
{"verified":true,"bitwise_parity":true,"verdict":"matched",
 "comparison":{"strictness":"canonical","compare_logs":true,"log_scope":"info",
   "strip_lines":false,"canonicalize_addresses":true,"full_trace":true,
   "exact_remainder":true,"stripped_prefixes":["real-wall-clock-prefix/v1"],
   "canonicalizations":["host-address-to-first-appearance-ordinal/v1"],
   "ignore_lines":false,"skip_commit":false,"skip_detlog":false},
 "compared_log_messages":{"left":56,"right":56}}
```

56\|56 reproduces the prior experiment's own INFO cross-check (`INFO 56|56 / 0 divergent`) exactly —
the two experiments agree on the measurement and disagree only about which tier `--verify-strict`
runs, which is what #1661 changed.

## Verdict: the comparator half of `add_an_info_tier` is STALE

The requested rung is occupied by the shipped `--verify-strict`. Building a third
`LogCompareStrictness` variant would add a redundant tier and a second policy token for a
distinction that no longer exists on main.

Two deliberate departures from the task's literal spec, both measured rather than assumed:

* **`full_trace=true`, not `false`.** The task asks for `full_trace=false`. On current main
  `full_trace=true` *is* the INFO comparison, because the capture envelope is INFO — stderr prints
  `Comparing INFO messages...`. Setting `full_trace=false` would select `LogComparisonMode::Deterministic`
  (DETLOG+COMMIT), a **different and narrower** set, not "INFO with DEBUG excluded".
* **`canonicalize_addresses=true`, not "no canonicalization".** The task says "no extra
  stripping/canonicalization". Address ordinalization is present and it did **not** mask anything:
  `mut_addr`, whose whole defect is a differing pointer argument, is caught. Ordinalization
  preserves identity/order/aliasing, so a changed pointer still diverges.

## What is NOT stale

The **consumer side is unchanged and still wrong**, verified on this same tree:

* `tests/backend-parity/run_matrix.py:536` — `VERIFY_WITNESS_DETLOG = b"Determinism verified"`.
  The verdict is still keyed on **scraping the stderr banner**, not on the typed JSON predicate.
* `tests/backend-parity/run_matrix.py:603` — `"detlog": "L2 DETLOG-bitwise: --verify double-run matched"`.
  Still labels the guest-visible result **L2 DETLOG-bitwise**.

Neither was touched here: `run_matrix.py` is owned by **hermit-w5** (`feat/parity-mutation-harness`)
and the task requires reconciling before editing.

`collect-fullcorpus.sh` was not found at the path the task cites
(`tests/backend-parity/collect-fullcorpus.sh` does not exist on this tree); that citation needs
re-deriving before anyone acts on it.

## Scope and limits

* **ptrace only.** liteinst, dbi and kvm were not re-measured. The prior experiment's per-backend
  blockers stand unaddressed by #1661: liteinst heap/stack content nondeterminism, DBI's
  un-virtualised `dtid`, KVM not completing on this host.
* **Six guests, one host, one run each.** No repetition, so this speaks to sensitivity, not flake rate.
* The positive control was run twice (once standalone, once in the sequential sweep); both certified.

## Reproduction

```bash
cd experiments/info-tier-premise-recheck_20260806
./gen.sh                                   # rebuild the six guests
HERMIT=<path-to-hermit-binary> ./sweep.sh  # sequential; see "Slow-drain"
column -s, -t < results.csv
```
