# e9patch corpus ratchet — round 51 (setsockopt write-path round-trips + mlockall on-fault)

## Question

Round 51 of the standing e9patch corpus ratchet. Can nine freestanding
raw-syscall x86-64 guests that exercise the `setsockopt(54)` **write** path —
each setting a NON-default socket option value then reading it back with
`getsockopt(55)` — plus an `mlockall(151)` on-fault variant reach L2 parity
across the golden ptrace backend and the e9patch-rewritten ptrace path?

**Answer: yes, all nine.** Corpus 355 → 364, 364/364 PASS_L2. Nine probed
candidates matched golden; several setsockopt round-trip candidates were dropped
as duplicates of existing set+read-back guests, and a few advice/prctl candidates
were dropped on divergence (see Dropped).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`). Unlike the read-only
`getsockopt` guests of prior rounds, each `setsockopt_*` guest opens a fresh
socket, **writes** a non-default option value with `setsockopt(54)`, reads it
back with `getsockopt(55)`, and prints only the round-tripped constant — the
kernel echoes exactly what was written, a host-independent value identical native
and golden. This is a distinct write code path from the read-only getsockopt
family. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored guest
was then native-tested, golden-hermit-ptrace L2-tested (`--strict --verify`), and
e9patch L2-tested (candidate_sites>0, mapped==candidate, no SIGILL fallback
`b0==0`, deterministic e9loader `prologue=8`, DETLOG tail-match). A candidate is
KEPT only if native, golden, and e9 all pass AND agree; any guest whose golden
output diverges from native is DROPPED (no false parity, hermit issue #152).

Options chosen for the write path were specifically ones whose read-only default
was host-variable or default-only (IP_TTL dropped in round 46, IP_TOS,
IP_MULTICAST_TTL, TCP_CORK, IPV6_UNICAST_HOPS): writing a fixed value pins the
result deterministically, recovering coverage the read-only side could not reach.

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (9)

| guest | option (level, socktype) | set→read | stdout |
|-------|--------------------------|----------|--------|
| setsockopt_priority | SO_PRIORITY (SOL_SOCKET, stream) | 6 | `sopriority=6` |
| setsockopt_nodelay | TCP_NODELAY (IPPROTO_TCP, stream) | 1 | `setnodelay=1` |
| setsockopt_ipttl | IP_TTL (IPPROTO_IP, stream) | 33 | `setipttl=33` |
| setsockopt_rcvlowat | SO_RCVLOWAT (SOL_SOCKET, stream) | 2 | `setrcvlowat=2` |
| setsockopt_iptos | IP_TOS (IPPROTO_IP, stream) | 8 | `setiptos=8` |
| setsockopt_ip_multicast_ttl | IP_MULTICAST_TTL (IPPROTO_IP, dgram) | 5 | `setipmcttl=5` |
| setsockopt_tcp_cork | TCP_CORK (IPPROTO_TCP, stream) | 1 | `settcpcork=1` |
| setsockopt_ipv6_hops | IPV6_UNICAST_HOPS (IPPROTO_IPV6, stream) | 5 | `setipv6hops=5` |
| mlockall_onfault | mlockall MCL_CURRENT\|MCL_ONFAULT then munlockall | — | `mlockonfault=0` |

## Dropped

- **ss_keepalive / ss_reuseport** — duplicates: existing `setsockopt_keepalive`
  and `setsockopt_reuseport` guests already set-then-read-back SO_KEEPALIVE and
  SO_REUSEPORT.
- **ss_broadcast** — the SO_BROADCAST option is already covered by the existing
  `setsockopt_broadcast` guest.
- **madv_popread / madv_popwrite** — MADV_POPULATE_READ/WRITE on a fresh anon
  page returned native 0 but golden −EINVAL(−22): golden diverges from native
  (#152).
- **prctl_gettimerslack** — PR_GET_TIMERSLACK returns the host default timer
  slack, which is host-configuration dependent (not a fixed constant).

## Results

- native: 9/9 exit 0 with expected stdout.
- golden ptrace: 9/9 L2, native-matching stdout.
- e9patch: 9/9 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **364/364 PASS_L2** (355 → 364, net +9).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
