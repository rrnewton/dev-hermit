# DBI #1147 CI hang — root-cause correction: post-exec time-rewind panic (not the from_guest exit deadlock)

Date: 2026-07-30
Agent: hermit-238 (opus-4.8), task `p1_fix_dbi_from`
Slot: `worktrees/238` (hermit `fix/dbi-from-guest-exit-deadlock` @ cf6f377f = #1147 head;
reverie `fix/dbi-from-guest-exit-deadlock` @ 4cee948e = #1147 pin, DynamoRIO submodule 929840ad)

## TL;DR

The hosted `Regular tests (GitHub-managed portable)` 900s `test.cli` node TIMEOUT on
Hermit PR #1147 (the `run_dbi_verifies_*` hang) is caused — in the build I could run — by a
**coordinator-side panic**, not the `run_tool_thread_exit_from_guest` exit-RPC deadlock that
task `rebase-rerun-pr1147` / memory `dbi-from-guest-exit-rpc-deadlock-pr1147` hypothesized.
The exit RPC is never even reached in the reproduction.

**Mechanism (decisive, backtrace-confirmed):** a guest-initiated `execve` (e.g. the
`#!/usr/bin/env echo` shebang test's env→echo exec) drives a **post-exec thread
re-registration** that sends `GlobalRequest::CreateChildThread(child==dtid==process, time=0)`
for a tid that already accumulated virtual time (~801.5ms from ld.so + locale loading).
`GlobalState::receive_rpc` (detcore/src/tool_global.rs:681) calls
`GlobalTime::update_global_time(dtid, 0)` which **panics** at detcore-model/src/time.rs:757
("Attempted to update tid X time to 0ns, but was already 801_500_000ns") because time went
backwards. The panic runs on a `tokio-rt-worker` of the in-process DBI coordinator, which
**poisons the scheduler `Mutex`** ("Could not acquire scheduler lock during forced
shutdown (poisoned lock: another task failed inside)"). Every in-flight guest RPC (blocked in
its synchronous UDS round-trip) then hangs → the `cargo test -p hermit --test cli` node
(no per-test timeout) burns the full 900s budget → CI reports whichever `run_dbi_verifies_*`
test was executing as the failure. The victim varies run-to-run because *which* exec-driven
test is running when the panic lands varies.

## Why DBI hits this and ptrace does not

The exec-reconnect machinery added by #1173 ("Fix SaBRe multithreaded exec reconciliation")
skips `update_global_time` for a recognized exec reconnect (tool_global.rs:606-620,677-685:
`exec_reconnect`/`is_thread_reconnect`). That path is populated by a `PrepareExec` RPC sent
from `Detcore::handle_execveat` (detcore/src/syscalls/threads.rs:689). **The DBI backend
never sends `PrepareExec`**: an instrumented run of the env→echo scenario shows only
`CreateChildThread → MarkPastFirstExecve → CreateChildThread(time=0)` — no `PrepareExec`,
no `DeregisterThread`. DBI handles execve natively in the client (reverie-dbi/native/client.c
`requires_native_lifecycle` pause at detcore-dbi pre_syscall lib.rs:1411-1424), then the
post-exec image's first syscall reaches `reverie_dbi_runtime_pre_syscall` with
`scratch.runtime_state.is_null()` and builds a **fresh** thread state via
`tool.init_thread_state(...)` (detcore-dbi/src/lib.rs:1450-1455) — logical time reset to the
epoch. So the coordinator, blind to the exec, sees a live tid's clock rewound to 0.

The initial (hermit→guest) exec is fine because the tid is registered for the first time
(no prior value → no monotonicity check). Only a **second, guest-initiated** exec rewinds a
tid that already has time — which is exactly the failing test set: `simple_env_shebang`
(env→echo), `shell_process_lifecycle`, `process_wait_lifecycle` (fork+exec). Single-exec
DBI programs (echo, true) never hit it.

## Reproduction (in this slot's build)

`hermit run --backend dbi --strict [--verify] -- <env-echo>` (script body `#!/usr/bin/env echo`).
Deterministic panic 4/4 with the fix build, 3/3 with #1147 as-is, and — importantly — **3/3
with detcore-dbi reverted to origin/main** (main pins the same reverie 4cee948e). So the panic
is NOT unique to #1147's diff; it reproduces on main-equivalent detcore-dbi too.

`hermit run --backend dbi --strict --verify -- /usr/bin/env echo hi` (double exec, no shebang)
also panics; `/bin/echo hi` (single exec) does not panic.

## UPDATE 2026-07-30 (hermit-dbi lane): caveat RESOLVED — CONFIRMED canonical

Reproduced on the fully CANONICAL build in the working hermit-dbi env
(`worktrees/dbi/hermit` @ cf6f377f = #1147 head; reverie **git-dep 4cee948e, NO
`[patch]`**; DBI client self-located from the reverie-dbi bundled build). A
direct `hermit run --backend dbi --strict --verify -- <#!/usr/bin/env echo>`
hangs and prints the EXACT predicted panic (`time.rs:757: update tid N time to
0ns, but was already 801_500_000ns` in `receive_rpc` → poisoned scheduler lock).
Control: `/bin/echo hi` and `/bin/true` run clean and fast (single exec). So the
post-exec time-rewind panic is a REAL canonical bug, **not** a build artifact of
the earlier `[patch]`+freshly-cloned-DynamoRIO env, and 236's "30/30 clean" did
not exercise the guest-initiated second exec under this harness. The earlier
env-instability ("even /bin/true hung at startup") was specific to agent-238's
patched build, not the canonical one. Fix in flight: guest-side DetTime
preservation across the DBI exec re-init (candidate #1), hermit-only in
detcore-dbi, pushed onto #1147.

## (historical) caveat — agent-238 could not establish a clean baseline

My DBI environment was unreliable: even `/bin/true` under `--backend dbi` hung at DynamoRIO
client↔coordinator startup (no guest child spawned) 5/5 in some windows, while env-echo runs
executed ~40 syscalls before panicking in others. `drrun` works standalone (prints output,
exit 0), so DynamoRIO itself is fine; the intermittent startup hang is the known in-progress
DBI client-startup/quiescence instability (see `dbi-preemption-in-process-reentrancy-blocker`,
task `dbi_preemption_via_safe`) possibly aggravated by host conditions (hardened kernel
6.18-fbk, concurrent hermit/nix agents).

Because I built reverie + a freshly-cloned DynamoRIO (929840ad) via a local `[patch]`, I
**cannot fully exclude** that the post-exec `runtime_state.is_null()` path (fresh state, time 0)
is taken in my build but not in the canonical git-crate build (a DynamoRIO TLS-persistence-
across-exec build difference could make canonical take the existing-thread branch with
preserved time, avoiding the panic). That would reconcile with agent-236's report of
release env-shebang "30/30 clean" on a quiet host. I could not get a clean canonical DBI
baseline to settle this. **Treat the panic as a strong, decisively-analyzed CANDIDATE for the
CI hang, not a confirmed canonical root cause.**

## Fix candidates (determinization-core; needs a working DBI env to validate)

1. **Preserve the thread's monotonic logical time across the DBI exec re-init** so the
   post-exec re-registration carries the accumulated time (matching global time → no rewind →
   no panic), mirroring ptrace where execve keeps the same task's clock. The pre-exec time is
   lost by the time `pre_syscall`'s fresh-state branch runs (runtime_state nulled during the
   exec pause), so it must be stashed across the exec boundary (native scratch / a tid-keyed
   process-global set before the `RUNTIME_PAUSE_REQUESTED` exec pause, read back in
   `reverie_dbi_runtime_pre_syscall` lib.rs:1450). NOTE: a `thread_init`-only fix does NOT
   work — the time-0 `CreateChildThread` originates in `pre_syscall`, not `thread_init`
   (verified: patching thread_init left the panic 4/4).
2. **DBI sends an exec notification** (a `PrepareExec`, or a dedicated DBI post-exec reconnect
   RPC) so the existing #1173 exec-reconnect machinery recognizes the re-registration and
   skips/repairs the time update. Most faithful to the shared model but larger.
3. **Coordinator tolerates a DBI post-exec self-reconnect**: in `receive_rpc`, when a
   `CreateChildThread(child==dtid==process, None, _)` arrives for a tid already in
   `global_time`, reset that tid's baseline instead of asserting monotonicity. Smallest, but
   touches shared detcore time/scheduling for all backends (trigger #4 territory) and could
   mask genuine rewind bugs — least preferred without owner review.

Any of these is a **new determinization strategy / core scheduling** change =
post-facto-human-review trigger #3/#4.

### Field-level implementation spec for fix #1 (verified against source 2026-07-30)

The naive "restore `thread_logical_time` only" patch is a **silent-determinism trap**, not a
fix. `DetTime` is a struct of counters (`detcore-model/src/time.rs:400`); its `as_nanos()` is
derived. But `ThreadState` caches *coupled* baselines that `init_thread_state` seeds from the
(zero) fresh clock:
- `thread_logical_time: DetTime` (tool_local.rs:1277, `pub`) — the piggyback source
  `receive_rpc` reads as `guest_time` (tool_global.rs:603); this is what must be non-zero to
  avoid the `update_global_time` panic.
- `last_accounted_user_time` / `last_accounted_system_time` (tool_local.rs:1595-1596, set from
  `thread_logical_time.user_cpu_time()`/`.system_cpu_time()`) — the deltas for
  getrusage/times CPU accounting. Left at 0 while `thread_logical_time` jumps to ~801ms, the
  next CPU-time delta is corrupted → **wrong guest-visible rusage, silently, no crash**.
- `committed_clock_value` (tool_local.rs:1631, seeded 0) — RCB commit baseline.
- `past_global_first_execve` (tool_local.rs:1647) — should already be true post-exec.

So a correct fix must carry the whole pre-exec `DetTime` **and** re-derive the coupled
baselines. Concrete plan:
1. `detcore` (additive): add a method on `ThreadState`, e.g.
   `pub fn restore_logical_time(&mut self, t: DetTime)` that sets `thread_logical_time = t`,
   then `last_accounted_user_time = t.user_cpu_time()`,
   `last_accounted_system_time = t.system_cpu_time()`, and leaves `committed_clock_value` /
   RCB caches consistent (verify against `next_timeslice`/`commit_rcbs` expectations). This
   keeps field coupling inside the crate that owns the invariant instead of poking `pub`
   fields from detcore-dbi.
2. `detcore-dbi` (native scratch / process-global): in `reverie_dbi_runtime_pre_syscall` at the
   `requires_native_lifecycle`+`SYS_execve` branch (lib.rs:1347, `runtime_state` still valid),
   stash `(tid → thread.state.thread_logical_time.clone())` into a `LazyLock<Mutex<HashMap<i32,
   DetTime>>>` before the `RUNTIME_PAUSE_REQUESTED` pause.
3. In the fresh-state branch (lib.rs:1390), after `tool.init_thread_state(...)`, if a stashed
   `DetTime` exists for this tid, call `state.restore_logical_time(stashed)` **before**
   `run_tool_thread_start` sends `CreateChildThread` (so the piggyback carries the real time).
   Remove the map entry.

**CRITICAL validation caveat:** because the corrupted-rusage failure mode is *silent* (env-echo
stops panicking and CI goes green even if `last_accounted_*` is wrong), a green env-echo/CI run
does **NOT** prove the fix is determinism-correct. Validation MUST include a CPU-time-sensitive
program under `--strict --verify` (e.g. a guest that calls `getrusage`/`times` after an exec)
plus the full `run_dbi_verifies_*` set, on a canonical DBI env. This is the concrete reason the
task cannot be closed on a green-CI signal alone.

## Separately: a real (latent) #1147 sub-bug found while investigating

#1147's `reverie_dbi_runtime_thread_init` `inherited_parent` COW-rebasing branch
(detcore-dbi/src/lib.rs:951, `host_tid == host_pid && !runtime_state.is_null()`) also matches
a post-exec re-init of the same process, not only a forked copy, forcing
`post_exec_pending=false` for exec. Discriminate by identity (`previous.tid == det_pid` ⇒
exec re-init, keep post_exec_pending; else ⇒ fork copy). This is a genuine correctness
improvement but is NOT the panic cause (the panic is upstream, in pre_syscall) and did not fix
the hang on its own.

## Recommendation

Route to an agent with a **working, canonical DBI validation environment** (e.g. hermit-dbi,
per `dbi-l2-corpus-baseline` build recipe) to: (a) confirm the panic against a clean canonical
baseline, and (b) implement + validate fix candidate #1 (preferred) with `hermit run
--backend dbi --strict --verify` on env-echo/shell/process-wait, then push to #1147's CI.

Local `[patch]` recipe to reproduce (this slot): add
`[patch."https://github.com/rrnewton/reverie.git"]` → local `../reverie/*` crate paths in
hermit `Cargo.toml`; `scripts/backend-submodule.sh activate dynamorio` in the reverie slot;
`cargo build --release -p hermit`.
