# Executed-count backfill onto already-published artifacts

**Task:** `backfill-executed-counts-onto-published-artifacts` · hermit-w11
(`[impl agent, opus-5]`) · **2026-08-07** · read-only against published artifacts;
the backfill is computed and verified but **not applied** (see §5).

All figures below are read from **`origin/main`**, not the working tree — the
working copy of `compat-envelope/scorecard.csv` grew from 618 to 646 rows while
this audit ran, so only the committed state is quotable.

---

## 0. Headline, with the half that was already done

| | count |
| --- | ---: |
| published measurement artifacts audited | **9** |
| CSV runs whose counts are **derivable and computed** | **18 runs / 2284 rows / 6852 cells** |
| CSV runs that had to be marked **UNQUALIFIED** | **0** |
| artifacts that **cannot** be backfilled without a schema change | **1** (1025 rows) |
| rows already carrying the **UNQUALIFIED** marker before I started | **1671** |

**The UNQUALIFIED half of this task was already done by someone else**, and
thoroughly. `legacy_parity_unqualified` is populated on **1671 of 3309**
published rows — exactly the number of parity figures in those files. The old
values were not deleted: they are preserved as `stdout_parity:0` / `:1`, the
qualified `stdout_parity` field is left empty (0 populated), `parity_comparator`
is blank, and `comparison_tier` reads `legacy-unqualified` on every row. That is
a correct demotion, not a relabel.

**What was not done is the executed count.** `executed_count` exists as a column
in 4 of the 5 published scorecards and is populated on **0 of 3309 rows**. The
column is present and inert — worse than absent, because a reader sees the field
and infers it was considered.

---

## 1. Semantics, taken from the collector rather than invented

From `collect-envelope.rs`, which computes these *after every row is known*:

```rust
let executed_count = pending_rows.iter().filter(|(_, executed, _)| *executed).count();
let evidence_count = pending_rows.iter().filter(|(_, _, evidence)| *evidence).count();
```

So `selected_count` / `executed_count` / `evidence_count` are **run-level**
values denormalised onto every row of that run. The backfill therefore groups by
`run_id` and never derives a per-row count. Executed means *ran*, not *passed* —
a `diverge` or `timeout` cell executed.

---

## 2. The backfill, per run (dry run against `origin/main`)

| artifact / run | selected | executed | evidence |
| --- | ---: | ---: | ---: |
| `e9patch-scorecard.csv` / `e9patch-20260801` | 454 | 454 | 0 |
| `fullcorpus-scorecard.csv` / `dbi-fullcorpus-scorecard` | 200 | 200 | 0 |
| `fullcorpus-scorecard.csv` / `e9patch-fullcorpus-scorecard` | 200 | 200 | 0 |
| `fullcorpus-scorecard.csv` / `kvm-fullcorpus-scorecard` | 200 | 200 | 0 |
| `fullcorpus-scorecard.csv` / `liteinst-fullcorpus-scorecard` | 200 | 200 | 0 |
| `fullcorpus-scorecard.csv` / `ptrace-fullcorpus-scorecard` | 200 | 200 | 0 |
| `fullcorpus-scorecard.csv` / `sabre-fullcorpus-scorecard` | 200 | 200 | 0 |
| `reverie-scorecard.csv` / `reverie-20260801` | 12 | 12 | 0 |
| `scorecard.csv` / `backend-parity-09d7bd0c…-1561902` | 24 | 24 | 0 |
| `scorecard.csv` / `backend-parity-09d7bd0c…-1586797` | 24 | 24 | 0 |
| `scorecard.csv` / `backend-parity-52d56e5c…-972152` | 28 | 28 | 0 |
| `scorecard.csv` / `backend-parity-75edd745…-3802619` | 28 | 28 | 0 |
| `scorecard.csv` / `backend-parity-fc49593a…-639593` | 28 | 28 | 0 |
| **`scorecard.csv` / `canonical-release-ptrace-dbi`** | **46** | **39** | 0 |
| `scorecard.csv` / `kvm-fullcorpus-scorecard` | 200 | 200 | 0 |
| `scorecard.csv` / `liteinst-fullcorpus-1785621912` | 200 | 200 | 0 |
| `scorecard.csv` / `liteinst-spst-1785620995` | 40 | 40 | 0 |

**17 of 18 runs executed everything they selected.** The one that did not is
`canonical-release-ptrace-dbi`: **46 selected, 39 executed, 7 unavailable**. That
gap is invisible in the published file today and is exactly what the field is
for.

**`evidence_count = 0` everywhere is correct, not a bug.** All parity evidence in
these files was demoted to `legacy_parity_unqualified`, so the count of
*qualified* evidence is genuinely zero — and writing `0` rather than leaving
blank is the point: zero-qualified-evidence and nobody-looked must not render
alike.

---

## 3. The one artifact that cannot be backfilled

`compat-envelope/pre-tightening-baseline-20260806/scorecard.csv` — **1025 rows**
— has **no `selected_count` / `executed_count` / `evidence_count` columns at
all**. The tool reports `NO-COLUMN` and changes nothing; inventing schema on a
frozen baseline would be worse than the gap.

It is, however, the **best-qualified artifact in the set** by every other
measure: its README states 235 nominal / 205 effective / 1025 rows, carries a
five-class `no-results.csv`, a producer sha256, and an independent replication.
Its counts are stated in prose beside it rather than in the row. Recommended
disposition: **leave the CSV frozen and mark the directory README** as carrying
its counts externally — not `UNQUALIFIED`, because the counts exist.

---

## 4. Markdown artifacts

| artifact | executed count | marker |
| --- | --- | --- |
| `compat-envelope/SCORECARD.md` | absent | HISTORICAL / NOT-row-comparable present |
| `compat-envelope/REPORT.md` | absent | HISTORICAL / NOT-row-comparable present |
| `ai_docs/measurements/headline-numbers-restated.md` | present | — |
| `ai_docs/measurements/prefix-parity-depth-ratchet_20260806.md` | derivable, unstated | — |

The ratchet artifact — the precedent this task cites — is well provenanced
(per-cell `Y/Z` denominators, a `-dirty` caveat, a `binary_sha256`, and a
re-identification note). Its residue is narrower than "countless": the table
holds **16 cells (4 guests × 4 backends)** while the backend set is 6, so
**8 cells — kvm and liteinst across all four guests — are absent without being
declared absent**. Backfill is `executed=16, planned=24`, not an UNQUALIFIED
stamp.

`ai_docs/measurements/measurements-index.csv` (11 rows) has `denominator` but no
executed-count column. Most of its rows are not cell sweeps (a commit count, a
host PMU count, a single exit status), so the field is not universally
applicable — but rows 2 and 3 (`scorecard_deterministic_cells = 346`,
`scorecard_bitwise_cells = 0`) are, and both quote a scorecard whose executed
count is blank.

---

## 5. Why it is computed but not applied

The backfill is a dry run. Two reasons, both external to the work:

1. **The files are owned and dirty.** `compat-envelope/` carries uncommitted
   changes from the schema workstream, including `scorecard.csv` itself, which
   moved 618 → 646 rows during this audit. Writing into a file another agent is
   actively producing would race their writer.
2. **Committing was not authorised** by the dispatch, and a backfill that is not
   committed is not published.

`compat-envelope/backfill_executed_counts.py --apply <csvs>` performs it. It
never overwrites an existing non-blank count, writes `UNQUALIFIED` rather than
blank where a run is not derivable, refuses an empty scorecard, and does not
invent absent columns. 12 tests; 6 planted mutations, all detected.

## Reproduction

```bash
git show origin/main:compat-envelope/scorecard.csv > /tmp/scorecard.csv   # and the others
compat-envelope/backfill_executed_counts.py /tmp/*.csv        # dry run, prints the table above
```
