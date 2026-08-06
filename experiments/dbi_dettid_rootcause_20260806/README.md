# DBI's host-TID leak: detcore never virtualizes `dettid`, and ptrace's determinism is borrowed from the PID namespace

**Task:** `dbi-determinize-detlog-thread-id` (P0, i=90) · **Agent:** hermit-audit
(`[impl agent, opus-5]`) · **2026-08-06** · local only, no egress.

## Root cause, in one sentence

**`detcore` does not virtualize the thread id — it uses whatever tid the backend hands it — and there
is a standing in-code TODO saying exactly that.** ptrace only *looks* deterministic because hermit runs
its guest in a fresh PID namespace, so the raw tid is already small and stable. DBI's tool-side tid is
not namespace-translated, so the host TID reaches the DETLOG unchanged.

## The chain, each link read from source

**1. The emitter is innocent.** Both branches of `detlog_memory_maps` use the same source
(`detcore/src/lib.rs:740`, `:757`):

```rust
let dettid = guest.thread_state().dettid;
detlog!("[memory][dtid {}] ...", dettid, ...)
```

**2. `dettid` is the backend-supplied tid, cast** (`detcore/src/lib.rs:1150`):

```rust
// TODO(T78538674): virtualize tid, extend tid<=>dettid mapping here.
match parent {
    None => ThreadState::new(DetPid::from_raw(tid.into()), &self.cfg, record_or_replay),
```

**The comment on the line above the defect names it.** There is no `tid → dettid` mapping; the root
thread's deterministic identity *is* the host-supplied one.

**3. ptrace's determinism is borrowed, not earned.** `container.rs:137` and `:171` do
`.unshare(Namespace::PID)`. Inside a fresh PID namespace the guest's tid is a small stable number —
`3` — so `DetPid::from_raw(tid)` *looks* virtualized when it is merely namespaced.

**4. DBI has no such translation on the tool-side tid**, so the raw host TID flows straight through.

## Measured, first-hand

| backend | 3 (resp. 2) identical runs | dtid |
| --- | --- | --- |
| **dbi** | run1 / run2 / run3 | **2179371 / 2179454 / 2179541** |
| ptrace (control) | run1 / run2 | **3 / 3** |

The DBI values are not merely unstable — they **increase monotonically**, which is the host tid
allocator advancing as other processes spawn between my runs. That is the signature of a raw host
identity, not of a virtualized one that happens to vary. Corroborates the prior evidence
(7 runs → 7 host TIDs, `experiments/dbi-strict-parity_20260806`).

## The sharp part: DBI has *two* identity paths, and only one is virtualized

PR #1147's test `run_dbi_virtualizes_process_identities` asserts canonical virtualized PIDs
(`pid=3/4/6/9`) under DBI — and it passes. So **what the guest sees via `getpid()` IS virtualized.**
What is not virtualized is the **tool-side** identity that detcore stamps into every DETLOG record.

Two identity paths, one correct, one leaking. That is why this was invisible until someone compared
DETLOG framing across backends: every guest-visible determinism test passes.

## Why this is P0 for the parity work

Every `[memory]`, `[syscall]` and scheduling DETLOG record carries `[dtid N]`. With N differing on
identity alone, **no cross-backend DETLOG comparison can ever match**, regardless of content. This is
the prerequisite the task states, and my companion measurement confirms it: comparing ptrace-vs-DBI
`[memory]` records yields 0 shared tuples before content is even reachable
(`experiments/dbi_detlog_parity_answered_20260806`).

## Fix design — and what would be the wrong fix

**Wrong (violates #140):** hardcode `dettid = 3`, or strip the field from the record. Both erase
**thread distinctness** — a two-thread guest would become indistinguishable from a one-thread guest,
which is coarsening the very signal the log exists to carry.

**Right — and the TODO already prescribes it:** *"virtualize tid, extend tid⇔dettid mapping here."*
Concretely, in `detcore::init_thread_state`:

* maintain a detcore-side `tid → DetTid` map assigning ids **in first-appearance order** from a
  counter (root gets the first ordinal; children get subsequent ones in creation order);
* look up on every subsequent access, so the same host thread always maps to the same `DetTid`;
* this preserves **distinctness and aliasing** — the determinism signal — while erasing the host
  value. It is the canonicalize-don't-strip discipline applied to identity, the same shape as the
  register-hash work's address ordinals.

A worthwhile side effect: it makes **ptrace's** determinism *earned* rather than a side effect of the
PID namespace, so the property no longer depends on a container flag remaining set.

## Bracket to ship with the fix (the task's three, made concrete)

1. **Repeated runs keep identities and aliasing stable** — run the same guest N× under DBI; assert the
   multiset of `dtid` values is *identical* across runs. Fails today (2179371/2179454/2179541).
2. **Two guest threads remain distinct** — a two-thread guest must yield **≥2 distinct** `dettid`s, and
   the same two across runs. This is the control that refuses the "hardcode 3" non-fix.
3. **A planted host-TID leak is refused** — assert no `dettid` exceeds a small ordinal bound
   (host TIDs on this box are ~2.1 × 10⁶; virtualized ordinals are single/double digits). Plant a raw
   host tid and confirm the check fires.

## Not done

* **The fix is not implemented.** It is hermit product code, and the only build I have is
  `worktrees/oci/hermit`, which is **owned by hermit-oci** — I must not edit another agent's slot.
* I did not check whether non-root threads (the `Some(pts)` branch) have the same defect; the
  measurement used a single-threaded guest.
* I did not confirm which layer supplies the tid on the DBI path (reverie-dbi vs the DynamoRIO client);
  the defect is established at the detcore consumer, and the mapping fix is correct regardless of
  supplier.

## Provenance (#268)

Binary `worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features
third-party-backends`. Guest `~/.local/hermit-deps/guests/guest_heap` (`gcc -O1`, dynamic).
Flags `--log=info --strict --no-virtualize-cpuid --max-timeslice=disabled --detlog-stack`.
`LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`. DBI DETLOG captured from **stderr**
(it ignores `--log-file`); ptrace from the log file.
