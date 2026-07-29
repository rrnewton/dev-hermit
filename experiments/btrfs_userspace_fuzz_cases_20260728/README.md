# btrfs userspace fuzz — reproducible failure/divergence cases under hermit chaos

Task: `btrfs-userspace-fuzz-cases` (track B continuation; PoC established btrfs
check deterministic under chaos, 2229/2229). Date: 2026-07-28.

## Question

Run the btrfs-progs upstream fuzz corpus (and crafted corruption images) under
`hermit run --strict --chaos --chaos-target-races` with fixed seeds. Find
divergences, demonstrate each reproduces **bitwise** from its seed, and build a
library of reproducible btrfs-userspace failure/divergence cases.

## Setup

- btrfs-progs **v7.1** static multicall box: `ignored/btrfs-progs-v7.1-bin/btrfs.box.static`
  (sha256 `ed9a8815d12e40d2bf413d1259133a6727106715a2e5d8095179439b5d3f1467`).
  (The task named `ignored/btrfs-progs-track-b-272`, which does not exist; this
  is the same v7.1 static box produced by the track-B build.)
- hermit `967abd99bdc453e9ab9b6f118faaf5f9195bd12b`.
- Host: `Linux 6.17.13 x86_64`.
- Corpus: 46 upstream fuzz images
  (`ignored/btrfs-progs-v7.1/tests/fuzz-tests/images/*.raw.xz`, decompressed) +
  5 crafted images (`experiments/btrfs_userspace_logic_20260728/corpus/*.raw`).
  **All 5 crafted images are exact sha256 duplicates of upstream images**;
  **45 unique** after dedup (`corpus-keep.txt`, `corpus-dedup.txt`).
- Scoped process-group kills via `../btrfs_userspace_logic_20260728/run_scoped.sh`
  (no broad pkill).
- `.raw` corpus + `.scratch` device copies are gitignored / deleted (binaries).
  Corpus is regenerable by `xz -dc` of the upstream `.raw.xz` images.

## Method

`sweep.sh <label> <btrfs args… %IMG%>` runs, for each of the 45 unique images:
seeds 1, 2, 3, and a **repeat of seed 1** — each under
`hermit run --strict --chaos --chaos-target-races --seed <N>`, always copying the
image to the **same** scratch path first (so the embedded device path is not a
variable). It records the sha256 of each run's combined stdout+stderr and derives:
`repro_s1` (seed-1 == seed-1-repeat, bitwise) and `divergent` (distinct
signatures among seeds 1/2/3).

Four subcommands were swept (720 hermit runs total): one genuinely multithreaded
(`rescue chunk-recover`) and three single-threaded controls
(`check`, `rescue super-recover`, `inspect-internal dump-super -Ffa`).

## Results

| subcmd | threading | images | per-seed repro FAIL | cross-seed DIVERGENT | crashes/hangs |
|---|---|---|---|---|---|
| `rescue chunk-recover -y -v` | multi (scan + progress thread) | 45 | **0** | **38** | 0 |
| `check` | single | 45 | 0 | 0 | 0 |
| `rescue super-recover -y -v` | single | 45 | 0 | 0 | 0 |
| `inspect-internal dump-super -Ffa` | single | 45 | 0 | 0 | 0 |

Key findings:

1. **Per-seed determinism is total.** Across all 720 runs (4 subcmds × 45 images
   × 4 seeds), seed-1 vs seed-1-repeat is **bitwise identical every time** — zero
   reproducibility failures. hermit `--chaos` + fixed seed = deterministic replay.
2. **Cross-seed divergence appears ONLY in the multithreaded subcommand.**
   `chunk-recover` diverges across seeds on **38/45** images (the other 7 bail out
   early with an unreadable superblock and never reach the threaded device scan).
   The three single-threaded controls show **zero** divergence on any image —
   confirming the divergence is genuine thread scheduling, not measurement noise.
3. **The exposed race is the scan-thread ↔ progress-thread interleaving, and it
   is semantically benign.** After stripping `Scanning: …` progress lines, the
   divergent outputs are byte-identical: the scan result, recovered chunk items,
   and recovery verdict do not change. What varies is only how many progress
   ticks the reporter thread emits before `DONE`, i.e. whether it got a timeslice
   mid-scan. No memory-safety bug, wrong verdict, crash, abort, FPE, or hang was
   observed on v7.1 (this corpus's historical crashers are all hardened to fail
   gracefully with rc=1).
4. **The interleaving space here is small.** A wide sweep of one 128 MB image
   (`up__bko-200403.raw`, seeds 1–12) yields exactly **2** distinct signatures —
   the progress race is effectively binary (mid-scan tick present or absent).
   Adjacent seeds converge (as in the B3 icount study): e.g. seeds 1 and 3 match,
   2/4–9/12 match. A divergence hunt must span a range of seeds, not neighbors.
5. **Reproduction is path-pinned (sub-finding).** The exposed interleaving is
   sensitive to the scratch device-path *string*: its length/content perturbs
   process memory layout and instruction timing, which shifts chaos scheduling
   decisions. Verified: `up__bko-161811.raw` seed1-vs-seed2 diverges under the
   sweep path `runs/sweep_chunkrec/up__bko-161811.raw.scratch`
   (d4f744c4 vs 6bbb3b11, each bitwise-stable on repeat) but the SAME two seeds
   converge under a short `/tmp/scr-*.img` path. Strict bitwise reproduction
   therefore requires pinning every input including the device path — the case
   library records the exact `pinned_scratch_path` per case. This is expected of
   a true deterministic-replay tool, not a hermit defect.

## Interpretation

hermit's chaos scheduler **does reach and explore the real concurrency** in a
multithreaded btrfs-progs tool (`chunk-recover`) and pins each interleaving to a
reproducible seed — the exact capability track B needs. On the current corpus +
v7.1 that concurrency is a benign progress-reporting race, so the "failure
library" is a **schedule-divergence library**, not a crash library. To surface
semantically meaningful userspace races one would need either an older/unhardened
btrfs-progs or a subcommand with data-carrying worker threads
(e.g. multi-device `chunk-recover`, or `btrfs check` with a corrupted image that
drives its readahead pool). Both are natural next steps.

## Files

- `sweep.sh` — the per-subcommand seed sweep harness.
- `native-triage.txt` — native `btrfs check` outcome for all 45 (all rc=1).
- `corpus-keep.txt`, `corpus-dedup.txt` — dedup manifest.
- `results/*.tsv` — compact per-image sha/repro/divergence tables. High-volume
  per-run transcripts remain in the gitignored `runs/` directory.
- `cases/CASE_LIBRARY.tsv`, `cases/CASE_LIBRARY.md` — the 38 reproducible
  divergence cases, each with two seeds yielding two bitwise-stable interleavings.
- `metadata.json` — provenance.
