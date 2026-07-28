# Migration and reclamation

`shmem-pod` upgrades state by replacing a complete generation. It never
rewrites a live object in place and never treats a timeout or dead owner as
permission to continue a partly completed write.

The protocol is intended for an authenticated supervisor-owned control mapping
plus two payload generations:

```text
source generation -- close/drain --> immutable source
                                      |
                                      +-- copy/transform --> private target
                                                            |
                                      atomic commit <--------+
                                             |
source generation -- reclamation fence ------+
```

The source and target can be different memfds, shared-memory objects, or
non-overlapping regions. Their region IDs must be nonzero and different. Do not
reuse a region ID while any stale `AllocationDescriptor` may exist.

## Define and negotiate schemas

A schema combines an application version with the exact `PodValue` structural
fingerprint:

```rust
use shmem_pod::migration::{
    MigrationSchema, SchemaIdentity, SchemaNegotiation, negotiate_schema,
};

const OLD_EXACT_FINGERPRINT: u128 = 0x1234;

#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct StateV2 {
    requests: core::sync::atomic::AtomicU64,
    failures: core::sync::atomic::AtomicU64,
}

impl MigrationSchema for StateV2 {
    const VERSION: u64 = 2;
}

let found = SchemaIdentity::new(1, OLD_EXACT_FINGERPRINT);
let decision = negotiate_schema::<StateV2>(found, &[found])?;
assert!(matches!(decision, SchemaNegotiation::UpgradeRequired { .. }));
# Ok::<(), shmem_pod::migration::MigrationError>(())
```

Matching only a version is not enough. The accepted source list contains exact
version/fingerprint pairs, so accidentally reusing version 1 for a different
Rust layout is rejected before typed access. Accepted sources must also be
older than the compiled target; negotiation does not silently downgrade a
future schema. `repr(C)` is not required: native Rust layout is supported when
all participants use the authenticated exact fingerprint and compatible target
metadata.

## Execute one upgrade

1. Stop new work and prove source quiescence. Use either a
   `CloseableSnzi` which reached terminal drain, or the typed `Mapping` owner in
   its drained `Draining` state.
2. Allocate a new target generation. Initialize its `RelocAllocator` with a
   fresh region ID. Keep its bootstrap handle private.
3. Create a validated `MigrationPlan` with `for_schemas::<Old, New>` when both
   types are compiled into the migrator (or `new` for runtime identities), then call
   `begin_after_admission_drain` or `begin_after_mapping_drain`.
4. Read the now-immutable source, transform it, and populate the target. Resolve
   every root and validate every target descriptor.
5. Call `mark_target_ready`. This method is unsafe because the library cannot
   discover application roots or prove that an application callback finished
   writing them.
6. Call `commit`. The `TargetReady -> Committed` compare-exchange is the route
   switch. Attach code must load `authoritative_generation` from the
   authenticated control mapping rather than cache an earlier route.
7. Close the typed source mapping or recheck its terminal admission barrier,
   then authorize reclamation. Only now explicitly destroy source allocations
   or discard the complete source region.

The target must not admit callers until commit succeeds. The source cannot
reopen after the initial drain. This means the generic protocol has a bounded
cutover interval; applications that need concurrent snapshot construction must
provide their own transactional snapshot or copy-on-write consistency scheme
before entering this protocol.

For a rolling executable deployment, first deploy code which can negotiate both
the old exact schema and the new exact schema. Stop every old-generation writer,
drain the old generation, migrate, commit, and only then route new attachments
to the target. An old executable which does not recognize the new fingerprint
must reject it. The protocol does not let an old writer continue mutating the
source while a generic migration callback reads it.

## Persistent phases and crash cuts

`MigrationControl` is pointer-free and uses release/acquire publication. It can
be mapped at a different virtual address in every process. It is deliberately
one-shot to avoid ABA and ambiguous recovery.

| Phase | Authoritative generation | Recovery action |
| --- | --- | --- |
| `Uninitialized` | external bootstrap | A drained source may claim it. |
| `Initializing` | none from this record | Owner died while publishing metadata. Poison and replace the control and target. |
| `Copying` | source | Discard the incomplete target. Do not resume application writes. |
| `TargetReady` | source | Discard the private target unless the original live owner commits it. |
| `Committed` | target | Never reopen source. Establish the reclamation fence. |
| `Reclaimed` | target | Reclamation was fenced; idempotently finish cleanup after a cleanup-process crash. |
| `Poisoned` | none from this record | Stop participants and replace the failed generation/control record. |

“Source is authoritative” in `Copying` and `TargetReady` is a recovery choice,
not permission to reopen its one-shot admission gate. After a failed migration,
the supervisor can copy the still-authoritative immutable source into another
fresh service generation, or take an application-specific recovery path.

Dropping a live `Migration` before commit poisons it. `_exit`, `SIGKILL`, or a
machine crash skips `Drop` and leaves `Initializing`, `Copying`, or
`TargetReady`. No participant steals that transaction. A supervisor should use
an external liveness authority such as `pidfd`, stop all affected participants,
poison the record, and discard the private target.

The metadata checksum catches accidental or torn changes after publication; it
is not an authenticity mechanism. Authenticate the executable image, control
object, bootstrap message, file descriptors, identities, and lengths before
mapping or typed access.

These atomics specify visibility between processes on coherent shared memory.
They do not flush persistent-memory cache lines and do not make a disk-backed
file crash-durable. A persistent-memory deployment needs a separately audited
flush, ordering, and recovery protocol.

## Reclaiming allocator objects and regions

`SharedBox` and `SharedVec` intentionally have no destructor. Their explicit
unsafe destruction methods require global exclusion because another process
may hold a copied descriptor or a resolved reference.

After a migration commits:

1. obtain `ReclamationPermit` using the exact source barrier or
   `ClosedMapping` proof;
2. ensure every raw/inherited mapping capability and nonconforming guest is
   gone;
3. either call explicit collection destruction under exclusive access, or
   discard the complete source region;
4. never publish its old region ID again.

Whole-generation discard is the preferred crash recovery policy. It requires
no walk and no Rust `Drop` calls because `PodValue` transitively forbids
destructors. If a cleanup process dies after the persistent `Reclaimed`
transition but before `munmap`, `ftruncate`, or unlink, the old bytes are leaked
rather than reused. A replacement cleanup process sees `Reclaimed`, calls the
unsafe `resume_reclamation` after authenticating the control record and
excluding source access, and finishes the idempotent host cleanup.

If a process dies during an individual `SharedBox::destroy` or
`SharedVec::destroy`, do not infer which allocator metadata stores completed.
The allocator fails closed; discard the whole retired generation instead of
resuming slot reuse.

`ReclamationPermit` does not make `munmap` safe by itself. It cannot observe raw
pointers hidden in foreign code, an inherited mapping after `fork`, or a guest
which bypasses admission. Those are host/bootstrap obligations.

## Why there is no tracing collector

A tracing collector is unsound with the library's current contract. In
particular:

- roots are application-defined and `PodValue` integers may semantically carry
  copied descriptors, so the allocator cannot enumerate roots;
- executable pod methods have no mandatory insertion or deletion write
  barrier;
- an arbitrary injected process can stop or die between publishing a pointer
  and updating collector metadata;
- allocator slots do not contain a trusted type map and trace function;
- the library has no stop-the-world handshake covering raw pointers, foreign
  code, and children created by `fork`;
- tracing alone does not make target publication or free-list mutation
  crash-atomic.

Conservative scanning does not repair these omissions. Integer fields can look
like offsets, processes can map the same object at different addresses, and a
false negative would free live shared state. False positives would also prevent
bounded reclamation indefinitely.

Implement tracing only after the public model has all of the following:

1. a closed, enumerable root schema;
2. typed trace metadata authenticated with the exact build;
3. enforced mutation barriers in every writer, including injected code;
4. a process-wide safepoint or a proven concurrent collector protocol;
5. crash-consistent mark state and allocation/free transactions;
6. a recovery proof for a process dying at every barrier and collector step.

Until then, use explicit ownership for individual objects and close/drain plus
fresh generation IDs for bulk reclamation. This is analogous to grace-period
reclamation: old storage is reused only after every possible reader is excluded,
not after a timer expires.

## Operational checklist

- Authenticate schema identities and `MigrationPlan` outside the mapped data.
- Close and drain the exact source generation before `begin`.
- Use a fresh target memfd/region ID and keep it undiscoverable before commit.
- Treat `mark_target_ready` as an unsafe root-validation boundary.
- Route new attachments by the control record's acquire load.
- On owner death, prove death externally; never steal a live transaction.
- Reclaim only after commit and a matching terminal quiescence proof.
- Prefer leaking and whole-generation replacement over uncertain repair.
