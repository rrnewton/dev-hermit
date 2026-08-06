# Heap-domain parity: running the owner's definition against ptrace, SaBRe and DynamoRIO

**Date:** 2026-08-06 · **Task:** `define-the-heap-as-guest-allocated-pages-only-code-and-static-excluded`
**Companion design doc:** [`ai_docs/heap-domain-definition-guest-allocated-pages-20260805.md`](../../ai_docs/heap-domain-definition-guest-allocated-pages-20260805.md)

This experiment runs §3 of that design doc, which specified the test but was recorded as
**NOT RUN**. Nothing here re-derives the definition; it measures it.

## Question

The owner's definition: **HEAP = all pages of guest-allocated memory, excluding code pages
and static regions.** The owner's prediction that follows from it:

> under a **patching** backend the heap should be **bitwise identical to ptrace — same
> address, same contents** — so any heap difference is a REAL BUG, not an artifact.

and the open question it splits off:

> under **DBT** (DynamoRIO) the translator's own allocations may be inseparable from the
> guest's, which would make heap parity **structurally unreachable** for that family.

Three things had to be established: (1) implement the definition from the memory map rather
than a per-backend exception list, (2) test the prediction on a patching backend, (3) answer
the DBT question **empirically** rather than assuming hopelessness.

## Method

`heap_domain_probe.c` enumerates the heap domain **from inside the guest**. That placement is
the point: the same binary applies the same rule under every backend, so the definition cannot
drift between arms.

The rule (Rule A of the design doc) admits a region iff it is anonymous, not executable,
readable, not the stack domain, and not kernel-special — with `.bss` removed by the
file-backed-adjacency test, and with one clause added by this experiment (see *Tagged arenas*
below). Each admitted region is reported as an **(address-range, digest) tuple**, never a bare
digest: the prediction is about addresses too, and a digest-only record cannot express the
address half.

**The self-reference trap this probe is built around.** If the enumerator kept its working
buffers on the heap, the heap digest would hash the memory map it had just read — and the map
differs between backends by construction, so every arm would differ for a reason that is purely
an artifact of measuring. Every buffer is therefore static (`.bss`, which the rule excludes),
and the enumeration path uses raw `open`/`read`/`write` with no stdio and no `malloc`.

Workload: 2000×64 B (served from `brk`, i.e. the kernel's `[heap]`) and 8×4 MiB (above glibc's
128 KiB `M_MMAP_THRESHOLD`, so anonymous `mmap` — the 99.8% of allocation that a `[heap]`-only
rule cannot see). All fills are deterministic functions of the index.

Guest env pinned with `--base-env minimal -e LC_ALL=C -e TZ=UTC`; without it the ambient shell
decides the guest's initial stack address and perturbs the layout being measured.

**Both directions, per the standing bar.** A pin or a domain that makes everything match is
vacuous, so each arm is run twice (must agree with itself) and once more with a planted
one-byte mutation deep inside a large allocation (must disagree).

## Results

Hermit `f89c6976` / Reverie `dd3c178e`, debug build (release lacks the `sabre` feature),
`devbig014`, kernel 6.18.39, glibc 2.34.

| arm | domain regions | domain bytes | unstable run-to-run | **unstable regions carrying GUEST data** |
|---|---:|---:|---:|---:|
| ptrace (reference) | 4 | 33,878,016 | **0** | **0** |
| sabre (patching) | 10 | 61,349,888 | **1** | **0** |
| dbi (DBT) | 38 | 41,451,520 | **35** | **0** |

Parity against the ptrace reference, and the planted negative:

| arm | content twins of ptrace | **exact (address + content) matches** | mutation detected | post-mutation digest equals ptrace's |
|---|---:|---:|---|---|
| ptrace | 4 / 4 | 4 / 4 | yes | yes |
| sabre | 2 / 4 | **0 / 4** | yes | yes |
| dbi | 2 / 4 | **0 / 4** | yes | yes |

The two guest-carrying regions, tracked across arms:

| what | size | digest | ptrace | sabre | dbi |
|---|---:|---|---|---|---|
| `brk`-served smalls | 270,336 | `71fe6547a133aac0` | `0x52d000` | `0x555555571000` | `0x52e000` |
| large `mmap`s | 33,587,200 | `7622af4d80f10383` | `0x7ffff5bf8000` | `0x7ffff31b8000` | `0x7ffdf4df8000` |

**The same digest in all three arms, before and after mutation.** The planted byte moves that
second region to `105480b4086afd6e` under ptrace, sabre **and** dbi — identical in all three.
The instrument is reading real guest data and the three backends agree on it bit-for-bit.

The two ptrace regions with no twin (12,288 B and 8,192 B) are loader/libc private data, not
guest allocations.

### What the rule removed, per arm (count / bytes)

| arm | file | exec | noread | stack | special | bss | **tagged** |
|---|---|---|---|---|---|---|---|
| ptrace | 15 / 2,351,104 | **1 / 4,096** | 0 | 1 / 135,168 | 4 / 36,864 | 2 / 1,261,568 | 0 |
| sabre | 62 / 14,581,760 | 5 / 53,248 | 0 | 1 / 135,168 | 4 / 36,864 | 14 / 4,636,672 | **6 / 1,076,559,872** |
| dbi | 59 / 13,627,392 | 14 / 274,432 | **59 / 9,655,668,736** | 1 / 135,168 | 4 / 36,864 | 6 / 1,548,288 | **0 / 0** |

Counts, never a list — they prove the rule ran and show what it removed, without becoming a
maintained per-backend exception table.

Two of these columns are load-bearing rather than theoretical. `exec` removes hermit's injected
executable page under ptrace and 14 regions of DynamoRIO's code cache — **this is the clause
that makes the owner's principle work**: patch and JIT bytes live in executable regions, so they
were never in the domain and nothing is excluded after the fact. `noread` removes **9.0 GiB** of
`PROT_NONE` address space DynamoRIO reserves; hashing it would fault or hash nothing.

### Tagged arenas: the discriminator already exists, and only one backend uses it

SaBRe's allocator names every arena it creates via `PR_SET_VMA_ANON_NAME`, so they appear in
`/proc/self/maps` as `[anon:mimalloc]` — **the runtime itself declaring "this memory is mine,
not the application's."** That is a first-class, kernel-visible provenance tag, so it is a
domain clause, not a special case, and it is exactly the "by arena, by mapping origin, by tag"
discrimination deliverable (3) asked about. It removes 1.0 GiB across 6 regions under sabre.

**DynamoRIO tags nothing: `tagged = 0 / 0`.** That single difference is why sabre lands 10
domain regions with 1 unstable and dbi lands 38 with 35.

## Interpretation

**1. "Same contents" — CONFIRMED, and more broadly than predicted.** The guest's own allocated
data is bit-identical to ptrace under the patching backend *and* under DBT, before and after a
planted mutation. Every arm's guest-carrying regions are also perfectly stable run-to-run
(0 unstable in all three).

**2. "Same address" — REFUTED for both families.** Zero exact (address+content) matches under
either sabre or dbi. Each backend relocates the guest: sabre loads it PIE at `0x555555571000`
where ptrace has it at `0x52d000`, and DynamoRIO shifts the `brk` region by exactly one page
(`0x52d000` → `0x52e000`) and the large mappings by ~2 GiB. So the prediction is **half right,
and the half that fails is the half that would have made a heap diff self-evidently a bug.**
A heap comparison across backends must be content-keyed, or the backends must be made to agree
on placement; as it stands, an address-sensitive comparison reports 100% divergence on a heap
whose contents match perfectly.

**3. The DBT question — "hopeless" is the wrong frame; the answer depends on the rule.** Under
a **map-derived** rule DynamoRIO's allocations are *not* separable from the guest's: 34 extra
regions enter the domain and 35 of 38 are unstable run-to-run, so a Rule-A heap hash under DBT
is dominated by translator noise and heap parity **is** structurally unreachable. But the
translator's noise never touched the guest's data — all 35 unstable regions are DR's own, and
the guest's two regions are stable and content-identical to ptrace. So DBT heap parity is
unreachable **under Rule A specifically**, and reachable under a provenance rule that can
attribute a page to its allocator. Two such channels are available and unused: DynamoRIO's own
documented `dr_memory_is_dr_internal()` / `dr_query_memory_ex()` (unplumbed in `reverie-dbi`),
and the anon-VMA naming SaBRe already relies on.

**4. The instrument is sharp for the patching backend, as the owner hoped — with one residual.**
Under sabre, exactly one region is unstable: an **untagged** anonymous 20 KiB block
(`0x7ffff58b1000-0x7ffff58b6000`) sitting between tagged mimalloc arenas and libm's file
mapping. It carries no guest data. Its position is consistent with a SaBRe-side allocation that
did not receive the anon-name tag its neighbours have, but that is **narrowed to a region, not
proven to a source.** Tag it (or attribute it) and sabre's heap domain becomes fully stable,
at which point any remaining heap difference really is a bug — which is the property the owner
was after.

**5. Today's shipped `--detlog-heap` would have shown none of this.** It hashes only the
kernel's `[heap]` label, i.e. the `brk` segment: 270,336 of 33,878,016 domain bytes here, 0.8%.
The 8 large `mmap`s that hold 99% of the guest's data are invisible to it. As the design doc
predicted, fixing the domain *creates* heap differences where there were none — those
differences are the instrument starting to work.

## Recommendation

Adopt Rule A **plus the tagged-arena clause** as the enumeration rule, and record the
`(range, digest)` tuples with the exclusion counts, per the design doc's record shape. Compare
**content-keyed** across backends until placement is unified; report the address delta as its
own column rather than folding it into a pass/fail. For DBT, either plumb
`dr_memory_is_dr_internal()` or have DynamoRIO name its arenas the way SaBRe's allocator
already does — the second is cheaper and makes the map self-describing for every consumer.

**The owner's open question stays open.** Nothing here demonstrates that a patching backend can
match a *full* heap hash: it matches on contents, not on addresses, and one untagged
SaBRe-side region is still unstable.

## Limitations

- **One guest, one allocation shape.** Chosen to cover the three shapes real programs use
  (brk smalls, large mmaps, thread arena) but it is not a corpus statistic.
- **The thread arena was not exercised in the parity runs** (`--threads 0`). A non-main thread
  stack is anonymous, readable, non-executable and *unlabelled*, so Rule A would admit it as
  heap. That ambiguity is identified, not measured, and it is a real gap in clause 4.
- **e9patch and liteinst were not run.** This hermit is built without the `e9patch` feature, and
  liteinst does not reach its preload handshake on this box. SaBRe is therefore the only
  patching arm measured; the "patching backend" conclusions rest on it alone.
- **`--detlog-heap` itself was not modified.** This measures what the definition *would* yield;
  wiring it into Detcore is separate work.
- **Attribution of sabre's unstable 20 KiB region is by adjacency**, not by tracing the
  allocation to its call site.
- The digests are FNV-1a 64, chosen for dependency-free determinism, not collision resistance.

## Reproduction

```sh
cd experiments/heap-domain-parity_20260806
./run.sh [OUTDIR]          # default OUTDIR: <repo>/ignored/w10-heapdomain
column -s, -t "$OUTDIR/results.csv"
```

`run.sh` builds the probe, runs each backend twice plus once mutated, and regenerates
`results.csv` via `analyze.py`. Requires `hermit/target/debug/hermit` (the release build has no
`sabre` feature) and `LD_LIBRARY_PATH` pointing at libunwind. **The guest must not live under
`/tmp`** — hermit isolates the guest's `/tmp`, so a probe staged there cannot resolve its own
path. Re-running end-to-end into a clean directory reproduced every figure in this document.
