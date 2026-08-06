# reverie #338 KVM exit statistics: the fork gap is real, and there is a **second, undocumented thread gap** that is strictly worse

**Task:** `reverie_338_aggregate_kvm` ("Reverie #338: aggregate KVM exit statistics from forked children")
**Agent:** hermit-clone (opus-5), 2026-08-05. **Constraint:** box-wide egress 403 → LOCAL ONLY.
**Investigation read-only**; no product file modified (no slot allocated to this agent).
**PR head examined:** `b4bea502` ("reverie-kvm: record vCPU exits in the Detcore production run
loops", 2026-08-02), branch `codex/kvm-backend-stats-provider`. Not on `main` (`025d3780`).

---

## 1. Premise verification — step 1 per the sequencing note

**CONFIRMED, and the reported gap is only half of it.** There are **two** independent leaks, with
*different* mechanisms and different severity.

### Gap A — forked processes: counted, then discarded

`prepare_forked_process` deliberately propagates the request (`vm.rs:823-829`):

```rust
let mut child = Self::from_process_snapshot(child_snapshot)?;
child.thread_ownership = self.thread_ownership;
// Inherit the statistics request so a measured run also accounts for
// child-process vCPU exits (each backend keeps its own counters).
child.stats_request = self.stats_request;
```

`from_process_snapshot` (`:698`) builds the child through
`new_with_memory_and_cpuid_policy` (`:408`), which initializes
`exit_collector: KvmExitCollector::default()` (`:471`) — so the child counts into a **fresh, private**
collector. `finish_forked_process` (`:794-872`) then takes `mut child: ForkedProcess` by value,
uses `child.backend.memory` and `child.pid`, and **returns without ever reading
`child.backend.exit_collector`** — the child backend is dropped and its counts are destroyed.
`backend_stats` (`:1603-1605`) returns `self.exit_collector.snapshot()` — root instance only.

So the child's exits are *recorded and then thrown away*. This matches the adversarial reviewer's
exact-head Codex FAIL (PR #338 comment 5179172374).

### Gap B — guest threads: never counted at all (NOT reported, NOT documented)

`from_thread_state` (`:717-751`) is the `CLONE_THREAD` worker constructor. It sets
`thread_group`, `is_guest_thread`, the transport slot, trampoline addresses, registers, and xsave —
and **never assigns `stats_request`**. The thread backend therefore keeps the constructor default
`BackendStatsRequest::DISABLED` (`:470`), so `record_exit` (`:500-509`) short-circuits on
`request.is_enabled()` and the thread's vCPU exits are **never recorded in the first place**.

Gap B is strictly worse than Gap A for three reasons:

1. It undercounts a **single-process multithreaded** guest — no fork required.
2. It is **silent by construction**: there is no populated collector anywhere to notice, whereas
   Gap A at least leaves a live child collector a test could catch.
3. It is **undocumented**. `backend_stats`'s doc comment warns only about the fork case
   ("forked child processes keep their own counters (see `prepare_forked_process`)"). A reader
   applying that caveat would still wrongly believe thread exits are included.

**Net effect on the API's headline claim.** `KvmBackendStats::total_exits()` is presented as the
KVM analogue of the other backends' patch/trap counters, but at `b4bea502` it counts only the root
vCPU. For any guest that forks or threads, it is an undercount with no error, no warning, and no
way for the caller to detect the shortfall.

---

## 2. Proposed fix — deterministic aggregation

### 2.1 One new primitive

`KvmExitCollector` (`stats.rs:110-131`) is a `BTreeMap<KvmExitReason, u64>` with `record` and
`snapshot` only. Add the merge:

```rust
/// Folds another collector's counts into this one, per reason.
///
/// Summation is commutative and associative, so the merged totals do not depend
/// on the order in which children/threads are merged — only on each contributor
/// being merged exactly once. That is what makes aggregation deterministic
/// without imposing an ordering constraint on concurrent guest threads.
pub fn merge(&mut self, other: &KvmExitCollector) {
    for (&reason, &count) in &other.counts {
        *self.counts.entry(reason).or_insert(0) += count;
    }
}
```

The commutativity argument is the load-bearing determinism claim and belongs in the PR's
**Determinism** section: no merge order can change the result, so a concurrent thread group cannot
make the snapshot nondeterministic.

### 2.2 Gap A — merge at the existing deterministic join point

In `finish_forked_process` (`:794`), before `child` is dropped:

```rust
if self.stats_request.is_enabled() {
    self.exit_collector.merge(&child.backend.exit_collector);
}
```

This point is already the deterministic parent-side rendezvous for the child (it is where
`record_child_exit` and `append_output` run), so it adds no new synchronization and no new ordering.
**Nested forks compose transitively**: a grandchild merges into the child at the child's own
`finish_forked_process`, and the child then merges into the parent.

### 2.3 Gap B — propagate the request, then merge at thread teardown

Two changes:

1. In `from_thread_state` (`:717`), mirror the fork path — `child.stats_request = <parent's>`.
   (The constructor does not currently receive the parent; pass the request in, or set it at the
   call site exactly as `prepare_forked_process` does at `:829`.)
2. Give `GuestThreadGroup` (`:138-149`, which already holds `Mutex`-guarded `exit_code`, `root`,
   `workers`, `worker_handles`, `transport_slots`) one more field —
   `collected_exits: Mutex<KvmExitCollector>` — and merge a worker's collector into it **once, at
   thread teardown** (alongside the existing `release_transport_slot`/join path), not per exit. Cost
   is one lock per thread lifetime, nothing on the vCPU exit path, preserving the crate's
   "adds one enum map and one increment per exit and nothing on the guest fast path" claim.

Then `backend_stats(&self)` unions without mutating (it only has `&self`):

```rust
fn backend_stats(&self) -> Self::Snapshot {
    let mut merged = self.exit_collector.clone();
    merged.merge(&self.thread_group.collected_exits.lock().expect(...));
    merged.snapshot()
}
```

### 2.4 Alternative, if aggregation is rejected

The task allows "explicitly change/document process-scoped API semantics". If that path is taken it
must cover **both** gaps — the current doc comment mentions only forks — and should expose the
scope in the type (e.g. rename to `root_vcpu_exits()` or add a `scope: BackendStatsScope` field), so
a caller cannot silently read a whole-run total that is actually a root-vCPU total. Documentation
alone leaves a footgun that reads as a measurement.

### 2.5 Residual caveat to state in the PR

A forked child that never reaches `finish_forked_process` (error/early-return path) still loses its
counts. Either merge on the error path too, or document it as a known bound.

---

## 3. Test plan

**Runnable today, no guest, no KVM, no slot:** a unit test for `merge` in `stats.rs`'s existing
`mod tests` — build two collectors, merge, assert per-reason sums and that merging in the opposite
order yields an equal snapshot (this is the determinism claim, tested directly). This is the
narrowest layer per `reverie-liteinst`-style change discipline and it is the test that actually
encodes the correctness argument.

**Requires a live guest (blocked, see §4):** the multi-process test the task asks for — a guest that
forks, run with stats enabled, asserting the root snapshot's `total_exits()` strictly exceeds a
fork-free baseline and that hypercall counts include the child's. Plus a threaded variant for Gap B.

---

## 4. Blocker — re-verified LIVE, not inherited

The task's sequencing note (2026-08-04) says the KVM startup livelock blocks the multi-process bar.
**I re-measured it rather than trusting the note.** Solo, nothing else co-scheduled, quiet box:

```
hermit run --backend=kvm /bin/true      # hermit b64d893a release build
```

Live read from `/proc/<pid>/stat`, with ownership proven by `readlink /proc/<pid>/fd/1` matching my
own redirect file (no name/pattern matching — Hard Invariant 15):

| pid | role | state | utime | stime |
|---|---|---|---|---|
| 1184964 | parent hermit | **S** (blocked on child) | 0 | 0 |
| 1184970 | container child | **R** | **0** | **5243 ticks = 52.4 s** |

Burn rate over a 5.0 s window: `utime +0 ticks`, `stime +449 ticks` → **0.898 cores burned, 100 %
system time, zero userspace CPU**. `/bin/true` never exits; stdout and stderr are both 0 bytes.

This confirms the documented **`R` + wall≈CPU burned-core livelock** and sharpens it: the spin is
**entirely kernel-side** (`utime` delta exactly 0), consistent with the recorded
`epoll_wait(fd,[],1024,0)=0` busy-poll signature rather than any guest-side loop. Both PIDs were
then killed by explicit PID — they are gone.

**Consequence:** §3's multi-process/threaded tests cannot run until the KVM startup livelock is
fixed. The `merge` unit test does not depend on it and can land first.

---

## 5. Status and what is needed

- Premise: **verified at `b4bea502`**, plus one new defect (Gap B) the task did not know about.
- Fix: designed with exact call sites and a determinism argument; **not implemented** — no slot is
  allocated to this agent and egress is down (no fetch/push/PR).
- Blocker: **re-verified live with numbers**, not inherited.
- No green is claimed anywhere in this document; nothing was built or run under test.

---

## Evidence index (all at `b4bea502`, file `reverie-kvm/src/vm.rs` unless noted)

- Fork request propagation: `:823-829` · fork child construction: `:698-715`
- Constructor defaults (`DISABLED`, fresh collector): `:408`, `:470-471`
- Drop-without-merge: `finish_forked_process` `:794-872`
- Root-only snapshot: `impl BackendStatsSource` `:1594-1606`
- **Thread constructor missing `stats_request`**: `from_thread_state` `:717-751`
- `GuestThreadGroup` fields: `:138-149`
- Gated recording: `record_exit` `:500-509`
- Collector/snapshot API: `reverie-kvm/src/stats.rs:110-158`
- Adversarial-review FAIL at this head: PR #338 comment 5179172374
