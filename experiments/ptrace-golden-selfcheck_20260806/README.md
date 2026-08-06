# The ptrace reference passes its own self-check at INFO depth — the disqualification list is empty

**Date:** 2026-08-06 · **Task:** `double-run-determinism-of-the-ptrace-reference-per-rung` · Local only.

## The deliverable: DISQUALIFIED RUNGS = **none**

Every rung, both rung sets, **N=5 independent pairs each, INFO-log compared (not stdout)**:

| set | rung | Z (COMMITs) | pairs clean | verdict |
|---|---|---:|---:|---|
| A — ratchet SHA `f89c69766` | `/bin/true` | 23 | 5/5 | **IDENTICAL** |
| A | `/bin/echo hello` | 52 | 5/5 | **IDENTICAL** |
| A | `wc -c /etc/hostname` | 53 | 5/5 | **IDENTICAL** |
| A | `sh -c 'echo a \| wc -c'` | 182 | 5/5 | **IDENTICAL** |
| B — pinned goldens `4c70658e7` | `/bin/true` | 5 | 5/5 | **IDENTICAL** |
| B | `/bin/echo hermit-golden` | 6 | 5/5 | **IDENTICAL** |
| B | `cat /etc/hostname` | 7 | 5/5 | **IDENTICAL** |
| B | `wc -l /etc/passwd` | 7 | 5/5 | **IDENTICAL** |
| B | `head -1 /etc/passwd` | 7 | 5/5 | **IDENTICAL** |
| B | `sh -c 'echo a \| wc -c'` | 30 | 5/5 | **IDENTICAL** |
| B | `sh -c 'for i in 1 2 3; do echo $i; done'` | 45 | 5/5 | **IDENTICAL** |

**No rung is disqualified as a golden at INFO depth.** The ratchet is not chasing its own reference:
the dbi `0/Z` and sabre `2/Z` numbers are backend divergence, not reference noise.

Set B's Z counts (5, 6, 7, 7, 7, 30, 45) **match the pinned manifest exactly**, which is the
strongest available cross-check that I measured the same thing that was pinned.

## Coordination with the golden-log pinning task

That task already ran a self-determinism gate, and it passed. This is an independent, **stronger**
re-check, and it agrees:

| | its gate | this check |
|---|---|---|
| samples | 1 pair (`captured twice`) | **5 pairs per rung** |
| comparator | raw bytes after timestamp strip | same definition, independently reimplemented |
| binary | manifest release build | debug build at the same SHA (sha256 differs — stated) |

**Verdict: the seven pinned goldens are safe to keep pinned at INFO depth.** One statistical caveat
worth carrying: 0 divergences in 5 pairs leaves roughly a 45% one-sided 95% upper bound on a
per-pair flake rate. "IDENTICAL" here means *not observed to diverge in 5 pairs*, not *proven
deterministic*. If a golden is going to gate a ratchet long-term, its pair count should grow.

## The finding that actually matters for the ratchet's next step

**At DEBUG depth the reference is NOT self-identical — reproducibly, at both SHAs.** Same guest
(`/bin/true`), same binary, INFO identical, but the DEBUG capture differs on ~15 lines:

| SHA | differing DEBUG lines | classes |
|---|---:|---|
| `f89c69766` | 16 | timer `precise_ip` / `CpuId {…}`, vDSO patch lines, inject lines, wall-clock |
| `4c70658e7` | 15 | same |

Two distinct causes, and only one is benign:

1. `DEBUG detcore::tool_global: Nondeterministic realtime elapsed: 32.396ms` vs `34.362ms` —
   hermit's own wall-clock instrumentation, **self-labelled nondeterministic**. Benign.
2. `DEBUG reverie_ptrace::task: … beginning inject of syscall: execveat, args SyscallArgs { arg1:
   94069433606240, arg2: 94069433600512, … }` — **guest addresses formatted as DECIMAL**. The
   comparator canonicalizes host addresses ("ordinal by first appearance") but that normalization
   keys on hex, so decimal-rendered addresses sail through unnormalized.

**Why this is a live hazard rather than trivia:** the whole direction of this ratchet is
"deeper is stronger" — INFO-log depth was adopted precisely because stdout-only was too weak. The
obvious next deepening is full-trace. Anyone who takes that step will get **false DIVERGENT verdicts
on the reference itself**, and will be tempted to blame a backend. Fix the decimal-address
canonicalization *before* deepening past INFO.

## A comparator trap that cost me a wrong first answer

My first pass reported **all four rungs DIVERGENT**, and it was wrong. Two things caused it:

* `--verify-strict` **without** `--log` compares INFO messages. Adding `--log info` — which reads
  like it should *narrow* the capture — flips the comparator into `Comparing full trace messages`,
  i.e. it *widens* comparison to include DEBUG. Passing a log level changes what "strict" compares.
* The resulting divergences were entirely the two DEBUG classes above.

I only caught it by opening the failing log instead of trusting the verdict string. Anyone
automating this should compare the INFO capture directly (as `inforung.sh` here does) rather than
inferring depth from `--verify-strict`'s behaviour.

## Reproduction

```sh
BIN=<hermit at f89c69766> N=5 TAG=A RUNGSET=ratchet ./inforung.sh
BIN=<hermit at 4c70658e7> N=5 TAG=B RUNGSET=goldens \
  EXTRA="--base-env minimal -e LC_ALL=C -e TZ=UTC" ./inforung.sh
```

Limitations are recorded in `metadata.json` rather than glossed: Set A's Z counts do not reproduce
the ratchet doc's (its env and exact fork/exec guest are unrecorded, so my Set A rungs approximate
its ladder), and Set B used a debug build at the golden SHA rather than the manifest's exact
release binary.
