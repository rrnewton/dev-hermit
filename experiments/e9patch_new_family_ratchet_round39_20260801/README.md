# e9patch corpus ratchet — round 39 (madvise advice, mmap flag, socket option)

## Question

Round 39 of the standing e9patch corpus ratchet. Can six freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — four more
`madvise` advice codes (`MADV_NORMAL`, `MADV_SEQUENTIAL`, `MADV_RANDOM`,
`MADV_DONTFORK`), an `mmap` `MAP_POPULATE` flag path, and the `SO_SNDLOWAT`
socket option — reach L2 parity across the golden ptrace backend and the
e9patch-rewritten ptrace path?

**Answer: yes, all six.** Corpus 248 → 254, 254/254 PASS_L2, zero drops.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`), then e9patch L2-tested (`--backend e9patch`:
candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, deterministic
e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only if native,
golden, and e9 all pass AND agree; any guest whose golden output diverges from
native is DROPPED (no false parity, hermit issue #152). Both layers were run;
all six passed both.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| madvise_normal | madvise(28) MADV_NORMAL=0 | default no-op advice succeeds | `madvnormal=0` |
| madvise_sequential | madvise(28) MADV_SEQUENTIAL=2 | read-ahead hint succeeds | `madvseq=0` |
| madvise_random | madvise(28) MADV_RANDOM=1 | read-ahead hint (opposite) succeeds | `madvrandom=0` |
| madvise_dontfork | madvise(28) MADV_DONTFORK=10 | page-property hint succeeds | `madvdontfork=0` |
| mmap_populate | mmap(9) MAP_POPULATE 0x8022 | prefaulted page is writable (sentinel echo) | `mmappopulate=42` |
| getsockopt_sndlowat | getsockopt(55) SO_SNDLOWAT=19 | send low-water mark fixed at 1 | `sndlowat=1` |

The four `madvise` guests extend the covered advice set
(DONTNEED/WILLNEED/FREE) with four more inert hints; `mmap_populate` adds the
`MAP_POPULATE` flag path beyond `mmap_anon`/`mmap_noreserve`;
`getsockopt_sndlowat` is the send-side counterpart of the round-38
`getsockopt_rcvlowat` (both fixed constants; SO_SNDLOWAT is unchangeable at 1
on Linux).

## Results

- native: 6/6 exit 0 with expected stdout.
- golden ptrace: 6/6 L2, native-matching stdout.
- e9patch: 6/6 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **254/254 PASS_L2** (248 → 254, net +6, 0 drops).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
