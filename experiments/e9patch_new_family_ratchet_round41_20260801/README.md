# e9patch corpus ratchet — round 41 (fcntl OPs, socket options, AF_INET sockets)

## Question

Round 41 of the standing e9patch corpus ratchet. Can eight freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — two new fcntl OPs
(`F_GETLEASE`, `F_GETOWN_EX`), three more socket options (`SO_LINGER`,
`SO_SNDBUF`/`SO_RCVBUF` positivity, `SO_PEEK_OFF`), and AF_INET TCP/UDP socket
creation — reach L2 parity across the golden ptrace backend and the
e9patch-rewritten ptrace path?

**Answer: yes, all eight.** Corpus 260 → 268, 268/268 PASS_L2. Two additional
candidates were probed and dropped (golden diverges from native).

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

## Kept (8)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| fcntl_getlease_memfd | fcntl(72) F_GETLEASE=1025 | lease-free file reports F_UNLCK | `getlease=2` |
| fcntl_getownex_pipe | fcntl(72) F_GETOWN_EX=16 | unowned pipe → owner type 0 (struct copyout) | `getownex=0` |
| getsockopt_linger | getsockopt(55) SO_LINGER=13 | struct linger l_onoff off by default | `linger=0` |
| getsockopt_sndbuf | getsockopt(55) SO_SNDBUF=7 | fresh socket has positive send buffer (boolean) | `sndbuf=1` |
| getsockopt_rcvbuf | getsockopt(55) SO_RCVBUF=8 | fresh socket has positive recv buffer (boolean) | `rcvbuf=1` |
| getsockopt_peekoff | getsockopt(55) SO_PEEK_OFF=42 | peek offset disabled by default | `peekoff=-1` |
| socket_inet_stream | socket(41) AF_INET=2 SOCK_STREAM=1 | TCP socket takes lowest free fd, no bind/connect | `inetstream=3` |
| socket_inet_dgram | socket(41) AF_INET=2 SOCK_DGRAM=2 | UDP socket takes lowest free fd, no bind/connect | `inetdgram=3` |

`fcntl_getownex_pipe` exercises the struct-copyout `F_GETOWN_EX` path, distinct
from the scalar `F_GETOWN` guest; `getsockopt_sndbuf`/`rcvbuf` deliberately print
only the host-independent positivity invariant (the exact buffer size is
host-tunable); `getsockopt_peekoff` is a negative-default option (-1); the two
AF_INET guests exercise a different address family than the AF_UNIX socket
guests and never communicate.

## Dropped (2)

| candidate | syscall | reason |
|-----------|---------|--------|
| prctl PR_GET_TIMING | prctl(157) op=13 | golden hermit returns -ENOSYS (-38); native returns 0 → hermit limitation, not parity (#152) |
| prctl PR_GET_SECCOMP | prctl(157) op=21 | golden hermit returns -ENOSYS (-38); native returns 0 → hermit limitation, not parity (#152) |

## Results

- native: 8/8 exit 0 with expected stdout.
- golden ptrace: 8/8 L2, native-matching stdout.
- e9patch: 8/8 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **268/268 PASS_L2** (260 → 268, net +8, 2 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
