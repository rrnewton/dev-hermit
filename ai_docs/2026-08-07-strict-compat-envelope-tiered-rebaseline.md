# Strict-compat envelope: tiered re-baseline, 2026-08-07

Position of the `vision-strict-compat-envelope-to-100` north star, measured under a
**fixed definition**, with the comparison tier stated per cell. Read-only; no cell was
re-run and no producer was changed.

## The fixed definition this is measured against

From the north star (memory #164/#89): ptrace strict/replay is the denominator; every
other backend cell is **parity%** (bitwise-identical to the ptrace reference) **and**
**determinism%** (run1 == run2), with det >= parity. **Full parity means `--log INFO` +
`--detlog-stack` + `--detlog-heap` all match — not stdout/exit.**

That definition is what makes a tier mandatory. A cell that passed a *stripped* comparison
and a cell that passed a *bitwise full-DETLOG* comparison both render as "green", and only
the second one counts toward this goal.

## Headline: 1063 green claims, 0 qualified

| scorecard | rows | `parity=1` claims | tier column | bitwise-qualified |
| --- | ---: | ---: | --- | ---: |
| `scorecard.csv` | 618 | 164 *(of 180 enabled)* | yes | **0** |
| `reverie-scorecard.csv` | 12 | — | yes (`counter`) | **0** |
| `e9patch-scorecard.csv` | 454 | 227 | **absent** | n/a |
| `fullcorpus-scorecard.csv` | 1200 | 672 | **absent** | n/a |
| **total** | **2284** | **1063** | | **0** |

**`bitwise_parity` is empty on all 618 rows of the authoritative scorecard.** Under the
fixed definition the envelope stands at **0 / 618 = 0.0%**, unchanged from the
2026-08-06 baseline.

And 899 of the 1063 green claims — the `e9patch` and `fullcorpus` populations — are
**untiered by construction**: those CSVs are still 19 columns with no `tier` and no
`bitwise_parity` field, so there is nowhere to record what comparison produced the green.

## Tier, stated per cell

`scorecard.csv`, denominator 618 records:

| tier | count | share |
| --- | ---: | ---: |
| `stripped-uncounted` | 346 | 56.0% |
| *(none)* | 272 | 44.0% |
| any bitwise tier | **0** | **0.0%** |

Enabled subset, denominator 180:

| backend | enabled | pass | `stripped-uncounted` | untiered | bitwise |
| --- | ---: | ---: | ---: | ---: | ---: |
| dbi | 87 | 86 | 8 | 79 | 0 |
| kvm | 7 | 3 | 3 | 4 | 0 |
| ptrace | 79 | 79 | 52 | 27 | 0 |
| sabre | 7 | 0 | 0 | 7 | 0 |
| **total** | **180** | **168** | **63** | **117** | **0** |

`parity=1` holds on **164 of 180** enabled cells — 91%, which reads like the north star is
nearly met. Not one of those 164 is qualified by a tier that would make the claim mean full
bitwise DETLOG parity. That gap between 91% and 0% is the entire point of stating the tier.

**Determinism, denominator 168 enabled passing cells:** 63 claim `deterministic=true`,
105 claim nothing. Of the 63, **63 carry no counted message comparison** —
`compared_log_messages` is empty on 180 of 180 enabled rows. Every determinism claim in the
ledger is uncounted.

## Re-baseline: old vs new, side by side

The definition did not change; the **instrumentation** did, so both columns are stated.

| quantity | 2026-08-06 baseline | 2026-08-07 measured | direction |
| --- | --- | --- | --- |
| qualifying bitwise greens | 0 | **0 / 618** | unchanged |
| enabled rows lacking `reverie_sha` | 173 / 180 | **0 / 180** | **fixed** |
| `tier` column | absent | present, 346 `stripped-uncounted` / 272 none | **new** |
| `bitwise_parity` column | absent | present, 0 populated | **new** |
| `compared_log_messages` | absent | present, 0 / 180 populated | **new, unfed** |
| determinism claims | 105/168 claimed without a double-run | 63 claim `true`, all 63 uncounted | reframed |
| non-ptrace enabled cells | 91 | 101 | +10 |
| scorecard columns | 19 | 23 | +4 |

**The ratchet moved on instrumentation, not on the envelope.** Provenance is now complete —
every enabled row carries an exact Reverie SHA, which closes the largest 2026-08-06 defect.
Three new columns exist to express the tier, the bitwise verdict, and the message count. All
three are either uniformly weakest (`stripped-uncounted`) or entirely unfed.

That is progress worth recording precisely, because it changes the *kind* of gap. On
2026-08-06 the ledger could not say what tier a green was. It can now, and the answer is that
none of them is bitwise. The problem moved from unmeasurable to measured-at-zero.

## What blocks the first non-zero bitwise cell

Not the scorecard. Producing a bitwise cell requires a backend whose full DETLOG can be
compared at all, and the per-backend frontier recorded on 2026-08-06 still gates that: DBI's
unvirtualized host TID in the DETLOG frame, KVM's disabled `compare_logs`, SaBRe's stderr
routing, LiteInst activation, and e9patch's zero-reach no-ops. Those are tracked as separate
tasks; this document is the measurement, not the repair.

## Two coverage holes worth naming

1. **899 of 1063 green claims live in CSVs that cannot express a tier.**
   `e9patch-scorecard.csv` (454 rows) and `fullcorpus-scorecard.csv` (1200 rows) are 19-column
   and carry no `tier`/`bitwise_parity`. Any consumer reading them gets an untiered green by
   construction, and the largest single block of `parity=1` claims (672) is in the one with no
   tier at all.
2. **`compared_log_messages` exists and is never populated.** A column that is always empty is
   indistinguishable from a column that is absent, except that its presence implies the count
   was checked. Either feed it or make readers refuse a determinism claim that lacks it.

## Reproduction

```
python3 - <<'EOF'
import csv, collections
rows = list(csv.DictReader(open('compat-envelope/scorecard.csv')))
en = [r for r in rows if (r.get('cell_state') or '').strip() == 'enabled']
print(collections.Counter((r.get('tier') or '<none>').strip() for r in rows))
print(sum(1 for r in rows if (r.get('bitwise_parity') or '').strip() in ('true','1')))
EOF
```
