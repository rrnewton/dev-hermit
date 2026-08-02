# e9patch corpus ratchet — round 54 (socket-syscall error-path constants)

## Question

Round 54 of the standing e9patch corpus ratchet. Can five freestanding
raw-syscall x86-64 guests that drive socket syscalls into a faithful Linux
**errno** on a wrong-state socket — getpeername/shutdown on an unconnected
socket, accept on a non-listening socket, listen on a datagram socket — plus a
getsockname address-family read reach L2 parity across the golden ptrace backend
and the e9patch-rewritten ptrace path?

**Answer: yes, all five.** Corpus 380 → 385, 385/385 PASS_L2. All five probed
candidates matched golden; none dropped this round.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`). Four exercise the
**error** path — a socket in the wrong state returns a fixed negative errno that
is a host-independent constant, identical native and golden (faithful Linux error
parity, hermit issue #152 requires native==golden, which holds here); the fifth
reads a fixed address family. Candidates were first native-probed AND
golden-probed (`hermit run --strict`) before authoring; each authored guest was
native-tested, golden-hermit-ptrace L2-tested (`--strict --verify`), and e9patch
L2-tested (candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`,
deterministic e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only
if native, golden, and e9 all pass AND agree.

These are the **error/identity** paths of syscalls whose success paths are already
covered by AF_UNIX guests (getpeername_unix, getsockname_unix, shutdown_socketpair,
accept_abstract, accept4_abstract, listen_abstract) — a genuinely different code
path and return value, not a duplicate.

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (5)

| guest | operation | errno/value | stdout |
|-------|-----------|-------------|--------|
| getpeername_unconnected | getpeername on unconnected AF_INET stream | -ENOTCONN | `getpeername=-107` |
| shutdown_unconnected | shutdown SHUT_RDWR on unconnected AF_INET stream | -ENOTCONN | `shutdownunc=-107` |
| accept_nonlisten | accept on non-listening AF_INET stream | -EINVAL | `acceptnolis=-22` |
| listen_dgram | listen on AF_INET dgram | -EOPNOTSUPP | `listendgram=-95` |
| getsockname_family | getsockname family on unbound AF_INET stream | AF_INET | `gsnfamily=2` |

## Dropped (0)

All five probed candidates matched golden ptrace; none dropped this round.

## Results

- native: 5/5 exit 0 with expected stdout.
- golden ptrace: 5/5 L2, native-matching stdout.
- e9patch: 5/5 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **385/385 PASS_L2** (380 → 385, net +5).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
