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

## Addendum: the two axes the definition names, and a renderer cross-check

### backend x bucket — coverage is the hidden variable

The definition ratchets "per backend x bucket", so the backend total alone hides where the
cells are. Denominator 180 enabled; each cell is `parity=1 / enabled`, `bw` = bitwise-qualified.

| backend | applications | backend-parity | c-programs | data-handling | det-stress | det-stress-c | lang-runtimes | system-utils | total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| dbi | – | 78/79 bw=0 | 8/8 bw=0 | – | – | – | – | – | 87 |
| kvm | – | – | 3/7 bw=0 | – | – | – | – | – | 7 |
| ptrace | 1/1 | 48/48 | 8/8 | 0/2 | 3/5 | 1/1 | 6/6 | 8/8 | 79 |
| sabre | 0/1 bw=0 | – | 0/3 bw=0 | 0/1 bw=0 | – | – | – | 0/2 bw=0 | 7 |

**DBI's headline 86/87 pass rate is 91% concentrated in one bucket.** 79 of its 87 enabled
cells are `backend-parity`; it has no cell at all in six of the eight buckets ptrace covers.
KVM is 7 cells in a single bucket. SaBRe spans four buckets with **0 parity in every one**.
A per-backend percentage that ignores this reports breadth it does not have.

### `det >= parity` holds, but only vacuously

The definition requires determinism% >= parity% per cell.

* violations (`det < parity`): **0**
* cells where BOTH fields are set: **60 of 180**
* cells claiming `parity=1` with determinism **unset**: **105 of 164** (64%)

So the invariant is satisfied on the 60 cells where it can be evaluated and is
**unenforceable on 64% of all parity claims**, because the determinism side is simply absent.
Zero violations here is not evidence the invariant holds; it is mostly evidence it was not
checkable.

### Renderer cross-check — it agrees, and it now fails loudly

`render-scorecard.rs --csv compat-envelope/scorecard.csv --all` states the tier in its own
caveat, and it matches the CSV-level finding exactly:

> stdout-parity% compares piped guest stdout SHA-256 only. It is an upper bound on four-signal
> cross-backend parity; INFO logs, stack detlogs, and heap detlogs are not measured.

So every percentage the renderer prints is stdout-tier — an **upper bound**, by its own
statement, on the quantity the north star is defined over. Independent agreement with
`bitwise_parity = 0/618`.

**One prior defect is fixed.** `--latest` used to print a confident `TOTAL 0` and exit 0. It now
refuses with a typed reason: *"NO DATA: run … has 0 ptrace/verify passing cells, so the
denominator is empty and no percentage is defined (this is NOT a measured zero)"*, and names
rows considered, ptrace rows, modes and backends present. That is the denominator travelling
with the count.

**One remains.** Six buckets have a **zero ptrace denominator** and still render `0%, 0%` across
all four backend columns — 24 cells reporting a percentage of nothing:
`backend-parity-c`, `bin-c`, `chaos-c`, `debugger-c`, `shared-futex-c`, `util-c`. The legend
already distinguishes `n/a` (not runnable) and `?` (never compared); a zero denominator needs
the same treatment rather than a confident `0%`, which is the exact failure `--latest` was just
fixed for, one level down.

