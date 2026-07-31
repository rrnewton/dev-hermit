# KVM frozen-corpus parity re-measurement after the ThreadOwnership merge (2026-07-30)

## Question

The last authoritative examples scorecard
(`ai_docs/transient/2026-07-28-examples-cross-backend-scorecard.md`, hermit
`adbfaca`, reverie `f93bad17`) placed the KVM backend at **2/5** ptrace
guest-stdout parity on the frozen five-example corpus: it matched `rand.py` and
`timed-progress-bar.py` but diverged on `date.sh` (logical clock),
`devrand.sh` (random stream), and `race.sh` (thread schedule).

Between that snapshot and today, the Reverie `ThreadOwnership` refactor
(reverie #284 @ `89388d7`, single per-thread ownership enum; folds in the #284
CLONE_THREAD-through-Tool deadlock fix), the KVM PATH-resolve change (reverie
#268 @ `514c583`), the hermit consumer pin bump (hermit #1165 @ merge
`9cd955f9`), and other main evolution all landed. **Does KVM example parity
move, and does it cross the standing corpus target (>=50% byte-identical
stdout vs ptrace)?**

## Method

Replicates the 2026-07-28 scorecard method: single `--strict` runs (no
`--verify`, because ptrace suppresses guest stdout under `--verify` while KVM
emits it), byte-for-byte stdout comparison of KVM against ptrace, plus a
`--strict --verify` backend-local determinism check on the three
previously-diverging examples. See `run.sh`.

```bash
hermit run --strict -- examples/<EX>                 # ptrace reference stdout
hermit run --backend kvm --strict -- examples/<EX>   # KVM stdout
hermit run --backend kvm --strict --verify -- examples/<EX>   # L2 backend-local
```

Backend activation was confirmed (not a silent ptrace fallback) via
`hermit --log info run --backend kvm`, which prints
`INFO hermit::kvm: launching guest through reverie-kvm` and a
`detcore::scheduler` daemon startup, and via wall-time (KVM ~596 ms vs ptrace
~268 ms on `date.sh`).

## Snapshot

- Hermit: `9cd955f9ae5e71e861f5fa779d6235d6eb946718` (current
  `rrnewton/hermit:main`)
- Reverie dependency pin: `89388d7428da600f4dd280467048dd900e44ff30`
- Host: Linux `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, x86-64,
  AMD EPYC 9D85 158-Core Processor
- `/dev/kvm` present, mode `0666`
- Binary: `target/debug/hermit` (debug build)
- Level: L0 execution + backend-local L2 (`--strict --verify`) + ptrace
  guest-stdout parity comparison. Logging default; no relaxations.
- Bound: 60 s per cell. No cell timed out.

## Results

KVM guest-stdout parity vs ptrace, single `--strict` runs (stable across two
full passes):

| Example | ptrace exit | KVM exit | ptrace stdout SHA-256 (12) | KVM stdout SHA-256 (12) | Parity |
| --- | ---: | ---: | --- | --- | :---: |
| `date.sh` | 0 | 0 | `ded910b908b9` | `ded910b908b9` | MATCH |
| `devrand.sh` | 0 | 0 | `f5edcf77a864` | `f5edcf77a864` | MATCH |
| `race.sh` | 0 | 0 | `44f4a9c58373` | `44f4a9c58373` | MATCH |
| `rand.py` | 0 | 0 | `e1b8db378cfd` | `e1b8db378cfd` | MATCH |
| `timed-progress-bar.py` | 0 | 0 | `d8778ce33675` | `d8778ce33675` | MATCH |

**KVM ptrace-stdout parity: 5/5** (was 2/5 on 2026-07-28).

Backend-local `--strict --verify` on the three previously-diverging examples:

| Example | KVM `--strict --verify` |
| --- | --- |
| `date.sh` | exit 0 — "KVM guest output and exit status matched" |
| `devrand.sh` | exit 0 — "KVM guest output and exit status matched" |
| `race.sh` | exit 0 — "KVM guest output and exit status matched" |

`race.sh` now reproduces ptrace's alternating schedule (`bababa...`, SHA
`44f4a9c58373`, the 2026-07-28 *ptrace* value); the 2026-07-28 KVM run emitted
the divergent `200x'a' then 200x'b'` schedule (`f2d620d1...`).

## Interpretation

At current landed main, the KVM backend matches ptrace guest stdout on all five
frozen examples and is backend-local L2-deterministic on each. This crosses the
standing KVM corpus target (>=50% byte-identical stdout vs ptrace) and meets
the **B2.1 gate on the example set**.

**Attribution (not bisected):** the jump is the cumulative effect of main
evolution between `adbfaca` and `9cd955f9`. The `race.sh` schedule-parity gain
is plausibly attributable to the `ThreadOwnership` refactor (reverie #284),
which makes Tool-owned CLONE/thread dispatch follow children deterministically;
`date.sh` (logical-clock) and `devrand.sh` (random-stream) parity came from
other work that landed on main in the same window and is **not** attributed to
this agent's changes. Exact per-commit attribution was not measured.

**Scope caution (per `ai_docs/backend-maturity-model.md`):** 5/5 example parity
is a **B2.1** diagnostic on the example set, **NOT a B3 claim**. B3's
denominator is the full frozen ptrace corpus (the ~183-row C manifest), not
these five examples. This result promotes KVM off "B2 base / fails B2.1" on the
example evidence; it does not by itself establish B2.2+ or B3.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/kvm/hermit   # at 9cd955f9, reverie pin 89388d7
cargo build -p hermit-cli --bin hermit
bash ~/work/dev-hermit/experiments/kvm_corpus_parity_postmerge_20260730/run.sh
```
