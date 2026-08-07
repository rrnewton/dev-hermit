# e9patch round-5 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-5)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1232
(branch `codex/e9patch-corpus-round5-families` @ `38b30ec1`, **stacked on**
#1231 → #1226 → #1220; runner = hermit built `--features e9patch` at merged
#1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond round-4
into **further non-gated syscall families** — filesystem errno paths, prctl
thread-name round-trips, cwd queries, pipe data I/O, statx mode bits, scatter
reads, umask round-trips, and fstat size reporting — while strictly avoiding the
owner-gated guest-clock/vtime, core-scheduling, SIGCHLD, new-syscall-support,
and Reverie-API families?

## Method

e9tool rewrites only the main executable, so only **freestanding, statically
linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`). Eight new such guests were added, one `syscall`-site
helper each (`candidate_sites=1`): `open_enoent` (open missing → `ENOENT`),
`prctl_name` (`PR_SET_NAME`/`PR_GET_NAME`), `getcwd_check` (host-specific path,
`expected_stdout=None`), `pipe_rw` (two-byte pipe round-trip), `statx_devnull`
(`statx` `stx_mode` `S_IFCHR`), `readv_zero` (scatter `readv` of `/dev/zero`),
`umask_set` (umask round-trip = 18), `fstat_size_memfd` (memfd `st_size` = 5).

Each guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **44/44 PASS_L2** after expanding the corpus 36 → 44
  (`RATCHET e9patch: 44/44 PASS_L2`). Each new guest reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (422 files; guest-fixture
  69 → 77; 196 manifest-tests and CI DAG correspondence unchanged).

## Interpretation

e9patch preprocessing leaves `open`(error)/`prctl`/`getcwd`/pipe `read`+`write`/
`statx`/`readv`/`umask`/`fstat`(size) byte-identical to golden ptrace. The
rewrite only redirects each `SYSCALL` instruction to the same in-process trap;
it changes no syscall arguments or results. Byte-identical detlog to plain
ptrace remains impossible by construction (the deterministic e9loader prologue
of 8 syscalls prefixes the guest sequence); the achievable and here-saturated
bar is guest-visible + L2 + detlog-modulo-prologue. No divergence was found and
none was fabricated. Routine backend-parity work; **not** a
`post-facto-human-review` trigger.

## Reproduction

```
cd <hermit-checkout>   # branch codex/e9patch-corpus-round5-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-5 guest sources.

## Related

- Rounds 1–4: `experiments/e9patch_*_ratchet_*_20260731/` and
  `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Stack: #1220 → #1226 → #1231 → #1232
- Memory: `e9patch-lane-state-and-ci-constraint`
