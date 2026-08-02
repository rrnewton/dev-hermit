# e9patch corpus ratchet — round 56 (wrong-arg errno constants, distinct classes)

## Question

Can freestanding raw-syscall guests that drive already-covered syscalls into
faithful **wrong-arg errno constants** — each on a distinct errno class not yet
represented as an error constant — reach e9patch preprocessing-parity L2?

## Method

Probe-first: for each candidate, compile a freestanding, statically linked,
raw-`syscall` x86-64 guest that prints exactly one host-independent value, then
compare **native** execution against the **golden plain-ptrace** hermit run
*before* authoring. A candidate is kept only when `native == golden` (faithful
parity, hermit#152). Kept guests are validated end-to-end through
`tests/backend-parity/e9patch_corpus.py` (native → golden L2 → e9patch L2, full
direct-AOT coverage `mapped==candidate`, no SIGILL/B0 fallback, DETLOG
tail-match against golden modulo the deterministic e9loader prologue).

## Results

**Kept (5) — corpus 390 → 395, `RATCHET e9patch: 395/395 PASS_L2`:**

| guest | syscall | condition | errno |
|---|---|---|---|
| `getcwd_erange` | `getcwd(79)` | 1-byte buffer | `-ERANGE(-34)` |
| `pread_pipe_espipe` | `pread64(17)` | on a pipe | `-ESPIPE(-29)` |
| `open_notdir` | `openat(257)` | `/dev/null` + `O_DIRECTORY` | `-ENOTDIR(-20)` |
| `memfd_einval` | `memfd_create(319)` | invalid flags | `-EINVAL(-22)` |
| `read_dir_eisdir` | `read(0)` | on a directory fd | `-EISDIR(-21)` |

Each new guest: `exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`. Zero
dropped this round — every candidate probed `native == golden`.

## Interpretation

Wrong-arg errno constants remain the richest faithful vein (opened in round 54
with the socket-syscall error paths). Each errno here comes from kernel argument
validation performed before any host-variable state is consulted, so the value
is host-independent (independent of the actual cwd string or filesystem layout —
the guest never emits the path/data, only the fixed errno) and `native ==
golden`. Choosing five *distinct* errno classes (ERANGE/ESPIPE/ENOTDIR/EINVAL/
EISDIR) on five distinct syscalls keeps the round honestly sized rather than
padding one errno across many syscalls. These are ERROR paths distinct from the
existing success-path guests (`getcwd_check`, `pread_past_eof`, `open_directory`,
`memfd_create_check`) and from `lseek_pipe` (lseek ESPIPE).

## Reproduction

```bash
cd worktrees/e9patch/hermit
git checkout codex/e9patch-corpus-round56-families   # @ f3a86e5a
cargo build -p hermit --features e9patch --bin hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
# expect: RATCHET e9patch: 395/395 PASS_L2
bash ci/test_harness.sh audit-inventory   # EXIT=0
```

## Provenance

- Hermit branch `codex/e9patch-corpus-round56-families` @ `f3a86e5a`
  (base `codex/e9patch-corpus-round55-families` @ `85f2f925`).
- PR https://github.com/rrnewton/hermit/pull/1446 (draft, stacked on #1439).
- e9tool/e9patch: `reverie/third-party/e9patch/{e9tool,e9patch}`.
