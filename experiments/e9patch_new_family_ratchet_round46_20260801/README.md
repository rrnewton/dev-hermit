# e9patch corpus ratchet — round 46 (protocol-level getsockopt: TCP / IP / IPv6)

## Question

Round 46 of the standing e9patch corpus ratchet. Can nine freestanding
raw-syscall x86-64 guests that read socket options at the **IPPROTO_TCP(6)**,
**IPPROTO_IP(0)**, and **IPPROTO_IPV6(41)** protocol levels — a new option-level
dimension distinct from every prior SOL_SOCKET guest — reach L2 parity across the
golden ptrace backend and the e9patch-rewritten ptrace path?

**Answer: yes, all nine.** Corpus 302 → 311, 311/311 PASS_L2. Two additional
candidates were probed and dropped (golden diverges from native).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) that opens a fresh
socket, reads one option default with `getsockopt(55)`, and prints only that
host-independent constant. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored guest
was then native-tested, golden-hermit-ptrace L2-tested (`--strict --verify`), and
e9patch L2-tested (candidate_sites>0, mapped==candidate, no SIGILL fallback
`b0==0`, deterministic e9loader `prologue=8`, DETLOG tail-match). A candidate is
KEPT only if native, golden, and e9 all pass AND agree; any guest whose golden
output diverges from native is DROPPED (no false parity, hermit issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (9)

| guest | option (level) | queried default | stdout |
|-------|----------------|-----------------|--------|
| getsockopt_tcp_nodelay | TCP_NODELAY (IPPROTO_TCP) | Nagle on → 0 | `tcpnodelay=0` |
| getsockopt_tcp_cork | TCP_CORK (IPPROTO_TCP) | off → 0 | `tcpcork=0` |
| getsockopt_tcp_keepcnt | TCP_KEEPCNT (IPPROTO_TCP) | 9 probes | `tcpkeepcnt=9` |
| getsockopt_tcp_keepintvl | TCP_KEEPINTVL (IPPROTO_TCP) | 75 s | `tcpkeepintvl=75` |
| getsockopt_tcp_syncnt | TCP_SYNCNT (IPPROTO_TCP) | 6 SYNs | `tcpsyncnt=6` |
| getsockopt_tcp_maxseg | TCP_MAXSEG (IPPROTO_TCP) | 536 (min-MTU MSS) | `tcpmaxseg=536` |
| getsockopt_ip_mtudiscover | IP_MTU_DISCOVER (IPPROTO_IP) | PMTUDISC_WANT=1 | `ipmtudisc=1` |
| getsockopt_ipv6_v6only | IPV6_V6ONLY (IPPROTO_IPV6) | dual-stack → 0 | `v6only=0` |
| getsockopt_ipv6_hops | IPV6_UNICAST_HOPS (IPPROTO_IPV6) | 64 | `v6hops=64` |

All nine read kernel-established defaults on an unconfigured socket; no option is
set. The values are protocol-level constants, a new option-level dimension vs the
prior SOL_SOCKET `getsockopt_*`/`setsockopt_*` guests.

## Dropped (2)

| candidate | option/syscall | reason |
|-----------|----------------|--------|
| IP_TTL | getsockopt(55) IP_TTL=2 (IPPROTO_IP) | host-variable: native 96 vs golden 64 (#152) |
| prctl PR_GET_ENDIAN | prctl(157) op=19 | native -EINVAL(-22) vs golden -ENOSYS(-38) (#152) |

## Results

- native: 9/9 exit 0 with expected stdout.
- golden ptrace: 9/9 L2, native-matching stdout.
- e9patch: 9/9 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **311/311 PASS_L2** (302 → 311, net +9, 2 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
