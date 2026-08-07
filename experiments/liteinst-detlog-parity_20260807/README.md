# LiteInst DETLOG: parity is low, and the "already clean" premise fails on the second cell

**Task:** `liteinst-detlog-was-already-clean-score-its-parity-now` · hermit-w7
(`[impl agent, opus-5]`) · **2026-08-07** · local, no egress.
**Arms:** hermit `0041130ccb0d` (ancestor of main — **landed** behaviour) and `077833ad6595`
(head of open PR [#1847](https://github.com/rrnewton/hermit/pull/1847) — **not** landed).
Both release, same worktree, identical Reverie pin `0ae0c01b`, neither `-dirty`.

## 0. Two answers, and the second one was not the question

**Asked:** score LiteInst cross-backend DETLOG parity, per cell, with denominators.
**Answer:** parity is **low** — order-preserving coverage of the ptrace golden runs 27–56 % under
the repo's own hex-normalisation policy, prefix depth is 10 records on every cell.

**Not asked, and it matters more:** the task's own Verify clause demands "self-determinism
status recorded on every cell". That clause is what caught the premise failing.
`0/1245 differing` was measured on **one guest**. On the second cell I tried,
**LiteInst DETLOG is self-NONdeterministic** — and the guest is `detlog_syscalls`, which is
`ci-hub/parity`'s own pinned reference guest for this very dimension.

The task warned "Do NOT infer any other dimension from this — the reverse inference was nearly
published as a false finding, and the forward one is equally wrong." The same caution applies
one level down, to **cells**, and the task premise did not apply it to itself.

## 1. The premise failure, at n=30 per arm

`detlog_syscalls`, `--strict --base-env=minimal`, no memory-hash flags, 30 runs per arm:

| arm | backend | DETLOG records | distinct outcome classes / 30 | verdict |
| --- | --- | --- | --- | --- |
| landed `0041130c` | ptrace | 336 | **1** (30) | self-deterministic |
| landed `0041130c` | **liteinst** | 1072 | **2** (17 / 13) | **SELF-NONDETERMINISTIC** |
| PR #1847 `077833ad` | liteinst | 1072 | **1** (30) | self-deterministic |

The record **count** is 1072 in every single run, so the schedule and event sequence are
deterministic; only content moves.

### The signature is exact, and it is virtual time

Of 1072 records, the differing set is **exactly the 32 `clock_gettime(CLOCK_MONOTONIC)`
records** (64 lines, inbound + finish). Every other record — including every
`/proc/self/maps` read and its byte count — is byte-identical between the two classes.

The delta is a **constant 6720 ns on all 32 reads**, not accumulating drift:

```
class A: ... clock_gettime(CLOCK_MONOTONIC, HEX -> { tv_sec: 1767225600, tv_nsec: 26182050 })
class B: ... clock_gettime(CLOCK_MONOTONIC, HEX -> { tv_sec: 1767225600, tv_nsec: 26175330 })
                                                                          ^ 6720 ns, every time
```

`NANOS_PER_RCB = 10.0` (`detcore-model/src/time.rs:39`), so **6720 ns is exactly 672 retired
conditional branches.** One perturbation of 672 RCBs lands somewhere before the guest's first
clock read (record 811 of 1072) and every later read carries it forward unchanged.

### What I did NOT establish, and the candidate I can rule out

I have **not** root-caused which pre-clock event contributes those 672 RCBs. Stating that
plainly matters, because the obvious guess is wrong:

- The obvious guess — the LiteInst runtime parses `/proc/self/maps`, whose memfd inode field
  is a host-global counter of *varying digit width* (I observed widths 4, 5 and 6 across 20
  runs), so a wider inode is more parse work and more branches.
- **Evidence against it:** the two classes have byte-identical `read()` records for the maps
  fd, including the terminal short read, so the maps text was the *same total length* in both.
  If digit width had differed, that length would have moved.

So the perturbation is in **unlogged retired-branch counting**, not in anything syscall-visible.
That leaves PMU skid and a genuine unlogged control-flow difference both live, and I did not
separate them. Two classes over 30 runs rather than a continuous spread argues weakly for a
binary control-flow branch over skid noise, but 30 runs is not enough to settle it.

### What IS established: the intervention works

PR #1847 — which determinizes the guest-visible maps inode column — takes this from 2 classes
in 30 runs to **1 class in 30 runs**. That is an intervention result, and it is the strongest
statement available without the root cause. It also means the fix's reach is wider than its own
PR body claims: #1847 is written up as a *memory-hash* fix, and it silently repairs a **DETLOG
virtual-time** defect too.

## 2. Cross-backend DETLOG parity, per cell, with denominators

7 cells × 2 backends × 3 runs, at PR #1847's head (where every cell is self-deterministic, so
every parity number below rests on a measurable baseline). Numbers are identical at the landed
arm except that `detlog_syscalls`'s liteinst baseline fails there, which makes its landed
parity cell **NOT-MEASURABLE** rather than merely low.

| cell | Z (ptrace) | E (liteinst) | Y raw | Y hex | covered hex | cover % | golden uncovered | liteinst inserted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bin_true` | 68 | 804 | 6 | 10 | 36 | 53 % | 32 | 768 |
| `bin_echo` | 84 | 814 | 6 | 10 | 44 | 52 % | 40 | 770 |
| `stack_deep_recursion` | 80 | 818 | 6 | 10 | 39 | 49 % | 41 | 779 |
| `stdout_bytes` | 82 | 818 | 6 | 10 | 40 | 49 % | 42 | 778 |
| `notsc` | 96 | 832 | 6 | 10 | 47 | 49 % | 49 | 785 |
| `heap_fragment_reuse` | 98 | 836 | 6 | 10 | 51 | 52 % | 47 | 785 |
| `detlog_syscalls` | 336 | 1072 | 6 | 10 | 56 | **17 %** | 280 | 1016 |

Self-determinism, recorded on every cell rather than inherited: **0 of 3 pairs differing on
both backends for all 7 cells at `077833ad`**; at `0041130c`, 6 of 7 cells the same and
`detlog_syscalls`/liteinst at **2 of 3 pairs differing**.

Three policies are reported because one number would mislead in one direction or the other.
`raw` is byte-exact. `hex` maps `0x<hex>` → `HEX` and is **not** a policy invented here to
manufacture a green — it is verbatim what `ci-hub/parity/prefix_depth.sh`'s `commits()` already
does. Coverage is a *second* measurement, not a relaxation: insertions are reported separately
and never subtracted, so "LiteInst adds 785 runtime records" cannot read as agreement.

### Why prefix depth is 6, and why that number is not the interesting one

Raw prefix depth is 6 on **every** cell because record 7 is
`arch_prctl(ARCH_SET_FS, <stack address>)` and the addresses differ — LiteInst's
`LD_PRELOAD` entry enlarges the environment block, which moves the initial stack pointer. Under
`hex` it extends to 10, where LiteInst inserts the `openat` of its own runtime `.so`. Prefix
depth saturates immediately for any preload backend and cannot ratchet; coverage is the metric
that can.

### What the gap is actually made of

Golden records LiteInst never covers (hex policy):

| cell | top uncovered golden syscalls |
| --- | --- |
| `bin_true` | 8 `mmap`, 4 `pread64`, 3 `openat`, 3 `mprotect`, 2 each `access`/`close`/`arch_prctl`/`rseq` |
| `notsc` | 8 `mmap`, **8 `getpid`**, 4 `pread64`, 4 `brk`, 3 `openat`, 3 `mprotect` |
| `detlog_syscalls` | **62 `clock_gettime`, 62 `getpid`, 62 `dup`, 55 `close`**, 8 `mmap` |

This is the important structural point. The uncovered set is **not** only loader noise: on
`notsc` all 8 of the guest's own `getpid()` calls are uncovered, and on `detlog_syscalls`
241 of the guest's own `clock_gettime`/`getpid`/`dup`/`close` calls are. The guest's records
*are present* in the LiteInst stream but do not match textually — different virtual-time values
and different addresses — so a subsequence match cannot pair them. **LiteInst does not merely
add records around an intact golden; the golden's own records are altered.**

And LiteInst inserts 785 records on `notsc` that ptrace never emits — 467 `read`, 70 `close`,
64 `mmap`, 55 `openat`, 48 `statx`, 16 each `memfd_create`/`ftruncate`. That is the preload
runtime's own bring-up executing *inside the guest*, fully visible to Detcore.

### The time-dimension gap, stated as a number

At the guest's first `clock_gettime`, virtual time reads:

| backend | first clock read | in RCBs |
| --- | --- | --- |
| ptrace | 2,241,735 ns | 224,173 |
| liteinst (`077833ad`) | 26,017,120 ns | 2,601,712 |

**11.6× more virtual time has elapsed at the same guest-visible point**, because ~2.4 M extra
retired branches of LiteInst runtime bring-up ran inside the guest first. This is not a bug —
it is the honest cost of a preload backend — but it means guest-visible time is *not* in parity
between the two backends, and any test comparing timestamps across them will diverge by
construction.

## 3. The comparator is bracketed both ways

A parity scorer that cannot see a planted change is worthless, and one that fires on an
identical rerun is noise. Both directions, 16 controls, all PASS (`comparator-controls.csv`):

| control | result |
| --- | --- |
| golden vs itself | Y=96/96, covered 96/96, 0 deleted, 0 inserted — perfect, so not lossy |
| planted substitution at ordinals 1 / 25 / 50 / 96 | detected at exactly k−1 each time |
| planted deletion of ordinal 50 | Y=49, one golden record uncovered, 0 inserted |
| planted insertion before ordinal 50 | coverage still 96/96, insertion reported separately — an extra record is never credited as agreement |
| hex policy vs a non-address change | still detected — hex is not a blanket mask |
| **end-to-end**, real guest, one extra `getpid()`, ptrace | raw Y 96→6, hex Y 96→83 — **DETECTED** |
| **end-to-end**, real guest, one extra `getpid()`, liteinst | raw Y 832→6, hex Y 832→138 — **DETECTED** |
| end-to-end negative control, unmutated rerun, both backends | Y=Z, coverage=Z — **no false positive** |

## 4. Corroboration of the upstream datum

hermit-w27 reported LiteInst DETLOG `0/1245` on `notsc` **with** `--detlog-stack`. I measure
`notsc` at **832** DETLOG records without stack hashes, and 412 `[stack]` hash records with
them. 832 + 412 = 1244 against their 1245. Independent path, same stream, off by one record.
Their measurement reproduces; it was simply generalised past its single cell.

## 5. Reproduction

```bash
harness/detlog_collect.sh <hermit-binary> <outdir> 3
python3 harness/detlog_parity.py <outdir> scores.json

# the premise failure, directly:
for i in $(seq 1 30); do
  <hermit> --log=info --backend=liteinst run --strict --base-env=minimal \
      -- ci-hub/parity/guests/detlog_syscalls 2>&1 >/dev/null \
    | grep -o 'DETLOG .*' | md5sum
done | sort | uniq -c        # landed 0041130c: 2 classes. PR #1847 077833ad: 1.
```

`harness/base-classA.detlog` and `base-classB.detlog` are the two captured outcome classes;
`diff` them and the only differing lines are the 32 `clock_gettime` records.

**Hold the hermit binary's install path fixed across arms** — the `LD_PRELOAD` absolute path
is in the guest environment block, so a different path silently changes every LiteInst record.
See `experiments/liteinst-maps-inode-self-determinism_20260807` §5.

## 6. Follow-ups this opens

1. **Root-cause the 672 RCBs.** Not done here. The maps-digit-width route is ruled out by
   identical read lengths; PMU skid vs an unlogged control-flow branch is unseparated.
2. **`detlog_syscalls` liteinst DETLOG is self-nondeterministic on landed main.** Any published
   LiteInst DETLOG cell measured at or before `0041130c` on a clock-reading guest is
   NOT-MEASURABLE, not merely low.
3. **PR #1847's reach is understated.** Its body claims a memory-hash fix; it also repairs this
   DETLOG virtual-time defect. Worth adding before it lands.
4. **Prefix depth cannot ratchet a preload backend** — it saturates at 6/10 on every cell.
   Coverage is the metric that moves. `ci-hub/parity/prefix_depth.sh` still has no `liteinst`
   row at all.
