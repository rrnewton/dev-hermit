# e9patch round-7 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-7)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014.atn7.facebook.com, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1240
(branch `codex/e9patch-corpus-round7-families` @ `afaee12c`, **stacked on**
#1236 → #1232 → #1231 → #1226 → #1220; runner = hermit built `--features
e9patch` at merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-6 into **further non-gated syscall families** — `dup2` fd placement,
`chdir`/`fchdir` + `getcwd`, `fsync`, `AF_UNIX` `socketpair` data transfer,
`flock`, the nonblocking empty-pipe errno path, and `getpgid` — while strictly
avoiding the owner-gated guest-clock/vtime, core-scheduling, SIGCHLD,
new-syscall-support, and Reverie-API families?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path.

Eight new freestanding guests were added, one `syscall`-site helper each
(`candidate_sites=1`):

- **dup2 fd placement** — `dup2_high` (`dup2` onto the caller-chosen fd 20).
- **chdir** — `chdir_root` (`chdir("/")` then `getcwd` = `/`).
- **fchdir** — `fchdir_root` (open `/` as a dir fd, `fchdir`, `getcwd` = `/`).
- **fsync** — `fsync_memfd` (`fsync` of a sized memfd returns 0).
- **socketpair** — `socketpair_rw` (`AF_UNIX`/`SOCK_STREAM` two-byte
  round-trip).
- **flock** — `flock_memfd` (`LOCK_EX` then `LOCK_UN`, both 0).
- **nonblocking pipe errno** — `pipe_nonblock_eagain` (`pipe2(O_NONBLOCK)`,
  read of the empty read end → `EAGAIN` = -11).
- **getpgid** — `getpgid_check` (`getpgid(0)`; host-specific, so
  `expected_stdout=None`, parity-only).

`chdir_root`/`fchdir_root` deliberately read `getcwd` back against the
**host-independent filesystem root** `/` rather than a machine-specific working
directory, so their expected stdout stays deterministic (the `getcwd` of an
arbitrary process cwd would be host-specific).

Each guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **59/59 PASS_L2** after expanding the corpus 51 → 59
  (`RATCHET e9patch: 59/59 PASS_L2`). Each of the eight new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (437 files; guest-fixture
  84 → 92; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves `dup2`/`chdir`/`fchdir`/`getcwd`/`fsync`/
`socketpair`/`flock`/nonblocking `read`/`getpgid` byte-identical to golden
ptrace. The rewrite only redirects each `SYSCALL` instruction to the same
in-process trap; it changes no syscall arguments or results, and Detcore
sanitizes the result identically whether or not the ELF was pre-rewritten.
Byte-identical detlog to plain ptrace remains impossible by construction (the
deterministic e9loader prologue of 8 syscalls prefixes the guest sequence); the
achievable and here-saturated bar is guest-visible + L2 + detlog-modulo-prologue.

No divergence was found and none was fabricated (no false parity claim). This is
routine backend-parity test work toward the golden ptrace reference and is
**not** a `post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round7-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-7 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3: `experiments/e9patch_new_family_ratchet_round3_20260731/`
- Round-4: `experiments/e9patch_new_family_ratchet_round4_20260731/`
- Round-5: `experiments/e9patch_new_family_ratchet_round5_20260731/`
- Round-6: `experiments/e9patch_new_family_ratchet_round6_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 → #1226 → #1231 → #1232 → #1236 → #1240
- Memory: `e9patch-lane-state-and-ci-constraint`
