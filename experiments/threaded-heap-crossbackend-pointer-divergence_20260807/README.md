# Threaded heap cross-backend divergence: one mmap'd-stack pointer per thread

**Bottom line.** The task premise is refuted and the real mechanism is identified
exactly. Threaded heap **self**-determinism is `38/38 PASS` on *both* backends —
it was never failing. The `1/38` is **cross-backend parity**, and it is caused by
**exactly one heap word per thread**: a pointer to that thread's `mmap`'d
stack/TLS block. ptrace places those blocks near `0x7ffff7bff000`; the KVM guest
address space **ends at `0x40000000`** and cannot contain that address at all, so
the pointers differ by construction and every heap hash taken after the first
thread is created diverges.

## Question

At Hermit `590fcc9e`, a multi-guest spot-check reported threaded heap `1/38`
against `heap_exercising` `6/6`. The originating task read `1/38` as a
*self*-determinism failure — "the same backend fails to reproduce its own heap
hashes 37 times out of 38" — and asked what threading introduces to break it.

## Premise check (do this first)

The cited artifact's own machine-readable rows say the opposite of the task text:

```
threaded,heap,ptrace,38,38,38,38,PASS     <- SELF-determinism, ptrace: 38/38
threaded,heap,kvm,38,38,38,38,PASS        <- SELF-determinism, KVM:    38/38
threaded,heap,1,38,FAIL                   <- CROSS-BACKEND parity:      1/38
heap_exercising,heap,6,6,PASS             <- CROSS-BACKEND parity:      6/6
```

`1/38` is cross-backend parity, from a **separate table** whose own heading reads
"Cross-backend results are emitted only after both backend self-checks pass". The
row exists *because* both self-checks passed; a self-determinism failure would
have rendered it `NOT-COMPARABLE`, as it did for six of the eight rows.

Corroborated by the per-run whole-sequence digests: threaded ptrace is
`d85452e4…` on both runs, threaded KVM is `8f9e8685…` on both runs — each backend
perfectly reproduces itself, and the two disagree. `heap_exercising` is
`c0dd4ab7…` for all four runs, which is why its parity is `6/6`.

Reproduced live at `8eb03238`: threaded heap self-determinism **38/38 on both
backends**, cross-backend **1/38**. The prior numbers replicate exactly.

The `1/38`-vs-`6/6` contrast is real and apples-to-apples — both are cross-backend
parity. Only the label was wrong, so the task's motivating point (a single-guest
measurement on `heap_exercising` would have read HEALTHY) still stands.

## Method

Four guests, two backends, two runs each, `--strict`, no relaxations. Records are
compared ordinally. The `size=` domain field from hermit PR #1875 is required:
without it a digest disagreement cannot be split into MEASUREMENT DOMAIN (the two
runs hashed different numbers of bytes — an unmeasured comparison) versus BYTE
CONTENT (same extent, different bytes — a real divergence).

Three purpose-built guests isolate one variable each:

| guest | threads | pointer stored in heap | purpose |
|---|---|---|---|
| `heap_const` | no | no | baseline |
| `heap_ptr` | no | **yes** (its own address) | tests "stored pointers diverge" |
| `thread_const` | **yes** | no | tests threading with zero guest pointers |

`heap_snap` then snapshots the heap into `.bss` *before printing* and dumps every
nonzero word, across thread counts 1/2/4/8.

## Results

Cross-backend heap parity (ptrace vs KVM), all self-checks passing first:

| guest | equal | compared | DOMAIN | CONTENT | verdict |
|---|---:|---:|---:|---:|---|
| `heap_const` | 3 | 3 | 0 | 0 | PASS |
| `heap_ptr` | 3 | 3 | 0 | 0 | **PASS** |
| `heap_exercising` | 6 | 6 | 0 | 0 | PASS |
| `thread_const` | 5 | 42 | 0 | **37** | FAIL |
| `pthread_lifecycle` | 1 | 38 | 0 | **37** | FAIL |

Two things fall out immediately. **`heap_ptr` passes** — storing an absolute
pointer in the heap does *not* break parity, because both backends place the heap
at the same base (`0x405000`), so the stored value is identical. And
`thread_const` fails with **zero pointers stored by the guest**, at the same
`37` content divergences as the real threaded fixture. Threading is the variable;
"pointers" as such is not.

Every failure is `0 DOMAIN / all CONTENT` at identical bases and identical
`size=0x21000`. This is genuine byte divergence at the same address, not a
measurement artifact — a distinction that could not be drawn before PR #1875.

### The mechanism: one word per thread

Differing heap words, by thread count:

| threads | nonzero words | **differing words** |
|---:|---:|---:|
| 1 | 6 | **1** |
| 2 | 10 | **2** |
| 4 | 18 | **4** |
| 8 | 40 | **8** |

Exactly one per thread, at a 288-byte stride (offsets 704, 992, 1280, 1568, …):

```
off  704   ptrace 00007ffff7bff5b8  ->  kvm 0000000001b515b8
off  992   ptrace 00007ffff73fe5b8  ->  kvm 00000000023525b8
off 1280   ptrace 00007ffff6bfd5b8  ->  kvm 0000000002b535b8
off 1568   ptrace 00007ffff63fc5b8  ->  kvm 00000000033545b8
```

The **low 12 bits are identical (`5b8`) in every pair** — the same field of the
same structure, at a different base. ptrace's blocks descend from
`0x7ffff7bff000` in steps of `0x801000`; KVM's ascend from `0x01b51000` in steps
of `0x1001000`. These are glibc's per-thread records, each holding a pointer to
that thread's `mmap`'d stack/TLS block.

For `pthread_lifecycle`, **the single matching record is index 1** — the one
sampled before any thread's pointer reaches the heap. Records 2–38 all diverge.

### Why this cannot be fixed by aligning addresses

The KVM guest's stack record ends at `guest_end = 0x40000000`: the guest address
space is **1 GiB**. Linux's mmap base of `0x7ffff7bff000` is ~128 TiB up and
**cannot exist inside it**. So KVM cannot reproduce ptrace's thread-stack
addresses by re-basing; the divergence is a consequence of the guest memory
model, not a bug in the scheduler or in either backend's determinism.

## Interpretation

Threaded cross-backend heap parity is **architecturally unattainable** while the
KVM guest is a 1 GiB flat space, for any guest that creates a thread. It is not a
determinism defect: both backends are perfectly self-deterministic on this
dimension (`38/38` each), which is the property the scheduler is responsible for.

Three remedies exist and **all are owner decisions, none taken here**:

1. Enlarge / relocate the KVM guest address space to mirror Linux's layout —
   large architectural change.
2. Make the heap hash pointer-aware (ordinalize heap-resident pointers before
   hashing) — changes the oracle, and would mask real pointer-valued divergence.
   This is the same "normalize until the numbers agree" trap refused in
   PR #1875.
3. Declare threaded heap cross-backend parity out of scope and assert
   self-determinism per backend instead — which already passes.

Reporting `1/38` as a bare ratio invites remedy 2. The decomposition
(`0 DOMAIN / 37 CONTENT`, one word per thread, an unreachable address) is what
makes remedy 2 visibly wrong.

## Ratios, before and after

Nothing was changed, so no ratio moved; what changed is that each is now
attributed. Stating both explicitly so the record cannot be misread as a fix:

| measurement | guest | before | after |
|---|---|---|---|
| heap **self**-determinism, ptrace | `pthread_lifecycle` | 38/38 PASS | 38/38 PASS |
| heap **self**-determinism, KVM | `pthread_lifecycle` | 38/38 PASS | 38/38 PASS |
| heap **cross-backend** parity | `pthread_lifecycle` | 1/38 | 1/38, now `0 DOMAIN / 37 CONTENT` |
| heap **cross-backend** parity | `heap_exercising` | 6/6 PASS | **6/6 PASS** (reconfirmed) |

`heap_exercising` was re-run and still passes: self `6/6` on both backends, cross
`6/6`, `0 DOMAIN / 0 CONTENT`. Nothing was traded between guests.

## Reproduction

```bash
cc -O2 -g -std=c11 -Wall -Wextra -Werror -pthread heap_const.c   -o heap_const
cc -O2 -g -std=c11 -Wall -Wextra -Werror -pthread heap_ptr.c     -o heap_ptr
cc -O2 -g -std=c11 -Wall -Wextra -Werror -pthread thread_const.c -o thread_const
cc -O2 -g -std=c11 -Wall -Wextra -Werror -pthread heap_snap.c    -o heap_snap

# per guest, per backend in {ptrace,kvm}, twice:
hermit --log=info --backend=$BE run --strict --detlog-heap -- ./$GUEST

# heap-word localization (bounds come from the detlog record of the same binary):
hermit --backend=$BE run --strict -- ./heap_snap $NTHREADS 485000 4a6000
```

Guests must **not** live under `/tmp`: Hermit replaces guest `/tmp` with an
isolated directory and refuses with a clear message. The first attempt here
produced 12 `rc=1` runs for that reason — a setup failure, recorded as **zero
qualifying trials, not as a negative result**.

`heap_snap` snapshots into `.bss` before printing because an earlier probe that
printed as it walked contaminated its own output: stdio's buffer lives on the
heap, so the dump contained ASCII renderings of the very pointers under test
(~500 spurious differing words at offsets ≥1880). Only the snapshot version's
counts are used above.

## Limits

- Two backends (ptrace, KVM). DBI/SaBRe/LiteInst not measured.
- Two runs per cell — enough to establish self-determinism failure, not enough to
  establish its absence at high confidence.
- Not a strict-verifier receipt: no `--verify-strict --verify-json`, no planted
  comparator mutation. Cross-backend numbers come from comparing two independent
  runs' logs, since KVM `--verify` is output-only and cannot reach L2/L3.
- Incidental finding, not chased: the KVM guest's `/proc/self/maps` contains **no
  `[heap]` line**, so a guest cannot discover its own heap bounds under KVM. The
  probe had to be given them explicitly.
- The KVM **stack** dimension fails self-determinism (`38/59`) and is a separate
  defect from anything here.
