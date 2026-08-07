# Validate ledger dataset — exact measurement

**Task:** `measure-current-validate-ledger-dataset`
**Measured:** 2026-08-07T01:48:45Z · **Producer box:** `devbig014` · **Method:** read-only
**Nothing was mutated, compacted, rewritten, uploaded, or committed.** Ledger data is untouched.

Companion to [`validate-ledger-storage-and-backup-options-20260806.md`](validate-ledger-storage-and-backup-options-20260806.md)
and [`2026-08-06-validate-ledger-multi-machine-scoping.md`](2026-08-06-validate-ledger-multi-machine-scoping.md).
This note answers only "how large is the current data", with the measurement and the
extrapolation kept strictly apart (§1–§7 measured, §8 projected).

---

## 0. Provenance

| Fact | Value |
| --- | --- |
| Path | `ignored/validate-run-ledger.jsonl` (parent root) |
| Canonical constant | `ci-hub/lib/validate_status.rs:87` — `LEDGER_REL` |
| Git-tracked? | **No** — `.gitignore:118` (`ignored/`), 0 entries in `git ls-files` |
| Snapshot sha256 | `40dccee853a9594834bcee294e557b53fd8a62baf65f96429a676a456bf7c4e0` |
| Concurrency control | Live file re-hashed **after** all work: identical sha and size, so no concurrent append perturbed any figure |

All measurements ran against a byte-identical copy so every number below is internally
consistent with every other.

## 1. Bytes and rows — two independent methods, agreeing

| Quantity | Method A | Method B | Method C |
| --- | --- | --- | --- |
| Bytes | `stat -c %s` = **724904** | `wc -c` = **724904** | `du -b` = **724904** |
| Rows | `wc -l` = **650** | `grep -c ''` = **650** | JSON parser = **650** |

**724,904 bytes (707.9 KiB) · 650 rows.**

The trailing newline **is** present (verified with `od` on the final byte), so `wc -l`
does not undercount here. That is stated rather than assumed because the missing-final-newline
off-by-one is the standard failure of this measurement.

**Stronger reconciliation than either count:** summing each parsed line's own byte length
plus one byte for its newline totals **724904 exactly**. The parser's decomposition accounts
for every byte in the file with no remainder, so the byte figure and the row figure are not
two independent guesses — they are consistent with each other.

## 2. Valid vs malformed

**650/650 valid JSON objects. 0 malformed, 0 blank, 0 non-object.** There is no salvage problem.

## 3. Machine identity

- **Unique machines: 1** (`devbig014`).
- **Rows lacking machine identity: 0** — `host` present and non-empty on 650/650.
- **FQDN check: 0 of 650** host values contain a dot. Stored values are *already* short
  names; no scrubbing was required and none is published here.

| machine | rows | bytes | row % | byte % | mean B/row |
| --- | ---: | ---: | ---: | ---: | ---: |
| `devbig014` | 650 | 724,904 | 100.0% | 100.0% | 1,115 |

The per-machine table is **degenerate today**, and that is the central fact for the sharding
decision: this is a single-producer dataset, so there is currently nothing to union.

## 4. Timestamp range

| Field | Present | Earliest | Latest |
| --- | --- | --- | --- |
| `started_at` | 650/650 | 2026-08-03T02:15:27Z | 2026-08-07T01:42:14Z |
| `finished_at` | 650/650 | 2026-08-03T02:15:30Z | 2026-08-07T01:46:35Z |

**Span = 3 d 23:31:08 = 3.9800 days.**

**No history was lost to the in-place rewrites** — checked, not assumed. The producer chain
does full-file rewrites (`ci-hub/validate/finalize_receipt.py:142-173`), and two backup
snapshots exist under `ignored/validate/`:

| snapshot | rows | earliest | malformed | hosts |
| --- | ---: | --- | ---: | --- |
| `…before-1227-truncated-20260804T1722Z.jsonl` | 389 | 2026-08-03T02:15:27Z | 0 | `devbig014` |
| `…before-stop-no-result-20260805T025347Z.jsonl` | 512 | 2026-08-03T02:15:27Z | 0 | `devbig014` |
| live | 650 | 2026-08-03T02:15:27Z | 0 | `devbig014` |

All three begin at the **identical** earliest timestamp, so 389 → 512 → 650 is monotonic
append and the live file holds the whole history since that instant.

## 5. Unique commits

`commit` present on 650/650, all exactly 40 hex characters.
**279 unique commits · mean 2.33 runs per commit.**

## 6. Compression

Basis 724,904 bytes uncompressed.

| tool | version | bytes | ratio | % of raw |
| --- | --- | ---: | ---: | ---: |
| `gzip -9` | gzip 1.12 | 53,546 | 13.54× | 7.4% |
| `zstd -3` | zstd v1.5.5 | 52,861 | 13.71× | 7.3% |
| `zstd -19` | zstd v1.5.5 | **40,303** | **17.99×** | **5.6%** |
| `xz -9` | XZ Utils 5.2.5 | 42,088 | 17.22× | 5.8% |

JSONL of near-identical records compresses 14–18×; `zstd -19` slightly beats `xz -9` here.

## 7. Git storage cost — measured, not reasoned about

Because the option under consideration is *committing shards to git*, the relevant cost is
what git charges, not what the file weighs. Replayed the ledger as **5 daily commits of the
growing file** into a throwaway repo, then `git gc --aggressive`:

| quantity | bytes |
| --- | ---: |
| `.git/objects`, all 5 commits | **56,674** |
| `.git` total | 85,369 |
| 5 naive full copies | 3,624,520 |
| one raw copy | 724,904 |

**0.08× a single raw copy, and 1.41× a single `zstd -19` archive — for the entire 5-commit
history.** Git's delta + zlib handles append-only growth about as well as compressing once;
per-commit overhead is not the cost driver.

## 8. Projection — extrapolation begins here

Measured rate over the 3.98-day span: **163.3 rows/day, 182,139 bytes/day (177.9 KiB/day)**,
mean 1,115 B/row.

A single-basis annualization is not defensible here, for two reasons visible in the data:

**(a) The run rate is bursty, not steady.** Rows per UTC day: 08-03 = 127, 08-04 = 369,
08-05 = 91, 08-06 = 57, 08-07 = 6 (partial). A **6.5× range** across four days. The mean
describes a four-day sample of fleet activity, not a stable process.

**(b) Row size is growing and will keep growing.** Mean bytes/row by `schema_version`:
v1 = 781 (76 rows), v2 = 767 (20), v3 = 963 (349), v4 = 1627 (154), v5 = 1240 (51). The last
100 rows average **1,780 B/row = 1.60×** the whole-file mean. Any projection anchored on the
whole-file mean row size is therefore **low**.

Sensitivity, **per machine per year** (zstd column uses this corpus's measured 5.56% ratio):

| basis | rows/day | B/day | raw MiB/yr | zstd-19 MiB/yr |
| --- | ---: | ---: | ---: | ---: |
| whole span (3.98 d) | 163.3 | 182,139 | 63.4 | 3.52 |
| full UTC days (n=3) | 172.3 | 203,398 | 70.8 | 3.94 |
| last 72 h | 163.0 | 195,574 | 68.1 | 3.78 |
| last 48 h | 72.5 | 119,892 | 41.7 | 2.32 |
| last 24 h | 63.0 | 122,070 | 42.5 | 2.36 |

**Headline as a band, not a point: ~40–70 MiB/year raw, ~2.3–4.0 MiB/year compressed, per
machine**, at today's run rate and today's row size. A 1.7× spread driven purely by which
window is chosen.

Scaling linearly across a fleet:

| machines | raw MiB/yr | zstd-19 MiB/yr |
| ---: | --- | --- |
| 1 | 42 – 71 | 2.3 – 3.9 |
| 10 | 417 – 708 | 23 – 39 |
| 20 | 834 – 1,416 | 46 – 79 |

**Bottom line for the decision: even the pessimistic end is small.** At 20 machines the
compressed annual union is well under 100 MiB, and §7 says a committed append-only shard
costs ~1.4× that. **Storage volume is not a reason to reject per-machine shards in git.** If
that option is rejected it should be on write-contention, merge-conflict, or provenance
grounds — not size.

## 9. Inherited caveat — attributed, not verified here

`2026-08-06-validate-ledger-multi-machine-scoping.md` records that a run with
`DEV_HERMIT_PARENT` unset skips the append entirely (`ci-hub/validate/aggregate.py:12-18`),
so this ledger already sees only a *fraction* of the machine's real runs. **I did not
re-verify that claim in this session — it is attributed and UNVERIFIED here.** If it holds,
every rate in §8 is a **lower bound** on true per-machine validate volume.

For continuity: that artifact measured 635 rows / ~697 KB earlier on 2026-08-06. Today's
650 rows / 724,904 B is consistent with the slow 08-06 → 08-07 tail (57 then 6 rows/day).
