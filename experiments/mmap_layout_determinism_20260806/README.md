# Guest-visible address-space layout: deterministic per backend, divergent across them — including a pointer-comparison flip

**Task:** `mmap-address-space-layout-determinism` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Answer, split in two

The task asks whether **the guest** observes a deterministic address space. Two different properties,
two different answers:

* **Within a backend: PASS.** ptrace, DBI and SaBRe each produce a **byte-identical** layout across
  separate hermit invocations, while the native control **varies** (ASLR on) — so the stability is
  earned, not vacuous.
* **Across backends: FAILS**, and — this is the part that matters — **in a way address normalization
  cannot repair.**

## Method

Guest `guest_layout.c` prints **raw** guest-visible facts, deliberately unnormalized: three `mmap`
returns, the delta between two of them, `sbrk(0)`, a `malloc` pointer, a stack local, a static, `main`,
and two pointer-comparison booleans. Any run-to-run difference in that output *is* guest-observable
nondeterminism by definition. Two separate hermit invocations per backend,
`--strict --no-virtualize-cpuid --max-timeslice=disabled`.

## The cross-backend table

| field | ptrace | dbi | sabre |
| --- | --- | --- | --- |
| `mmap1` | 0x7ffff7fb9000 | 0x7ffff7e4a000 | 0x7ffff78a4000 |
| `mmap2` | 0x7ffff7fa0000 | 0x7ffff7e3a000 | 0x7ffff5870000 |
| `mmap3` | 0x7ffff7fb8000 | 0x7ffff7e39000 | 0x7ffff78a3000 |
| **`mmap2 - mmap1`** | **−102400** | **−65536** | **−33767424** |
| `brk0` | 0x405000 | 0x406000 | **0x555555571000** |
| `malloc` | 0x4052a0 | 0x4062a0 | 0x5555555712a0 |
| `stacklocal` | 0x7fffffffb84c | 0x7fffffffaf7c | 0x7fffffffb71c |
| `static` | **0x404040** | **0x404040** | **0x404040** |
| `main` | **0x401156** | **0x401156** | **0x401156** |
| **`m2 < m3`** | **1** | **0** | **1** |

### 1. The static image agrees — so this is not general chaos

`static` and `main` are **identical on all three backends**. The executable's load address is
deterministic and consistent. Whatever is diverging, it is not the image.

### 2. The relative spacing differs, so a base shift is not the explanation

`mmap2 − mmap1` is −102400 under ptrace, **−65536** under DBI, **−33767424** under SaBRe. A pure base
relocation would *preserve* that delta. It does not. **Therefore subtracting a base, or assigning
first-appearance ordinals, cannot normalize these into agreement** — the internal geometry of the mmap
region differs, not just where it starts.

This is directly load-bearing for the parity work: the register-hash canonicalization preserves "order
and aliasing" precisely because those are supposed to be the invariant signal underneath differing
absolute addresses. Here that assumption does not hold across backends.

### 3. A guest-observable pointer comparison flips

`m2 < m3` is **1 under ptrace and SaBRe, 0 under DBI.**

That is not a cosmetic address difference — it is a **boolean the guest can branch on**. A program that
compares two pointers takes a **different code path** under DBI than under ptrace. Any such guest will
diverge in behaviour, not merely in printed output, and no output-normalization can hide it (nor
should it).

### 4. SaBRe puts the heap in a different region class

`brk0` is 0x405000 (ptrace) / 0x406000 (DBI, one page higher) / **0x555555571000** (SaBRe). Under
SaBRe the heap sits in the PIE-style 0x5555… range while the image is at 0x40…; `malloc` follows `brk`
in each case. So under SaBRe the heap is nowhere near the image, unlike the other two.

This corroborates from the guest side what I measured from the tool side in
`experiments/dbi_detlog_parity_answered_20260806`: heap/stack **regions**, not their contents, are what
differ between backends.

## What this means for the north star

The task's own warning — *"Do NOT paper over by normalizing the OUTPUT — the guest-visible layout
itself must be deterministic"* — is exactly right, and findings 2 and 3 show why it is not merely a
matter of taste: **normalization is not even available as a shortcut here.** Two of the four divergence
classes (relative spacing, comparison order) survive any base/ordinal canonicalization.

The tractable framing: per-backend determinism already holds. The open work is making the **layout
policy itself** identical across backends — a single deterministic allocator policy for the mmap region
and a consistent brk placement — rather than trying to reconcile the outputs afterwards.

## Scope and limits

* **KVM not tested** — it livelocks at guest startup on this host (reverie `640c5bc`).
* **One guest**, single-threaded; no `dlopen`, no thread stacks, no vDSO probing.
* I did **not** determine *why* DBI's and SaBRe's mmap geometry differs — that is the next step, and it
  is backend code I have no slot to change.
* Per-backend determinism was checked at N=2 invocations, not a large sample.

## Provenance (#268)

Binary `worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features
third-party-backends`. Guest `~/.local/hermit-deps/guests/guest_layout` (`gcc -O1`, source committed
here as `guest_layout.c`). `LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`. Raw per-backend outputs
committed as `out-{ptrace,dbi,sabre}.txt`.
