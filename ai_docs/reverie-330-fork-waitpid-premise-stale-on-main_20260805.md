# reverie #330 fork/wait: the failing assert exists only on a 77-commit-stale branch — premise is stale on main, and it does NOT share the clone-SIGSEGV mechanism

**Task:** `fix_reverie_330_fork` ("fix_reverie_330_fork_wait_reconstruction")
**Agent:** hermit-clone (opus-5), 2026-08-05. **Constraint:** box-wide egress 403 → LOCAL ONLY.
**Investigation was read-only.** No product file was modified; see §5 — this task's slot is owned
and dirty under a *different* agent.

---

## 1. Headline

Two findings, both checkable from local git objects:

1. **The exact cited failure site does not exist on reverie `main`.** The task points at
   `reverie-liteinst/src/bin/rpc_tool_guest.rs:538`,
   `assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child)`. On main
   (`025d3780`) that string appears **nowhere in the file**. It exists only on the
   `fix/reverie-330-fork-waitpid` branch in slot `worktrees/fork330`.
2. **That branch is 77 commits behind main and is missing the owner's own rewrite of exactly this
   mechanism** — `718686c "reverie-liteinst: detect fork at the interception point, not via
   pthread_atfork"` (Ryan Newton, 2026-08-04 17:15 PDT).

```
git -C worktrees/fork330/reverie rev-parse --short HEAD      # d3ed343
git -C worktrees/fork330/reverie merge-base --is-ancestor 718686c HEAD   # rc=1  -> ABSENT
git -C reverie      merge-base --is-ancestor 718686c 025d3780            # rc=0  -> on main
git -C worktrees/fork330/reverie rev-list --count HEAD..025d3780         # 77
# HEAD is NOT an ancestor of main -> diverged (it merges codex/liteinst-perf-attribution-fastpath)
```

`718686c` touches precisely the files under investigation — `rpc_tool_guest.rs` (+57),
`rpc.rs`, `tool_host.rs`, `tests/rpc_tool.rs` — and its own commit message records:

> with the fix, `cargo test -p reverie-liteinst --all-features -- --test-threads=1` passes
> **17/17** … Full `./validate.sh`: 6 passed, 0 failed.

**Conclusion: the task premise ("new regression test fails deterministically") describes the
stale branch, not main.** The most likely correct disposition is *rebase onto main and re-derive*,
not "fix the fork path".

---

## 2. Was the assertion relaxed to make it pass? — **No** (with one honest coverage caveat)

The task explicitly forbids relaxing the test, so this needed checking. On main the wait is:

```rust
fn wait_for_child(child: libc::pid_t) {
    let waited = unsafe { reverie_liteinst_rpc_wait4(child, &mut status, 0, null_mut()) };
    assert_eq!(waited, i64::from(child));
    assert!(libc::WIFEXITED(status));
    assert_eq!(libc::WEXITSTATUS(status), 0);
}
```

`reverie_liteinst_rpc_wait4` is **hand-written assembly containing a bare `syscall`**
(`rpc_tool_guest.rs:272-287`):

```asm
reverie_liteinst_rpc_wait4:
    mov r10, rcx
    mov eax, 61                     ; SYS_wait4
reverie_liteinst_rpc_wait4_site:
    syscall
```

So it is the **identical kernel call** (`SYS_wait4`, `options = 0`), merely placed at a labeled
site so the framework's hook can be installed and *counted*. The semantic assertion is unchanged
("a blocking wait must return the child pid"), and main **adds** assertions the old form lacked:
`site_trap_count(wait_address) == 1` and `site_hook_count(wait_address) == 1`. Introduced by
`9e7af7d` (#362), not by 718686c.

**Honest caveat — one real coverage delta.** `libc::waitpid` no longer appears anywhere in
`rpc_tool_guest.rs` on main. The raw-syscall site does not exercise **glibc's `waitpid` wrapper**
(its internal bookkeeping / cancellation handling) under the in-guest tool. If the original
`waitpid → 0` anomaly was specific to the *libc wrapper* path rather than to raw `SYS_wait4`, main
would no longer catch it. **Recommended, cheap:** keep the instrumented assertion and add one
plain `libc::waitpid` child alongside it, so both paths are covered.

---

## 3. Does it share the `clone_with_stack` SIGSEGV mechanism? — **No as a cause; yes as a latent class**

Compared against
`ai_docs/clone-with-stack-sigsegv-is-perf-timer-panic-not-355-livelock_20260805.md`:

| | clone-SIGSEGV | #330 fork/wait |
|---|---|---|
| Layer | host-side `reverie-process` sandbox-container creation | in-guest `reverie-liteinst` SIGSYS/patch interception |
| Backend | ptrace (hermit supervisor startup) | LiteInst in-guest tool |
| Symptom | child **died by signal**, diagnostic destroyed | clean `assert_eq!` failure, `left: 0 right: <pid>` |
| Shared code | none — disjoint crates, no shared reap/clone path | |

**Not the same bug.** (This matches hermit-238b's earlier independent read that #330 and #355 are
disjoint; the clone-SIGSEGV is disjoint from both.)

**But the *class* transfers, and it is an unfixed exposure in liteinst.** The clone-SIGSEGV's
generalizable defect is *a panic crossing a `nounwind` FFI/handler frame becomes an uncatchable
abort that destroys the diagnostic*. `reverie-liteinst` has the same shape and no guard:

- `grep -rn catch_unwind reverie-liteinst/src/` → **no matches**
- `unsafe extern "C" fn host_syscall_hook` — `runtime.rs:1596`
- `unsafe extern "C" fn installed_syscall_hook` — `runtime.rs:1614`
- `unsafe extern "C" fn tool_trampoline` — `runtime.rs:1804` (this one calls the concrete
  `Tool::handle_syscall_event`, i.e. arbitrary user Rust)

`installed_syscall_hook` already calls `exit_now(122)` on a null context — the code *knows* you
cannot unwind out of these frames, but nothing catches a tool panic. A panicking tool therefore
dies by signal with the message lost, exactly as in the clone case. This is a **latent
diagnosability defect, not the cause of the #330 assert failure**; worth filing separately and
fixing with the same `catch_unwind` shape.

---

## 4. If the failure does reproduce post-rebase: the `wait4 == 0` reasoning

Kept because it is cheap and may save the owner a cycle. A blocking `wait4(pid, &status, 0)`
**cannot legally return 0** — Linux returns 0 only under `WNOHANG` with no state change. So a
literal 0 means one of:

- **(a) `options` acquired a `WNOHANG` (bit 0) value** through arg capture. Capture on main is
  `args: [rdi, rsi, rdx, r10, r8, r9]` from `HookContext` (`runtime.rs:1628-1635`) — the correct
  x86-64 syscall ABI, so this requires the LiteInst-generated trampoline to mis-populate
  `HookContext`, not an ABI mistake in the Rust.
- **(b) the syscall never executed and `result` was left/forced at 0.** Note the dispatch path has
  a sentinel (`if event.result == UNSET_RESULT { event.result = -ENOSYS }`, `runtime.rs:1654-1656`)
  — so an *unset* result surfaces as `-ENOSYS`, not 0. A literal 0 must be **written** by some
  branch. The reentry branch (`CURRENT_EVENT != null` → `forward_nested_tool_syscall`,
  `runtime.rs:1641-1647`) sets `ENOTSUP`/`EPERM`/raw-result — all non-zero for a successful wait.
- **(c) child already reaped elsewhere** → would be `-ECHILD`, **not** 0. Ruled out.

(a) and (b) are distinguished by one datum: the `options` value actually passed to the kernel.
`hermit-fork330`'s in-slot probe already prints `a0`/`a2`/`result` for `SYS_wait4` — that is the
right instrument; it just needs to run on a **rebased** base.

---

## 5. Ownership conflict — flagged, not resolved

`worktrees/ACTIVE.md:371` registers this task's slot to a **different agent**:

```
| fork330 | hermit-fork330 | - | fix/reverie-330-fork-waitpid | - | fix_reverie_330_fork | active | no |
```

and that slot is **dirty** with live uncommitted work:

```
 M reverie-liteinst/src/runtime.rs      (+112)   # diagnostic probes, "Remove before land"
 M reverie-liteinst/src/tool_host.rs    (+12)
```

`tg claim fix_reverie_330_fork` reassigned the task owner to `hermit-clone` while
`hermit-fork330` holds the slot and mid-debug state. Per Hard Invariants 2 and 5 I did not touch
that slot. **Coordinator decision needed:** either return the task to `hermit-fork330` (carrying
this artifact's rebase finding), or have them hand off the slot. Do not run two agents at this
diff.

---

## 6. What I could not settle locally, and why

I could not produce a live pass/fail of
`installed_hook_reentry_bypasses_tool_with_shared_coordinator_rpc` **at main's head**. A clean
out-of-tree build (`CARGO_TARGET_DIR=scratch/fork330-probe/target cargo test --offline
-p reverie-liteinst --test rpc_tool`) fails in a transitive dependency:

```
unwind-sys-0.1.4/build.rs:11: pkg-config exited with status code 1
  Package 'libunwind-ptrace', required by 'virtual:world', not found
```

No `libunwind-ptrace.pc` exists on this host; existing checkouts only build because their *shared*
`target/` already contains the prebuilt artifact. Building into the primary's `target/` is not
sanctioned for a non-integration agent, and reflink-seeding a cmake-backed cache across worktrees
is a known cross-worktree poison. So the live run needs a **coordinator-allocated slot** (and, for
a fresh cache, either a `libunwind-ptrace` pkg-config file or an unaffected feature selection).

Standing in for it: `718686c`'s recorded 17/17 + `validate.sh` 6/0 at that head, plus reverie's
`merge-gate-v2` landing requirement (both `Regular tests` and `Host-dependent tests` green at the
exact head). That is **landing-gate inference, not a local measurement** — labelled as such.

---

## Evidence index

- Stale assert: `worktrees/fork330/reverie:reverie-liteinst/src/bin/rpc_tool_guest.rs:538,563`
  (absent on main)
- Main's wait path: `reverie/reverie-liteinst/src/bin/rpc_tool_guest.rs:566-573` (`wait_for_child`),
  asm site `:272-287`
- Fork detection rewrite: reverie `718686c`; instrumented-wait introduction: `9e7af7d` (#362)
- Arg capture / dispatch / sentinel: `reverie/reverie-liteinst/src/runtime.rs:1628-1656`
- Unguarded `extern "C"` frames: `runtime.rs:1596`, `:1614`, `:1804`; no `catch_unwind` in
  `reverie-liteinst/src/`
- Slot registry: `worktrees/ACTIVE.md:371`
- Reverie main at time of writing: `025d3780`
