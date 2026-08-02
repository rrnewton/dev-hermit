# e9patch round-8 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-8)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014.atn7.facebook.com, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1267
(branch `codex/e9patch-corpus-round8-families` @ `628b5346`, **stacked on**
#1240 → #1236 → #1232 → #1231 → #1226 → #1220; runner = hermit built
`--features e9patch` at merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-7 into **further non-gated syscall families** — `dup` fd allocation,
`fcntl` `F_DUPFD`/`F_GETFL`, `lseek` `SEEK_CUR` composition, `pread64` past EOF,
`readlinkat`, `getresuid`, and `prlimit64` — while strictly avoiding the
owner-gated guest-clock/vtime, core-scheduling, SIGCHLD, new-syscall-support,
and Reverie-API families?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path.

Eight new freestanding guests were added, one `syscall`-site helper each
(`candidate_sites=1`):

- **dup fd allocation** — `dup_lowest` (`dup` lands on the lowest free fd).
- **fcntl F_DUPFD** — `fcntl_dupfd` (`F_DUPFD` honors a minimum fd of 20).
- **fcntl F_GETFL** — `fcntl_getfl` (access-mode bits are `O_RDONLY`).
- **lseek SEEK_CUR** — `lseek_seekcur_memfd` (`SEEK_SET` 3 then `SEEK_CUR` 2 =
  offset 5).
- **pread past EOF** — `pread_past_eof` (`pread64` past end returns 0).
- **readlinkat** — `readlinkat_exe` (`/proc/self/exe`; host-specific, so
  `expected_stdout=None`, parity-only).
- **getresuid** — `getresuid_check` (real uid; host-specific, parity-only).
- **prlimit64** — `prlimit_nofile` (`RLIMIT_NOFILE` soft limit; host-specific,
  parity-only).

Each guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **67/67 PASS_L2** after expanding the corpus 59 → 67
  (`RATCHET e9patch: 67/67 PASS_L2`). Each of the eight new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (445 files; guest-fixture
  92 → 100; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves `dup`/`fcntl`/`lseek`/`pread64`/`readlinkat`/
`getresuid`/`prlimit64` byte-identical to golden ptrace. The rewrite only
redirects each `SYSCALL` instruction to the same in-process trap; it changes no
syscall arguments or results, and Detcore sanitizes the result identically
whether or not the ELF was pre-rewritten. Byte-identical detlog to plain ptrace
remains impossible by construction (the deterministic e9loader prologue of 8
syscalls prefixes the guest sequence); the achievable and here-saturated bar is
guest-visible + L2 + detlog-modulo-prologue.

No divergence was found and none was fabricated (no false parity claim). This is
routine backend-parity test work toward the golden ptrace reference and is
**not** a `post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round8-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-8 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3..7: `experiments/e9patch_new_family_ratchet_round{3,4,5,6,7}_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 → #1226 → #1231 → #1232 → #1236 → #1240 → #1267
- Memory: `e9patch-lane-state-and-ci-constraint`
