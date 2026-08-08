# Scorecard Table 2: selection predicate, the six programs, and the missing Reverie backends

Task: `audit-scorecard-table2-selection-and-reverie-coverage` (research-only; no edits made).
Agent: hermit-w15. Date: 2026-08-06.
Parent HEAD at audit time: `765eb0a21b320eed92341b40d345c43ee8628710`.

**Terminology:** this document uses **DBT**. The source, CSVs, and CLI flags all still
use the legacy name **`dbi`**; every `dbi` token quoted below is verbatim from source.

---

## 0. Correction to the task's premise

The task names `compat-envelope/SCORECARD-CURRENT.md`. **That file does not exist**, and
the string `SCORECARD-CURRENT` appears nowhere in the repository. The document actually
containing "Table 2" is:

- `compat-envelope/SCORECARD.md:84` — `## Table 2 — Reverie: Tool callback-count parity (B1.5+ backends)`

Table 1 is at `compat-envelope/SCORECARD.md:41`.

---

## 1. TL;DR

1. **Table 2 is NOT a subset of Table 1. It is disjoint from it** — zero shared buckets,
   zero shared test_ids. The two tables do not share a denominator, a corpus, a collector,
   a CSV, or even a hermit SHA.
2. **There is no selection predicate**, because there is no selection. Table 2 is not
   derived from Table 1's data by filtering. It comes from a **separate collector**
   writing a **separate CSV**. The two-table layout is **editorial** — hand-assembled in
   `SCORECARD.md`. The renderer has no concept of "Table 1"/"Table 2" (0 occurrences in
   `render-scorecard.rs`).
3. **The six programs are 2 Reverie tools × 3 busybox applets**, collapsed in the doc to
   the single count `6`.
4. **DBT / SaBRe / LiteInst absence in Table 2 is `NOT COLLECTED — structurally
   unrepresentable`.** It is *not* backend-unavailable, *not* no-result, and emphatically
   *not* a backend failure. The Reverie collector's data model has no field in which a
   DBT/SaBRe/LiteInst launcher could be named.
5. Those same three backends **do** produce rows in the sibling Hermit CSVs from this very
   checkout — so host capability is proven present and is definitively not the cause.

---

## 2. The six programs, enumerated as separate rows

Source of truth: `compat-envelope/reverie-scorecard.csv` — 12 data rows = 6 programs × 2
backends. Every program is `bucket=reverie-examples`, `test_mode=counter`, `lane=portable`,
`cell_state=enabled`.

| # | test_id (program) | tool | guest argv | ptrace syscalls | kvm syscalls | delta | kvm outcome | kvm det | kvm parity |
|---|-------------------|------|-----------|----------------:|-------------:|------:|-------------|--------:|-----------:|
| 1 | `counter1-true`     | counter1 | `true`    | 12 | 8  | −4 | diverge | 1 | 0 |
| 2 | `counter1-echo-hi`  | counter1 | `echo hi` | 15 | 11 | −4 | diverge | 1 | 0 |
| 3 | `counter1-pwd`      | counter1 | `pwd`     | 16 | 12 | −4 | diverge | 1 | 0 |
| 4 | `counter2-true`     | counter2 | `true`    | 12 | 8  | −4 | diverge | 1 | 0 |
| 5 | `counter2-echo-hi`  | counter2 | `echo hi` | 15 | 11 | −4 | diverge | 1 | 0 |
| 6 | `counter2-pwd`      | counter2 | `pwd`     | 16 | 12 | −4 | diverge | 1 | 0 |

All 6 ptrace rows: `outcome=pass`, `deterministic=1`, `parity=` (empty — ptrace *is* the
reference, so it has no parity value to carry). All 6 kvm rows: `outcome=diverge`,
`deterministic=1`, `parity=0`.

**The `0%, 100%` cell in Table 2 decodes as:** 0/6 parity (all six diverge) and 6/6
determinism. The `−4` delta is constant across all six, which is what makes the doc's
"constant 4 fewer syscalls" claim a real structural interception-surface gap rather than
noise.

**Where the 6 comes from** (`collect-reverie-compat.rs`):

- `TOOLS` = exactly 2 entries — lines 47–50:
  `Tool { name: "counter1", kvm_bin: Some("reverie-kvm-counter1") }`,
  `Tool { name: "counter2", kvm_bin: Some("reverie-kvm-counter2") }`
- default applets = exactly 3 — line 168: `.unwrap_or_else(|| "true;echo hi;pwd".to_string())`
- test_id construction — line 238: `let test_id = format!("{}-{}", tool.name, slug);`
  where `slug` is the argv joined by `-` (line 236), giving `echo hi` → `echo-hi`.

2 × 3 = **6**. The guest ELF is static busybox (lines 158–165), required because the KVM
launchers need a statically-linked guest (header, line 18–19).

---

## 3. Set relation to Table 1 — DISJOINT, proven

Table 1's rows are the 13 e2e manifest buckets (`c-programs`, `determinism-stress-c`,
`system-utils`, …). Table 2's single bucket is `reverie-examples`, which is **not one of
them**.

Measured against every Table 1 source CSV:

| CSV (Table 1 source) | rows with `bucket=reverie-examples` | rows with a `counter1-`/`counter2-` test_id |
|---|---:|---:|
| `scorecard.csv` | 0 | 0 |
| `fullcorpus-scorecard.csv` | 0 | 0 |
| `corpus-manifest.csv` | 0 | 0 |
| `e9patch-scorecard.csv` | 0 | — |

Intersection is empty on both the bucket axis and the program axis. **Table 2 adds six
programs that exist nowhere in Table 1's 200-cell denominator**; it does not re-slice
them. Consequently Table 1's `TOTAL = 200` and Table 2's `TOTAL = 6` are **not
commensurable** and must never be summed or expressed as a percentage of one another.

Why they are disjoint by construction: they measure different *boundaries*. Table 1
measures the Hermit/Detcore program-compat envelope over the e2e manifest corpus. Table 2
measures the **Reverie B1.5 `Guest`/`Tool` callback boundary** over a synthetic
busybox-applet corpus. `collect-reverie-compat.rs` header lines 6–11 states this directly:
*"before the hermit-side Detcore envelope, measure that the shared Reverie tools run
through both the ptrace and KVM `Guest` contracts."*

---

## 4. There is no selection predicate — the split is editorial

- `render-scorecard.rs` contains **0** occurrences of `Table 1` or `Table 2`.
- The renderer takes exactly one `--csv` and discovers its buckets dynamically:
  `render-scorecard.rs:368` — `let buckets: BTreeSet<String> = denom_cells.iter().map(|c| c.bucket.clone()).collect();`
- `render-scorecard.rs:230` documents the only CSV-level choice offered:
  *"`--csv` is required: choose `compat-envelope/fullcorpus-scorecard.csv` for the full
  corpus or `compat-envelope/scorecard.csv` for the CI/regression subset"* — note this is a
  Table-1-family choice; `reverie-scorecard.csv` is not even mentioned.

So the pipeline is two independent producers whose outputs a human pasted into one
document:

```
collect-envelope.rs        -> scorecard.csv / fullcorpus-scorecard.csv -> render-scorecard.rs -> Table 1
collect-reverie-compat.rs  -> reverie-scorecard.csv                    -> render-scorecard.rs -> Table 2
```

**Consequence:** nothing regenerates `SCORECARD.md` as a unit, and nothing enforces that
the two tables describe the same code. See §6.

---

## 5. Backend × program collection matrix, with typed absence reasons

Legend for the absence taxonomy requested by the task:
`COLLECTED` · `NOT COLLECTED` (never requested/emitted) · `NOT SELECTED` (emitted, filtered
out of the view) · `BACKEND UNAVAILABLE` (host/artifact gate) · `NO RESULT` (ran, no verdict).

| program | ptrace | KVM | DBT (`dbi`) | SaBRe | LiteInst | e9patch |
|---|---|---|---|---|---|---|
| `counter1-true`    | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |
| `counter1-echo-hi` | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |
| `counter1-pwd`     | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |
| `counter2-true`    | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |
| `counter2-echo-hi` | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |
| `counter2-pwd`     | COLLECTED pass det=1 | COLLECTED diverge par=0 | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** | **NOT COLLECTED** |

The matrix is uniform: the absence is a property of the **collector**, not of any program
or any backend.

### 5.1 Why KVM has data (and what gated it)

Two conditions had to hold, and both did:

1. **A KVM launcher exists for the tool.** `struct Tool` carries a `kvm_bin` field
   (line 44), and both tools populate it (lines 48–49) with `reverie-kvm-counter1` /
   `reverie-kvm-counter2`.
2. **`/dev/kvm` exists on the host.** Line 182: `let kvm_present = Path::new("/dev/kvm").exists();`

Runnability resolution, lines 241–249:

```rust
let (bin_name, runnable, why_unrunnable) = match backend.as_str() {
    "ptrace" => (tool.name.to_string(), true, String::new()),
    "kvm" => match tool.kvm_bin {
        Some(b) if kvm_present => (b.to_string(), true, String::new()),
        Some(_) => ("".into(), false, "no /dev/kvm".to_string()),
        None    => ("".into(), false, "no kvm launcher for tool".to_string()),
    },
    other => ("".into(), false, format!("unsupported backend {other}")),
};
```

The collector is honest about non-runnable cells — it emits a `skip` row with the reason
rather than faking a pass (lines 251–258; header line 16: *"honestly recorded as
not-runnable (0/0), never faked"*). **No such skip rows exist for DBT/SaBRe/LiteInst**,
which is itself the proof that those backends were never even requested.

### 5.2 Why DBT / SaBRe / LiteInst have no data — three independent structural causes

**(a) The data model cannot name their launchers.** `struct Tool` (lines 40–45) has exactly
one non-ptrace launcher field, `kvm_bin`. There is no `dbi_bin`, `sabre_bin`, or
`liteinst_bin`. A DBT launcher is *unrepresentable*; this is a schema limitation, not a
configuration gap.

**(b) The default backend list is hardcoded to two.** Line 175–176:
`.unwrap_or_else(|| "ptrace,kvm".to_string())`. The recorded run used the default —
`reverie-scorecard.csv` contains exactly `ptrace` (6) and `kvm` (6) and nothing else.

**(c) Even an explicit request would not measure them.** The `match` above has no arm for
them, so `--backends dbi` falls to `other =>` and produces a `skip` row reading
`unsupported backend dbi`. The collector would refuse, loudly and in-band.

### 5.3 The absence is NOT backend-unavailability — proof

All three backends emit real rows in the sibling Hermit CSVs **from this same checkout**:

| CSV | ptrace | kvm | dbi (DBT) | sabre | liteinst | e9patch |
|---|---:|---:|---:|---:|---:|---:|
| `reverie-scorecard.csv` | 6 | 6 | **0** | **0** | **0** | **0** |
| `scorecard.csv` | 99 | 200 | 92 | 7 | 220 | 0 |
| `fullcorpus-scorecard.csv` | 200 | 200 | 200 | 200 | 200 | 200 |

`fullcorpus-scorecard.csv` carries 200 rows for every one of the six backends. Host
capability and launcher availability for DBT/SaBRe/LiteInst are therefore demonstrated.
**Nothing about the missing Table 2 cells licenses any inference of backend failure** —
exactly the trap the task warned against.

(The low `sabre=7` in `scorecard.csv` versus 200 in the full corpus is a separate
Table-1-side coverage question, out of scope here and not evidence about Reverie.)

---

## 6. Provenance skew between the two tables (additional finding, not asked for)

The two tables are pinned to **different hermit SHAs**, and the doc does not say so:

| | source CSV | hermit SHA | reverie SHA | run id / date |
|---|---|---|---|---|
| Table 2 | `reverie-scorecard.csv` | `2f3689bd8830ab6b59dacea6cb72951f4d0d899e` | `a4f33d69a56ed4233a53b218c39d93807ffc8cd0` | `reverie-20260801`, committed `9db7ee0` 2026-08-01 |
| Table 1 (L2 green 28) | `scorecard.csv` | `9429005c` (per `SCORECARD.md:35`) | — | canonical release run |
| Table 1 (measured 179/200) | `REPORT.md` | `82a8e853` (per `SCORECARD.md:8`) | — | — |

`SCORECARD.md` itself was last committed `781a07a` (2026-08-03), while
`reverie-scorecard.csv` and every Table 1 CSV on disk were regenerated **2026-08-06
13:58**. The rendered document is therefore **older than the data it purports to
summarize**, and Table 2's numbers are bound to a hermit SHA three days behind Table 1's.
Per policy, evidence binds to commits, not to a document — the doc currently carries
neither table's SHA inline.

Also note `compat-envelope/render-scorecard.rs` is **uncommitted-modified** in the working
tree (another agent's in-flight work — untouched by this audit), so re-rendering right now
would not reproduce the committed renderer's output.

---

## 7. Recommended doc/schema changes (exact)

**D1 — Retitle Table 2 and state the disjointness inline.** Change `SCORECARD.md:84` to
name the boundary and the corpus, e.g.
`## Table 2 — Reverie B1.5 Guest/Tool callback parity (SEPARATE corpus; disjoint from Table 1)`
with a one-line note: *"These 6 programs are not part of Table 1's 200-cell denominator.
The two totals are not commensurable."*

**D2 — Expand the collapsed `6` into the six named rows.** Replace the single
`reverie-examples | 6 | 0%, 100%` row with the six-row table in §2 of this document
(program, ptrace count, kvm count, delta, det, parity). The collapsed count is precisely
what hid the program set from the reader.

**D3 — Replace blank backend cells with typed absence tokens.** Table 2 should carry a
column per backend with an explicit reason token rather than nothing:
`NOT-COLLECTED(no launcher field)` for DBT/SaBRe/LiteInst. A blank cell is
boolean-blind — it reads identically to "measured and empty" and to "backend broken".

**D4 — Stamp both tables with their hermit/reverie SHA and run date.** Table 2's data is
already SHA-stamped inside the CSV (`hermit_sha`, `reverie_sha` columns); surface it in the
rendered table footer. This also makes the §6 skew self-evident on sight.

**S1 — Schema: generalize `struct Tool`'s launcher field.** Replace the single
`kvm_bin: Option<&'static str>` (`collect-reverie-compat.rs:44`) with a per-backend map,
e.g. `launchers: &'static [(&'static str, &'static str)]` (backend → bin name). This is the
one change that makes DBT/SaBRe/LiteInst *expressible*; without it, D3 can only ever report
a permanent structural gap.

**S2 — Emit explicit skip rows for every known backend, not just the requested ones.**
Today a backend that is never requested leaves *no trace at all*, which is
indistinguishable from a collector that forgot. Iterating the known-backend list and
emitting `skip` rows with `unsupported backend <b>` / `no <b> launcher for tool` would make
every absence typed and auditable directly from the CSV — the same honesty the collector
already applies to the KVM cell (lines 244–246).

**S3 — Rename `dbi` → `dbt` in the CSV backend vocabulary** (with a compatibility read
path), so the scorecard stops carrying the legacy name into new evidence.

Priority: **D2 and D3 answer the owner's actual complaint** ("collapsed counts and blank
backend cells hide the actual program set and coverage gaps") and need no code change to
the collector. S1/S2 are the durable fix.

---

## 8. Reproduction

All read-only; no test execution required.

```bash
cd ~/work/dev-hermit/compat-envelope

# The six programs, per backend:
awk -F, 'NR>1{printf "%-18s %-7s %-8s det=%-2s par=%-2s syscalls=%s\n",$9,$11,$13,$14,$15,$16}' \
  reverie-scorecard.csv

# Backend inventory per CSV (col 11 = backend):
for f in reverie-scorecard.csv scorecard.csv fullcorpus-scorecard.csv; do
  echo "--- $f ---"; awk -F, 'NR>1{print $11}' "$f" | sort | uniq -c | sort -rn
done

# Disjointness proof (all zero):
grep -c 'reverie-examples' scorecard.csv fullcorpus-scorecard.csv corpus-manifest.csv
grep -cE 'counter1-|counter2-' scorecard.csv fullcorpus-scorecard.csv corpus-manifest.csv

# Generator evidence:
sed -n '40,50p;155,185p;238,258p' collect-reverie-compat.rs
grep -c 'Table 1\|Table 2' render-scorecard.rs   # -> 0
```
