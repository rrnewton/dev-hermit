# LiteInst nested-fork wait/reap deadlock — a non-root-parent lifecycle gap

## Question

The LiteInst in-guest flagship supports flat plain-fork: a root process that
forks N leaf children (each exits promptly) and `wait4`s them runs at L2
(`--strict --verify`, byte-identical). The supported boundary claims
"single-threaded plain-fork children sharing the coordinator GlobalTool."

Does that support extend to a **nested** process tree — a fork-child that
itself forks a grandchild and then `waitpid()`s it (i.e. a *mid-tree* process
that is simultaneously a fork-child and a fork-parent-waiter)?

**Answer: no.** A mid-tree process that waits on its own child deadlocks the
LiteInst backend at teardown. The Detcore scheduler is *not* at fault — the
golden ptrace backend runs the identical tree at L2. The gap is LiteInst's
`wait4`/exit **reap mediation for a non-root parent**.

## Method

Three C fixtures (`src/`), each a pure-fork tree with trivial children, run
under `hermit run --backend liteinst`:

- `nested_min.c` — root -> child1 -> **one** grandchild; **child1 `waitpid`s
  the grandchild**; root `waitpid`s child1. (minimal deadlock repro)
- `nested_nowait.c` — same tree, but **child1 does NOT wait** for the
  grandchild (exits immediately). (isolation control)
- `nested_fork.c` — 3-iteration nested fork with waits. (original discovery)

Each fixture was run:

1. native (no hermit) — sanity;
2. `--backend liteinst --strict` — the flagship path;
3. `--backend ptrace --strict` and `--strict --verify` — the golden reference
   (attribution).

Hermit binary: `worktrees/liteinst/hermit/target/debug/hermit`
(`codex/liteinst-flagship-hermit-pin` @ `1470de83`, reverie pin `456b628`).
Timeouts wrap every hermit run; rc=124 = hang.

## Results

See `results.csv`. Key cells:

| fixture         | backend  | mode             | outcome            |
|-----------------|----------|------------------|--------------------|
| nested_min      | native   | -                | ok, rc=0           |
| nested_min      | liteinst | --strict         | **HANG (rc=124)**  |
| nested_min      | ptrace   | --strict         | ok, rc=0           |
| nested_min      | ptrace   | --strict --verify| **L2 verified**    |
| nested_nowait   | liteinst | --strict         | ok, rc=0           |
| nested_fork     | liteinst | --strict --verify| error "-6"         |

The two decisive contrasts:

- **liteinst vs ptrace on the identical `nested_min` tree**: ptrace is L2
  byte-identical; liteinst hangs. => the deadlock is LiteInst-backend-specific,
  NOT a Detcore scheduler defect.
- **`nested_min` (child1 waits) vs `nested_nowait` (child1 doesn't wait)**:
  waiting hangs; not-waiting is fine. => the trigger is a *non-root parent
  blocking in `wait4`/`waitpid`*, not fork depth per se.

## Interpretation (root cause)

`logs/liteinst_nested_min_deadlock.detcore.txt` shows the fork tree forms
correctly three levels deep — `Final thread-tree was: [3 [5 [6]]]`
(DetPid 3 root -> 5 child1 -> 6 grandchild) — then the scheduler stalls after
6 turns (rc=124 after ~89s wall) with run-queue size 3 and:

```
dtid 3, req <ivar ... {InternalIOPolling: W}, fyi: "wait4">, resp <HasWaiter>   # root, waiting on child1
dtid 5, req <ivar HasWaiter>, resp <NoWaiter>                                   # child1, waiting on grandchild
dtid 6, req <ivar ... {Exit { group: true, process: DetPid(6) }: RW}>, resp <HasWaiter>  # grandchild exit never commits
```

Detcore *did* register the mid-tree wait — turn 5 committed
`ParentContinue { parent: DetPid(5), child: DetPid(6) }` — so the coordinator
scheduling rendezvous is correct. What never completes is the grandchild's
actual death being reaped by its real (non-root) parent.

Mechanism (confirmed by source reading of reverie-liteinst + reverie-ptrace):

- The LiteInst lifecycle supervisor is built with `TracerBuilder::<()>` — a
  **unit tool with empty `subscriptions`** (backend.rs `launch`, `None` arm).
  It attaches `PTRACE_O_TRACEFORK|CLONE|VFORK|EXIT`, so descendants of any
  depth auto-attach (tracking is recursive; depth is NOT the problem).
- Because the tool is `()`, `wait4`/`waitpid` is never seccomp-trapped by the
  supervisor. But by ptrace semantics the **tracer** is the process that
  receives every tracee's death/wait status. The grandchild's exit is consumed
  by the supervisor's `handle_exit_event`; it is never re-injected to child1,
  the real non-root parent.
- Detcore's coordinator special-cases the **root**'s `wait4` (root children are
  reaped through the supervisor->coordinator path), so flat `root -> N leaves`
  works. There is no equivalent re-injection/mediation for a **mid-tree**
  parent, so child1's `waitpid(grandchild)` never returns, child1 never reaches
  its own `exit_group`, and root's `wait4` never completes.

This corrects an earlier note that attributed completion to "only the root
child is followed" — following is recursive; the gap is **wait/reap mediation
for a non-root parent**, an interception-model / lifecycle-ownership concern.

## Disposition

This is a genuine LiteInst multiproc-parity gap, precisely bounded and
reproducible. A fix requires the `()` supervisor (or the in-guest Tool +
supervisor jointly) to mediate/re-inject a child's death to its real non-root
parent — a lifecycle-ownership / syscall-interception change, which is
owner-gated under the Reverie API Policy (and a candidate for
post-facto-human-review). It is therefore reported as a blocker, not fixed
autonomously. The shipped flagship increment (flat plain-fork L2, PR #1466)
is unaffected; this defines the next multiproc rung.

## Reproduction

```bash
cd ~/work/dev-hermit
HB=worktrees/liteinst/hermit/target/debug/hermit   # build: cargo build -p hermit
D=experiments/liteinst_nested_fork_wait_reap_gap_20260802/src
cc -O2 -g -Wall -Wextra -Werror -o /tmp/nested_min    $D/nested_min.c
cc -O2 -g -Wall -Wextra -Werror -o /tmp/nested_nowait $D/nested_nowait.c

timeout 40 $HB --log=info run --backend liteinst --strict -- /tmp/nested_min      # -> HANG (rc=124)
timeout 40 $HB --log=error run --backend liteinst --strict -- /tmp/nested_nowait  # -> nowait-ok rc=0
timeout 90 $HB --log=info  run --backend ptrace   --strict --verify -- /tmp/nested_min  # -> L2 verified rc=0
```
