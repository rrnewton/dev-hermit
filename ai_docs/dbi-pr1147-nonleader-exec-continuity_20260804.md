# DBI #1147 non-leader-thread exec continuity — premise CONFIRMED, fix + test WRITTEN & compile/native-verified, land BLOCKED on #1200

- **Task:** `fix_pr_1147_nonleader` (P0, owner hermit-dbi)
- **Date:** 2026-08-04 (updated 2026-08-04 second session: designs upgraded to written, ready-to-apply, compile+native-verified artifacts — see "Written artifacts" at end)
- **Verified against:** hermit `683fb5ca` (PR #1147 head), static source trace only (no runtime repro yet — see Blocker).
- **Origin:** adversarial-review finding on PR #1147 (comment 5170042181). Premise came from a
  review note; per *Establish What You Have* it was treated as UNVERIFIED and the first step was to
  verify it. **Outcome: CONFIRMED** (a refutation would have been an equally valid deliverable).

## The finding, in one line

DBI sends `PrepareExec` **only when `tid == pid`** (the thread-group leader). Linux permits *any*
thread to `execve`, and Detcore's coordinator already implements the non-leader case end-to-end. So a
**non-leader DBI exec bypasses the coordinator's continuity path and silently re-registers with a
fresh epoch** (logical clock reset to the container epoch) plus an orphaned caller identity — the
exact "time-blunting on exec" the owner flags as a major red flag.

## Static verification (both halves proven)

### Half 1 — DBI structurally excludes non-leader execs
`detcore-dbi/src/lib.rs:1491`, inside the `SYS_execve` arm of `reverie_dbi_runtime_pre_syscall`:

```rust
if tid == pid && !scratch.runtime_state.is_null() {
    let thread = unsafe { &mut *scratch.runtime_state };
    if thread.initialized {
        send_dbi_prepare_exec(context, tid, pid, branches, &mut thread.state, ...);
    }
}
```

A non-leader thread has `tid != pid` (its TID differs from the process's TGID), so the whole block is
skipped — **no `PrepareExec` is sent**. The comment even scopes this to "EVERY initialized process
leader (`tid == pid`)", confirming the leader-only intent.

### Half 2 — the coordinator fully supports the non-leader case
`detcore/src/scheduler.rs` `reconnect_after_exec` has a dedicated, documented `caller != new_leader`
branch:

> "Every sibling disappears. If a non-leader called exec, Linux also changes that surviving task's TID
> to the process leader's TID. In that case the old caller registration is retired and a fresh leader
> registration is installed before it is removed…"

And `detcore/src/tool_global.rs`:
- `:727` `PrepareExec` recv records `PendingExecState { caller: dtid, process, mm, fd_blocking }`
  keyed by `process` (detpid). **`caller = dtid` = whichever thread sent PrepareExec.** A PrepareExec
  from non-leader T would record `caller = T`.
- `:774` post-exec `CreateChildThread(new_leader==dtid==process)` self-registration matches the pending
  record → `exec_reconnect = Some` → calls `reconnect_after_exec(ExecReconnect{ caller: pending.caller,
  new_leader: dettid, ... })`.
- `:796` **`if pending.caller != dettid { global_time.reassign_thread(pending.caller, dettid) }`** —
  transfers the non-leader caller's *accumulated logical clock* to the new leader identity. This is
  precisely the continuous-time preservation for a non-leader exec.

**The non-leader continuity path is complete and correct — it is gated entirely behind DBI sending a
`PrepareExec`, which the `tid == pid` check suppresses.**

## Exact runtime consequence (static prediction; runtime disambiguation pending)

With no `PrepareExec`, `pending_exec_states` has no entry for the process, so at the post-exec
self-registration (`tool_global.rs:606-684`):
- `exec_reconnect = None` and `is_exec_caller_after_local_mm_swap = false`.
- The self-registration therefore falls to one of:
  1. **retired-incarnation rejection** (`:628` tombstone check, if `rpc_incarnation_matches` fails on
     the post-exec mm) → `R::ThreadExited` → the exec'd program is told to exit (loud-ish, wrong program
     never runs); or
  2. **`update_global_time(dtid, 0)` panic** (Bug-A class) if the incarnation happens to match with an
     accumulated-time tid; or
  3. **silent fresh registration** via `recv_create_child_thread` → epoch-reset clock, orphaned caller
     identity — the outcome the finding names.
- In all three, `reassign_thread(caller, new_leader)` **never runs** → the non-leader's accumulated
  time is lost. Which of 1/2/3 fires depends on runtime mm-incarnation details — hence a *first-sample*
  check would miss the silent case; you must compare the *continuous* accumulated clock across the exec
  boundary, repeatedly.

## Fix design (hermit-only, detcore-dbi)

Broaden the gate at `detcore-dbi/src/lib.rs:1491` so `send_dbi_prepare_exec` also fires for a non-leader
exec (`tid != pid`, still requiring `!runtime_state.is_null()` and `thread.initialized`). The pre-exec
non-leader thread state is live at the syscall, so `prepare_exec` records `caller = T`, and post-exec
reconciliation runs `reassign_thread(T, pid)` + the `caller != new_leader` branch → clock + scheduler
identity continuity, matching ptrace/SaBRe. `send_dbi_prepare_exec` already builds the guest with
`(tid, pid)`, so it needs the non-leader `(T, pid)` unchanged.

**Non-trivial parts requiring runtime validation (why this is not a one-liner):**
1. **Bug-A background-thread ownership.** A non-leader exec kills the leader + siblings; the surviving
   task adopts the leader TID. Whether the survivor owns a DynamoRIO background client thread
   (`RUNTIME_BACKGROUND_OWNER_PID` gate, added by Bug-A fix) for the pause handshake must be verified —
   a non-leader exec is a *different* lifecycle from the forked-child exec Bug-A handled.
2. **Coupling to #1200 (the real land blocker).** `reconnect_after_exec` → `logically_kill_thread` →
   `remove_tid` hits the unconditional assert `runqueue.rs:392` — this is exactly `fix_1200_codex_review`
   finding #3 ("in the very race the PR claims to harden"). The non-leader path retires *more* tids
   (all siblings + caller) so it exercises that assert *harder*. Landing the non-leader fix on top of
   an un-hardened `reconnect_after_exec` would trade a silent epoch reset for a panic. **#1200's reconnect
   fix must land first.**

## Repeated continuous-time + identity test design (deliverable 2)

New C guest (`hermit/tests/c/dbi_nonleader_exec_continuity.c` + a manifest entry, or a
`run_dbi_verifies_*` CLI test):
- Parent spawns a second pthread. The **non-leader** thread (`gettid() != getpid()`) calls `execve`
  to re-exec a small reporter.
- Reporter emits, **before and after** the exec, an identity+time bracket: `getpid()` (must be stable
  across the non-leader exec — kernel makes the survivor's TID = pid) and a monotonic
  logical-time-sensitive probe (`clock_gettime(CLOCK_MONOTONIC)` and/or `times()`/`getrusage`, which
  read Detcore accumulated time).
- **Assertions (bracket from both sides):**
  - *Negative:* on the buggy (leader-only) build the post-exec accumulated time is **< pre-exec**
    (epoch rewind) and/or pid changes — the test must FAIL, proving the mechanism is not inert.
  - *Positive:* on the fixed build post-exec time is **≥ pre-exec** (non-decreasing) and pid is
    stable — the test PASSES, proving the fix fires.
  - Run under `--strict --verify` (L2) and require byte-identical determinism across runs.
- **Repeated** samples across the boundary, not one: a single post-exec read can look plausible while
  the *continuous* clock has silently reset.

## Disposition

- **Premise: CONFIRMED** (static). Precise silent-vs-panic-vs-reject runtime disambiguation and the
  fix's runtime validation are **BLOCKED on `fix_1200_codex_review`** (reconnect_after_exec assert
  hardening) landing, plus a canonical DBI env with the combined #1147+#1200 stack.
- Task stays `in_progress`, NOT `implemented` (policy: blocked tasks never tag implemented). Land order:
  #1200 → rebase #1147 → this non-leader fix on top.
- Siblings `fix_pr_1147_fail` (fail-open 1e6-iter poll bridge) and `fix_pr_1147_failed` (no CancelExec
  rollback) share the same #1200 blocker and the same exec bridge; fixing all three together on the
  rebased stack is the efficient path.

Related: `dbi-pr1147-ci-hang-postexec-time-rewind_20260730.md`,
`dbi-pr1147-second-hang-forked-child-no-background-thread_20260730.md`,
`dbi-pr1147-tentative-pop-vs-exec-bootstrap-race_20260731.md`.

---

## Written artifacts (2026-08-04 second session — designs → ready-to-apply, verified where solo-possible)

State established this session (re-verified, not inherited on trust):
- `#1200` **still OPEN @9761a4ac** (not landed), `#1147` **still OPEN @683fb5ca**. The land blocker is
  therefore **real**. The whole PrepareExec bridge (`send_dbi_prepare_exec` + the `tid == pid` gate) exists
  **only on the #1147 branch**; on `main` (`8f656b4d`) it is absent. So this is a *pre-land review finding on
  #1147's own code*, and the fix modifies the unmerged #1147 branch, not `main`.
- No free canonical DBI env: the `dbi` slot has a warm DynamoRIO build but is owned by
  `groupa-drain-1256-1290` on `drain/pr-1471-rebased` (do not disturb); slot pool at/over cap; solo, no
  coordinator to allocate. Fresh combined #1147+#1200 DBI build is heavy and hang-prone. So a clean
  **positive** runtime validation remains blocked; the **negative** (buggy) repro needs only a #1147-head
  DBI env, still unavailable solo this session.

### Artifact 1 — exact fix (hermit-only, `detcore-dbi/src/lib.rs`, on the #1147 stack)

Broaden the `SYS_execve` gate so a **non-leader** exec (`tid != pid`) also sends `PrepareExec`. Drop the
`tid == pid &&` clause; keep `!scratch.runtime_state.is_null()` and `thread.initialized`.
`send_dbi_prepare_exec(context, tid, pid, …)` already carries both ids, so a non-leader records
`caller = T` and post-exec reconciliation runs `reassign_thread(T, pid)` + the `caller != new_leader`
branch → clock + scheduler-identity continuity, matching ptrace/SaBRe.

```diff
-            if tid == pid && !scratch.runtime_state.is_null() {
+            // A NON-LEADER thread may also execve (Linux permits any thread to
+            // exec). Detcore's `reconnect_after_exec` implements `caller !=
+            // new_leader`, and `reassign_thread(caller, new_leader)` carries the
+            // caller thread's accumulated logical clock to the surviving leader.
+            // Sending `PrepareExec` for a non-leader (`tid != pid`) is exactly
+            // what lets that continuity path run; the old `tid == pid` gate
+            // suppressed it, so a non-leader exec silently re-registered the
+            // post-exec leader with a FRESH EPOCH (virtual-time rewind).
+            if !scratch.runtime_state.is_null() {
```

Note on the pause handshake just below: `owns_background_thread =
RUNTIME_BACKGROUND_OWNER_PID.load(..) == pid`. A non-leader exec keeps the **same pid**, so the process
still owns its background thread and the handshake runs correctly (this is *unlike* the forked-child copy
case, where pid differs and the handshake is skipped). No change needed there — but this is the exact
"Bug-A background-thread ownership" point to confirm at runtime.

### Artifact 2 — regression guest (WRITTEN, compiles clean, native run verified)

Full source staged at `scratch/dbi-1147-nonleader_20260804/dbi_nonleader_exec_continuity.c`; drop into
`hermit/tests/c/dbi_nonleader_exec_continuity.c`. It brackets the defect per
`continuous-virtual-time-is-sacred` (REPEATED samples, cross-exec, cross-thread, identity + time):

- A **non-leader** pthread (`gettid() != getpid()`) samples `(pid, CLOCK_MONOTONIC)` `NUM_SAMPLES=8`
  times (asserting strict increase), then `execve("/proc/self/exe", …)` in "reporter" mode, handing its
  virtual pid + last pre-exec monotonic reading across via argv.
- The reporter samples again and asserts: **pid stable** across the exec (exit 13 if not), **first
  post-exec reading ≥ last pre-exec reading** (exit 14 on epoch rewind — the buggy-DBI signature), and
  the post-exec sequence **strictly increasing** (exit 15).

**Verification done this session (native gcc, no hermit):**
`gcc -D_GNU_SOURCE -pthread -Wall -Wextra` → clean, no warnings. Native run exits 0 with pid stable and
monotonic non-decreasing across the boundary; a deliberately-mismatched reporter invocation exits 13,
proving the identity assertion is not inert. Native cannot exhibit the DBI epoch-rewind (native monotonic
never rewinds) — it proves the **guest logic is correct**, so under hermit any non-zero exit is a real
backend defect, not a test bug.

Expected under hermit: buggy leader-only DBI → non-zero (14 epoch rewind, or 13 identity change, or a
loud reject/panic if the mm-incarnation path fires per the three-way prediction above). Fixed DBI +
ptrace/SaBRe baseline → 0.

### Artifact 3 — harness wiring (models: `dbi_exec_failure` / `dbi_wait_lifecycle`)

1. `tests/e2e/manifests/c-programs.toml`: add a `[[test]]` mirroring the `dbi_exec_failure` block
   (`lane="portable"`, `requires=["linux","x86_64","userns","ptrace","cc"]`, the same
   `[test.build].cflags` incl. `-pthread`, `observation={status=true,stdout=true,stderr=false}`). **Unlike
   the migrated corpus, ENABLE `dbi` in `[test.modes.verify].backends_enabled`** (that is the whole point —
   the other entries disable DBI with "qualify DBI separately"). Keep `ptrace`+`sabre` enabled as the
   golden baseline. Add a `[test.modes.run]` too.
2. `tests/e2e/manifests/inventory/test-files.json`: add the `tests/c/dbi_nonleader_exec_continuity.c` entry.
3. `hermit-cli/tests/cli.rs`: add the `run_dbi_*` CLI-test entry alongside the other `dbi_*` guests.

### Disposition this session

Task kept **`in_progress`** with the `blocked_by fix_1200_codex_review` edge intact. **NOT tagged
`implemented`**: no pushed branch, no PR, and no runtime validation — hermit `CLAUDE.md` "Not done"
explicitly covers "code written but uncommitted/not pushed" and "builds locally, no PR" → stays
`in_progress`, never `IMPLEMENTED`. Tagging implemented here would manufacture the phantom the policy
forbids. Unblock paths, in order of value: (a) coordinator allocates a slot → build #1147-rebased-on-#1200
DBI stack → reproduce the negative (buggy) case to disambiguate silent/reject/panic, apply Artifact 1,
confirm positive at L2 `--strict --verify`, open a **stacked draft PR** (base #1147) → then `implemented`;
(b) wait for #1200 to land, rebase #1147, then (a). Do all three #1147 siblings
(`fix_pr_1147_fail`, `fix_pr_1147_failed`, this) together on the rebased stack — same blocker, same bridge.

### Artifact 2 full source (verbatim — drop into `hermit/tests/c/dbi_nonleader_exec_continuity.c`)

```c
/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 */

/*
 * Regression guest for PR #1147 non-leader-thread exec continuity.
 *
 * A NON-LEADER thread (gettid() != getpid()) calls execve. Linux permits this:
 * the surviving task adopts the thread-group leader's TID, every sibling
 * disappears, and the new program image runs as the leader. Detcore's
 * reconnect_after_exec implements this path and, via reassign_thread(caller,
 * new_leader), carries the caller thread's ACCUMULATED VIRTUAL CLOCK across the
 * exec boundary so virtual time stays continuous.
 *
 * The DBI backend only sends PrepareExec when tid == pid, so a non-leader exec
 * bypasses that continuity path and silently re-registers with a FRESH EPOCH
 * (virtual time reset to the container origin) -- the "per-exec clock reset"
 * red flag that continuous-virtual-time-is-sacred forbids.
 *
 * This guest brackets the defect from both sides by sampling virtual time
 * REPEATEDLY on each side of a non-leader exec (a single post-exec sample can
 * look plausible while the continuous clock has silently reset):
 *
 *   Phase 1 (no argv):   leader spawns a pthread; the non-leader thread samples
 *                        (pid, CLOCK_MONOTONIC) N times, then execve()s this same
 *                        binary in "reporter" mode, passing its virtual pid and
 *                        its LAST pre-exec monotonic reading via argv.
 *   Phase 2 ("reporter"): samples (pid, CLOCK_MONOTONIC) N times and asserts
 *                        - pid is STABLE across the exec (identity continuity), and
 *                        - every post-exec reading is >= the pre-exec reading
 *                          (no epoch rewind) and strictly increasing among
 *                          themselves (fine-grained continuous advance).
 *
 * Under `hermit run --strict [--verify]` CLOCK_MONOTONIC is Detcore virtual time.
 *   - Buggy (leader-only) DBI: post-exec monotonic < pre-exec (epoch rewind)
 *     and/or pid changes  => this guest exits non-zero (mechanism is not inert).
 *   - Fixed DBI (and ptrace/SaBRe baseline): non-decreasing across the boundary,
 *     pid stable  => exits 0 (mechanism fires and preserves continuity).
 */

#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <sys/syscall.h>

#define NUM_SAMPLES 8

static pid_t gettid_(void) {
  return (pid_t)syscall(SYS_gettid);
}

static uint64_t monotonic_ns(void) {
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
    perror("clock_gettime");
    _exit(20);
  }
  return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* Read the clock NUM_SAMPLES times and require a strictly increasing, hence
 * fine-grained and continuous, sequence. Returns the last (largest) reading. */
static uint64_t sample_monotonic_strictly_increasing(const char *phase) {
  uint64_t prev = 0;
  uint64_t last = 0;
  for (int i = 0; i < NUM_SAMPLES; i++) {
    uint64_t now = monotonic_ns();
    if (i > 0 && now <= prev) {
      fprintf(stderr,
              "%s: CLOCK_MONOTONIC not strictly increasing at sample %d: "
              "%llu <= %llu\n",
              phase, i, (unsigned long long)now, (unsigned long long)prev);
      _exit(21);
    }
    prev = now;
    last = now;
  }
  return last;
}

/* ---- Phase 1: the non-leader thread that execs ---- */

static void *nonleader_exec(void *arg) {
  (void)arg;

  pid_t tid = gettid_();
  pid_t pid = getpid();
  if (tid == pid) {
    fprintf(stderr, "expected a NON-leader thread but tid == pid == %d\n", pid);
    _exit(10);
  }

  uint64_t pre = sample_monotonic_strictly_increasing("pre-exec(non-leader)");

  /* Re-exec this same binary in reporter mode, handing across the pre-exec
   * virtual pid and the last pre-exec virtual-time reading. */
  char pid_arg[32];
  char pre_arg[32];
  snprintf(pid_arg, sizeof(pid_arg), "%d", (int)pid);
  snprintf(pre_arg, sizeof(pre_arg), "%llu", (unsigned long long)pre);

  extern char **environ;
  char *const argv[] = {"reporter", "reporter", pid_arg, pre_arg, NULL};
  execve("/proc/self/exe", argv, environ);

  perror("execve"); /* only reached on failure */
  _exit(11);
}

/* ---- Phase 2: the re-exec'd reporter ---- */

static int reporter(int argc, char **argv) {
  if (argc != 4) {
    fprintf(stderr, "reporter: expected 3 args, got %d\n", argc - 1);
    return 12;
  }
  pid_t pre_pid = (pid_t)atoi(argv[2]);
  uint64_t pre_time = strtoull(argv[3], NULL, 10);

  pid_t post_pid = getpid();
  if (post_pid != pre_pid) {
    fprintf(stderr,
            "IDENTITY DISCONTINUITY: pid changed across non-leader exec: "
            "pre=%d post=%d\n",
            (int)pre_pid, (int)post_pid);
    return 13;
  }

  /* First post-exec reading must not be < the last pre-exec reading: that is
   * the epoch rewind the leader-only DBI path produces. */
  uint64_t first_post = monotonic_ns();
  if (first_post < pre_time) {
    fprintf(stderr,
            "VIRTUAL-TIME REWIND across non-leader exec (fresh epoch): "
            "pre=%llu first_post=%llu\n",
            (unsigned long long)pre_time, (unsigned long long)first_post);
    return 14;
  }

  /* And the post-exec sequence must itself advance continuously. */
  uint64_t prev = first_post;
  for (int i = 1; i < NUM_SAMPLES; i++) {
    uint64_t now = monotonic_ns();
    if (now <= prev) {
      fprintf(stderr,
              "post-exec CLOCK_MONOTONIC not strictly increasing at %d: "
              "%llu <= %llu\n",
              i, (unsigned long long)now, (unsigned long long)prev);
      return 15;
    }
    prev = now;
  }

  if (printf("non-leader exec continuity OK: pid=%d pre=%llu first_post=%llu "
             "last_post=%llu\n",
             (int)post_pid, (unsigned long long)pre_time,
             (unsigned long long)first_post, (unsigned long long)prev) < 0) {
    return 16;
  }
  return 0;
}

int main(int argc, char **argv) {
  if (argc >= 2 && strcmp(argv[1], "reporter") == 0) {
    return reporter(argc, argv);
  }

  pthread_t t;
  int rc = pthread_create(&t, NULL, nonleader_exec, NULL);
  if (rc != 0) {
    fprintf(stderr, "pthread_create: %s\n", strerror(rc));
    return 17;
  }
  /* The non-leader thread execs, replacing the whole image; the leader here
   * never returns from the join in practice. Join anyway for the native
   * (non-exec-failing) path and to keep the leader alive until then. */
  pthread_join(t, NULL);
  return 0;
}
```

Native verification (this session): `gcc -D_GNU_SOURCE -pthread -Wall -Wextra` clean; two native runs exit
0 (pid stable, monotonic non-decreasing across the non-leader exec); `./a.out reporter 4242 100` exits 13
(identity assertion fires). Under hermit DBI the epoch-rewind path is what turns exit 0 into non-zero.
