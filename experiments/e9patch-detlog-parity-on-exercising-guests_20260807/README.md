# e9patch's first real DETLOG parity number — and the perfect column was indeed vacuous

**Agent:** hermit-w7 (`[impl agent, opus-5]`) · **2026-08-07** · hermit `723d19ad5d10`
(= `origin/main`, freshly fetched), release, not `-dirty`, Reverie pin `038e9939`,
third-party staged via `-p hermit-install`.

## 0. The question this answers, and whose question it was

The reach-matrix task (`prove-new-corpus-guests-exercise-each-patching-backend`, CLOSED) ended
with an explicit open item, quoted verbatim:

> **NOT ESTABLISHED:** e9patch reach is measured, but it is NOT shown that a rewritten site
> changes any observable — only that the rewriter mapped it. **Whether patched execution then
> AGREES with ptrace is the parity question this unblocks, not one it answers.**

This closes that. The answer is **no, and now we know by how much and why.**

Separately, the cross-backend DETLOG matrix scored e9patch `not-exercised` on all 7 of its
guests (`candidate_sites=0` everywhere) and warned that its byte-identical column was a perfect
score for a component that rewrote nothing. **That warning is now confirmed the hard way: the
moment e9patch actually rewrites, it stops matching.**

## 1. Guests that exercise e9patch

The reach-matrix artifact records the guest *shapes* but its guest **sources were never checked
in** — `origin/research/patching-backend-reach-matrix` contains only `README.md`,
`metadata.json`, `results.csv`. So that matrix is not reproducible as published. I re-authored
the three shapes it describes; they are in `guests/` here, so this one is.

| guest | shape | `syscall` insns in main ELF | candidate | **mapped** | verdict |
| --- | --- | --- | --- | --- | --- |
| `inline_syscall_sites` | inline asm in a dynamically linked main ELF | 2 | 2 | **2** | EXERCISED |
| `mixed_inline_and_libc_syscalls` | inline asm interleaved with ordinary libc calls | 1 | 1 | **1** | EXERCISED |
| `static_nolibc_syscall_sites` | `-static -nostdlib -nostartfiles`, freestanding `_start` | 2 | 2 | **2** | EXERCISED |
| `notsc` (contrast) | ordinary dynamically linked libc guest | 0 | 0 | **0** | NOT-EXERCISED |
| `/bin/true` (contrast) | system binary | 0 | 0 | **0** | NOT-EXERCISED |

The two contrast rows are carried deliberately: without them, "2" is a number with no scale and
the counter is not shown to discriminate.

## 2. The answer: patched execution does NOT agree with ptrace

30 runs per cell. Golden is ptrace on the same guest at the same binary. Coverage is
order-preserving LCS under the repo's `0x<hex>` policy.

| guest | mapped | backend | Z | E | classes/30 | Y hex | cover % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `inline_syscall_sites` | | ptrace *(self-ref)* | 78 | 78 | 1 | 78 | 100.0 |
| | **2** | **e9patch** | 78 | **90** | 1 | 5 | **92.3** |
| | | kvm | 78 | 78 | 1 | 9 | 84.6 |
| | | dbi | 78 | 78 | **30** | — | *withheld* |
| | | sabre | 78 | 61 | 1 | 4 | 12.8 |
| | | liteinst | 78 | 822 | 1 | 10 | 52.6 |
| `mixed_inline_and_libc_syscalls` | | ptrace *(self-ref)* | 86 | 86 | 1 | 86 | 100.0 |
| | **1** | **e9patch** | 86 | **98** | 1 | 5 | **88.4** |
| | | kvm | 86 | 86 | 1 | 9 | 86.0 |
| | | dbi | 86 | 84 | **30** | — | *withheld* |
| | | sabre | 86 | 69 | 1 | 4 | 9.3 |
| | | liteinst | 86 | 828 | 1 | 10 | 48.8 |
| `static_nolibc_syscall_sites` | | ptrace *(self-ref)* | 15 | 15 | 1 | 15 | 100.0 |
| | **2** | **e9patch** | 15 | **31** | 1 | 4 | **66.7** |
| | | **kvm** | 15 | 15 | 1 | **15** | **100.0** |
| | | dbi | 15 | 15 | **30** | — | *withheld* |
| | | sabre | 15 | 15 | 1 | 5 | 66.7 |
| | | liteinst | 15 | 4 | 1 | — | **NOT-APPLICABLE** |

**e9patch is self-deterministic** (1 class in 30 runs on all three guests) — so its divergence
from the golden is a real parity gap, not noise.

### Where the 92.3 % comes from — the divergence is small and fully attributable

On `inline_syscall_sites`, only **6 of 78** golden records are uncovered, and they are exactly:

```
2 × getpid, 1 × getppid, 1 × getuid, 1 × write, 1 × brk
```

Those first five are **precisely the guest's own inline syscalls — the rewritten sites.** The
records e9patch adds are 18 loader records (4 `mmap`, 2 `readlink`, 2 `open`, 2 `arch_prctl`,
2 `close`, 2 `getpid`) from the rewriting machinery bringing itself up inside the guest. And
the first divergence is the program break:

```
ptrace : brk(NULL) = Ok(4214784)
e9patch: brk(NULL) = Ok(8835211264)
```

The rewritten ELF is a different, larger image loaded elsewhere, so the break moves. That is an
unavoidable consequence of AOT rewriting, not a bug — but it does mean **e9patch can never be
byte-identical to ptrace on a guest it actually rewrites**, and any gate that expects that will
be permanently red.

## 3. Two findings that are not about e9patch

**KVM reaches its first 100 % cell** — `static_nolibc_syscall_sites`, Y hex = 15/15, coverage
100 %. This corroborates the earlier claim that KVM's residual is address formatting: a
freestanding `-nostdlib` guest maps no shared objects, so there are no `mmap` return addresses
to disagree about, and the divergence vanishes entirely. It is the cleanest evidence yet that
KVM's DETLOG gap is *formatting*, not semantics.

**LiteInst on a static guest is NOT-APPLICABLE, not 26.7 %.** 30 of 30 runs fail:

```
Error: verify LiteInst runtime before executable entry failed for tracee 3:
       tracee reached guarded executable entry 0x401053 before the required
       preload handshake completed
```

LiteInst is an `LD_PRELOAD` path and a `-static -nostdlib` binary has no dynamic loader to
preload into — the documented limitation, not a defect. It emits 4 DETLOG records before
failing, and scoring those as 26.7 % coverage would have been a false datum of exactly the kind
this line of work exists to prevent. It is recorded as NOT-APPLICABLE with the refusal text.

**DBI is withheld on all three cells**, same cause as everywhere else: 30 distinct outcome
classes in 30 runs because `dtid` is the raw host pid. See
`experiments/detlog-parity-matrix_20260807/dbi-root-cause/`.

## 4. What this changes about the matrix

The cross-backend matrix has 7 `not-exercised` e9patch cells. They are correctly classified —
those guests genuinely do not exercise e9patch — but they can now be *supplemented* rather than
just flagged: on a corpus that does exercise it, e9patch scores **66.7–92.3 %**, not 100 %.

Concretely, an e9patch parity gate should require **`mapped_sites > 0` AND a coverage figure**,
and it should not expect 100 %: the program break alone guarantees a divergence.

## 5. Not established

- **Whether the 92.3 % gap is *correct* divergence.** I showed the uncovered records are the
  rewritten sites and the inserted ones are loader activity. I did **not** verify that the
  rewritten sites produce semantically equivalent results — only that the DETLOG text differs
  and where. A rewritten `getpid` that returned the wrong value would look the same in this
  measurement as one that returned the right value at a different ordinal.
- **Anything about stack or heap.** DETLOG only; no `--detlog-stack`/`--detlog-heap`.
- **SaBRe engagement on these guests.** SaBRe exposes no site counter (the reach-matrix task's
  finding, unchanged here), so its 9.3–66.7 % cells cannot be distinguished from a silent
  ptrace fallback. They are reported, not trusted.

## 6. Reproduction

```bash
cd guests
gcc -O0 -o inline_syscall_sites inline_syscall_sites.c
gcc -O0 -o mixed_inline_and_libc_syscalls mixed_inline_and_libc_syscalls.c
gcc -O0 -static -nostdlib -nostartfiles -o static_nolibc_syscall_sites static_nolibc_syscall_sites.c

# reach first -- a cell without mapped_sites is not an e9patch cell
hermit --log=info --backend=e9patch run --strict --base-env=minimal -- ./inline_syscall_sites 2>&1 \
  | grep -o 'candidate_sites=[0-9]*; mapped_sites=[0-9]*'

./e9_collect.sh <hermit> <outdir> 30     # 540 runs, ~85s
```

`attempts.tsv` records all 540 runs with exit code, DETLOG record count and `mapped_sites`.
`reach.csv` and `parity.csv` carry every number above with its denominator.
