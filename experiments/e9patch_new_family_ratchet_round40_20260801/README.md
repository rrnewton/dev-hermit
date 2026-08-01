# e9patch corpus ratchet — round 40 (socket options, memfd fcntl seals)

## Question

Round 40 of the standing e9patch corpus ratchet. Can six freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — five more
`getsockopt` socket options (`SO_REUSEPORT`, `SO_PASSCRED`, `SO_TIMESTAMP`,
`SO_NO_CHECK`, `SO_PRIORITY`) and a memfd `fcntl(F_GET_SEALS)` seal-mask query
— reach L2 parity across the golden ptrace backend and the e9patch-rewritten
ptrace path?

**Answer: yes, all six.** Corpus 254 → 260, 260/260 PASS_L2, zero drops.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`), then e9patch L2-tested (`--backend e9patch`:
candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, deterministic
e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only if native,
golden, and e9 all pass AND agree; any guest whose golden output diverges from
native is DROPPED (no false parity, hermit issue #152). Both layers were run;
all six passed both.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| getsockopt_reuseport | getsockopt(55) SO_REUSEPORT=15 | boolean option unset by default | `reuseport=0` |
| getsockopt_passcred | getsockopt(55) SO_PASSCRED=16 | ancillary-cred passing off by default | `passcred=0` |
| getsockopt_timestamp | getsockopt(55) SO_TIMESTAMP=29 | rx timestamping off by default | `timestamp=0` |
| getsockopt_nocheck | getsockopt(55) SO_NO_CHECK=11 | disable-checksum off by default | `nocheck=0` |
| getsockopt_priority | getsockopt(55) SO_PRIORITY=12 | packet priority defaults to 0 (non-boolean) | `priority=0` |
| fcntl_get_seals | memfd_create(319)+fcntl(72) F_GET_SEALS=1034 | memfd born with F_SEAL_SEAL | `seals=1` |

The five `getsockopt` guests extend the covered `SO_*` option set on an AF_UNIX
`socketpair` endpoint (four booleans reading 0 plus `SO_PRIORITY` as a
non-boolean integer reading 0); `fcntl_get_seals` adds a new fcntl OP
(`F_GET_SEALS`) and a new memfd contract (the seal mask, distinct from the
existing memfd seek/positioning guests): a memfd created without
`MFD_ALLOW_SEALING` is born with `F_SEAL_SEAL` (0x1) set, so the query returns 1.

## Results

- native: 6/6 exit 0 with expected stdout.
- golden ptrace: 6/6 L2, native-matching stdout.
- e9patch: 6/6 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **260/260 PASS_L2** (254 → 260, net +6, 0 drops).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
