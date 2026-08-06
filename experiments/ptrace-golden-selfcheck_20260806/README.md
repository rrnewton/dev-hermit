# Double-run self-determinism of the ptrace reference, per rung

**Date:** 2026-08-06 · **Task:** `double-run-determinism-of-the-ptrace-reference-per-rung` · Local only.

Two passes. The first (N=5 pairs/rung) found nothing and reported an empty disqualification
list. The second (N=300+ pairs/rung, **7100 INFO-depth pairs total**) found **9 divergent
pairs**, and that changes the deliverable. This document reports the deepened result; the
first pass's conclusions are marked where they are superseded.

---

## The deliverable: the disqualification list is NOT empty

A rung "diverges from itself" when two `--strict` runs produce different INFO logs. Under the
golden's current whole-log comparator:

| set | rung | env | pairs | divergent | verdict |
|---|---|---|---:|---:|---|
| **B — the 7 PINNED goldens** (`4c70658e7`, `--base-env minimal`) ||||||
| B | `/bin/true` | minimal | 300 | 0 | **QUALIFIED** |
| B | `/bin/echo hermit-golden` | minimal | 300 | 0 | **QUALIFIED** |
| B | `cat /etc/hostname` | minimal | 300 | 0 | **QUALIFIED** |
| B | `wc -l /etc/passwd` | minimal | 300 | 0 | **QUALIFIED** |
| B | `head -1 /etc/passwd` | minimal | 300 | 0 | **QUALIFIED** |
| B | `sh -c 'echo a \| wc -c'` | minimal | 300 | 0 | **QUALIFIED** |
| B | `sh -c 'for i in 1 2 3; …'` | minimal | 300 | 0 | **QUALIFIED** |
| **A — ratchet-ladder approximation** (`f89c69766`, full inherited env) ||||||
| A | `/bin/true` | inherited | 300 | 0 | QUALIFIED |
| A | `wc -c /etc/hostname` | inherited | 300 | 0 | QUALIFIED |
| A | `/bin/echo hello` | inherited | 300 | **1** | **DISQUALIFIED** (logger reordering) |
| A | `sh -c 'echo a \| wc -c'` | inherited | 300+400 | **8** | **DISQUALIFIED** (ambient host state) |

**The seven pinned goldens all qualify.** The two disqualified rungs are both in Set A, both
run with the **full inherited environment**, and — this is the load-bearing part — **neither
failure is hermit being nondeterministic.**

> **In all 7100 pairs, across every rung and both SHAs, the COMMIT stream was byte-identical.**
> Zero exceptions. Scheduling never varied. Everything that varied was ambient host state or
> log emission order.

---

## Why each disqualified rung fails

### `sh -c 'echo a | wc -c'` with inherited env — ambient host filesystem state

8 divergent pairs in 700. Every one is a single DETLOG line, and it is always the same shape:

```
DETLOG […] finish syscall #192: newfstatat(-100, … -> "/home/newton/work/…/w2-selfcheck-deepen",
                                 … -> {st_mode=…S_IFDIR|0755, st_size=1662, …}) = Ok(0)
                                                          ^^^^^^^^^^^^^ 1692 in the other run
```

Observed `st_size` deltas: 1662→1692, 1622→1644, 1692→1698, and (in the first sweep) `/home/newton`
6032→5924 and `.` 106→132.

**The guest stats a live directory and the directory's size lands in the golden.** Eighteen
agents share this box, so `$HOME` changes constantly — and the `.` 106→132 delta was caused by
*this experiment's own scripts being written into its cwd mid-sweep*. A rung like this can never
be a stable golden no matter how deterministic hermit is: its log is a function of ambient
filesystem state, and it is not even reproducible across a change of working directory.

**`--base-env minimal` eliminates it.** Controlled comparison, same guest argv, same box, same
quiescent cwd, same time window:

| env | pairs | divergent |
|---|---:|---:|
| inherited | 400 | **6** |
| `--base-env minimal -e LC_ALL=C -e TZ=UTC` | 400 | **0** |

This is also why Set B never flaked on the *identical* guest command: the pinned goldens were
captured with `--base-env minimal`. That flag is not cosmetic — it is what keeps host state out
of the golden.

### `echo hello` with inherited env — logger reordering, load-dependent

1 divergent pair in 300. The two logs hold **the same 489 lines as multisets**; the 52-COMMIT
stream is byte-identical. Only the position differs — the scheduler daemon's startup banner
races the root thread's seeding lines:

```
A: …SCHEDRAND seed 0 / USER RAND seed 0 / CHAOSRAND seed 0 / [scheduler] daemon task starting up…
B: …SCHEDRAND seed 0 / [scheduler] daemon task starting up… / USER RAND seed 0 / CHAOSRAND seed 0
```

This is a comparator defect, not a reference defect: a whole-file byte comparison is sensitive to
log interleaving that carries no semantic content.

**It is load-dependent, and I did not characterize its load curve.** Observed once in 300 pairs
under 11-way concurrency; **zero in 1500 pairs under 4-way concurrency**. So no single rate is
quotable. It is a contention-sensitive race that a busy box makes more likely — which is exactly
the condition the ratchet runs under.

---

## Three comparators

Each captured pair was scored three ways. `STREAM` = DETLOG + COMMIT lines; `COMMIT` = COMMIT
turns only.

| configuration | pairs | WHOLE | STREAM | COMMIT |
|---|---:|---:|---:|---:|
| fork-pipeline, inherited env | 400 | 6 | 6 | **0** |
| fork-pipeline, minimal env | 400 | 0 | 0 | **0** |
| echo-hello, inherited env | 1500 | 0 | 0 | **0** |
| echo-hello, minimal env | 1500 | 0 | 0 | **0** |

`STREAM` fixes reordering but **not** the ambient-state failures — those are real content
differences inside DETLOG. Only `COMMIT` was immune to everything, and only because it discards
the syscall detail that makes a golden worth having. **Dropping to COMMIT-only is not a
recommendation** — it would hide exactly the backend divergence the ratchet exists to catch. The
right fix is capturing with `--base-env minimal` from a quiescent cwd, not weakening the
comparator.

---

## DEBUG depth: fully explained, five classes, zero guest nondeterminism

The first pass flagged DEBUG-depth divergence and attributed it to two causes, calling
decimal-rendered addresses the one non-benign item. That is **incomplete** — and the missing
class matters, because it cannot be fixed by any address canonicalizer.

Raw DEBUG comparison fails **40/40**. Canonicalizing exactly these five classes: **0/40**.

| # | class | example | what it really is |
|---|---|---|---|
| 1 | wall clock | `Nondeterministic realtime elapsed: 21.07ms` | self-labelled; benign |
| 2 | host CPU identity | `initial_local_apic_id`, `x2apic_id`, `core_id`, `max_cores_for_cache` | which core/CCX the host scheduler picked |
| 3 | hex addresses | `patched __vdso_getcpu@7f12f5346fe0` | host-side ASLR |
| 4 | **decimal** addresses | `SyscallArgs { arg1: 94011568533984, … }` | the same ASLR, decimal-rendered — a hex-keyed canonicalizer misses it |
| 5 | **vdso patch ORDER** | `time, getcpu, gettimeofday` vs `getcpu, gettimeofday, time` | iteration over a randomly-seeded Rust hash container |

**Class 5 is the one the first pass missed and the one that will bite.** It is not an address
problem, so fixing the decimal-address canonicalization — the first pass's stated
recommendation — leaves the reference still diverging from itself at DEBUG depth. It also has
to be canonicalized *before* address ordinals are assigned: the permutation changes the order
addresses are first seen, so ordinal-mapping first can never converge (this cost two iterations
here; `debugdepth.py` documents it).

Class 5 is worth a second look on its own merits: randomly-seeded hash iteration inside
`reverie_ptrace::vdso` is invisible today but is the kind of thing that becomes load-bearing.

Note that the guest-side vdso address (`@7ffff7fc2fe0`) is **identical** across runs while the
host-side one is not — the guest address space is properly determinized. Only host metadata varies.

---

## What this means for the ratchet

1. **The seven pinned goldens are safe to keep pinned at INFO depth.** 2100 pairs, 0 divergences.
2. **Capture goldens with `--base-env minimal` from a quiescent cwd, and record the cwd.** This is
   now an evidenced requirement, not hygiene: without it a golden silently embeds directory sizes.
3. **A rung whose guest stats a live directory is not a golden candidate.** Screen for it.
4. **Before deepening past INFO, fix all five DEBUG classes — especially the vdso patch order.**
   Fixing only decimal addresses is not enough.
5. **`dbi 0/Z` and `sabre 2/Z` remain backend divergence, not reference noise.** The first pass's
   central conclusion survives the deeper check.

## Statistical strength

| | first pass | this pass |
|---|---|---|
| pairs per rung | 5 | 300 (+1500/400 on the follow-ups) |
| 95% one-sided upper bound, per rung | ~45% | **~0.99%** |
| pooled over the 7 pinned goldens (0/2100) | — | **~0.14%** |
| stops at first divergence? | yes (`break`) | no — counts all pairs, so a flake yields a rate |

Still an upper bound, not a proof. 0/300 means "not observed to diverge in 300 pairs".

## Reproduction

```sh
# per-rung sweep (one rung per process; RUNG selects)
BIN=<hermit> N=300 TAG=B-true RUNG=true EXTRA="--base-env minimal -e LC_ALL=C -e TZ=UTC" ./deepen.sh

# three-comparator matrix with cwd/env controls
BIN=<hermit> N=400 TAG=forkpipe-asis RUNCWD=<quiescent dir> ./comparators.sh /bin/sh -c '/bin/echo a | /usr/bin/wc -c'

# DEBUG-depth class analysis (exit 0 iff canonicalization reaches byte-equality)
python3 debugdepth.py <hermit> 40 -- --base-env minimal -e LC_ALL=C -e TZ=UTC
```

Binaries were verified by their baked-in SHA, which carries a `-dirty` marker:
`hermit 0.2.0 (2026-08-06, gf89c69766371)` and `(…, g4c70658e7858)` — both clean. Limitations are
recorded in `metadata.json`.
