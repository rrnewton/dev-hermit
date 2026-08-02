# e9patch warm-path preprocessing optimization: tool-digest memo

## Question

The owner flagged the e9patch backend as "suspiciously slow." Where does the
time go, and what is dumb/broken?

## Attribution

`hermit run --backend e9patch` is **pure AOT preprocessing + a plain ptrace
runtime**: `runtime_backend()` maps `Backend::E9patch -> Backend::Ptrace`, and
the reverie-e9patch SIGSYS/hybrid runtime is dead code from hermit's
perspective. So **all** e9patch per-run cost is in `hermit::e9patch::prepare`.

Using a `/bin/true` control (which has zero candidate sites and returns from
`prepare_in` before the tool block) vs. a tiny static guest (which reaches the
tool block), 100% of the warm-path overhead was attributed to `prepare_in`
re-reading and SHA-256-hashing the `e9tool` (935 KiB) and `e9patch` (159 KiB)
executables **twice each** (source bytes + on-disk snapshot verify, via
`snapshot_binary`) on **every** invocation — purely to rebuild the
content-addressed rewrite-cache key. On a rewrite-cache **hit** the tools are
never executed, so ~2.2 MiB of hashing per run is wasted. Debug SHA-256 runs
~30 MB/s, so this is ~55 ms per warm run and ~1 min across a 385-guest sweep.

## Fix

Memoize the tool digests in a `(path,len,mtime)`-keyed sidecar cache (mirroring
`instruction_map`'s warm-cache model); defer the trusted snapshot copy to the
miss path where `e9tool` actually runs; rebuild the authoritative key from real
snapshot digests on a miss before writing metadata (no cache poisoning); keep
artifact content-verification before bind-mount+execute. Add `preprocess_us` to
the `:: Backend: e9patch ...` banner and a diagnostic
`HERMIT_E9PATCH_NO_DIGEST_CACHE` toggle for single-build A/B measurement.

## Method

Same debug build, same warm rewrite-cache, toggled only by
`HERMIT_E9PATCH_NO_DIGEST_CACHE`. 8 warm reps each. Guest: freestanding static
raw-syscall x86-64 (1 candidate site). `preprocess_us` read from the backend banner.

## Results

See results.csv. Warm-path `prepare` preprocessing:

- old (always snapshot tools): median ~55 ms (53.4–70.1 ms)
- new (digest memo):           median ~1.9 ms (1.9–2.0 ms)
- **~28x faster; ~53 ms saved per warm run.**

Cold-miss, warm-new-hit, and warm-old-hit all select the identical
`artifact_sha256=567c3409...` — the fast path is behaviorally identical.

## Correctness

- e9patch corpus: `RATCHET e9patch: 12/12 PASS_L2` (base corpus on main).
- e9patch unit tests: 15/15 (2 new: memo match/staleness; group-writable sidecar rejected).
- cargo fmt/clippy clean.

## Reproduction

```
cd worktrees/e9patch/hermit
cargo build -p hermit --features e9patch --bin hermit
export HERMIT_E9TOOL=.../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=.../reverie/third-party/e9patch/e9patch
H=target/debug/hermit; G=<tiny static guest>
$H run --backend e9patch --strict -- "$G"            # warm; note preprocess_us (fast)
HERMIT_E9PATCH_NO_DIGEST_CACHE=1 $H run --backend e9patch --strict -- "$G"  # old path (slow)
```
