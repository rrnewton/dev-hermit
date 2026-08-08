# Why DBI cannot be patched to match ptrace's anonymous-mmap layout

**Date:** 2026-08-06 · **Host:** devbig014 · **Task:** `dbi_packs_anonymous_mmaps`

Third session on this defect. The first two established the reproduction, the
native oracle, and a hole-structure model. This one converts the **blocking
claim** from an assertion into a measurement, and names a second, separable
defect that hermit does own.

## The defect

Four successive NULL-hint anonymous `mmap`s of 1, 2, 3, 4 pages. The gap between
the 3rd and 4th differs by backend:

| arm | d01 | d12 | **d23** | cmp / word |
|---|---:|---:|---:|---|
| native ×2 | -2 | -3 | **-8** | identical |
| ptrace | -2 | -3 | **-8** | identical |
| **dbi** | -2 | -3 | **-4** | identical |

Pointer *order* agrees; only *spacing* diverges. **native == ptrace**, so ptrace
is faithful and DBI is the divergent arm — it packs tighter than Linux does.
Reproduced here on one binary
(`worktrees/dbi/hermit/target/release/hermit`).

## What actually causes -8

Exactly one arrangement, in ptrace's address space:

```
7ffff7fb0000-7ffff7fb3000   3-page anon block   <- glibc loader
        [ 7 free pages ]
7ffff7fba000-7ffff7fbc000   2-page anon block
7ffff7fbc000-               [vvar]
```

Filling top-down, 1+2+3 = 6 pages land in the 7-page gap and leave **exactly one
free page**. The 4-page request cannot fit, so it skips below the 3-page block
and lands 8 pages under `ANON[2]`. That single free page is the whole of `-8`.

## The measurement that settles the fix direction

The prior session concluded that relocating `reverie-dbi`'s pids memfd or DR's
vmheap "could not make it -8". That was an argument. It is now a measurement.

`gap-signature.py` searches an address space for the structure that produces -8
— a 5–9 page free gap sitting directly above a 2–4 page anonymous block:

```
maps.ptrace.txt:  25 VMAs,  6 gaps,  1 glibc-signature hole
                     7-page gap directly above a 3-page anon block at 7ffff7fb0000
maps.dbi.txt:    180 VMAs,  7 gaps,  0 glibc-signature holes
```

**Zero, across DBI's entire address space.** Relocating a mapping only chooses
among the gaps that already exist, and none of DBI's seven has the required
shape. So no relocation of any DBI-side mapping can produce -8. The blocker is
confirmed by evidence rather than by reasoning, and the question should not be
re-litigated a fourth time without new data.

## Why: DBI hands the guest a different address space, not a different policy

|  | ptrace | dbi |
|---|---:|---:|
| guest-visible VMAs | **25** | **185** (7.4×) |

The extra 160 are DynamoRIO's runtime: ~62 `---p` guard pages, ~40 `rw-p`, 15
`rwxp` code-cache mappings, plus `libdynamorio.so` and the `libdr*` extensions.
The guest's four allocations land in a large clean gap below
`/memfd:reverie-dbi-pids`, so they pack contiguously — `-N` for any `N` — while
ptrace's land in glibc's fragmented tail.

This is why there is no DBI `mmap` code path to correct:
`detcore/src/syscalls/files.rs:1275` `handle_mmap` passes the call through and
only records the resulting range. **Neither backend places anything; the kernel
does, from different starting conditions.** Making guest-visible spacing
backend-independent requires detcore to assign guest addresses from its own
allocator — a **new determinization strategy** (post-facto-review trigger 3)
touching the guest memory contract, which `CLAUDE.md` requires be discussed with
the owner first. Not a drive-by, and not done here.

Note it would also move **ptrace** away from native, since native == ptrace
today. Backend-independence and native-fidelity are not the same goal, and this
defect cannot satisfy both.

## A separable defect this census surfaced, which hermit does own

Nine of DBI's guest-visible mappings are **hermit's / reverie's own**, not
DynamoRIO's:

| mapping | VMAs | owner |
|---|---:|---|
| `libreverie_dbi_client.so` | 4 | reverie-dbi |
| `libdetcore_dbi.so` | 4 | hermit |
| `/memfd:reverie-dbi-pids` | 1 | reverie-dbi |

Under ptrace the guest sees **none** of these (`grep -c 'reverie\|detcore\|memfd'`
= 0). So a guest reading `/proc/self/maps` observes hermit's own plumbing under
DBI and not under ptrace — a guest-observable divergence that is **entirely
hermit-owned and independent of the placement question**.

Removing it will **not** fix `d23` (the zero-signature-holes result above rules
that out). It is worth filing on its own merits: it is the same defect class as
`HERMIT_DBI_DETCONFIG` leaking into the guest *environment* under DBI —
the backend's internals reaching guest-observable state.

## Status

**Not fixed, and not tagged `implemented`.** The task's acceptance is "the d23
case and the anonymous-mmap fixtures show identical page layout DBI vs ptrace".
Measured this session: DBI -4, ptrace -8. Tagging would assert a Verify
condition that demonstrably does not hold — the same call both prior sessions
made. `backend-parity-c/mmap-layout-pointer-order` must stay DBI-disabled;
enabling it would encode -4 as expected, which native says is wrong.

## Reproduction

```
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
H=worktrees/dbi/hermit/target/release/hermit
$H run --backend ptrace --strict -- ~/.local/hermit-deps/guests/mlo
$H run --backend dbi    --strict -- ~/.local/hermit-deps/guests/mlo
python3 gap-signature.py maps.ptrace.txt maps.dbi.txt
```

`allmaps.c` dumps the guest-visible VMA set for the 25-vs-185 census.
