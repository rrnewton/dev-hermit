# DBI DETLOG's raw host `dtid`: the cause is a missing tid mapping, not a DBI bug

**Task:** `dbi_detlog_prints_a` · **Date:** 2026-08-07
**Method:** source verification at hermit `f89c69766371806d3c9b2c3003531df2d59d6118`. Read-only;
no code changed, no branch, no PR.

## The finding in one line

`dettid` is never virtualized by detcore. Under ptrace it only *looks* virtualized because hermit
runs the guest in a **PID namespace**; DBI has no such namespace, so the same code path prints the
raw host tid.

## The chain, each link read rather than inferred

**1. The emitter is correct and backend-agnostic.** `detcore/src/lib.rs:718-764`
(`detlog_memory_maps`) — both the backend-supplied-regions path (`:742`) and the
`/proc/<pid>/maps` fallback (`:759`) read the same value:

```rust
let dettid = guest.thread_state().dettid;
detlog!("[memory][dtid {}] ...", dettid, ...)
```

No backend branch, no formatting difference. detcore prints what the Guest hands it.

**2. detcore assigns `dettid = tid`, and says so.** `detcore/src/lib.rs:1137-1149`:

```rust
fn init_thread_state(&self, tid: Tid, parent: Option<(Tid, &Self::ThreadState)>) -> Self::ThreadState {
    ...
    // TODO(T78538674): virtualize tid, extend tid<=>dettid mapping here.
```

and `detcore/src/tool_local.rs:1603` — `dettid: pid`. **The virtualization this defect assumes
exists is an explicit, tracked in-tree TODO.**

**3. ptrace gets small tids from a PID namespace, not from detcore.**
`hermit/hermit-cli/src/lib.rs:289` — `unshare(CLONE_NEWUSER | CLONE_NEWPID)`. Inside that
namespace the guest's tids *are* 2, 3, 4…, so `dettid = tid` yields a small stable number and the
absence of virtualization is invisible.

**4. DBI has no PID namespace.** `grep CLONE_NEWPID reverie/reverie-dbi/src` → nothing.
`reverie-dbi/src/tools.rs:1417,1467` keys its thread registry on `tid.as_raw()`, and
`init_thread_state` (`:762`) ignores both arguments. So detcore receives raw host tids and prints
them: `dtid 2877095`, `dtid 3928318`, host-magnitude and different every run.

## Why this reframes the fix

The obvious reading — "reverie-dbi forgot to virtualize its tids" — is **wrong**, and a DBI-local
patch that manufactures a small number would be a third wrong answer: it would make DBI's logs
*look* like ptrace's while the tid↔dettid mapping still does not exist, and any consumer treating
`dtid` as a stable identity would remain wrong under every backend that lacks a PID namespace.

Two defensible directions, neither validated here:

- **Give DBI the namespace.** Bring DBI's launcher to the same `CLONE_NEWPID` footing as ptrace.
  Smallest change; makes DBI match the existing (implicit) contract rather than inventing a new one.
  Needs checking against DynamoRIO's own process handling.
- **Implement the mapping the TODO names.** Do the tid↔dettid virtualization in detcore, which
  fixes every present and future backend at once and removes the reliance on a namespace side
  effect. Larger, and it touches thread identity — Reverie API core-abstraction territory, so an
  owner call.

**Do not patch the emitter.** Masking or normalising `dtid` at `detcore/src/lib.rs:742` hides the
defect at the point of display while leaving the wrong identity in the thread state that every
other consumer reads.

## What a correct test must assert

Stability **across runs** and **equality with the ptrace reference** — not that the value is small.
A magnitude check ("not host-sized") would pass a different wrong answer.

## Scope, unchanged

`dtid` is log framing and is **not** hashed, so `[stack]`/`[heap]` hash values are unaffected; the
adjacent stack-hash defect had an unrelated cause (DynamoRIO residue, reverie#394). The harm is
that any consumer diffing whole DETLOG lines across runs or backends sees spurious divergence on
every line.

## Not established here

- No live DBI reproduction was run for this note. The reproduction is the task's two independent
  measurements (hermit-w8, then hermit-w6) at two hermit SHAs; I verified the *mechanism*, not the
  observation.
- Whether DynamoRIO tolerates `CLONE_NEWPID` is unchecked, so direction 1 is a candidate, not a
  recommendation.
