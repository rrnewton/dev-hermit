# The validate ledger: what it actually is, and options for committing/backing it up

**Task:** `ledger-storage-and-offmachine-backup-options` · **Agent:** herdr-dev · **2026-08-06**

## 1. The facts, from source and from the file

**Path — from source, not from notes.** The literal exists in exactly one place:

```
ci-hub/lib/validate_status.rs:87
pub const LEDGER_REL: &str = "ignored/validate-run-ledger.jsonl";
```

Everything else resolves through it (`ci-hub.rs:3244 ledger_path()` joins it to the repo root; the
CLI `--ledger` flags only override it). So the path is `dev-hermit/ignored/validate-run-ledger.jsonl`
and the notes were right *this* time.

Why the name keeps being misremembered: `ignored/` also holds `validate-1580-ghdag.log`,
`validate-1609-d6a28771.log`, `validate-local-<epoch>.log` and a dozen more `validate-*.log`
siblings. Those are per-run *logs*, not the ledger. Only the `.jsonl` is the ledger.

**Format.** JSON Lines, one object per validate run, `0600`. Fifty distinct fields observed.
The load-bearing ones: `commit`, `result`, `schema_version`, `selection_mode`, `commit_anchored`,
`tree_dirty`, `executed_tests`, `filtered_tests`, `coverage`, `full_coverage`, `gates*`, `host`,
`repo`, `started_at`/`finished_at`, `real/user/sys_seconds`, `log_file`.

**Size and growth, measured today:**

| | |
| --- | --- |
| Size | 647,388 bytes (632 KiB) |
| Records | 607 |
| Mean record | ~1,067 bytes |
| Span | 2026-08-03 → 2026-08-06 (4 days) |
| Per day | 127 / 367 / 91 / 22-so-far |

The 367 was a drain burst, so the 152/day mean is skewed. A **median day is ~100–130 records**.

Projection at ~1 KB/record:

| Basis | Per day | Per month | Per year |
| --- | --- | --- | --- |
| Median day (~115) | ~120 KiB | ~3.5 MB | ~42 MB |
| Mean incl. burst (152) | ~158 KiB | ~4.6 MB | ~56 MB |

**This is small.** Even the pessimistic basis is under 60 MB/year of highly compressible text — git
will pack it to a fraction of that. Storage is not the constraint; history hygiene is.

## 2. The wrinkle is already solved by the existing format

The owner's concern is that landing branches contribute soft-green points that later firm to hard
red or green, so the format must allow **refining a point without rewriting history**.

**It already does, and the data proves it is the normal case:**

- 257 distinct commits across 607 records.
- **166 of those 257 commits already have more than one record** (max 11 for a single commit).
- The reader does not treat a record as the answer. `validate_status.rs` folds *all* records for a
  commit into a verdict, so a later record refines the earlier one by existing.
- The schema already carries refinement vocabulary: `reclassified_reason` (present on 3 records),
  `solo_rerun_of` / `solo_rerun_confirmation`.

So a soft green firming to hard red is **an append**, not an edit. No format change is needed for
this requirement, and any proposal that introduces mutation would be a regression against what is
already working.

**One real caveat for any exported format:** five schema versions coexist in the current file
(v1×76, v2×20, v3×346, v4×116, v5×49). Anything that reads the committed copy must tolerate mixed
versions in one file. A consumer that assumes the newest schema will silently mis-read the majority
of the history.

## 3. Options

All assume the owner's frame: append-only, monotonic, **main-only**, per-branch/PR stays
uncommitted.

### Option A — commit the JSONL as-is to the parent, main-only

Add `ignored/validate-run-ledger.jsonl` as a tracked path (it is currently inside a gitignored
directory, so this needs an explicit un-ignore or a move to a tracked path).

- **For:** zero new code; the file already has the exact append-only shape wanted; git diffs are
  clean single-line additions; full history for free; off-machine backup is just `git push`.
- **Against:** every agent's validate run dirties the shared parent tree, so it collides with the
  "commit only your task's paths" discipline and will produce constant merge conflicts at the tail
  of the file — a hundred appends a day from concurrent agents all touching the last line.
- **Verdict:** simplest, and the conflict problem is real but *mechanical* — appends to distinct
  lines resolve by union. Would need a merge driver (`union`) declared in `.gitattributes`, which is
  a one-line change and exactly what union-merge exists for.

### Option B — commit a periodic *snapshot*, not the live file

A tracked `ledger/` directory receiving a daily (or per-landing) roll-up committed by one owner —
the coordinator or the lander — rather than by every agent.

- **For:** removes the concurrency problem entirely; one writer; keeps the live file machine-local
  and unchanged; natural place to normalise the five schema versions on export.
- **Against:** introduces a job that can silently stop, which is the inert-guard failure this fleet
  keeps finding. Needs the snapshot to record *what it covered* so a gap is detectable, not just the
  data.
- **Verdict:** good if and only if the snapshot carries its own coverage claim (first/last record
  timestamp and count) so a missed run is visible rather than inferred.

### Option C — main-only, append-on-land

The lander appends the records for the commit it just landed, as part of landing.

- **For:** naturally main-only, which is precisely the owner's frame; one writer; the append happens
  exactly when a verdict becomes meaningful; no daemon to rot.
- **Against:** records for commits that never land are dropped — which is *most* records, since
  soft-green refinement happens on branches. That may be acceptable (main-only is the stated intent)
  but it should be a conscious loss, not a surprise: today's file is majority non-main data.
- **Verdict:** best fit for the stated frame, worst fit for "keep the refinement history", because
  the refinement mostly happens before landing.

### Option D — leave it uncommitted; back up off-machine directly

Ship the file (or its appends) to durable storage outside git — object store, or a scheduled
rsync/upload.

- **For:** no repo-hygiene cost at all; no conflicts; keeps a 42–56 MB/year stream out of git
  history forever.
- **Against:** loses the property the owner actually wants, which is that the ledger travels with
  the repo and is reviewable alongside it. Also introduces a credential and an endpoint.
- **Verdict:** the right answer only if the goal is disaster recovery rather than reviewability.

## 4. Recommendation

**A + union merge, scoped main-only by C's trigger.** Concretely: keep the live file where it is,
and have the landing path append the landed commit's records to a tracked
`ledger/validate-main.jsonl` with a `union` merge driver. That gets the owner's stated shape
(append-only, monotonic, main-only, in the parent), needs no daemon, and the union driver turns the
one real objection to A into a non-event.

Two things to decide explicitly rather than by default:

1. **Do pre-landing refinement records travel?** Under a main-only rule they do not, and that
   discards the majority of the current file — including most of the soft-green→firm transitions the
   wrinkle is about. If the point is to be able to audit *how* a verdict firmed, main-only defeats
   it. If the point is a durable record of what main was, main-only is correct.
2. **Normalise schema on export, or preserve v1–v5 verbatim?** Preserving is honest and cheap;
   normalising is friendlier to consumers but rewrites what was actually recorded. Preserve, and put
   the version-tolerance burden on the reader.

Size is not a factor in any of this — at ~3.5 MB/month compressible text, all four options are
affordable. Choose on history hygiene and on which of the two questions above matters more.
