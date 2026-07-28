//! Crash-stop schema migration and generation reclamation.
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

use core::fmt;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::sync::atomic::{AtomicU64, Ordering};

use crate::admission::CloseableSnzi;
use crate::mapping::{ClosedMapping, Draining};
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
        (self.fingerprint_high as u128) << 64 | self.fingerprint_low as u128
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

/// Immutable description of one source-to-target generation replacement.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MigrationPlan {
    transaction_id: u64,
    source: SchemaIdentity,
    target: SchemaIdentity,
    source_region_id: u64,
    target_region_id: u64,
}

impl MigrationPlan {
    /// Constructs a plan from two compiled schema types.
    pub fn for_schemas<Source: MigrationSchema, Target: MigrationSchema>(
        transaction_id: u64,
        source_region_id: u64,
        target_region_id: u64,
    ) -> Result<Self, MigrationError> {
        Self::new(
            transaction_id,
            SchemaIdentity::of::<Source>(),
            SchemaIdentity::of::<Target>(),
            source_region_id,
            target_region_id,
        )
    }

    /// Constructs and validates a migration plan.
    ///
    /// Region and transaction identifiers must be nonzero and never reused
    /// while stale descriptors may exist. Target versions must increase.
    pub fn new(
        transaction_id: u64,
        source: SchemaIdentity,
        target: SchemaIdentity,
        source_region_id: u64,
        target_region_id: u64,
    ) -> Result<Self, MigrationError> {
        let plan = Self {
            transaction_id,
            source,
            target,
            source_region_id,
            target_region_id,
        };
        plan.validate()?;
        Ok(plan)
    }

    /// Returns the transaction identity.
    pub const fn transaction_id(self) -> u64 {
        self.transaction_id
    }

    /// Returns the exact source schema.
    pub const fn source(self) -> SchemaIdentity {
        self.source
    }

    /// Returns the exact target schema.
    pub const fn target(self) -> SchemaIdentity {
        self.target
    }

    /// Returns the source allocator or mapping generation identity.
    pub const fn source_region_id(self) -> u64 {
        self.source_region_id
    }

    /// Returns the fresh target allocator or mapping generation identity.
    pub const fn target_region_id(self) -> u64 {
        self.target_region_id
    }

    fn validate(self) -> Result<(), MigrationError> {
        self.source.validate()?;
        self.target.validate()?;
        if self.transaction_id == 0 {
            return Err(MigrationError::ZeroTransactionId);
        }
        if self.source_region_id == 0 || self.target_region_id == 0 {
            return Err(MigrationError::ZeroRegionId);
        }
        if self.source_region_id == self.target_region_id {
            return Err(MigrationError::ReusedRegionId);
        }
        if self.target.version <= self.source.version {
            return Err(MigrationError::VersionDidNotIncrease {
                source: self.source.version,
                target: self.target.version,
            });
        }
        Ok(())
    }
}

// SAFETY: MigrationPlan recursively consists only of address-independent
// integers and SchemaIdentity values.
unsafe impl FixedAddressPodValue for MigrationPlan {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "migration plans must not need drop");
        let mut state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-migration-plan-v1");
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
            size_of::<SchemaIdentity>(),
            align_of::<SchemaIdentity>(),
            SchemaIdentity::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"target",
            offset_of!(Self, target),
            size_of::<SchemaIdentity>(),
            align_of::<SchemaIdentity>(),
            SchemaIdentity::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"source_region_id",
            offset_of!(Self, source_region_id),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        state = mix_field(
            state,
            b"target_region_id",
            offset_of!(Self, target_region_id),
            size_of::<u64>(),
            align_of::<u64>(),
            u64::FINGERPRINT,
        );
        __private::finish(state)
    };
}

// SAFETY: MigrationPlan stores no address.
unsafe impl PodValue for MigrationPlan {}
// SAFETY: MigrationPlan exposes immutable scalar fields only.
unsafe impl PodSync for MigrationPlan {}

/// Persistent phase of a one-shot migration control record.
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
    /// Returns the persistent phase.
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
    target_version: AtomicU64,
    target_fingerprint_low: AtomicU64,
    target_fingerprint_high: AtomicU64,
    source_region_id: AtomicU64,
    target_region_id: AtomicU64,
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
            target_version: AtomicU64::new(0),
            target_fingerprint_low: AtomicU64::new(0),
            target_fingerprint_high: AtomicU64::new(0),
            source_region_id: AtomicU64::new(0),
            target_region_id: AtomicU64::new(0),
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
            core::ptr::addr_of_mut!((*destination).target_version).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_fingerprint_low).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_fingerprint_high)
                .write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).source_region_id).write(AtomicU64::new(0));
            core::ptr::addr_of_mut!((*destination).target_region_id).write(AtomicU64::new(0));
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

    /// Claims the record only after a closeable admission barrier is drained.
    ///
    /// The barrier must guard the exact source generation named by `plan`.
    /// This association is an application/bootstrap invariant because the
    /// barrier intentionally stores no external generation identifier.
    pub fn begin_after_admission_drain<const NODES: usize>(
        &self,
        source_admission: &CloseableSnzi<NODES>,
        plan: MigrationPlan,
    ) -> Result<Migration<'_>, MigrationError> {
        if !source_admission.is_drained() {
            return Err(MigrationError::SourceNotDrained);
        }
        self.begin(plan)
    }

    /// Claims the record after a typed mapping reaches stable drain.
    ///
    /// The `Draining` authority must belong to the source generation named by
    /// `plan`. The mapping cannot reopen after this check.
    pub fn begin_after_mapping_drain<T: PodValue + PodSync>(
        &self,
        source: &Draining<'_, T>,
        plan: MigrationPlan,
    ) -> Result<Migration<'_>, MigrationError> {
        if !source.is_drained().map_err(MigrationError::Mapping)? {
            return Err(MigrationError::SourceNotDrained);
        }
        self.begin(plan)
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

    /// Fences source reclamation with the original closeable admission record.
    ///
    /// The transition to `Reclaimed` occurs before external cleanup. A process
    /// dying afterward may leak the source, but recovery can safely resume
    /// cleanup because the target remains authoritative.
    ///
    /// # Safety
    ///
    /// `source_admission` must guard the exact source generation in the
    /// published plan. The caller must also prevent uncounted raw access. The
    /// returned permit does not by itself make `munmap`, descriptor destruction,
    /// or file truncation safe.
    pub unsafe fn authorize_reclamation_after_admission<const NODES: usize>(
        &self,
        source_admission: &CloseableSnzi<NODES>,
    ) -> Result<ReclamationPermit, MigrationError> {
        let snapshot = self.snapshot()?;
        let plan = snapshot.plan.ok_or(MigrationError::WrongPhase {
            expected: MigrationPhase::Committed,
            actual: snapshot.phase,
        })?;
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
        if !source_admission.is_drained() {
            return Err(MigrationError::SourceNotDrained);
        }
        self.mark_reclaimed(plan, false)
    }

    /// Fences source reclamation with a typed-mapping close proof.
    ///
    /// # Safety
    ///
    /// `closed` must be the exact source generation in the published plan and
    /// the host must have excluded all raw mappings and nonconforming guests.
    /// [`ClosedMapping`] alone explicitly does not prove those host conditions.
    pub unsafe fn authorize_reclamation_after_mapping_close(
        &self,
        _closed: &ClosedMapping<'_>,
    ) -> Result<ReclamationPermit, MigrationError> {
        let snapshot = self.snapshot()?;
        let plan = snapshot.plan.ok_or(MigrationError::WrongPhase {
            expected: MigrationPhase::Committed,
            actual: snapshot.phase,
        })?;
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
    /// quiescence proof; it relies on the proof which preceded the persistent
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

    fn begin(&self, plan: MigrationPlan) -> Result<Migration<'_>, MigrationError> {
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
            .store(plan.source.version, Ordering::Relaxed);
        self.source_fingerprint_low
            .store(plan.source.fingerprint_low, Ordering::Relaxed);
        self.source_fingerprint_high
            .store(plan.source.fingerprint_high, Ordering::Relaxed);
        self.target_version
            .store(plan.target.version, Ordering::Relaxed);
        self.target_fingerprint_low
            .store(plan.target.fingerprint_low, Ordering::Relaxed);
        self.target_fingerprint_high
            .store(plan.target.fingerprint_high, Ordering::Relaxed);
        self.source_region_id
            .store(plan.source_region_id, Ordering::Relaxed);
        self.target_region_id
            .store(plan.target_region_id, Ordering::Relaxed);
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
            armed: true,
        })
    }

    fn load_valid_plan(&self) -> Result<MigrationPlan, MigrationError> {
        let plan = MigrationPlan {
            transaction_id: self.transaction_id.load(Ordering::Relaxed),
            source: SchemaIdentity {
                version: self.source_version.load(Ordering::Relaxed),
                fingerprint_low: self.source_fingerprint_low.load(Ordering::Relaxed),
                fingerprint_high: self.source_fingerprint_high.load(Ordering::Relaxed),
            },
            target: SchemaIdentity {
                version: self.target_version.load(Ordering::Relaxed),
                fingerprint_low: self.target_fingerprint_low.load(Ordering::Relaxed),
                fingerprint_high: self.target_fingerprint_high.load(Ordering::Relaxed),
            },
            source_region_id: self.source_region_id.load(Ordering::Relaxed),
            target_region_id: self.target_region_id.load(Ordering::Relaxed),
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

// SAFETY: every persistent field is an address-independent atomic integer.
unsafe impl FixedAddressPodValue for MigrationControl {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "migration controls must not need drop"
        );
        let mut state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-migration-control-v1",
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
            b"source_region_id",
            offset_of!(Self, source_region_id),
        );
        state = atomic_field(
            state,
            b"target_region_id",
            offset_of!(Self, target_region_id),
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
pub struct Migration<'a> {
    control: &'a MigrationControl,
    plan: MigrationPlan,
    armed: bool,
}

impl Migration<'_> {
    /// Returns the immutable migration plan.
    pub const fn plan(&self) -> MigrationPlan {
        self.plan
    }

    /// Publishes that the target is complete and has passed application checks.
    ///
    /// # Safety
    ///
    /// Every target payload byte and allocator descriptor reachable from the
    /// target root must be initialized and validated. No writer may retain an
    /// untracked mutable reference. The target must remain private until
    /// [`Self::commit`] succeeds.
    pub unsafe fn mark_target_ready(&mut self) -> Result<(), MigrationError> {
        self.control
            .transition(self.plan, MigrationPhase::Copying, COPYING, TARGET_READY)
    }

    /// Atomically makes the validated target generation authoritative.
    ///
    /// Authenticated attach/bootstrap code must consult
    /// [`MigrationControl::authoritative_generation`] and must not cache the
    /// pre-commit route across this transition.
    pub fn commit(&mut self) -> Result<CommittedMigration, MigrationError> {
        self.control.transition(
            self.plan,
            MigrationPhase::TargetReady,
            TARGET_READY,
            COMMITTED,
        )?;
        self.armed = false;
        Ok(CommittedMigration { plan: self.plan })
    }
}

impl Drop for Migration<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.control.poison_if_incomplete(self.plan.transaction_id);
        }
    }
}

/// Confirmation that target publication linearized in this process.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[must_use = "the committed plan identifies the newly authoritative generation"]
pub struct CommittedMigration {
    plan: MigrationPlan,
}

impl CommittedMigration {
    /// Returns the committed plan.
    pub const fn plan(self) -> MigrationPlan {
        self.plan
    }
}

/// Confirmation that reclamation was fenced in the persistent control record.
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
    /// Source and target attempted to use the same generation identity.
    ReusedRegionId,
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
    /// A protocol operation observed the wrong persistent phase.
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
    /// A process-local authority does not match the persistent transaction.
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
            Self::ReusedRegionId => {
                formatter.write_str("target region id must differ from source region id")
            }
            Self::VersionDidNotIncrease { source, target } => write!(
                formatter,
                "target schema version {target} did not exceed source version {source}"
            ),
            Self::UnsupportedSchema { observed, current } => write!(
                formatter,
                "schema {observed:?} is unsupported by current schema {current:?}"
            ),
            Self::SourceNotDrained => formatter.write_str("source generation is not drained"),
            Self::WrongPhase { expected, actual } => {
                write!(formatter, "expected phase {expected:?}, found {actual:?}")
            }
            Self::CorruptPhase { raw } => write!(formatter, "unknown migration phase {raw}"),
            Self::CorruptMetadata => formatter.write_str("migration metadata is corrupt"),
            Self::TransactionMismatch => {
                formatter.write_str("migration authority does not match persistent transaction")
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
    state = checksum_mix(state, plan.source.version);
    state = checksum_mix(state, plan.source.fingerprint_low);
    state = checksum_mix(state, plan.source.fingerprint_high);
    state = checksum_mix(state, plan.target.version);
    state = checksum_mix(state, plan.target.fingerprint_low);
    state = checksum_mix(state, plan.target.fingerprint_high);
    state = checksum_mix(state, plan.source_region_id);
    checksum_mix(state, plan.target_region_id)
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

    const SOURCE: SchemaIdentity = SchemaIdentity::new(1, 0x11);
    const TARGET: SchemaIdentity = SchemaIdentity::new(2, 0x22);

    fn plan() -> MigrationPlan {
        MigrationPlan::new(7, SOURCE, TARGET, 41, 42).unwrap()
    }

    #[test]
    fn published_metadata_corruption_poisons() {
        let admission = CloseableSnzi::<20>::new();
        admission.close();
        assert!(admission.is_drained());
        let control = MigrationControl::new();
        let migration = control
            .begin_after_admission_drain(&admission, plan())
            .unwrap();
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
