# e9patch heap records: real divergence, not windowable, not a defect

**Verdict.** The task's structural conclusion holds: the gap is **not purely
scoping**, and the same-position content replace is **real**. But it is **not a
determinism defect** — both legs reproduce themselves exactly. It is a
**documented legitimate divergence**, recorded here rather than windowed away.

## Reproduction

Guest: a static, no-RDTSC C program that grows and reuses the heap. The original
guest could not be identified; this one is built to the same spec, and as before
the **shape reproduces even though absolute numbers differ**.

Engagement was checked before anything was believed: `candidate_sites=135`,
`preprocess_us=1088481`. e9patch really rewrote the binary — this is not the
known silent-ptrace-fallback (`sites=0`) failure, so the trial qualifies.

| leg | heap records | distinct digests | stack records | heap base |
|---|---:|---:|---:|---|
| ptrace | 19 | **14** | 21 | `0x4bd000` |
| e9patch | 17 | **12** | 125 | `0x20e9eb000` |

Both `rc=0`, stdout byte-identical (`heapguest sum=748`).

**The reported `14 → 12` is the DISTINCT-DIGEST count, not the record count.**
This run reproduces it exactly — 14 distinct over 19 records, 12 over 17 — which
also identifies what the original was counting. The record count moves `19 → 17`.
Either way the delta is `−2`, and **a purely additive story cannot produce a
negative delta.**

Stack moves the other way, `21 → 125` (+104). That is the additive preprocessing
effect, and it *is* windowable. The two effects are genuinely distinct, exactly
as the task concluded.

## What the replace changes, and why

Three facts, measured:

1. **The domains are IDENTICAL.** Both legs emit exactly the two sizes `0x1000`
   and `0x22000`. So this is not a measurement-domain artifact — the same extent
   of memory was hashed on both sides. (Separating this from content required the
   `size=` field added in hermit PR #1875; without it the two are indistinguishable.)
2. **The heap base moved ~8.7 GB**, `0x4bd000` → `0x20e9eb000`. e9patch rewrites
   the binary, so the image is laid out somewhere else entirely and the brk heap
   follows it.
3. **Positionally, `EQUAL=0`, `DOMAIN=2`, `CONTENT=15`.** Not one heap record
   matches.

So the replace is: **the same-sized region at a different address, holding
different bytes.** The bytes differ because heap content holds absolute pointers,
and every one of them shifts with the relocated image. This is the same mechanism
established earlier today for threaded KVM heap divergence — pointers to
regions whose placement differs between execution modes — and it is inherent, not
incidental.

**Why records can DECREASE, which is the part a preprocessing story cannot
explain.** `detlog_memory_maps` samples the heap at scheduler checkpoints. e9patch
does not merely prepend work to the same instruction stream; it *rewrites* it, so
the guest reaches a **different set of checkpoints**. The record sequence is
therefore not "the ptrace sequence with insertions" — it is a *different
sequence*, which can be shorter. That is precisely why narrowing the comparison
window would have hidden it: there is no alignment under which the two sequences
correspond.

## Defect or legitimate? — the discriminator

Each leg was run twice and compared against itself:

| leg | repeat runs | identical records | verdict |
|---|---|---|---|
| ptrace | 20 vs 20 | **20/20** | self-deterministic |
| e9patch | 18 vs 18 | **18/18** | self-deterministic |

**Neither leg is nondeterministic.** Each reproduces itself bit-for-bit; they
merely disagree with each other. Determinism is the property the scheduler owns,
and it holds on both sides. The disagreement is an address-space and
instruction-stream consequence of binary rewriting.

(These self-checks ran with `--detlog-heap` only, no `--detlog-stack`, which
shifts the sample count slightly — 20/18 rather than 19/17. The `−2` delta is
stable across both configurations.)

So: **legitimate divergence, documented here.** Not a bug to fix, and not
something to make green by narrowing the comparator — which the task correctly
identified as the fake-green move the floor of 0 exists to prevent.

## Consequence for the ratchet

Confirmed: **e9patch is not the free first increment.** Cross-mode heap parity
against e9patch is unattainable while the rewritten image relocates the heap,
for the same reason KVM threaded heap parity is unattainable while its guest
address space is 1 GiB. Both are layout facts, not defects.

The correct handling is a declared, reasoned exclusion for the e9patch heap
dimension — never a narrowed window, which would silently also hide a real
regression.

## Reproduction

```bash
cc -O2 -g -static -std=c11 -Wall -Wextra -Werror heapguest.c -o heapguest
cargo build --release -p hermit --bin hermit --features third-party-backends
export HERMIT_E9TOOL=<repo>/reverie/third-party/e9patch/e9tool
hermit --log=info --backend=<ptrace|e9patch> run --strict --detlog-heap --detlog-stack -- ./heapguest
```

`--features third-party-backends` is required; a default build refuses with
"e9patch support was not included in this build" — a **setup failure, and zero
qualifying trials, not a negative result**. Build and run with a clean
environment (no `LD_LIBRARY_PATH`).

## Limits

- One guest, one host. `n=2` per leg for the self-determinism check — enough to
  demonstrate reproducibility, not to bound its rate.
- Stack was measured only in the first configuration; the `+104` is not
  decomposed here.
- The pointer explanation for the content divergence is inferred from the
  relocated base plus identical domains; individual differing heap words were not
  dumped for this guest, as they were for the threaded KVM case.
