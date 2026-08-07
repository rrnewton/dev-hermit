# Prefix-depth metrics: three definitions, one name, no conversion

**Verified from live source 2026-08-07** (hermit `590fcc9e` artifacts, parent `origin/main`).
Task `two-prefix-depth-metrics-exist-and-are-not-comparable`.

Every one of these produces a number shaped `Y/Z` against the ptrace golden and is
called a *prefix-parity depth*. **Nothing in the number reveals which produced it.**
The premise was two; there are three.

## The three definitions, side by side

| | **A — COMMIT-record depth** | **C — DETLOG-or-COMMIT depth** | **B — completed-syscall index** |
| --- | --- | --- | --- |
| what one record IS | a `COMMIT turn …` line (scheduler commit) | a line containing `DETLOG` **or** `COMMIT turn` | one completed guest **syscall** |
| source of truth | `ci-hub/parity/prefix_depth.sh:21-33` | `experiments/prefix-parity-ratchet_20260806/ratchet.py:38` (`REC = re.compile(r"DETLOG\|COMMIT turn")`) | reverie [#402](https://github.com/rrnewton/reverie/pull/402) evidence block |
| normalization | `sed -E 's/0x[0-9a-f]+/HEX/g'` — **all hex erased** | wall-clock prefix **only**; syscall values, counts, flags, addresses all significant | stack-region pointers masked (audited: 10 of 10 differing lines) |
| streams read | `cat <tag>.log <tag>.err` | log file, else stderr (dbi ignores `--log-file`) | INFO log |
| run flags | `--log=info run --backend <be>` | `--log=info` per rung | `--log=info run --backend {ptrace,kvm} --strict --detlog-heap --detlog-stack --tmp=/tmp` |
| "depth" means | longest identical **leading run of COMMIT records** | longest identical leading run of **DETLOG-or-COMMIT records** | index of **first control-flow divergence** among syscalls |
| declares itself? | yes — prints `metric=COMMIT-record prefix depth (INFO log)` | yes — header comment | yes — prose, in-table |

## The evidence that they are not comparable

**Same golden log**, `/bin/true`, ptrace, hermit `590fcc9e`, `--base-env minimal`
(`experiments/prefix-parity-commit-turn-2_20260807/true.ptrace.log`, 115 lines):

| | count |
| --- | ---: |
| Metric **A** records (`COMMIT turn`) | **5** |
| Metric **C** records (`DETLOG` or `COMMIT turn`) | **73** |
| — of which DETLOG-bearing | 68 |
| — lines containing **both** tokens | **0** |
| ratio Z_C / Z_A | **14.6×** |

The token sets are **disjoint**, so `C = A ∪ DETLOG` exactly: A is **6.8%** of C.

Now the same guest across published artifacts — **one guest, three published denominators**:

| published in | metric | env | Z for `/bin/true` |
| --- | --- | --- | ---: |
| `prefix-parity-depth-ratchet-ladder_20260806.md` | C | host env | **145** |
| `measurements/prefix-parity-depth-ratchet_20260806.md` (reproduced in memory) | A | host env | **14** |
| `experiments/prefix-parity-commit-turn-2_20260807` | A | `--base-env minimal` | **5** |

**29× spread on one guest**, all three legitimately labelled "prefix-parity depth `Y/Z`".

## Is there a conversion? **No — and not even in principle from the published numbers.**

Three independent reasons, each sufficient:

1. **Interleaving.** Depth is a *longest common prefix*, not a count. C inspects 68
   DETLOG records that A never looks at, so C's first divergence can occur at a
   DETLOG record that lies *before* A's next COMMIT record. Recovering Y_C from
   Y_A requires the full interleaved sequence — i.e. the raw logs, not the numbers.
2. **Normalization asymmetry, and it is not a rounding difference.** A erases every
   hex address; C keeps them. On this log **44 of 68 DETLOG records carry a `0x…`
   address (65%)** versus **0 of 5 COMMIT records**. So an address-only divergence
   is *invisible* to A and *fatal* to C. The two are therefore **not monotone in
   each other**: A can be deep exactly where C is 1.
3. **B counts a different object.** Guest syscalls, under a different command line
   (`--strict --detlog-heap --detlog-stack`), with a different mask. It shares only
   the `Y/Z` shape.

**"Not comparable" is the complete answer.** Do not build a conversion table.

## What to write wherever a depth is quoted

A depth is unqualified without all four:

```
depth = Y/Z  ·  metric = {COMMIT-record | DETLOG-or-COMMIT | completed-syscall}
             ·  hermit SHA  ·  guest  ·  env pin (--base-env minimal, or say "host env")
```

The env pin is not optional: Z is **64–86% env-attributable** (`/bin/true` 14 → 5,
`/bin/echo` 43 → 6 under `--base-env minimal`), so an unpinned denominator moves
when the *host* changes with no backend change, and the ratchet then reads as
progress nobody earned.

## Normalization discipline: what the KVM measurement actually did

The KVM depth (`experiments/prefix-parity-commit-turn-2_20260807`) records its metric in
`metadata.json`:

> `metric: Y = longest identical leading run of COMMIT-turn records; Z = golden ptrace COMMIT count; 0x hex normalised`

so it is **Metric A**, matching `prefix_depth.sh` — *not* the rung-ladder baseline, which is C.
It also discloses that it is a **reconstruction**, not the same script:

> `caveat: Reconstruction of the metric (ci-hub/parity/prefix_depth.sh has no liteinst/kvm arm)`

That is the honest form: it copied A's semantics and **said** it re-implemented rather
than invoked. Any new depth measurement must do the same or say it did not.

## Standing caveats on the current numbers

- KVM depth measured at hermit `590fcc9e`; `origin/main` has since moved (`294e89bf` and
  beyond) and it is **not re-confirmed**.
- **n=1 per guest/backend** for the KVM/liteinst rows.
- The rung ladder's finding still holds and is worth re-reading before quoting any depth:
  Z varies **2,765×** across rungs while **no backend depth moves** (dbi 3, sabre 1,
  liteinst 8 at every rung) — the ratchet measures a process *prologue*. `3/400940` and
  `3/145` are the same fact, so **a percentage is the wrong headline**.
- `prefix_depth.sh` concatenates `.log` then `.err`, so a backend emitting on both streams
  yields a "prefix" of a sequence that never occurred (order exposure, not magnitude) —
  documented in the script itself at lines 22-29.
