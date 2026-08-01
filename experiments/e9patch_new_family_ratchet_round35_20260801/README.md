# e9patch corpus ratchet — round 35 (socket type/option, fcntl op, mmap flag, dup2 special case)

## Question

Round 35 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for six inert query/no-op probes on previously uncovered *socket*,
*fcntl*, *mmap*, and *dup2* boundaries — a lone `AF_UNIX`/`SOCK_STREAM` socket, two
new `getsockopt` options (`SO_ERROR`, `SO_ACCEPTCONN`), the `F_GETSIG` fcntl op,
the `MAP_NORESERVE` mmap flag, and the POSIX `dup2(fd,fd)` no-op special case —
reach L2 parity across the golden ptrace backend and the e9patch-rewritten ptrace
path?

Rounds 32–34 established that the *inert query/no-op* vein is clean (round 33:
4/4, round 34: 6/6) while the *data-movement/zero-copy* vein is dead (round 32:
5/8 dropped for golden `-ENOSYS`/`-EPERM`). Round 35 stays on the inert vein but
widens coverage of *already-supported families* along new axes: a new socket
TYPE, two new socket OPTIONS beyond round-13's `SO_TYPE`, a new fcntl OP beyond
the `F_GETFL`/`F_GETFD`/`F_GETOWN` guests, a new mmap FLAG beyond `mmap_anon`'s
plain private-anon, and the `dup2` oldfd==newfd special case distinct from
`dup2_high`'s redirection.

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values (a fixed fd number, a queried 0, or a written-back
sentinel byte). Each was native-tested, then golden-hermit-ptrace L2-tested
(`--strict --verify`, "Determinism verified"), then e9patch L2-tested (the
`--backend e9patch` preprocessing arm: candidate_sites>0, mapped==candidate, no
SIGILL fallback `b0==0`, DETLOG tail-match with the deterministic e9loader
prologue removed). A candidate is KEPT only if native, golden, and e9 all pass
and agree; any guest failing native OR golden is DROPPED (no false parity, hermit
issue #152).

**Environment note.** The fleet PMU was heavily contended during vetting
(loadavg ~67–140, dozens of concurrent `--verify`). These probes have minimal
retired-conditional-branch counts and hit L2 for both golden and e9; the verify
legs were run through a `killpg`-on-wedge retry harness (the scorecard collector
`compat-envelope/collect-e9patch-compat.rs`, which retries strict and verify
legs on PMU wedge/skid). Native and `--strict` (non-verify) runs are unaffected
by PMU load.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| socket_stream | socket(41) | lone AF_UNIX SOCK_STREAM socket fd number | `socketstream=3` |
| getsockopt_soerror | getsockopt(55) SO_ERROR | no pending error on fresh endpoint → 0 | `soerror=0` |
| getsockopt_acceptconn | getsockopt(55) SO_ACCEPTCONN | non-listening endpoint → 0 | `acceptconn=0` |
| fcntl_getsig | fcntl(72) F_GETSIG | no signal set → 0 (SIGIO) | `getsig=0` |
| mmap_noreserve | mmap(9) MAP_NORESERVE | writable anon mapping, sentinel round-trip | `noreserve=42` |
| dup2_same_fd | dup2(33) oldfd==newfd | POSIX no-op returns newfd, no close | `dup2same=3` |

`socket_stream` creates a lone `AF_UNIX`/`SOCK_STREAM` socket — a distinct socket
TYPE from `socket_dgram`'s `SOCK_DGRAM` — and prints the lowest free fd (3).
`getsockopt_soerror` and `getsockopt_acceptconn` read two more socket OPTIONS
beyond round-13's `SO_TYPE`: `SO_ERROR` (a just-created socketpair endpoint has no
pending error → 0) and `SO_ACCEPTCONN` (a connected, non-listening endpoint is
not accepting → 0). `fcntl_getsig` reads `F_GETSIG` on a fresh pipe read-end
(no signal set → 0, meaning SIGIO), a distinct fcntl OP from the covered
`F_GETFL`/`F_GETFD`/`F_GETOWN`. `mmap_noreserve` exercises the `MAP_NORESERVE`
flag path (distinct from `mmap_anon`'s plain `MAP_PRIVATE|MAP_ANONYMOUS`), writing
a sentinel byte (42) and reading it back to confirm the mapping is writable, then
`munmap`. `dup2_same_fd` covers the POSIX `oldfd==newfd` special case: `dup2` on
an already-open fd is a no-op that returns `newfd` (3) without closing it,
distinct from `dup2_high`'s redirection to a fresh number.

## Dropped (0)

The inert-query vein remains clean: all six candidates were kept, extending the
round-33/34 result and reconfirming the round-32 lesson — prefer inert probe/
query syscalls, non-blocking error boundaries, and new axes of already-supported
families over zero-copy / data-movement syscalls, which golden hermit does not
support.

## Results

- native: 6/6 exit 0 with expected stdout.
- golden ptrace: 6/6 L2 "Determinism verified"; native==golden and expected
  stdout matched.
- e9patch: 6/6 PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=8, tail_match=yes
  (all guests share one `syscall` instruction in the `sc()` helper, so
  candidate_sites=1). Scorecard collector: every ptrace arm `det=1`, every
  e9patch arm `det=1 par=1` — the rewritten output is byte-identical to golden.
- full corpus: clean re-run **233/233 PASS_L2** (0 non-passes). A first, more
  heavily contended run reported 228/233 with all six new guests passing; the
  five non-passes were pre-existing guests transiently wedged on the verify leg
  under PMU load and all passed on the immediate clean re-run — env artifacts,
  not regressions (this round is a purely additive change).
- corpus size: 227 → 233.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

Per-guest L2 preprocessing-invariance can also be confirmed with the scorecard
collector:

```
cd ~/work/dev-hermit/compat-envelope
./collect-e9patch-compat.rs --only socket_stream --csv /tmp/r35.csv --run-id r35
```

See `metadata.json` for exact SHAs and environment.
