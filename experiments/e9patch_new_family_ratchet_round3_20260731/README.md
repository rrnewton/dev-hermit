# e9patch round-3 new-family parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-corpus-round-3`
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014, kernel 6.18.39, GCC 11.5.0, E9Tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1226
(branch `codex/e9patch-corpus-round3-families` @ `9545b8e5`, **stacked on**
#1220's branch `codex/e9patch-corpus-fd-hygiene-round2` @ `50b62fb4`; runner =
hermit built `--features e9patch` at merged #1216 `61d52337`)

## Question

Can the e9patch preprocessing + ptrace parity corpus be ratcheted beyond the
round-2 fd/output-hygiene batch into **new non-gated syscall families** —
content I/O, stat mode bits, memory protection, errno paths, credentials —
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
(`candidate_sites=1`), spanning five families deliberately disjoint from the
round-2 fd/output-hygiene batch:

- **content I/O** — `read_devzero` (16 zero bytes from `/dev/zero`),
  `read_devnull_eof` (`read(/dev/null)` returns 0 / EOF).
- **stat mode bits** — `fstat_devnull` (`fstat(/dev/null)` reports `S_IFCHR`;
  only the deterministic `st_mode` field is inspected, never timestamps).
- **memory protection** — `mprotect_roundtrip` (anonymous `mmap` RW ->
  `mprotect` PROT_READ -> `mprotect` RW -> write -> `munmap`).
- **errno paths** — `lseek_pipe` (`lseek` on a pipe fails `ESPIPE` = -29),
  `write_badfd` (`write` to fd 999 fails `EBADF` = -9).
- **credentials** — `getid_identity` (uid/euid/gid/egid),
  `getgroups_identity` (`getgroups(0, NULL)` count). Both emit host-specific
  absolute values, so they assert golden==e9patch parity only
  (`expected_stdout=None`).

Each guest was run through the landed corpus harness
`hermit/tests/backend-parity/e9patch_corpus.py --require-backend` and its full
contract: exit-status parity, stdout parity, golden L2, e9patch L2,
`mapped_sites == candidate_sites > 0`, `b0_sites == 0`, and guest-syscall
DETLOG tail-match.

## Result

- Corpus ratchet: **28/28 PASS_L2** after expanding the corpus 20 -> 28
  (`RATCHET e9patch: 28/28 PASS_L2`). Each of the eight new guests reports
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh audit-inventory` = exit 0 (406 files; guest-fixture
  53 -> 61; 196 manifest-tests and CI DAG correspondence unchanged, so no
  `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

e9patch preprocessing leaves `read`/`fstat`/`mmap`/`mprotect`/`lseek`/`write`
and the credential syscalls byte-identical to golden ptrace. The rewrite only
redirects each `SYSCALL` instruction to the same in-process trap; it changes no
syscall arguments or results, and Detcore sanitizes the result identically
whether or not the ELF was pre-rewritten. Byte-identical detlog to plain
ptrace remains impossible by construction (the deterministic e9loader prologue
of 8 syscalls prefixes the guest sequence); the achievable and here-saturated
bar is guest-visible + L2 + detlog-modulo-prologue.

No divergence was found and none was fabricated (no false parity claim). This
is routine backend-parity test work toward the golden ptrace reference and is
**not** a `post-facto-human-review` trigger.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>   # branch codex/e9patch-corpus-round3-families
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh audit-inventory
```

`src/` holds the eight round-3 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch).

## Related

- Round-1 corpus artifact: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Round-2 fd/output-hygiene artifact:
  `experiments/e9patch_fd_hygiene_ratchet_round2_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Round-2 PR (base of this stack): hermit PR #1220
- Memory: `e9patch-lane-state-and-ci-constraint`
