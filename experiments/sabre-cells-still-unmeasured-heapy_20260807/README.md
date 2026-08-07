# SaBRe's 4 cells are still UNMEASURED: `patched_sites=0`, silent ptrace fallback

**Task:** option 1 of the follow-on list on `pr1847-did-not-deliver-the-predicted-baseline`
**Agent:** hermit-w2 · **2026-08-07** · **Build:** hermit `86842f741` (`worktrees/cc/hermit`)
**Guest:** `heapy` (`gcc -O0 -static -nostdlib -ffreestanding`)

## Result: the matrix stays at 16/20, not 20/20

SaBRe ran to `rc=0` twice and produced numbers that would score cleanly. **They must not be scored.**
The log shows the backend never engaged:

```
patched_sites=0
fallback        (x2)
```

This is the known silent-fallback trap: with zero patched sites SaBRe falls back to ptrace, so any
cell scored from that run measures **ptrace**, not SaBRe. The one detlog record it emitted is merely

```
INFO detcore::scheduler::runqueue: DETLOG SCHEDRAND: seeding scheduler runqueue with seed 0
```

— the scheduler seed line, present regardless of backend.

| backend | stdout | detlog | stack | heap |
|---|---|---|---|---|
| ptrace (control) | 0/0 vacuous | 40/40 | 9/9 | 8/8 |
| **sabre** | **ZQT** | **ZQT** | **ZQT** | **ZQT** |

ZQT = **zero qualifying trials**. Not a pass, not a fail, not vacuous-n=0 — the backend under test
did not run. Scoring `detlog 1/1 SELF-DETERMINISTIC` here would have been a fake green on n=1.

## A second reason these numbers cannot join the published ledger

My ptrace control disagrees with the ledger's ptrace row on **every** dimension:

| | stdout | detlog | stack | heap |
|---|---|---|---|---|
| ledger (`not-comparable-applied...20260807`) | 1/1 | 108 | 26 | 24 |
| this run | 0/0 | 40 | 9 | 8 |

Same guest *name*, different guest *binary* and/or build. **These cells therefore form a separate
self-consistent matrix; they cannot be pasted into the published table.** Any merge needs one agreed
`heapy` binary and one build, with the ptrace control reconciled first.

## Provenance caveat on the SaBRe artifact

No slot ships a SaBRe executable beside hermit. I used
`worktrees/227b/reverie/target/sabre/sabre` (read-only, via `HERMIT_SABRE_BINARY`), built from
reverie `73695ea`, while the hermit build pins reverie `dd3c178e`. That skew is **not** the cause of
`patched_sites=0` being disqualifying — a fallback run is unscoreable regardless — but a real SaBRe
measurement should use a matched artifact.

## What would actually close these cells

1. Build SaBRe from the pinned reverie (`dd3c178e`) and stage it beside hermit.
2. Use a guest SaBRe can patch — `heapy` is `-nostdlib -ffreestanding` with no PLT, which is the
   likely reason reach is zero. Confirm `patched_sites > 0` **before** scoring any cell.
3. Only then score the 4 dimensions.

**Engagement must gate scoring.** `rc=0` is not engagement.

## Limits

One host, one guest, one run pair. Main-side build only. The `patched_sites=0` reading is from the
run log; I did not separately instrument reach.
