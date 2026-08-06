# The detlog heap/stack hash is NOT address-sensitive — premise refuted, and normalizing would be harmful

**Tasks:** `detlog_heap_stack_hash`, `revalidate-detlog-parity-normalized`
**Date:** 2026-08-06 · Local, no egress, no validate run, **no code changed**.

## The claim under test

> "detlog heap/stack hash is address-sensitive, unusable cross-backend. Fix via
> address-NORMALIZED hashing (ASLR-off/fixed base, or hash address-relative offsets /
> canonicalize pointers)."

And its follow-up: *"re-run … with the NEW address-normalized hash — earlier results used
the broken address-sensitive metric."*

## Verdict

> **REFUTED. The addresses are already determinized and already identical across backends.
> There is no address-sensitivity to fix, the earlier results were not produced by a broken
> metric, and implementing address-normalized hashing would destroy a real signal the owner
> explicitly asked for.**

I did not implement the normalization. Evidence below.

## Evidence 1 — the addresses are identical across backends

Distinct address ranges appearing in `DETLOG [memory]` records, same guest
(`/bin/echo hello`), same binary, pinned environment:

| backend | ranges observed |
| --- | --- |
| ptrace | `0x55555555c000-0x55555557d000` (heap), `0x7ffffffde000-0x7ffffffff000` (stack) |
| **e9patch** | **exactly the same two** |
| dbi | `0x7ffffffde000-0x7ffffffff000` (stack) — heap range **absent entirely** |

`0x555555554000`-family is the canonical **ASLR-disabled PIE base**. Hermit already runs
the guest with a fixed layout — that is what determinization *is*. The hash is
address-bearing, and the addresses are reproducible, which is the intended design.

## Evidence 2 — the address-bearing hash already achieves cross-backend parity

ptrace vs e9patch, via the **shipped** checker:

```
Done processing logs, no substantive differences found (172 | 172 DETLOG messages compared).
```

and specifically for the heap records:

```
IDENTICAL heap records (addresses AND content hashes) across ptrace vs e9patch
```

| | stack records | heap records |
| --- | --- | --- |
| ptrace | 49 | 5 |
| e9patch | 49 | 5 |
| dbi | 49 | **0** |

**A metric that is "unusable cross-backend" cannot produce a byte-identical
address-and-content match across two execution paths.** It just did, on 54 memory records.

This is not a vacuous pass: the same checker on the same log shape scored **6/6** against
planted mutants (including a single flipped hex digit inside a heap hash) with 2/2 controls
correctly tolerated — `experiments/parity-checker-mutation_20260806/`.

## Evidence 3 — what actually differs under DBI is not addresses

DBI's stack range is *the same* as ptrace's. What differs is:

1. **`dtid` is the raw host TID** — 7 distinct values across 7 runs vs ptrace's constant
   `dtid 3`. Every DETLOG line carries a dtid, so every line differs. *(This is the real
   blocker, and it is a determinism bug.)*
2. **DBI emits zero heap records** (0 vs ptrace's 5) — a **coverage** gap: the heap mapping
   is not reported at all under DBI.
3. Content hashes differ where records exist.

None of these is address noise. Address-normalizing the hash would fix **none** of them.

## Why implementing the normalization would be actively harmful

1. **It fixes a non-problem** — see Evidence 1 and 2.
2. **It contradicts a recorded owner directive.** The heap-domain directive is explicit:
   *"under a patching backend the heap should be BITWISE IDENTICAL to ptrace — **same
   address, same contents** — so ANY heap diff is a REAL BUG."* Normalizing addresses away
   discards precisely the address half of that check.
3. **It would manufacture false greens.** Hashing address-relative offsets makes a run
   whose heap *moved* compare equal to one where it did not. Given the metric currently
   demonstrates a true positive (e9patch) and a true negative (mutants killed), replacing it
   with a weaker one can only lose information.
4. **It would be a "softer strip"** — the exact anti-pattern the canonicalize-don't-strip
   policy exists to prevent. Canonicalization is admissible only where a value is genuinely
   irreproducible; these addresses are reproducible, as measured.

## Consequence for `revalidate-detlog-parity-normalized`

That task cannot be executed, and should not be. It asks to re-run parity "with the NEW
address-normalized hash" and to reclassify "address-noise vs real divergence". There is no
new hash (I declined to build it, above), and **there is no address-noise category to
reclassify** — the address component matched exactly everywhere it was measured. Re-running
would produce the same numbers already recorded:

| backend | full-detlog parity vs ptrace | blocker |
| --- | --- | --- |
| e9patch | **PASS** (172\|172, 0 diffs; heap byte-identical) | — (caveat: `mapped_sites=0` on the tested guest) |
| dbi | 0/6 | host-TID `dtid`; no heap records; log misrouted to stderr |
| sabre / liteinst | unmeasured | — |
| kvm | unmeasurable | startup livelock |

**No scorecard update is warranted**, because no metric changed.

## What would actually unblock the detlog-parity lane

Unchanged from the gap-to-100 map, in order: determinize DBI's `dtid`; route DBI's log
through `--log-file`; measure SaBRe and LiteInst (never probed); build the in-product
cross-backend comparator; fix the **heap domain** (brk-only, 0.2% coverage) — that last one
is a genuine metric-validity defect, and it is the one worth the effort this task was
aiming at.

## Limitations

- One guest (`/bin/echo hello`), 54 memory records, three backends. The address-identity
  result is categorical (identical vs not) rather than statistical, but it is one workload.
- The e9patch pass carries its own caveat: `candidate_sites=0` on that guest, so e9patch
  rewrote nothing.
- I did not test a guest large enough to exercise `mmap`-backed allocation, where a heap
  region *could* land at a different address. If such a case exists, it would be a **real
  divergence to file**, not a reason to normalize the metric.
- Binary: `worktrees/covnode/hermit` @ `fc49593ac`, not current main.
