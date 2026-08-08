# The compat envelope, republished under the tightenings

**Task:** `republish-compat-envelope-under-the-tightened-definition` · agent `hermit-w6` · 2026-08-07

## The one clean drop

> **OLD: 1,837 green / 2,284 rows** — raw `outcome=pass`, no tier required.
> **NEW: 0 green / 2,284 rows** — `outcome=pass` **AND** a qualifying comparison tier.
>
> Definition change: a pass now needs a *named comparison* behind it. Every one of the 2,284 rows carries
> `comparison_tier = legacy-unqualified`; **zero** carry either qualifying value
> (`full-stdout-info-stack-heap`, `stdout-info-stack-heap-spot-check`).

This is the drop, it is measured, and it is the baseline the ratchet starts from.

## Premise correction: one of five tightenings is in force here, not five

The task lists five definition changes as having "landed together". Checked one at a time against
`origin/main` `3353768049`:

| # | tightening | status in the compat envelope |
|---|---|---|
| 1 | per-cell **TIER** recorded | ✅ **LANDED** — 2,284/2,284 rows tiered |
| 2 | per-row **RELAXATION** set, no green under a relaxation | ❌ **NOT FOUND** |
| 3 | **NOT-COMPARABLE** where a side is not self-deterministic | ⚠️ landed as a **measurement**, not a scorecard field |
| 4 | hash **DOMAIN** normalized | ❌ **NOT FOUND** — and the underlying defect is **still open** |
| 5 | **0/0 dimensions REFUSED** | ⚠️ landed in the **ci-hub receipt layer**, not the envelope |

Publishing all five as "the drop" would overstate what is in force. One is — and its drop is large and real.

### (2) Relaxation — not found

The only `relaxation` match near this is a prose comment at `ci-hub/validate/green_class.py:36` about the
rebase wrapper's `receipt_present`. There is **no relaxation column** in the 33-column scorecard schema and no
per-row relaxation set. Reported as not landed rather than assumed.

### (3) NOT-COMPARABLE — measured, not wired

Real and specific, on **8 guest-dimension rows** (4 guests × {stack, heap}):

| guest | stack | heap |
|---|---|---|
| trivial | NOT-COMPARABLE | NOT-COMPARABLE |
| fork_exec_pipeline | NOT-COMPARABLE | NOT-COMPARABLE |
| threaded | NOT-COMPARABLE | **1/38 FAIL** |
| heap_exercising | NOT-COMPARABLE | **6/6 PASS** |

**Comparable 2/8; NOT-COMPARABLE 6/8.** The 6/6 generalises only to the named guest `heap_exercising` and does
not override `threaded` heap 1/38. KVM trials are **refused** (30 s wall bound, exit 137) — a refusal, not a
self-determinism pass. No scorecard column carries any of this.

### (4) Hash domain — not found, and the defect is live

Zero relevant matches in `compat-envelope`. rrnewton/hermit **#1810** — *"every ptrace `output_hash` is the
empty-input digest, so the golden reference carries no output evidence"* — is **OPEN** as of this writing.

Measured on the committed `scorecard.csv` (618 rows): **186** distinct `output_hash` values, **216 blank**, and
**80 rows carrying exactly `sha256("")` = `e3b0c44298fc1c14…`**. So the normalization is absent *and* the
evidence gap it was meant to address is still present.

### (5) 0/0 refused — real, but a different layer

`ci-hub/lib/measured.rs:101` keys on `denominator == 0`; the qualifying-receipt path requires
`planned_test_nodes > 0` (`qualifying_receipt.py:111`, `records.rs:200`, `finalize_receipt.py:222`). A genuine
guard — governing **validation receipts**, not compat-envelope cells.

## Every cell accounted for — nothing silently dropped

2,284 rows across four committed scorecards. The outcome buckets sum to exactly 2,284.

| scorecard | rows | pass | diverge | fail | timeout | skip | unavailable | gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `e9patch-scorecard.csv` | 454 | 454 | – | – | – | – | – | – |
| `fullcorpus-scorecard.csv` | 1200 | 926 | 188 | 70 | 16 | – | – | – |
| `reverie-scorecard.csv` | 12 | 6 | 6 | – | – | – | – | – |
| `scorecard.csv` | 618 | 451 | – | 83 | – | 72 | 7 | 5 |
| **TOTAL** | **2284** | **1837** | **194** | **153** | **16** | **72** | **7** | **5** |

Of the 1,837 passes, **0** carry a qualifying tier. The other 447 rows were never passes and are unchanged by
this tightening.

## Comparisons that are NOT drops

**`346/618` vs `128/133` is not a drop.** Different populations: 618 rows all-backend under the stripped
probe, versus 133 rows ptrace-only under the strict predicate. Of the 346 old greens, the strict run
re-measured **8 — 2.3%**. Do not subtract them.

**ptrace falsifiability: 8/8 old → 8/8 strict, DROP = 0.** On the only like-for-like population that exists,
the tightening cost nothing.

## SHA and measurement conditions

| | |
|---|---|
| parent `origin/main` | `335376804902fb67b28c12f8416fc08e886e840c` |
| scorecard content commit | `0c38fb3773d0300c46b68fa48561c932b9dc524f` — *"scorecard: require a comparison tier for every green"* |
| tier figures | reproduced by `compat-envelope/check-scorecard-tier.py`, run against the **committed** tree |
| envelope population | 4 scorecards, 2,284 rows |
| strict ptrace run | hermit `590fcc9eeb0339c5cf23f72b84394a63333e88ff`, `--verify-strict` at `log_scope=info`, ptrace only |

**Method note.** My first run of the tier checker reported 7 schema violations. That was my error: the script
globs relative to *its own location* and I had copied it to `/tmp`, so it audited `/tmp`'s stray CSVs. Run
in place against the committed tree it enumerates exactly 4. A checker whose population is derived by glob is
sensitive to where it is invoked from — worth knowing before quoting its refusals.

## What is still not true

- **Not falsifiability-backed beyond ptrace.** kvm, dbi, sabre, liteinst, e9patch have no strict-predicate
  measurement. Their rows are `legacy-unqualified` and stay that way.
- **0 rows at either qualifying tier**, so the ratchet starts from zero. That is the intended shape of one
  clean drop, but it means the *first* ratchet step has no prior green to protect.
- **Three of the five tightenings are not in force in this artifact** (§ above). The ratchet should not be
  described as running "under the tightened definition" plural until 2 and 4 land and 3 and 5 are wired to
  the envelope.
