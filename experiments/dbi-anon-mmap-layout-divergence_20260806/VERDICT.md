# Verdict: INHERENT — mark the DBI anon-mmap cell NOT-PARITY-ACHIEVABLE-FOR-DBT

**Date:** 2026-08-06 · **Host:** devbig014 · **Agent:** hermit-w4
**Task:** `root-cause-dbi-anon-mmap-divergence-inherent-or-fixable`
(sessions 1–4 are `README.md` and `README-prefix-sweep.md`)

The root-cause task asked one question with two allowed answers. **Both are
refuted by measurement.** The answer is INHERENT, by a third mechanism.

> **Note on the dispatch's numbers.** The task says *"DBI packs anonymous mmaps
> −8 pages vs ptrace −4"*. Measured by four agents: **ptrace −8, DBI −4,
> native −8.** The arms are the other way round. This matters, because the
> hypothesis under test is about which arm carries the *extra* pages.

## Branch (a) — "DR's allocations intermix with the guest's, so exclude them" — REFUTED

The hypothesis is about VMAs inside a span, so measure the span
(`interleave-probe.c`, raw data `interleave.txt`):

| arm | span for a 10-page request | VMAs overlapping the span | translator allocations inside |
|---|---|---|---|
| **dbi** | `7ffff7e41000-7ffff7e4b000` = **10 pages** | **1** | **0** |
| ptrace | `7ffff7fac000-7ffff7fba000` = **14 pages** | 2 (with a 1-page hole) | 0 |

Under DBI the four guest mappings are **perfectly contiguous and coalesced into
a single VMA**: zero padding, zero waste, zero DynamoRIO allocations between
them. **The quantity the proposed fix would subtract is already zero**, so
"exclude translator allocations from the count" would be implemented and change
nothing.

And the direction is the opposite of the premise: **the four extra pages are on
the ptrace side**, and they are glibc loader blocks plus a hole — not translator
allocations. DBI's packing is not merely different, it is *strictly tighter and
optimal*.

## Branch (b) — "inherent because translator allocations are inseparable" — NOT THE MECHANISM

Separability was already settled on the sibling define-heap task (`PR #57`,
hermit-w10): DR's allocations **are** separable by provenance —
`dr_memory_is_dr_internal`, `dr_query_memory_ex`, `DR_MEMTYPE_*` are documented
first-class API, merely **unplumbed** in `reverie-dbi`. (DR tags nothing,
`tagged=0/0`, where SaBRe names its arenas via `PR_SET_VMA_ANON_NAME` — that is
a plumbing gap, not a structural one.)

Inseparability is not why this defect exists.

## The actual mechanism — address-space displacement, orthogonal to separability

DR's runtime makes the guest-visible address space **185 VMAs instead of 25**
(session-3 census). That changes **where the kernel's top-down allocator places
the guest's own mappings**.

**Perfect separability would not move them by one byte.** The guest's pages are
already attributed correctly under both arms — they are simply at different
offsets. *Attribution* and *placement* are different properties, and only the
first is separable.

This is the first measurement of a risk hermit-w10 named but could not test.
Their design note, qualification (b):

> *"separable-by-origin does NOT imply bitwise-STABLE: DynamoRIO's presence
> changes the address space and may perturb the APPLICATION'S OWN allocation
> addresses … That is the real DBT risk and it is a DIFFERENT question from
> separability — measure it, do not assume either way."*

Measured, and confirmed. w10 independently hit the same class on the heap side:
DR shifts `brk` by **exactly one page** and the large mappings by ~2 GiB, while
the guest's *contents* stay identical.

## Why this is NOT-PARITY-ACHIEVABLE rather than a failure to fix

Beyond the mechanism, **the target does not exist.** From `README-prefix-sweep.md`:

* sweeping a K-page allocation prefix K=0..8, ptrace/native produce **nine
  distinct layouts** and DBI produces **one**;
* at **K=7 the two arms are byte-identical**, and `d23` already agrees at 6 of 9;
* native itself has a **1.3–2 % tail** near −530.

So DBI is being asked to reproduce a value that is not a constant, is not stable
on the reference arm, and — per the span measurement — encodes **four pages of
wasted address space**.

### Cell marking

Mark the DBI cell on `backend-parity-c/mmap-layout-pointer-order`
**NOT-PARITY-ACHIEVABLE-FOR-DBT**, per the `#319` / e9patch-vacuous precedent,
**not** scored as a failure-to-fix. Suggested reason string:

> Anonymous-mmap placement is a function of the whole address space, which a
> translator necessarily changes (25 → 185 guest-visible VMAs). DBI packs the
> guest's mappings optimally with zero translator allocations interleaved (span
> = exactly the requested 10 pages, 1 VMA); ptrace's layout carries 4 pages of
> glibc loader slack and varies with the guest's allocation history (9 distinct
> layouts over 9 prefixes, identical to DBI at one of them). There is no stable
> ptrace layout to match.

### Scope this narrowly — it is not a DBT parity write-off

**Contents parity IS reachable for DBT.** w10 measured guest heap digests
identical across ptrace / sabre / dbi, with a planted one-byte mutation caught
in all three arms. What is unreachable is **address** parity.

**Corollary for the fleet:** any future backend-parity fixture asserting
absolute *or relative* addresses is unreachable for DBT by the same argument.
Worth knowing before more are written — the existing fixture's `cmp` and `word`
fields (pointer *ordering*) agree on every arm and every prefix, so ordering is
the parity-bearing part and spacing is not.

## Reproduction

```bash
gcc -O1 -Wall -Werror -D_GNU_SOURCE -o mlo_interleave interleave-probe.c
H=worktrees/dbi/hermit/target/release/hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
$H run --backend ptrace --strict -- ./mlo_interleave
$H run --backend dbi    --strict -- ./mlo_interleave
# the guest must not live under /tmp; hermit isolates guest /tmp and refuses.
```

## Limitations

One host, one guest, one libc, one DynamoRIO revision. The span measurement
covers this allocation sequence; it shows DBI interleaves nothing *here*, not
that DR can never interleave for any sequence. The separability claim is w10's,
read from DR headers in a local build tree rather than the pinned revision.
