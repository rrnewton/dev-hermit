# e9patch round-10 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-10)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1294
(branch `codex/e9patch-corpus-round10-families` @ `3bc8957a`, **stacked on**
#1288 → #1267 → #1240 → #1236 → #1232 → #1231 → #1226 → #1220; runner = hermit
built `--features e9patch` at merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-9 into **further non-gated syscall families** — `sendto`/`recvfrom` over a
socketpair, `getsockname`/`getpeername` address copyout, `fallocate` memfd
size-extension, `fdatasync`, `mincore` residency, `fadvise64`, and `sysinfo` —
while strictly avoiding the owner-gated guest-clock/vtime, core-scheduling,
SIGCHLD, new-syscall-support, and Reverie-API families?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path.

Eight new freestanding guests were added, one `syscall`-site helper each
(`candidate_sites=1`):

- **sendto / recvfrom** — `sendto_socketpair` (`sendto` then `recvfrom` with a
  NULL source over an `AF_UNIX`/`SOCK_STREAM` socketpair = `sf=hi`).
- **getsockname** — `getsockname_unix` (local address family of a socketpair
  endpoint = the `AF_UNIX` constant `sockname=1`).
- **getpeername** — `getpeername_unix` (peer address family = `peername=1`).
- **fallocate** — `fallocate_memfd` (`fallocate(mode=0,0,8)` extends a memfd;
  size read back via `fstat` = `falloc=8`).
- **fdatasync** — `fdatasync_memfd` (data flush; syscall return = `fdatasync=0`).
- **mincore** — `mincore_resident` (residency query of a faulted-in anon page;
  syscall return = `mincore=0`).
- **fadvise64** — `fadvise_memfd` (`POSIX_FADV_NORMAL` hint on a sized memfd;
  syscall return = `fadvise=0`).
- **sysinfo** — `sysinfo_ok` (fills `struct sysinfo`; syscall return =
  `sysinfo=0`, host-specific fields not printed).

Each guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **83/83 PASS_L2** after expanding the corpus 75 → 83
  (`RATCHET e9patch: 83/83 PASS_L2`). Each of the eight new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (461 files; guest-fixture
  108 → 116; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).
- No guest was dropped — all eight passed on the first harness run.

## Interpretation

e9patch preprocessing leaves
`sendto`/`recvfrom`/`getsockname`/`getpeername`/`fallocate`/`fdatasync`/
`mincore`/`fadvise64`/`sysinfo` byte-identical to golden ptrace. The rewrite
only redirects each `SYSCALL` instruction to the same in-process trap; it
changes no syscall arguments or results, and Detcore sanitizes the result
identically whether or not the ELF was pre-rewritten. Every printed value is
host-independent by construction — the `AF_UNIX` family constant for the two
socket-name guests, and the syscall return (0 on success) for the flush/query
guests — so the exact-stdout assertions are portable. Byte-identical detlog to
plain ptrace remains impossible by construction (the deterministic e9loader
prologue of 8 syscalls prefixes the guest sequence); the achievable and
here-saturated bar is guest-visible + L2 + detlog-modulo-prologue.

No divergence was found and none was fabricated (no false parity claim). This is
routine backend-parity test work toward the golden ptrace reference and is
**not** a `post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round10-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-10 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3..9: `experiments/e9patch_new_family_ratchet_round{3,4,5,6,7,8,9}_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 → #1226 → #1231 → #1232 → #1236 → #1240 → #1267 → #1288 → #1294
- Memory: `e9patch-lane-state-and-ci-constraint`
