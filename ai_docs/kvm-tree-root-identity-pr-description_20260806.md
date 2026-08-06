# PR description (ready to open): reverie-kvm tree-root identity vs guest-visible getppid

**Stack:** `kvm-tree-root-identity` (reverie-only; zero hermit diff)
**Branch:** `fix/kvm-tree-root-identity-vs-guest-ppid`
**Head:** `ad1b845c5d0a454d51c5d4d44f3bb655ab59241c`
**Base:** `rrnewton/reverie` `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` (= the exact reverie rev
that hermit `main` @ `4c70658e785834737cbe1524f77330c781a6f5ea` already pins)
**Target:** `rrnewton/reverie:main`

---

`[impl agent, opus-5]`

## Summary

`ExecutorState::ppid` in `reverie-kvm` was carrying two different meanings in one field:

* the **guest-visible `getppid()`** value, returned to the guest by `executor.rs` for `SYS_getppid`; and
* the **parent within the traced tree**, which `parent_pid()` exposes as `Guest::ppid()` and which
  Reverie's default `is_root_process() = self.ppid().is_none()` consumes.

Those are not the same value. Under the ptrace backend the container's root guest reports
`getppid() == 1` (the PID-namespace init) while `Task::ppid` — the traced-tree parent — is `None`,
because the root tracee has no traced parent. KVM synthesizes its guest identity instead of
entering a real namespace, and stored the guest-visible value in the field Reverie reads for tree
identity.

This commit carries tree-rootness explicitly in `LoadedStaticElf::tree_root` rather than inferring
it from a value that legitimately means something else:

* the freshly loaded root guest sets `tree_root: true`;
* `try_clone_for_fork` sets `tree_root: false` — a forked child always has a traced parent;
* `thread_child` inherits it, because a thread of the root process is still inside the root
  process, and `is_main_thread()` is what distinguishes the leader;
* `parent_pid()` returns `None` for the tree root and is otherwise unchanged.

`SYS_getppid` still reads `state.ppid`, so guest-visible parentage is untouched.

26 added lines, 2 files, no deletions, no behaviour change outside the KVM backend.

## Why this matters: it is the KVM startup livelock

The conflation was latent for as long as KVM's root guest reported a guest-visible `getppid() == 0`,
which made `parent_pid()` return `None` *by accident*. Hermit commit
`8b7345103a896072cf98ddad46dd4022035d852b` ("kvm: use canonical Detcore root pid") then began
configuring `set_root_pid(ROOT_DETPID)` — pid 3, ppid 1 — so KVM would match the ptrace reference.
That is a correct parity change, and it removed the accident:

1. `set_root_pid(3)` sets `loaded.ppid = root_parent_pid(3) = 1` (`vm.rs:313`, `vm.rs:535`).
2. `parent_pid()` therefore returns `Some(1)` instead of `None` (`executor.rs:1721`).
3. `is_root_process()` — hence `is_root_thread()` — becomes **false** for the root
   (`reverie/src/guest.rs:82-91`).
4. Detcore's `handle_thread_start` takes neither its `pending_vfork` branch nor its
   `guest.is_root_thread()` branch (`detcore/src/lib.rs:1303`), so the root is never admitted to
   the run queue.
5. The scheduler daemon waits forever for a guest thread start that cannot arrive, and the
   single-threaded tokio `current_thread` runtime never parks.

Result: **every KVM guest livelocks at startup**, burning exactly one core and never completing.
Bisected to that commit over hermit `82a8e853..f89c69766` (9 steps, each a fresh build plus a boxed
probe); the parent and the first-bad commit carry the *same* reverie pin, so the boundary is a
single-variable differential.

## Determinism

The change is deterministic, and strictly increases determinism, on three independent grounds.

**1. It removes no virtualization and adds no host-derived input.** `tree_root` is a compile-time
constant at each of its three assignment sites: `true` for the ELF-loaded root, `false` for a fork
child, inherited for a thread child. It is never read from the host, never derived from a clock,
address, host PID, or scheduling order, and never varies between two runs of the same guest. There
is no path by which it can differ across executions.

**2. It restores, rather than weakens, the property Detcore relies on.** Detcore's root admission
is a total function of "is this the tree root", and the tree root is a structural fact fixed before
the guest executes its first instruction. Before this change that question was answered by
`ppid().is_none()` — a *proxy* that happened to correlate with rootness only while the root's
guest-visible parent was 0. Answering it from the recorded structure makes the admission decision
independent of a guest-visible value that is free to change for parity reasons. The prior code was
not deterministically wrong in a random way; it was deterministically wrong in one direction, which
is why the livelock is 100% reproducible.

**3. It does not restore the previously-broken behaviour to make anything pass.** The alternative
"fix" — dropping hermit's `set_root_pid(ROOT_DETPID)` — also clears the livelock, and was rejected:
it re-breaks identity parity with the golden ptrace reference on all three of `getpid`, `getppid`
and `gettid` (measured below). This commit is the fix that satisfies both properties at once.

Fork/thread reasoning, stated explicitly because it is the only non-root case: a forked child is
by construction not the tree root and gets `false`; a thread child shares the process and so shares
its rootness, with `is_main_thread()` — unchanged by this commit — continuing to distinguish the
leader from its peers. So `is_root_thread()` remains true for exactly one task in the tree, which
is the invariant Detcore's admission path depends on.

## Linux Semantics

Guest-visible semantics are unchanged and stay at ptrace parity. `SYS_getppid` continues to read
`state.ppid`, so the root guest still observes `getppid() == 1`, exactly as it does under ptrace
inside a PID namespace whose init is 1, and exactly as Linux reports for a process whose parent is
init. `tree_root` is invisible to the guest: it has no syscall, no `/proc` surface, and no register
or memory footprint. The distinction this commit draws — guest-visible parent versus tracer-tree
parent — is the same distinction reverie-ptrace already makes, so the two backends now model
Linux's process tree the same way instead of only one of them doing so.

## Relationship to gVisor

gVisor's Sentry owns a synthetic task tree and keeps the guest-visible parent (`getppid`, reported
from its own `Task`/`ThreadGroup` links) separate from the supervisor-side structure that decides
which task is the tree's root; the two are never read off one field. This commit moves reverie-kvm
to the same separation. KVM previously resembled a Sentry that inferred "am I the init task?" from
the value it was about to hand back to `getppid` — which works only while that value is 0.

## Validation

Host: devbig014, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, `/dev/kvm` present, 316 cores,
shared box. All hermit runs boxed in a transient `systemd-run --user` unit; CPU read from the
unit's cgroup `CPUUsageNSec`, **not** `/usr/bin/time` (`getrusage` misses the KVM executor, which
lives in a separate PID namespace, and reports a false 0%).

**Reverie unit tests at the exact head `ad1b845c`:** `cargo test -p reverie-kvm` →
**221 passed, 0 failed** (175 + 34 + 6 + 3 + 3 across the lib and integration targets).

**End-to-end, hermit `main` @ `4c70658e7` (the current tip), which already pins reverie `dd3c178e`:**

| hermit build | reverie | `run --strict --backend kvm -- /bin/true` | cores burned |
| --- | --- | --- | --- |
| `4c70658e7` unmodified | `dd3c178e` (pinned) | **KILLED at 45s** | **0.996** |
| `4c70658e7` + this branch | `ad1b845c` | **rc=0** in 0.82–0.87s, 3/3 | 0.43–0.48 |

**Identity parity against the golden ptrace reference**, freestanding static guest issuing raw
`getpid`/`getppid`/`gettid`:

| configuration | output |
| --- | --- |
| ptrace (reference) | `pid=3 ppid=1 tid=3` |
| KVM + this branch | `pid=3 ppid=1 tid=3` ✅ |
| KVM with hermit's `set_root_pid` deleted instead (the rejected alternative) | `pid=1 ppid=0 tid=1` ❌ |
| KVM unfixed | *(no output — livelock)* |

**Limitations, stated rather than implied.**

* No hermit `ci-hub validate-run` receipt is attached. This branch has **zero hermit diff**, so
  there is no hermit head to bind a receipt to; the authoritative gates for it are reverie's
  `Regular tests` and `Host-dependent tests`. Attaching a receipt would have required a
  `[patch]`-overridden hermit tree that will not exist at landing time, and would have consumed a
  box-exclusive validate slot for evidence bound to an unpushable SHA.
* The full 184-cell KVM corpus sweep was **started and deliberately abandoned** on an owner
  priority change; it is not quoted here. The Aug-1 baseline for that corpus is 130/200
  L2-deterministic at hermit `82a8e853` (`experiments/kvm_fullcorpus_scorecard_20260801/`), which
  is the number a post-land re-sweep should be compared against.
* Nothing here was pushed: in-jail `git fetch`/push to github.com returns proxy 403.

## Landing shape

Two steps, and the second is free:

1. This reverie PR merges to `rrnewton/reverie:main`.
2. Hermit advances its reverie pin from `dd3c178e` to the merge commit. That is a one-line pin
   bump with no other hermit content, so it should ride an existing pin-bump PR rather than
   consuming its own validate slot.

Hermit `8b7345103` stays. It is a correct parity change; this commit fixes the defect it exposed.
