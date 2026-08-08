# e9patch round-4 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-4)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1231
(branch `codex/e9patch-corpus-round4-families` @ `5780f573`, **stacked on**
#1226 → #1220; runner = hermit built `--features e9patch` at merged #1216
`61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-3 into **further non-gated syscall families** — heap/brk, memory advice,
file-backed mmap, anonymous-file seek positioning, ioctl error paths, filesystem
access checks, signal-disposition queries, and process hierarchy — while
strictly avoiding the owner-gated guest-clock/vtime, core-scheduling, SIGCHLD,
new-syscall-support, and Reverie-API families?

## Method

e9tool rewrites only the main executable, so only **freestanding, statically
linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path. Eight new such
guests were added, one `syscall`-site helper each (`candidate_sites=1`):

- **heap/brk** — `brk_grow` (query break, grow by a page, assert advance).
- **memory advice** — `madvise_dontneed` (`MADV_DONTNEED` zero-fills an anon
  page).
- **file-backed mmap** — `file_mmap_zero` (`MAP_PRIVATE` of `/dev/zero` reads
  zero).
- **anonymous-file seek** — `memfd_seek` (`memfd_create`/`ftruncate`/`SEEK_END`
  = 4096; a device's `lseek` is a no-op returning 0, so a memfd is used to test
  genuine positioning rather than assert a false `off=100`).
- **ioctl error path** — `ioctl_enotty` (`TCGETS` on `/dev/null` → `ENOTTY`).
- **fs access** — `access_devnull` (`access(/dev/null, R_OK)` → 0).
- **signal disposition** — `sigaction_query` (NULL-act `rt_sigaction` read; no
  delivery or scheduling).
- **process hierarchy** — `getppid_check` (virtualized parent pid;
  host-specific, so `expected_stdout=None`, parity-only).

Each guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **36/36 PASS_L2** after expanding the corpus 28 → 36
  (`RATCHET e9patch: 36/36 PASS_L2`). Each of the eight new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (414 files; guest-fixture
  61 → 69; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves `brk`/`madvise`/file-backed `mmap`/`memfd_create`/
`ftruncate`/`lseek`/`ioctl`/`access`/`rt_sigaction`/`getppid` byte-identical to
golden ptrace. The rewrite only redirects each `SYSCALL` instruction to the same
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
cd <hermit-checkout>   # branch codex/e9patch-corpus-round4-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-4 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3: `experiments/e9patch_new_family_ratchet_round3_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 (round-2) → #1226 (round-3) → #1231 (round-4)
- Memory: `e9patch-lane-state-and-ci-constraint`
