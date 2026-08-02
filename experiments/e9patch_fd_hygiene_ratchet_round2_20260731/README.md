# e9patch round-2 fd/output-hygiene parity ratchet (non-gated)

**Date:** 2026-07-31
**Task:** `e9patch-ratchet-round2-nongated`
**Backend:** e9patch preprocessing + ptrace backend
**Host:** devbig014.atn7.facebook.com, kernel 6.18.39, GCC 11.5.0, e9tool 1.0.1
**Hermit PR:** https://github.com/rrnewton/hermit/pull/1220
(branch `codex/e9patch-corpus-fd-hygiene-round2` @ `50b62fb4`, base `origin/main`
`c4b7b1a6`; runner = hermit built `--features e9patch` at merged #1216 `61d52337`)

## Question

The round-2 task premised a "next fd/output-hygiene STDOUT_DIVERGE batch
(non-time, non-gated)" between e9patch preprocessing and golden plain-ptrace,
to be fixed while strictly avoiding the owner-gated guest-clock/vtime,
core-scheduling, and SIGCHLD families. Does such a divergence batch exist, and
can the fd/output-hygiene family be ratcheted?

## Method

e9patch is binary-rewriting **preprocessing for the ptrace backend**, not a
standalone Detcore backend: e9tool rewrites the guest ELF ahead of time to
pre-trap its `SYSCALL` sites, then Detcore runs the rewritten image under
ptrace. e9tool rewrites only the main executable, so only **freestanding,
statically linked, raw-`syscall`** guests expose in-ELF `SYSCALL` sites
(`candidate_sites > 0`) and actually exercise the rewrite path (dynamic libc
guests are a no-op, `candidate_sites=0`).

Round-1 scratch sweep (`scratch/e9patch_fd_probe_20260731/`) probed **20**
freestanding fd/output-hygiene guests: fd allocation numbers
(open/dup/dup2/dup3/pipe2), lowest-free-fd reuse, `fcntl` `F_GETFD` flags,
`/proc/self/fd` count, `/proc/self/exe` + `/proc/self/fd/1` readlink,
`/proc/self/comm`, `/proc/self/cmdline`, `prctl` `PR_GET_NAME`, `lseek`
(`ESPIPE`), `fstat` (`S_IFIFO`), `writev`, and error paths (`EBADF`, `EFAULT`,
zero-length). Golden plain-`--strict` stdout was compared against e9patch
plain-`--strict` stdout for each.

The eight most decisive, deterministic guests were then promoted into the
landed corpus harness `hermit/tests/backend-parity/e9patch_corpus.py` and run
through its full contract (`--require-backend`): exit-status parity, stdout
parity, golden L2, e9patch L2, `mapped_sites == candidate_sites > 0`,
`b0_sites == 0`, and guest-syscall DETLOG tail-match.

## Result

- Round-1 sweep: **20/20** golden==e9patch stdout MATCH. No divergence found.
- Corpus ratchet: **20/20 PASS_L2** after expanding the corpus 12 -> 20
  (`RATCHET e9patch: 20/20 PASS_L2`). The eight new guests each report
  `candidate=1 mapped=1 b0=0 prologue=8 tail_match=yes`. See `results.csv`.
- `ci/test_harness.sh validate` = PASS (audit_inventory 398 files;
  guest-fixture 45 -> 53; 196 manifest-tests and CI DAG correspondence
  unchanged, so no `expected-e2e-plan.json` / `portable.json` edits).

## Interpretation

**The premised fd/output-hygiene STDOUT_DIVERGE batch does not exist.** e9patch
preprocessing perturbs no fd/output-hygiene guest-visible behavior for two
structural reasons:

1. the e9loader closes its own self-fd after mapping trampolines, so the guest
   fd table is unshifted (first guest `open` still gets fd 3, closed fds are
   reused, `pipe2`/`dup3` numbering matches golden); and
2. `/proc/self/exe` (and `/proc/self/{comm,cmdline}`) still resolve to the
   **original** guest binary, not the rewritten e9patch temp image — no
   temp-binary leak.

There is therefore no divergence to fix, and none was fabricated (no false
parity claim). The honest, non-gated deliverable is to **institutionalize** this
parity as a regression ratchet: corpus 12 -> 20 in hermit PR #1220. This keeps
the e9patch lane moving without touching any owner-gated determinism family.

## Reproduction

```
# hermit built --features e9patch; e9tool/e9patch on the vendored reverie path
cd <hermit-checkout>
export HERMIT_E9TOOL=<reverie>/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=<reverie>/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --require-backend
ci/test_harness.sh validate
```

`src/` holds the eight promoted round-2 guest sources (identical to
`hermit/tests/backend-parity/e9patch_corpus/*.c` on the PR branch). The full
round-1 20-guest sweep (harness + probes) is under
`scratch/e9patch_fd_probe_20260731/`.

## Related

- Round-1 corpus artifact: `experiments/e9patch_ptrace_corpus_parity_20260731/`
- Landed corpus harness: hermit PR #1216 (merge commit `a08ce33b`)
- Memory: `e9patch-lane-state-and-ci-constraint`
