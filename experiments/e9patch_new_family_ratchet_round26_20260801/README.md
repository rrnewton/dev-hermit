# e9patch new-family ratchet, round-26 (2026-08-01)

## Question

Do two thread-directed signalling syscall families with no existing corpus
guest -- `tkill(200)` and `rt_tgsigqueueinfo(297)`, each sending signal 0 --
hold byte-identical parity under e9patch **preprocessing** with the golden
hermit **ptrace backend** (e9patch is binary-rewriting preprocessing, not a
backend)?

## Method

Freestanding, statically linked, raw-`syscall` x86-64 guests (one in-ELF
`SYSCALL` site via a shared `sc()` helper; every guest ends in
`exit_group(231)`). Each guest is native-tested, then run through
`tests/backend-parity/e9patch_corpus.py`, which builds hermit `--features
e9patch`, preprocesses the guest with e9tool, and compares guest-visible
output and the detlog tail (modulo the 8-syscall e9loader prologue) against the
golden ptrace run at `--strict --verify` (L2). A guest that fails under golden
ptrace is dropped per no-false-parity (#152).

Both kept guests send **signal 0**, which Linux treats as a target-exists /
permission probe: no signal is queued or delivered, no handler runs, and the
deterministic thread schedule, virtual time, and randomness are all unperturbed.
Each guest prints only the syscall return (0 on success), which is
host-independent.

## Results

185/185 PASS_L2 (183 prior + 2 new). Kept 2 of 5 candidates:

- `tkill_self_sig0` (200) -> `tkill=0`, PASS_L2
- `rt_tgsigqueueinfo_self` (297) -> `tgsigqueueinfo=0`, PASS_L2

Dropped 3 per no-false-parity (#152):

- `prctl_securebits` -- prctl `PR_GET_SECUREBITS(27)` returns -ENOSYS (-38)
  under golden hermit ptrace.
- `prctl_mce_kill_get` -- prctl `PR_MCE_KILL_GET(34)` returns -ENOSYS (-38)
  under golden hermit ptrace.
- `userfaultfd_create` -- `userfaultfd(323)` fails even natively here
  (unprivileged userfaultfd disabled); an error path, not a supported-success
  guest.

## Interpretation

Both thread-directed signal-0 probes are routine backend-parity coverage: they
touch neither time, randomness, nor CPU scheduling, so they meet no
`post-facto-human-review` trigger. `tkill` is distinct from the
process-directed `kill`/`tgkill` guests of round-19; `rt_tgsigqueueinfo` is
the thread-group-targeted, siginfo-carrying counterpart to round-23's
`rt_sigqueueinfo`.

Field thinning continues (drop rate r22 0/6, r23 1/7, r24 4/7, r25 1/3,
r26 3/5): readily-reachable supported non-gated syscall families are nearly
exhausted.

## Reproduction

```
cd ~/work/dev-hermit/worktrees/e9patch/hermit
git checkout codex/e9patch-corpus-round26-families   # @ fdb5fe38c7a8022e9e00cc335da797c48c3bb151
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py
bash ci/test_harness.sh audit-inventory
```
