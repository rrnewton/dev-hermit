# The tier gate checked the label, not the claim: qualified green 6 → 0

**Task:** `record-the-strictness-tier-per-cell-so-a-green-states-its-standard`
**Date:** 2026-08-07 · **Base:** `rrnewton/dev-hermit` `origin/main` @ `57df943`

## Headline

`comparison_tier` was a **self-declared label with nothing binding it to the comparison performed.**
The gate validated the tier's *spelling* and promoted any raw pass carrying a qualifying spelling to
"qualified green". It never read an evidence column.

**Definition corrections** (old → new; evidence unchanged, only the rule that reads it):

| figure | old | new |
| --- | ---: | ---: |
| qualified green across all 4 scorecards | **6 / 1843** raw passes | **0 / 1843** |
| FULL claims upheld | 6 / 6 | **0 / 6** |
| spot-check cells that could qualify | 3 / 41 | **0 / 41** |

Neither is a regression. The six FULL claims never carried stdout evidence and the three CURRENT
spot-check receipts were never bound to an identifiable tree.

## The planted cell was already in production

The task asked me to plant a cell claiming FULL while missing stdout and confirm rejection. I did — and
found **six real rows of exactly that shape live in `scorecard.csv`**, all scored qualified green:

```
scorecard.csv:620-625  ptrace/ptrace-short-full-tier/{heapy, name_to_handle_at_eopnotsupp,
  name_to_handle_directory_eopnotsupp, name_to_handle_empty_path_eopnotsupp,
  name_to_handle_regular_eopnotsupp, print_memaddrs}
    claims full-stdout-info-stack-heap
    but  missing:stdout (stdout_parity is blank)
         schema-cannot-express:stack (no 'stack_parity' column)
         schema-cannot-express:heap  (no 'heap_parity' column)
```

Reproduction of the old behaviour, before any change — four planted rows, one comparing *nothing*:

```
check-scorecard-tier.py --root <probe>
  tier_distribution={'full-stdout-info-stack-heap': 4}  qualified_green=4/4 raw_passes   rc=0
```

## Three premises corrected

**1. The named test does not exist.** `test_tier_claim_carries_its_evidence` appears nowhere in the tree.
The real gate is `compat-envelope/check-scorecard-tier.py`.

**2. It does not "verify only stack+heap" — it verifies neither.** Lines 108-115 compare the tier string
against `KNOWN`, then count a pass as qualified iff the string is in `QUALIFYING`. No evidence column is
read anywhere. Across the entire tree, `comparison_tier` appears in exactly **one** code location: a
**docstring** in `spot-check-cadence.py`. Nothing consumed it.

**3. Half of FULL is unverifiable by construction.** The 33-column core schema carries `stdout_parity`
and `compared_log_messages` but **no stack or heap column**. FULL claims "stdout + INFO + stack + heap,
every run"; the schema can express two of the four. Stack/heap evidence exists only in
`spot-check-ledger.csv`, which is per-large-cell and *periodic* — structurally unable to support an
"every run" claim. So a FULL claim on a real scorecard is refused as `schema-cannot-express`, not passed.
Widening the schema is the fix; nothing is back-filled, because inventing the evidence is the defect.

## The cadence was documented, not enforced

`spot-check-cadence.py` computes CURRENT/STALE/NEVER correctly, but `main()` returns 0 unconditionally —
nothing ever refused a stale cell. `CADENCE_TRIGGERS` is a tuple of three prose strings that is printed
and never evaluated.

Ledger state, with denominators: **41 rows — 38 `hermit_sha` blank, 3 = `gf89c69766371-dirty`.**
Live: CURRENT 3 / STALE 0 / NEVER 38. The three CURRENT rows *are* the three dirty ones, and each records
`detail="stack+heap --verify"` — so even the recorded spot-check evidence covers only stack and heap,
never stdout or INFO. Enforced against the real ledger, all three are refused:

```
tier_evidence.py --ledger ./spot-check-ledger.csv
  ptrace/c-programs/{prodcons-determinism, sigmask-preemption, sysinfo-uptime}
    claims stdout-info-stack-heap-spot-check
    but dirty-receipt (hermit_sha='gf89c69766371-dirty' does not identify a tree)
```

**Dirtiness is tested before age, deliberately.** A `-dirty` receipt is not *stale* evidence; it is
evidence about no identifiable tree. Reporting it as STALE would imply it had once been valid.

## What was added

`compat-envelope/tier_evidence.py` — the missing half, deliberately separate from the vocabulary gate
(the same split `check_cell_comparison.py` documents against the header checker: neither subsumes the
other). It introduces no third vocabulary: tier names come from `check-scorecard-tier.py`, components
from `strict_verdict.STRICT_COMPONENTS`, cadence from `spot-check-cadence.age_state`.

| tier | stdout | INFO | stack | heap |
| --- | --- | --- | --- | --- |
| `full-stdout-info-stack-heap` | every run | every run | every run | every run |
| `stdout-info-stack-heap-spot-check` | every run | every run | cadenced | cadenced |

Refusal classes are kept distinct because they need different fixes: `missing:` (producer did not
measure), `schema-cannot-express:` (no column exists), `empty-comparison:` (`0|0` compared nothing),
`dirty-receipt`, `cadence:STALE`, `cadence:NEVER`. A non-qualifying tier such as `legacy-unqualified`
asserts nothing and is counted as neither upheld nor violated.

## Bracket, both directions

`test_tier_evidence.py` — **18 tests, all passing.** Every refusal is paired with a row differing only in
the field under test that must be upheld; a checker that refused everything would satisfy every negative
and destroy the tier.

| direction | cases |
| --- | --- |
| **positive (must be upheld)** | complete FULL claim; spot-check with a current clean receipt; `legacy-unqualified` not counted as a claim |
| **negative (must be refused)** | FULL missing stdout · missing INFO · INFO `0|0` · all four missing · missing *column* vs blank *value* · dirty receipt · blank SHA · STALE · NEVER · old-and-dirty reported dirty not stale · spot-check still requiring stdout+INFO every run · empty population REFUSED not passed |

**Anti-vacuity — 6 planted mutations, 6 detected, baseline green:**

| mutation | result |
| --- | --- |
| FULL no longer claims stdout | DETECTED (3 failing) |
| `is_dirty_sha` always False | DETECTED (4 failing) |
| STALE accepted as CURRENT | DETECTED (1 failing) |
| blank evidence value accepted | DETECTED (5 failing) |
| `0\|0` accepted as a real comparison | DETECTED (1 failing) |
| missing column no longer a violation | DETECTED (1 failing) |

## Verify clause

| clause | status |
| --- | --- |
| every green cell carries an explicit tier | **already held** — `check-scorecard-tier.py` refuses a blank; live 2290/2290 rows carry a known tier, 0 blank. Not my work. |
| the tier check verifies ALL components the tier claims | **now does** — it previously verified none |
| the spot cadence is ENFORCED IN CODE | **now is** — STALE and NEVER are violations, not report lines |
| a receipt bound to a DIRTY SHA is REFUSED | **now is**, ahead of the age test |
| plant a cell claiming FULL while missing stdout → REJECTED | **rejected**, and six such rows were already live |

## Not done

- **Widening the schema** with `stack_parity`/`heap_parity`. Until then every FULL claim is refused as
  `schema-cannot-express`. The columns are read by name so widening is the only change needed.
- **Wiring `tier_evidence.py` into a gate.** It is a standalone checker; nothing calls it yet, so it
  does not yet block a publish. An unwired check is a check that does not run.
- **Re-tiering the six cells.** They must drop to a lower tier or NO-RESULT; that is a producer change.
