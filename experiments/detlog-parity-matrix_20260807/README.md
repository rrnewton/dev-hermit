# The cross-backend DETLOG parity matrix — 42 cells, and only 20 of them are parity results

**Task:** `assemble-the-detlog-parity-matrix-across-measured-backends` · hermit-w7
(`[impl agent, opus-5]`) · **2026-08-07** · local, no egress.
**One binary for every cell:** hermit `0041130ccb0d` (an **ancestor of** `origin/main`, not the
tip — main was `e808322385d4`), release, not `-dirty`, sha256 `cc623684…`, Reverie pin
`0ae0c01b`, third-party backends staged from `target/install_pkg/rsrcs`.

## 0. Why this is a re-measurement and not an aggregation

The task supplies per-backend numbers taken at hermit `590fcc9e` by another agent. Pasting
those into one table would produce rows that are not comparable to each other — different
binary, different guests, different run counts per cell — and a matrix whose rows are not
comparable is a picture, not a measurement. **Every cell below is one binary, one corpus, one
run count.** The prior numbers appear in §6 as corroboration, with the difference stated.

## 1. The matrix

7 guests × 6 backends = **42 cells**, 3 runs each = 126 runs, **0 with a nonzero exit and 0
with zero DETLOG records**. Golden is the ptrace run of the same guest at the same binary.
`Z` = golden record count (the denominator for every row of that guest); `E` = candidate's.
Coverage is order-preserving (longest common subsequence) under the `hex` policy.

| guest | Z | ptrace | kvm | dbi | sabre | e9patch | liteinst |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notsc` | 96 | byte-identical | diverges 87.5 % | **self-nondet** | diverges 8.3 % | **not-exercised** | diverges 49.0 % |
| `detlog_syscalls` | 336 | byte-identical | diverges 86.9 % | **self-nondet** | diverges 32.7 % | **not-exercised** | **self-nondet** |
| `heap_fragment_reuse` | 98 | byte-identical | diverges 87.8 % | **self-nondet** | diverges 19.4 % | **not-exercised** | diverges 52.0 % |
| `stack_deep_recursion` | 80 | byte-identical | diverges 85.0 % | **self-nondet** | diverges 10.0 % | **not-exercised** | diverges 48.8 % |
| `stdout_bytes` | 82 | byte-identical | diverges 85.4 % | **self-nondet** | diverges 11.0 % | **not-exercised** | diverges 48.8 % |
| `bin_true` | 68 | byte-identical | diverges 80.9 % | **self-nondet** | diverges 7.4 % | **not-exercised** | diverges 52.9 % |
| `bin_echo` | 84 | byte-identical | diverges 82.1 % | **self-nondet** | diverges 14.3 % | **not-exercised** | diverges 52.4 % |

**Counts sum to the population:**

| tier | cells | |
| --- | --- | --- |
| `byte-identical` | 7 | ptrace against itself — a self-reference row, not an achievement |
| `diverges` | 20 | the only cells that are actual parity results |
| `self-nondeterministic` | 8 | parity **withheld**, not printed low |
| `not-exercised` | 7 | backend ran, transformed nothing |
| **total** | **42** | = 7 × 6 |

Per-cell self-determinism, corpus provenance, engagement witness, both policies' prefix depth
and coverage, and the reason for every non-result are in `detlog-matrix.csv`. Every attempted
run with its exit code and record count is in `attempts.tsv`.

**So of 42 cells, 20 carry a parity number.** The other 22 are ptrace-against-itself (7),
withheld for a failed baseline (8), or inert (7). A matrix that omitted the 15 non-results
would have reported "20 of 20 scored" — which is why they are rows.

## 2. e9patch is not merely INHERITED here — it is INERT

The task said to label e9patch's cleanliness as inherited from the attached ptracer rather than
earned. That is right, and the CLI says so in its own help text: *"e9patch: Preprocess the main
ELF with e9patch, then use the ptrace runtime."* `hermit/AGENTS.md` is explicit that e9patch is
not a backend.

But on **this corpus it is stronger than inherited**. e9patch's own engagement banner, on all
seven guests:

```
:: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0;
   b0_sites=0; instruction_map_cache=Hit; rewrite_cache=not-applicable; artifact_sha256=none
```

**Zero candidate sites, zero mapped sites, no artifact produced, on every cell.** e9patch's
DETLOG is byte-identical to ptrace's because it *is* ptrace's — nothing was rewritten. Scoring
those 7 cells `byte-identical` would have published a perfect column for a component that did
nothing, which is the ambiguous-zero this repo has already been burned by twice (SaBRe's
`patched_sites=0` silent ptrace fallback is the canonical case). They are `not-exercised`.

To make an e9patch cell mean anything, the corpus needs guests containing the mnemonics
e9patch actually rewrites. Every guest here has none.

The other three engagement witnesses were checked the same way and all show real engagement:
SaBRe `guest_rpc_observed=true` with `ptrace_fallback_sites=0`; DBI `Detcore Tool active;
running … under DynamoRIO`; KVM `launching guest through reverie-kvm`.

## 3. What each divergence actually is

### KVM — closest to the golden, and its residual is almost all address formatting

12 of 96 golden records uncovered on `notsc`, and the same 12 kinds reinserted — so these are
*substitutions*, not insertions or deletions: 8 `mmap`, 2 `arch_prctl`, 2 `rseq`. The content:

```
ptrace: mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON, -1, 0) = Ok(140737353850880)
kvm:    mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON, -1, 0) = Ok(18067456)
```

Sizes, flags, fds, offsets and ordering all match exactly. The difference is the guest virtual
address — KVM maps around 18 MB where ptrace maps around 140 TB — and it survives the repo's
`0x<hex>` normalisation **because syscall return values are printed in decimal**.

A labelled diagnostic policy (hex, plus ordinalising `= Ok(N)` for `N ≥ 2²⁰`) confirms it:

| guest | kvm uncovered, hex | kvm uncovered, +addr |
| --- | --- | --- |
| `notsc` | 12 | **4** |
| `bin_true` | 13 | **4** |
| `detlog_syscalls` | 44 | **36** |

This is reported to *locate* the residual, **not as a tier** — it could mask a genuinely large
returned count. But it says something concrete: the repo's DETLOG normalisation is incomplete
in a way that specifically penalises KVM, and roughly 4 records per simple guest are a real
difference that address formatting does not explain.

### SaBRe — structurally different from record 5, and it emits FEWER records

SaBRe emits 79 records where ptrace emits 96, and 47 where ptrace emits 68 on `bin_true`. It
diverges at golden ordinal 5: ptrace's fifth record is `brk(NULL)`, SaBRe's is `getpid()`.
It inserts 38 `clock_gettime` and 16 `getpid` that ptrace never emits, while 88 of the golden's
96 go uncovered — including 16 `mmap` and 8 `pread64`. Its coverage (7–33 %) is the lowest of
any self-deterministic backend. Fewer records than the golden means SaBRe is not observing part
of what ptrace observes; that is a coverage gap, not a formatting difference.

### LiteInst — adds a whole runtime, and alters the guest's own records

Characterised fully in `experiments/liteinst-detlog-parity_20260807`. Summary: 785 inserted
records on `notsc` (467 `read`, 70 `close`, 64 `mmap`, 55 `openat`, 48 `statx`) from the
preload runtime's bring-up executing inside the guest, and 49 of the golden's 96 uncovered —
including all 8 of the guest's *own* `getpid()` calls, because they carry different
virtual-time values. LiteInst does not merely surround an intact golden.

### DBI — self-nondeterministic on all 7 cells, so it has no parity number

3 of 3 pairs differ on every guest. Parity is **withheld**, not reported low: you cannot say a
stream differs from the golden by N when it differs from itself by an unknown amount. This
independently reproduces the earlier `DBI 4/94 FAIL_SELF_NONDETERMINISM` at a different binary
and on a different corpus.

## 4. One cell's verdict is overridden by a deeper measurement, and the override is recorded

`detlog_syscalls` × `liteinst` reads **0 of 3 pairs differing** in this matrix's own sample. A
**30-run** measurement at this same binary found **2 outcome classes (17 | 13)**. The minority
class is 13/30, so a 3-run sample draws all three from one class — and reports a clean pass —
about 60 % of the time.

The cell is recorded `self-nondeterministic`, with the 3-run sample preserved in the
`matrix_sample_selfdet` column rather than discarded. One differing pair establishes
nondeterminism, so a failure at higher n strictly dominates a pass at lower n; never the
reverse.

**This is a caveat on the whole matrix, not on one cell.** Every other cell here rests on 3
runs, and 3 runs cannot distinguish "deterministic" from "has a minority class I did not draw".
The honest reading of the 20 `diverges` and 7 `not-exercised` rows is *self-determinism not
refuted at n=3* — the only cell measured at n=30 is the one that failed.

## 5. Planted divergence is DETECTED, both directions, per backend

Real guest mutation (`notsc_mut.c`: 9 `getpid()` calls instead of 8, nothing else), run through
the live pipeline on each backend, against an unmutated-rerun negative control:

| backend | Z | control rerun | planted | verdict |
| --- | --- | --- | --- | --- |
| ptrace | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | **DETECTED**, no false positive |
| kvm | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | **DETECTED**, no false positive |
| sabre | 79 | Y=79/79, cover 79/79 | Y=20/79, cover 29/79 | **DETECTED**, no false positive |
| liteinst | 832 | Y=832/832, cover 832/832 | Y=6/832, cover 581/832 | **DETECTED**, no false positive |
| e9patch | 96 | Y=96/96, cover 96/96 | Y=6/96, cover 70/96 | DETECTED — but this is the ptrace runtime, not e9patch |
| dbi | 94 | **Y=3/94, cover 4/94** | Y=3/94, cover 4/94 | **NOT-ATTRIBUTABLE** |

DBI is the honest row: its control *already* fails, so the plant cannot be attributed to the
mutation. Reporting it as "detected" would be crediting the comparator for noise it would have
produced anyway. Sixteen further comparator controls — identity, planted substitution at four
ordinals, deletion, insertion, and hex-policy leakage — are in
`experiments/liteinst-detlog-parity_20260807/comparator-controls.csv`.

## 6. Corroboration against the earlier per-backend numbers

Prior measurement at hermit `590fcc9e`, two runs, a different guest:
`ptrace 94/94 PASS · DBI 4/94 FAIL_SELF_NONDETERMINISM · LiteInst 836/836 PASS ·
SaBRe 77/77 PASS · e9patch 94/94 PASS`.

Agreements: ptrace clean, DBI self-nondeterministic, SaBRe self-deterministic, and record
counts in the same range (my `notsc` gives ptrace 96, SaBRe 79, LiteInst 832).

Disagreements, both from **sample size**, not method: LiteInst "836/836 PASS" holds on a
`notsc`-shaped guest but fails on `detlog_syscalls` at n=30; and e9patch "94/94 PASS" is a
`not-exercised` cell once the engagement witness is read. Neither number was wrong — both were
generalised past the conditions that produced them.

## 7. Explicit non-inferences

- **Nothing here transfers to stack or heap.** No `--detlog-stack`/`--detlog-heap` was passed;
  DETLOG is its own emission path. The reverse inference — reading stack cleanliness off
  DETLOG, or vice versa — was nearly published as a false finding in both directions.
- **`byte-identical` for ptrace is a self-reference**, not a parity achievement. It is in the
  table so the denominator is visible and the comparator's non-vacuity is demonstrated.
- **This is `0041130ccb0d`, an ancestor of main, not the tip.** Chosen so every number here is
  comparable with the two companion artifacts built from the same worktree at the same pin.
  A cell measured here is not a claim about `e808322385d4`.

## 8. Reproduction

```bash
harness/matrix_collect.sh <hermit-binary> <outdir> 3    # 126 runs, ~30s
python3 harness/matrix_score.py <outdir> detlog-matrix.csv
```

The binary must be built with `--features third-party-backends -p detcore-dbi -p detcore-sabre
-p hermit-install`, or dbi/sabre/e9patch refuse with "backend is unavailable" and become
`no-result` rows. `matrix_collect.sh` records every attempt including refusals, so an
unavailable backend appears as a row with its exact refusal text rather than vanishing.

## 9. Follow-ups

1. **e9patch needs a corpus that exercises it.** All 7 guests give `candidate_sites=0`. Until
   then no e9patch DETLOG cell can mean anything. See the e9patch reach work (10 mnemonics).
2. **DBI's DETLOG self-nondeterminism blocks its whole column** — 7 withheld cells here.
3. **KVM's residual is mostly decimal-printed addresses.** The repo's `0x<hex>` DETLOG
   normalisation does not reach syscall return values, which specifically penalises KVM;
   ~4 records per simple guest remain unexplained by formatting and are worth a look.
4. **Re-measure the whole matrix at n=30.** Every non-DBI cell here is *n=3*, and the one cell
   measured deeper is the one that changed verdict.
