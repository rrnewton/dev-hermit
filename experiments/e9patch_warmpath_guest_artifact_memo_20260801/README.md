# e9patch warm-path perf: guest ELF + rewrite-artifact digest memo (PR #1459)

## Question

The owner flagged e9patch as "suspiciously slow." e9patch has **zero runtime
overhead** — `hermit run --backend e9patch` is a one-time AOT e9tool rewrite plus
a **plain ptrace runtime** (`runtime_backend()` maps `E9patch→Ptrace`). So all
per-run cost is in `hermit::e9patch::prepare`. #1436 (merged, `c7611ea7`) removed
the tool-binary re-hash on the warm path. **Does any dumb per-run cost remain on a
cache hit, and does the rewrite cache reliably HIT run-to-run** (a cache that
never hits would pay the full e9tool rewrite every run)?

## Method

Build hermit at the #1459 branch tip (`a140c72e`, base origin/main `55fd251b`,
reverie `028fe523`); freestanding 24 MiB static guest with one patchable
`syscall` site (`/var/tmp/biggp`). Isolate a scratch `HOME` so the rewrite cache
is controlled. Measure `preprocess_us` (banner stat) and wall time on a cold cache
(empty) then three warm runs (cache populated). Confirm `rewrite_cache` /
`instruction_map_cache` state and that the produced `artifact_sha256` is identical
across cold and warm (no re-rewrite, byte-identical output).

```bash
cd worktrees/e9patch/hermit
cargo build -p hermit --features e9patch --bin hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
HOME=$(mktemp -d /var/tmp/e9perf.XXXXXX) \
  target/debug/hermit run --backend e9patch -- /var/tmp/biggp   # cold, then repeat for warm
```

## Results

| run | cache | preprocess_us | wall_s | rewrite_cache | artifact_sha256 |
|---|---|---|---|---|---|
| cold | empty | 1,274,313 | 1.29 | miss | f70c819d…217b2 |
| warm1 | populated | 961 | 0.02 | **hit** | f70c819d…217b2 |
| warm2 | populated | 929 | 0.02 | **hit** | f70c819d…217b2 |
| warm3 | populated | 1,048 | 0.02 | **hit** | f70c819d…217b2 |

- **Warm preprocess ≈ 1 ms** (down from ~1.8–1.9 s pre-#1459 on the same guest,
  measured via the `HERMIT_E9PATCH_NO_DIGEST_CACHE` toggle in the PR) →
  **~1800–2000×** on a 24 MiB guest.
- **The cache HITS reliably every warm run** (`rewrite_cache=hit`,
  `instruction_map_cache=Hit`); there is **no dumb re-rewrite** per run.
- **`artifact_sha256` is byte-identical across cold and warm** — the memo path and
  the full-hash path produce exactly the same rewritten artifact.

## Interpretation

After #1436 + #1459 there is **no remaining dumb slowness on the warm path**: a
cache hit costs ~1 ms (a couple of `stat`s + JSON metadata read, no O(binary-size)
SHA-256). The **cold** ~1.27 s is the genuine one-time e9tool rewrite of the
24 MiB binary — inherent to AOT rewriting, not a defect — and it is amortized
because the cache reliably hits on every subsequent run. The pre-#1459 waste was
three O(binary-size) SHA-256 passes (guest read+hash, snapshot-copy re-hash,
artifact re-hash) executed on **every** run even on a cache hit; #1459 replaces
the guest and artifact re-hashes with `(len, mtime)` trust stamps under the same
private-cache trust model, and `fast_snapshot` bypasses the snapshot-copy re-hash
entirely on a warm hit.

## Provenance

- Hermit branch `codex/e9patch-warmpath-guest-artifact-memo` @ `a140c72e`
  (base origin/main `55fd251b`, reverie dep `028fe523`). Draft **PR #1459**,
  follow-up to merged **#1436** (`c7611ea7`).
- Host: 316-core devbig, debug hermit binary. Guest: `/var/tmp/biggp` (24 MiB
  static, one patchable syscall site).
- `results.csv` in this directory holds the raw measurements.
