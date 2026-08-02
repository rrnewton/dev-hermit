# e9patch corpus ratchet — round 45 (pkey_mprotect, xattr-list, session, setsockopt set/confirm, mprotect-exec, madvise, IPv6)

## Question

Round 45 of the standing e9patch corpus ratchet. Can eleven freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — a
protection-key application (`pkey_mprotect`), a no-follow xattr-list query
(`llistxattr`), a new session (`setsid`), four `setsockopt` set-and-confirm
options (`SO_KEEPALIVE`, `SO_RCVBUF`, `SO_REUSEPORT`, `SO_SNDBUF`), a
non-blocking randomness fill (`getrandom(GRND_NONBLOCK)`), an executable
protection transition (`mprotect(PROT_EXEC)`), a core-dump advice
(`madvise(MADV_DONTDUMP)`), and an IPv6 socket (`socket(AF_INET6)`) — reach L2
parity across the golden ptrace backend and the e9patch-rewritten ptrace path?

**Answer: yes, all eleven.** Corpus 291 → 302, 302/302 PASS_L2. Eight additional
candidates were probed and dropped (golden diverges from native).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored
guest was then native-tested, golden-hermit-ptrace L2-tested (`--strict
--verify`), and e9patch L2-tested (candidate_sites>0, mapped==candidate, no SIGILL
fallback `b0==0`, deterministic e9loader `prologue=8`, DETLOG tail-match). A
candidate is KEPT only if native, golden, and e9 all pass AND agree; any guest
whose golden output diverges from native is DROPPED (no false parity, hermit
issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (11)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| pkey_mprotect_page | pkey_mprotect(329) | apply pkey to anon page PROT_READ | `pkeymprotect=0` |
| llistxattr_devnull | llistxattr(195) | empty no-follow xattr list size | `llistxattr=0` |
| setsid_check | setsid(112) | new session (success boolean) | `setsid=1` |
| setsockopt_keepalive | setsockopt(54) SO_KEEPALIVE=9 | set then confirm | `setkeepalive=1` |
| getrandom_nonblock | getrandom(318) GRND_NONBLOCK=1 | fill count, not bytes | `grndnb=16` |
| setsockopt_rcvbuf | setsockopt(54) SO_RCVBUF=8 | set then confirm >0 | `setrcvbuf=1` |
| setsockopt_reuseport | setsockopt(54) SO_REUSEPORT=15 | set then confirm | `setreuseport=1` |
| mprotect_exec | mprotect(10) PROT_READ\|PROT_EXEC | executable transition | `protexec=0` |
| madvise_dontdump | madvise(28) MADV_DONTDUMP=16 | exclude from core dump | `dontdump=0` |
| socket_inet6 | socket(41) AF_INET6=10 | IPv6 stream socket lowest fd | `inet6=3` |
| setsockopt_sndbuf | setsockopt(54) SO_SNDBUF=7 | set then confirm >0 | `setsndbuf=1` |

`pkey_mprotect_page` exercises the pkey application path (distinct from
`pkey_alloc_free`); the four `setsockopt` guests are set-and-confirm ops distinct
from their query-only `getsockopt_*` counterparts; `mprotect_exec` is the
executable transition distinct from `mprotect_none`/`mprotect_roundtrip`;
`socket_inet6` is a new address family. Buffer sizes, the session id, and the
random bytes are host-variable and deliberately not printed.

## Dropped (8)

| candidate | syscall | reason |
|-----------|---------|--------|
| rseq | rseq(334) | native 0; golden hermit -ENOSYS (-38) (#152) |
| futimesat | futimesat(261) | native 0; golden hermit -ENOSYS (-38) (#152) |
| tee | tee(276) | native 2; golden hermit -ENOSYS (-38) (#152) |
| vmsplice | vmsplice(278) | native 2; golden hermit -ENOSYS (-38) (#152) |
| io_uring_setup | io_uring_setup(425) | native ok; golden hermit -ENOSYS (-38) (#152) |
| mq_open | mq_open(240) | native -EACCES(-13) vs golden -ENOSYS(-38) (#152) |
| prctl PR_GET_TSC | prctl(157) op=25 | native 1; golden hermit -ENOSYS (-38) (#152) |
| prctl PR_GET_SPECULATION_CTRL | prctl(157) op=52 | native ok; golden hermit -ENOSYS (-38) (#152) |

## Results

- native: 11/11 exit 0 with expected stdout.
- golden ptrace: 11/11 L2, native-matching stdout.
- e9patch: 11/11 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **302/302 PASS_L2** (291 → 302, net +11, 8 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
