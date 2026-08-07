# The heap constant over the full 179: the sampled rate held, the sampled *share* did not

**Task:** `firm-the-heap-constant-rate-over-all-179-cells-reparse-only`
**Method:** re-parse only. No new runs.
**Source:** `experiments/detlog-stack-heap-parity-179_20260807/results.csv`, content commit
`d7168e259be2ccfbee8e9717b1a540fac219e41c` (parent `main`). Measurement conditions inherited from that
experiment's README: Hermit `0041130ccb0daa54ffe7dce2792c1f1495c57e58`, Reverie
`0ae0c01b5e4c9fbf85c97adc66c2740f280727df`, fixed 179-cell population, 1,074 heap rows (6 targets/cell).
**Reproduce:** `python3 experiments/detlog-stack-heap-parity-179_20260807/reparse_heap_population.py` (rc=0).

## Headline

The 51-cell sample was **representative for the rate the task asked about and unrepresentative for the
number that actually motivated the correction.**

| quantity | 51-cell sample | full 179 population | verdict |
| --- | ---: | ---: | --- |
| cells exercising heap | 48/51 = **94.1 %** | 172/179 = **96.1 %** | +2.0 pp — **not material** |
| constant's share of all heap records | 48/290 = **16.55 %** | 172/31,950 = **0.54 %** | **31× overstatement** |

So: *"the heap dimension is a constant 2 on most cells"* survives as a statement about **cells**
(97/179 = 54.2 % sit at exactly 2, where the constant is half the dimension). It does **not** survive as a
statement about **records**. Over the whole corpus the constant is half a percent of the heap data.

**Why the sample missed it:** it was drawn from the light `c-programs` end. The full population is
extraordinarily top-heavy — **17 cells carry 30,650 of the 31,950 heap records, i.e. 95.9 % of all heap
data**, with a maximum of 4,547 on a single cell. Those cells are almost entirely guest-varying, so they
dilute a per-cell constant to near nothing. The sample's 16.55 % was arithmetically correct *for the sample*
and describes a corpus that does not exist.

That skew is the finding behind the finding: **any record-level heap statistic quoted over this corpus is
really a statistic about 17 cells.** A rate computed over the light tail and a rate computed over the corpus
are different quantities, and neither is wrong so long as it names which one it is.

This is the task's own predicate turned on the earlier finding: it was not wrong about the constant, it was
**unqualified about its population** — specifically about which denominator, *cells* or *records*, it was
quoting.

## The per-cell correction holds across the population

The correction — **−1 record on every cell that has any heap record**, uniform across guests and backends —
was derived on the sample and is applied, not re-derived, here. Two population checks confirm its *shape*
without needing per-record hashes:

1. **`ref_records` is genuinely a per-cell property.** It agrees across all six backend rows for
   **179/179 cells; 0 disagreements.** Had it varied by backend, "−1 per cell" would be ill-defined. The
   script raises rather than proceeding if this fails.
2. **The distribution has a hard floor at 2 with an empty bin at 1.**

   | raw heap records | 0 | 1 | 2 | ≥3 |
   | --- | ---: | ---: | ---: | ---: |
   | cells | 7 | **0** | 97 | 75 |

   With no universal constant, `raw == 1` would be the natural floor for the lightest allocating guest, and
   with 97 cells piled at exactly 2 we would expect a comparable population at 1. Observed: **zero.** A
   floor at 2 with bin 1 empty is the signature of *one constant + at least one guest record*, over all 179
   rather than over the sample.

**Bound on what a re-parse can prove.** `results.csv` preserves per-cell record *counts* and whole-dimension
digests (`ref_dimension_sha256`) but **not per-record hashes**. The identity claim — that the subtracted
record is specifically `74518f204d46de660dff3ed003e92476bad8c691` — is therefore **not re-verifiable from
this file.** It rests on the original 48/48 measurement. What the re-parse establishes is the correction's
shape (exactly one, uniform, per-cell), by the two hash-free checks above. Re-verifying identity across the
population would need the raw detlogs, i.e. a re-run, which this task explicitly excluded.

## Definition correction to the published figure

The experiment's README states its denominator policy explicitly: *"Failed, absent, or non-engaged cells
remain in the denominator."* For the heap dimension that policy admits seven cells that **cannot produce a
heap measurement at all** — they have no heap records, so there is nothing for a parity comparison to
compare. Typing them as NO-RESULT rather than as denominator members:

| backend | published | corrected |
| --- | ---: | ---: |
| DBI | 0 / 179 | **0 / 172** |
| KVM | 0 / 179 | **0 / 172** |
| SaBRe | 0 / 179 | **0 / 172** |
| LiteInst | 0 / 179 | **0 / 172** |
| e9patch | 0 / 179 | **0 / 172** |
| *ptrace-control (reference lane)* | *167 / 179 = 93.3 %* | ***167 / 172 = 97.1 %*** |

**This is a DEFINITION CORRECTION, not a regression.** The evidence is unchanged; only the denominator's
membership rule changed. Concretely:

- **No numerator moves anywhere.** All five candidate backends were already 0 and stay 0. The reference
  lane's numerator is unchanged at 167 — only its denominator shrinks, so its rate *rises* 93.3 % → 97.1 %.
- **No green is created or destroyed.** All seven dropped cells were already non-passing on independent
  grounds, verified per-cell by the script (check 3, which exits 1 if any dropped cell held a PASS):

  | cell | raw | pre-existing backend results |
  | --- | ---: | --- |
  | `applications/timed-progress-bar` | 0 | `NO_RESULT_FIXTURE_ABSENT` |
  | `backend-parity-c/cpuid-probe` | 0 | `FAILURE_REFERENCE` |
  | `c-programs/dbi-execveat-unsupported` | 0 | `FAILURE_BACKEND`, `NO_RESULT_NOT_ENGAGED`, `NO_RESULT_ZERO_REFERENCE` |
  | `c-programs/dbi-wait-lifecycle` | 0 | `FAILURE_REFERENCE` |
  | `c-programs/hello-nostdlib` | 0 | `FAILURE_BACKEND`, `NO_RESULT_ZERO_REFERENCE` |
  | `c-programs/pread64-nostdlib` | 0 | `FAILURE_BACKEND`, `NO_RESULT_ZERO_REFERENCE` |
  | `c-programs/racewrite-nostdlib` | 0 | `FAILURE_BACKEND`, `NO_RESULT_ZERO_REFERENCE` |

  **All three** `nostdlib` cells in the corpus appear in that list — there is no fourth, and none of them
  is retained. They are the controls that already refuted the "2 is a libc-startup floor" claim:
  freestanding guests, zero heap records. They belong outside a heap denominator on their own merits, and
  the correction removing exactly them is a positive sign it is typing the right thing.

## What this does and does not change about the backend gap

**Does not change it.** 0/172 is the same measured gap as 0/179. Nothing here softens the finding that no
candidate backend achieves strict full-depth heap parity against the golden ptrace reference.

**Does change how a future heap figure must be read.** A heap-parity rate over cells that carry only the
initial image describes libc startup, not the guests. With the constant excluded, the 97 cells at raw==2
are measuring **exactly one guest allocation each** — real, but thin — and that thinness is now visible in
the typed disposition instead of hidden inside a 2 that looks like data. The 0.54 % record-level share is
the honest statement of how much of the *data* the constant contaminates; the 54.2 % cell-level share is
the honest statement of how many *cells* it dominates. Both need their denominator named.

## Not done here

- The identity re-verification described under *Bound* (needs raw detlogs / a re-run).
- Wiring `compat-envelope/heap_disposition.py` into `collect-envelope.rs` so future sweeps emit the typed
  disposition natively rather than requiring this re-parse. Tracked separately.
