# e9patch corpus ratchet — round 53 (socket-identity getsockopt: SO_DOMAIN / SO_PROTOCOL / SO_TYPE)

## Question

Round 53 of the standing e9patch corpus ratchet. Can five freestanding
raw-syscall x86-64 guests reading socket-**identity** properties with
`getsockopt(55)` at `SOL_SOCKET` — the address family (SO_DOMAIN), protocol
(SO_PROTOCOL), and type (SO_TYPE) of a socket — reach L2 parity across the golden
ptrace backend and the e9patch-rewritten ptrace path?

**Answer: yes, all five.** Corpus 375 → 380, 380/380 PASS_L2. Candidates that
duplicated existing guests were dropped before authoring (see Dropped).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) that opens a fresh
socket and reads one socket-identity option with `getsockopt(55)`, printing only
the returned constant. Unlike an option *value* that a caller sets, these report a
fixed property the kernel assigned when the socket was created (its family,
protocol, or type), so the printed value is a compile-time constant of the guest —
identical native and golden. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) before authoring; each authored guest was native-tested,
golden-hermit-ptrace L2-tested (`--strict --verify`), and e9patch L2-tested
(candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, deterministic
e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only if native,
golden, and e9 all pass AND agree.

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (5)

| guest | option (socket) | stdout |
|-------|-----------------|--------|
| getsockopt_so_domain_inet | SO_DOMAIN (AF_INET stream) | `sodomain_inet=2` |
| getsockopt_so_domain_inet6 | SO_DOMAIN (AF_INET6 stream) | `sodomain_inet6=10` |
| getsockopt_so_protocol_tcp | SO_PROTOCOL (AF_INET stream, IPPROTO_TCP) | `soprotocol_tcp=6` |
| getsockopt_so_protocol_udp | SO_PROTOCOL (AF_INET dgram, IPPROTO_UDP) | `soprotocol_udp=17` |
| getsockopt_so_type_dgram | SO_TYPE (AF_INET dgram) | `sotype_dgram=2` |

SO_DOMAIN and SO_PROTOCOL are options with no prior corpus guest.
getsockopt_so_type_dgram exercises SO_TYPE on an AF_INET dgram socket returning
SOCK_DGRAM(2), a distinct socket type and return value from the existing
AF_UNIX/SOCK_STREAM `getsockopt_socktype` guest (which returns 1).

## Dropped

- **so_type_stream** — duplicate of existing `getsockopt_socktype` (SO_TYPE → 1).
- **so_acceptconn** — duplicate of existing `getsockopt_acceptconn`
  (SO_ACCEPTCONN → 0).
- **fcntl_getfd_socket / fcntl_getfl_socket** — the F_GETFD/F_GETFL reads
  duplicate the covered `fcntl_getfl` and `fcntl_setfd_cloexec` family (18 fcntl
  guests already present); dropped to avoid marginal padding, not divergence.

## Results

- native: 5/5 exit 0 with expected stdout.
- golden ptrace: 5/5 L2, native-matching stdout.
- e9patch: 5/5 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **380/380 PASS_L2** (375 → 380, net +5).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
