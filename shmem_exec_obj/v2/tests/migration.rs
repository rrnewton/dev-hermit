#![cfg(all(feature = "derive", target_os = "linux", target_has_atomic = "64"))]

use std::mem::ManuallyDrop;
use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use shmem_pod::admission::CloseableSnzi;
use shmem_pod::collections::SharedBox;
use shmem_pod::mapping::{BuildIdentity, InstanceIdentity, Owner, RawMapping};
use shmem_pod::migration::{
    AdmissionQuiescence, AuthoritativeGeneration, AuthorityIdentity, BackingIdentity,
    FailClosedSourceAuthority, GenerationIdentity, MappingQuiescence, MigrationControl,
    MigrationError, MigrationPhase, MigrationPlan, MigrationSchema, PrecommitTargetBacking,
    SchemaIdentity, SchemaNegotiation, negotiate_schema,
};
use shmem_pod::reloc_allocator::{RelocAllocator, RelocRegion};
use shmem_pod::{PodSync, PodValue};

const MAPPING_LEN: usize = 64 * 1024;
const ARENA_OFFSET: u64 = 8 * 1024;
const SLOT_SIZE: usize = 4 * 1024;
const SLOTS: usize = 8;
const TARGET_AUTHORITY: AuthorityIdentity = AuthorityIdentity::new([0xa5; 16]);

#[derive(Clone, Copy, Debug, Eq, PartialEq, shmem_pod::PodValue, shmem_pod::PodSync)]
struct AccountV1 {
    account_id: u64,
    balance_cents: u64,
}

impl MigrationSchema for AccountV1 {
    const VERSION: u64 = 1;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, shmem_pod::PodValue, shmem_pod::PodSync)]
struct AccountV2 {
    account_id: u64,
    balance_micros: u64,
    migrated_by: u64,
}

impl MigrationSchema for AccountV2 {
    const VERSION: u64 = 2;
}

struct SharedMapping {
    base: *mut u8,
    len: usize,
    descriptor: Option<libc::c_int>,
}

impl SharedMapping {
    fn anonymous(len: usize) -> Self {
        // SAFETY: creates one page-aligned, process-shared writable range.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(mapping, libc::MAP_FAILED, "anonymous mmap failed");
        Self {
            base: mapping.cast(),
            len,
            descriptor: None,
        }
    }

    fn memfd_pair(len: usize) -> (Self, Self) {
        // SAFETY: the literal is NUL-terminated and flags intentionally omit
        // CLOEXEC only because this test does not exec.
        let descriptor = unsafe { libc::memfd_create(c"shmem-pod-migration-test".as_ptr(), 0) };
        assert!(descriptor >= 0, "memfd_create failed");
        // SAFETY: the new descriptor is writable and len fits off_t here.
        assert_eq!(
            unsafe { libc::ftruncate(descriptor, len as libc::off_t) },
            0
        );
        let map_one = Self::map_descriptor(descriptor, len, Some(descriptor));
        let map_two = Self::map_descriptor(descriptor, len, None);
        assert_ne!(map_one.base, map_two.base);
        (map_one, map_two)
    }

    fn map_descriptor(descriptor: libc::c_int, len: usize, owner: Option<libc::c_int>) -> Self {
        // SAFETY: maps the complete live memfd as shared writable memory.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                descriptor,
                0,
            )
        };
        assert_ne!(mapping, libc::MAP_FAILED, "memfd mmap failed");
        Self {
            base: mapping.cast(),
            len,
            descriptor: owner,
        }
    }

    unsafe fn initialize_region<const N: usize>(&self, region_id: u64) -> RelocRegion<'_, N> {
        let allocator = self.base.cast::<RelocAllocator<N>>();
        // SAFETY: this mapping is zeroed, exclusive initialization storage and
        // the allocator fits before ARENA_OFFSET.
        unsafe { allocator.write(RelocAllocator::new()) };
        // SAFETY: the allocator is initialized within this live mapping and its
        // configured arena is aligned, disjoint, and in bounds.
        unsafe {
            (*allocator)
                .initialize(self.base, self.len, region_id, ARENA_OFFSET, SLOT_SIZE)
                .unwrap()
        }
    }

    unsafe fn attach_region<const N: usize>(&self, region_id: u64) -> RelocRegion<'_, N> {
        let allocator = self.base.cast::<RelocAllocator<N>>();
        // SAFETY: this is a second mapping of the authenticated initialized
        // memfd at the same file offset.
        unsafe { (*allocator).attach(self.base, self.len, region_id).unwrap() }
    }
}

impl Drop for SharedMapping {
    fn drop(&mut self) {
        // SAFETY: base/len name the mapping owned by this helper.
        assert_eq!(unsafe { libc::munmap(self.base.cast(), self.len) }, 0);
        if let Some(descriptor) = self.descriptor {
            // SAFETY: exactly one helper owns the descriptor.
            assert_eq!(unsafe { libc::close(descriptor) }, 0);
        }
    }
}

fn generation<T: MigrationSchema>(region: u64, sequence: u64, byte: u8) -> GenerationIdentity {
    GenerationIdentity::for_schema::<T>(region, sequence, BackingIdentity::new([byte; 32]))
}

fn migration_plan(transaction_id: u64, source_region: u64, target_region: u64) -> MigrationPlan {
    MigrationPlan::new(
        transaction_id,
        generation::<AccountV1>(source_region, 100, 0x41),
        generation::<AccountV2>(target_region, 101, 0x42),
        TARGET_AUTHORITY,
    )
    .unwrap()
}

struct RejectedTargetBacking<'a> {
    _builder_mapping: &'a SharedMapping,
    _recovery_mapping: &'a SharedMapping,
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
    private: bool,
}

// SAFETY: these fixtures are used only to verify rejection before the
// target-ready transition. Their two mappings remain private to this test.
unsafe impl PrecommitTargetBacking for RejectedTargetBacking<'_> {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        self.private
    }
}

struct RelocSourceAuthority {
    mapping: ManuallyDrop<SharedMapping>,
    root: SharedBox<AccountV1>,
    region_id: u64,
}

// SAFETY: the authority exclusively owns its mapping and root. ManuallyDrop
// makes every implicit drop leak the mapping; reclamation is explicit below.
unsafe impl FailClosedSourceAuthority for RelocSourceAuthority {}

impl RelocSourceAuthority {
    fn reclaim(mut self) -> AccountV1 {
        // SAFETY: callers obtain this value only by consuming an exact-plan
        // reclamation permit, so the owned mapping can be recovered once.
        let mapping = unsafe { ManuallyDrop::take(&mut self.mapping) };
        let mut region = unsafe { mapping.attach_region::<SLOTS>(self.region_id) };
        // SAFETY: terminal quiescence and the reclamation fence exclude every
        // source reference and duplicate descriptor.
        unsafe { self.root.destroy(&mut region) }.unwrap()
    }
}

struct RelocTargetBacking<'a> {
    region: RelocRegion<'a, SLOTS>,
    root: SharedBox<AccountV2>,
    _recovery_mapping: &'a SharedMapping,
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

// SAFETY: this value owns the only safe allocator/root mutation authority and
// the test exposes no attach route until commit. The independently mapped alias
// is retained by the recovery-authority fixture.
unsafe impl PrecommitTargetBacking for RelocTargetBacking<'_> {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        true
    }
}

struct TypedTargetBacking<'a> {
    owner: ManuallyDrop<Owner<'a, AccountV2>>,
    _recovery_mapping: &'a SharedMapping,
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

impl<'a> TypedTargetBacking<'a> {
    fn into_owner(mut self) -> Owner<'a, AccountV2> {
        // SAFETY: this consumes the post-commit backing and takes its owner once.
        unsafe { ManuallyDrop::take(&mut self.owner) }
    }
}

// SAFETY: moving this value into migration also moves the unique typed mapping
// owner. ManuallyDrop prevents implicit target poisoning if migration abandons
// or drops the capability; explicit post-commit cleanup extracts the owner.
unsafe impl PrecommitTargetBacking for TypedTargetBacking<'_> {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        true
    }
}

struct ProcessTargetBacking {
    _builder_mapping: SharedMapping,
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

// SAFETY: the mapping is the sole child-side target capability and is moved
// into migration after target initialization. The parent recovery process owns
// a distinct mapping of the same authenticated memfd.
unsafe impl PrecommitTargetBacking for ProcessTargetBacking {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        true
    }
}

struct PermitSourceAuthority(u64);

// SAFETY: this synthetic authority owns no source bytes or access path, has no
// destructor, and is safe to leak. It exists only to test permit provenance.
unsafe impl FailClosedSourceAuthority for PermitSourceAuthority {}

struct SyntheticTargetBacking {
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

// SAFETY: this synthetic target has no payload, aliases, publication path, or
// destructor. Its immutable identity answers remain stable while owned.
unsafe impl PrecommitTargetBacking for SyntheticTargetBacking {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        true
    }
}

#[test]
fn migrates_schema_then_reclaims_only_after_commit_and_drain() {
    const SOURCE_REGION: u64 = 0x1001;
    const TARGET_REGION: u64 = 0x1002;

    let source_mapping = SharedMapping::anonymous(MAPPING_LEN);
    let (target_mapping, target_alias) = SharedMapping::memfd_pair(MAPPING_LEN);
    let target_region = unsafe { target_mapping.initialize_region::<SLOTS>(TARGET_REGION) };

    let (control_mapping, control_alias) = SharedMapping::memfd_pair(4096);
    let control = control_mapping.base.cast::<MigrationControl>();
    unsafe { MigrationControl::initialize_at(control) };
    let control = unsafe { &*control };
    let control_from_other_address = unsafe { &*control_alias.base.cast::<MigrationControl>() };

    let admission = CloseableSnzi::<20>::new();
    let (source, old) = {
        let source_region = unsafe { source_mapping.initialize_region::<SLOTS>(SOURCE_REGION) };
        let source = SharedBox::new(
            &source_region,
            AccountV1 {
                account_id: 17,
                balance_cents: 1234,
            },
        )
        .unwrap();
        assert!(admission.close());
        assert!(admission.is_drained());
        let old = *source.get(&source_region).unwrap();
        (source, old)
    };
    let plan = migration_plan(0x55, SOURCE_REGION, TARGET_REGION);
    let source_authority = RelocSourceAuthority {
        mapping: ManuallyDrop::new(source_mapping),
        root: source,
        region_id: SOURCE_REGION,
    };
    // SAFETY: the terminal gate is bound to the complete plan and its unique
    // control, and moving the authority consumes the only safe source
    // region/root mutation paths.
    let source_quiescence =
        unsafe { AdmissionQuiescence::bind_with_authority(&admission, plan, source_authority) }
            .unwrap();
    let migration = control
        .begin_with_quiescent_source(source_quiescence, plan)
        .unwrap();

    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap(),
        (AuthoritativeGeneration::Source, plan)
    );
    let target = SharedBox::new(
        &target_region,
        AccountV2 {
            account_id: old.account_id,
            balance_micros: old.balance_cents * 10_000,
            migrated_by: plan.transaction_id(),
        },
    )
    .unwrap();
    let target_descriptor = target.descriptor();

    let target_backing = RelocTargetBacking {
        region: target_region,
        root: target,
        _recovery_mapping: &target_alias,
        generation: plan.target(),
        authority: plan.target_authority(),
    };
    // SAFETY: target construction completed, every descriptor resolves through
    // the builder mapping, and target_backing owns the complete safe region/root
    // mutation authority. The alias has not been converted into an attach route.
    let ready = unsafe { migration.mark_target_ready(target_backing) }.unwrap();
    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap()
            .0,
        AuthoritativeGeneration::Source
    );
    let committed = ready.commit().unwrap();
    assert_eq!(committed.plan(), plan);
    assert_eq!(committed.target().generation(), plan.target());
    assert_eq!(
        committed.target().recovery_authority(),
        plan.target_authority()
    );
    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap(),
        (AuthoritativeGeneration::Target, plan)
    );
    control.poison();
    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap(),
        (AuthoritativeGeneration::Target, plan),
        "late poison must not erase a committed route"
    );

    let target_alias_region = unsafe { target_alias.attach_region::<SLOTS>(TARGET_REGION) };
    let reconstructed = unsafe { SharedBox::<AccountV2>::from_descriptor(target_descriptor) };
    assert_eq!(
        reconstructed.get(&target_alias_region).unwrap(),
        &AccountV2 {
            account_id: 17,
            balance_micros: 12_340_000,
            migrated_by: 0x55,
        }
    );

    // SAFETY: admission is the terminal barrier bound to this unique control's
    // exact plan, and this test owns every mapping and descriptor.
    let permit =
        unsafe { control_from_other_address.authorize_reclamation(committed.source()) }.unwrap();
    assert!(!permit.is_resume());
    assert_eq!(permit.plan().source_region_id(), SOURCE_REGION);
    let (source_quiescence, target_backing) = committed.into_capabilities();
    assert_eq!(
        target_backing.root.get(&target_backing.region).unwrap(),
        reconstructed.get(&target_alias_region).unwrap()
    );
    let source_authority = match source_quiescence.into_authority(permit) {
        Ok(authority) => authority,
        Err((error, _witness)) => panic!("reclamation permit mismatch: {error}"),
    };
    assert_eq!(
        source_authority.reclaim(),
        AccountV1 {
            account_id: 17,
            balance_cents: 1234,
        }
    );

    // Reclamation state remains in the live shared control mapping so a cleanup
    // supervisor can resume after a process crash between fence and cleanup.
    let resumed = unsafe { control_from_other_address.resume_reclamation() }.unwrap();
    assert!(resumed.is_resume());
}

#[test]
fn reclamation_permit_must_match_the_complete_bound_plan() {
    fn exercise(
        bound_plan: MigrationPlan,
        foreign_plan: MigrationPlan,
        expected_transaction_match: bool,
        expected_target_match: bool,
        expected_authority_match: bool,
    ) {
        assert_eq!(bound_plan.source(), foreign_plan.source());
        assert_ne!(bound_plan, foreign_plan);
        let authority_marker = bound_plan.transaction_id() ^ 0x5a5a_5a5a_5a5a_5a5a;

        let admission = CloseableSnzi::<20>::new();
        assert!(admission.close());
        assert!(admission.is_drained());

        // SAFETY: the test's source and authority are effect-free. Constructing
        // competing controls deliberately violates the documented uniqueness
        // rule so the public API's fail-closed mismatch check can be exercised.
        let bound_source = unsafe {
            AdmissionQuiescence::bind_with_authority(
                &admission,
                bound_plan,
                PermitSourceAuthority(authority_marker),
            )
        }
        .unwrap();
        // SAFETY: as above, this is an effect-free adversarial duplicate witness.
        let foreign_source =
            unsafe { AdmissionQuiescence::bind(&admission, foreign_plan) }.unwrap();

        let bound_control = MigrationControl::new();
        let bound_migration = bound_control
            .begin_with_quiescent_source(bound_source, bound_plan)
            .unwrap();
        let bound_committed = unsafe {
            bound_migration.mark_target_ready(SyntheticTargetBacking {
                generation: bound_plan.target(),
                authority: bound_plan.target_authority(),
            })
        }
        .unwrap()
        .commit()
        .unwrap();

        let foreign_control = MigrationControl::new();
        let foreign_migration = foreign_control
            .begin_with_quiescent_source(foreign_source, foreign_plan)
            .unwrap();
        let foreign_committed = unsafe {
            foreign_migration.mark_target_ready(SyntheticTargetBacking {
                generation: foreign_plan.target(),
                authority: foreign_plan.target_authority(),
            })
        }
        .unwrap()
        .commit()
        .unwrap();

        // SAFETY: all synthetic source access is excluded. The mismatched
        // witness must be rejected before the bound control records a fence.
        let authorization_error =
            unsafe { bound_control.authorize_reclamation(foreign_committed.source()) }.unwrap_err();
        assert!(matches!(
            authorization_error,
            MigrationError::SourcePlanMismatch {
                transaction_matches,
                source_matches: true,
                target_matches,
                authority_matches,
                ..
            } if transaction_matches == expected_transaction_match
                && target_matches == expected_target_match
                && authority_matches == expected_authority_match
        ));
        assert_eq!(
            bound_control.snapshot().unwrap().phase(),
            MigrationPhase::Committed
        );

        // SAFETY: the synthetic source has no raw, inherited, or nonconforming
        // access. This obtains a real permit from the competing control.
        let foreign_permit =
            unsafe { foreign_control.authorize_reclamation(foreign_committed.source()) }.unwrap();
        let (bound_source, _bound_target) = bound_committed.into_capabilities();
        let bound_source = match bound_source.into_authority(foreign_permit) {
            Ok(_) => panic!("foreign permit released the bound source authority"),
            Err((error, source)) => {
                assert!(matches!(
                    error,
                    MigrationError::SourcePlanMismatch {
                        transaction_matches,
                        source_matches: true,
                        target_matches,
                        authority_matches,
                        ..
                    } if transaction_matches == expected_transaction_match
                        && target_matches == expected_target_match
                        && authority_matches == expected_authority_match
                ));
                source
            }
        };
        assert_eq!(bound_source.plan(), bound_plan);
        assert_eq!(
            bound_control.snapshot().unwrap().phase(),
            MigrationPhase::Committed,
            "foreign permit must not advance or release the bound transaction"
        );

        // SAFETY: this permit exactly matches the bound witness, and the
        // synthetic source has no access path. The foreign record exists only as
        // an effect-free counterexample to the external uniqueness contract.
        let bound_permit = unsafe { bound_control.authorize_reclamation(&bound_source) }.unwrap();
        assert_eq!(bound_permit.plan(), bound_plan);
        match bound_source.into_authority(bound_permit) {
            Ok(PermitSourceAuthority(returned_marker)) => {
                assert_eq!(returned_marker, authority_marker)
            }
            Err((error, _source)) => panic!("matching permit was rejected: {error}"),
        }
    }

    let bound_plan = migration_plan(0xa00, 401, 402);
    let transaction_only = MigrationPlan::new(
        0xa01,
        bound_plan.source(),
        bound_plan.target(),
        bound_plan.target_authority(),
    )
    .unwrap();
    exercise(bound_plan, transaction_only, false, true, true);

    let target_only = MigrationPlan::new(
        bound_plan.transaction_id(),
        bound_plan.source(),
        generation::<AccountV2>(403, 102, 0x43),
        bound_plan.target_authority(),
    )
    .unwrap();
    exercise(bound_plan, target_only, true, false, true);

    let authority_only = MigrationPlan::new(
        bound_plan.transaction_id(),
        bound_plan.source(),
        bound_plan.target(),
        AuthorityIdentity::new([0x99; 16]),
    )
    .unwrap();
    exercise(bound_plan, authority_only, true, true, false);
}

#[test]
fn same_source_witness_cannot_be_retargeted_before_control_claim() {
    let admission = CloseableSnzi::<20>::new();
    assert!(admission.close());
    assert!(admission.is_drained());

    let bound_plan = migration_plan(0xb00, 501, 502);
    let transaction_only = MigrationPlan::new(
        0xb01,
        bound_plan.source(),
        bound_plan.target(),
        bound_plan.target_authority(),
    )
    .unwrap();
    // SAFETY: this effect-free synthetic source is bound to `bound_plan` and its
    // unique control. The safe begin call deliberately attempts to retarget it.
    let witness = unsafe { AdmissionQuiescence::bind(&admission, bound_plan) }.unwrap();
    let control = MigrationControl::new();

    assert!(matches!(
        control.begin_with_quiescent_source(witness, transaction_only),
        Err(MigrationError::SourcePlanMismatch {
            transaction_matches: false,
            source_matches: true,
            target_matches: true,
            authority_matches: true,
            ..
        })
    ));
    assert_eq!(
        control.snapshot().unwrap().phase(),
        MigrationPhase::Uninitialized,
        "full-plan mismatch must be rejected before claiming the control"
    );
    assert!(
        admission.try_enter(0).is_err(),
        "mismatched begin consumes the witness but cannot reopen terminal admission"
    );
}

#[test]
fn active_admission_blocks_migration_until_departure_tail_finishes() {
    let admission = CloseableSnzi::<20>::new();
    let token = admission.try_enter(0).unwrap();
    assert!(admission.close());
    let control = MigrationControl::new();
    let plan = migration_plan(9, 10, 11);
    assert!(matches!(
        // SAFETY: this test binds the complete synthetic plan only to exercise
        // the not-yet-drained rejection; its control is unique.
        unsafe { AdmissionQuiescence::bind(&admission, plan) },
        Err(MigrationError::SourceNotDrained)
    ));
    token.depart().unwrap();
    assert!(admission.is_drained());
    // SAFETY: the terminal gate is the sole source access path, and the complete
    // plan identifies this test's unique control.
    let source = unsafe { AdmissionQuiescence::bind(&admission, plan) }.unwrap();
    let migration = control.begin_with_quiescent_source(source, plan).unwrap();
    drop(migration);
    assert_eq!(
        control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );
}

#[test]
fn drained_but_unrelated_source_witness_cannot_claim_plan() {
    let unrelated_gate = CloseableSnzi::<20>::new();
    assert!(unrelated_gate.close());
    assert!(unrelated_gate.is_drained());
    let plan = migration_plan(19, 31, 32);
    let unrelated = generation::<AccountV1>(99, 100, 0x99);
    let unrelated_plan = MigrationPlan::new(
        plan.transaction_id(),
        unrelated,
        plan.target(),
        plan.target_authority(),
    )
    .unwrap();
    // SAFETY: the synthetic gate is correctly bound to `unrelated_plan` and its
    // unique control; the test deliberately attempts to retarget that witness.
    let witness = unsafe { AdmissionQuiescence::bind(&unrelated_gate, unrelated_plan) }.unwrap();
    let control = MigrationControl::new();
    assert!(matches!(
        control.begin_with_quiescent_source(witness, plan),
        Err(MigrationError::SourcePlanMismatch {
            transaction_matches: true,
            source_matches: false,
            target_matches: true,
            authority_matches: true,
            ..
        })
    ));
    assert_eq!(
        control.snapshot().unwrap().phase(),
        MigrationPhase::Uninitialized,
        "identity mismatch must be rejected before claiming the record"
    );
    assert!(
        unrelated_gate.try_enter(0).is_err(),
        "terminal drain cannot resume safe admission after witness consumption"
    );

    let wrong_schema_gate = CloseableSnzi::<20>::new();
    wrong_schema_gate.close();
    assert!(wrong_schema_gate.is_drained());
    let wrong_schema = GenerationIdentity::new(
        SchemaIdentity::new(AccountV1::VERSION, 0xdead_beef),
        plan.source().region_id(),
        plan.source().sequence(),
        plan.source().backing(),
    );
    let wrong_schema_plan = MigrationPlan::new(
        plan.transaction_id(),
        wrong_schema,
        plan.target(),
        plan.target_authority(),
    )
    .unwrap();
    // SAFETY: the synthetic gate is bound to a complete plan with a distinct
    // schema identity and unique control to exercise retargeting rejection.
    let wrong_schema_witness =
        unsafe { AdmissionQuiescence::bind(&wrong_schema_gate, wrong_schema_plan) }.unwrap();
    assert!(matches!(
        control.begin_with_quiescent_source(wrong_schema_witness, plan),
        Err(MigrationError::SourcePlanMismatch {
            transaction_matches: true,
            source_matches: false,
            target_matches: true,
            authority_matches: true,
            ..
        })
    ));
    assert_eq!(
        control.snapshot().unwrap().phase(),
        MigrationPhase::Uninitialized
    );
}

#[test]
fn target_capability_must_match_and_remain_private_until_commit() {
    fn begin<'a>(
        control: &'a MigrationControl,
        admission: &'a CloseableSnzi<20>,
        plan: MigrationPlan,
    ) -> shmem_pod::migration::Migration<'a, AdmissionQuiescence<'a, 20>> {
        // SAFETY: each gate is the sole source access path, and the complete plan
        // identifies the corresponding unique control.
        let source = unsafe { AdmissionQuiescence::bind(admission, plan) }.unwrap();
        control.begin_with_quiescent_source(source, plan).unwrap()
    }

    let (target_mapping, target_recovery) = SharedMapping::memfd_pair(4096);
    let plan = migration_plan(29, 41, 42);

    let wrong_generation_gate = CloseableSnzi::<20>::new();
    wrong_generation_gate.close();
    assert!(wrong_generation_gate.is_drained());
    let wrong_generation_control = MigrationControl::new();
    let migration = begin(&wrong_generation_control, &wrong_generation_gate, plan);
    let wrong_generation = RejectedTargetBacking {
        _builder_mapping: &target_mapping,
        _recovery_mapping: &target_recovery,
        generation: generation::<AccountV2>(43, 101, 0x43),
        authority: plan.target_authority(),
        private: true,
    };
    assert!(matches!(
        unsafe { migration.mark_target_ready(wrong_generation) },
        Err(MigrationError::TargetGenerationMismatch { .. })
    ));
    assert_eq!(
        wrong_generation_control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );

    let wrong_authority_gate = CloseableSnzi::<20>::new();
    wrong_authority_gate.close();
    assert!(wrong_authority_gate.is_drained());
    let wrong_authority_control = MigrationControl::new();
    let migration = begin(&wrong_authority_control, &wrong_authority_gate, plan);
    let wrong_authority = RejectedTargetBacking {
        _builder_mapping: &target_mapping,
        _recovery_mapping: &target_recovery,
        generation: plan.target(),
        authority: AuthorityIdentity::new([0x66; 16]),
        private: true,
    };
    assert!(matches!(
        unsafe { migration.mark_target_ready(wrong_authority) },
        Err(MigrationError::TargetAuthorityMismatch { .. })
    ));
    assert_eq!(
        wrong_authority_control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );

    let published_gate = CloseableSnzi::<20>::new();
    published_gate.close();
    assert!(published_gate.is_drained());
    let published_control = MigrationControl::new();
    let migration = begin(&published_control, &published_gate, plan);
    let published = RejectedTargetBacking {
        _builder_mapping: &target_mapping,
        _recovery_mapping: &target_recovery,
        generation: plan.target(),
        authority: plan.target_authority(),
        private: false,
    };
    assert!(matches!(
        unsafe { migration.mark_target_ready(published) },
        Err(MigrationError::TargetAlreadyPublished)
    ));
    assert_eq!(
        published_control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );
}

#[test]
fn typed_mapping_close_proof_fences_reclamation() {
    const BUILD: BuildIdentity = BuildIdentity::new([0x31; 32]);
    const SOURCE_INSTANCE: InstanceIdentity = InstanceIdentity::new([0x41; 16]);
    const TARGET_INSTANCE: InstanceIdentity = InstanceIdentity::new([0x42; 16]);

    let source_bytes = SharedMapping::anonymous(4096);
    let (target_bytes, target_recovery) = SharedMapping::memfd_pair(4096);
    let source_raw = unsafe { RawMapping::from_raw_parts(source_bytes.base, 4096).unwrap() };
    let target_raw = unsafe { RawMapping::from_raw_parts(target_bytes.base, 4096).unwrap() };
    let source_mapping = source_raw.prepare(BUILD, SOURCE_INSTANCE);
    let source_owner = source_mapping
        .try_initialize(AccountV1 {
            account_id: 1,
            balance_cents: 2,
        })
        .unwrap();
    let target_owner = target_raw
        .prepare(BUILD, TARGET_INSTANCE)
        .try_initialize(AccountV2 {
            account_id: 1,
            balance_micros: 20_000,
            migrated_by: 77,
        })
        .unwrap();
    let source_draining = source_owner.begin_drain().unwrap();
    assert!(source_draining.is_drained().unwrap());
    let closed_source = source_draining.try_close().unwrap();

    let control = MigrationControl::new();
    let plan = migration_plan(77, 101, 102);
    // SAFETY: closed_source came from the AccountV1 mapping named by the complete
    // plan, the control is unique, and no raw access remains.
    let source = unsafe { MappingQuiescence::bind(closed_source, plan) }.unwrap();
    assert!(
        source_mapping.attach::<AccountV1>().is_err(),
        "consumed ClosedMapping corresponds to a lifecycle that safe attach cannot reopen"
    );
    let migration = control.begin_with_quiescent_source(source, plan).unwrap();
    let target = TypedTargetBacking {
        owner: ManuallyDrop::new(target_owner),
        _recovery_mapping: &target_recovery,
        generation: plan.target(),
        authority: plan.target_authority(),
    };
    // SAFETY: the backing owns target_owner, which is the only safe path to
    // mutable target access, and the recovery alias remains private.
    let ready = unsafe { migration.mark_target_ready(target) }.unwrap();
    let committed = ready.commit().unwrap();
    // SAFETY: the consumed mapping witness is bound to this unique control's
    // exact plan, and this test owns every raw mapping capability.
    let permit = unsafe { control.authorize_reclamation(committed.source()) }.unwrap();
    assert!(!permit.is_resume());

    let (_source, target) = committed.into_capabilities();
    target
        .into_owner()
        .begin_drain()
        .unwrap()
        .try_close()
        .unwrap();
}

#[test]
fn killed_migrator_leaves_copying_state_and_never_steals_transaction() {
    struct CrashState {
        control: MigrationControl,
        admission: CloseableSnzi<20>,
        child_started: AtomicU64,
    }

    let shared = SharedMapping::anonymous(4096);
    let state = shared.base.cast::<CrashState>();
    unsafe {
        state.write(CrashState {
            control: MigrationControl::new(),
            admission: CloseableSnzi::new(),
            child_started: AtomicU64::new(0),
        })
    };
    let state = unsafe { &*state };
    assert!(state.admission.close());
    assert!(state.admission.is_drained());
    let plan = migration_plan(88, 201, 202);

    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        // SAFETY: the shared gate is the sole source access path and the complete
        // plan identifies this fork test's unique control.
        let source = unsafe { AdmissionQuiescence::bind(&state.admission, plan) }.unwrap();
        let _migration = state
            .control
            .begin_with_quiescent_source(source, plan)
            .unwrap();
        state.child_started.store(1, Ordering::Release);
        loop {
            // SAFETY: wait until the parent deliberately kills this process in
            // the middle of a copy transaction.
            unsafe { libc::pause() };
        }
    }

    let deadline = Instant::now() + Duration::from_secs(5);
    while state.child_started.load(Ordering::Acquire) == 0 {
        assert!(Instant::now() < deadline, "child did not start migration");
        std::thread::yield_now();
    }
    // SAFETY: child is a live direct child blocked above.
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFSIGNALED(status));
    assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);
    assert_eq!(
        state.control.snapshot().unwrap().phase(),
        MigrationPhase::Copying
    );
    // SAFETY: the gate remains terminal and bound to the same complete plan and
    // control; this second witness demonstrates the control cannot be stolen.
    let source = unsafe { AdmissionQuiescence::bind(&state.admission, plan) }.unwrap();
    assert!(matches!(
        state.control.begin_with_quiescent_source(source, plan),
        Err(MigrationError::WrongPhase {
            actual: MigrationPhase::Copying,
            ..
        })
    ));
    state.control.poison();
    assert_eq!(
        state.control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );
}

#[derive(Clone, Copy)]
enum TargetCrashCut {
    TargetReady,
    Committed,
}

fn hold_capability_until_killed<T>(_capability: T) -> ! {
    loop {
        // SAFETY: the parent deliberately terminates this process after it
        // observes the selected shared control phase.
        unsafe { libc::pause() };
    }
}

fn exercise_target_crash_cut(cut: TargetCrashCut) {
    struct CrashState {
        control: MigrationControl,
        admission: CloseableSnzi<20>,
    }

    const RECOVERED_TARGET: AccountV2 = AccountV2 {
        account_id: 91,
        balance_micros: 7_654_321,
        migrated_by: 0xabc,
    };

    let shared = SharedMapping::anonymous(4096);
    let state = shared.base.cast::<CrashState>();
    unsafe {
        state.write(CrashState {
            control: MigrationControl::new(),
            admission: CloseableSnzi::new(),
        })
    };
    let state = unsafe { &*state };
    assert!(state.admission.close());
    assert!(state.admission.is_drained());

    let transaction_id = match cut {
        TargetCrashCut::TargetReady => 0x901,
        TargetCrashCut::Committed => 0x902,
    };
    let plan = migration_plan(transaction_id, 301, 302);
    let (target_builder, target_recovery) = SharedMapping::memfd_pair(4096);

    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        // The recovery alias belongs to the supervisor process. Removing the
        // child's copy leaves one child-side builder mapping to move into B.
        drop(target_recovery);
        // SAFETY: target_builder is exclusive child-side initialization storage,
        // AccountV2 is Pod, and the complete value is written before publication.
        unsafe {
            target_builder
                .base
                .cast::<AccountV2>()
                .write(RECOVERED_TARGET)
        };
        // SAFETY: the shared terminal gate is the only source path and is bound
        // to this crash test's complete plan and unique control.
        let source = unsafe { AdmissionQuiescence::bind(&state.admission, plan) }.unwrap();
        let migration = state
            .control
            .begin_with_quiescent_source(source, plan)
            .unwrap();
        let target = ProcessTargetBacking {
            _builder_mapping: target_builder,
            generation: plan.target(),
            authority: plan.target_authority(),
        };
        // SAFETY: initialization is complete, the backing owns the only child
        // mutation path, and the parent mapping is private recovery authority.
        let ready = unsafe { migration.mark_target_ready(target) }.unwrap();
        match cut {
            TargetCrashCut::TargetReady => hold_capability_until_killed(ready),
            TargetCrashCut::Committed => {
                let committed = ready.commit().unwrap();
                hold_capability_until_killed(committed);
            }
        }
    }

    // The parent keeps only the independent recovery mapping. Closing its
    // builder mapping and descriptor proves recovery does not depend on either
    // the migrator's mapping or its descriptor surviving.
    drop(target_builder);
    let expected_phase = match cut {
        TargetCrashCut::TargetReady => MigrationPhase::TargetReady,
        TargetCrashCut::Committed => MigrationPhase::Committed,
    };
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let phase = state.control.snapshot().unwrap().phase();
        if phase == expected_phase {
            break;
        }
        assert!(Instant::now() < deadline, "child did not reach crash cut");
        std::thread::yield_now();
    }

    let expected_authority = match cut {
        TargetCrashCut::TargetReady => AuthoritativeGeneration::Source,
        TargetCrashCut::Committed => AuthoritativeGeneration::Target,
    };
    assert_eq!(state.control.snapshot().unwrap().phase(), expected_phase);
    assert_eq!(
        state.control.authoritative_generation().unwrap(),
        (expected_authority, plan)
    );

    // SAFETY: child is a live direct child blocked while owning the migration
    // capability selected above.
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFSIGNALED(status));
    assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);
    assert_eq!(state.control.snapshot().unwrap().phase(), expected_phase);

    // The acquire phase read above observes the child release publication. The
    // independently mapped supervisor backing remains readable after SIGKILL.
    let recovered = unsafe { &*target_recovery.base.cast::<AccountV2>() };
    assert_eq!(recovered, &RECOVERED_TARGET);

    match cut {
        TargetCrashCut::TargetReady => {
            state.control.poison();
            assert_eq!(
                state.control.snapshot().unwrap().phase(),
                MigrationPhase::Poisoned
            );
        }
        TargetCrashCut::Committed => {
            // SAFETY: the source gate is still terminal and exactly bound to the
            // committed plan; the parent is the authenticated cleanup authority.
            let source = unsafe { AdmissionQuiescence::bind(&state.admission, plan) }.unwrap();
            let permit = unsafe { state.control.authorize_reclamation(&source) }.unwrap();
            assert!(!permit.is_resume());
            assert_eq!(
                state.control.snapshot().unwrap().phase(),
                MigrationPhase::Reclaimed
            );
        }
    }
}

#[test]
fn killed_target_ready_migrator_leaves_source_authoritative() {
    exercise_target_crash_cut(TargetCrashCut::TargetReady);
}

#[test]
fn killed_post_commit_migrator_leaves_recovery_backing_available() {
    exercise_target_crash_cut(TargetCrashCut::Committed);
}

#[test]
fn schema_negotiation_is_exact_and_plan_checks_local_freshness_evidence() {
    fn require_relocatable_shared<T: PodValue + PodSync>() {}
    require_relocatable_shared::<SchemaIdentity>();
    require_relocatable_shared::<MigrationPlan>();
    require_relocatable_shared::<MigrationControl>();

    assert_eq!(
        negotiate_schema::<AccountV2>(SchemaIdentity::of::<AccountV2>(), &[]).unwrap(),
        SchemaNegotiation::Exact
    );
    assert!(matches!(
        negotiate_schema::<AccountV2>(
            SchemaIdentity::of::<AccountV1>(),
            &[SchemaIdentity::of::<AccountV1>()]
        )
        .unwrap(),
        SchemaNegotiation::UpgradeRequired { .. }
    ));
    let wrong_layout = SchemaIdentity::new(AccountV1::VERSION, 0xdead_beef);
    assert!(matches!(
        negotiate_schema::<AccountV2>(wrong_layout, &[SchemaIdentity::of::<AccountV1>()]),
        Err(MigrationError::UnsupportedSchema { .. })
    ));
    let future = SchemaIdentity::new(3, 0xfeed_face);
    assert!(matches!(
        negotiate_schema::<AccountV2>(future, &[future]),
        Err(MigrationError::UnsupportedSchema { .. })
    ));
    assert_eq!(
        MigrationPlan::new(
            1,
            generation::<AccountV1>(7, 1, 0x10),
            generation::<AccountV2>(7, 2, 0x11),
            TARGET_AUTHORITY,
        ),
        Err(MigrationError::ReusedRegionId)
    );
    assert_eq!(
        MigrationPlan::new(
            1,
            generation::<AccountV1>(7, 9, 0x10),
            generation::<AccountV2>(8, 9, 0x11),
            TARGET_AUTHORITY,
        ),
        Err(MigrationError::GenerationDidNotIncrease {
            source: 9,
            target: 9,
        })
    );
    assert_eq!(
        MigrationPlan::new(
            1,
            generation::<AccountV1>(7, 9, 0x10),
            generation::<AccountV2>(8, 10, 0x10),
            TARGET_AUTHORITY,
        ),
        Err(MigrationError::ReusedBackingIdentity)
    );
}
