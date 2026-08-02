# e9patch corpus ratchet — round 52 (setsockopt write-path: TCP timer/segment/MTU + multicast-loop)

## Question

Round 52 of the standing e9patch corpus ratchet. Can eleven freestanding
raw-syscall x86-64 guests that exercise the `setsockopt(54)` **write** path for
TCP timer/segment/MTU-discovery options and IPv4/IPv6 multicast-loop options —
each setting a NON-default value then reading it back with `getsockopt(55)` —
reach L2 parity across the golden ptrace backend and the e9patch-rewritten ptrace
path? In particular, can the write path **recover** the two options dropped on
the read side in round 47 (TCP_KEEPIDLE, TCP_LINGER2, whose read-only defaults
mirrored host sysctls)?

**Answer: yes, all eleven.** Corpus 364 → 375, 375/375 PASS_L2. All eleven probed
candidates matched golden; none dropped this round. TCP_KEEPIDLE and TCP_LINGER2
coverage is recovered by the write path.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) that opens a fresh
socket, **writes** a non-default option value with `setsockopt(54)`, reads it back
with `getsockopt(55)`, and prints only the round-tripped constant. Because the
kernel echoes exactly what was written, the printed value is fixed by the program
and identical native and golden — this is precisely why writing TCP_KEEPIDLE /
TCP_LINGER2 is deterministic where *reading* their host-sysctl-derived defaults
was not (both were dropped in round 47 under #152). Candidates were first
native-probed AND golden-probed (`hermit run --strict`) before authoring; each
authored guest was native-tested, golden-hermit-ptrace L2-tested (`--strict
--verify`), and e9patch L2-tested (candidate_sites>0, mapped==candidate, no SIGILL
fallback `b0==0`, deterministic e9loader `prologue=8`, DETLOG tail-match). A
candidate is KEPT only if native, golden, and e9 all pass AND agree.

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (11)

| guest | option (level, socktype) | set→read | stdout |
|-------|--------------------------|----------|--------|
| setsockopt_keepidle | TCP_KEEPIDLE (TCP, stream) | 120 | `setkeepidle=120` |
| setsockopt_keepcnt | TCP_KEEPCNT (TCP, stream) | 5 | `setkeepcnt=5` |
| setsockopt_keepintvl | TCP_KEEPINTVL (TCP, stream) | 30 | `setkeepintvl=30` |
| setsockopt_linger2 | TCP_LINGER2 (TCP, stream) | 15 | `setlinger2=15` |
| setsockopt_maxseg | TCP_MAXSEG (TCP, stream) | 1000 | `setmaxseg=1000` |
| setsockopt_syncnt | TCP_SYNCNT (TCP, stream) | 3 | `setsyncnt=3` |
| setsockopt_ip_mtu_discover | IP_MTU_DISCOVER→PMTUDISC_DO (IP, stream) | 2 | `setipmtudisc=2` |
| setsockopt_v6only | IPV6_V6ONLY (IPv6, stream) | 1 | `setv6only=1` |
| setsockopt_ip_multicast_loop | IP_MULTICAST_LOOP (IP, dgram) | 0 | `setipmcloop=0` |
| setsockopt_ipv6_multicast_loop | IPV6_MULTICAST_LOOP (IPv6, dgram) | 0 | `setv6mcloop=0` |
| setsockopt_ipv6_multicast_hops | IPV6_MULTICAST_HOPS (IPv6, dgram) | 7 | `setv6mchops=7` |

TCP_KEEPIDLE and TCP_LINGER2 were dropped in round 47 on the read side because
their getsockopt defaults mirrored the host sysctls `tcp_keepalive_time` and
`tcp_fin_timeout`; setting a fixed value pins the round-trip deterministically.

## Dropped (0)

All eleven probed candidates matched golden ptrace; none dropped this round.

## Results

- native: 11/11 exit 0 with expected stdout.
- golden ptrace: 11/11 L2, native-matching stdout.
- e9patch: 11/11 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **375/375 PASS_L2** (364 → 375, net +11).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
