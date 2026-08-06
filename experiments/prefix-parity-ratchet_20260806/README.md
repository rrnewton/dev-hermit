# Prefix-parity depth Y/Z — the ratchet is pinned at the process prologue

Full write-up: `ai_docs/prefix-parity-depth-ratchet-ladder_20260806.md`.

**Result.** Eight rungs, Z from 145 to 400,940 (factor 2,765). Depths do not move:
**dbi 3, sabre 1, liteinst 8 — at every rung.** ptrace double-run is Z/Z everywhere (sanity).

So the metric currently measures a **process-prologue** divergence, not workload parity. Heavier
rungs cannot move it, and neither could fixing demo05. `3/400940` and `3/145` are the same fact.

**Unblock list (the #315 loop, same answer at every rung).**
- **dbi** — record 3: the raw host pid appears where the golden has `DetPid(3)` (and it changes every
  run, so dbi's DETLOG is not self-deterministic in those fields). Dominant blocker underneath is
  **address-space layout**: `brk(NULL)` returns `0x5555…` in the golden vs `0x7ffff7…` under
  DynamoRIO. Folding addresses collapses differing records 114 -> 16. See
  `dbi-fold-decomposition.csv` (DIAGNOSTIC; the ratchet number stays 3).
- **sabre** — record 1 (missing `DETLOG USER RAND`), but the real problem is a truncated trace:
  91 records against a 400,940-record golden.
- **liteinst** — record 8, guest stack address in `arch_prctl`.

**e9patch must never enter the ratchet as a score.** It reports `mapped_sites=0`, patches nothing,
runs the plain ptrace runtime, and would have scored a perfect `400940/400940`. Engagement is
asserted from each run's own output; a backend that cannot prove it engaged is NOT-ENGAGED.

**demo05** is excluded: its golden fails the self-determinism precondition (see
`ai_docs/demo05-golden-capture-fixed-and-residual-disqualification_20260806.md`). The `dd bs=1
count=N` rungs replace it — qualified, QEMU-free, tunable to any record count.

## Reproduction
```sh
TMO=600 python3 ratchet.py                       # full ladder
ONLY=true BACKENDS=dbi,sabre python3 ratchet.py  # subset
```
