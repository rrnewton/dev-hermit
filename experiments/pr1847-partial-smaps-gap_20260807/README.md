# PR #1847: the prediction was right and the fix is still partial — `/proc/self/smaps` is uncovered

**Task:** `pr1847-did-not-deliver-the-predicted-baseline` · **Agent:** hermit-w2 · **2026-08-07**
**Fix build measured:** hermit `077833ad6595` (PR #1847 head) · **Pre-fix comparison:** `86842f741`
**Read-only:** no repository mutated, no slot evicted

## Actual vs predicted, side by side

The task is titled *"PR #1847 did NOT deliver the predicted 410/410"*. **On the measured cell it did.**
Recomputed independently from the landed artifacts (`bcede29`, `fb4e995`), not quoted from memory:

| cell | predicted | **actual** | gap |
|---|---|---|---|
| liteinst stack ordinals matching (`notsc`) | 410 / 410 | **410 / 410** | **0** |
| liteinst maps reproducer differing | 0 / 56 | **0 / 56** | **0** |
| ptrace stack control | — | 44 / 44 | — |
| planted-defect control (harness liveness) | — | 11 / 414 differing | harness not inert |
| pre-fix liteinst stack, for contrast | — | 110 / 410 matching | — |

**The gap on the predicted cell is zero.** The prediction was not adopted — it was re-derived from the
committed hash lists, and the planted-defect control confirms the harness could still have failed.

I also checked the way a false clean could arise — a fix that *removes the evidence* rather than
determinizing it. It does not: post-fix maps still carries **14 trampoline mappings across 56 lines**,
identical to pre-fix, with the same addresses. Only the inode column changed, from a host-global
counter (`195994`) to a small deterministic ordinal (`8`).

## But the fix is PARTIAL, and that is the real finding

The predicted cell is clean because `notsc` never reads `/proc/self/smaps`. **`smaps` carries the same
inode column and is not covered.** Measured on the fix build:

| procfs surface | pre-fix `86842f741` | post-fix `077833ad` |
|---|---|---|
| `/proc/self/maps` | 14 / 56 differing | **0 / 56** — fixed |
| `/proc/self/smaps` | 14 / 1456 differing | **14 / 1456 differing — NOT fixed** |
| `/proc/self/numa_maps` | 0 / 55 | 0 / 55 — no inode column, unaffected |

The leaking rows on the fix build still carry the raw host value:

```
70f80000-71000000 r-xs 00000000 00:01 157155      /memfd:liteinst2-trampoline (deleted)
5555554d4000-555555554000 r-xs 00000000 00:01 157156  /memfd:liteinst2-trampoline (deleted)
```

### Cause, named at source

`detcore/src/procfs.rs:592-594` gates the whole mechanism on one kind:

```rust
pub(crate) fn needs_maps_inodes(&self) -> bool {
    self.kind == ProcfsKind::Maps
}
```

and the dispatch at `:668-669` passes the inode list to exactly one arm:

```rust
ProcfsKind::Smaps => sanitize_smaps(&contents),                 // no inode determinization
ProcfsKind::Maps  => sanitize_maps(&contents, &maps_inodes)?,   // determinized
```

`ProcfsKind::Smaps` is a distinct variant (`:61`, matched at `:460`), so it never reaches
`sanitize_maps`. This is a scope gap, not a bug in the sanitizer.

## The gap has measurable consequence, not just a file diff

A guest that reads `smaps` into a live stack buffer (`smapsread.c`, same shape as `notsc.c`)
reproduces the **original tail-divergence signature on the fix build**:

```
ptrace     DIFFERING  0 / 51    MATCHING  51 / 51    smaps_read=1
liteinst   DIFFERING 15 / 417   MATCHING 402 / 417   first differing ordinal 402   smaps_read=1
```

Contiguous tail from ordinal 402, never recovering — the same shape as the pre-fix 110/410, just
entering later because the guest reads `smaps` late. `smaps_read=1` confirms the read really
happened, so this is not a vacuous cell.

**So the defect class is not eliminated; it is narrowed to guests that read `smaps` instead of `maps`.**

## A second-order effect the partial fix introduces

Pre-fix, `maps` and `smaps` both reported the same host inode for a mapping — nondeterministic but
**mutually consistent**. Post-fix, `maps` reports a small renumbered ordinal (`8`) while `smaps`
reports the raw host inode (`157155`) for the same mapping. Any guest reading both now sees **two
different inodes for one mapping**, which is a new inconsistency on top of the surviving
nondeterminism.

This is derived from the two per-file measurements above rather than a single-run co-observation:
the attempt to read both files in one run hit a **separate LiteInst defect** (below).

## Incidental defect found, not in scope

Multi-process shell guests fail under liteinst on the fix build:

```
ERROR reverie_ptrace::tracer: LiteInst cancellation cleanup failed pid=3
  error=notifier did not acknowledge terminal cleanup for LiteInst tracee 3
Error: LiteInst tracee cleanup failed after -524 ENOTSUPP (Operation is not supported)
```

Triggered by `/bin/sh -c` and `/bin/bash -c`; a direct `/bin/cat` is fine. Not investigated —
reported so it is not rediscovered as a measurement artefact.

## What remains, scoped

1. Add `ProcfsKind::Smaps` to `needs_maps_inodes()` and thread `maps_inodes` into `sanitize_smaps`.
2. **Use the same renumbering for both files**, or the cross-file inconsistency above becomes
   permanent. The two sanitizers must share one inode map per snapshot.
3. Audit the remaining inode-bearing surfaces before declaring the class closed — `SmapsRollup` is
   aggregate and likely safe, but `/proc/<pid>/map_files/` was **not** tested here and should be.
4. Re-run both checks after the change: maps `0/56`, smaps `0/1456`, and `smapsread` reaching
   `417/417`.

## Scope and limits

- **One host, one guest per check, one run pair per backend** — presence and removal of the defect,
  not a flake rate.
- Measured at PR head `077833ad`, **not rebased onto current main**; a rebase needs a re-measure.
- Only the stack dimension was scored; heap and detlog were not re-measured.
- `/proc/<pid>/map_files/` untested — listed as remaining work, not claimed either way.
- **SaBRe is a different cause and was not touched** (121/121 differing from ordinal 0, maps
  byte-identical, needs a Reverie-side change).

## Reproduction

```bash
gcc -O0 -o /tmp/smapsread smapsread.c
for be in ptrace liteinst; do for r in 1 2; do
  hermit --log=info --log-file=/tmp/sm-$be-$r.log run --backend $be \
    --strict --base-env=minimal --detlog-stack --tmp=/tmp -- /tmp/smapsread
  grep -oE '\[stack\]->[0-9a-f]+' /tmp/sm-$be-$r.log | sed 's/.*->//' > /tmp/sm-$be-$r.h
done; paste /tmp/sm-$be-1.h /tmp/sm-$be-2.h | awk '$1!=$2' | wc -l; done
```
