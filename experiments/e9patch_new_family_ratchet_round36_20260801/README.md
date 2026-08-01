# e9patch corpus ratchet — round 36 (socket options, madvise advice, mmap flag, open flag, waitid)

## Question

Round 36 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for six inert query/no-op probes on previously uncovered axes of
already-supported *socket*, *madvise*, *mmap*, *open*, and *wait* families — two
new `getsockopt` options (`SO_DOMAIN`, `SO_PROTOCOL`), the `MADV_FREE` madvise
advice, the `MAP_STACK` mmap flag, the `O_DIRECTORY` open flag, and the `waitid`
no-children `ECHILD` boundary — reach L2 parity across the golden ptrace backend
and the e9patch-rewritten ptrace path?

Rounds 32–35 established that the *inert query/no-op* vein is clean (round 33:
4/4, round 34: 6/6, round 35: 6/6) while the *data-movement/zero-copy* vein is
dead (round 32: 5/8 dropped for golden `-ENOSYS`/`-EPERM`). Round 36 stays on the
inert vein and widens coverage of already-supported families along new axes: two
socket OPTIONS beyond round-13's `SO_TYPE` and round-35's `SO_ERROR`/
`SO_ACCEPTCONN`, a new madvise advice beyond `madvise_dontneed`'s
`MADV_DONTNEED`, a new mmap FLAG beyond `mmap_anon`/`mmap_noreserve`, a new open
FLAG beyond `open_enoent`/`openat_devnull`, and the `waitid` (247) analogue of
`wait4_nochild`'s (61) `ECHILD` boundary.

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values (a queried constant, a fixed fd, a written-back sentinel
byte, or a fixed negated errno). Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`, "Determinism verified"), then e9patch L2-tested
(the `--backend e9patch` preprocessing arm: candidate_sites>0, mapped==candidate,
no SIGILL fallback `b0==0`, DETLOG tail-match with the deterministic e9loader
prologue removed). A candidate is KEPT only if native, golden, and e9 all pass
and agree; any guest failing native OR golden is DROPPED (no false parity, hermit
issue #152).

**Environment note.** The fleet PMU was contended during vetting (loadavg
~high, dozens of concurrent `--verify`). These probes have minimal
retired-conditional-branch counts and hit L2 for both golden and e9; the verify
legs were run through a `killpg`-on-wedge retry harness (the scorecard collector
`compat-envelope/collect-e9patch-compat.rs`, which retries strict and verify legs
on PMU wedge/skid). Native and `--strict` (non-verify) runs are unaffected by PMU
load.

## Kept (6)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| getsockopt_domain | getsockopt(55) SO_DOMAIN | AF_UNIX endpoint domain | `domain=1` |
| getsockopt_protocol | getsockopt(55) SO_PROTOCOL | default AF_UNIX protocol | `protocol=0` |
| madvise_free | madvise(28) MADV_FREE | lazy-reclaim advice succeeds | `madvfree=0` |
| mmap_stack | mmap(9) MAP_STACK | sentinel byte round-trip | `mmapstack=42` |
| open_directory | open(2) O_DIRECTORY | "/" opens at lowest free fd | `opendir=3` |
| waitid_nochild | waitid(247) no children | ECHILD (negated errno) | `waitid=10` |

`getsockopt_domain` and `getsockopt_protocol` read two more socket OPTIONS on an
`AF_UNIX` socketpair endpoint: `SO_DOMAIN` returns the address family
(`AF_UNIX` = 1) and `SO_PROTOCOL` returns the socket's protocol (0, the AF_UNIX
default) — both distinct from the `SO_TYPE`/`SO_ERROR`/`SO_ACCEPTCONN` options
already covered. `madvise_free` applies `MADV_FREE` (lazy reclaim) to an anon
page, a distinct advice from `madvise_dontneed`'s `MADV_DONTNEED`; because
`MADV_FREE` reclaims lazily, the guest asserts only the host-independent syscall
return (0). `mmap_stack` adds the `MAP_STACK` flag path (a no-op hint on x86-64
but a distinct flag mask from `mmap_anon`/`mmap_noreserve`), confirming a sentinel
byte (42) round-trips through the writable mapping. `open_directory` opens `/`
with `O_DIRECTORY` (a distinct open-flag path from `open_enoent`/`openat_devnull`;
`O_DIRECTORY` fails `ENOTDIR` on a non-directory) and prints the lowest free fd
(3). `waitid_nochild` calls `waitid(P_ALL, 0, &info, WEXITED)` with no children,
the `waitid` (247) analogue of `wait4_nochild`'s wait4 (61) `ECHILD` boundary,
printing the negated errno (10 = `ECHILD`).

## Dropped (0)

The inert-query vein remains clean: all six candidates were kept, extending the
round-33/34/35 result and reconfirming the round-32 lesson — prefer inert probe/
query syscalls, non-blocking error boundaries, and new axes of already-supported
families over zero-copy / data-movement syscalls, which golden hermit does not
support.

## Results

- native: 6/6 exit 0 with expected stdout (`domain=1`, `protocol=0`,
  `madvfree=0`, `mmapstack=42`, `opendir=3`, `waitid=10`).
- golden ptrace: 6/6 L2 "Determinism verified"; native==golden and expected
  stdout matched.
- e9patch: 6/6 PASS_L2 exit=0. Scorecard collector: every ptrace arm `det=1`,
  every e9patch arm `det=1 par=1`, and the `output_hash` is byte-identical
  between the golden and e9patch arms for each guest — the rewritten output is
  bitwise-identical to golden.
- full corpus: clean run **239/239 PASS_L2** (0 non-passes) on the first pass;
  all six new guests PASS_L2 with sites c/1 m/1 b0/0, prologue=8, tail_match=yes.
  A purely additive change (6 new files + dict/manifest rows).
- corpus size: 233 → 239.
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0 (617 files, 272
  guest-fixtures).

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
./collect-e9patch-compat.rs --only getsockopt_domain --csv /tmp/r36.csv --run-id r36
```

See `metadata.json` for exact SHAs and environment.
