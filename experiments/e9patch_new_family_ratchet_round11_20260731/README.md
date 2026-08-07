# e9patch round-11 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3` (rolling continuation — round-11)
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1298
(branch `codex/e9patch-corpus-round11-families` @ `43af05e5`, **stacked on**
#1294 → #1288 → #1267 → #1240 → #1236 → #1232 → #1231 → #1226 → #1220; runner =
hermit built `--features e9patch` at merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond
round-10 into **further non-gated syscall families** — `rt_sigprocmask` and
`sigaltstack` queries, `epoll_ctl` descriptor registration, memfd `fcntl`
sealing, `uname`, `prctl` `PR_GET_DUMPABLE`, `capget`, and `fstatfs` — while
strictly avoiding the owner-gated guest-clock/vtime, core-scheduling, SIGCHLD,
new-syscall-support, and Reverie-API families?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path.

Nine candidate guests were written, one `syscall`-site helper each
(`candidate_sites=1`). Eight passed and were kept:

- **rt_sigprocmask** — `rt_sigprocmask_query` (`SIG_BLOCK` a signal in the mask;
  no delivery; syscall return = `sigprocmask=0`).
- **sigaltstack** — `sigaltstack_query` (query with a NULL new stack; flags
  report `SS_DISABLE` = `altstack=2`).
- **epoll** — `epoll_ctl_add` (`epoll_create1` then `epoll_ctl(EPOLL_CTL_ADD)`
  for a pipe fd; no `epoll_wait`; syscall return = `epoll=0`).
- **memfd sealing** — `memfd_seal` (`fcntl F_ADD_SEALS F_SEAL_SEAL` then
  `F_GET_SEALS` = `seals=1`).
- **uname** — `uname_sysname` (sysname field, hermit-determinized to
  `uname=Linux`).
- **prctl** — `prctl_dumpable` (`PR_GET_DUMPABLE` = `dumpable=1`; a distinct
  prctl op from the round-4/5 name guests).
- **capget** — `capget_ok` (v3 header; syscall return = `capget=0`).
- **fstatfs** — `fstatfs_memfd` (on a memfd; syscall return = `fstatfs=0`).

The ninth candidate, **`splice`** (`splice_pipe_memfd`), was **dropped**: hermit
returns `-ENOSYS`, so golden ptrace itself prints `splice=-38`. Including it
would encode a hermit limitation rather than a parity claim (no false-parity),
exactly as round-6 handled `copy_file_range`.

Each kept guest was run through `hermit/tests/backend-parity/e9patch_corpus.py
--require-backend` and its full contract: exit-status parity, stdout parity,
golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`, `b0_sites == 0`,
and guest-syscall DETLOG tail-match.

## Result

- Corpus ratchet: **91/91 PASS_L2** after expanding the corpus 83 → 91
  (`RATCHET e9patch: 91/91 PASS_L2`; the interim run with `splice` was
  `91/92`, and `splice` was removed). Each of the eight kept guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (469 files; guest-fixture
  116 → 124; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves
`rt_sigprocmask`/`sigaltstack`/`epoll_create1`/`epoll_ctl`/`fcntl`(sealing)/
`uname`/`prctl`/`capget`/`fstatfs` byte-identical to golden ptrace. The rewrite
only redirects each `SYSCALL` instruction to the same in-process trap; it
changes no syscall arguments or results, and Detcore sanitizes the result
identically whether or not the ELF was pre-rewritten. The signal guests
manipulate/query the mask and alternate stack only — no signal is delivered and
no scheduler timed-waiter is involved — and `epoll_ctl_add` registers a
descriptor without ever calling `epoll_wait`, so none of them touch a gated
scheduling path. Every printed value is host-independent by construction — the
`uname` sysname `Linux`, the `SS_DISABLE`/`F_SEAL_SEAL`/`PR_GET_DUMPABLE`
constants, or the syscall return (0 on success). Byte-identical detlog to plain
ptrace remains impossible by construction (the deterministic e9loader prologue
of 8 syscalls prefixes the guest sequence); the achievable and here-saturated
bar is guest-visible + L2 + detlog-modulo-prologue.

No divergence was found among the kept guests and none was fabricated; the one
`-ENOSYS` family was dropped rather than blessed (no false parity claim). This
is routine backend-parity test work toward the golden ptrace reference and is
**not** a `post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round11-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight kept round-11 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch). The dropped
`splice_pipe_memfd` guest is not included.

## Related

- Round-1: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2: `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Round-3..10: `experiments/e9patch_new_family_ratchet_round{3,4,5,6,7,8,9,10}_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Stack: #1220 → #1226 → #1231 → #1232 → #1236 → #1240 → #1267 → #1288 → #1294 → #1298
- Memory: `e9patch-lane-state-and-ci-constraint`
