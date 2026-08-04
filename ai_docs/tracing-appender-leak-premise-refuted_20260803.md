# tracing-appender "leak on abandoned exit": premise empirically refuted

**Date:** 2026-08-03
**Agent:** hermit-ci (opus-4.8)
**Task:** `tracing-appender-not-shutdown-on-exit` (P1)
**Verdict:** The stated causal model is **wrong**. The `tracing-appender` worker
is a *passenger* in already-stuck hermit supervisor processes, not the cause of
namespace retention or unreaped zombies. Do **not** change the appender guard.

## The claim under test

hermit-ptw reported: of 478 hermit processes, 415 were zombies across 8 dead PID
namespaces; each zombie's "companion" was a `tracing-appender` thread parked in
`__futex_wait`. Inferred mechanism: *the supervisor's main thread exited while the
non-daemon appender worker stayed parked forever, and that lone live thread holds
the PID namespace open so the zombies can never be reaped.* Proposed fix: drop the
guard / join / daemonize the worker.

The task itself warned: "Do not infer the fix from the code change; today produced
seven cases of exactly that inference being wrong." This investigation took that
literally and tried to *reproduce* the leak. It does not reproduce, and direct
inspection of the live leaked processes contradicts the mechanism.

## What the code actually does (confirmed)

- The worker is spawned by `tracing_appender::non_blocking(f)` at
  `hermit-cli/src/bin/hermit/tracing.rs:29`; the returned `WorkerGuard`
  (`init_file_tracing`, tracing.rs:44, `#[must_use]`) is the only path that
  flushes + signals shutdown + joins the worker on `Drop`.
- It is created **inside** the PID-namespace child: `global_opts.rs:56-57`
  ("If using a container, this must be done *inside* of the container because the
  tracer may create a new thread") and `run.rs:2975` (`let _guard =
  global.init_tracing();` inside `with_container`).
- The namespace child is forked by **glibc** `libc::clone()`
  (`reverie-process/src/clone.rs:38`). When the child callback **returns** — it
  returns `0` at `reverie-process/src/container.rs:801` after serializing the
  result — glibc's clone wrapper calls `_exit()`, i.e. the **`exit_group`**
  syscall. `exit_group` terminates **every** thread in the process, including the
  appender worker.

**Consequence:** on *any* path where the child callback returns (normal exit,
error, guard-dropped or not), `exit_group` kills the worker. The non-daemon worker
therefore **cannot** outlive the supervisor via the callback-return path. The only
way the worker persists is if the process **never returns from the callback** —
i.e. it is hung or spinning *inside* `f()`.

## What the live processes actually show (measured)

Scanning `/proc/<hermit-pid>/task/<tid>/comm` for the worker (note: the comm is
truncated to 15 chars, **`tracing-appende`** — grepping the full 16-char
`tracing-appender` silently matches nothing; this is a real measurement trap).

Population fluctuated 15 → 73 → 6 within minutes as agents spawned/completed and
hermit-ptw tore down. At the 73-process peak:

- **73 supervisors carried a parked worker. Leader-thread states: 14 R, 59 S.
  ZERO defunct (Z) leaders.** The main thread is **alive in 100% of cases** — so
  the namespace is held by the whole live process, never by a lone orphan worker.
- Two shapes:
  - **Hung** (dominant): main in `epoll_wait` (syscall 232, tokio reactor idle),
    tokio workers up, a live or ptrace-stopped guest child. A live mass-parallel
    `hermit run --strict --verify -- awk 'BEGIN{...}'` burst showed **59/59
    trivial ~1 ms guests hung for ~2 minutes.**
  - **Spin-stuck**: main **R** (running), no guest, no tokio — only main + parked
    worker; process finished its work but never exits.
- **The 39 concurrent system zombies were NOT children of any appender-carrying
  supervisor** (every such supervisor had 0 zombie children). The claimed
  "appender thread → namespace retained → zombies unreapable" link is **absent**
  in the live state.

## Reproduction attempts (all clean — zero leaks)

Using the current debug binary (`hermit/target/debug/hermit`, symbols matching):

| scenario | result |
|---|---|
| normal `run -- /bin/true` (`--log-file`) | exits, **0 leaked worker** |
| `run -- sh -c '<busy loop>'`, SIGKILL launcher mid-run | supervisor orphans to ppid=1, **keeps running the guest**, exits cleanly when guest ends — 0 leak |
| launcher SIGKILL **then** guest SIGKILL | supervisor **exits cleanly** — 0 leak |
| `run --strict --verify`, SIGKILL launcher mid-Run1 | supervisor keeps driving guest correctly — 0 leak |
| 6× `run --strict --verify -- awk`, staggered launcher + guest kills | **0 orphaned/leaked supervisors** across all 6 |

No abandonment path (tool-cap kill of launcher, recycle, detached run, verify)
strands the worker or leaves a namespace-holding process. This matches the code:
`exit_group` on callback return cleans everything up.

## Conclusion

The real resource leak is **hung / wedged hermit supervisors** — processes stuck
*inside* the clone callback (detcore/reverie deadlock, worst under mass-parallel
`--strict --verify` load; also seen on e9patch/sabre/kvm) so they never reach
`exit_group`. Each holds its PID namespace via the **entire live process**; the
`tracing-appender` worker is one of several co-resident parked threads, not the
namespace-holder and not the cause of unreaped zombies.

Changing the appender guard would:
1. **not** free any of the observed leaked processes (main is alive and holds the
   namespace regardless of the worker), and
2. risk the exact flush regression the task forbids ("do not fix a leak by
   silently dropping buffered logs").

So the appender fix is **not warranted**. It would be fixing a bug that isn't the
bug — treating a symptom's neighbor.

## Recommended forward work

1. **The real bug:** `hermit run --strict --verify` hangs — main parks in tokio
   `epoll_wait` with the guest not progressing — especially under mass-parallel
   load. This is the process that holds the namespace. Owners: detcore/reverie
   scheduling + whoever runs the mass-parallel verify sweep. Related known
   livelock/hang family: mass-parallel-drain saturation, min-vtime
   blocking-via-polling livelock, demo5 unbounded spin, JVM max-timeslice
   livelock.
2. **hermit-ptw** already owns teardown/cleanup of the leaked set (the correct
   symptomatic remediation). Keep it.
3. **Monitoring:** the `pids` / namespace-lifetime axis is the right signal (CPU
   and memory look healthy while these accumulate) — but the thing to count is
   **orphaned hermit supervisors (ppid=1) with a live main thread**, not
   tracing-appender threads specifically.

## Reproduce

```bash
# Correct scan (comm truncated to 'tracing-appende'; restrict to hermit pids):
ps -eo pid=,comm= | awk '$2=="hermit"{print $1}' | while read p; do
  for t in /proc/$p/task/*/comm; do grep -ql tracing-appende "$t" && { echo "$p"; break; }; done
done
# For each, the leader thread is alive (state R/S, never Z); shape is hung
# (main wchan=do_epoll_wait, guest child present) or spin-stuck (main R, 2 threads).
```
