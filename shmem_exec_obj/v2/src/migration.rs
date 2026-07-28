//! Process-crash-stop schema migration and generation reclamation.
//!
//! This module deliberately implements generation replacement rather than
//! in-place mutation. A source generation is closed and drained, a fresh target
//! generation is populated and checked, and one atomic state transition makes
//! the target authoritative. The source can be reclaimed only after that
//! transition and a second quiescence proof.
//!
//! [`MigrationControl`] contains no pointers and may be mapped at different
//! virtual addresses. It is a one-shot control record: a failed or interrupted
//! transaction is poisoned and the control record and incomplete target must be
//! replaced. This prevents an implementation from guessing whether a process
//! died before or after its last non-atomic payload write.
//!
//! The model requires coherent shared memory whose backing survives the
//! participating process failure being recovered. It makes no machine-crash,
//! reboot, filesystem-durability, or power-loss guarantee.

use core::fmt;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::sync::atomic::{AtomicU64, Ordering};

use crate::admission::CloseableSnzi;
use crate::mapping::ClosedMapping;
use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const UNINITIALIZED: u64 = 0;
const INITIALIZING: u64 = 1;
const COPYING: u64 = 2;
const TARGET_READY: u64 = 3;
const COMMITTED: u64 = 4;
const RECLAIMED: u64 = 5;
const POISONED: u64 = 6;

/// Application schema identity used during attachment and upgrade negotiation.
///
/// The version is application-defined. The fingerprint is the exact structural
/// fingerprint of the Rust type, so reusing a version for a different compiled
/// layout is rejected.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct SchemaIdentity {
    version: u64,
    fingerprint_low: u64,
    fingerprint_high: u64,
}

impl SchemaIdentity {
    /// Creates an identity from an application version and layout fingerprint.
    pub const fn new(version: u64, fingerprint: u128) -> Self {
        Self {
            version,
            fingerprint_low: fingerprint as u64,
            fingerprint_high: (fingerprint >> 64) as u64,
        }
    }

    /// Returns the identity for a compiled shared schema.
    pub const fn of<T: MigrationSchema>() -> Self {
        Self::new(T::VERSION, T::FINGERPRINT)
    }

    /// Returns the application schema version.
    pub const fn version(self) -> u64 {
        self.version
    }

    /// Returns the exact structural layout fingerprint.
    pub const fn fingerprint(self) -> u128 {
        ((self.fingerprint_high as u128) << 64) | self.fingerprint_low as u128
    }

    fn validate(self) -> Result<(), MigrationError> {
        if self.version == 0 {
            Err(MigrationError::ZeroSchemaVersion)
        } else {
            Ok(())
        }
    }
}

// SAFETY: all fields are fixed-width integers and the complete layout is bound
// into the fingerprint below.
unsafe impl FixedAddressPodValue for SchemaIdentity {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "schema identities must not need drop"
        );
        let mut state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-schema-identity-v1");
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = mix_field(
            state,
            b"version",
            offset_of!(Self, version),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"fingerprint_low",
            offset_of!(Self, fingerprint_low),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"fingerprint_high",
            offset_of!(Self, fingerprint_high),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        __private::finish(state)
    };
}

// SAFETY: SchemaIdentity contains no address.
unsafe impl PodValue for SchemaIdentity {}
// SAFETY: immutable scalar fields support process-shared references.
unsafe impl PodSync for SchemaIdentity {}

/// Caller-authenticated digest of one backing object.
///
/// The library does not compute this digest. A host should derive it from its
/// authenticated bootstrap metadata and backing-object identity, not from a
/// reusable file-descriptor number. All-zero bytes are reserved as invalid.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct BackingIdentity([u64; 4]);

impl BackingIdentity {
    /// Creates an identity from a caller-computed 256-bit digest.
    #[must_use]
    pub const fn new(bytes: [u8; 32]) -> Self {
        Self([
            u64::from_le_bytes([
                bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            ]),
            u64::from_le_bytes([
                bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14],
                bytes[15],
            ]),
            u64::from_le_bytes([
                bytes[16], bytes[17], bytes[18], bytes[19], bytes[20], bytes[21], bytes[22],
                bytes[23],
            ]),
            u64::from_le_bytes([
                bytes[24], bytes[25], bytes[26], bytes[27], bytes[28], bytes[29], bytes[30],
                bytes[31],
            ]),
        ])
    }

    /// Returns the digest bytes.
    #[must_use]
    pub const fn to_bytes(self) -> [u8; 32] {
        let a = self.0[0].to_le_bytes();
        let b = self.0[1].to_le_bytes();
        let c = self.0[2].to_le_bytes();
        let d = self.0[3].to_le_bytes();
        [
            a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], b[0], b[1], b[2], b[3], b[4], b[5],
            b[6], b[7], c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7], d[0], d[1], d[2], d[3],
            d[4], d[5], d[6], d[7],
        ]
    }

    const fn is_zero(self) -> bool {
        self.0[0] == 0 && self.0[1] == 0 && self.0[2] == 0 && self.0[3] == 0
    }
}

// SAFETY: the value contains four address-independent integer words.
unsafe impl FixedAddressPodValue for BackingIdentity {
    const FINGERPRINT: u128 = {
        let mut state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-backing-identity-v1",
        );
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = __private::mix_u128(state, <[u64; 4]>::FINGERPRINT);
        __private::finish(state)
    };
}

// SAFETY: BackingIdentity contains no address.
unsafe impl PodValue for BackingIdentity {}
// SAFETY: immutable integer words support process-shared references.
unsafe impl PodSync for BackingIdentity {}

/// Authenticated identity of the supervisor authority which retains the target.
///
/// This names the authority that has an independently live target backing
/// handle before commit, so recovery does not depend on the migrator surviving.
/// All-zero bytes are reserved as invalid.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct AuthorityIdentity([u64; 2]);

impl AuthorityIdentity {
    /// Creates an identity from authenticated caller-provided bytes.
    #[must_use]
    pub const fn new(bytes: [u8; 16]) -> Self {
        Self([
            u64::from_le_bytes([
                bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            ]),
            u64::from_le_bytes([
                bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14],
                bytes[15],
            ]),
        ])
    }

    /// Returns the identity bytes.
    #[must_use]
    pub const fn to_bytes(self) -> [u8; 16] {
        let a = self.0[0].to_le_bytes();
        let b = self.0[1].to_le_bytes();
        [
            a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], b[0], b[1], b[2], b[3], b[4], b[5],
            b[6], b[7],
        ]
    }

    const fn is_zero(self) -> bool {
        self.0[0] == 0 && self.0[1] == 0
    }
}

// SAFETY: the value contains two address-independent integer words.
unsafe impl FixedAddressPodValue for AuthorityIdentity {
    const FINGERPRINT: u128 = {
        let mut state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-authority-identity-v1",
        );
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = __private::mix_u128(state, <[u64; 2]>::FINGERPRINT);
        __private::finish(state)
    };
}

// SAFETY: AuthorityIdentity contains no address.
unsafe impl PodValue for AuthorityIdentity {}
// SAFETY: immutable integer words support process-shared references.
unsafe impl PodSync for AuthorityIdentity {}

/// Marks an exact shared-memory type as an application migration schema.
///
/// The version must be nonzero and must increase for each semantic schema
/// upgrade. The inherited [`FixedAddressPodValue::FINGERPRINT`] binds the exact
/// compiled Rust layout, including native Rust layout when `repr(C)` is absent.
pub trait MigrationSchema: PodValue + PodSync {
    /// Monotonically increasing application schema version.
    const VERSION: u64;
}

/// Result of negotiating an observed schema with this executable.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SchemaNegotiation {
    /// The observed schema exactly matches the current compiled schema.
    Exact,
    /// The observed schema is an explicitly accepted migration source.
    UpgradeRequired {
        /// Exact source identity found in shared storage.
        source: SchemaIdentity,
        /// Exact target identity compiled into this executable.
        target: SchemaIdentity,
    },
}

/// Performs exact schema negotiation without allocation.
///
/// Versions are not wildcards: both version and structural fingerprint must
/// match either the current schema or one entry in `accepted_sources`.
pub fn negotiate_schema<T: MigrationSchema>(
    observed: SchemaIdentity,
    accepted_sources: &[SchemaIdentity],
) -> Result<SchemaNegotiation, MigrationError> {
    observed.validate()?;
    let current = SchemaIdentity::of::<T>();
    current.validate()?;
    if observed == current {
        return Ok(SchemaNegotiation::Exact);
    }
    if observed.version < current.version && accepted_sources.contains(&observed) {
        return Ok(SchemaNegotiation::UpgradeRequired {
            source: observed,
            target: current,
        });
    }
    Err(MigrationError::UnsupportedSchema { observed, current })
}

/// Exact identity of one schema-bearing allocator or mapping generation.
///
/// `sequence` is supplied by a supervisor-owned monotonic counter. The crate
/// validates ordering within a plan, but cannot persist that counter or prove
/// global non-reuse after a supervisor restart. `backing` must identify the
/// authenticated backing object rather than a reusable descriptor number.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct GenerationIdentity {
    schema: SchemaIdentity,
    region_id: u64,
    sequence: u64,
    backing: BackingIdentity,
}

impl GenerationIdentity {
    /// Constructs an identity for a compiled schema.
    #[must_use]
    pub const fn for_schema<T: MigrationSchema>(
        region_id: u64,
        sequence: u64,
        backing: BackingIdentity,
    ) -> Self {
        Self::new(SchemaIdentity::of::<T>(), region_id, sequence, backing)
    }

    /// Constructs an identity from runtime-authenticated metadata.
    #[must_use]
    pub const fn new(
        schema: SchemaIdentity,
        region_id: u64,
        sequence: u64,
        backing: BackingIdentity,
    ) -> Self {
        Self {
            schema,
            region_id,
            sequence,
            backing,
        }
    }

    /// Returns the exact schema identity.
    #[must_use]
    pub const fn schema(self) -> SchemaIdentity {
        self.schema
    }

    /// Returns the allocator namespace discriminator.
    ///
    /// This value alone is not evidence of global freshness; use
    /// [`Self::sequence`] and [`Self::backing`] as well.
    #[must_use]
    pub const fn region_id(self) -> u64 {
        self.region_id
    }

    /// Returns the caller-persisted monotonic generation sequence.
    #[must_use]
    pub const fn sequence(self) -> u64 {
        self.sequence
    }

    /// Returns the authenticated backing-object digest.
    #[must_use]
    pub const fn backing(self) -> BackingIdentity {
        self.backing
    }

    fn validate(self) -> Result<(), MigrationError> {
        self.schema.validate()?;
        if self.region_id == 0 {
            return Err(MigrationError::ZeroRegionId);
        }
        if self.sequence == 0 {
            return Err(MigrationError::ZeroGenerationSequence);
        }
        if self.backing.is_zero() {
            return Err(MigrationError::ZeroBackingIdentity);
        }
        Ok(())
    }
}

// SAFETY: every field is an address-independent PodValue.
unsafe impl FixedAddressPodValue for GenerationIdentity {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "generation identities must not need drop"
        );
        let mut state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-generation-identity-v1",
        );
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = mix_field(
            state,
            b"schema",
            offset_of!(Self, schema),
            size_of::<SchemaIdentity>(),
            align_of::<SchemaIdentity>(),
            SchemaIdentity::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"region_id",
            offset_of!(Self, region_id),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"sequence",
            offset_of!(Self, sequence),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"backing",
            offset_of!(Self, backing),
            size_of::<BackingIdentity>(),
            align_of::<BackingIdentity>(),
            BackingIdentity::FINGERPRINT,
        );
        __private::finish(state)
    };
}

// SAFETY: GenerationIdentity stores no address.
unsafe impl PodValue for GenerationIdentity {}
// SAFETY: GenerationIdentity exposes immutable scalar fields only.
unsafe impl PodSync for GenerationIdentity {}

/// Immutable description of one source-to-target generation replacement.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationPlan {
    transaction_id: u64,
    source: GenerationIdentity,
    target: GenerationIdentity,
    target_authority: AuthorityIdentity,
}

impl MigrationPlan {
    /// Constructs and validates a migration plan.
    pub fn new(
        transaction_id: u64,
        source: GenerationIdentity,
        target: GenerationIdentity,
        target_authority: AuthorityIdentity,
    ) -> Result<Self, MigrationError> {
        let plan = Self {
            transaction_id,
            source,
            target,
            target_authority,
        };
        plan.validate()?;
        Ok(plan)
    }

    /// Returns the transaction identity.
    pub const fn transaction_id(self) -> u64 {
        self.transaction_id
    }

    /// Returns the exact source generation.
    pub const fn source(self) -> GenerationIdentity {
        self.source
    }

    /// Returns the exact target generation.
    pub const fn target(self) -> GenerationIdentity {
        self.target
    }

    /// Returns the authenticated recovery/publication authority.
    pub const fn target_authority(self) -> AuthorityIdentity {
        self.target_authority
    }

    /// Returns the source allocator namespace discriminator.
    pub const fn source_region_id(self) -> u64 {
        self.source.region_id
    }

    /// Returns the target allocator namespace discriminator.
    pub const fn target_region_id(self) -> u64 {
        self.target.region_id
    }

    fn validate(self) -> Result<(), MigrationError> {
        self.source.validate()?;
        self.target.validate()?;
        if self.transaction_id == 0 {
            return Err(MigrationError::ZeroTransactionId);
        }
        if self.target_authority.is_zero() {
            return Err(MigrationError::ZeroAuthorityIdentity);
        }
        if self.source.region_id == self.target.region_id {
            return Err(MigrationError::ReusedRegionId);
        }
        if self.source.backing == self.target.backing {
            return Err(MigrationError::ReusedBackingIdentity);
        }
        if self.target.sequence <= self.source.sequence {
            return Err(MigrationError::GenerationDidNotIncrease {
                source: self.source.sequence,
                target: self.target.sequence,
            });
        }
        if self.target.schema.version <= self.source.schema.version {
            return Err(MigrationError::VersionDidNotIncrease {
                source: self.source.schema.version,
                target: self.target.schema.version,
            });
        }
        Ok(())
    }
}

// SAFETY: MigrationPlan recursively consists only of address-independent
// integers and identity values.
unsafe impl FixedAddressPodValue for MigrationPlan {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "migration plans must not need drop");
        let mut state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-migration-plan-v2");
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = mix_field(
            state,
            b"transaction_id",
            offset_of!(Self, transaction_id),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"source",
            offset_of!(Self, source),
            size_of::<GenerationIdentity>(),
            align_of::<GenerationIdentity>(),
            GenerationIdentity::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"target",
            offset_of!(Self, target),
            size_of::<GenerationIdentity>(),
            align_of::<GenerationIdentity>(),
            GenerationIdentity::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"target_authority",
            offset_of!(Self, target_authority),
            size_of::<AuthorityIdentity>(),
            align_of::<AuthorityIdentity>(),
            AuthorityIdentity::FINGERPRINT,
        );
        __private::finish(state)
    };
}

// SAFETY: MigrationPlan stores no address.
unsafe impl PodValue for MigrationPlan {}
// SAFETY: MigrationPlan exposes immutable scalar fields only.
unsafe impl PodSync for MigrationPlan {}

mod quiescence_sealed {
    use super::GenerationIdentity;

    pub trait Sealed {
        fn generation(&self) -> GenerationIdentity;
        fn still_quiescent(&self) -> bool;
    }
}

/// Sealed terminal-quiescence capability accepted by migration operations.
///
/// Callers cannot implement this trait. Obtain a witness by binding an
/// authenticated generation identity to a terminal [`CloseableSnzi`] or by
/// consuming a typed [`ClosedMapping`].
pub trait QuiescenceWitness: quiescence_sealed::Sealed {}

/// Terminal source witness backed by a one-shot closeable admission gate.
///
/// The witness borrows the gate for its lifetime. Once constructed, safe gate
/// operations cannot admit another participant because `DRAINED` is terminal.
pub struct AdmissionQuiescence<'source, const NODES: usize, Authority = ()> {
    source: &'source CloseableSnzi<NODES>,
    generation: GenerationIdentity,
    authority: Authority,
}

impl<'source, const NODES: usize> AdmissionQuiescence<'source, NODES> {
    /// Binds a terminal gate to the exact authenticated source generation.
    ///
    /// # Safety
    ///
    /// `generation` must identify the schema, allocator namespace, monotonic
    /// generation, and backing object guarded by `source`. Every conforming
    /// source access path must pass through this gate. The caller must have
    /// authenticated this association outside attacker-writable shared bytes.
    pub unsafe fn bind(
        source: &'source CloseableSnzi<NODES>,
        generation: GenerationIdentity,
    ) -> Result<Self, MigrationError> {
        generation.validate()?;
        if !source.is_drained() {
            return Err(MigrationError::SourceNotDrained);
        }
        Ok(Self {
            source,
            generation,
            authority: (),
        })
    }
}

impl<'source, const NODES: usize, Authority>
    AdmissionQuiescence<'source, NODES, Authority>
{
    /// Binds terminal admission while consuming the source access authority.
    ///
    /// The authority remains owned by the migration transaction through commit.
    /// This is useful when an otherwise safe source handle could mutate allocator
    /// metadata or payload bytes without taking an admission token. Stage the
    /// immutable data needed for migration before calling this method.
    ///
    /// # Safety
    ///
    /// `generation` must identify the schema, allocator namespace, monotonic
    /// generation, and backing object guarded by `source`. `authority` must own
    /// every remaining safe source access path which is not intrinsically tied
    /// to an admission token. The caller must authenticate both associations
    /// outside attacker-writable shared bytes and exclude raw or nonconforming
    /// access.
    pub unsafe fn bind_with_authority(
        source: &'source CloseableSnzi<NODES>,
        generation: GenerationIdentity,
        authority: Authority,
    ) -> Result<Self, MigrationError> {
        generation.validate()?;
        if !source.is_drained() {
            return Err(MigrationError::SourceNotDrained);
        }
        Ok(Self {
            source,
            generation,
            authority,
        })
    }

    /// Returns the bound source generation identity.
    #[must_use]
    pub const fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    /// Returns the consumed source authority after reclamation was fenced.
    ///
    /// During migration the witness is owned by the transaction. After commit,
    /// the caller must first obtain a matching [`ReclamationPermit`]. Consuming
    /// the witness here prevents retaining quiescence evidence while regaining
    /// the source mutation authority.
    pub fn into_authority(
        self,
        permit: ReclamationPermit,
    ) -> Result<Authority, MigrationError> {
        if permit.plan.source != self.generation {
            return Err(MigrationError::SourceGenerationMismatch {
                expected_tag: generation_tag(self.generation),
                observed_tag: generation_tag(permit.plan.source),
            });
        }
        Ok(self.authority)
    }
}

impl<const NODES: usize, Authority> quiescence_sealed::Sealed
    for AdmissionQuiescence<'_, NODES, Authority>
{
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn still_quiescent(&self) -> bool {
        self.source.is_drained()
    }
}

impl<const NODES: usize, Authority> QuiescenceWitness
    for AdmissionQuiescence<'_, NODES, Authority>
{
}

/// Terminal source witness which owns a typed mapping's close capability.
///
/// Construction consumes [`ClosedMapping`], so no safe typed API can resume
/// mutation or obtain the payload while migration is in progress.
pub struct MappingQuiescence<'source> {
    closed: ClosedMapping<'source>,
    generation: GenerationIdentity,
}

impl<'source> MappingQuiescence<'source> {
    /// Binds a consumed closed mapping to its authenticated generation.
    ///
    /// # Safety
    ///
    /// `closed` must be the mapping whose exact schema, allocator namespace,
    /// monotonic generation, and backing digest are named by `generation`.
    /// The host must have authenticated that association and excluded raw or
    /// nonconforming access which bypasses the typed mapping lifecycle.
    pub unsafe fn bind(
        closed: ClosedMapping<'source>,
        generation: GenerationIdentity,
    ) -> Result<Self, MigrationError> {
        generation.validate()?;
        Ok(Self { closed, generation })
    }

    /// Returns the bound source generation identity.
    #[must_use]
    pub const fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    /// Borrows the consumed close capability for host-side mapping metadata.
    #[must_use]
    pub const fn closed_mapping(&self) -> &ClosedMapping<'source> {
        &self.closed
    }
}

impl quiescence_sealed::Sealed for MappingQuiescence<'_> {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn still_quiescent(&self) -> bool {
        true
    }
}

impl QuiescenceWitness for MappingQuiescence<'_> {}

/// Supervisor-held authority over a complete, private target backing object.
///
/// An implementation may wrap an owned file descriptor, a borrow from a
/// supervisor object which owns an independently live duplicate, or another
/// authenticated backing capability. Migration owns the value from target
/// validation through the commit CAS, then returns it in [`CommittedMigration`]
/// so only post-commit code can publish attachment material.
///
/// # Safety
///
/// When [`Self::is_private`] returns true:
///
/// - [`Self::generation`] must identify the exact mapped target bytes;
/// - [`Self::recovery_authority`] must name a supervisor which already owns an
///   authenticated handle that remains live if the migrator process exits;
/// - no client can discover or attach the target before commit; and
/// - moving this value into the library must exclude every safe publication
///   path until it is returned after commit.
pub unsafe trait PrecommitTargetBacking {
    /// Returns the exact target generation represented by this capability.
    fn generation(&self) -> GenerationIdentity;

    /// Returns the supervisor/recovery authority which retains the backing.
    fn recovery_authority(&self) -> AuthorityIdentity;

    /// Returns whether the target is still unavailable to clients.
    fn is_private(&self) -> bool;
}

/// Shared-memory phase of a one-shot migration control record.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MigrationPhase {
    /// No migration has claimed the record.
    Uninitialized,
    /// A process is publishing immutable plan metadata.
    Initializing,
    /// The target is private and may be populated.
    Copying,
    /// The target is complete but the source remains authoritative.
    TargetReady,
    /// The target is authoritative; the source must never reopen.
    Committed,
    /// Reclamation was fenced and may be completed or resumed.
    Reclaimed,
    /// Progress is forbidden and both the control and incomplete target fail closed.
    Poisoned,
}

impl MigrationPhase {
    fn decode(raw: u64) -> Result<Self, MigrationError> {
        match raw {
            UNINITIALIZED => Ok(Self::Uninitialized),
            INITIALIZING => Ok(Self::Initializing),
            COPYING => Ok(Self::Copying),
            TARGET_READY => Ok(Self::TargetReady),
            COMMITTED => Ok(Self::Committed),
            RECLAIMED => Ok(Self::Reclaimed),
            POISONED => Ok(Self::Poisoned),
            _ => Err(MigrationError::CorruptPhase { raw }),
        }
    }

    fn has_published_plan(self) -> bool {
        matches!(
            self,
            Self::Copying | Self::TargetReady | Self::Committed | Self::Reclaimed
        )
    }
}

/// Which generation an authenticated router must select.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AuthoritativeGeneration {
    /// The source remains authoritative; a private target must be ignored.
    Source,
    /// Commit linearized and the target is authoritative.
    Target,
}

/// Validated diagnostic view of a migration control record.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationSnapshot {
    phase: MigrationPhase,
    plan: Option<MigrationPlan>,
}

impl MigrationSnapshot {
    /// Returns the shared-memory phase.
    pub const fn phase(self) -> MigrationPhase {
        self.phase
    }

    /// Returns plan metadata only after its release publication completed.
    pub const fn plan(self) -> Option<MigrationPlan> {
        self.plan
    }

    /// Returns the authoritative generation when recovery can decide safely.
    pub const fn authoritative(self) -> Option<AuthoritativeGeneration> {
        match self.phase {
            MigrationPhase::Copying | MigrationPhase::TargetReady => {
                Some(AuthoritativeGeneration::Source)
            }
            MigrationPhase::Committed | MigrationPhase::Reclaimed => {
                Some(AuthoritativeGeneration::Target)
            }
            _ => None,
        }
    }
}

/// Pointer-free, one-shot migration transaction record.
///
/// The record is suitable for a small supervisor-owned control mapping. It is
/// not a durability primitive for disk-backed persistent memory: its atomics
/// define process visibility, not cache-line flush or filesystem ordering.
pub struct MigrationControl {
    phase: AtomicU64,
    transaction_id: AtomicU64,
    source_version: AtomicU64,
    source_fingerprint_low: AtomicU64,
    source_fingerprint_high: AtomicU64,
    source_region_id: AtomicU64,
    source_sequence: AtomicU64,
    source_backing_0: AtomicU64,
    source_backing_1: AtomicU64,
    source_backing_2: AtomicU64,
    source_backing_3: AtomicU64,
    target_version: AtomicU64,
    target_fingerprint_low: AtomicU64,
    target_fingerprint_high: AtomicU64,
    target_region_id: AtomicU64,
    target_sequence: AtomicU64,
    target_backing_0: AtomicU64,
    target_backing_1: AtomicU64,
    target_backing_2: AtomicU64,
    target_backing_3: AtomicU64,
    target_authority_0: AtomicU64,
    target_authority_1: AtomicU64,
    checksum: AtomicU64,
}

impl MigrationControl {
    /// Creates an unused one-shot control record.
    pub const fn new() -> Self {
        Self {
            phase: AtomicU64::new(UNINITIALIZED),
            transaction_id: AtomicU64::new(0),
            source_version: AtomicU64::new(0),
            source_fingerprint_low: AtomicU64::new(0),
            source_fingerprint_high: AtomicU64::new(0),
            source_region_id: AtomicU64::new(0),
            source_sequence: AtomicU64::new(0),
            source_backing_0: AtomicU64::new(0),
            source_backing_1: AtomicU64::new(0),
            source_backing_2: AtomicU64::new(0),
            source_backing_3: AtomicU64::new(0),
            target_version: AtomicU64::new(0),
            target_fingerprint_low: AtomicU64::new(0),
            target_fingerprint_high: AtomicU64::new(0),
            target_region_id: AtomicU64::new(0),
            target_sequence: AtomicU64::new(0),
            target_backing_0: AtomicU64::new(0),
            target_backing_1: AtomicU64::new(0),
            target_backing_2: AtomicU64::new(0),
            target_backing_3: AtomicU64::new(0),
            target_authority_0: AtomicU64::new(0),
            target_authority_1: AtomicU64::new(0),
            checksum: AtomicU64::new(0),
        }
    }

    /// Initializes a control record directly in final shared storage.
    ///
    /// # Safety
    ///
    /// `destination` must be non-null, aligned, exclusively writable for one
    /// `Self`, and valid for the completed record's full shared lifetime.
    pub unsafe fn initialize_at(destination: *mut Self) {
        // SAFETY: the caller grants exclusive final storage. Field-wise writes
        // initialize the complete all-AtomicU64 record without inviting a
        // freestanding compiler to lower a large aggregate copy to `memcpy` or
        // `memset`.
        unsafe {
            core::ptr::addr_of_mut!((*destination).phase).write(AtomicU64::new(UNINITIALIZED));
            core::ptr::addr_of_mut!((*destination).transaction_id).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_version).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_fingerprint_low).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_fingerprint_high)
                .write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_region_id).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_sequence).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_backing_0).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_backing_1).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_backing_2).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_backing_3).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_version).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_fingerprint_low).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_fingerprint_high)
                .write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_region_id).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_sequence).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_backing_0).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_backing_1).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_backing_2).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_backing_3).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_authority_0).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_authority_1).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).checksum).write(AtomicU64::new(0));
        }
    }

    /// Validates and snapshots the current record.
    ///
    /// An unknown phase or changed published metadata poisons the record. A
    /// record left in `Initializing` or `Copying` by process death remains
    /// visibly stuck and cannot be stolen.
    pub fn snapshot(&self) -> Result<MigrationSnapshot, MigrationError> {
        let raw = self.phase.load(Ordering::Acquire);
        let phase = self.decode_or_poison(raw)?;
        let plan = if phase.has_published_plan() {
            Some(self.load_valid_plan()?)
        } else {
            None
        };
        Ok(MigrationSnapshot { phase, plan })
    }

    /// Claims the record while consuming a terminal source witness.
    ///
    /// The witness generation must exactly equal the plan source, including
    /// schema fingerprint, region namespace, monotonic sequence, and backing
    /// digest. Consuming the witness keeps safe source mutation unavailable for
    /// the complete migration transaction.
    pub fn begin_with_quiescent_source<Q: QuiescenceWitness>(
        &self,
        source: Q,
        plan: MigrationPlan,
    ) -> Result<Migration<'_, Q>, MigrationError> {
        if source.generation() != plan.source {
            return Err(MigrationError::SourceGenerationMismatch {
                expected_tag: generation_tag(plan.source),
                observed_tag: generation_tag(source.generation()),
            });
        }
        if !source.still_quiescent() {
            return Err(MigrationError::SourceNotDrained);
        }
        self.begin(source, plan)
    }

    /// Permanently poisons a stuck or failed transaction.
    ///
    /// A supervisor should first prove the migration owner has exited, for
    /// example with `pidfd`, and prevent all participants from accessing the
    /// incomplete target. Poisoning never unlocks or repairs payload writes.
    /// It cannot overwrite a committed or reclaimed route.
    pub fn poison(&self) {
        let mut phase = self.phase.load(Ordering::Acquire);
        loop {
            if matches!(phase, COMMITTED | RECLAIMED | POISONED) {
                return;
            }
            if MigrationPhase::decode(phase).is_err() {
                self.phase.store(POISONED, Ordering::Release);
                return;
            }
            match self.phase.compare_exchange_weak(
                phase,
                POISONED,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return,
                Err(actual) => phase = actual,
            }
        }
    }

    /// Returns the authoritative generation after validating all metadata.
    pub fn authoritative_generation(
        &self,
    ) -> Result<(AuthoritativeGeneration, MigrationPlan), MigrationError> {
        let snapshot = self.snapshot()?;
        let generation =
            snapshot
                .authoritative()
                .ok_or(MigrationError::NoAuthoritativeGeneration {
                    phase: snapshot.phase,
                })?;
        Ok((
            generation,
            snapshot
                .plan
                .expect("authoritative phases always publish a plan"),
        ))
    }

    /// Fences source reclamation with an exactly bound terminal witness.
    ///
    /// The transition to `Reclaimed` occurs before external cleanup. A process
    /// dying afterward may leak the source, but recovery can safely resume
    /// cleanup because the target remains authoritative.
    ///
    /// # Safety
    ///
    /// The caller must prevent uncounted raw access, inherited mappings, and
    /// nonconforming clients. The returned permit does not by itself make
    /// `munmap`, descriptor destruction, or file truncation safe.
    pub unsafe fn authorize_reclamation<Q: QuiescenceWitness>(
        &self,
        source: &Q,
    ) -> Result<ReclamationPermit, MigrationError> {
        let snapshot = self.snapshot()?;
        let plan = snapshot.plan.ok_or(MigrationError::WrongPhase {
            expected: MigrationPhase::Committed,
            actual: snapshot.phase,
        })?;
        if source.generation() != plan.source {
            return Err(MigrationError::SourceGenerationMismatch {
                expected_tag: generation_tag(plan.source),
                observed_tag: generation_tag(source.generation()),
            });
        }
        if !source.still_quiescent() {
            return Err(MigrationError::SourceNotDrained);
        }
        if snapshot.phase == MigrationPhase::Reclaimed {
            return Ok(ReclamationPermit {
                plan,
                resumed: true,
            });
        }
        if snapshot.phase != MigrationPhase::Committed {
            return Err(MigrationError::WrongPhase {
                expected: MigrationPhase::Committed,
                actual: snapshot.phase,
            });
        }
        self.mark_reclaimed(plan, false)
    }

    /// Resumes host cleanup after reclamation was already fenced.
    ///
    /// This supports a cleanup process which died after the `Reclaimed`
    /// transition and before an idempotent `munmap`, unlink, or truncate step.
    ///
    /// # Safety
    ///
    /// The caller must authenticate this exact control record and prevent all
    /// access to the retired source generation. This method supplies no new
    /// quiescence proof; it relies on the proof which preceded the shared
    /// `Reclaimed` transition.
    pub unsafe fn resume_reclamation(&self) -> Result<ReclamationPermit, MigrationError> {
        let snapshot = self.snapshot()?;
        if snapshot.phase != MigrationPhase::Reclaimed {
            return Err(MigrationError::WrongPhase {
                expected: MigrationPhase::Reclaimed,
                actual: snapshot.phase,
            });
        }
        Ok(ReclamationPermit {
            plan: snapshot
                .plan
                .expect("reclaimed phase always publishes a plan"),
            resumed: true,
        })
    }

    fn begin<Q: QuiescenceWitness>(
        &self,
        source: Q,
        plan: MigrationPlan,
    ) -> Result<Migration<'_, Q>, MigrationError> {
        plan.validate()?;
        if let Err(actual) = self.phase.compare_exchange(
            UNINITIALIZED,
            INITIALIZING,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            let actual = self.decode_or_poison(actual)?;
            return Err(MigrationError::WrongPhase {
                expected: MigrationPhase::Uninitialized,
                actual,
            });
        }

        let mut poison = InitializationPoison {
            control: self,
            armed: true,
        };
        self.transaction_id
            .store(plan.transaction_id, Ordering::Relaxed);
        self.source_version
            .store(plan.source.schema.version, Ordering::Relaxed);
        self.source_fingerprint_low
            .store(plan.source.schema.fingerprint_low, Ordering::Relaxed);
        self.source_fingerprint_high
            .store(plan.source.schema.fingerprint_high, Ordering::Relaxed);
        self.source_region_id
            .store(plan.source.region_id, Ordering::Relaxed);
        self.source_sequence
            .store(plan.source.sequence, Ordering::Relaxed);
        self.source_backing_0
            .store(plan.source.backing.0[0], Ordering::Relaxed);
        self.source_backing_1
            .store(plan.source.backing.0[1], Ordering::Relaxed);
        self.source_backing_2
            .store(plan.source.backing.0[2], Ordering::Relaxed);
        self.source_backing_3
            .store(plan.source.backing.0[3], Ordering::Relaxed);
        self.target_version
            .store(plan.target.schema.version, Ordering::Relaxed);
        self.target_fingerprint_low
            .store(plan.target.schema.fingerprint_low, Ordering::Relaxed);
        self.target_fingerprint_high
            .store(plan.target.schema.fingerprint_high, Ordering::Relaxed);
        self.target_region_id
            .store(plan.target.region_id, Ordering::Relaxed);
        self.target_sequence
            .store(plan.target.sequence, Ordering::Relaxed);
        self.target_backing_0
            .store(plan.target.backing.0[0], Ordering::Relaxed);
        self.target_backing_1
            .store(plan.target.backing.0[1], Ordering::Relaxed);
        self.target_backing_2
            .store(plan.target.backing.0[2], Ordering::Relaxed);
        self.target_backing_3
            .store(plan.target.backing.0[3], Ordering::Relaxed);
        self.target_authority_0
            .store(plan.target_authority.0[0], Ordering::Relaxed);
        self.target_authority_1
            .store(plan.target_authority.0[1], Ordering::Relaxed);
        self.checksum.store(plan_checksum(plan), Ordering::Relaxed);
        if let Err(actual) =
            self.phase
                .compare_exchange(INITIALIZING, COPYING, Ordering::Release, Ordering::Acquire)
        {
            return Err(MigrationError::WrongPhase {
                expected: MigrationPhase::Initializing,
                actual: self.decode_or_poison(actual)?,
            });
        }
        poison.armed = false;
        Ok(Migration {
            control: self,
            plan,
            source: Some(source),
            armed: true,
        })
    }

    fn load_valid_plan(&self) -> Result<MigrationPlan, MigrationError> {
        let plan = MigrationPlan {
            transaction_id: self.transaction_id.load(Ordering::Relaxed),
            source: GenerationIdentity {
                schema: SchemaIdentity {
                    version: self.source_version.load(Ordering::Relaxed),
                    fingerprint_low: self.source_fingerprint_low.load(Ordering::Relaxed),
                    fingerprint_high: self.source_fingerprint_high.load(Ordering::Relaxed),
                },
                region_id: self.source_region_id.load(Ordering::Relaxed),
                sequence: self.source_sequence.load(Ordering::Relaxed),
                backing: BackingIdentity([
                    self.source_backing_0.load(Ordering::Relaxed),
                    self.source_backing_1.load(Ordering::Relaxed),
                    self.source_backing_2.load(Ordering::Relaxed),
                    self.source_backing_3.load(Ordering::Relaxed),
                ]),
            },
            target: GenerationIdentity {
                schema: SchemaIdentity {
                    version: self.target_version.load(Ordering::Relaxed),
                    fingerprint_low: self.target_fingerprint_low.load(Ordering::Relaxed),
                    fingerprint_high: self.target_fingerprint_high.load(Ordering::Relaxed),
                },
                region_id: self.target_region_id.load(Ordering::Relaxed),
                sequence: self.target_sequence.load(Ordering::Relaxed),
                backing: BackingIdentity([
                    self.target_backing_0.load(Ordering::Relaxed),
                    self.target_backing_1.load(Ordering::Relaxed),
                    self.target_backing_2.load(Ordering::Relaxed),
                    self.target_backing_3.load(Ordering::Relaxed),
                ]),
            },
            target_authority: AuthorityIdentity([
                self.target_authority_0.load(Ordering::Relaxed),
                self.target_authority_1.load(Ordering::Relaxed),
            ]),
        };
        let expected_checksum = plan_checksum(plan);
        let found_checksum = self.checksum.load(Ordering::Relaxed);
        if plan.validate().is_err() || found_checksum != expected_checksum {
            self.phase.store(POISONED, Ordering::Release);
            return Err(MigrationError::CorruptMetadata);
        }
        Ok(plan)
    }

    fn transition(
        &self,
        plan: MigrationPlan,
        expected: MigrationPhase,
        expected_raw: u64,
        next_raw: u64,
    ) -> Result<(), MigrationError> {
        let found = self.load_valid_plan()?;
        if found != plan {
            self.phase.store(POISONED, Ordering::Release);
            return Err(MigrationError::TransactionMismatch);
        }
        match self.phase.compare_exchange(
            expected_raw,
            next_raw,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => Ok(()),
            Err(raw) => Err(MigrationError::WrongPhase {
                expected,
                actual: self.decode_or_poison(raw)?,
            }),
        }
    }

    fn poison_if_incomplete(&self, transaction_id: u64) {
        if self.transaction_id.load(Ordering::Relaxed) != transaction_id {
            return;
        }
        let mut phase = self.phase.load(Ordering::Acquire);
        loop {
            if phase != COPYING && phase != TARGET_READY {
                return;
            }
            match self.phase.compare_exchange_weak(
                phase,
                POISONED,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return,
                Err(actual) => phase = actual,
            }
        }
    }

    fn mark_reclaimed(
        &self,
        plan: MigrationPlan,
        resumed: bool,
    ) -> Result<ReclamationPermit, MigrationError> {
        self.transition(plan, MigrationPhase::Committed, COMMITTED, RECLAIMED)?;
        Ok(ReclamationPermit { plan, resumed })
    }

    fn decode_or_poison(&self, raw: u64) -> Result<MigrationPhase, MigrationError> {
        match MigrationPhase::decode(raw) {
            Ok(phase) => Ok(phase),
            Err(error) => {
                self.phase.store(POISONED, Ordering::Release);
                Err(error)
            }
        }
    }
}

impl Default for MigrationControl {
    fn default() -> Self {
        Self::new()
    }
}

// SAFETY: every shared field is an address-independent atomic integer.
unsafe impl FixedAddressPodValue for MigrationControl {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "migration controls must not need drop"
        );
        let mut state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-migration-control-v2",
        );
        state = __private::mix_usize(state, size_of::<Self>());
        state = __private::mix_usize(state, align_of::<Self>());
        state = atomic_field(state, b"phase", offset_of!(Self, phase));
        state = atomic_field(state, b"transaction_id", offset_of!(Self, transaction_id));
        state = atomic_field(state, b"source_version", offset_of!(Self, source_version));
        state = atomic_field(
            state,
            b"source_fingerprint_low",
            offset_of!(Self, source_fingerprint_low),
        );
        state = atomic_field(
            state,
            b"source_fingerprint_high",
            offset_of!(Self, source_fingerprint_high),
        );
        state = atomic_field(
            state,
            b"source_region_id",
            offset_of!(Self, source_region_id),
        );
        state = atomic_field(state, b"source_sequence", offset_of!(Self, source_sequence));
        state = atomic_field(
            state,
            b"source_backing_0",
            offset_of!(Self, source_backing_0),
        );
        state = atomic_field(
            state,
            b"source_backing_1",
            offset_of!(Self, source_backing_1),
        );
        state = atomic_field(
            state,
            b"source_backing_2",
            offset_of!(Self, source_backing_2),
        );
        state = atomic_field(
            state,
            b"source_backing_3",
            offset_of!(Self, source_backing_3),
        );
        state = atomic_field(state, b"target_version", offset_of!(Self, target_version));
        state = atomic_field(
            state,
            b"target_fingerprint_low",
            offset_of!(Self, target_fingerprint_low),
        );
        state = atomic_field(
            state,
            b"target_fingerprint_high",
            offset_of!(Self, target_fingerprint_high),
        );
        state = atomic_field(
            state,
            b"target_region_id",
            offset_of!(Self, target_region_id),
        );
        state = atomic_field(state, b"target_sequence", offset_of!(Self, target_sequence));
        state = atomic_field(
            state,
            b"target_backing_0",
            offset_of!(Self, target_backing_0),
        );
        state = atomic_field(
            state,
            b"target_backing_1",
            offset_of!(Self, target_backing_1),
        );
        state = atomic_field(
            state,
            b"target_backing_2",
            offset_of!(Self, target_backing_2),
        );
        state = atomic_field(
            state,
            b"target_backing_3",
            offset_of!(Self, target_backing_3),
        );
        state = atomic_field(
            state,
            b"target_authority_0",
            offset_of!(Self, target_authority_0),
        );
        state = atomic_field(
            state,
            b"target_authority_1",
            offset_of!(Self, target_authority_1),
        );
        state = atomic_field(state, b"checksum", offset_of!(Self, checksum));
        __private::finish(state)
    };
}

// SAFETY: every atomic stores data rather than an address.
unsafe impl PodValue for MigrationControl {}
// SAFETY: all shared mutation uses process-shared atomics.
unsafe impl PodSync for MigrationControl {}

/// Process-local authority for the private target build phase.
///
/// Dropping this value before commit poisons the transaction. Process death
/// skips `Drop` and leaves `Copying` or `TargetReady`, which is also fail closed.
#[must_use = "a migration authority must either commit or poison the transaction"]
pub struct Migration<'a, Q: QuiescenceWitness> {
    control: &'a MigrationControl,
    plan: MigrationPlan,
    source: Option<Q>,
    armed: bool,
}

impl<'control, Q: QuiescenceWitness> Migration<'control, Q> {
    /// Returns the immutable migration plan.
    pub const fn plan(&self) -> MigrationPlan {
        self.plan
    }

    /// Validates and release-publishes a complete private target.
    ///
    /// # Safety
    ///
    /// Before this call, the caller must establish all of the following:
    ///
    /// - `target.generation()` is the exact schema, allocator namespace,
    ///   monotonic sequence, and backing object populated by the migration;
    /// - every root, payload byte, and allocator descriptor reachable in that
    ///   generation is initialized and validated for `self.plan().target()`;
    /// - all target writes happen-before this thread's call (the successful
    ///   release transition publishes them to an acquire-reading router);
    /// - no interior writer, raw mutable alias, callback, or child process can
    ///   mutate the target after validation; and
    /// - the target uses coherent process-shared memory. This is not a
    ///   machine-crash or power-loss persistence boundary.
    ///
    /// The [`PrecommitTargetBacking`] contract additionally keeps client attach
    /// material private and guarantees an authenticated recovery authority owns
    /// an independently live handle before the commit CAS.
    pub unsafe fn mark_target_ready<B: PrecommitTargetBacking>(
        mut self,
        target: B,
    ) -> Result<TargetReadyMigration<'control, Q, B>, MigrationError> {
        if target.generation() != self.plan.target {
            return Err(MigrationError::TargetGenerationMismatch {
                expected_tag: generation_tag(self.plan.target),
                observed_tag: generation_tag(target.generation()),
            });
        }
        if target.recovery_authority() != self.plan.target_authority {
            return Err(MigrationError::TargetAuthorityMismatch {
                expected: self.plan.target_authority,
                observed: target.recovery_authority(),
            });
        }
        if !target.is_private() {
            return Err(MigrationError::TargetAlreadyPublished);
        }
        self.control
            .transition(self.plan, MigrationPhase::Copying, COPYING, TARGET_READY)?;
        self.armed = false;
        Ok(TargetReadyMigration {
            control: self.control,
            plan: self.plan,
            source: self.source.take(),
            target: Some(target),
            armed: true,
        })
    }
}

impl<Q: QuiescenceWitness> Drop for Migration<'_, Q> {
    fn drop(&mut self) {
        if self.armed {
            self.control.poison_if_incomplete(self.plan.transaction_id);
        }
    }
}

/// Target-ready authority which owns both terminal source proof and private
/// target publication authority through the commit CAS.
#[must_use = "target-ready authority must commit or poison the transaction"]
pub struct TargetReadyMigration<'control, Q: QuiescenceWitness, B: PrecommitTargetBacking> {
    control: &'control MigrationControl,
    plan: MigrationPlan,
    source: Option<Q>,
    target: Option<B>,
    armed: bool,
}

impl<Q: QuiescenceWitness, B: PrecommitTargetBacking> TargetReadyMigration<'_, Q, B> {
    /// Returns the immutable migration plan.
    pub const fn plan(&self) -> MigrationPlan {
        self.plan
    }

    /// Atomically makes the validated target generation authoritative.
    ///
    /// Authenticated attach/bootstrap code must consult
    /// [`MigrationControl::authoritative_generation`] and must not cache the
    /// pre-commit route across this transition. The target capability remains
    /// private until this method returns it inside [`CommittedMigration`].
    pub fn commit(mut self) -> Result<CommittedMigration<Q, B>, MigrationError> {
        let target = self
            .target
            .as_ref()
            .expect("target-ready authority retains target");
        if target.generation() != self.plan.target {
            return Err(MigrationError::TargetGenerationMismatch {
                expected_tag: generation_tag(self.plan.target),
                observed_tag: generation_tag(target.generation()),
            });
        }
        if target.recovery_authority() != self.plan.target_authority {
            return Err(MigrationError::TargetAuthorityMismatch {
                expected: self.plan.target_authority,
                observed: target.recovery_authority(),
            });
        }
        if !target.is_private() {
            return Err(MigrationError::TargetAlreadyPublished);
        }
        self.control.transition(
            self.plan,
            MigrationPhase::TargetReady,
            TARGET_READY,
            COMMITTED,
        )?;
        self.armed = false;
        Ok(CommittedMigration {
            plan: self.plan,
            source: self
                .source
                .take()
                .expect("target-ready authority retains source witness"),
            target: self
                .target
                .take()
                .expect("target-ready authority retains target"),
        })
    }
}

impl<Q: QuiescenceWitness, B: PrecommitTargetBacking> Drop for TargetReadyMigration<'_, Q, B> {
    fn drop(&mut self) {
        if self.armed {
            self.control.poison_if_incomplete(self.plan.transaction_id);
        }
    }
}

/// Confirmation that target publication linearized while both capabilities
/// remained owned by this transaction.
#[must_use = "the committed plan identifies the newly authoritative generation"]
pub struct CommittedMigration<Q: QuiescenceWitness, B: PrecommitTargetBacking> {
    plan: MigrationPlan,
    source: Q,
    target: B,
}

impl<Q: QuiescenceWitness, B: PrecommitTargetBacking> CommittedMigration<Q, B> {
    /// Returns the committed plan.
    pub const fn plan(&self) -> MigrationPlan {
        self.plan
    }

    /// Borrows the exact terminal source witness for reclamation fencing.
    pub const fn source(&self) -> &Q {
        &self.source
    }

    /// Borrows the target authority. Publication must occur only after commit.
    pub const fn target(&self) -> &B {
        &self.target
    }

    /// Returns the source witness and target publication authority to the host.
    pub fn into_capabilities(self) -> (Q, B) {
        (self.source, self.target)
    }
}

/// Confirmation that reclamation was fenced in the shared control record.
///
/// This is evidence for higher-level cleanup logic, not an owning Rust
/// capability. The host must still exclude raw pointers, inherited mappings,
/// and nonconforming guests before destructive operations.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[must_use = "reclamation remains an explicit host operation"]
pub struct ReclamationPermit {
    plan: MigrationPlan,
    resumed: bool,
}

impl ReclamationPermit {
    /// Returns the source and target generation plan.
    pub const fn plan(self) -> MigrationPlan {
        self.plan
    }

    /// Returns true when cleanup was resumed from an already-fenced state.
    pub const fn is_resume(self) -> bool {
        self.resumed
    }
}

struct InitializationPoison<'a> {
    control: &'a MigrationControl,
    armed: bool,
}

impl Drop for InitializationPoison<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.control.phase.store(POISONED, Ordering::Release);
        }
    }
}

/// Migration protocol failure.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MigrationError {
    /// Schema version zero is reserved for invalid/uninitialized metadata.
    ZeroSchemaVersion,
    /// Transaction identity zero is reserved for invalid metadata.
    ZeroTransactionId,
    /// Region generation identity zero is reserved for invalid metadata.
    ZeroRegionId,
    /// Monotonic generation sequence zero is reserved for invalid metadata.
    ZeroGenerationSequence,
    /// An all-zero backing digest is reserved for invalid metadata.
    ZeroBackingIdentity,
    /// An all-zero supervisor authority is reserved for invalid metadata.
    ZeroAuthorityIdentity,
    /// Source and target attempted to use the same allocator region namespace.
    ReusedRegionId,
    /// Source and target attempted to name the same backing object.
    ReusedBackingIdentity,
    /// The caller-provided monotonic generation sequence did not increase.
    GenerationDidNotIncrease {
        /// Source sequence.
        source: u64,
        /// Proposed target sequence.
        target: u64,
    },
    /// An upgrade did not monotonically increase its schema version.
    VersionDidNotIncrease {
        /// Source version.
        source: u64,
        /// Proposed target version.
        target: u64,
    },
    /// The observed schema was neither current nor an accepted exact source.
    UnsupportedSchema {
        /// Identity observed in shared storage.
        observed: SchemaIdentity,
        /// Identity compiled into this executable.
        current: SchemaIdentity,
    },
    /// The supplied source has not reached stable quiescence.
    SourceNotDrained,
    /// A terminal source witness names a different generation than the plan.
    SourceGenerationMismatch {
        /// Non-authenticating diagnostic tag of the plan generation.
        expected_tag: u64,
        /// Non-authenticating diagnostic tag of the witness generation.
        observed_tag: u64,
    },
    /// A target backing capability names a different generation than the plan.
    TargetGenerationMismatch {
        /// Non-authenticating diagnostic tag of the plan generation.
        expected_tag: u64,
        /// Non-authenticating diagnostic tag of the capability generation.
        observed_tag: u64,
    },
    /// A target backing capability names a different recovery authority.
    TargetAuthorityMismatch {
        /// Authority required by the plan.
        expected: AuthorityIdentity,
        /// Authority represented by the capability.
        observed: AuthorityIdentity,
    },
    /// The target was already discoverable by clients before commit.
    TargetAlreadyPublished,
    /// A protocol operation observed the wrong shared-memory phase.
    WrongPhase {
        /// Required phase.
        expected: MigrationPhase,
        /// Observed phase.
        actual: MigrationPhase,
    },
    /// The raw phase word is not part of this protocol version.
    CorruptPhase {
        /// Unknown raw phase.
        raw: u64,
    },
    /// Published immutable metadata failed validation or its checksum changed.
    CorruptMetadata,
    /// A process-local authority does not match the shared transaction.
    TransactionMismatch,
    /// Recovery cannot choose a generation in this phase.
    NoAuthoritativeGeneration {
        /// Incomplete or poisoned phase.
        phase: MigrationPhase,
    },
    /// Typed mapping lifecycle validation failed.
    Mapping(crate::mapping::MappingError),
}

impl fmt::Display for MigrationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroSchemaVersion => formatter.write_str("schema version must be nonzero"),
            Self::ZeroTransactionId => formatter.write_str("transaction id must be nonzero"),
            Self::ZeroRegionId => formatter.write_str("region id must be nonzero"),
            Self::ZeroGenerationSequence => {
                formatter.write_str("generation sequence must be nonzero")
            }
            Self::ZeroBackingIdentity => {
                formatter.write_str("backing identity must not be all zero")
            }
            Self::ZeroAuthorityIdentity => {
                formatter.write_str("target authority identity must not be all zero")
            }
            Self::ReusedRegionId => {
                formatter.write_str("target region id must differ from source region id")
            }
            Self::ReusedBackingIdentity => {
                formatter.write_str("target backing identity must differ from source backing")
            }
            Self::GenerationDidNotIncrease { source, target } => write!(
                formatter,
                "target generation sequence {target} did not exceed source sequence {source}"
            ),
            Self::VersionDidNotIncrease { source, target } => write!(
                formatter,
                "target schema version {target} did not exceed source version {source}"
            ),
            Self::UnsupportedSchema { observed, current } => write!(
                formatter,
                "schema {observed:?} is unsupported by current schema {current:?}"
            ),
            Self::SourceNotDrained => formatter.write_str("source generation is not drained"),
            Self::SourceGenerationMismatch {
                expected_tag,
                observed_tag,
            } => write!(
                formatter,
                "source witness generation tag {observed_tag:#018x} does not match plan tag {expected_tag:#018x}"
            ),
            Self::TargetGenerationMismatch {
                expected_tag,
                observed_tag,
            } => write!(
                formatter,
                "target backing generation tag {observed_tag:#018x} does not match plan tag {expected_tag:#018x}"
            ),
            Self::TargetAuthorityMismatch { expected, observed } => write!(
                formatter,
                "target recovery authority {observed:?} does not match plan {expected:?}"
            ),
            Self::TargetAlreadyPublished => {
                formatter.write_str("target backing is already published to clients")
            }
            Self::WrongPhase { expected, actual } => {
                write!(formatter, "expected phase {expected:?}, found {actual:?}")
            }
            Self::CorruptPhase { raw } => write!(formatter, "unknown migration phase {raw}"),
            Self::CorruptMetadata => formatter.write_str("migration metadata is corrupt"),
            Self::TransactionMismatch => {
                formatter.write_str("migration authority does not match shared transaction")
            }
            Self::NoAuthoritativeGeneration { phase } => write!(
                formatter,
                "phase {phase:?} does not identify an authoritative generation"
            ),
            Self::Mapping(error) => write!(formatter, "mapping lifecycle error: {error}"),
        }
    }
}

impl core::error::Error for MigrationError {}

fn plan_checksum(plan: MigrationPlan) -> u64 {
    let mut state = 0x9e37_79b9_7f4a_7c15_u64;
    state = checksum_mix(state, plan.transaction_id);
    state = checksum_generation(state, plan.source);
    state = checksum_generation(state, plan.target);
    state = checksum_mix(state, plan.target_authority.0[0]);
    checksum_mix(state, plan.target_authority.0[1])
}

fn generation_tag(generation: GenerationIdentity) -> u64 {
    checksum_generation(0xd6e8_feb8_6659_fd93, generation)
}

fn checksum_generation(mut state: u64, generation: GenerationIdentity) -> u64 {
    state = checksum_mix(state, generation.schema.version);
    state = checksum_mix(state, generation.schema.fingerprint_low);
    state = checksum_mix(state, generation.schema.fingerprint_high);
    state = checksum_mix(state, generation.region_id);
    state = checksum_mix(state, generation.sequence);
    state = checksum_mix(state, generation.backing.0[0]);
    state = checksum_mix(state, generation.backing.0[1]);
    state = checksum_mix(state, generation.backing.0[2]);
    checksum_mix(state, generation.backing.0[3])
}

fn checksum_mix(state: u64, value: u64) -> u64 {
    state
        .rotate_left(17)
        .wrapping_add(value ^ 0xa076_1d64_78bd_642f)
        .wrapping_mul(0xe703_7ed1_a0b4_28db)
}

const fn atomic_field(mut state: u128, name: &[u8], offset: usize) -> u128 {
    state = __private::mix_bytes(state, name);
    state = __private::mix_usize(state, offset);
    state = __private::mix_usize(state, size_of::<AtomicU64>());
    state = __private::mix_usize(state, align_of::<AtomicU64>());
    __private::mix_u128(state, AtomicU64::FINGERPRINT)
}

const fn mix_field(
    mut state: u128,
    name: &[u8],
    offset: usize,
    size: usize,
    alignment: usize,
    fingerprint: u128,
) -> u128 {
    state = __private::mix_bytes(state, name);
    state = __private::mix_usize(state, offset);
    state = __private::mix_usize(state, size);
    state = __private::mix_usize(state, alignment);
    __private::mix_u128(state, fingerprint)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SOURCE_SCHEMA: SchemaIdentity = SchemaIdentity::new(1, 0x11);
    const TARGET_SCHEMA: SchemaIdentity = SchemaIdentity::new(2, 0x22);
    const AUTHORITY: AuthorityIdentity = AuthorityIdentity::new([0x77; 16]);

    fn plan() -> MigrationPlan {
        MigrationPlan::new(
            7,
            GenerationIdentity::new(SOURCE_SCHEMA, 41, 100, BackingIdentity::new([0x41; 32])),
            GenerationIdentity::new(TARGET_SCHEMA, 42, 101, BackingIdentity::new([0x42; 32])),
            AUTHORITY,
        )
        .unwrap()
    }

    #[test]
    fn published_metadata_corruption_poisons() {
        let admission = CloseableSnzi::<20>::new();
        admission.close();
        assert!(admission.is_drained());
        let control = MigrationControl::new();
        // SAFETY: this unit test's admission gate is the sole access path for
        // the exact synthetic source generation in plan().
        let source = unsafe { AdmissionQuiescence::bind(&admission, plan().source()) }.unwrap();
        let migration = control.begin_with_quiescent_source(source, plan()).unwrap();
        control.target_region_id.store(99, Ordering::Relaxed);
        assert_eq!(control.snapshot(), Err(MigrationError::CorruptMetadata));
        assert_eq!(
            control.snapshot().unwrap().phase(),
            MigrationPhase::Poisoned
        );
        core::mem::forget(migration);
    }

    #[test]
    fn malformed_phase_poisons() {
        let control = MigrationControl::new();
        control.phase.store(u64::MAX, Ordering::Relaxed);
        assert_eq!(
            control.snapshot(),
            Err(MigrationError::CorruptPhase { raw: u64::MAX })
        );
        assert_eq!(
            control.snapshot().unwrap().phase(),
            MigrationPhase::Poisoned
        );
    }
}
