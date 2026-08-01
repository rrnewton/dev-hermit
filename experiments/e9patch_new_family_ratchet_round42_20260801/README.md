# e9patch corpus ratchet — round 42 (fcntl OPs, ioctl, socket options, madvise, xattr)

## Question

Round 42 of the standing e9patch corpus ratchet. Can seven freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — the pipe-resize
`fcntl(F_SETPIPE_SZ)` and OFD-lock `fcntl(F_OFD_GETLK)` paths, a new working
`ioctl(FIOCLEX)` request, a receive-timeout socket option (`SO_RCVTIMEO`), a
settable socket option (`SO_BROADCAST`), a new madvise advice (`MADV_COLD`), and
a path-based `listxattr` size query — reach L2 parity across the golden ptrace
backend and the e9patch-rewritten ptrace path?

**Answer: yes, all seven.** Corpus 268 → 275, 275/275 PASS_L2. One additional
candidate was probed and dropped (golden diverges from native).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored
guest was then native-tested, golden-hermit-ptrace L2-tested (`--strict
--verify`), and e9patch L2-tested (`--backend e9patch`: candidate_sites>0,
mapped==candidate, no SIGILL fallback `b0==0`, deterministic e9loader
`prologue=8`, DETLOG tail-match). A candidate is KEPT only if native, golden, and
e9 all pass AND agree; any guest whose golden output diverges from native is
DROPPED (no false parity, hermit issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (7)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| fcntl_setpipe_sz | fcntl(72) F_SETPIPE_SZ=1031 | 4096 request grants exactly one page | `setpipesz=4096` |
| fcntl_ofd_getlk_memfd | fcntl(72) F_OFD_GETLK=36 | unlocked memfd → l_type rewritten to F_UNLCK | `ofdgetlk=2` |
| ioctl_fioclex_pipe | ioctl(16) FIOCLEX=0x5451 | set close-on-exec returns 0 | `fioclex=0` |
| getsockopt_rcvtimeo | getsockopt(55) SO_RCVTIMEO=20 | fresh socket receive timeout unset (tv_sec 0) | `rcvtimeo=0` |
| setsockopt_broadcast | setsockopt(54) SO_BROADCAST=6 | settable boolean option set returns 0 | `setbroadcast=0` |
| madvise_cold | madvise(28) MADV_COLD=20 | advisory deactivation hint returns 0 | `madvcold=0` |
| listxattr_devnull | listxattr(194) /dev/null | device node carries no user xattrs | `listxattr=0` |

`fcntl_setpipe_sz` exercises the pipe-resize write path (distinct from the
read-only `F_GETPIPE_SZ` guest); `fcntl_ofd_getlk_memfd` exercises the OFD-lock
struct-copyout path (distinct from process-associated `F_GETLK`);
`ioctl_fioclex_pipe` adds a new working ioctl request beyond
FIONREAD/FIONBIO/TCGETS; `setsockopt_broadcast` is a new *set* option beyond the
existing setsockopt_reuseaddr guest; `madvise_cold` adds a new advice code.

## Dropped (1)

| candidate | syscall | reason |
|-----------|---------|--------|
| prctl PR_GET_FP_MODE | prctl(157) op=46 | golden hermit returns -ENOSYS (-38); native returns -EINVAL (-22) → hermit limitation, not parity (#152) |

## Results

- native: 7/7 exit 0 with expected stdout.
- golden ptrace: 7/7 L2, native-matching stdout.
- e9patch: 7/7 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **275/275 PASS_L2** (268 → 275, net +7, 1 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
