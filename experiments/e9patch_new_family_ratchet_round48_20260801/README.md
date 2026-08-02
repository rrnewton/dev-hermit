# e9patch corpus ratchet — round 48 (socket receive/ancillary-control getsockopt)

## Question

Round 48 of the standing e9patch corpus ratchet. Can twelve freestanding
raw-syscall x86-64 guests reading the socket **receive/ancillary-control** option
sub-family with `getsockopt(55)` at the IPPROTO_IP(0), IPPROTO_IPV6(41), and
IPPROTO_TCP(6) levels reach L2 parity across the golden ptrace backend and the
e9patch-rewritten ptrace path?

**Answer: yes, all twelve.** Corpus 321 → 333, 333/333 PASS_L2. All twelve probed
candidates matched golden; none dropped this round.

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

## Kept (12)

| guest | option (level, socktype) | stdout |
|-------|--------------------------|--------|
| getsockopt_ip_recvtos | IP_RECVTOS (IP, dgram) | `iprecvtos=0` |
| getsockopt_ip_recvttl | IP_RECVTTL (IP, dgram) | `iprecvttl=0` |
| getsockopt_ip_pktinfo | IP_PKTINFO (IP, dgram) | `ippktinfo=0` |
| getsockopt_ip_recvopts | IP_RECVOPTS (IP, dgram) | `iprecvopts=0` |
| getsockopt_ip_freebind | IP_FREEBIND (IP, stream) | `ipfreebind=0` |
| getsockopt_ip_nodefrag | IP_NODEFRAG (IP, dgram) | `ipnodefrag=0` |
| getsockopt_ipv6_recvpktinfo | IPV6_RECVPKTINFO (IPv6, dgram) | `v6recvpktinfo=0` |
| getsockopt_ipv6_recvhoplimit | IPV6_RECVHOPLIMIT (IPv6, dgram) | `v6recvhoplim=0` |
| getsockopt_ipv6_recvtclass | IPV6_RECVTCLASS (IPv6, dgram) | `v6recvtclass=0` |
| getsockopt_ipv6_dontfrag | IPV6_DONTFRAG (IPv6, dgram) | `v6dontfrag=0` |
| getsockopt_tcp_inq | TCP_INQ (TCP, stream) | `tcpinq=0` |
| getsockopt_tcp_save_syn | TCP_SAVE_SYN (TCP, stream) | `tcpsavesyn=0` |

All twelve are per-socket ancillary-control booleans disabled by default; unlike
the round-47 host-sysctl-tuned options they do not mirror any global sysctl, so
native and golden agree.

## Dropped (0)

All twelve probed candidates matched golden ptrace; none dropped this round.

## Results

- native: 12/12 exit 0 with expected stdout.
- golden ptrace: 12/12 L2, native-matching stdout.
- e9patch: 12/12 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **333/333 PASS_L2** (321 → 333, net +12).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
