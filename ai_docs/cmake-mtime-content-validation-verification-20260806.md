# cmake mtime-vs-content — independent verification addendum

**Task:** `cmake-trusts-mtime-not-content-so-a-truncated-artifact-is-permanent`
**Date:** 2026-08-06 · **Mode:** local verification only. No egress, nothing built, nothing deleted.

**This is an addendum, not a re-implementation.** The fix was implemented earlier today
(2026-08-06 03:29, opus-5): `experiments/cmake_mtime_vs_content_20260806/`, parent content commit
`407874b3` (local only — egress 403). 14 executed mutations, both-sides bracketed, cost measured.
Re-doing it would be the duplicate-mechanism defect the owner has repeatedly named. I verified its
load-bearing claims instead.

## Verified independently

| Claim | Verdict |
|---|---|
| Artifact exists and is runnable | **CONFIRMED** — `cc-atomic`, `verify-objects`, `repro.sh`, `results.csv`, `metadata.json`, `README.md`, `slowcc` |
| Corruption denominator is **1**, and the survivor is a **`.so` not a `.o`** | **CONFIRMED — and it is still live**: `worktrees/226/hermit/target/debug/build/reverie-dbi-89ddd5351296fa25/out/dynamorio-build/clients/lib64/release/libdrpoints.so`, **0 bytes**, mtime `2026-08-04 11:44` |
| The shipped mitigation misses it | **CONFIRMED at source** (below) |

This also corroborates my own sweep from the previous task, which found **zero** truncated
DynamoRIO `.o` files: the 2026-08-04 cluster decayed to one survivor, and that survivor is a shared
object, so an `.o`-only search cannot see it. Two independent sweeps agreeing is the reason to
believe the "one kill event, not a recurring condition" discriminator.

## The live gap, quoted

`hermit/validate.sh:872-878` (PR #1616):

```bash
function purge_zero_byte_objects {
    local root=$1 removed=0 f
    [[ -d $root ]] || { printf 0; return 0; }
    while IFS= read -r -d '' f; do
        rm -f -- "$f" && removed=$((removed + 1))
    done < <(find "$root" -type f -name '*.o' -size 0 -print0 2>/dev/null)
```

Two independent narrownesses, both load-bearing:

1. **`-name '*.o'`** — misses `.so`, `.a`, `.rlib`, `.lo`. **The only corrupt artifact on this box
   today is a `.so`, so the shipped purge is currently a no-op against the one real instance.**
2. **`-size 0`** — size is a *proxy* for "corrupt". A file truncated to N>0 bytes has a valid mtime,
   fails the size test, and links exactly as wrongly. An **ELF-magic** check (first 4 bytes
   `\x7fELF`) binds to the artifact's identity rather than to a correlate, and costs 0.25 s wall
   across 834 objects / 407 MiB — under 0.1 % of a 500-800 s validate.

That is the same Proxy Binding shape as everything else this week: the predicate keys on something
correlated with the condition instead of on the condition itself.

## Why the durable fix is the launcher, not the sweeper

The prior agent's D2 taxonomy is the part worth preserving: `.DELETE_ON_ERROR` **is** emitted by
cmake (line 5 of every generated `build.make`, confirmed in the live tree), and it works in three of
four kill shapes. It fails in exactly one — **the whole process group is SIGKILLed**, i.e.
`memory.oom.group=1` semantics, where make itself dies before it can clean up. That is precisely the
configuration I verified live in the runner earlier today (`memory.oom.group=1` set at both scope and
per-step level).

So: **detection sweeps the damage; the atomic compiler launcher prevents it.** `cc-atomic` compiles
to `$out.tmp.$$` and renames only on `rc=0 AND non-empty`, so a group kill leaves *no target at
all* — `gmake -q` returns 1 and make rebuilds, instead of returning 0 forever against a poisoned
0-byte file. `CMAKE_<LANG>_COMPILER_LAUNCHER` is a supported hook needing no DynamoRIO patch, and
`reverie-dbi/build.rs` already threads `CMAKE_GENERATOR`, so it is the same shape of change.

## Recommended order

1. **Widen the `find` in `validate.sh:877`** to `.o|.a|.so|.rlib|.lo` — one line, and without it the
   single live corrupt artifact stays invisible.
2. **Replace `-size 0` with the ELF-magic predicate** — catches truncated-to-N and binds to identity.
3. **Wire `CMAKE_C/CXX_COMPILER_LAUNCHER` to `cc-atomic`** — the durable prevention.
4. **sha256 sidecars only if 1-3 prove insufficient** — no corruption preserving a valid ELF header
   has been observed; ship behind the evidence.

Explicitly **do not** adopt "clean the build dir after any failure": a cold DynamoRIO rebuild is
~232 s and fails more often, and the F2 control showed the targeted validator preserves the warm
cache completely (4 of 4 skipped, `make: app is up to date`).

## One correction to the dispatch's framing

The dispatch asked me to add content-hash validation "for the 11 DynamoRIO/cmake objects the audit
identified" and to verify by truncating one. **Those 11 no longer exist** — the cluster decayed to a
single survivor, which my previous task's sweep and this one both confirm. There is no population of
11 to hash. The planted-mutation verification the dispatch wants was already performed against a
synthetic reproducer (F1: `checked=5 corrupt=1 rc=1` → rebuilt exactly 1 of 4; F2 control:
`corrupt=0 rc=0`, 0 of 4 rebuilt), which is the correct method precisely *because* the real
population is now empty.

## Provenance

| Claim | Status |
|---|---|
| Artifact contents; `libdrpoints.so` 0 bytes @ 2026-08-04 11:44; `validate.sh:872-878` predicate | **verified this session** |
| Zero truncated DynamoRIO `.o` on the box | **verified in the preceding task this session** |
| D1/D2 taxonomy, F1/F2/F3 brackets, 14 mutations, cost figures, 101→1 sweep | inherited from the 2026-08-06 03:29 note — **not re-executed** |
| PR #1616 number and state | inherited; **not verifiable — egress down** |
