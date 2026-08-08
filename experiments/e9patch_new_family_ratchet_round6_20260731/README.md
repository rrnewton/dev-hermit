# e9patch round-6 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-6)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1236
(branch `codex/e9patch-corpus-round6-families` @ `681ec92c`, **stacked on**
#1232 → #1231 → #1226 → #1220; runner = hermit built `--features e9patch` at
merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-5 into **further non-gated syscall families** — `*at`-suffixed
stat/access, `sendfile` zero-copy transfer, positioned `pwrite64`/`pread64`,
eventfd counters, `rt_sigpending`, and `fchmod` — while strictly avoiding the
owner-gated guest-clock/vtime, core-scheduling, SIGCHLD, new-syscall-support,
and Reverie-API families?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path.

Seven new freestanding guests were added, one `syscall`-site helper each
(`candidate_sites=1`):

- **`*at` stat** — `newfstatat_devnull` (`newfstatat(AT_FDCWD, "/dev/null")`
  reports `S_IFCHR`; only the deterministic `st_mode` field is inspected, never
  timestamps).
- **`*at` access** — `faccessat_devnull` (`faccessat(AT_FDCWD, "/dev/null",
  R_OK)` → 0).
- **sendfile** — `sendfile_memfd` (zero-copy five bytes between two memfds).
- **positioned I/O** — `pwrite_pread_memfd` (`pwrite64` at offset 10, then
  `pread64` reads the same three bytes back without moving the file pointer).
- **eventfd** — `eventfd_rw` (`eventfd2` counter written then read back).
- **signal pending** — `rt_sigpending_empty` (`rt_sigpending` reports an empty
  set; no delivery or scheduling).
- **fchmod** — `fchmod_memfd` (memfd mode set to `0644` and confirmed).

All seven pin exact deterministic stdout. Each guest was run through
`hermit/tests/backend-parity/e9patch_corpus.py --require-backend` and its full
contract: exit-status parity, stdout parity, golden L2, e9patch L2,
`mapped_sites == candidate_sites > 0`, `b0_sites == 0`, and guest-syscall
DETLOG tail-match.

## Dropped guest (no false parity)

`copy_file_range_memfd` was written and then **intentionally dropped**: hermit
returns `-ENOSYS` for `copy_file_range` (the golden ptrace run itself yields
`copied=-38`). A guest around it exercises no working feature and would only
encode a hermit limitation rather than a genuine e9patch-vs-ptrace parity
claim, so per the no-false-parity rule the `.c` file and CORPUS entry were
removed. Round-6 therefore landed 7 guests, not 8.

## Result

- Corpus ratchet: **51/51 PASS_L2** after expanding the corpus 44 → 51
  (`RATCHET e9patch: 51/51 PASS_L2`). Each of the seven new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (429 files; guest-fixture
  77 → 84; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves `newfstatat`/`faccessat`/`sendfile`/`pwrite64`/
`pread64`/`eventfd2`/`rt_sigpending`/`fchmod` byte-identical to golden ptrace.
The rewrite only redirects each `SYSCALL` instruction to the same in-process
trap; it changes no syscall arguments or results, and Detcore sanitizes the
result identically whether or not the ELF was pre-rewritten. Byte-identical
detlog to plain ptrace remains impossible by construction (the deterministic
e9loader prologue of 8 syscalls prefixes the guest sequence); the achievable
and here-saturated bar is guest-visible + L2 + detlog-modulo-prologue.

No divergence was found and none was fabricated (no false parity claim); the one
guest that could not honestly claim parity was dropped. This is routine
backend-parity test work toward the golden ptrace reference and is **not** a
`post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round6-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the seven round-6 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3: `experiments/e9patch_new_family_ratchet_round3_20260731/`
- Round-4: `experiments/e9patch_new_family_ratchet_round4_20260731/`
- Round-5: `experiments/e9patch_new_family_ratchet_round5_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 → #1226 → #1231 → #1232 → #1236
- Memory: `e9patch-lane-state-and-ci-constraint`
