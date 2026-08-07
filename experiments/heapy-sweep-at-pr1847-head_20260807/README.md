# heapy sweep at PR #1847's head: 8 of 8 cells identical to main-side

**Agent:** hermit-w2 · **2026-08-07** · **Guest:** `heapy` (`-O0 -static -nostdlib -ffreestanding`)
**Builds:** main-side `86842f741` vs PR #1847 head `077833ad` (both with the constructor-enabled
LiteInst DSO staged via `scripts/stage-liteinst-runtime.sh`)

## Result: measured, not inferred

The previous artifact
(`experiments/liteinst-selfdeterminism-cells-heapy_20260807`) stated that the maps-inode fix
*cannot plausibly* change these cells because `heapy` is freestanding and never reads
`/proc/self/maps` — and explicitly flagged that as **reasoning, not measurement**. It is now
measured, and it holds.

| backend | dimension | main `86842f741` | PR head `077833ad` |
|---|---|---|---|
| ptrace | stdout | 0/0 vacuous | 0/0 vacuous |
| ptrace | detlog | 40/40 | 40/40 |
| ptrace | stack | 9/9 | 9/9 |
| ptrace | heap | 8/8 | 8/8 |
| liteinst | stdout | 0/0 vacuous | 0/0 vacuous |
| liteinst | detlog | 4/4 | 4/4 |
| **liteinst** | **stack** | **0/0 vacuous** | **0/0 vacuous** |
| liteinst | heap | 0/0 vacuous | 0/0 vacuous |

**8 of 8 cells identical.** The fix changes nothing here, exactly as predicted — and for the stated
reason: with no dynamic loader, `heapy` never opens `/proc/self/maps`, so the determinized inode
column is never read into guest memory.

This is the complement to the `notsc` result: the same fix takes liteinst stack from 110/410 to
410/410 on a dynamically-linked guest and leaves it at 0/0 here. **A LiteInst stack verdict is not
well-formed without naming its guest.**

## Guards applied

Native precheck (`./heapy`, rc=0) before scoring, so a build failure cannot masquerade as 0/0.
Zero-hash extraction is refused as a no-result rather than scored. Run-to-run count mismatch would
be reported as *structure moved*; it did not occur. Comparison uses `awk -F'\t'`, not the default
whitespace FS that silently broke an earlier detlog scoring pass.

## Limits

One host, one guest, one run pair per backend — presence, not flake rate. Stack/heap/detlog/stdout
only. The ptrace control here disagrees with the published ledger's ptrace row on all four
dimensions, so these cells form a self-consistent set of their own and must not be merged into that
table.
