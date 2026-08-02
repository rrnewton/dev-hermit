# e9patch corpus ratchet — round 50 (madvise / credential / membarrier)

## Question

Round 50 of the standing e9patch corpus ratchet. With the getsockopt option-flag
lane exhausted (rounds 46-49), can twelve freestanding raw-syscall x86-64 guests
across three new families — credential no-ops, additional madvise advice codes,
and a private-expedited membarrier — reach L2 parity across the golden ptrace
backend and the e9patch-rewritten ptrace path?

**Answer: yes, all twelve.** Corpus 343 → 355, 355/355 PASS_L2. Twelve kept;
five probed candidates dropped before authoring for golden-vs-native divergence.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) issuing one classified
syscall and printing only a host-independent constant. Candidates were first
native-probed AND golden-probed (`hermit run --strict`) to catch divergence
before authoring; each authored guest was then native-tested, golden-hermit-
ptrace L2-tested (`--strict --verify`), and e9patch L2-tested (candidate_sites>0,
mapped==candidate, no SIGILL fallback `b0==0`, deterministic e9loader
`prologue=8`, DETLOG tail-match). A candidate is KEPT only if native, golden, and
e9 all pass AND agree; any guest whose golden output diverges from native is
DROPPED (no false parity, hermit issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (12)

| guest | syscall / operation | stdout |
|-------|---------------------|--------|
| setuid_noop | setuid(105) to current uid | `setuid=0` |
| setgid_noop | setgid(106) to current gid | `setgid=0` |
| madvise_hugepage | MADV_HUGEPAGE=14 | `madvhugepage=0` |
| madvise_nohugepage | MADV_NOHUGEPAGE=15 | `madvnohugepage=0` |
| madvise_wipeonfork | MADV_WIPEONFORK=18 | `madvwipeonfork=0` |
| madvise_keeponfork | MADV_KEEPONFORK=19 | `madvkeeponfork=0` |
| madvise_mergeable | MADV_MERGEABLE=12 | `madvmergeable=0` |
| madvise_unmergeable | MADV_UNMERGEABLE=13 | `madvunmergeable=0` |
| madvise_dodump | MADV_DODUMP=17 | `madvdodump=0` |
| madvise_pageout | MADV_PAGEOUT=21 | `madvpageout=0` |
| madvise_collapse | MADV_COLLAPSE=25 → -EINVAL | `madvcollapse=-22` |
| membarrier_private | membarrier REGISTER+PRIVATE_EXPEDITED | `membarpriv=0` |

MADV_COLLAPSE is kept because its -EINVAL(-22) on a 4 KiB anon range is faithful
Linux behavior — native and golden both return -22 — so it regresses the madvise
error path, not a hermit limitation.

## Dropped (5)

| candidate | syscall | reason |
|-----------|---------|--------|
| rseq | rseq(334) | golden -ENOSYS(-38), native 0 (#152) |
| name_to_handle_at | name_to_handle_at(303) | golden -EOPNOTSUPP(-95), native 0 (#152) |
| prctl_nonewprivs | prctl PR_GET_NO_NEW_PRIVS=39 | golden -ENOSYS(-38), native 0 (#152) |
| clock_getres_realtime | clock_getres(229) CLOCK_REALTIME | native 1 ns vs golden-determinized 10000 ns (#152) |
| clock_getres_proccpu | clock_getres(229) CLOCK_PROCESS_CPUTIME_ID | native 1 ns vs golden-determinized 10000 ns (#152) |

## Results

- native: 12/12 exit 0 with expected stdout.
- golden ptrace: 12/12 L2, native-matching stdout.
- e9patch: 12/12 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **355/355 PASS_L2** (343 → 355, net +12).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
