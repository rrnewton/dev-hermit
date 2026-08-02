# e9patch corpus ratchet — round 47 (more protocol-level getsockopt + multicast)

## Question

Round 47 of the standing e9patch corpus ratchet. Can ten more freestanding
raw-syscall x86-64 guests reading socket options at the **IPPROTO_TCP(6)**,
**IPPROTO_IP(0)**, and **IPPROTO_IPV6(41)** levels — extending the round-46
option-level dimension with new members plus the IPv4/IPv6 **multicast**
sub-families on datagram sockets — reach L2 parity across the golden ptrace
backend and the e9patch-rewritten ptrace path?

**Answer: yes, all ten.** Corpus 311 → 321, 321/321 PASS_L2. Two additional
candidates were probed and dropped (they mirror host sysctls and diverge).

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

## Kept (10)

| guest | option (level, socktype) | queried default | stdout |
|-------|--------------------------|-----------------|--------|
| getsockopt_tcp_quickack | TCP_QUICKACK (TCP, stream) | on → 1 | `tcpquickack=1` |
| getsockopt_tcp_defer_accept | TCP_DEFER_ACCEPT (TCP, stream) | off → 0 | `tcpdeferaccept=0` |
| getsockopt_tcp_window_clamp | TCP_WINDOW_CLAMP (TCP, stream) | unset → 0 | `tcpwinclamp=0` |
| getsockopt_tcp_user_timeout | TCP_USER_TIMEOUT (TCP, stream) | unset → 0 | `tcpusertimeo=0` |
| getsockopt_tcp_fastopen | TCP_FASTOPEN (TCP, stream) | off → 0 | `tcpfastopen=0` |
| getsockopt_ip_tos | IP_TOS (IP, stream) | 0 | `iptos=0` |
| getsockopt_ip_multicast_ttl | IP_MULTICAST_TTL (IP, dgram) | link-local → 1 | `ipmcttl=1` |
| getsockopt_ip_multicast_loop | IP_MULTICAST_LOOP (IP, dgram) | on → 1 | `ipmcloop=1` |
| getsockopt_ipv6_multicast_hops | IPV6_MULTICAST_HOPS (IPv6, dgram) | link-local → 1 | `v6mchops=1` |
| getsockopt_ipv6_tclass | IPV6_TCLASS (IPv6, stream) | 0 | `v6tclass=0` |

All ten read per-socket option defaults (not host sysctls); the multicast guests
introduce the IPv4/IPv6 multicast option sub-families on datagram sockets, not
previously in the corpus.

## Dropped (2)

| candidate | option | reason |
|-----------|--------|--------|
| TCP_KEEPIDLE | getsockopt(55) op=4 (IPPROTO_TCP) | host sysctl tcp_keepalive_time: native 2400 vs golden 7200 (#152) |
| TCP_LINGER2 | getsockopt(55) op=8 (IPPROTO_TCP) | host sysctl tcp_fin_timeout: native 5 vs golden 60 (#152) |

## Results

- native: 10/10 exit 0 with expected stdout.
- golden ptrace: 10/10 L2, native-matching stdout.
- e9patch: 10/10 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **321/321 PASS_L2** (311 → 321, net +10, 2 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
