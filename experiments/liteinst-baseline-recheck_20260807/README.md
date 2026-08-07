# LiteInst stack baseline re-measure: the fix is not in, and the baseline has not moved

**Task:** `re-measure-liteinst-baseline-after-self-scan-fix` · **Agent:** hermit-w2 · **2026-08-07**
**Host:** devbig014 · **Build:** `worktrees/cc/hermit` @ `86842f741` (read-only use; slot not mutated)
**Guest:** `scratch/w27-tsc/notsc.c`, `gcc -O0` (control — constant stamps; `rdtsc()` is defined but
never called)

## Headline

The task opens *"The `/proc/self/maps` self-scan virtual-time fix is in."* **It is not in.** The
baseline was re-measured anyway, as instructed, and it has not moved.

| | matching / denominator | first differing ordinal |
|---|---|---|
| ptrace (reference) | **44 / 44** | none |
| liteinst | **110 / 410** | **110**, contiguous tail |
| liteinst, prior measurement (`diagnose-sabre-liteinst-baseline-stack-nondeterminism`) | 110 / 413 | 110 |

**Verify clause NOT satisfied** — ordinals-matching did not reach n/n. But the reason is *not* that
the fix underperformed: **the fix never landed, so this baseline tested nothing about it.**

## Two premise corrections

**1. The figure is stated backwards in the task.** The dispatch says *"303/413 ordinals matching"*.
The source note says **303 of 413 differing** (73.4%), with ordinals 0-109 matching. Matching was
**110/413**, not 303/413. My re-measure gives **110/410** — the matching prefix is 110 in both
independent runs, so the correct figure to beat is 110, and "beating" it means reaching 410/410.

**2. It is not a virtual-time fix.** The change determinizes the **inode column**:
commit `de638e587` *"Determinize the inode column of guest-visible /proc/<pid>/maps"*, adding
`procfs_needs_maps_inodes` and `MapsSanitizeError` (`detcore/src/fd.rs`). Nothing about virtual time.

### The fix is unlanded

```
gh pr view -R rrnewton/hermit 1847
  {"state":"OPEN","isDraft":true,"mergedAt":null,"mergeCommit":null,
   "headRefOid":"077833ad65955b30309d40ac3105a135779c0dce",
   "headRefName":"fix/liteinst-maps-inode-nondeterminism-w23"}

git merge-base --is-ancestor 077833ad origin/main   # rc=1 -> NOT landed
```

No commit on the last 40 of `origin/main` mentions maps/inode/self-scan.

## The measurement

Two runs per backend, `--strict --base-env=minimal --detlog-stack`, hashes extracted from the INFO
DETLOG as `[stack]->HASH` and compared ordinal-by-ordinal.

```
ptrace     counts 44 vs 44     differing   0 / 44    matching  44 / 44
liteinst   counts 410 vs 410   differing 300 / 410   matching 110 / 410   first diff = ordinal 110
```

Counts are **stable run-to-run on both backends** (44 vs 44, 410 vs 410), so the schedule and event
sequence are deterministic; only hashed *content* moves. The liteinst divergence is a **contiguous
tail**: zero ordinals match at or after 110. One poisoning event, not diffuse drift.

### Mechanism re-confirmed independently, and bracketed

```
hermit run --backend <be> --strict --base-env=minimal -- /bin/cat /proc/self/maps   (x2)
  ptrace     0 / 25 lines differing
  liteinst  14 / 56 lines differing
```

All 14 are `/memfd:liteinst2-trampoline (deleted)` mappings, e.g.

```
run1: 70f80000-71000000 r-xs 00000000 00:01 195994  /memfd:liteinst2-trampoline (deleted)
run2: 70f80000-71000000 r-xs 00000000 00:01 309345  /memfd:liteinst2-trampoline (deleted)
```

**Masking only the inode field collapses the diff to 0 of 56.** That is the positive half of the
bracket: the inode is not merely *a* differing field, it is the *only* one — address range,
permissions, offset, device and pathname are all stable.

### The causal link, checked rather than assumed

Stack ordinal 110 — the first that differs — is the record emitted **immediately after** the guest
opens the file:

```
DETLOG [syscall][detcore, dtid 3] finish syscall #112: openat(-100, ... "/proc/self/maps", OFlag(O_CLOEXEC)) = Ok(3)
DETLOG [memory][dtid 3]  0x7ffffffde000-0x7ffffffff000 ... [stack]->65019c8ad78cde2e58fe83da9eb...
```

The host-global memfd inode is read into a guest stack buffer and then persists in dead stack, which
is why the signature is a tail and never recovers. This independently reproduces the prior
diagnosis on a different build.

Note the guest itself never opens `/proc/self/maps` — `notsc.c` only calls `getpid()`. The open comes
from the dynamic loader / libc, so **any dynamically-linked guest inherits this**, not just guests
that read maps deliberately.

## A trap for whoever tests PR #1847

`worktrees/w12chaos/hermit` is checked out on the fix branch and **has a built binary, but that
binary predates the fix**:

```
HEAD                     de638e587   <- the fix commit itself
binary --version         gc67774ddcac5
merge-base --is-ancestor de638e587 c67774ddcac5   -> rc=1   (fix NOT in the built binary)
```

Testing the fix with the binary sitting in the fix's own slot would report **"the fix does not work"**
— a false negative produced entirely by a stale build. `worktrees/pr1847/hermit` is detached at
exactly the PR head `077833ad` and has **no binary at all**; that is the slot to build in.

## What was NOT run, and why

Per the task's own sequencing (*baseline FIRST, then the TSC probe, then the ratchet*), and because
the baseline did not come back clean:

- **TSC-leak probe: NOT RUN.** It remains unfalsifiable for LiteInst for exactly the original reason —
  a control that is only 110/410 self-deterministic cannot attribute a stack-hash change to a TSC.
- **Parity ratchet: NOT RUN.** Gated on the probe.
- **PR #1847 not measured.** It is unlanded and unbuilt; building it is the `pr1847-branch-has-no-slot-owner`
  task's scope, not this one, and `worktrees/pr1847` carries another registered owner.

The fix does target the field this measurement isolates — that is a **source-level** observation
(`procfs_needs_maps_inodes` + the commit subject), not a measured one. **Expected post-fix result:
410/410 matching**, and the one-command pre-check is the maps reproducer reaching 0/56.

## Scope and limits

- **One host, one guest, one run pair per backend.** Speaks to the presence of the defect, not to a
  flake rate.
- Denominators differ slightly between measurements (410 here vs 413 prior, 44 vs 45 for ptrace);
  the *matching prefix is 110 in both*, which is the stable quantity.
- SaBRe was not touched. Its baseline is a **different cause** (121/121 differing from ordinal 0,
  maps byte-identical) and needs a Reverie-side change — do not conflate the two.
- No slot was mutated; `w12chaos` and `pr1847` were inspected read-only and no binary was built.

## Reproduction

```bash
gcc -O0 -o /tmp/notsc scratch/w27-tsc/notsc.c
for be in ptrace liteinst; do for r in 1 2; do
  hermit --log=info --log-file=/tmp/$be-$r.log run --backend $be \
    --strict --base-env=minimal --detlog-stack --tmp=/tmp -- /tmp/notsc
  grep -oE '\[stack\]->[0-9a-f]+' /tmp/$be-$r.log | sed 's/.*->//' > /tmp/$be-$r.h
done; paste /tmp/$be-1.h /tmp/$be-2.h | awk '$1!=$2' | wc -l; done
```
