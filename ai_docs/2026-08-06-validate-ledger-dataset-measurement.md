# Measuring the current validate ledger dataset

**Task:** `measure-current-validate-ledger-dataset`
**Measured:** 2026-08-07T01:47:16Z (snapshot) / 2026-08-07T01:49:17Z (provenance) · **Box:** `devbig014`
**Mode:** read-only. No ledger file was written, compacted, rotated, or committed.
**Snapshot identity:** `sha256:40dccee853a9594834bcee294e557b53fd8a62baf65f96429a676a456bf7c4e0`

Every number below is a measurement of that exact snapshot unless it is in §6, which is
explicitly labelled **projection**.

---

## 0. Provenance

| Item | Value |
| --- | --- |
| Producer / measuring host | `devbig014` (short form; `hostname -s`) |
| Parent repo HEAD at measurement | `453f23beaaba04680b9a56233f705c8c47a3fada` (branch `main`) |
| Kernel | `6.18.39-0_fbk0_hardened_0_ga43d5727b443` |
| Python | 3.12.13+meta · `jq` 1.6 · `wc` (GNU coreutils) 8.32 |
| `gzip` | 1.12 |
| `zstd` | v1.5.5 |
| `xz` | 5.2.5 (XZ Utils) |

**Snapshot method.** The ledger is *live* — it grew from 722 203 B / 649 rows to
724 904 B / 650 rows between two commands during this session. All measurements were
therefore taken against a byte-identical copy at `/tmp/ledger-measure-snap/ledger.jsonl`.
The source file re-hashed to the same sha256 at the end of the session, so no append
landed mid-measurement and the snapshot equals the live file for this whole report.

---

## 1. What files exist (whole-box sweep)

`find /home/newton/work -xdev` (pruning `target/ .git/ node_modules/ build/`) for
`validate-run-*.jsonl` returns **exactly four files**. There are **no per-machine shards
anywhere on this box**, and no checkout-local ledger in any primary or worktree slot
(`{hermit,reverie,liteinst2}/ignored/` and `worktrees/*/*/ignored/` contain none).

| Path | Bytes | Rows | Role |
| --- | --- | --- | --- |
| `ignored/validate-run-ledger.jsonl` | **724 904** | **650** | **THE ledger** (`LEDGER_REL`, `ci-hub/lib/validate_status.rs:87`) |
| `ignored/validate-run-global.jsonl` | 626 949 | 326 | machine-wide aggregate (`aggregate.py`), stale since 2026-08-03T20:56 |
| `ignored/validate/validate-run-ledger.before-stop-no-result-20260805T025347Z.jsonl` | 494 513 | 512 | backup (prefix of the live series) |
| `ignored/validate/validate-run-ledger.before-1227-truncated-20260804T1722Z.jsonl` | 363 586 | 389 | backup (prefix of the live series) |

Both backups share the live ledger's earliest `started_at` (`2026-08-03T02:15:27Z`), so
the ledger has **not** been head-truncated: 2026-08-03 is its genuine epoch, and the
backups are strict prefixes (389 → 512 → 650 rows).

Ledger is **not** git-tracked: `git ls-files` → 0; `git check-ignore -v` →
`.gitignore:118:ignored/`. Mode `-rw------- newton:users`.

---

## 2. Bytes and rows — three independent methods agree

| Quantity | M1 `stat`/coreutils | M2 Python `json` parser | M3 `jq` 1.6 (C parser) |
| --- | --- | --- | --- |
| Bytes | `stat` 724 904 · `wc -c` 724 904 | 724 904 (bytes read) | — |
| Rows | `wc -l` 650 · `awk NR` 650 · `grep -c ''` 650 | 650 physical, 650 parsed | `jq -s length` → 650 |
| Malformed | blank/ws-only lines: **0** | **0** (0 blank, 0 non-object, 0 UTF-8 errors) | `map(type=="object")\|all` → **true** |

File ends with a newline, so `wc -l` is not undercounting a final unterminated record.
**All three methods agree: 724 904 bytes, 650 rows, 0 malformed.**

Mean row 1 115.2 B. Sum of per-row byte lengths equals the file size exactly (delta 0),
so there is no inter-record padding.

---

## 3. Machines

| Fact | Value |
| --- | --- |
| Unique machines (normalized short) | **1** |
| Raw `host` values as written | `devbig014` × 650 — one spelling, no variants |
| Rows with **no** machine identity | **0** (no missing, null, or empty `host`) |
| Rows with FQDN-form host | **0** |

**Per-machine breakdown** (trivial today — the dataset is single-producer):

| Machine | Rows | Bytes | % of dataset | Mean row | Unique commits |
| --- | --- | --- | --- | --- | --- |
| `devbig014` | 650 | 724 904 | 100.00 % | 1 115 B | 279 |

**A per-machine shard scheme applied to today's data yields exactly one shard containing
the entire dataset.** The sharding question cannot be validated against real multi-machine
data because no second producer's rows exist here.

### 3.1 The aggregate *does* carry two host spellings — and the split is not random

`ignored/validate-run-global.jsonl` writes `os.uname().nodename` (`aggregate.py:367`),
which on this box is fully qualified. Measured: **181 rows short `devbig014`, 145 rows
`devbig014.<domain-suffix>`** (literal domain scrubbed per task
context; it is the box's ordinary corp suffix) — one physical machine under two keys, as the earlier
scoping note found. New here: the split is a **perfect provenance discriminator**, not
noise —

| host form | `started_at` present | rows |
| --- | --- | --- |
| short | yes | 181 |
| FQDN | **no** | 145 |

All 145 FQDN rows are the log-reconstructed ones (no `started_at`); all 181 short-name
rows are ledger-copied. So the FQDN contamination is confined to one code path, and
normalizing `aggregate.py:367` fixes it without touching the ledger-copy path.

---

## 4. Time range, commits, and the run/row distinction

| Quantity | Value |
| --- | --- |
| Earliest `started_at` | **2026-08-03T02:15:27Z** |
| Latest `finished_at` | **2026-08-07T01:46:35Z** |
| Span | 343 868 s = **3.9800 days** (95.52 h) |
| Rows with no timestamp | 0 |
| **Unique commits** | **279** |
| Rows with no commit | 0 |
| Rows per commit | 2.33 |
| Distinct slots | 68 |

Per UTC day, by `started_at`:

| Day | Rows | Bytes | Commits | |
| --- | --- | --- | --- | --- |
| 2026-08-03 | 127 | 106 166 | 50 | partial (from 02:15:27Z) |
| 2026-08-04 | 369 | 366 515 | 143 | complete |
| 2026-08-05 | 91 | 130 153 | 50 | complete |
| 2026-08-06 | 57 | 113 526 | 35 | complete |
| 2026-08-07 | 6 | 8 544 | 3 | partial (to 01:46:35Z) |

Only **3 complete UTC days**, and they vary **6.5×** in row count (57 → 369). Any rate
derived from this window is weakly determined; §6 reports a range, not a point.

### 4.1 650 rows ≠ 650 runs

Keying on `(host, started_at, finished_at, commit, slot)`: **604 distinct keys, 37 keys
appearing more than once, 46 excess rows.** Of the 51 schema-5 rows, **46 share a key with
an existing row** — these are the finalizer-minted clones (`_clone_upgraded`,
`finalize_receipt.py:189-197`) described in the scoping note, not new runs.

**The dataset holds ~604 distinct validate runs in 650 records; ~7.1 % of rows are
re-statements of a run already present.** A per-machine-shard design that dedups on
arrival would store ~7 % fewer records; one that does not must expect a row count that
exceeds the run count.

Schema versions: v1 = 76, v2 = 20, v3 = 349, v4 = 154, v5 = 51.
Results: pass 357, fail 282, no_result 10, killed 1.
Profiles: `full` 419, `portable-strict-compat-only` 184, `portable-only` 24,
`only-portable` 18, `quick` 2, `envelope-only`/`selective`/`rr-compat-only` 1 each.
(`portable-only` vs `only-portable` remains two spellings of one concept — 42 rows split
across them.)

### 4.2 The ledger's epoch is not the machine's activity epoch

The machine-wide aggregate's log-reconstructed rows reach back to **2026-07-30T10:54:54Z**,
nearly four days before the ledger's first row. Rows/day there (by `finished_at`):
07-30 = 42, 07-31 = 14, 08-01 = 13, 08-02 = 40, 08-03 = 154, 08-04 = 63.

So validate activity on `devbig014` is observed over ~7.6 days, but the **ledger** only
covers the last 3.98 of them. The pre-08-03 runs are not in the ledger at all. This is a
measurement of retention, and it caps confidence in any annualization from ledger data.

---

## 5. Compression (measured on the 724 904 B snapshot)

| Codec | Bytes | Ratio | % of raw |
| --- | --- | --- | --- |
| `gzip -6` | 56 437 | 12.84× | 7.79 % |
| `gzip -9` | **53 528** | **13.54×** | 7.38 % |
| `zstd -3` | 52 861 | 13.71× | 7.29 % |
| `zstd -19` | **40 303** | **17.99×** | 5.56 % |
| `zstd -19 --long=27` | 40 283 | 18.00× | 5.56 % |
| `xz -9` | 42 088 | 17.22× | 5.81 % |

`zstd -19` beats `xz -9` here. The `--long=27` window buys 20 bytes — nothing at this size.

**The ratio is still climbing with dataset size**, so applying today's ratio to a year is
*conservative*:

| Prefix | Raw | gzip -9 | ratio | zstd -19 | ratio |
| --- | --- | --- | --- | --- | --- |
| 65 rows | 50 559 | 4 505 | 11.22× | 3 984 | 12.69× |
| 163 rows | 137 096 | 10 846 | 12.64× | 9 285 | 14.77× |
| 325 rows | 293 452 | 22 559 | 13.01× | 18 622 | 15.76× |
| 488 rows | 461 959 | 35 016 | 13.19× | 26 310 | 17.56× |
| 650 rows | 724 904 | 53 528 | 13.54× | 40 303 | 17.99× |

For reference, the machine-wide aggregate (626 949 B, 326 longer rows) compresses better
still: gzip -9 → 31 459 B (19.93×), zstd -19 → 20 244 B (30.97×).

**Sharding cost note:** compression ratio is size-dependent, so splitting one stream into
N per-machine shards costs ratio. At 65 rows a shard gets 12.69×, versus 17.99× for the
650-row whole — roughly a 40 % worse ratio for small shards. Irrelevant at these absolute
sizes (kilobytes), but it is the direction of the effect.

---

## 6. Annualized projection — **PROJECTION, NOT MEASUREMENT**

All of §6 is extrapolation from a **3.98-day, single-producer, 3-complete-day** window
whose daily rate varies 6.5×. Treat as an order of magnitude.

Measured rate (whole-span basis): **163.3 rows/day, 177.9 KiB/day, 151.8 distinct
runs/day**.

**One producer, 365.25 days:**

| Basis | Rows/yr | Raw/yr | gzip -9/yr | zstd -19/yr |
| --- | --- | --- | --- | --- |
| Whole-span (3.98 d) | 59 652 | **63.4 MiB** | 4.7 MiB | **3.5 MiB** |
| Complete-days mean (n=3) | 62 933 | 70.8 MiB | 5.2 MiB | 3.9 MiB |
| Slowest complete day (08-06) | 20 819 | 39.5 MiB | 2.9 MiB | 2.2 MiB |
| Fastest complete day (08-04) | 134 777 | 127.7 MiB | 9.4 MiB | 7.1 MiB |
| Machine-wide aggregate window (4.70 d, quieter) | 25 326 | 46.4 MiB | — | — |

**Honest range for one producer: ~20 k–135 k rows/yr, ~40–130 MiB/yr raw, ~2–7 MiB/yr
zstd-19.** Point estimate ≈ 60 k rows and ~63 MiB raw / ~3.5 MiB zstd per producer-year.

Scaled by producer count (whole-span basis, assuming a new box behaves like this one —
itself unverified, see §7):

| Producers | Rows/yr | Raw/yr | zstd -19/yr |
| --- | --- | --- | --- |
| 1 | 59 652 | 63.4 MiB | 3.5 MiB |
| 2 | 119 304 | 126.9 MiB | 7.1 MiB |
| 5 | 298 260 | 317.2 MiB | 17.6 MiB |
| 10 | 596 521 | 634.4 MiB | 35.3 MiB |

**Sizing read:** even ten producers for a year is well under a gigabyte raw and ~35 MiB
compressed. **Ledger volume is not a constraint on the storage decision** at any fleet
size currently plausible.

### 6.1 The ledger is the small tier

The durable *evidence logs* the ledger points at are far larger:

| Store | Files | Bytes |
| --- | --- | --- |
| `ignored/validate-run-ledger.jsonl` | 1 | 724 904 (**0.7 MiB**) |
| `ignored/validation-evidence/` | 68 | 39 623 407 (**37.8 MiB**, mean 583 KiB/log) |
| `ignored/validate/runs/` | 126 | 173 500 (0.17 MiB) |

Preserved logs are **54× the ledger's size** at 68 logs versus 650 rows. If log
preservation ever became universal rather than receipt-triggered, ~604 runs × 583 KiB ≈
344 MiB for these four days alone — ~31 GiB/producer-year. **Any storage design should
size the log tier, not the ledger tier**; a git-tracked log store is the decision that
carries real cost, and Invariant 11 (never commit binaries/artifacts) bears on it.

---

## 7. What this measurement does *not* establish

1. **Nothing about a second machine.** All 650 rows are `devbig014`. The per-machine
   breakdown is a one-row table. No cross-machine row size, rate, or schema variance was
   measured because no such data exists here.
2. **Whether another box's rate resembles this one.** The ×2/×5/×10 rows in §6 assume it
   does; that is an assumption, not a measurement.
3. **Whether the 78 receipts on the `validation-receipts` branch include a non-`devbig014`
   producer.** Not downloaded or censused (carried over unresolved from the scoping note).
4. **A stable rate.** 3 complete days varying 6.5× is not a rate; it is three samples.
   The fleet was also actively being changed during the window, so the series is not
   stationary.
5. **Pre-2026-08-03 ledger volume.** It is not retained in ledger form (§4.2); the
   log-derived aggregate rows are the only trace and they lack `started_at`.
6. **Query cost.** Not benchmarked. `ci-hub.rs:1081-1089` still records
   "not measured: ledger/store scan cost history is not retained."

---

## 8. Exact reproduction

```
# snapshot (the ledger is live; measure a copy)
cp --preserve=timestamps ignored/validate-run-ledger.jsonl /tmp/snap.jsonl
sha256sum /tmp/snap.jsonl        # expect 40dccee853a9594834bcee294e557b53fd8a62baf65f96429a676a456bf7c4e0

# method 1 — filesystem + coreutils
stat -c %s /tmp/snap.jsonl; wc -lc < /tmp/snap.jsonl; awk 'END{print NR}' /tmp/snap.jsonl

# method 3 — independent C JSON parser
jq -s 'length' /tmp/snap.jsonl
jq -s 'map(type=="object")|all' /tmp/snap.jsonl
jq -rs '[.[].host]|unique' /tmp/snap.jsonl
jq -rs '[.[].commit]|unique|length' /tmp/snap.jsonl

# compression
gzip -9 -c /tmp/snap.jsonl | wc -c
zstd -q -19 -c /tmp/snap.jsonl | wc -c
```

Method 2 (the Python parser producing the field censuses, per-machine byte breakdown,
duplicate-key analysis, and daily histogram) is inline in this task's notes; it opens the
file `'rb'` and writes nothing.
