# reverie #344 (KVM `seccomp(2)` determinization): already LANDED; trigger-1 markers verified on main; Claude-side adversarial review with two fix-forward findings

**Task:** `reverie_344_route_new` ("Reverie #344: route new KVM seccomp syscall through required review")
**Agent:** hermit-clone (opus-5), 2026-08-05. **Constraint:** box-wide egress 403 → LOCAL ONLY.
**Read-only**; no product file modified (no slot allocated to this agent).
**No green claimed** — nothing was built or run under test here.

---

## 1. Disposition change: the task says "before publication", but it is published and landed

| SHA | what | on main? |
|---|---|---|
| `3157b542` | original PR #344 head (task description) | no |
| `d58bc00c` | rebased head (prior note, 2026-08-04 00:40) | no |
| **`e0db081`** | **squashed landing commit, 2026-08-04 05:46 EDT** | **YES** |

```
git -C reverie merge-base --is-ancestor e0db081 025d3780   # rc=0
git -C reverie log --oneline -S'TODO-HUMAN-REVIEW(PR-344)' -- reverie-kvm/src/executor.rs
#   e0db081 reverie-kvm: determinize seccomp(2) to EOPNOTSUPP (ptrace parity)
```

**The landed code is byte-identical to the reviewed head.** Extracting the `seccomp` function from
each blob gives the same md5 (`31ee855a…`) and the same 32 lines:

```
git show d58bc00c:reverie-kvm/src/executor.rs | awk '/^fn seccomp\(args/,/^}/' | md5sum
git show 025d3780:reverie-kvm/src/executor.rs | awk '/^fn seccomp\(args/,/^}/' | md5sum
```

So no post-review drift occurred between the reviewed head and the landing. Per the post-facto
protocol §8, anything found now is a **fix-forward follow-up**, not a landing blocker.
(Same squash-renaming trap as #355 and #330: the PR-head SHA is absent from main; only the squashed
commit is reachable. Testing ancestry with the PR head returns a false "never landed".)

---

## 2. Trigger routing — checked against the four triggers

- **Trigger 1 (new syscall support): APPLIES.** `e0db081` adds a new `SYS_seccomp` dispatch arm at
  `reverie-kvm/src/executor.rs:799-802`. The task's complaint — that the PR body claimed no trigger
  applied — was correct at `3157b542`.
- **Trigger 3 (new determinization strategy): correctly does NOT apply.** This mirrors detcore's
  already-established, human-reviewed `seccomp_result` ladder; trigger 3 is for a *new* strategy,
  not an implementation of an established one. The commit message states this accurately.
- **Triggers 2 and 4: do not apply.** No `Tool`/`Guest`/`Backend`/interception-model change; no
  DetCore scheduling change.

### Trigger-1 audit markers — **VERIFIED PRESENT ON MAIN**

`reverie-kvm/src/executor.rs:799-802` (main, `025d3780`):

```rust
} else if number == libc::SYS_seccomp as u64 {
    // AUTONOMOUS-BOT-IMPLEMENTED
    // TODO-HUMAN-REVIEW(PR-344): Review deterministic seccomp unavailability.
    seccomp(args)
```

Both required breadcrumbs are present, correctly scoped to the new dispatch entry, and the
`TODO-HUMAN-REVIEW` carries the right PR id. This satisfies the mechanically checkable half of
trigger 1.

---

## 3. Parity claim — **VERIFIED LINE-FOR-LINE**, not taken on faith

The PR claims the KVM arm mirrors detcore "exactly". Compared against
`hermit/detcore/src/syscalls/misc.rs:42-69`:

| rung | detcore `seccomp_result` | reverie-kvm `seccomp` | match |
|---|---|---|---|
| `op > GET_NOTIF_SIZES` → `EINVAL` | `:49-51` | same | ✅ |
| `STRICT && (flags != 0 \|\| has_args)` → `EINVAL` | `:52-54` | same | ✅ |
| `FILTER && flags & !TSYNC != 0` → `EINVAL` | `:55-57` | same | ✅ |
| `{FILTER,GET_ACTION_AVAIL,GET_NOTIF_SIZES} && !has_args` → `EFAULT` | `:58-64` | same | ✅ |
| fallthrough → `EOPNOTSUPP` | `:66-68` | same | ✅ |

Constants match by value as well as by name — `STRICT=0, FILTER=1, GET_ACTION_AVAIL=2,
GET_NOTIF_SIZES=3, TSYNC=1` in both (`misc.rs:42-46`, `executor.rs:7499-7503`). Predicate **order**
is identical, which matters: the ladder is order-sensitive (an invalid op must yield `EINVAL` before
the `EFAULT` arg check can fire).

`has_args = args[2] != 0` correctly maps to `uargs`, seccomp's third argument, matching detcore's
`call.args().is_some()`. ✅

**Positive bracket:** the new unit test drives `syscall_result(&mut memory, &mut state,
libc::SYS_seccomp, …)` — i.e. through the **dispatch path by syscall number**, not by calling
`seccomp()` directly. It therefore proves the new arm is reachable and wired, not merely that the
helper computes the right value. That is the right shape for a new-dispatch-arm regression.

---

## 4. Claude-side adversarial review (protocol §2)

I am Claude-family and **not** the author (author: Ryan Newton; PR metadata fixed by a gpt-5 impl
agent), so this is a valid independent review. Bound to exact content: the `seccomp` function as
landed in `e0db081`, reachable at `025d3780`.

**Verdict: the implementation is correct and faithful to its stated parity target.** Two
fix-forward findings, neither blocking; one checklist item I cannot verify offline.

### Finding 1 — the filter-flag mask is stale relative to modern Linux (fidelity, low severity)

`SECCOMP_FILTER_FLAG_TSYNC` (1) is only the first of six flags Linux defines: `TSYNC=1`, `LOG=2`,
`SPEC_ALLOW=4`, `NEW_LISTENER=8`, `TSYNC_ESRCH=16`, `WAIT_KILLABLE_RECV=32`. Both detcore and the KVM
mirror return `EINVAL` for any of the other five, whereas a real kernel accepts them as valid flags.

Observable consequence: a guest probing flag support cannot distinguish "this kernel rejects the
flag" (`EINVAL`) from "seccomp is unavailable here" (`EOPNOTSUPP`) — it gets `EINVAL` for five flags
that a real kernel would accept. **No determinism risk** (the answer is a pure function of the
arguments, identical every run); this is a *fidelity* gap only. It is inherited from the reviewed
detcore ladder, so **parity is correctly preserved** — the right fix is in detcore first, with the
KVM mirror following. It belongs in the PR's **Linux Semantics** section as an intentional deviation.

### Finding 2 — no mechanical drift guard between the two ladders (maintenance)

detcore's `seccomp_result` and reverie-kvm's `seccomp` are independent copies of the same
order-sensitive 5-rung ladder with independently declared constants. The dependency direction
(hermit → reverie) forbids sharing the code, so duplication is forced — but **nothing fails if
detcore's ladder changes**: the KVM test asserts absolute errno values, not equality with detcore.

The citation is currently one-directional — the KVM doc comment names
`detcore/src/syscalls/misc.rs`, but detcore's `seccomp_result` says nothing about its KVM mirror. A
one-line back-reference at `misc.rs:48` would put the obligation in front of the next person to edit
the ladder. Cheap, fix-forward, and the same pattern applies to every other detcore→backend parity
mirror.

### Finding 3 — the most security-relevant deviation is correct, and should be stated (not a defect)

Real Linux `SECCOMP_SET_MODE_STRICT` with no flags and no args **succeeds** and thereafter kills the
process on any syscall outside `read`/`write`/`_exit`/`sigreturn`. This implementation returns
`EOPNOTSUPP` instead. That is the **right** choice — a guest that asks for a sandbox and cannot get
one receives a visible error rather than a false success, so a correct guest fails closed rather
than continuing to believe it is confined. It deserves an explicit sentence in **Linux Semantics**
precisely because it is the deviation with security consequences: *Hermit does not sandbox, and it
says so rather than pretending.*

### Cannot verify offline (egress 403)

Protocol §3 makes **Relationship to gVisor** mandatory for KVM changes, and §2 requires both
numbered review-round labels plus both `passed-review-*` labels visible before landing. I cannot
dereference the PR body, its labels, or its review trail. **Since the change is already on main,
these are now audit questions, not gates** — the coordinator should confirm the trail existed at
land time when egress returns. I make no claim either way.

---

## 5. Status

- Task premise ("route through review **before publication**") is **stale**: landed `e0db081`.
- Mechanically checkable trigger-1 requirement (both audit markers, correct PR id, correctly scoped):
  **verified on main**.
- Parity claim: **verified line-for-line**, including constants and predicate ordering.
- Claude-side adversarial review: **performed**, two fix-forward findings above. I cannot post the
  role-tagged review comment or the `adversarial-review-claude1` / `passed-review-claude` labels
  without egress, and I do not claim those labels exist.
- Codex-side review: **not performed** (out of scope for this agent; requires a Codex-family
  reviewer per §2).

## Evidence index

- Landing commit: reverie `e0db081`, ancestor of `025d3780`
- New dispatch arm + both audit markers: `reverie-kvm/src/executor.rs:799-802`
- KVM ladder + constants: `reverie-kvm/src/executor.rs:7499-7530` (approx.), doc comment above it
- Reference ladder: `hermit/detcore/src/syscalls/misc.rs:42-69`
- Dispatch-routed regression test: `reverie-kvm/src/executor.rs` `mod tests`,
  `seccomp_reports_unsupported_matching_detcore`
- Protocol: `.llms/skills/post-facto-review.md` §§1-3, 6, 8
