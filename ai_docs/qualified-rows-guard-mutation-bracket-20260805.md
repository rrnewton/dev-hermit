# `qualified-rows` guard — mutation bracket, and the measured cost of the blocked coverage flip

**Task:** `validate_ledger_qualified_rows`
**Date:** 2026-08-05
**Scope of this pass:** unit-test the landed guard. No full `validate` run (livelock
risk, per directive). No fetch/push/land (egress down box-wide). Local ci-hub code only.

---

## What was already true on arrival

The task's deliverable — the canonical accessor — is **landed on parent main**, not
pending:

- `ci-hub/validate/qualified_rows.py` (`is_qualified` / `qualified_rows`), landed in
  `16ce9cb`, amended by `95c40d2` (over-run `ran >= expected`).
- CLI front door `ci-hub ledger qualified-rows` (`ci-hub/ci-hub.rs:1654,4489`).
- Live consumer: `ci-hub/health/pr_status.py:515` shells out to it.
- Docstring states both invariants (order by `finished_at`, drop incomplete/aborted/
  zero-executed before bucketing).

Baseline on arrival: `tests/test_qualified_rows.py` = 6 tests, all green.

---

## Finding 1 — the bracket had four holes, including the headline clause

A guard clause with no failing mutant is decoration: it survives deletion in a refactor
and nothing reports it. I mutated each clause of `is_qualified`/`qualified_rows` and
re-ran the file's own tests.

**Before (6 tests) — 5 caught / 4 unbracketed:**

| Mutation | Result |
| --- | --- |
| delete `result == "pass"` | **STILL PASSED — UNBRACKETED** |
| delete `not isinstance(executed, bool)` | **STILL PASSED — UNBRACKETED** |
| delete `expected > 0` | **STILL PASSED — UNBRACKETED** |
| sort key drops commit/slot/log_file tie-breakers | **STILL PASSED — UNBRACKETED** |
| delete `executed > 0` | caught |
| `ran >= expected` → `True` | caught |
| delete `event_time(row) is not None` | caught |
| return unsorted (file position) | caught |

The first row is the one that matters. **`result == "pass"` — the accessor's entire
headline claim, "only a PASS counts as green" — had zero coverage.** Every fixture in
the file was built by a `_row()` helper that hardcodes `result: "pass"`, and no test
ever overrode it. Deleting the clause left the suite fully green. A `result="fail"` row
with a positive executed count and a complete gate contract would have been emitted as a
qualified GREEN by the accessor whose stated purpose is to prevent exactly that.

The other three are real but narrower:

- **bool guard.** `bool` subclasses `int` in Python, so `executed_tests: true` satisfies
  `isinstance(x, int) and x > 0`. A producer type error would be counted as a green with
  an unknown test count.
- **`expected > 0`.** Without it, a row claiming a zero-gate contract makes
  `ran >= expected` trivially true — "green over no work at all".
- **sort tie-breakers.** Concurrent slots routinely finish inside the same recorded
  second. Without the tie-breakers an equal-timestamp group falls back to read order,
  which is the position-not-event-time defect the accessor exists to remove.

## Finding 2 — bracket closed, 9/9 mutants now caught

Added 6 tests to `ci-hub/validate/tests/test_qualified_rows.py` (6 → 12), each with a
positive control so a refusal is attributable to the clause under test and not to a
broken fixture:

- `test_non_pass_results_never_qualify` — `fail`, `timeout`, `truncated`, `pass-partial`,
  `no_result`, `""`, `"PASS"`, and a missing `result` key, all otherwise-perfect rows;
  plus the identical shape with `result="pass"` asserted to qualify. `pass-partial` is
  deliberate: it is `flake_class.effective_result`'s downgrade for a non-full-coverage
  pass and contains the substring `pass`, so a `in`/prefix test instead of equality
  would admit it. That variant is now its own mutant and is caught.
- `test_boolean_executed_tests_does_not_qualify`
- `test_zero_expected_gate_contract_does_not_qualify`
- `test_equal_event_times_sort_deterministically` — also asserts order is invariant
  under input reversal
- `test_naive_timestamp_fails_closed`
- `test_load_rows_counts_malformed_and_skips_non_dict`

**After (12 tests) — 9 caught / 0 unbracketed:**

```
drop result=='pass'              -> CAUGHT
result equality -> substring     -> CAUGHT
drop executed>0                  -> CAUGHT
drop bool guard                  -> CAUGHT
drop expected>0                  -> CAUGHT
ran>=expected -> True            -> CAUGHT
drop event_time clause           -> CAUGHT
return unsorted                  -> CAUGHT
sort key drops tie-breakers      -> CAUGHT

MUTATION BAR: 9 caught / 0 unbracketed (of 9)
UNMUTATED CONTROL: 12 passed
```

Regression check: full `ci-hub/validate/tests` suite **134 passed**. CLI front door
exercised read-only and agrees with the library:
`qualified-rows: 107/585 qualified; malformed=0; sorted=finished_at`.

---

## Finding 3 — the blocked coverage flip, measured

The open rule-compliance defect (coordinator note, 2026-08-05 07:01) is that
`qualified_rows.py:51-56` still keys on `executed_tests > 0`, while the settled rule is
that `executed_tests` is **diagnostic only, never a key**. The correct final form gates
on coverage: `planned_test_nodes > 0 AND zero_executed_nodes == [] AND absent_nodes == []`.

That note called the flip `SEQUENCED-AFTER wire_the_coverage_node` and estimated the
resulting population at "~47 landing receipts". Measured on the live 585-row ledger:

| Population | Count |
| --- | --- |
| ledger rows | 585 (malformed 0) |
| rows carrying a `coverage` object at all | 49 / 585 |
| **CURRENT qualified** (`executed_tests > 0` form) | **107 / 585** |
| **COVERAGE-GATED qualified** (planned>0, zero_executed==[], absent==[]) | **37 / 585** |
| in both | 37 |
| current-only | 70 |
| **coverage-only** | **0** |

Two things follow, and the second was not previously established:

1. The sequencing call is **confirmed with a number**: flipping today drops the green
   analytics population 107 → 37, a **65% loss**. The estimate was ~47; the observed
   figure is 37.
2. **`coverage-only = 0` — the two predicates are strictly nested.** Not one row is
   admitted by coverage-gating that `executed_tests > 0` rejects. So the flip is a pure
   tightening with **no disagreement in admission direction**: the only risk it carries
   is going dark, not admitting something wrong. When the write path lands and coverage
   is populated, the flip needs no correctness re-litigation — only a check that the
   population has recovered.

**Recommended trip-wire for the flip** (not implemented — it belongs with the write-path
landing): gate on coverage once coverage-carrying rows exceed the current qualified
population, i.e. flip when `count(rows with coverage) >= 107`-ish rather than on a date.
That binds the switch to the observable condition instead of a guess.

I did **not** make the coverage change. It is blocked, the coordinator sequenced it
after `wire_the_coverage_node`, and making it now would take the accessor dark.

---

## Finding 4 — the deliverable's "lint" half, now built

The task deliverable reads "one canonical accessor … **plus a docstring/lint** stating
the two invariants". The docstring existed (`qualified_rows.py:2-15`); the lint did not.
Nothing mechanically detected a *new* ad-hoc reader — which is the actual shape of both
2026-08-04 incidents. Neither was a bug *inside* a hardened tool; both **bypassed** the
hardened tools, and the bypassing line looks perfectly ordinary in a diff.

**Built:** `ci-hub/validate/tests/test_ledger_reader_allowlist.py` (7 tests). It scans
every `.py`/`.rs`/`.sh` file under `ci-hub/` for references to the ledger and fails on
any path not present in one of two explicit maps:

- `DECLARED_READERS` — 15 entries, each with a one-line reason recording *why* the raw
  read is allowed (canonical accessor, hardened tool that qualifies first, producer,
  CLI dispatch, prose, or test fixture). Derived by enumerating the live tree, not
  guessed.
- `KNOWN_BYPASSES` — readers that genuinely violate the invariants and are not yet
  fixed. Listing the debt keeps the lint landable *today* while counting rather than
  hiding it, and a separate ratchet test refuses silent growth of the set.

Design points that matter for a guard's survival:

- **Staleness is checked only for files that still exist.** A declaration whose file is
  merely absent is not stale. The parent tree routinely carries other agents' untracked
  WIP (`validate/anchor_select.py` today), and a clean checkout legitimately lacks those
  paths — failing on absence would make the lint red for everyone whose checkout differs
  from the author's, which is the fastest way to get a guard disabled. Caught during
  development: the first version would have broken on a clean checkout.
- **The checker is pure and injectable** (`classify(paths, present=…)`), so its own
  logic is bracketed rather than only being run against the live tree. A lint whose
  logic is untested can pass for the wrong reason.
- **Bracketed both directions on the LIVE tree, not just in unit tests.** A real
  violating file was planted in `ci-hub/validate/`, the lint failed naming it exactly
  (`Undeclared reader(s) of the validate run ledger: validate/zz_planted_bypass_probe.py`),
  and it returned green when the file was removed. Plus a negative control (all declared
  paths accepted), an anti-vacuity check (the scan must find ≥10 readers and must include
  `qualified_rows.py`), and a check that a bypass reason is actionable prose, not a label.

## Finding 5 — building the allowlist surfaced a live bypass

Enumerating the readers is what found it. `ci-hub/remediation/protocol.py:312-330`,
`estimate_local_validate_cost()`:

```python
if record.get("profile") == "full" and wall > 0 and cpu >= 0:
    samples.append((wall, cpu))
samples = samples[-50:]
```

It filters on `profile == "full"` and `wall > 0` — and nothing else. **Both invariants
are violated in one function:** `samples[-50:]` is the last 50 by *file position*, not
`finished_at`; and no `result`/completeness qualification runs, so reds, truncated runs,
and aborted runs all contribute. It then labels its own output
*"derived from p90 of the last 50 usable **successful** full-profile validate ledger
row(s)"* — a claim nothing in the function establishes. That is the proxy-binding
failure pattern: the label asserts a property the code never checked.

**Measured impact — small today, and honestly so.** On the live 585-row ledger:

| | p90 wall | p90 CPU |
| --- | --- | --- |
| current (file position, unqualified) | 700.0 s | 5907.1 s |
| qualified (event time, `result == "pass"`) | 702.0 s | 5907.1 s |

The estimate is off by ~0.3% on wall and not at all on CPU. But the *population* is
badly mixed: the 50-row window is **23 pass / 23 fail / 4 no_result** raw, and
**19 / 21 / 10** by `effective_result` — **54% of the sample is not a successful run**,
against a basis string that says it is.

The near-zero error is luck, not design. The contaminating rows sit in the **lower tail**
where p90 does not look — truncated median 100 s (min 6 s) and fail min 35 s, against a
pass median of 655 s. The failure mode is the other direction: one red in the window runs
1470 s, above the 860 s pass maximum, so a burst of long reds biases the p90 **upward**.
The estimate feeds `--estimate-wall-seconds`/`--estimate-cpu-seconds` in the remediation
protocol (~`protocol.py:1515`), so a biased estimate becomes a wrong budget.

**Fix (one-line shape, not applied):** sort by `finished_at` and drop non-pass rows
before the p90, exactly as `wall_cpu_ratchet._baseline()` already does. I did not change
it — it is a live consumer in another module's remediation path and this dispatch scoped
me to the guard. It is recorded in `KNOWN_BYPASSES` with the full analysis so the fix
does not require re-deriving it.

---

## State / limitations

- **Files changed:** `ci-hub/validate/tests/test_qualified_rows.py` (additive; the file
  was clean and committed before I touched it) and new
  `ci-hub/validate/tests/test_ledger_reader_allowlist.py`. **No production logic
  changed** — in particular `remediation/protocol.py` is recorded as debt, not edited.
- Full suite after both: **147 passed** (was 134). The planted live-tree violation probe
  was deleted; `git status ci-hub/` shows only my two test paths plus another agent's
  untracked `anchor_select.py`/`test_anchor_select.py`, untouched.
- **Uncommitted.** This dispatch did not authorize a commit, and egress is down, so the
  ci-hub straight-to-main push could not run regardless.
- **No `validate` run** (livelock risk, per directive). All numbers come from reading
  the existing ledger and running unit tests.
- The mutation sweep ran on a throwaway copy under `scratch/qr-mutation/` (ignored); the
  tracked file was never left mutated.
- The 585-row ledger is machine-local (`ignored/`), so the population figures are a
  snapshot of this host at this time, not a shared artifact.

## Reproduction

```bash
cd ~/work/dev-hermit
python3 -m pytest ci-hub/validate/tests/ -q                 # 134 passed
./ci-hub/ci-hub ledger qualified-rows >/dev/null            # 107/585 on stderr

# population comparison
python3 - <<'PY'
import sys; sys.path.insert(0,'ci-hub/validate')
import qualified_rows as qr
rows,_ = qr.load_rows(qr.DEFAULT_LEDGER)
def cov_sat(r):
    c = r.get('coverage')
    return (isinstance(c,dict) and isinstance(c.get('planned_test_nodes'),int)
            and not isinstance(c.get('planned_test_nodes'),bool)
            and c['planned_test_nodes'] > 0
            and c.get('zero_executed_nodes') == [] and c.get('absent_nodes') == [])
print(len(qr.qualified_rows(rows)), sum(1 for r in rows
      if r.get('result')=='pass' and cov_sat(r) and qr.event_time(r)), len(rows))
PY
```
