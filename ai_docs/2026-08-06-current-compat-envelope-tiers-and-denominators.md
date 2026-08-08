# The current compat envelope, with a denominator and a tier on every number

**Task:** `report-current-envelope-with-tiers-and-denominators`
**Produced:** 2026-08-06, agent `hermit-w6`, slot `worktrees/w6` (read-only analysis; no measurement re-run)

This is the pre-certification baseline. It reports what the scorecard **records**, not what the
backends can do. Where a tier was never recorded, the number is marked `UNKNOWN` rather than
promoted to a comparison it did not earn.

---

## 1. Exact SHAs and measurement conditions

| item | value |
|---|---|
| Source of truth | `compat-envelope/scorecard.csv` **as committed on `origin/main`** |
| `origin/main` tip at analysis | `b4b94a0e7626ee3728327975980d99e819891faf` |
| Last content commit of the CSV | `b9f20ef5c8236a0932a660966b3d8e41e8bf5d73` — *"Bind scorecard parity to full provenance"* (2026-08-06 21:44:15 -0700) |
| Rows in file | 618 |
| Renderer | `compat-envelope/render-scorecard.rs` |

**Do not read this envelope off a local checkout.** The parent primary was 205 commits behind
`origin/main` at analysis time and its working tree carried an uncommitted 23-column scorecard,
while `origin/main` carries 32 columns. The measurements are byte-identical between the two; only
the schema differs. Every number below is taken from the committed `origin/main` file.

### The envelope is not a single measurement

There is **no single hermit SHA for this envelope.** The 127 enabled cells were produced by four
different hermit builds across a 3.5-day window:

| condition | value |
|---|---|
| `hermit_sha` | **4 distinct** over the enabled set — `09d7bd0c6f98` (48), `9429005ca04b` (46), `fc49593ac21c` (26), `82a8e8533575` (7) |
| `reverie_sha` | **`unknown` on 120 of 127 enabled cells**; `a4f33d69a56e` on the remaining 7 |
| `run_utc` span | 2026-08-01 18:25:44Z → 2026-08-05 07:24:24Z (3.54 days) |
| `run_id` | 5 distinct over the enabled set |
| `run_mode` | regression 120 / expansion 7 |
| `lane` | portable 127/127 (no privileged-lane cell is enabled) |
| `dirty` | `false` on 618/618 |
| Host | shared 316-core dev box; portable lane |

Any statement of the form "the envelope at SHA X" is therefore false as stated. The correct form is
"the envelope pooled across these four SHAs." This alone is enough to block monotonic ratcheting
until a single-SHA sweep exists.

---

## 2. Denominator

618 rows is **not** the denominator. Two reductions apply, in order:

1. **Last-writer-wins de-duplication** on `(bucket, test_id, test_mode, backend)` — the key the
   renderer uses. 618 rows → **562 distinct cells**; 56 rows are superseded re-runs.
2. **`cell_state == enabled`** — 562 cells → **127 enabled cells**. (Pre-dedup the enabled count is
   180; the difference is 53 superseded dbi backend-parity re-runs.)

> **The envelope denominator is 127 enabled cells.**

De-duplication is not cosmetic. Pooled over raw rows, dbi reads `78/79` with one failure; after
last-writer-wins that failing cell was superseded by a passing re-run and dbi reads `26/26` in
`backend-parity`. Quoting the raw-row number reports a failure that the current code no longer has.

---

## 3. The envelope — pass count / denominator, per backend × bucket

Enabled cells only, last-writer-wins. `.` = no cell exists for that pair (**not** a zero score).

| bucket | ptrace | dbi | kvm | sabre | liteinst |
|---|---|---|---|---|---|
| applications | 1/1 | . | . | 0/1 † | . |
| backend-parity | 48/48 | 26/26 | . | . | . |
| c-programs | 8/8 | 8/8 | 3/7 | 0/3 † | . |
| data-handling | 2/2 | . | . | 0/1 † | . |
| determinism-stress | 5/5 | . | . | . | . |
| determinism-stress-c | 1/1 | . | . | . | . |
| language-runtimes | 6/6 | . | . | . | . |
| system-utils | 8/8 | . | . | 0/2 † | . |
| **TOTAL** | **79/79** | **34/34** | **3/7** | **0/7 †** | **0/0** |

† **sabre's 0/7 is not a failure.** All seven cells record `outcome=unavailable`, reason
*"backend binary not present in this checkout."* They were never run. Reporting sabre as 0% parity
would be a confirmed-red claim built on a not-measured cell.

**Coverage is the hidden variable.** ptrace spans all 8 buckets. dbi appears in **2 of 8**, and 26
of its 34 cells sit in `backend-parity` alone. kvm appears in **1 of 8**. liteinst has **no enabled
cell at all** — it contributes 220 of the 618 rows and 0 to the envelope. A per-backend percentage
that ignores the bucket axis reports breadth no backend except ptrace has.

**kvm's 4 failures are not all kvm's.** Three of the four (`epoll-determinism`, `mmap-determinism`,
`thread-sync-determinism`) record `ptrace-side-fail-exit2` — the ptrace **reference** also failed on
that cell, so there is no valid comparison to fail. Only `madvise-determinism`
(`kvm-run-fail-exit17`) is an unambiguous kvm-side failure. kvm is better read as **3 pass / 1
confirmed fail / 3 no-valid-reference**, denominator 7.

---

## 4. Tier — the answer is UNKNOWN on every cell

The current schema has four tier-bearing columns. On the 127 enabled cells:

| column | populated | reading |
|---|---|---|
| `parity_tier` | **0 / 127** | no cell records which tier it met |
| `parity_comparator` | **0 / 127** | no cell records what compared it |
| `stdout_parity` (qualified) | **0 / 127** | the qualified parity column is empty |
| `bitwise_parity` | **0 / 127** | full-strict never recorded |
| `compared_log_messages` | **0 / 618** | INFO-log comparison never counted |
| `tier` (legacy) | 63 `stripped-uncounted`, 64 blank | a named **weak** comparator, or nothing |

> **0 of 127 enabled cells carry a recorded comparison tier.** Every green in section 3 is
> **UNKNOWN tier**. None is full stdout+INFO+stack+heap. None is even confirmed stdout-only.

The schema says this itself. Commit `b9f20ef5` renamed the old `parity` column to
**`legacy_parity_unqualified`** (values are now the string `stdout_parity:1` / `stdout_parity:0`)
and left the new qualified `stdout_parity` empty. The 112 parity claims below are, by the file's own
column name, unqualified.

The legacy `tier=stripped-uncounted` label is the honest maximum available for 63 cells: it names a
comparator the row already recorded (`verify_compare=stripped`) and admits in its own name that the
count is absent. It is **not** a tier in the sense this task means, and it must not be counted green.

### The greenness ladder over 127 enabled cells

| definition | count |
|---|---|
| `outcome=pass` | **116 / 127** |
| + `legacy_parity_unqualified = 1` | **112 / 127** |
| + `deterministic = 1` | **59 / 127** |
| + `stdout_parity` (qualified) recorded | **0 / 127** |
| + `parity_tier` recorded | **0 / 127** |
| + `bitwise_parity = 1` (full-strict) | **0 / 127** |

The drop from 112 → 59 is the honest cost of requiring determinism alongside parity. The drop from
59 → 0 is the cost of requiring the evidence to say *which comparison* earned it.

**Mode composition matters for reading the tier.** Of the 127: verify 74, strict 50, chaos 1, custom
1, replay 1. All 63 `stripped-uncounted` cells are verify-mode; all 50 strict-mode cells are blank
tier. dbi's 34 cells are 26 strict + 8 verify; kvm's and sabre's are all verify.

---

## 5. The rendered output disagrees with the file, and why

`render-scorecard.rs --all` reports a different envelope from the same CSV:

```
bucket             ptrace       dbi        kvm      sabre   liteinst
TOTAL                  72  11%~, 11%  21%~, 26%        n/a  38%~, 40%
```

The divergence is **selection, not data**:

- It de-duplicates last-writer-wins (same as above), then filters `test_mode == verify` → 507 cells.
- Its denominator per bucket is *distinct test_ids where **ptrace** passed*, total 72 — not the
  enabled-cell count.
- **It never filters `cell_state`.** `cell_state` appears in `render-scorecard.rs` only at line 151
  inside `REQUIRED_COLUMNS` (header validation); it is never read as a value.

Consequence: **liteinst's rendered `38%~, 40%` is computed entirely from 220 cells that are all
`cell_state=disabled`.** kvm's 200 verify cells are 177 disabled / 7 enabled / 16 expansion. So
"liteinst is at 38–40%" and "liteinst has no enabled cells" are both true of the same file on the
same day. That is precisely the fake-green failure this baseline exists to prevent.

**What the renderer gets right**, and should be preserved in any replacement: it guards vacuity
(`not-exercised`, `backend_engaged==0`, `parity_exercised==false` are withheld, never credited);
it tracks `par_measured` so a blank parity reads as unknown rather than 0; it tracks `ran` so an
`unavailable` cell is not a confirmed fail; and it states its own tier caveat in the header —
*"stdout-parity% compares piped guest stdout SHA-256 only … an upper bound on four-signal
cross-backend parity."* Every percentage it prints is stdout-tier at best, by its own admission.

### Two renderer defects

1. **Zero-denominator buckets print a percentage of nothing.** Six buckets have a zero ptrace
   denominator (`backend-parity-c`, `bin-c`, `chaos-c`, `debugger-c`, `shared-futex-c`, `util-c`)
   and still render `0%, 0%` across four backend columns = **24 cells reporting 0% of 0**. The
   legend already distinguishes `n/a` (not runnable) from `?` (never compared); a zero denominator
   needs the same treatment and currently gets neither.

2. **A latent fail-open determinism inference.** The renderer computes
   `det = deterministic.unwrap_or(pass && test_mode == "verify")`, i.e. a passing verify cell with a
   blank determinism field is *counted deterministic*. **Measured: it fires on 0 cells** — 79
   non-ptrace verify cells have a blank `deterministic`, and none of them pass. So no current number
   is inflated by it. It is a hole to close before the next collector run, not a correction to today's
   figures.

---

## 6. Baseline statement

> As of `origin/main` `b4b94a0e`, scorecard content commit `b9f20ef5`:
> the compat envelope is **127 enabled cells**, pooled across **4 hermit SHAs** over 3.5 days, with
> `reverie_sha` unknown on 120 of them.
> **116/127 pass; 112/127 pass with unqualified stdout parity; 59/127 also record determinism;
> 0/127 carry a recorded comparison tier; 0/127 are bitwise/full-strict.**
> ptrace 79/79 (8 buckets) · dbi 34/34 (2 buckets) · kvm 3/7 (1 bucket, 3 of 4 failures lack a valid
> ptrace reference) · sabre 0/7 **all not-run** · liteinst 0/0 enabled.
> **Every green above is UNKNOWN tier.**

## 7. What must change before this can be ratcheted

1. **One sweep, one SHA.** A baseline pooled over 4 builds cannot be compared to a later one.
2. **Populate `parity_tier` / `parity_comparator` / `stdout_parity` at the collector.** The columns
   exist and are load-bearing; nothing writes them. Until then every green is untiered by construction.
3. **Record `reverie_sha`.** Unknown on 120/127 makes cross-repo attribution impossible.
4. **Make the renderer honor `cell_state`**, or rename what it reports — it is not currently
   rendering the envelope.
5. **Suppress zero-denominator cells** instead of printing `0%`.
6. **Close the `unwrap_or` determinism fallback** while it still fires on 0 cells.
