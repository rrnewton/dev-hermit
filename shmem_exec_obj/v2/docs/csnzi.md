# Scalable closeable admission

`Csnzi<NODES>` is a pointer-free, one-shot closeable scalable nonzero
indicator. It is intended for a shared-memory generation whose participants
need cheap admission and a stable point after which the protected payload can
be reclaimed.

The algorithm is based on Figure 2 of Yossi Lev, Victor Luchangco, and Marek
Olszewski, [*Scalable Reader-Writer
Locks*](http://people.csail.mit.edu/mareko/spaa09-scalablerwlocks.pdf), SPAA
2009. This crate keeps the paper's parent-before-child activation and
redundant-parent compensation, while adding generation-tagged linear tokens,
fail-closed poison, a one-shot lifecycle, and explicit method-tail states.

## Why it scales

Each non-root node has a local count. Its subtree contributes exactly one count
to its parent while the local count is nonzero. A same-leaf arrival from `n` to
`n + 1`, or departure from `n` to `n - 1`, touches only that leaf when both
values are nonzero. Ancestors change only for local 0-to-1 and 1-to-0
transitions. A continuously active leaf therefore holds one root contribution
while arbitrarily many same-leaf tokens come and go.

Activation runs parent first. Several processes racing to activate one idle
node may each arrive at its parent. One publishes the child activation; the
others increment the now-active child and compensate their redundant parent
arrivals. If close reaches a zero root first, the parent arrival fails without
leaving a child update to roll back.

This differs from `admission::CloseableSnzi`, which wraps the PODC 2007 SNZI in
one global reservation gate. That baseline has a simpler terminal scan, but
every entry and departure performs a read-modify-write on the gate. `Csnzi`
removes that per-token root bottleneck.

## Root state machine

The root atomically packs a contribution count and one of these phases:

| Phase | Meaning |
| --- | --- |
| `Open` | New admission may start. |
| `Closed` | Admission is permanently closed; contributions remain. |
| `Closing` | Close claimed an empty root and is verifying all nodes. |
| `OpenDepartureTail` | The last contribution was removed while still open. |
| `ClosedDepartureTail` | The last contribution was removed after close. |
| `Drained` | Closed, verified empty, and permanently sealed. |

`Closing` is necessary even though activation is parent first. Scanning nodes
while the root merely *appears* open and zero can overlap an entrant which has
just incremented the root. Close first changes `Open(0)` to `Closing`, thereby
rejecting root activation, then verifies every node, then seals `Drained`.

The departure-tail states cover a different race. A last departure cannot
publish ordinary root zero and let close report drain while it is still
unwinding through the method. Instead it publishes a non-drainable tail phase.
After the recursive departure returns and an idle-node scan succeeds, it makes
the tail-to-open or tail-to-drained CAS as its final shared-memory access. Close
racing an open tail atomically changes it to a closed tail, so the departure
seals `Drained`.

Non-final departures also make their successful node or ancestor CAS their
last shared-memory access. A later final departure may seal drain while only
stack/register return work remains in an earlier call. The protected payload
must be relinquished before calling `depart`; no departure accesses it.

## Linearization summary

All algorithm atomics currently use sequential consistency.

| Operation | Publication/order point |
| --- | --- |
| Successful entry | Normally the CAS which first represents the operation at an already active node, or the highest newly installed parent/root contribution. If a racing close precedes a later successful join to already represented surplus, the entry is instead ordered at its initial `Open` observation, as in the paper. The leaf CAS always publishes local token ownership. |
| Rejected entry | Observation of closed, closing, drained, or an unowned departure tail. |
| Non-final departure | Successful local or ancestor decrement CAS. |
| Final departure | Tail-to-open or tail-to-drained CAS after unwinding and verification. |
| Close of nonempty root | Open-to-closed CAS. |
| Close of empty root | Open-to-closing CAS closes admission; closing-to-drained CAS publishes reclamation safety. |

`query()` treats both departure-tail phases as nonzero. That keeps the final
departure visible until its linearization/sealing CAS and avoids ordering a
false query before a racing `close()` which still reports `Pending`.

Packed local-count exhaustion observed before an entrant reserves a parent
contribution rejects only that attempt and does not poison the object. If the
count reaches its maximum only after the entrant reserved a parent during a
race, that operation waits for a count to depart and then completes admission.
It cannot return an error after a query or close observed its provisional
contribution. This wait occurs only at the exact 65,535-arrival per-node limit;
process death while waiting retains the parent contribution and fails closed.

The 47-bit activation generation wraps from its maximum to one. A safe typed
token prevents its own activation from reaching zero, so wrap cannot invalidate
that token. Raw tokens are already unsafe linear capabilities whose contract
forbids stale reuse; the generation is a bounded accidental-staleness check,
not a permanent identity. The outer mapping generation remains the durable ABA
boundary across replacement.

Merely observing `Open` does not guarantee that `try_enter` succeeds. A last
departure can win the selected leaf race; the retry may then return `Closed` or
`DepartureTailBusy`. The latter is explicit rather than an unbounded spin. A
live owner normally clears the tail immediately, but a dead owner leaves it
permanently fail closed.

## Crash cuts

No timeout or lease can safely steal a contribution: a stopped process can
resume with its old Rust references. Each interruption point is conservative:

| Process dies after... | Persistent result |
| --- | --- |
| Sampling open, before any CAS | No footprint; a later root operation rechecks close. |
| Parent arrival, before child activation | Leaked ancestor/root contribution. |
| Parent arrival, while a full child count prevents publication | Leaked ancestor/root contribution. |
| Child increment, before returning its token | Leaked participant. |
| Redundant child increment, before compensation | Leaked ancestor contribution. |
| Child 1-to-0, before ancestor departure | Leaked ancestor/root contribution. |
| Root last-count CAS, before tail seal | Permanent departure-tail state. |
| Empty-root open-to-closing CAS | Permanent closing state. |

Every footprint prevents `is_drained()` from returning true. Recovery is a
supervisor operation: fence all old processes (for example with generation
identity plus process-liveness control), then discard or type-specifically
repair the complete mapping generation. Elapsed time alone is not evidence of
owner death.

## Tokens, fork, and raw ABIs

`CsnziToken` is neither `Copy` nor `Clone`; `depart(self)` consumes it. It has no
`Drop` implementation because cancellation, panic, and `fork` make implicit
ownership decisions unsafe. Losing a token intentionally leaks presence.

`fork` bypasses Rust's ownership model and physically duplicates stack values.
Fork before tokens exist, or designate exactly one process to consume every
inherited token while all others proceed directly to `exec` or `_exit` without
unwinding it.

`into_raw()` encodes a 16-bit leaf and 47-bit activation generation in `u64`.
`depart_raw` is unsafe because the scalar cannot carry instance identity or
linear ownership. The wrapping generation rejects many accidental stale
activations, but it is not an ABA proof: after 2^47 - 1 complete activations the
nonzero tag can repeat. Two valid same-leaf tokens in one activation also share a
generation. The caller must uphold exact instance and sole-consumption rules;
duplicating a raw token can consume another participant's count.

## Storage and lifetime

The object stores only atomics, integers, and padding. Tree edges are computed
from breadth-first integer indices, so different processes may map the same
bytes at different virtual addresses. `initialize_at` writes every field and
padding byte directly in final storage without moving the aggregate. The
`PodValue` and `PodSync` implementations bind native layout into the structural
fingerprint; `repr(C)` is not required.

`is_drained() == true` permits reclamation of the payload protected by the
admission protocol. It does not prove that no process retains `&Csnzi`, calls a
diagnostic method, or is returning from a non-final operation. The control
mapping must remain alive under an outer attachment/liveness protocol until all
possible callers are fenced. This distinction is what allows the scalable path
to avoid a global method-entry reservation.

As with the other primitives in this crate, process-shared use of Rust atomics
is an audited Linux/hardware deployment assumption rather than a complete Rust
abstract-machine proof. Loaders must also validate the structural fingerprint,
authenticate executable code, and reject unsupported 64-bit-atomic targets.

Run the process example with:

```console
cargo run --example csnzi
```

For a bounded shape comparison against raw `Snzi` and gate-based
`CloseableSnzi`, run:

```console
cargo run --release --example csnzi_comparison -- 8 100000
```

The harness runs hot-leaf and sharded topologies, verifies the exact completed
operation count and terminal state for every primitive, and emits one JSON line
per result plus environment metadata. Its elapsed time is evidence for that
host, build, and invocation only. It is not a cross-machine performance claim.

## Rust 1.85 freestanding evidence

`examples/csnzi.rs` is also the minimal freestanding closure used for codegen
validation. With `--cfg csnzi_freestanding` it is `no_std`, has no `main`, and
exports layout, direct initialization, entry, raw departure, close, query, and
drain functions. The following is the exact build shape tested on x86-64 (the
output directory may be changed freely):

```console
out=/tmp/shmem-pod-csnzi-codegen
mkdir -p "$out"
common=(
  -Copt-level=3 -Cpanic=abort -Crelocation-model=pic -Ccode-model=small
  -Ccodegen-units=1 -Coverflow-checks=no -Cdebug-assertions=no
  -Cforce-unwind-tables=no -Ctarget-cpu=x86-64 -Cembed-bitcode=no
)

rustc +1.85.0 src/lib.rs --crate-name shmem_pod --edition=2024 \
  --crate-type=rlib "${common[@]}" --emit link="$out/libshmem_pod.rlib"
rustc +1.85.0 examples/csnzi.rs --crate-name shmem_pod_csnzi_closure \
  --edition=2024 --crate-type=lib --cfg csnzi_freestanding \
  --extern shmem_pod="$out/libshmem_pod.rlib" "${common[@]}" \
  --emit obj="$out/csnzi.o"

sysroot=$(rustc +1.85.0 --print sysroot)
"$sysroot/lib/rustlib/x86_64-unknown-linux-gnu/bin/rust-lld" \
  -flavor gnu -static --gc-sections --no-undefined --emit-relocs \
  --build-id=none -z noexecstack --fatal-warnings -T poc/code/pod.ld \
  -o "$out/csnzi.elf" "$out/csnzi.o" "$out/libshmem_pod.rlib"

readelf -rW "$out/csnzi.o"
readelf -SW "$out/csnzi.elf"
readelf -lW "$out/csnzi.elf"
readelf -Ws "$out/csnzi.elf"
nm -a "$out/csnzi.elf"
objdump -dC "$out/csnzi.elf"
```

The audited run used `rustc 1.85.0 (4d91de4e4 2025-02-17)` and produced:

- input relocations: 45 `R_X86_64_PC32`, 7 `R_X86_64_PLT32`, and no absolute
  `R_X86_64_64` relocation;
- one 5,676-byte allocated `.pod` section at VMA zero in a read/execute-only
  `PT_LOAD`, plus a non-executable `GNU_STACK`;
- no undefined global symbol and no `memcpy`, `memset`, allocator, pthread,
  futex, or other host-runtime symbol;
- all eight expected `shmem_pod_*` exports; and
- no call instruction in `shmem_pod_init`; Rust 1.85 emitted direct scalar and
  volatile byte stores for the 1,408-byte, 64-byte-aligned state object.

As an execution smoke test, the `.pod` bytes were copied into an anonymous RX
mapping while state remained in a separate RW mapping. Calling the exported
function offsets performed `init -> enter twice on one leaf -> query -> close ->
depart twice -> drained`, rejected a post-close entry, and printed:

```text
PASS freestanding RX closure bytes=5676 state=1408 align=64 overlap_rejected=true unaligned_output=true
```

The smoke runner also passed a deliberately unaligned token output pointer and
verified that an output pointer overlapping the live state object is rejected
before admission. The code mapping was writable only while copied, then changed
to read/execute before any function call; state remained in a separate
read/write, non-executable mapping.

This audit caught and removed a Rust 1.85 bounds-check edge from recursive node
indexing. Internal node access now uses one documented pointer helper after the
public leaf check; every computed parent index is strictly smaller than an
already validated child index.
