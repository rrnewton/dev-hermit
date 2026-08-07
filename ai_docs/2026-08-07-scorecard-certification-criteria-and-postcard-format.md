# Scorecard certification criteria and one-time postcard format

**Task:** `certify-post-tightening-scorecard-postcard`

**Status:** criteria only — **no certification issued**

**Date:** 2026-08-07

This document defines what a scorecard cell must prove before it may be shown as
green, and the exact postcard shape for the one-time definition correction. It
does not certify the current scorecard.

## Current authority boundary

The scorecard consumer wiring was demonstrated on dev-hermit draft PR
[#94](https://github.com/rrnewton/dev-hermit/pull/94), exact head
`03264242aca464f24e476a6eb11635d538f18893`. Its two-way fixture discriminated:

- clean control: 2/2 cells evidence-qualified;
- one planted INFO divergence (`bitwise_parity=0`, INFO counts `169|186`):
  1/2 cells evidence-qualified, with exactly the planted cell rejected.

That is useful mechanism evidence, not release authority. At the time of this
document, GitHub reports the PR `draft=true`, `state=CLOSED`, `mergedAt=null`;
live `rrnewton/dev-hermit:main` is
`a0275cb4a854d9a0916121f9415ea3ea4505872e`. The validation ledger also cannot
yet issue a dev-hermit exact-head receipt. A certification must therefore wait
for landed consumer wiring and a nonzero exact-head receipt from an authority
that accepts this repository.

## Population discipline

The canonical historical census is bound to parent
`49ba1ddc1b6192a252194aab7323f39213d9857b`:

- 2,290 total rows;
- 1,843 raw passes;
- 6 cells qualified by the old tier-label definition;
- 0/6 independently evidence-qualified.

The later 2,312-row scan with 0 comparison verdicts is a different working-tree
population. It is neither an update to the 2,290-row census nor a valid second
column in an old-versus-new table. The 2-cell PR fixture is also a test fixture,
not a substitute scorecard denominator.

Old and new definitions may be compared only when all of these match:

- `population_id`;
- total row count;
- the sorted logical cell-key set and its hash;
- source artifact content SHA;
- corpus/profile and observable selection.

The logical cell key is
`(population_id, bucket, test_id, test_mode, backend)`. Repeated measurements
retain their `run_id` and source-row identity as provenance; they do not silently
replace or duplicate a logical cell. If either side has a different key set, the
postcard must print `NOT COMPARABLE` and omit a delta.

## Certifiable-cell predicate

A cell is certifiable only when every condition below holds.

1. **Exact identity.** The row records a unique logical cell key, source file and
   row, run ID, UTC time, exact parent artifact/content commit, clean 40-hex
   Hermit and Reverie SHAs, producer version, comparator policy and version,
   tier-verifier version, exact command, and a dereferenceable receipt. Blank,
   dirty, mutable, or unresolved identity yields `NO_RESULT`.

2. **Real execution.** `selected_count`, `executed_count`, and `evidence_count`
   are explicit and nonzero; declared coverage is satisfied; the raw outcome is
   `pass`; guest exit status was captured and compared. A usage exit, timeout,
   filtered-only run, hollow completion, or zero-comparison result cannot pass.

3. **Per-cell tier.** `comparison_tier` uses the recognized vocabulary and the
   row carries a separately computed `tier_evidence_state`, verifier SHA,
   evidence receipt, and refusal reasons. A declared tier is a claim to verify,
   never evidence by itself.

4. **Every named channel is proved.** A channel needs a typed A/B verdict and a
   receipt, not merely a nonblank digest or count. `stack_hash` or `heap_hash`
   alone does not establish stack or heap parity. Zero INFO records compared is
   no evidence.

5. **Declared comparison policy.** Every normalization, canonicalization, or
   stripping rule is named and versioned. Data outside that policy is compared
   byte-for-byte. No consumer may infer strictness from `verified=true`,
   `outcome=pass`, a tier string, a digest, or a nonblank count.

6. **One semantic authority.** The renderer, gate, summary generator, and every
   downstream consumer call the same tier-evidence verifier for the exact
   population. They cross-check rows, claims, upheld claims, and cell identities.
   Missing or malformed verifier output, population disagreement, or a nonempty
   population with zero comparison verdicts fails closed.

7. **Two-sided falsifiability.** A clean control remains green and each
   applicable plant rejects the intended cell without changing unrelated cells.
   Cell-bound receipts enumerate stdout, exit, DETLOG-only, address/path
   identity, register, stack, and heap plants. A class outside the tier contract
   is `NOT_COVERED`, never silently passed. Aggregate `7/7` is insufficient
   without all seven rows and denominators.

8. **Correctness is not inferred from parity.** Record whether an absolute
   oracle ran, its identity/version, and its verdict. Without a passing oracle,
   the strongest reader-visible claim is `PARITY_ONLY`, not correctness.

9. **Reader-visible authoritative state.** The rendered state is one of the
   states in the legend below. A label-only row never shares the word “green”, a
   checkmark, or the styling used for evidence-qualified green.

## Tier contracts

| Declared tier | Every-run evidence | Cadenced evidence | Green condition |
| --- | --- | --- | --- |
| `full-stdout-info-stack-heap` | stdout bytes and exit status; typed INFO verdict with nonzero left/right counts; stack A/B verdict; heap A/B verdict | none | every named channel passes at the exact cell identity |
| `stdout-info-stack-heap-spot-check` | stdout bytes and exit status; typed INFO verdict with nonzero left/right counts | current passing stack and heap receipts | every-run channels pass and receipts match exact Hermit, Reverie, comparator, verifier, tier, and cell identity; source/comparator change or expired cadence invalidates them |
| `legacy-unqualified`, blank, unknown, or malformed | none sufficient | none sufficient | never green |

DETLOG, address/path, and register observations must state which named channel
and comparator policy owns them. If a tier claims those observations through
its INFO contract, the matching mutation receipt is required.

## Required per-cell record

The durable row or joined receipt must expose, without reverse engineering:

- logical cell key, source row, `run_id`, raw outcome, and captured exit;
- `comparison_tier` (declared) and `tier_evidence_state` (verified);
- verifier SHA/version, receipt URL/path, and exact refusal reasons;
- stdout and exit verdicts plus receipts;
- INFO verdict, comparator policy/version, and left/right record counts;
- stack verdict plus A/B digests and receipt;
- heap verdict plus A/B digests and receipt;
- DETLOG/address/path/register verdicts when claimed by the tier;
- oracle exercised yes/no, oracle ID/version, and verdict;
- exact parent, Hermit, Reverie, producer, comparator, and verifier SHAs.

## One-time postcard

### Identity header

Print the artifact URL/path and content commit; generation UTC; old and new
definition names/versions; `population_id`; row count; sorted cell-key-set hash;
parent, Hermit, and Reverie provenance (or explicit per-row binding); producer,
comparator, and verifier SHAs; corpus/profile/observable; and oracle policy.

### Comparability guard

Before any totals, print one of:

- `COMPARABLE: identical population_id, row count, and cell-key-set hash`; or
- `NOT COMPARABLE: <exact mismatch>` and no before/after delta.

### Side-by-side definition correction

Both columns are computed from the same immutable rows. State both useful
denominators: raw-pass candidates and total population rows.

| Definition | Qualified / raw passes | Qualified / total rows | Meaning |
| --- | ---: | ---: | --- |
| OLD — label-qualified | `old_label_green / raw_passes` | `old_label_green / rows` | tier label accepted without dereferencing every component |
| NEW — evidence-qualified | `new_evidenced_green / raw_passes` | `new_evidenced_green / rows` | shared verifier upheld every channel named by the tier |

For the exact historical `49ba1ddc` census, the input values are OLD
`6/1843` (`6/2290`) and NEW `0/1843` (`0/2290`). This is a **DEFINITION
CORRECTION**, not a product regression and not a ratchet increase. It is not a
certification of a later scorecard. Ratcheting begins only after a landed,
exact-head new-definition baseline is published.

### State summary

With the same total denominator, report:

- total rows and executed rows;
- raw passes;
- tier declarations;
- old label-qualified cells;
- new evidence-qualified cells;
- declared-only non-green cells, grouped by refusal reason;
- divergences;
- no-results, grouped by reason;
- legacy/unqualified cells.

### Tier summary

For each tier, show cost class, every-run channels, cadence channels, claims,
upheld claims, refused claims by reason, and mutation classes exercised and
passed. Claims and upheld claims must never be collapsed into one count.

### Per-cell table

| Cell key | Raw outcome / exit | Old state | Declared tier | New authoritative state | stdout + exit | INFO verdict and L\|R | stack | heap | oracle | comparator | refusal / receipt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use text and symbols, not color alone. Every refusal links to its source row or
receipt.

### Mutation bracket

| Mutation class | Tier/channel claimed | Clean control green | Mutant rejected | Intended cells changed | Exact receipt and artifact SHA |
| --- | --- | ---: | ---: | ---: | --- |
| stdout | stdout | `N/N` | `N/N` | `N/N` | link |
| exit | exit | `N/N` | `N/N` | `N/N` | link |
| DETLOG-only | INFO/DETLOG | `N/N` | `N/N` | `N/N` | link |
| address/path identity | INFO identity policy | `N/N` | `N/N` | `N/N` | link |
| register | declared register channel | `N/N` | `N/N` | `N/N` | link |
| stack | stack | `N/N` | `N/N` | `N/N` | link |
| heap | heap | `N/N` | `N/N` | `N/N` | link |

The PR #94 INFO fixture may be cited in the INFO row as pre-landing design
evidence, but it cannot fill the final receipt column until the verifier is
landed and the exact-head authority accepts the run.

### Legend

- `✓ EVIDENCED_GREEN` — dereferenced proof for every channel named by the tier.
- `◇ DECLARED_ONLY_NON_GREEN` — a tier label exists, but proof is absent,
  malformed, stale, or refused. **Not green.**
- `✗ DIVERGED_RED` — a completed comparison found a mismatch.
- `? NO_RESULT` — execution or evidence did not establish a verdict.
- `~ LEGACY_UNQUALIFIED` — historical measurement under a weaker definition.
- `PARITY_ONLY` — repeatability/parity established; correctness not established.

## Preconditions for an actual certification

An independent reviewer may issue a certification only after all are true:

1. tier-evidence consumer wiring is landed on `rrnewton/dev-hermit:main` and the
   reviewed SHA is freshly resolved;
2. the validation authority accepts dev-hermit and provides a nonzero exact-head
   receipt for that landed SHA;
3. old and new columns are generated from one immutable population/key set;
4. every claimed cell has the per-cell record above, with no dirty or mutable
   provenance;
5. all consumers invoke the one semantic verifier and fail closed on zero
   verdicts or population disagreement;
6. the seven-row mutation bracket passes both directions at the final artifact
   SHA, with exact denominators;
7. the postcard and machine-readable source are version controlled and
   independently reproducible.

Until then, the correct verdict is **NOT CERTIFIED**.
