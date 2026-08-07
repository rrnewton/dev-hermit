# Matched-corpus drop — 2026-08-07

> ## ⚠️ THESE POSITIVES ARE NOT FALSIFIABILITY-BACKED
>
> Every "green" below was scored by the **stripped probe**, which **cannot
> detect a planted defect**. Under `--verify-strict` the same control catches
> **all five** planted defects; the stripped probe **misses three** of them —
> DETLOG-only, address, and path divergence, each reporting
> `:: Success: deterministic. Determinism verified.`
>
> **So this measures the TIER / DEFINITION change only. It is not a strictness
> number.** The `--verify-strict` corpus re-run now in flight **supersedes**
> this, and the two must eventually be published **side by side**.
>
> A caveat the superseding run has to confront, from the same sweep: strict also
> diverged on the **clean control** (`bitwise_parity=False` on every row,
> including `clean_ctrl`). Strict is not yet a drop-in scorer, and that has to
> be resolved before its counts can be read as ground truth.
>
> **Explicitly NOT the drop:** the historical *"346 claimed bitwise → 0"*
> relabel. That was a label correction on a different artifact
> (`compat-envelope/scorecard.csv`, where 618 rows are 346 `stripped` + 272
> blank and **zero** `bitwise`). It is excluded here by construction — the
> deriver reads only determinism/parity verdicts.

## Anchor and exact SHAs

| what | value |
| --- | --- |
| anchor merge OID (directive) | `590fcc9eeb0339c5cf23f72b84394a63333e88ff` — verified present, ancestor of hermit `origin/main` |
| what that commit actually is | *"Bump the Reverie pin to 6144323c and carry the DBI budget forward"* — a **Reverie pin bump**. It did **not** add the missing fixtures and did **not** match the corpus. |
| Hermit SHA the new rows were measured at | `1fadc03779f2a246a9b5af5d4a93533511c837df` |
| Reverie | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` |
| old (2026-08-01) sweep | hermit `82a8e853357584a3a567fd80812e015572a607c7`, reverie `a4f33d69a56ed4233a53b218c39d93807ffc8cd0` |
| data | `cross-check-w14-raw.csv`, `../fullcorpus-scorecard.csv`, both at parent `a2c4467ccf1f431229523c5c8741fbdc73bb1156` |
| host / width / timeouts | shared 316-core devbig; ptrace+dbi storm width 16, sabre/e9patch/liteinst width 4, repairs PAR=1; run 90 s, verify 120 s |

**Provenance discrepancy, stated rather than smoothed over.** The measured SHA
is an *ancestor* of the anchor but **ten commits behind** it, and three of those
touch the scoring surface: `4da44515` *"refuse a determinism positive whose
evidence fields are blank"*, `0b2475b2` *"state the comparison each cell earned,
not the one it claimed"*, and `a86113e0` *"Bind the outer-scorecard writer to the
file's schema"*, plus `ci/test_harness.sh` and the e2e inventory. Because a
blank-evidence positive is now **refused**, re-deriving at the anchor could
legitimately *lower* these counts — and this corpus contains exactly such a cell
(see the accounting below). These numbers are therefore reported **as measured
at `1fadc037` and labelled with the anchor**, not claimed to have been measured
at it. The `--verify-strict` re-run should derive at `590fcc9e` directly.

## The definition change

A cell counts only if its guest source exists at the measured Hermit SHA, and
every cell that cannot produce a verdict is carried as an **explicit
no-result** rather than vanishing. Frozen corpus: **235 nominal / 205 effective
/ 1025 rows** (205 × 5 backends).

## Cell accounting — zero ambiguous missing rows

| class | cells | disposition |
| --- | ---: | --- |
| nominal corpus | 235 | as listed in `corpus/corpus-c.tsv` + `corpus-nonc.tsv` |
| missing guest fixtures (`performance/*`) | 30 | NO-RESULT: source absent at the measured SHA, no row emitted |
| **effective corpus** | **205** | rows emitted, x5 backends = 1025 |
| ptrace reference unusable — verify non-pass | 22 | NO-RESULT for parity on every candidate backend |
| ptrace reference unusable — verify PASSED, plain `--strict` reference run failed | 1 | NO-RESULT for parity; a green determinism cell with no reference stdout |
| unaccounted | 0 | must be 0 |

The second reference class is a single named cell: `c-programs/dbi-pid-virtualization` (`ptrace-run-fail-exit124`; output hash is the sha256 of empty).

## Old vs new greens

`green` = `deterministic == 1`, i.e. the STRIPPED probe returned a determinism pass.

| backend | old green | old executed | old % | new green | new executed | new % | abs delta | pp delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dbi | 156 | 200 | 78.0% | 158 | 205 | 77.1% | +2 | -0.9 |
| e9patch | 179 | 200 | 89.5% | 181 | 205 | 88.3% | +2 | -1.2 |
| kvm | 130 | 200 | 65.0% | — | 0 | no-result | — | — |
| liteinst | 118 | 200 | 59.0% | 121 | 205 | 59.0% | +3 | +0.0 |
| ptrace | 179 | 200 | 89.5% | 183 | 205 | 89.3% | +4 | -0.2 |
| sabre | 164 | 200 | 82.0% | 152 | 205 | 74.1% | -12 | -7.9 |
| **TOTAL** | **926** | **1200** | **77.2%** | **795** | **1025** | **77.6%** | **-131** | **+0.4** |

## Same numerator, two denominators — the definition change alone

| backend | green | nominal 235 | effective 205 | pp moved by the definition change |
| --- | ---: | ---: | ---: | ---: |
| dbi | 158 | 67.2% | 77.1% | +9.8 |
| e9patch | 181 | 77.0% | 88.3% | +11.3 |
| liteinst | 121 | 51.5% | 59.0% | +7.5 |
| ptrace | 183 | 77.9% | 89.3% | +11.4 |
| sabre | 152 | 64.7% | 74.1% | +9.5 |
| **TOTAL** | **795** | **67.7%** | **77.6%** | **+9.9** |

Not one additional cell passed between those two columns; only the denominator moved.

## Reading the drop — the total is a trap

| comparison | old | new | absolute | pp |
| --- | --- | --- | ---: | ---: |
| all backends | 926/1200 · 77.2% | 795/1025 · 77.6% | **−131** | **+0.4** |
| **excluding kvm** | 796/1000 · 79.6% | 795/1025 · 77.6% | **−1** | **−2.0** |
| kvm alone | 130/200 · 65.0% | **0 rows — NO-RESULT** | — | — |

**The −131 is almost entirely KVM leaving the matrix, not a regression.** KVM
contributed 130 greens on 2026-08-01 and today produces **no rows at all**: it
hangs on this host, confirmed at a 45 s bound and again at 300 s. Quoting −131
as a compatibility loss would be wrong; quoting the +0.4 pp as an improvement
would be equally wrong, since it is the denominator moving.

With KVM set aside, the four comparable backends moved **−1 green in total**.
That is the honest aggregate.

### The one real per-backend movement

| backend | old → new | abs | pp | reading |
| --- | --- | ---: | ---: | --- |
| **sabre** | 164 → 152 | **−12** | **−7.9** | the only movement outside noise — worth attribution |
| ptrace | 179 → 183 | +4 | −0.2 | small |
| liteinst | 118 → 121 | +3 | +0.0 | small |
| dbi | 156 → 158 | +2 | −0.9 | small |
| e9patch | 179 → 181 | +2 | −1.2 | small |

Calibrate against the measured noise floor: an independent replication of this
same corpus one commit apart agreed within **±1 cell of 205 per backend**
(`cross-check-w14.md`). So +2…+4 is at or just above noise, and **sabre −12 is
roughly an order of magnitude outside it** — the single result here that
warrants investigation rather than filing.

Note the sign disagreement in the small movers: several gained greens in
absolute terms while losing percentage points, because the denominator grew
from 200 to 205. Both columns are given for exactly that reason; neither alone
is interpretable.

## Reproducing

```bash
./matched-corpus-drop.py                                   # print the tables
./matched-corpus-drop.py --out matched-corpus-drop-tables.md --check
# => REPRODUCIBLE: re-derived matched-corpus-drop-tables.md is byte-identical
```

The deriver reads only committed CSVs, recomputes every figure, and refuses
(exit 3) if any cell is unaccounted for.
