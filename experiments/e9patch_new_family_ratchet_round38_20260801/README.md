# e9patch corpus ratchet — round 38 (more socket options, interval timers)

## Question

Round 38 of the standing e9patch corpus ratchet. Can six freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — four more
`getsockopt` options (`SO_KEEPALIVE`, `SO_OOBINLINE`, `SO_DONTROUTE`,
`SO_RCVLOWAT`) and the two remaining `getitimer` interval timers
(`ITIMER_VIRTUAL`, `ITIMER_PROF`) — reach L2 parity across the golden ptrace
backend and the e9patch-rewritten ptrace path?

**Answer: yes, all six.** Corpus 242 → 248, 248/248 PASS_L2, zero drops.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`), then e9patch L2-tested (`--backend e9patch`:
candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, deterministic
e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only if native,
golden, and e9 all pass AND agree; any guest whose golden output diverges from
native is DROPPED (no false parity, hermit issue #152). Both the scorecard
collector (e9-vs-golden) and the full corpus harness (golden-vs-native) were run;
all six passed both layers.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| getsockopt_keepalive | getsockopt(55) SO_KEEPALIVE=9 | boolean option unset on fresh endpoint | `keepalive=0` |
| getsockopt_oobinline | getsockopt(55) SO_OOBINLINE=10 | OOB inlining disabled by default | `oobinline=0` |
| getsockopt_dontroute | getsockopt(55) SO_DONTROUTE=5 | routing-bypass disabled by default | `dontroute=0` |
| getsockopt_rcvlowat | getsockopt(55) SO_RCVLOWAT=18 | receive low-water mark default | `rcvlowat=1` |
| getitimer_virtual | getitimer(36) ITIMER_VIRTUAL=1 | unarmed timer reads all-zero (field sum) | `getitimervirt=0` |
| getitimer_prof | getitimer(36) ITIMER_PROF=2 | unarmed timer reads all-zero (field sum) | `getitimerprof=0` |

The four socket-option guests extend the covered `SO_*` set (previously
acceptconn/broadcast/domain/protocol/reuseaddr/socktype/soerror);
`getsockopt_rcvlowat` is the first non-boolean option in the round (default low-
water mark 1). The two `getitimer` guests extend the single existing
`getitimer_real` to the remaining two interval timers; each sums the four
`itimerval` fields to prove the unarmed timer reads zero, a stronger and still
host-independent assertion than the bare return value.

## Results

- native: 6/6 exit 0 with expected stdout.
- golden ptrace: 6/6 L2, native-matching stdout.
- e9patch: 6/6 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **248/248 PASS_L2** (242 → 248, net +6, 0 drops).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
