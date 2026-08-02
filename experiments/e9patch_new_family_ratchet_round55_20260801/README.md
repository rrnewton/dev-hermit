# e9patch corpus ratchet — round 55 (removed-syscall faithful ENOSYS)

## Question

Can freestanding raw-syscall guests that drive **removed Linux syscall numbers**
reach e9patch preprocessing-parity L2 as legitimate (faithful, host-independent)
corpus cells, and which candidate veins must be dropped as false parity?

## Method

Probe-first: for each candidate, compile a freestanding, statically linked,
raw-`syscall` x86-64 guest that prints exactly one host-independent value, then
compare **native** execution against the **golden plain-ptrace** hermit run
*before* authoring the corpus cell. A candidate is kept only when
`native == golden` (faithful parity, hermit#152); any guest where golden
diverges from faithful native is dropped, never recorded as parity. Kept guests
are then validated end-to-end through the standalone
`tests/backend-parity/e9patch_corpus.py` driver (native → golden L2 → e9patch
L2, full direct-AOT coverage `mapped==candidate`, no SIGILL/B0 fallback, and a
guest-syscall DETLOG tail-match against golden modulo the deterministic
e9loader prologue).

e9patch is binary-rewriting **preprocessing** for the ptrace backend, not a
standalone Detcore backend (`runtime_backend()` maps `E9patch → Ptrace`); the
harness uses in-ELF `SYSCALL` sites so e9tool actually rewrites the guest.

## Results

**Kept (5) — corpus 385 → 390, `RATCHET e9patch: 390/390 PASS_L2`:**

| guest | syscall | history | value |
|---|---|---|---|
| `sysctl_enosys` | `_sysctl(156)` | removed 5.5 | `sysctl=-38` |
| `create_module_enosys` | `create_module(174)` | ENOSYS since 2.6, removed 5.18 | `createmod=-38` |
| `query_module_enosys` | `query_module(178)` | ENOSYS since 2.6, removed 5.18 | `querymod=-38` |
| `get_kernel_syms_enosys` | `get_kernel_syms(177)` | ENOSYS since 2.6, removed 5.18 | `getkernelsyms=-38` |
| `nfsservctl_enosys` | `nfsservctl(180)` | removed 3.1 | `nfsservctl=-38` |

Each new guest: `exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`.

**Dropped as false parity (probe-first rejected, none added):**

- **In-process data movement** — `splice(275)`, `tee(276)`, `vmsplice(278)`,
  `copy_file_range(326)` return golden `-ENOSYS(-38)` and
  `process_vm_readv(310)`/`process_vm_writev(311)` return golden `-1`, while
  native returns the real 16-byte transfer count. Hermit does not faithfully
  implement these under ptrace; recording golden would be false parity.
- **`prctl` GET variants** — `PR_GET_SECCOMP`, `PR_GET_NO_NEW_PRIVS`,
  `PR_GET_TSC`, `PR_GET_CHILD_SUBREAPER`, `PR_MCE_KILL_GET` return golden
  `-ENOSYS(-38)` where native returns real values; hermit's prctl allowlist
  does not cover them.

See `results.csv` for the full native-vs-golden probe table.

## Interpretation

Removed syscall numbers are a genuinely faithful vein: the kernel returns
`-ENOSYS` for an unimplemented/removed number on both native and golden ptrace,
so the value is host-independent (every supported kernel has already removed
them) and `native == golden`. Each guest exercises e9patch's rewritten-`SYSCALL`
dispatch of an unknown number, which must classify to the same `ENOSYS`. The two
dropped veins are a reminder that "returns a constant" is not sufficient —
faithfulness (`native == golden`) is the gate, and hermit's ENOSYS on
unimplemented syscalls / prctl commands is a hermit divergence, not a corpus cell.

## Reproduction

```bash
cd worktrees/e9patch/hermit
git checkout codex/e9patch-corpus-round55-families   # @ 85f2f925
cargo build -p hermit --features e9patch --bin hermit
export HERMIT_E9TOOL=$PWD/../reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=$PWD/../reverie/third-party/e9patch/e9patch
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
# expect: RATCHET e9patch: 390/390 PASS_L2
bash ci/test_harness.sh audit-inventory   # EXIT=0
```

## Provenance

- Hermit branch `codex/e9patch-corpus-round55-families` @ `85f2f925`
  (base `codex/e9patch-corpus-round54-families` @ `064bfee2`).
- PR https://github.com/rrnewton/hermit/pull/1439 (draft, stacked on #1426).
- e9tool/e9patch: `reverie/third-party/e9patch/{e9tool,e9patch}`.
