# Session 4 — the reference value is not a constant, and native is not a stable oracle

**Date:** 2026-08-06 · **Host:** devbig014 · **Task:** `dbi_packs_anonymous_mmaps` · **Agent:** hermit-w4

Extends `README.md` (session 3). Sessions 1–3 established the reproduction, the
native oracle, the hole-structure model, and — by measurement — that no DBI-side
relocation can produce ptrace's `-8`. **None of that is contradicted here.** All
three sessions measured at a single point, and this session sweeps it.

## Question

Sessions 1–3 answered *"can DBI be changed to produce -8?"* (no). They did not
ask *"is -8 a stable thing to match?"* This session asks that.

## Method

Every prior measurement used the fixture's own allocation sequence: four
anonymous `mmap`s of 1, 2, 3, 4 pages with **nothing before them**. `prefix-sweep.c`
adds `K` pages of anonymous `mmap` before that sequence, `K = 0..8`, and reports
the same three page deltas. Everything else is identical: same host, same
`hermit` binary (`worktrees/dbi/hermit/target/release/hermit`, release,
third-party-backends), same `--strict`.

Native was run **20×** per cell, hermit arms 3× per cell, and the table reports
the modal outcome **with its count and the number of distinct outcomes** — see
§3 for why a single native sample is not safe.

Raw data: `prefix-sweep.tsv`. Guest: `prefix-sweep.c`.

## 1. Baseline reproduced, then the prediction

Baseline first, independently, on one binary with the original fixture guest:
native ×2, ptrace, dbi all give `cmp=000000111101 word=961f1af9f2b1a9bb`;
`d23 = -8, -8, -8, -4`. Unchanged from sessions 1–3.

The session-3 model says `-8` is **one leftover free page** in a 7-page glibc
loader gap. If that is right, consuming the gap first must erase the skip and
force ptrace to pack contiguously — i.e. to produce DBI's answer. Stated before
running: **`K = 7` should make ptrace produce `-2 -3 -4`.**

## 2. Result — it held exactly, and the two backends converge

| K | native (20×) | ptrace | dbi | |
|---:|---|---|---|---|
| 0 | -2 -3 **-8** | -2 -3 **-8** | -2 -3 -4 | |
| 1 | -2 -3 **-7** | -2 -3 **-7** | -2 -3 -4 | |
| 2 | -2 **-8** -4 | -2 **-8** -4 | -2 -3 -4 | |
| 3 | -2 **-7** -4 | -2 **-7** -4 | -2 -3 -4 | |
| 4 | -2 **-6** -4 | -2 **-6** -4 | -2 -3 -4 | |
| 5 | **-6** -3 -4 | **-6** -3 -4 | -2 -3 -4 | |
| 6 | **-5** -3 -4 | **-5** -3 -4 | -2 -3 -4 | |
| **7** | **-2 -3 -4** | **-2 -3 -4** | **-2 -3 -4** | **← identical** |
| 8 | -2 -3 **-16** | -2 -3 **-16** | -2 -3 -4 | |

**At K = 7 the two backends agree byte-for-byte.** So this is not a fixed
backend-versus-backend offset; there is a guest allocation prefix at which the
"divergence" is zero. That is the second known convergence point — session 2
found the first at a 1-page final allocation.

On `d23` alone the arms **agree at 6 of 9 prefixes** (K = 2..7) and disagree at
3 (K = 0, 1, 8). The task's headline `-4 vs -8` is one sample of a function,
presented as a constant.

## 3. Which arm is the erratic one — the framing inverts

Across K = 0..8:

* native / ptrace produce **nine distinct triples** — a different layout for
  every prefix.
* DBI produces **one** triple, `-2 -3 -4`, for all nine.

**DBI is the stable arm. ptrace is the sensitive one.** ptrace's layout is a
function of glibc's incidental hole structure, so it moves whenever the guest's
allocation history moves; DBI's four allocations land in a large clean gap left
by DynamoRIO's runtime and pack contiguously regardless.

This does **not** overturn "ptrace is faithful": native == ptrace at 9 of 9
prefixes, which is exactly what fidelity means. It does show that *faithful* and
*stable* are different properties, and that sessions 1–3's "ptrace is the
reference" silently assumed the reference is a constant.

## 4. Native has a tail — it is not a deterministic oracle

Sessions 1–3 sampled native 2–3 times and read `-8` as native's value. With a
tail of ~1%, three samples miss it 96% of the time. Sampling harder:

| arm | runs | modal outcome | modal count | tail |
|---|---:|---|---:|---|
| native, K=0 | 300 | `d23 = -8` | 296 | 4 runs at −530/−531/−532 (**1.3%**) |
| native, K=1 | 300 | `d23 = -7` | 294 | 6 runs at −530/−531/−532 (**2.0%**) |
| ptrace, K=0 | 20 | `d23 = -8` | 20 | **none** |
| dbi, K=0 | 20 | `d23 = -4` | 20 | **none** |

`/proc/sys/kernel/randomize_va_space = 1`, and the effect survives `setarch -R`
in small samples (10/10 modal), so the tail is a rare rearrangement of the
loader's holes rather than plain base randomisation.

Two consequences:

1. **Both hermit arms are perfectly deterministic and native is not.** Hermit is
   doing its job. The `-8` that ptrace reproduces is native's *mode*, not
   native's only answer.
2. "native == ptrace" is a statement about the modal native run. It remains the
   right basis for calling ptrace faithful, but it is not the invariant the
   phrase suggests.

## 5. A code-level premise this falsifies

`hermit-cli/src/replayer/mmap.rs:43`:

```rust
// Let anonymous mappings through. This should already be deterministic
// because ASLR is disabled.
if flags.contains(MapFlags::MAP_ANONYMOUS) {
    return guest.inject_with_retry(syscall).await;
}
```

Anonymous mappings are *not* replayed from the log; they are re-injected and
whatever the kernel returns is used, justified by "ASLR is disabled". The sweep
shows the real precondition is stronger: anonymous placement is deterministic
given an **identical address space**, and it is highly sensitive to that address
space (nine distinct layouts across nine prefixes).

**This is not a live bug** — record/replay is declared unsupported on DBI, KVM,
SaBRe and LiteInst in the manifests, so replay only ever runs ptrace→ptrace with
the same address space. It is recorded because it is the assumption that breaks
first if replay is ever extended across backends, and because the comment states
a weaker precondition than the code actually needs.

**Also relevant to sizing the fix:** the same file *does* force addresses for
file-backed maps — `syscall.with_addr(Some(addr))` followed by
`assert_eq!(ptr as usize, event.addr, "Failed to inject mmap at desired address")`
(line 80). So the "inject this mapping at a chosen address" half of a
determinized allocator is already shipped and proven in-tree. What is missing is
an address *source* and `MAP_FIXED` semantics for a guaranteed rather than
hinted placement. That is smaller than "build an allocator from scratch", though
still a determinization-strategy decision.

## 6. Conclusion

The acceptance criterion — *"d23 and the anon-mmap fixtures show identical page
layout DBI vs ptrace"* — is **ill-posed as a DBI-side fix target**:

* there is no single ptrace layout to match (§2): matching `-8` at K=0 also
  means matching `-7` at K=1, `-16` at K=8 and the `d01`/`d12` shifts at K=4..6;
* doing so means reproducing one libc's loader hole structure for every possible
  allocation sequence, and session 3 showed DBI's address space contains **zero**
  holes of that shape;
* the arm being held up as the reference is the *less* stable of the two (§3),
  and the native value it derives from has a 1.3–2% tail (§4).

The remaining lever is unchanged from sessions 1–3: **detcore assigns guest mmap
addresses itself** instead of returning the kernel's choice. This session adds
the cost of that choice, quantified: it would move ptrace away from native at
**9 of 9** prefixes, not just at K=0. Native-fidelity and backend-independence
are mutually exclusive here, and picking between them is an owner decision, not
an implementation detail.

## Reproduction

```bash
gcc -O1 -Wall -Werror -D_GNU_SOURCE -o mlo_prefix prefix-sweep.c
H=worktrees/dbi/hermit/target/release/hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
for k in 0 1 2 3 4 5 6 7 8; do
  ./mlo_prefix $k
  $H run --backend ptrace --strict -- ./mlo_prefix $k
  $H run --backend dbi    --strict -- ./mlo_prefix $k
done
# the guest must NOT live under /tmp: hermit replaces guest /tmp with an
# isolated directory and refuses the run.
```

## Limitations

One host, one guest, one libc. The sweep covers K = 0..8; it establishes that
the reference value varies and that a convergence point exists, not the full
shape of the function. The native tail is characterised at K = 0 and K = 1 only.
