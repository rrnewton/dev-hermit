# 105 of 168 passing cells claim `deterministic=1` from a single run that never compared anything

**Task:** `strict-verify-holes-audit` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Answer

**105 of 168 enabled+passing cells (62.5%) carry `deterministic=1` without ever having run a two-run
comparison.** The collector sets the column from `pass` alone, with no mode check — and its own module
doc names the restriction the code omits.

There is a second layer underneath: of the 63 cells that *did* run `--verify`, **none** used the
bitwise comparator. So the envelope currently contains **zero** bitwise-verified determinism claims.

## The mechanism, at source

`compat-envelope/collect-envelope.rs:260-265`:

```rust
let deterministic = if !available || outcome == "skip" { None }
                    else if pass { Some(true) }
                    else { Some(false) };
```

**No mode check.** Any passing cell gets `deterministic=1`.

The module doc (lines 22-25) states the rationale, and it is correct *only under a condition the code
does not enforce* (emphasis mine):

> "determinism = the cell passed under its own backend (**for `verify`**, hermit's internal
> `--strict --verify` double-run already proved run1==run2, so a passing verify cell is deterministic
> by construction)"

For a `verify` cell that reasoning holds. For a `strict` cell there is no double-run at all — the
collector's own `run_and_hash` builds `hermit run --backend <b> --strict` with **no `--verify`**
(`collect-envelope.rs:581`). One run, no comparison, `deterministic=1`.

This is a proxy substitution: **"the cell passed" is standing in for "the cell is deterministic"**, and
it is a valid proxy in exactly one of the five modes present.

## The tiering

| backend | passing | ran `verify` | earned | **unearned** |
| --- | ---: | ---: | ---: | ---: |
| dbi | 86 | 8 | 9.3% | **78** |
| ptrace | 79 | 52 | 65.8% | **27** |
| kvm | 3 | 3 | **100.0%** | 0 |
| **TOTAL** | **168** | **63** | **37.5%** | **105** |

Unearned cells by mode: `strict` **102**, `chaos` 1, `custom` 1, `replay` 1.

Two things worth naming:

* **DBI's determinism column is ~91% unearned** (78 of 86). It is also the backend with the most
  enabled cells, so it dominates any aggregate determinism figure.
* **KVM is the only backend whose column is fully earned** — and only because it has 3 passing cells.
  The best-quality claim in the envelope belongs to the least-covered backend.

Full list of the 105 in `tiering.txt`.

## Second layer: even the 63 earned cells are not bitwise

`hermit-cli/src/bin/hermit/run.rs:2783-2787` (and again at `:3117-3121`):

```rust
strictness: if self.verify_verbose || self.verify_strict {
    LogCompareStrictness::Canonical
} else {
    LogCompareStrictness::Stripped
},
```

A plain `--verify` compares under **`Stripped`**; `Canonical` (bitwise) requires `--verify-strict` or
`--verify-verbose`. The collector passes neither. So:

| tier | count | what the claim actually rests on |
| --- | ---: | --- |
| bitwise-verified | **0** | nothing in this path passes `--verify-strict` |
| stripped double-run | **63** | run1 == run2 under the lossy comparator |
| single passing run | **105** | nothing was compared |

That is the precise form of the task's premise (*"log-identical != execution-deterministic"*): even the
"log-identical" tier is **stripped**-identical, which is weaker again.

## Recommendation: one change, not 105 tasks

The task says *"file each; require they run `--verify` or be reclassified."* Filing 105 tasks would be
noise — all 105 share one cause and one fix:

1. **Make the collector honest (3 lines).** Set `deterministic = None` (blank = unmeasured) unless
   `test_mode == "verify"`. Blank already has a defined meaning in this CSV — the collector uses it for
   unavailable/skipped cells — so this reuses existing semantics rather than inventing a state. Effect:
   105 cells move from a false `1` to an honest blank, and the determinism figure drops from 168/168 to
   63/63-measured. **The number gets smaller and true.**
2. **Then** decide per backend whether to spend runs promoting `strict` cells to `verify`. That is a
   real cost — a verify cell is two runs — so it should be a deliberate, prioritised choice, not a
   silent default. DBI is where the 78 are.
3. **Record the comparator in the row.** A `verify` cell should say whether it was `Stripped` or
   `Canonical`. Today the CSV cannot express the difference, so tier-2 and a future tier-1 are
   indistinguishable in the artifact — the same "the value must carry its condition" problem the
   receipt work fixed elsewhere.
4. **Do not report an aggregate determinism%** until (1) lands. Today's aggregate is 100% of passing
   cells by construction, which is why it has never looked alarming.

## Scope and limits

* This is an audit of the **recorded scorecard**, not a re-run. I did not re-execute any cell.
* The 105 count is for `scorecard.csv` (618 rows, written 2026-08-05T00:24). `fullcorpus-scorecard.csv`
  has only 14 enabled cells and is not the canonical artifact.
* I have **not** shown that any of the 105 is actually non-deterministic. The finding is that **the
  claim is unearned**, not that it is false. Some — probably most — would pass a verify run. The defect
  is that the artifact asserts something it did not measure.
* Related and already reported separately: the `parity` column is stdout-hash-only, and DBI's DETLOG
  cannot be collected via `--log-file` (`experiments/compat_scorecard_depth_20260806`).

## Reproduction

```bash
python3 - <<'PY'
import csv, collections
rows=[r for r in csv.DictReader(open('compat-envelope/scorecard.csv'))
      if r['cell_state']=='enabled' and r['outcome']=='pass']
print(len(rows), 'passing;',
      sum(1 for r in rows if r['test_mode']!='verify'), 'never ran verify;',
      sum(1 for r in rows if r['deterministic']=='1'), "claim deterministic=1")
PY
# -> 168 passing; 105 never ran verify; 168 claim deterministic=1
```
