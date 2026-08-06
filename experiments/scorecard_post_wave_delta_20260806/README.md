# Post-wave standing: +79 cells, +0 verify-backed. The wave was DBI-only and landed entirely in the shallowest tier.

**Task:** `compat-scorecard-refresh-post-wave` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress. Standards: **#89** full-detlog parity, **#268** provenance.

## Could I regenerate? No — and the reason matters

A true refresh means re-running the corpus. Three blockers, none of which I can clear locally:

* the only built binary is `target/release/hermit` from **2026-08-03**, while `origin/main` is now
  `2c54dfb5` — roughly 300 commits later, so a refresh needs a rebuild first;
* **KVM livelocks at guest startup** on this host (reverie `640c5bc`);
* **SaBRe returns `unavailable` on all 7 of its cells** — it produces no data to refresh.

So 2 of 5 backends cannot contribute to a regeneration regardless of rebuild. What I did instead is
the readout the task actually wants, from data that already exists: **`compat-envelope/scorecard.csv`
is tracked in git**, so the delta is computable exactly, with provenance, without re-running anything.

## The delta (two real snapshots)

| | snapshot A `d83a34b` (2026-08-03) | snapshot B `13c791e` (2026-08-05, HEAD) |
| --- | ---: | ---: |
| rows | 534 | 618 |
| enabled cells | **101** | **180** |

| backend | enabled A→B | passing A→B | parity=1 A→B | **verify-mode A→B** |
| --- | --- | --- | --- | --- |
| **dbi** | **8 → 87** (+79) | **8 → 86** (+78) | **8 → 86** (+78) | **8 → 8 (+0)** |
| kvm | 7 → 7 | 3 → 3 | 3 → 3 | 7 → 7 (+0) |
| ptrace | 79 → 79 | 79 → 79 | 75 → 75 | 52 → 52 (+0) |
| sabre | 7 → 7 | **0 → 0** | 0 → 0 | 7 → 7 (+0) |
| liteinst | 0 → 0 | — | — | — |

**The entire wave is DBI, and the verify-mode column did not move for any backend.**

## What the 79 new cells actually are

| property | value |
| --- | ---: |
| new enabled cells | 79 |
| …in `verify` mode | **0** |
| …in `strict` mode (single run, nothing compared) | **79** |
| …newly claiming `deterministic=1` | **78** |
| …newly claiming `parity=1` | **78** |
| …with `reverie_sha = unknown` | **79** |

Provenance of all 79 (#268): three runs, each at a **PR-head SHA that is not on main**, each with an
unrecorded Reverie pin —

```
27  backend-parity-75edd7455dc9-1785909047   hermit=75edd7455dc9  reverie=unknown  mode=strict
26  backend-parity-52d56e5ceb38-1785912310   hermit=52d56e5ceb38  reverie=unknown  mode=strict
26  backend-parity-fc49593ac21c-1785914664   hermit=fc49593ac21c  reverie=unknown  mode=strict
```

So the wave grew **coverage breadth by +78%** (101 → 180 enabled) and grew **verified depth by zero**.
Every new `deterministic=1` came from a single passing run that compared nothing, and every new
`parity=1` is a stdout-hash comparison whose reference operand was not recorded.

This is not a criticism of the ratchet work — enabling 79 DBI cells is real progress on the *coverage*
axis, which is the axis with the most headroom. It is a statement about **which axis moved**, so the
next wave can be aimed deliberately.

## Current standing

| backend | enabled | pass | stdout-parity | **verify-backed** | **bitwise** |
| --- | ---: | ---: | ---: | ---: | ---: |
| dbi | 87 | 86 | 99% (86/87) | 8 | **0** |
| kvm | 7 | 3 | 75% (3/4) | 3 | **0** |
| ptrace | 79 | 79 | 100% (75/75) | 52 | **0** |
| sabre | 7 | 0 | n/a (0 measured) | 0 | **0** |
| **total** | **180** | **168** | **99%** | **63** | **0** |

Buckets: `backend-parity` 127, `c-programs` 26, `system-utils` 10, `language-runtimes` 6,
`determinism-stress` 5, `data-handling` 3, `applications` 2, `determinism-stress-c` 1.

**Read the 99% correctly.** It is *stdout-hash parity, over the 180 enabled cells, pooled across six
hermit SHAs, with the Reverie pin unrecorded for 96% of them.* The bitwise column is zero because
nothing in this path passes `--verify-strict`. Against the north star (#89, full
detlog-stack/heap/INFO parity) the honest standing is **0%**, and the blocker is not backend quality —
it is that DBI's DETLOG cannot be collected via `--log-file` at all
(`experiments/compat_scorecard_depth_20260806`).

## Premise check

The task says the refresh is warranted "now that DBI/KVM/liteinst/sabre/e9patch ratchets+parity
landed". Measured against the snapshots:

| backend | landed in this window? |
| --- | --- |
| DBI | **yes** — +79 enabled cells |
| KVM | no — 7 → 7, unchanged |
| liteinst | no — **0 enabled in both snapshots** |
| SaBRe | no — 7 → 7, still **0 passing** |
| e9patch | **not represented at all** — it is not one of the five backends in this CSV (there is a separate `e9patch-scorecard.csv`) |

**1 of 5.** Worth correcting before the number is quoted as a five-backend wave.

## What to aim the next wave at

1. **Depth, not breadth, for DBI.** It now has 87 enabled cells and 8 verify-backed. Promoting even 20
   `strict` cells to `verify` would move the axis that is currently at zero. Cost is honest: a verify
   cell is two runs.
2. **Unblock DETLOG collection under DBI** before promising any #89 number — otherwise the deepest
   measurable tier stays stdout.
3. **liteinst: enable the first cell.** Zero cannot be ratcheted, only enabled.
4. **SaBRe: 7 enabled, 0 passing** is an availability problem, not a parity problem — it should not sit
   in a parity readout as though it were being measured.
5. **Record `reverie_sha`** so the next wave's cells are bindable; all 79 of this one's are not.

## Limits

* No cell was re-run; this is a diff of two committed snapshots plus a read of the current one.
* The delta covers only what git records — if a snapshot was overwritten without a commit, that
  movement is invisible here. Only 2 commits have ever touched this file.
* "≈300 commits behind" is against `origin/main` `2c54dfb5` at audit time and will grow.
