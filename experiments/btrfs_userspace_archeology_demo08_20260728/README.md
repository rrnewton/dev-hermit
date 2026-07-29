# Demo 08 — hermit deterministically reproduces a historical, fuzzer-found btrfs-progs userspace bug

**Task:** `demo08-btrfs-userspace-archeology-repro`. Date: 2026-07-28.
Builds on track B (btrfs userspace under hermit): this adds *historical-bug
archeology* + a *pinned-scheduling deterministic repro* with a live differential
between the buggy and fixed revisions.

## The bug (archeology + provenance)

- **What:** `btrfs check` aborts via `BUG_ON('eb->refs < 0')` in
  `extent_io.c:free_extent_buffer_internal` while opening a crafted filesystem.
  A double free of a cached extent buffer during `open_ctree()`:
  `btrfs_read_sys_array()` creates and releases an eb for `[64K,68K)` (which
  overlaps the primary superblock physical range `[61440,77824)`), then the
  fuzzed `log_root` bytenr `61440` drives `read_tree_block()` →
  `alloc_extent_buffer()` to free that already-zero-refcount cached eb again.
- **Class:** purely self-contained **userspace FS logic** — btree / disk-io /
  extent-buffer cache. No kernel, no mount, no QEMU. Exactly the track-B target
  class (btree/extent/chunk/disk-io).
- **Found by a fuzzer:** reported as **kdave/btrfs-progs GitHub issue #207**
  ("BUG_ON `eb->refs < 0` triggered in extent_io.c") against a crafted/minimal
  crashing image. Reporter tested **v5.2.1** (Arch) and **v5.2.2** (`55a8c962`).
- **Fix commit:** `6a061158617f3aa670df861c912ef76d11aa69e4`
  *"btrfs-progs: disk-io: Verify the bytenr passed in is mapped for
  read_tree_block()"* (Qu Wenruo, 2020-01-09; first released in **v5.4.1**).
  The fix stops polluting the eb cache when reading the sys chunk array, so the
  corrupt log_root errors out cleanly at the logical-mapping phase instead of
  double-freeing.
- **Buggy revision used:** `3dcce48fd7038efbf0c40707d3ff26c1c080ae50` — the
  **immediate parent** of the fix (`btrfs-progs v5.4`). Adjacent buggy/fixed
  pair ⇒ the only difference between the two binaries is the fix itself.

## Reproducer image

- `crash.btrfs`, sha256 `9a2e51910bfcb0c98817e4db8081477688ea26e6af75ac076769a0820f3b51ee`,
  114,294,784 bytes (mostly sparse). Not committed (binary; gitignored under
  `ignored/`). Re-fetch:
  `curl -L -o crash.zip https://github.com/kdave/btrfs-progs/files/3639876/crash.zip && unzip crash.zip`
  (the `crash.zip` attachment on issue #207).

## Result

| revision | hermit mode | exit | guest output (sha256/32) | meaning |
|---|---|---|---|---|
| **buggy** `3dcce48f` | `--strict --seed 1` | **134 (SIGABRT)** | `59610be8d8ba911e250b6c069f7ff971` | BUG_ON abort |
| **buggy** `3dcce48f` | `--chaos --chaos-target-races …` | **134 (SIGABRT)** | `59610be8d8ba911e250b6c069f7ff971` | BUG_ON abort |
| **fixed** `6a061158` | `--strict --seed 1` | **1** | `641cd8d6f21ead2d56977ef7be355e68` | graceful "cannot open file system" |
| **fixed** `6a061158` | `--chaos …` | **1** | `641cd8d6f21ead2d56977ef7be355e68` | graceful |

- **Deterministic + bitwise-reproducible:** repeating each buggy config produces
  a byte-identical guest transcript (same sha). hermit pins the address-space
  layout, so even the crash backtrace addresses are stable across runs —
  including the libc frame `/lib64/libc.so.6(+0x2a610)[0x7ffff7c2a610]`, which is
  ASLR-randomized on a native run. The btrfs frames match commit `6a061158`'s
  documented [CAUSE] stack (`free_extent_buffer → alloc_extent_buffer →
  btrfs_find_create_tree_block → read_tree_block → btrfs_setup_all_roots →
  open_ctree_fs_info → main`).
- **Differential:** the fixed binary, run under the identical hermit config,
  does **not** abort — it emits `Invalid mapping for 61440-77824 …
  ERROR: cannot open file system` (exit 1). The `61440-77824` range is exactly
  the fuzzed log_root / superblock overlap named in the fix commit.

## Exact repro command

```bash
ROOT=~/work/dev-hermit
H=$ROOT/hermit/target/release/hermit          # hermit 967abd99 (this run)
IMG=$ROOT/ignored/demo08-repro/crash.btrfs    # issue #207 crash.zip

# --- buggy revision aborts (SIGABRT, exit 134), bitwise-reproducible ---
$H run --strict --seed 1 -- $ROOT/ignored/bp-buggy/btrfs check "$IMG"
# chaos variant, identical outcome + also bitwise-reproducible:
$H run --chaos --chaos-target-races --sched-seed 1 --rng-seed 1 \
      --target-timeslice 100000 --max-timeslice 1000000000 \
   -- $ROOT/ignored/bp-buggy/btrfs check "$IMG"

# --- fixed revision handles it gracefully (exit 1) under the SAME config ---
$H run --strict --seed 1 -- $ROOT/ignored/bp-fixed/btrfs check "$IMG"
```

`demo.sh` runs all six invocations through the scoped killer and self-checks the
four assertions (bitwise repro ×2, issue-#207 BUG_ON present, graceful
differential). Recorded transcripts are in `outputs/`.

## Honest scope note

This is a **deterministic single-threaded logic bug**, not a data race: the
crash path (`open_ctree`) runs on one thread, so `--chaos`/`--chaos-target-races`
and the timeslice knobs do **not** change the outcome — the buggy strict and
chaos transcripts are byte-identical (`59610be8…`). hermit's contribution here is
*deterministic, bitwise-faithful reproduction from a pinned config* (stable
addresses, exit, and transcript), plus a clean differential against the fix — not
interleaving exploration. Chaos/seed/timeslice are recorded per the task, and the
config is the canonical handle for the repro. (The interleaving-exploration
capability was established separately on the one multithreaded btrfs-progs path,
`rescue chunk-recover` — see the track-B fuzz-cases experiment.)

## Reproduce from scratch

```bash
cd ~/work/dev-hermit/ignored
# 1. clone history + create adjacent buggy/fixed worktrees
with-proxy git clone https://github.com/kdave/btrfs-progs.git btrfs-progs-git
git -C btrfs-progs-git worktree add --detach ../bp-buggy 3dcce48f
git -C btrfs-progs-git worktree add --detach ../bp-fixed 6a061158
# 2. deps (once): autoconf automake libtool libblkid-devel libuuid-devel \
#    zlib-devel lzo-devel e2fsprogs-devel
# 3. build each: ./autogen.sh && \
#    ./configure --disable-documentation --disable-python --disable-zstd --disable-libudev && \
#    make -j"$(nproc)" btrfs
# 4. fetch reproducer (see "Reproducer image") into demo08-repro/crash.btrfs
# 5. run: bash ~/work/dev-hermit/experiments/btrfs_userspace_archeology_demo08_20260728/demo.sh
```

## Files

- `demo.sh` — self-verifying driver (exact configs + assertions).
- `metadata.json` — provenance (SHAs, hashes, host, toolchain).
- `outputs/*.guest.txt` — recorded guest transcripts (buggy abort + fixed error).
