# Non-leader-thread `execve` under Hermit: continuous-time + identity probe

**Question (PR #1147 adversarial-review finding).** A secondary (non-leader)
thread calls `execve`. In Linux `de_thread` destroys the other threads and the
caller becomes the new thread-group leader, taking over the tgid (PID). The
finding claimed: detcore already handles this (`reconnect_after_exec` +
`GlobalTime::reassign_thread`), so the **golden ptrace reference is correct** and
the **DBI backend is narrower** — a silent parity divergence where a non-leader
exec re-registers with a fresh epoch and determinism is quietly wrong.

The reviewer specified the verification method: **repeated PRE/POST-EXEC
continuous-virtual-time and identity coverage** (a single sample can pass while
time is blunted — the #1095 clock-freeze lesson).

## Headline result — the premise is CORRECTED, and the gap is BROADER than claimed

1. **The golden ptrace backend does NOT correctly handle a non-leader exec — it
   PANICS (SIGSEGV), not silently mis-determinizes.** Empirically, under
   `hermit run --backend ptrace --strict`, a non-leader exec aborts the whole run
   at `reverie/safeptrace/src/lib.rs:389` (`assume_exited` on a
   `Stopped(Pid(3), …, Exec(Pid(5)))`). Deterministic across runs.
2. **A LEADER exec works perfectly** under ptrace: continuous virtual time
   (`t_pre → t_post` monotonic, +2.25 ms virtual) and correct identity, bitwise
   identical across runs. This isolates the defect to the **non-leader** path.
3. **The specific mechanism in the finding is wrong.** DBI does **not** send
   `PrepareExec` (grep: absent from `detcore-dbi`); it handles `execve` natively
   (pause + image reload). `detcore-dbi/src/lib.rs:1491` is a function argument,
   not a guard. The real `tid == pid` guard is DBI's leader-vs-non-leader
   decision at `detcore-dbi/src/lib.rs:965`/`:993`.
4. **Root cause is in the Reverie ptrace lifecycle layer, upstream of Detcore.**
   `reverie-ptrace` discards the former tid on `PTRACE_EVENT_EXEC`
   (`Event::Exec(_new_pid)` at `task.rs:1972`, `4843`) and has an explicit
   unimplemented marker: `// TODO: Update PID? Need to write a test checking
   this.` (`reverie-ptrace/src/task.rs:2669`). The de_thread'd old leader is
   routed into `handle_exit_event` (`task.rs:4260-4273`), which resumes it
   expecting an exit, receives the Exec event instead, and panics at `4272`.
   Detcore's exec-reconnect machinery (`tool_global.rs:780-820`
   `reconnect_after_exec` + `reassign_thread`, unit-tested at `tool_global.rs:2908`
   `nonleader_exec_rebinds_caller_to_leader_and_preserves_its_clock`) is real and
   correct **but is never reached on the ptrace backend** — the task layer
   panics first. That machinery is driven only by the post-exec
   `CreateChildThread` RPC that the tool-reload backends (SaBRe / in-process DBI)
   issue.
5. **Coverage gap:** there is NO end-to-end non-leader-thread-exec test anywhere
   in the corpus (all exec guests/tests are fork/vfork + **leader** exec). Only
   detcore unit tests exercise the reconnect logic in isolation.

## What was NOT measured (honest limitation)

**DBI was not exercised empirically.** The only prebuilt hermit at PR #1147's
head (`683fb5ca`) has the DBI feature compiled OUT
(`backend dbi is unavailable: DBI support was not included in this build`), and a
DBI-enabled build (DynamoRIO via `hermit-install`) was not undertaken in this
pass. Code analysis predicts DBI would take the leader path post-exec
(`host_tid == host_pid` → `init_thread_state(None)` → fresh `DetTime::new(cfg)`,
no `reassign_thread`), but whether the guest OBSERVES discontinuous time depends
on whether virtualized `clock_gettime` exposes per-thread vs aggregate global
time — that must be MEASURED on a DBI build, not inferred. **Open follow-up.**

## Environment caveat

Runs are inside a 3pai agent sandbox on a loaded host (devbig014). The ptrace
non-leader failure is a **deterministic Rust logic panic** (`InvalidState` at a
specific state transition), corroborated by the in-code `TODO: Update PID?`
(`task.rs:2669`) and by the leader-exec control succeeding in the same
environment — i.e. a genuine product limitation, not a sandbox/permission
artifact. Nesting could still affect exact ptrace timing; the panic itself is a
state-machine defect independent of load.

## Method

- Host: devbig014.atn7.facebook.com
- hermit: `683fb5ca25b6b4af2391c634a01f5245349a46ad` (= PR #1147 head), ptrace
  build; reverie git-dep `d973a85`.
- Guests (this dir): `nonleader_exec.c` (worker thread execs; non-leader),
  `leader_exec.c` (main thread execs; leader control). Both print
  `tid`/`pid`/`leader`(=tid==pid)/`CLOCK_MONOTONIC` at PRE (just before execve)
  and POST (re-exec'd image).
- Backends/modes: native; `hermit run --backend ptrace --strict` (x2 each).
- Logs: `logs/` (`native_*`, `ptrace_leader.txt`, `ptrace_nonleader.txt`, `env.txt`).

## Results (verbatim, see logs/)

```
native   nonleader : PRE tid=2847083 pid=2847079 leader=0 → POST tid=2847079 pid=2847079 leader=1   (monotonic)
native   leader    : PRE tid=2847094 pid=2847094 leader=1 → POST tid=2847094 pid=2847094 leader=1   (monotonic)

ptrace   leader run1: PRE tid=3 pid=3 leader=1 t_pre=1767225600003045915 → POST tid=3 pid=3 leader=1 t_post=1767225600005298720
ptrace   leader run2: (bitwise identical to run1)                          ✓ continuous +2.25ms, identity correct, deterministic

ptrace   nonleader : PRE tid=5 pid=3 leader=0 t_pre=1767225600005171000
                     ERROR reverie_ptrace::task: Error in tracee tid 5: ECHILD
                     ERROR reverie_ptrace::task: Failed to detach from 5: tracee 5 is a zombie
                     panic safeptrace/src/lib.rs:389  InvalidState(...Exec(Pid(5)))  → SIGSEGV
                     (identical both runs; deterministic crash)
```

## Reproduction

```
cc -O0 -g -pthread -o ignored/nonleader_exec nonleader_exec.c
cc -O0 -g          -o ignored/leader_exec    leader_exec.c
HB=<hermit @683fb5ca, ptrace build>
$HB run --backend ptrace --strict -- $PWD/ignored/leader_exec      # works
$HB run --backend ptrace --strict -- $PWD/ignored/nonleader_exec   # panics
```

## Interpretation / next actions

- The finding is REAL in substance (non-leader exec is mishandled) but
  MISLOCATED: it is not a DBI-vs-correct-golden parity divergence. Non-leader
  exec is unsupported engine-wide; the **ptrace** backend crashes at the
  lifecycle layer before Detcore's (correct, tested) reconnect logic runs.
- The fix is a **core Reverie ptrace-lifecycle change**: on `PTRACE_EVENT_EXEC`
  read the former tid (`PTRACE_GETEVENTMSG`, already captured by safeptrace) and
  remap the execing task's identity to the leader instead of routing the old
  leader through the exit path. Per the Reverie API policy this touches the
  syscall-interception / task-lifecycle model — a design discussion, not a
  freelance patch.
- Landable now regardless of the fix: a regression guest (`nonleader_exec.c`) +
  a cli.rs case pinning the gap (xfail/ignored with reason) for ptrace, and, once
  a DBI build exists, the DBI continuous-time + identity comparison this
  experiment could not run.
