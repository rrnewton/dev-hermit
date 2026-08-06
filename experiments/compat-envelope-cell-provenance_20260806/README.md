# compat-envelope cell provenance

## Question

The compat-envelope scorecards report per-backend `parity%` and `determinism%`. Those
percentages are only auditable if each cell records **what produced it**. This asks, for
every published cell: is it a CI-enforced measurement, a probe of a cell CI does not run,
or an expansion candidate — and does the row carry the evidence its claim requires?

## Method

Classify each row of the four published scorecards using only fields the row itself
carries (`cell_state`, `output_hash`, `ref_output_hash`, `reverie_sha`). `cell_state`
semantics come from `compat-envelope/collect-envelope.rs:405-432`: cells from `plan` are
`enabled` (the CI plan); cells from `audit-gaps` are `disabled` — **not in the CI plan**,
executed only during an expansion run to see whether they would pass.

Reproduce: `python3 classify.py > results.csv 2> summary.txt`

## Results — 2284 cells across 4 scorecards

| provenance class | cells | share |
|---|---:|---:|
| `expansion-candidate` | 848 | 37.1% |
| `gap-probe-not-in-ci` | 776 | 34.0% |
| `ci-enforced-measured` | 522 | 22.9% |
| `ci-enforced-unhashed` | 138 | 6.0% |

Of the **1317 cells reporting `parity=1`**, only **268 (20.3%)** are both CI-enforced and
carry an output hash. 351 (26.7%) are cells CI does not run; 126 (9.6%) record no output
hash at all.

Two evidence fields are missing wholesale:

- **`ref_output_hash`: 0 of 2284.** Absent from the published *schema*, not merely blank.
  Every published scorecard is 19–20 columns; `collect-envelope.rs:80` now emits 22
  including `ref_output_hash` and `run_flags`. The collector comment at line 492 states
  the defect exactly — *"The reference hash was always computed here and then DISCARDED,
  which left a `parity=1` row unable to say what it matched … Both hashes are now
  recorded."* **The fix is in the code and has never been applied to the artifact.** So no
  published `parity=1` row names both sides of its comparison.
- **`reverie_sha`: unknown for 178 of 2284**, and those 178 are *exactly* the
  `run_mode=regression` rows of `scorecard.csv` (perfect 178/178 correlation). Parity is a
  cross-backend claim and the backends live in Reverie, so those cells cannot say which
  Reverie they tested.

`scorecard.csv` also aggregates **7 distinct `hermit_sha`s** into one table.

## Interpretation

The percentages are not wrong arithmetic; they are **under-labelled**. `render-scorecard.rs`
does not split by `cell_state`, so a headline number sums CI-enforced cells together with
gap probes CI never runs. A reader takes the result as protected capability; 60.7% of it
is not enforced by anything. This is the same class as the already-recorded finding that
backend-parity `c` cells are CI-false — here it is quantified across the whole envelope.

Nothing here says a backend is worse than reported. It says the published artifact cannot
support the claim its labels make, and one fix for that is already written and unshipped.

## Recommended, in dependency order

1. **Regenerate the scorecards with the current collector** — recovers `ref_output_hash`
   for free and turns every `parity=1` into a two-sided, re-checkable claim.
2. **Split headline `parity%`/`determinism%` by `cell_state`** in `render-scorecard.rs`:
   report CI-enforced and gap-probe as separate columns; never sum them.
3. **Fail the regression lane when `reverie_sha` is `unknown`** — a cross-backend parity
   claim that cannot name its Reverie is not evidence.
4. **Subtotal by `hermit_sha`**, or pin one, rather than aggregating 7 into a single row.

## Limitations

Classification uses only what the CSVs record; it cannot recover provenance for a cell
whose producing run was never written down, and it does not re-run anything. It says
nothing about parity *depth* — that separate gap (parity compares piped stdout only) is
recorded elsewhere and is not re-derived here.
