# DBI Reverie tool compat ratchet + cross-backend microbenchmark

**Date:** 2026-07-27
**Task:** `compat-dbi-reverie-ratchet` (agent hermit-274, slot `worktrees/dbi`)
**Reverie:** `codex/reverie-dbi-gap-closure` @ `e4ce514` (HEAD = "wire coordinator-backed global state")
**Hermit:** `a6573a3c` (`codex/tests-language-runtimes`, read-only — another agent's branch)
**DynamoRIO:** submodule `929840a`, client built `PROFILE=debug`, `-stack_size 2M`

## Questions

1. Do **all** Reverie tools run correctly under the DBI (DynamoRIO) backend?
2. Does global↔local (GlobalState RPC) state communication work, including across a fork/exec process tree?
3. How does DBI tool overhead compare to the ptrace backend?
4. How far does `hermit run --backend dbi --strict` determinism compat now extend?

## Results

### 1. Reverie tools under DBI — 25/25 PASS

`PROFILE=debug bash reverie-dbi/scripts/test-example-tools.sh` → **All DBI example tools passed.**
Covers: `noop`, `strace` (+decoded `exit_group`), `counter` (histogram), `counter1`
(GlobalState RPC total), `counter2` (tail_inject admission accounting), `chunky_print`
(suppress+reemit), `chrome_trace` (per-thread timeline→JSON), `chaos` (read-trunc + EINTR
inject), plus DBI Guest-surface checks: deferred-syscall rewrite, `set_regs`, `ppid`/
`is_root_process`, in-process `backtrace`, virtual identity/private-fd policy, forked-child
clock+rlimit virtualization, and concurrent pthread lifecycle for noop/strace/counter1/counter2.

### 2. Global↔local state communication — WORKS, incl. cross-process

- `counter1`: nonzero total obtained via `send_rpc` to the tool's GlobalState. PASS.
- `counter2` in-process: `total system calls: N, from 1 processes, 1 thread(s)`. PASS.
- `counter2` **production UDS coordinator**: the typed GlobalState coordinator runs outside the
  instrumented tree; a fork/exec child reconnects to the inherited UDS path. Result:
  `counter2 global system calls: N, from 2 processes, 2 thread(s)` — a single coordinator result
  aggregating both processes. This is the global↔local RPC working across a fork/exec tree
  (HEAD commit e4ce514). PASS.

### 3. Cross-backend microbenchmark (see results.csv)

Guest = `getpid()` loop of N real syscalls. Per-syscall cost via **two-point slope**
(N1=50k, N2=250k), which cancels fixed startup; min-of-3 wall time. Host: shared devserver,
heavily loaded (absolute times noisy; slope + min-of-3 robust).

| config | marginal µs/syscall | interpretation |
| --- | --- | --- |
| native | 0.069 | raw `getpid` |
| ptrace **noop** | 0.067 | **no trap** — empty subscriptions → syscalls pass at native speed |
| dbi **noop** | 1.969 | DynamoRIO intercepts *every* syscall regardless of subscription |
| ptrace **counter1** | 26.514 | full ptrace-stop round trip per observed syscall (the ptrace tax) |
| dbi **counter1** | 2.468 | in-process RPC + interception |

**Headline:** for a tool that observes every syscall and does a GlobalState RPC per syscall
(`counter1`, apples-to-apples on both backends), **DBI is 10.7× faster than ptrace**
(2.468 vs 26.514 µs/syscall marginal). DBI has a ~2 µs *flat* interception floor (noop 1.97 →
counter1 2.47, +0.5 µs for the actual RPC because it stays in-process). ptrace is **bimodal**:
near-native (0.067 µs) for unsubscribed syscalls, but ~26 µs for every observed one.
**Trade-off:** ptrace wins for sparse-subscription tools (pays nothing for unobserved
syscalls); DBI wins decisively (≈10×) for observation-heavy tools (counter1, strace, Detcore),
which trap on most/all syscalls. Consistent with the earlier per-syscall benchmark (memory
`gvisor-reverie-persyscall-benchmark`: ptrace ~40 µs, dbi ~1 µs).

### 4. `hermit run --backend dbi --strict` ratchet — 8/8 L2

Real `detcore::Detcore` hosted over `reverie_dbi::DbiGuest` in-process. All L2 (`--strict --verify`
"Determinism verified"), cross-checked against ptrace:
`echo hello`, `date -u`, `true`, `whoami`, `seq 5`, `head -3 /etc/hostname`, `echo -n abc`, `uname -s`.

- `echo` memory_hash `9175dd372546a296` (matches the recorded main baseline).
- **`date -u` no longer hangs.** Memory `detcore-over-dbi-blocked-by-executor` recorded
  `date -u` hanging >600s under DBI (clock/single-step). On hermit `a6573a3c` it is L2:
  prints the virtual epoch `Thu Jan 1 12:00:00 AM UTC 2026`, memory_hash `f0565a37aaf4a99a`,
  "Determinism verified". The clock/single-step hang is resolved on this build.
- No divergences observed → no "turn X→X+1" mismatch to fix in this batch; the debugging
  methodology applies to the next program that *does* diverge (frontier extension work),
  which would be Detcore-over-DBI changes in the hermit repo (out of scope for a read-only
  strict-compat assessment on another agent's branch).

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/dbi/reverie
PROFILE=debug bash reverie-dbi/scripts/build-client.sh      # DynamoRIO + client
PROFILE=debug bash reverie-dbi/scripts/test-example-tools.sh # 25/25 tools

# microbenchmark
cc -O2 -D_GNU_SOURCE -o /tmp/sl syscall_loop.c
ph=target/debug/reverie-dbi-dynamorio-path
export DYNAMORIO_HOME=$($ph home) REVERIE_DBI_CLIENT=target/debug/reverie-dbi-native/libreverie_dbi_client.so
# ptrace: target/debug/{noop,counter1} -- /tmp/sl N
# dbi:    env HERMIT_DBI_{NOOP,COUNTER1}=1 $($ph drrun) -quiet -disable_rseq -stack_size 2M -c $REVERIE_DBI_CLIENT -- /tmp/sl N

# strict ratchet
../hermit/target/debug/hermit run --backend dbi --strict --verify -- date -u
```

## Files

- `results.csv` — per-config timings and marginal µs/syscall.
- `metadata.json` — SHAs, host facts, method.
- `syscall_loop.c` — benchmark guest.
