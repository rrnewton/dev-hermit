# Scorecard provenance: the runs were real, but 8 of 91 parity verdicts can be re-derived from the artifact

**Task:** `scorecard-parity-claims-verify-backed` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress. Standard applied: **#268 — carry the condition with the value.**

## Answer

The question is whether each parity/det cell "traces to a real run with recorded provenance". Split
those two:

* **Real run: yes.** Every row carries a 40-hex `hermit_sha`, no row is `dirty`, and the runs plainly
  happened. I found no fabricated cell.
* **Recorded provenance: no.** The rows do not carry enough condition to bind a value to a code state
  or to re-derive it. **8 of 91** parity verdicts are re-derivable from the artifact; **173 of 180**
  enabled cells do not record which Reverie they ran against; and the enabled set **pools six different
  hermit SHAs**, five of which are not on `main` at all.

## The measurements

| dimension | state | value |
| --- | --- | --- |
| `hermit_sha` recorded | ✅ | 618/618, 40-hex |
| `dirty` recorded | ✅ | 0 dirty rows |
| code is current | ❌ | 2 SHAs are **304 / 306 commits** behind tip `2c54dfb5`; **5 of 7 are not on main at all** |
| single code state | ❌ | the 180 enabled cells pool **six** hermit SHAs, 2026-08-01 → 08-05 |
| `reverie_sha` recorded | ❌ | **173 of 180 enabled cells = `unknown` (96%)** |
| `run_utc` single-valued | ❌ (1 run) | `kvm-fullcorpus-scorecard` carries **4 different** `run_utc` under one `run_id` |
| comparator / flags recorded | ❌ | **no such column exists** |
| parity re-derivable from the artifact | ❌ | **8 of 91** |

### 1. Nine runs, seven code states, five of them off-main

`scorecard.csv` is not a measurement — it is a **merge of nine runs**:

| run_id | rows | hermit_sha | on main? | behind tip |
| --- | ---: | --- | --- | ---: |
| `kvm-fullcorpus-scorecard` | 200 | `82a8e8533575` | yes | **306** |
| `liteinst-fullcorpus-…` | 200 | `464cbd9f9bb4` | yes | **304** |
| `canonical-release-ptrace-dbi` | 46 | `9429005ca04b` | **NO** | – |
| `liteinst-spst-…` | 40 | `464cbd9f9bb4` | yes | 304 |
| `backend-parity-75edd745…` | 28 | `75edd7455dc9` | **NO** | – |
| `backend-parity-52d56e5c…` | 28 | `52d56e5ceb38` | **NO** | – |
| `backend-parity-fc49593a…` | 28 | `fc49593ac21c` | **NO** | – |
| `backend-parity-09d7bd0c…` ×2 | 48 | `09d7bd0c6f98` | **NO** | – |

The off-main SHAs are PR heads — expected, since hermit lands by squash, so a PR head never becomes a
main ancestor. That is not misconduct; it does mean **no cell in this scorecard was measured at a
commit currently on main**, and any aggregate sums cells produced by six different products.

### 2. `reverie_sha` is `unknown` for 96% of the enabled cells

Parity is a **cross-backend** claim, and DBI/KVM behaviour is a function of the Reverie pin. Yet:

```
enabled cells with reverie_sha = "unknown":  173 / 180
```

Only the 7 KVM cells record one (`a4f33d69a56e`). The 46-row `canonical-release-ptrace-dbi` run — the
one that produces most of the DBI parity claims — records `unknown`. **Those cells cannot be bound to
a Reverie state, re-derived, or compared across runs.** This is the single largest provenance hole and
it sits directly on the claim the scorecard exists to make.

### 3. A `parity=1` cannot be checked from the row that asserts it

`parity=1` means "this backend's stdout SHA-256 equalled the ptrace reference's". The row records the
backend's `output_hash` — but **not the reference hash it was compared against**. To re-derive the
verdict you must find the ptrace row for the same `test_id`/`test_mode` **in the same run**:

```
non-ptrace enabled cells carrying a parity verdict : 91
  re-derivable (ptrace ref row present, with hash) :  8
  NOT re-derivable — no ptrace row in the same run : 83
```

For 83 of 91, the reference hash existed only transiently inside the collector process and is **gone**.
The verdict survives; the evidence for it does not. That is exactly the shape #268 exists to prevent —
a value that does not carry the condition it was computed under.

### 4. Nothing records *how* the value was produced

The 19 columns say what was run (`backend`, `test_mode`, `lane`, `run_mode`) but never the
**strictness or flags**. So a `verify` cell cannot be distinguished as `Stripped` vs `Canonical`, and a
`parity` cell cannot state which profile flags (`--no-virtualize-cpuid --max-timeslice=disabled`) were
in effect. Two cells with identical rows can rest on different comparators.

## Recommendations, smallest first

1. **Record `reverie_sha`.** The collector already records `hermit_sha`; 96% of enabled cells lack the
   pin that half the claim depends on. Biggest hole, likely smallest fix.
2. **Add `ref_output_hash` to parity rows.** One extra column makes `parity=1` self-checking and takes
   re-derivability from 8/91 to 91/91. Today the artifact asserts a comparison whose other operand it
   discarded.
3. **Add a `flags`/`comparator` column** — profile flags, and for verify cells `Stripped` vs
   `Canonical`. Without it a future bitwise tier is indistinguishable from today's.
4. **Refuse to pool silently.** Either regenerate in one run, or stamp every aggregate with the number
   of distinct code states behind it. An aggregate over six hermit SHAs is not a measurement of any one
   product, and nothing in the current output says so.
5. **Stamp staleness.** Every row should be comparable to the current tip; today the freshest enabled
   cell is from 2026-08-05 and the two largest runs are ~305 commits behind.

## Scope and limits

* Audit of the recorded artifact only — **I re-ran no cell** and generated no new data.
* **I found no evidence of a fabricated or assumed value.** Every cell appears to come from a real run.
  The finding is about *bindability and re-derivability*, not honesty.
* "Behind tip" is measured against `origin/main` `2c54dfb5` as of this run; main moves, so those
  numbers grow.
* Companion findings, reported separately and not repeated here: the parity column is stdout-hash-only
  and DBI's DETLOG is uncollectable via `--log-file`
  (`experiments/compat_scorecard_depth_20260806`); the `deterministic` column is set from `pass` with
  no mode check, leaving 105 of 168 unearned (`experiments/strict_verify_holes_20260806`).

## Reproduction

```bash
python3 - <<'PY'
import csv, collections
rows=list(csv.DictReader(open('compat-envelope/scorecard.csv')))
en=[r for r in rows if r['cell_state']=='enabled']
print('enabled reverie_sha=unknown:', sum(1 for r in en if r['reverie_sha']=='unknown'), '/', len(en))
idx=collections.defaultdict(dict)
for r in rows: idx[(r['run_id'],r['test_id'],r['test_mode'])][r['backend']]=r
par=[r for r in en if r['parity'] in ('0','1') and r['backend']!='ptrace']
ok=sum(1 for r in par if idx[(r['run_id'],r['test_id'],r['test_mode'])].get('ptrace',{}).get('output_hash'))
print('parity verdicts re-derivable:', ok, '/', len(par))
PY
```
