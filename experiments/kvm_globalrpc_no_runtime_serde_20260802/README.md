# KVM global-state RPC: does serde run at runtime?

**Date:** 2026-08-02
**Task:** `verify_kvm_global_state` (owner-requested)
**Question:** In the ptrace backend, the global-state RPC types carry `Serialize +
DeserializeOwned` bounds, but serialize/deserialize must NEVER run at runtime in a
real (release) run — it is a plain function call within ONE address space. KVM
holds local + global tool state in one address space too, so the same must hold.
Does the KVM global-state-RPC path actually invoke serde at runtime, or is it a
direct in-address-space call? If KVM serializes at runtime, that is a perf bug.

## Verdict: PROPERTY HOLDS for KVM (no runtime serde)

The KVM global-state RPC path is a **direct in-address-space async call** with the
live Rust `Request`/`Response` values. No `serialize`/`deserialize` runs on it — in
release **or** debug. KVM is in fact stricter than ptrace, which does a debug-only
validation round-trip. This is **not** a KVM perf bug.

## Evidence

### 1. Source: `send_rpc` calls `receive_rpc` directly (no serde, any cfg)

`reverie-kvm/src/runtime.rs`:

- `KvmGlobal::send_rpc` (line 333-336):
  ```rust
  async fn send_rpc(&self, message: G::Request) -> G::Response {
      self.state.receive_rpc(self.tid, message).await
  }
  ```
- `KvmGuest::send_rpc` (line 405-414):
  ```rust
  async fn send_rpc(&self, message: <T::GlobalState as GlobalTool>::Request)
      -> <T::GlobalState as GlobalTool>::Response {
      self.global_state.receive_rpc(self.tid, message).await
  }
  ```
  `self.global_state: &'a T::GlobalState` (also `shared_global_state:
  Option<Arc<T::GlobalState>>`) — an in-process reference / `Arc`, i.e. the SAME
  address space. `message` is passed by value straight into `receive_rpc`.

### 2. Contrast: ptrace serializes ONLY in debug builds (validation round-trip)

`reverie-ptrace/src/task.rs:5507` (`WrappedFrom::send_rpc`):
```rust
let deserial = if cfg!(debug_assertions) {
    // debug-only self-check round-trip
    let serial = bincode::serde::encode_to_vec(&args, bincode::config::legacy())...;
    bincode::serde::decode_from_slice(&serial, ...).0
} else {
    args            // release: direct, NO serde
};
self.1.gs_ref.receive_rpc(self.0, deserial).await
```
So in the **release** binary the owner uses (`./target/release/hermit`), NEITHER
ptrace nor KVM serializes global RPC. In **debug**, ptrace does a round-trip;
KVM still does not.

### 3. Dependency graph: KVM does not link the RPC transport crate

- `reverie-kvm/Cargo.toml`: **no** dependency on `reverie-rpc-transport`.
- `cargo tree -p reverie-kvm -i bincode` → bincode reaches reverie-kvm ONLY via
  `reverie-process` (used for the container spawn/bootstrap handshake in
  `reverie-process/src/container.rs:794/839`), **not** for global RPC.

### 4. Tree-wide RPC-serialize call-site census (none in reverie-kvm)

`grep -rn 'bincode::serde' reverie*/src/`:
- `reverie-rpc-transport/src/codec.rs` — UDS+bincode codec (RpcClient/Server;
  used by the genuinely out-of-process backends). reverie-kvm doesn't depend on it.
- `reverie-dbi/src/sync_rpc.rs` — **DBI DOES serialize RPC at runtime, all builds**
  (real cross-process). Separate backend; possible DBI overhead contributor.
- `reverie-preload/src/rpc.rs` — liteinst/preload cross-process RPC.
- `reverie-ptrace/src/task.rs:5512` — the debug-only round-trip above.
- `reverie-ptrace/src/gdbstub/*`, `reverie-process/src/container.rs` — unrelated
  (gdb register hex, container bootstrap).
- **reverie-kvm/src/: ZERO RPC serialize/deserialize call sites.**

### 5. Runtime evidence (real booted KVM guest)

`cargo test -p reverie-kvm --test counter` → **3 passed in 0.06s**, incl.
`hierarchical_counter_aggregates_per_process`, which aggregates syscall counts
**across process boundaries** through `CounterGlobal::receive_rpc` via the KVM
direct-call `send_rpc` path on a guest booted via `/dev/kvm` — with no transport
codec present.

### 6. Link-level artifact (transport codec not linked)

`nm target/debug/deps/counter-<hash>`:
- `reverie_rpc_transport` symbol count: **0** (transport codec absent).
- Present and monomorphized: `<reverie_kvm::runtime::KvmGlobal<HierarchicalCounter>
  as GlobalRPC>::send_rpc` and `<reverie_kvm::runtime::KvmGuest<CounterTool> as
  GlobalRPC>::send_rpc` — the direct-call path.

## Interpretation

- **Property HOLDS.** KVM's global-state RPC is a direct in-process async call; the
  `Serialize + DeserializeOwned` bounds on `Request`/`Response` (reverie
  `tool.rs:120-123`) are a compile-time contract shared across all backends but are
  never exercised by KVM at runtime.
- **Not a KVM perf bug.** KVM avoids even ptrace's debug-mode round-trip. KVM's
  overhead lives elsewhere (vCPU exits / hypercall transport of the *syscall*
  itself), not in global-state RPC serde.
- **Adjacent finding (not KVM):** `reverie-dbi` and `reverie-preload`/liteinst DO
  serialize RPC at runtime in all builds because they are genuinely out-of-process.
  If the owner's serde-overhead concern applies to any backend, it is DBI/liteinst,
  worth a separate perf look.
- **Doc drift:** `reverie/CLAUDE.md` states KVM runs GlobalState "in a coordinator
  process reached over reverie-rpc-transport (UDS + bincode)". That is stale for
  the current code — KVM runs global state in-process (single supervisor,
  direct call). Recommend a doc correction.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/kvm/reverie   # or primary reverie on main
sed -n '333,341p;404,419p' reverie-kvm/src/runtime.rs      # direct send_rpc
sed -n '5507,5520p' reverie-ptrace/src/task.rs             # ptrace debug-only round-trip
grep -n rpc-transport reverie-kvm/Cargo.toml || echo "no transport dep"
cargo tree -p reverie-kvm -i bincode
grep -rn 'bincode::serde' reverie*/src/                    # census
cargo test -p reverie-kvm --test counter                   # runtime, real guest
nm target/debug/deps/counter-* | grep -c rpc_transport     # 0
```
