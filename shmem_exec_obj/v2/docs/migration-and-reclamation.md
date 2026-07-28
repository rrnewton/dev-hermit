# Migration and reclamation

`shmem-pod` upgrades state by replacing a complete generation. It never
rewrites a live object in place and never treats a timeout or dead owner as
permission to continue a partly completed write.

The protocol is intended for an authenticated supervisor-owned control mapping
plus two payload generations on coherent shared memory:

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
non-overlapping regions. A `GenerationIdentity` binds four facts: the exact
schema, an allocator region namespace, a supervisor-persisted monotonic
sequence, and an authenticated 256-bit backing identity. For non-overlapping
regions in one enclosing object, that identity must bind object identity plus
region offset, extent, and generation; reusing one whole-object digest for both
regions is invalid. `region_id` alone is not freshness evidence and may be
reused by operating systems or application configuration. The library checks
that source and target region IDs and backing identities differ and that the
target sequence increases, but only the supervisor can keep the sequence
monotonic across control-record replacement and restart.

The supervisor must also authenticate one unique, persistent control identity
for each source generation. In the current protocol that identity is represented
operationally by the one live/recoverable `MigrationControl` and its unique
`transaction_id`; the library can check the complete plan but cannot detect an
exact plan copied into another control record. Competing controls or plans for
one source are forbidden. A replicated-control design must first add a stronger
persistent control identity to the plan, permit, bootstrap authentication, and
recovery procedure.

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

## Bind capabilities and execute one upgrade

1. Stop new work and prove source quiescence. Use either a
   `CloseableSnzi` which reached terminal drain, or consume a typed `Mapping`
   owner through `Draining::try_close`. Copy the drained source into
   process-local staging before consuming its final access authority. A typed
   mapping cannot expose its payload after close. For an externally gated
   allocator or object graph, move its region/root authority into
   `AdmissionQuiescence::bind_with_authority`. Its type must implement unsafe
   `FailClosedSourceAuthority`: it must be owned and `'static`, safe to leak
   forever, free of borrowed or duplicate regain paths, and have no destructor
   which releases, reopens, unmaps, or reclaims source state. Both paths trade
   streaming ergonomics for a type-level guarantee that safe mutation cannot
   resume during migration.
2. Allocate a new target generation. Initialize its `RelocAllocator` with a
   new region namespace and a sequence minted by the supervisor's durable
   monotonic counter. Compute/authenticate a digest for the actual backing
   object, not its reusable file-descriptor number.
3. Before migration, make the recovery supervisor own an independently live,
   authenticated target backing handle. Clients must not know an attach route.
   Name that supervisor with `AuthorityIdentity`.
4. Construct `GenerationIdentity` values and a `MigrationPlan`. Authenticate its
   transaction as the unique live and recoverable control for this source.
   Unsafely bind the complete plan and the remaining safe source authority to
   `AdmissionQuiescence`, or consume a `ClosedMapping` into
   `MappingQuiescence`. This unsafe boundary is where the host attests that the
   barrier/mapping really guards the named backing and that no duplicate control
   or competing plan exists.
5. Call `begin_with_quiescent_source`. It consumes the witness and rejects a
   transaction, source, target, or recovery-authority mismatch before claiming
   the control. A witness bound to one plan cannot be safely retargeted even when
   another plan names the same source generation.
6. Transform the process-local staging copy and populate the target. Resolve
   every root and validate every descriptor.
7. Move a host type implementing unsafe `PrecommitTargetBacking` into
   `mark_target_ready`. Its generation and recovery authority must match the
   plan and `is_private` must remain true while the library owns it. The backing
   value must structurally own every safe target mutation and publication
   authority; do not leave a `RelocRegion`, typed mapping `Owner`, callback, or
   child writer outside it. The unsafe call also attests that all target writes
   happen-before its release CAS and no raw mutable alias survives validation.
8. Call `TargetReadyMigration::commit`. The `TargetReady -> Committed`
   compare-exchange is the route switch. The returned `CommittedMigration`
   still owns both the terminal source proof and target authority. Only now may
   the host extract and publish target attachment material. Attach code must
   acquire-load `authoritative_generation` rather than cache an earlier route.
9. Pass `committed.source()` to `authorize_reclamation`. It rechecks the complete
   plan binding and terminal state before recording `Reclaimed`. After
   splitting `CommittedMigration`, pass that permit to
   `AdmissionQuiescence::into_authority`; this consumes the witness before
   returning the source handles. A permit for any other transaction, source,
   target, or recovery authority returns `SourcePlanMismatch` together with the
   intact witness, so the caller may retry with the exactly matching permit.
   Only now explicitly destroy source allocations or discard the complete source
   region.

The target must not admit callers until commit succeeds. Safe source access
cannot reopen after the consumed terminal witness, and owned source handles are
not returned until the reclamation permit is consumed. This means the generic
protocol has a bounded cutover interval; applications that need concurrent
snapshot construction must provide their own transactional snapshot or
copy-on-write consistency scheme before entering this protocol.

Every failure path is deliberately asymmetric. Dropping `Migration`,
`TargetReadyMigration`, `CommittedMigration`, or a returned error never runs the
source authority's destructor; the authority and source backing leak closed.
Only exact full-plan permit extraction restores the owned value. Conversely,
`PrecommitTargetBacking::drop` must never publish or mutate a private target,
destroy an authoritative target, or invalidate the supervisor's independent
recovery handle. Target publication, discard, and reclamation are explicit host
operations rather than destructor side effects.

For a rolling executable deployment, first deploy code which can negotiate both
the old exact schema and the new exact schema. Stop every old-generation writer,
drain the old generation, migrate, commit, and only then route new attachments
to the target. An old executable which does not recognize the new fingerprint
must reject it. The protocol does not let an old writer continue mutating the
source while a generic migration callback reads it.

## Shared phases and process-crash cuts

`MigrationControl` is pointer-free and uses release/acquire publication. It can
be mapped at a different virtual address in every process. It is deliberately
one-shot to avoid ABA and ambiguous recovery. These phases cover abrupt process
termination only while a supervisor still owns the backing objects.

One-shot applies to both the record and its externally authenticated identity.
Do not copy the same plan into another live or recoverable record: exact plan
comparison cannot distinguish those controls, and cleanup recovery would no
longer know which `Reclaimed` transition fenced the source.

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

Dropping a live `Migration` or `TargetReadyMigration` before commit poisons it.
`_exit` or `SIGKILL` skips `Drop` and leaves `Initializing`, `Copying`, or
`TargetReady`. No participant steals that transaction. A supervisor should use
an external liveness authority such as `pidfd`, stop all affected participants,
poison the record, and discard the private target. If the migrator dies after
commit but before publishing its local descriptor, the independently live
recovery-authority handle named in the plan remains available.

The process tests exercise both sides of that boundary. A child owns and
release-publishes the only builder mapping while the parent retains a distinct
mapping of the same memfd. The parent observes the migration phase CAS directly,
without a later helper atomic publication. Killing the child at `TargetReady`
leaves the source authoritative and the private target discardable; killing it
immediately after `Committed` leaves the target authoritative and readable
through the parent's recovery mapping.

The metadata checksum catches accidental or torn changes after publication; it
is not an authenticity mechanism. Authenticate the executable image, unique
control object and transaction, bootstrap message, file descriptors, identities,
and lengths before mapping or typed access.

These atomics specify visibility between processes on coherent shared memory.
The protocol does not cover machine crash, reboot, power loss, torn storage,
filesystem durability, or persistent-memory cache flushing. Recovery claims in
this document mean process recovery while the authenticated control and backing
objects remain live. Durable storage needs a separate audited protocol.

## Reclaiming allocator objects and regions

`SharedBox` and `SharedVec` intentionally have no destructor. Their explicit
unsafe destruction methods require global exclusion because another process
may hold a copied descriptor or a resolved reference.

After a migration commits:

1. obtain `ReclamationPermit` from the unique control using the exact consumed,
   full-plan-bound `QuiescenceWitness`;
2. ensure every raw/inherited mapping capability and nonconforming guest is
   gone;
3. either call explicit collection destruction under exclusive access, or
   discard the complete source region;
4. advance the supervisor's persisted generation sequence before any namespace
   reuse, and never authenticate an old backing digest as the new generation.

Whole-generation discard is the preferred crash recovery policy. It requires
no walk and no Rust `Drop` calls because `PodValue` transitively forbids
destructors. If a cleanup process dies after the shared `Reclaimed`
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

- Authenticate complete `GenerationIdentity` and `MigrationPlan` values outside
  attacker-writable mapped data. Persist one unique control/transaction identity
  per source; never create competing or duplicate controls for it.
- Persist and advance the supervisor generation sequence; do not call a region
  ID alone globally unique.
- Stage source data, then bind the complete authenticated plan while consuming
  both a terminal witness and every remaining safe source access authority before
  `begin_with_quiescent_source`; authority types must uphold
  `FailClosedSourceAuthority` and explicit cleanup.
- Give the recovery authority an authenticated duplicate target handle before
  commit, and keep every client attach route undiscoverable.
- Treat `mark_target_ready` as an unsafe exact-generation, happens-before,
  no-interior-writers, and root-validation boundary; the backing value must own
  the target's safe mutation and publication authorities through commit, and
  its destructor must have no publication or reclamation effects.
- Route new attachments by the control record's acquire load.
- On owner death, prove death externally; never steal a live transaction.
- Reclaim only after commit and an exact full-plan permit/witness match from the
  unique control record.
- Prefer leaking and whole-generation replacement over uncertain repair.
- Do not interpret process-crash recovery as machine/power durability.
