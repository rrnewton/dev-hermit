# LiteInst self-determinism cells on `heapy`: the stack cell is 0/0 — the ratchet increment is ZERO

**Task:** `pr1847-did-not-deliver-the-predicted-baseline`, as retitled to *"Score the LiteInst stack
cells now scorable"* · **Agent:** hermit-w2 · **2026-08-07**
**Build:** `86842f741` (`worktrees/cc/hermit`, main-side — PR #1847 is **not** landed)
**Guest:** `heapy`, the ledger's guest (`gcc -O0 -static -nostdlib -ffreestanding`)

## The retitled premise does not hold on the ledger's guest

The retitle says *"LiteInst stack self-determinism now holds, so its stack cells move from
NOT-COMPARABLE to scorable."* Two corrections, both from measurement:

**1. LiteInst was never in the NOT-COMPARABLE set.** The published ledger
(`ai_docs/not-comparable-applied-to-the-published-scorecard_20260807.md`) records 12 measured cells,
5 of them NOT-COMPARABLE — **all 5 are kvm or dbi**. LiteInst is in a different bucket entirely:
*"Unmeasured, and not passing: sabre and liteinst — 2 of 5 backends, 0 of 8 dimension-cells."*
So the available class change is **UNMEASURED → measured**, not NOT-COMPARABLE → scorable.

**2. On `heapy`, the LiteInst stack cell is vacuous.** It emits **zero** stack records.

| backend | dimension | run1 | run2 | match/denom | verdict |
|---|---|---|---|---|---|
| ptrace | stdout | 0 | 0 | 0/0 | NOT-COMPARABLE — vacuous n=0 |
| ptrace | detlog | 40 | 40 | 40/40 | SELF-DETERMINISTIC |
| ptrace | stack | 9 | 9 | 9/9 | SELF-DETERMINISTIC |
| ptrace | heap | 8 | 8 | 8/8 | SELF-DETERMINISTIC |
| liteinst | stdout | 0 | 0 | 0/0 | NOT-COMPARABLE — vacuous n=0 |
| liteinst | detlog | 4 | 4 | **4/4** | SELF-DETERMINISTIC |
| **liteinst** | **stack** | **0** | **0** | **0/0** | **NOT-COMPARABLE — vacuous n=0** |
| liteinst | heap | 0 | 0 | 0/0 | NOT-COMPARABLE — vacuous n=0 |

> **Stack cells that became scorable: 0. The measured ratchet increment above the floor of 0 is
> ZERO.** Four previously-unmeasured LiteInst cells now have a class, and exactly **one** of them
> (detlog) is parity-emittable.

## Why this does not contradict the 410/410 result

Both are true and the difference is the **guest**:

| guest | liteinst stack records | verdict |
|---|---|---|
| `notsc` (dynamic, libc) | 410 | 410/410 SELF-DETERMINISTIC at PR head; 110/410 pre-fix |
| `heapy` (static, `-nostdlib -ffreestanding`) | **0** | vacuous — nothing to score |

`heapy` never loads a dynamic loader and never reads `/proc/self/maps`, which is the whole mechanism
#1847 addresses. **Scorability here is guest-dependent, and the ledger's matrix uses `heapy`.**

Note the denominator asymmetry on the one green cell: liteinst detlog is **4** records where ptrace
emits **40**. A 4/4 green on a 10× smaller denominator is a much weaker statement than 40/40 and
should not be read as equal standing.

## Two harness bugs found and fixed — both would have produced fake results

**1. A setup failure read as eight 0/0 cells.** `heapy` first built without `-nostdlib
-ffreestanding`, so the link failed (`multiple definition of '_start'`). Every run produced no log
and the harness scored **0/0 across all 8 cells** — including ptrace's. A setup failure is **zero
qualifying trials, not a negative result**. Caught because the ptrace control, which must be clean,
was not.

**2. `awk` default field separator silently broke every multi-word dimension.**

```bash
paste a b | awk '$1!=$2'     # WRONG: $1 and $2 are the first two WORDS OF LINE A
paste a b | awk -F'\t' '$1!=$2'   # correct: compares line A against line B
```

With the default FS this reported ptrace detlog as **0/40 — all divergent**, which is impossible for
the reference backend. Single-token files (the stack/heap hash lists) are unaffected because their
`$1`/`$2` happen to land on the two hashes, so **prior stack measurements in this session stand**;
only this sweep's detlog dimension was wrong, and it is corrected above.

Both were caught by the same rule: **the ptrace control must come back clean, or the harness is
wrong before the backend is.**

## Scope and limits

- **One host, one guest, one run pair per backend.** Presence, not flake rate.
- **Main-side build only.** The PR-head (`077833ad`) re-run of this sweep was **not done**. `heapy`
  is freestanding and never reads `/proc/self/maps`, so the maps-inode fix cannot plausibly change
  these cells — but that is reasoning, not measurement, and is flagged as such.
- SaBRe's 4 cells remain unmeasured; this closes 4 of the 8, not 8.
- `stdout` is 0/0 for **both** backends on this guest, so that is a guest property, not a backend
  finding.

## Reproduction

```bash
gcc -O0 -static -nostdlib -ffreestanding -o heapy heapy.c
./heapy; echo "native rc=$?"      # precheck: refuse to score if this fails
for be in ptrace liteinst; do for r in 1 2; do
  hermit --log=info --log-file=/tmp/$be-$r.log run --backend $be \
    --strict --base-env=minimal --detlog-stack --detlog-heap --tmp=/tmp -- ./heapy
done; done
paste a.d b.d | awk -F'\t' '$1!=$2' | wc -l    # note -F'\t'
```
