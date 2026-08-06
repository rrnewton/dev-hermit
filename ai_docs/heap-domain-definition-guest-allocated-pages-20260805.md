# Defining the heap domain: guest-allocated pages only, code and static excluded

**Task:** `define-the-heap-as-guest-allocated-pages-only-code-and-static-excluded` (P0, owner)
**Date:** 2026-08-05
**Scope:** local design and analysis. **No `validate` run, no egress, no product change.**
Deliverable (2) — testing the prediction on a live patching backend — is *designed here
but not executed*, per dispatch. The owner explicitly left the patching-backend question
**OPEN**; nothing below claims it is settled.

**Companions:** `ai_docs/verify-strip-site-audit-20260805.md` (phase 1, what the
comparison strips), `ai_docs/correctness-oracle-design-beyond-ptrace-parity-20260805.md`
(phase 2, oracles beyond parity).

---

## 1. What the code does today, and why it is not the owner's definition

`detcore/src/lib.rs:718-765` (`detlog_memory_maps`) is the whole implementation. It:

- returns immediately unless `--detlog-stack` or `--detlog-heap` is set (**both default off**);
- on the ptrace path, filters `/proc/<pid>/maps` to exactly two `procfs` region kinds —
  `MMapPath::Stack` and `MMapPath::Heap` (`detcore/src/procmaps.rs:56-67`);
- on the backend-reported path (KVM), filters `DetlogRegionKind::{Stack, Heap}`;
- hashes each surviving region whole via `procmaps::compute_hash{,_range}`.

`MMapPath::Heap` is the kernel's `[heap]` label, which is **the brk segment and nothing
else**. So the shipped definition is not "all guest-allocated pages minus code and
static". It is "the brk segment", which is dramatically narrower.

### Measured: how narrow

A C probe (`scratch/heap-domain/probe.c`) performing the three allocation shapes every
real program uses — 2 000 × 64 B (brk), 8 × 4 MiB (above glibc's 128 KiB
`M_MMAP_THRESHOLD`, so `mmap`), and one thread arena — then classifying its own
`/proc/self/maps`:

| region class | size |
| --- | --- |
| `[heap]` (brk) | **264 KiB** |
| anonymous, non-executable | **106 608 KiB** |
| file-backed executable (code) | 1 740 KiB |
| file-backed non-executable | 664 KiB |
| `[stack]` | 140 KiB |
| vdso/vvar/vsyscall | 36 KiB |

> **The `[heap]`-only rule captures 0.2% of this program's non-executable anonymous
> memory** (264 of 106 872 KiB).

Everything a program allocates above 128 KiB, plus every non-main thread arena, is an
anonymous `mmap` and is invisible to the current hash. So today's heap digest is not a
weak version of the owner's definition — for allocation-heavy guests it is very nearly
*empty*, and a heap-hash "match" between two backends is close to vacuous. This is the
same vacuity shape phase 1 found in the stdout metric, in a different place.

---

## 2. The domain rule

The owner's principle is *define the domain, never maintain an exclusion list*. Two
boundaries can express "guest-allocated". They are not equivalent, and the difference is
exactly what decides the DBT question.

### Rule A — structural, derived from the memory map

A region is in the HEAP domain iff **all** of:

1. **anonymous** — no file backing. Excludes `.text`, `.rodata`, `.data`, and every mapped
   file, in one clause, with no per-backend list.
2. **not executable** — excludes JIT/code-cache pages and any injected/patched code. Under
   a patching backend the patch bytes live in executable file-backed or anonymous-exec
   regions, so **they were never in the domain**; nothing is "excluded" after the fact.
3. **readable** — a `PROT_NONE` region holds no guest data and cannot be read. Required in
   practice, not theory: measured 16 KiB of `PROT_NONE` anonymous guard pages in a 4-thread
   process (`scratch/heap-domain/pn.c`), and glibc reserves 64 MiB per thread arena of
   which the uncommitted remainder is `PROT_NONE`. Hashing it would fault or hash nothing.
4. **not the stack domain** — `[stack]` and `[tstack:N]` are a *separate* hashed domain
   (`--detlog-stack`), not part of the heap.
5. **not a kernel-special region** — `[vdso]`, `[vvar]`, `[vsyscall]`.

**Rule A has one genuine hole: `.bss`.** The uninitialized static segment is anonymous,
readable, non-executable, and not the stack — so clauses 1–5 admit it, yet it is
precisely the "static region" the owner excludes. It is separable from the map without an
exception list, because `.bss` is the anonymous region *immediately adjacent to and
following* a file-backed mapping of the same object. That adjacency test is derivable
per-run, so it stays a domain rule — but it is a heuristic about loader layout, and it is
the weakest joint in Rule A.

### Rule B — provenance, derived from syscall interception (recommended)

"Guest-allocated" has a literal reading: the pages **the guest itself obtained**, via its
own `brk`/`mmap` calls. Detcore already intercepts every one of them
(`detcore/src/lib.rs:1849` → `handle_mmap` at `syscalls/files.rs:1275`, plus `munmap`,
`mremap`, `brk`).

Critically, **the range-tracking substrate already exists**. `handle_mmap` already calls
`guest.thread_state().unmap_memory(start, len)` and, for shared mappings,
`map_shared_anonymous` / `map_shared_object` (`tool_local.rs:1659-1700`), maintaining an
interval model in `memory_metadata` for futex identity and shared-memory determinism.
Today it records **only shared** mappings; a private anonymous `mmap` hits the `None`
backing arm and is only *removed* from tracking, never recorded.

So Rule B is an extension of live machinery, not new subsystem work: record private
anonymous guest mappings in the same interval model, maintain it across
`munmap`/`mremap`/`brk`, and define the heap domain as that recorded set.

**Rule B's advantages over Rule A:**

- **`.bss` falls out for free.** The loader maps it before/outside the guest's own
  allocation calls, so it is never recorded. No adjacency heuristic.
- **It is the definition, not a proxy for it.** "Pages the guest allocated" is recorded
  at the moment of allocation rather than inferred from a snapshot.
- **It decides the DBT question by construction** (§4).

**Rule B's costs and risks, stated plainly:**

- The model must track `brk` growth/shrink, `mremap`, partial `munmap`, and `MAP_FIXED`
  overlap correctly, or the domain silently drifts from reality.
- It must survive `execve` (address space replaced) and `fork`/`clone` (address space
  copied or shared).
- A guest that allocates *without* a syscall Detcore sees would be invisible. Under the
  patching backends this is the case to check, not assume.

**Recommendation: implement Rule B as the domain, and run Rule A as a cross-check.** Their
disagreement set is itself a finding — a page Rule A calls guest-allocated that Rule B did
not record is either a provenance-tracking bug or a backend allocating behind Detcore's
back, and both are worth knowing. Do not average them; report the delta.

---

## 3. The testable prediction, and exactly how to test it (NOT RUN)

Owner's prediction: under a **patching** backend the heap should be **bitwise identical to
ptrace — same address, same contents** — so any heap diff is a real bug.

The experiment, specified so it can be run unchanged when a validate slot is free:

1. Pick a guest with a non-trivial, deterministic allocation pattern — the probe shape in
   §1 (small brk + large mmap + one thread arena) is a good first fixture; the corpus's
   `c-programs/*-determinism` family is the natural second.
2. Run under ptrace with `--detlog-heap --detlog-stack --strict --verify --verify-strict`.
3. Run the same guest under `liteinst` (simplest patcher) and `e9patch`.
4. Compare the emitted `DETLOG [memory]` records **as (address-range, digest) pairs**, not
   just digests. The prediction is about *addresses too*; comparing digests alone would
   pass a run whose heap moved.
5. **Both directions, per the standing bar:** confirm an address-only or benign difference
   compares EQUAL, and plant a real one-byte heap mutation and confirm it compares UNEQUAL.
   Without the second, a "match" may only mean the domain is empty — the 0.2% finding in §1
   is exactly how that failure looks.

**Predicted first result, stated in advance:** with the domain fixed per §2 this will
likely produce *new* heap diffs where today there are none, because today's domain is
nearly empty. Those diffs are the instrument working, not a regression.

---

## 4. The DBT question — the "hopeless" premise is refuted at the API level

The task asks whether DynamoRIO's allocations are distinguishable from the guest's "by
arena, by mapping origin, by tag" — and says *do not assume hopeless*. They are.

DynamoRIO's public API exposes exactly this discrimination, and its own header documents
the recipe (`scratch/dynamorio-build-experiment/build/include/dr_os_utils.h:653-654, 676-677`):

> *"To examine only application memory, skip memory for which `dr_memory_is_dr_internal()`
> or `dr_memory_is_in_client()` returns true."*

with `dr_query_memory_ex()` returning a typed `dr_mem_info_t` whose
`DR_MEMTYPE_{FREE,IMAGE,DATA,RESERVED}` further separates mapped images from data and from
reserved-without-storage address space (the same `PROT_NONE` distinction §2 clause 3
requires).

So: **guest and translator allocations are separable by origin, through a documented
first-class API, from inside the DR client.** DBT heap parity is not structurally
unreachable. The premise that DynamoRIO "may be hopeless" should be retired.

Three honest qualifications:

1. This establishes separability is **available**, not that it is **plumbed**.
   `reverie-dbi` currently makes no use of these APIs — no reference to
   `dr_memory_is_dr_internal`, `dr_query_memory_ex`, or `DR_MEMTYPE_*` exists in
   `reverie/reverie-dbi/src/`. Wiring is required work.
2. Separable-by-origin does not imply **bitwise-stable**. DynamoRIO may perturb the
   *application's own* allocation addresses (its presence changes the address space), which
   would break "same address" even with a perfectly clean domain. That is the real DBT
   risk, and it is a different question from separability — it should be measured, not
   assumed either way.
3. `DR_MEMPROT_PRETEND_WRITE`: DR may mark writable code pages read-only while reporting
   them writable (`dr_os_utils.h:656-658, 679-681`). Any permission-based filter (Rule A
   clauses 2–3) must read DR's reported protection under DBI, not the raw kernel bits, or
   it will classify those pages wrongly.

**Consequence for scoping:** on current evidence DBT keeps heap parity *as a goal*. If
measurement later shows point 2 defeats it, the honest scope reduction is stdout + INFO +
stack for DBT — but that conclusion needs the measurement, which this pass did not run.

---

## 5. What a heap-hash record must carry

Inheriting the phase-1 proxy-binding discipline — a value that does not record its
conditions is a proxy:

```
heap_digest = {
  domain_rule: "guest-allocated/v1",       # A | B | A∩B, versioned
  regions: [ {start, end, prot, digest} ], # per-region, ADDRESSES INCLUDED
  region_count, total_bytes,               # nonzero-work evidence
  excluded: {code, static, stack, special, prot_none},  # counts, not names
}
```

- **Addresses travel with digests.** The prediction is "same address, same contents";
  a bare digest list cannot express it.
- **`region_count == 0` is a NO-RESULT, never a match.** This is the direct lesson of the
  0.2% measurement: an empty domain matches trivially.
- **Record the rule and its version.** A digest computed under Rule A is not comparable to
  one computed under Rule B.
- **Exclusion counts, not an exclusion list.** Counts prove the domain rule ran and show
  what it removed, without ever becoming a maintained list of special cases.

---

## 6. Status against the task's three deliverables

| | Deliverable | State |
| --- | --- | --- |
| (1) | Implement the definition; boundary from the memory map, not a per-backend list | **Rule A implemented and run** as a guest-side enumerator (2026-08-06, see §8). Rule B still designed-only; its substrate exists in `memory_metadata`. |
| (2) | Test the prediction on a patching backend | **RUN.** SaBRe vs ptrace: contents identical, addresses not. §8. |
| (3) | Answer the DBT question empirically, do not assume hopeless | **Answered, with a condition.** Not separable under a map-derived rule; separable in principle by provenance. §8. |

**The task stays open per the owner's explicit instruction:** do not claim patching
backends can match full heap hashes until demonstrated. §8 does not demonstrate it — it
matches on *contents* but not on *addresses*, and one SaBRe-side region is still unstable.

---

## 8. Results — §3's experiment, run (2026-08-06)

Full artifact, reproduction and limitations:
[`experiments/heap-domain-parity_20260806/`](../experiments/heap-domain-parity_20260806/README.md).
Hermit `f89c6976` / Reverie `dd3c178e`, ptrace + sabre + dbi, both-directions bracketed.

| arm | domain regions | unstable run-to-run | unstable regions carrying **guest** data | content twins of ptrace | **exact (addr+content) matches** |
|---|---:|---:|---:|---:|---:|
| ptrace | 4 | 0 | 0 | 4/4 | 4/4 |
| sabre | 10 | 1 | **0** | 2/4 | **0/4** |
| dbi | 38 | 35 | **0** | 2/4 | **0/4** |

**"Same contents" — CONFIRMED, and for DBT too.** The guest's own allocations carry identical
digests under all three backends, before *and* after a planted one-byte mutation (which moves
every arm to the same new digest, so the instrument is reading real guest data and is not
vacuous).

**"Same address" — REFUTED for both families.** Zero exact matches. Each backend relocates the
guest: sabre loads PIE at `0x555555571000` where ptrace has `0x52d000`; DynamoRIO shifts `brk`
by exactly one page and the large mappings by ~2 GiB. The prediction is half right, and the
failing half is the one that would have made a heap diff self-evidently a bug. **Compare
content-keyed, and report the address delta as its own column.**

**The DBT answer depends on which rule you use, which is why "hopeless" was the wrong frame.**
Under Rule A, DR's allocations are *not* separable: 34 extra regions enter the domain and 35 of
38 are unstable, so a Rule-A heap hash under DBT is dominated by translator noise. But none of
that noise touched the guest's data. So DBT heap parity is unreachable **under Rule A
specifically** and reachable under a provenance rule.

**A provenance channel already exists in the memory map, and only one backend uses it.** SaBRe's
allocator names its arenas with `PR_SET_VMA_ANON_NAME`, so they appear as `[anon:mimalloc]` —
the runtime declaring the memory is its own. This is a domain clause, not an exception list, and
it removes 1.0 GiB across 6 regions under sabre. **DynamoRIO tags nothing.** That single
difference explains sabre's 10 regions / 1 unstable versus dbi's 38 / 35. Adopt this as a clause
alongside Rule A; for DBT, either plumb `dr_memory_is_dr_internal()` (§4) or have DR name its
arenas — the latter is cheaper and makes the map self-describing for every consumer.

**Two Rule A clauses proved load-bearing rather than theoretical.** *Not-executable* removed
hermit's injected page and 14 regions of DR's code cache — this is the clause that makes the
owner's principle work, since patch and JIT bytes were then never in the domain. *Readable*
removed **9.0 GiB** of `PROT_NONE` address space DR reserves.

**Residual, and it is small:** under sabre exactly one region is unstable — an *untagged*
anonymous 20 KiB block between tagged mimalloc arenas and libm's mapping, carrying no guest
data. Tag or attribute it and sabre's heap domain is fully stable, which is the property the
owner wanted.

**Today's `--detlog-heap` would have shown none of this**: it hashes only `[heap]`, 0.8% of the
domain here. Fixing the domain *creates* heap differences where there were none — that is the
instrument starting to work, as §3 predicted.

## 7. Limitations

- No Hermit execution of any kind. All measurements are host-native probes
  (`scratch/heap-domain/{probe.c,pn.c}`, ignored dir) characterizing *allocator and kernel
  layout behaviour*, which is what determines the domain — not Hermit's behaviour under a
  backend.
- The 0.2% figure is one program's allocation shape, chosen to include the three common
  cases. It demonstrates the definitional gap; it is not a corpus-wide statistic.
- The `.bss`-adjacency test in Rule A is reasoned from ELF loader layout, not verified
  against a linker corpus.
- The DynamoRIO API reading comes from headers in a local build tree
  (`scratch/dynamorio-build-experiment`), not from the pinned DynamoRIO revision the DBI
  backend builds against. Confirm the API exists at that revision before relying on it.
- Rule B's `execve`/`fork`/`clone` and `MAP_FIXED` edge cases are named, not designed.

## Reproduction

```bash
cd ~/work/dev-hermit/scratch/heap-domain
gcc -O1 -o probe probe.c -lpthread && ./probe   # brk vs anon split
gcc -O1 -o pn    pn.c    -lpthread && ./pn      # PROT_NONE guard pages
# current implementation
sed -n '718,765p' ../../hermit/detcore/src/lib.rs
sed -n '56,67p'   ../../hermit/detcore/src/procmaps.rs
# existing provenance substrate
sed -n '1275,1330p' ../../hermit/detcore/src/syscalls/files.rs
sed -n '1655,1700p' ../../hermit/detcore/src/tool_local.rs
# DBT separability
grep -n "dr_memory_is_dr_internal\|DR_MEMTYPE" \
  ../dynamorio-build-experiment/build/include/dr_os_utils.h
```
