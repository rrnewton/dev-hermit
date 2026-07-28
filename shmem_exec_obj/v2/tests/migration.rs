#![cfg(all(feature = "derive", target_os = "linux", target_has_atomic = "64"))]

use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use shmem_pod::admission::CloseableSnzi;
use shmem_pod::collections::SharedBox;
use shmem_pod::mapping::{BuildIdentity, InstanceIdentity, RawMapping};
use shmem_pod::migration::{
    AuthoritativeGeneration, MigrationControl, MigrationError, MigrationPhase, MigrationPlan,
    MigrationSchema, SchemaIdentity, SchemaNegotiation, negotiate_schema,
};
use shmem_pod::reloc_allocator::{RelocAllocator, RelocRegion};

const MAPPING_LEN: usize = 64 * 1024;
const ARENA_OFFSET: u64 = 8 * 1024;
const SLOT_SIZE: usize = 4 * 1024;
const SLOTS: usize = 8;

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
            (&*allocator)
                .initialize(self.base, self.len, region_id, ARENA_OFFSET, SLOT_SIZE)
                .unwrap()
        }
    }

    unsafe fn attach_region<const N: usize>(&self, region_id: u64) -> RelocRegion<'_, N> {
        let allocator = self.base.cast::<RelocAllocator<N>>();
        // SAFETY: this is a second mapping of the authenticated initialized
        // memfd at the same file offset.
        unsafe {
            (&*allocator)
                .attach(self.base, self.len, region_id)
                .unwrap()
        }
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

fn migration_plan(transaction_id: u64, source_region: u64, target_region: u64) -> MigrationPlan {
    MigrationPlan::new(
        transaction_id,
        SchemaIdentity::of::<AccountV1>(),
        SchemaIdentity::of::<AccountV2>(),
        source_region,
        target_region,
    )
    .unwrap()
}

#[test]
fn migrates_schema_then_reclaims_only_after_commit_and_drain() {
    const SOURCE_REGION: u64 = 0x1001;
    const TARGET_REGION: u64 = 0x1002;

    let source_mapping = SharedMapping::anonymous(MAPPING_LEN);
    let mut source_region = unsafe { source_mapping.initialize_region::<SLOTS>(SOURCE_REGION) };
    let mut source = SharedBox::new(
        &source_region,
        AccountV1 {
            account_id: 17,
            balance_cents: 1234,
        },
    )
    .unwrap();

    let (target_mapping, target_alias) = SharedMapping::memfd_pair(MAPPING_LEN);
    let target_region = unsafe { target_mapping.initialize_region::<SLOTS>(TARGET_REGION) };
    let target_alias_region = unsafe { target_alias.attach_region::<SLOTS>(TARGET_REGION) };

    let (control_mapping, control_alias) = SharedMapping::memfd_pair(4096);
    let control = control_mapping.base.cast::<MigrationControl>();
    unsafe { MigrationControl::initialize_at(control) };
    let control = unsafe { &*control };
    let control_from_other_address = unsafe { &*control_alias.base.cast::<MigrationControl>() };

    let admission = CloseableSnzi::<5>::new();
    assert!(admission.close());
    assert!(admission.is_drained());
    let plan = migration_plan(0x55, SOURCE_REGION, TARGET_REGION);
    let mut migration = control
        .begin_after_admission_drain(&admission, plan)
        .unwrap();

    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap(),
        (AuthoritativeGeneration::Source, plan)
    );
    let old = *source.get(&source_region).unwrap();
    let target = SharedBox::new(
        &target_region,
        AccountV2 {
            account_id: old.account_id,
            balance_micros: old.balance_cents * 10_000,
            migrated_by: plan.transaction_id(),
        },
    )
    .unwrap();

    unsafe { migration.mark_target_ready() }.unwrap();
    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap()
            .0,
        AuthoritativeGeneration::Source
    );
    let committed = migration.commit().unwrap();
    assert_eq!(committed.plan(), plan);
    assert_eq!(
        control_from_other_address
            .authoritative_generation()
            .unwrap(),
        (AuthoritativeGeneration::Target, plan)
    );

    let reconstructed = unsafe { SharedBox::<AccountV2>::from_descriptor(target.descriptor()) };
    assert_eq!(
        reconstructed.get(&target_alias_region).unwrap(),
        &AccountV2 {
            account_id: 17,
            balance_micros: 12_340_000,
            migrated_by: 0x55,
        }
    );

    // SAFETY: admission is the exact source barrier, it is terminally drained,
    // and this test owns every mapping and descriptor.
    let permit =
        unsafe { control_from_other_address.authorize_reclamation_after_admission(&admission) }
            .unwrap();
    assert!(!permit.is_resume());
    assert_eq!(permit.plan().source_region_id(), SOURCE_REGION);
    // SAFETY: the permit follows terminal drain, and this process owns the only
    // source descriptor and mapping.
    assert_eq!(
        unsafe { source.destroy(&mut source_region) }.unwrap(),
        AccountV1 {
            account_id: 17,
            balance_cents: 1234,
        }
    );

    // Reclamation state is persistent so a cleanup supervisor can resume after
    // a crash between the fence and munmap/truncate.
    let resumed =
        unsafe { control_from_other_address.authorize_reclamation_after_admission(&admission) }
            .unwrap();
    assert!(resumed.is_resume());
}

#[test]
fn active_admission_blocks_migration_until_departure_tail_finishes() {
    let admission = CloseableSnzi::<5>::new();
    let token = admission.try_enter(0).unwrap();
    assert!(admission.close());
    let control = MigrationControl::new();
    let plan = migration_plan(9, 10, 11);
    assert!(matches!(
        control.begin_after_admission_drain(&admission, plan),
        Err(MigrationError::SourceNotDrained)
    ));
    token.depart().unwrap();
    assert!(admission.is_drained());
    let migration = control
        .begin_after_admission_drain(&admission, plan)
        .unwrap();
    drop(migration);
    assert_eq!(
        control.snapshot().unwrap().phase(),
        MigrationPhase::Poisoned
    );
}

#[test]
fn typed_mapping_close_proof_fences_reclamation() {
    const BUILD: BuildIdentity = BuildIdentity::new([0x31; 32]);
    const SOURCE_INSTANCE: InstanceIdentity = InstanceIdentity::new([0x41; 16]);
    const TARGET_INSTANCE: InstanceIdentity = InstanceIdentity::new([0x42; 16]);

    let source_bytes = SharedMapping::anonymous(4096);
    let target_bytes = SharedMapping::anonymous(4096);
    let source_raw = unsafe { RawMapping::from_raw_parts(source_bytes.base, 4096).unwrap() };
    let target_raw = unsafe { RawMapping::from_raw_parts(target_bytes.base, 4096).unwrap() };
    let source_mapping = source_raw.prepare(BUILD, SOURCE_INSTANCE);
    let target_mapping = target_raw.prepare(BUILD, TARGET_INSTANCE);
    let source_owner = source_mapping
        .try_initialize(AccountV1 {
            account_id: 1,
            balance_cents: 2,
        })
        .unwrap();
    let target_owner = target_mapping
        .try_initialize(AccountV2 {
            account_id: 1,
            balance_micros: 20_000,
            migrated_by: 77,
        })
        .unwrap();
    let source_draining = source_owner.begin_drain().unwrap();
    assert!(source_draining.is_drained().unwrap());

    let control = MigrationControl::new();
    let mut migration = control
        .begin_after_mapping_drain(&source_draining, migration_plan(77, 101, 102))
        .unwrap();
    unsafe { migration.mark_target_ready() }.unwrap();
    migration.commit().unwrap();
    let closed_source = source_draining.try_close().unwrap();
    // SAFETY: closed_source is the exact source mapping, and this test owns all
    // raw mapping capabilities.
    let permit =
        unsafe { control.authorize_reclamation_after_mapping_close(closed_source) }.unwrap();
    assert!(!permit.is_resume());

    target_owner.begin_drain().unwrap().try_close().unwrap();
}

#[test]
fn killed_migrator_leaves_copying_state_and_never_steals_transaction() {
    struct CrashState {
        control: MigrationControl,
        admission: CloseableSnzi<5>,
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
        let _migration = state
            .control
            .begin_after_admission_drain(&state.admission, plan)
            .unwrap();
        state.child_started.store(1, Ordering::Release);
        // SAFETY: deliberately skips Rust destructors to model process death in
        // the middle of a copy transaction.
        unsafe { libc::_exit(0) };
    }

    let deadline = Instant::now() + Duration::from_secs(5);
    while state.child_started.load(Ordering::Acquire) == 0 {
        assert!(Instant::now() < deadline, "child did not start migration");
        std::thread::yield_now();
    }
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status));
    assert_eq!(
        state.control.snapshot().unwrap().phase(),
        MigrationPhase::Copying
    );
    assert!(matches!(
        state
            .control
            .begin_after_admission_drain(&state.admission, plan),
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

#[test]
fn schema_negotiation_is_exact_and_plan_rejects_aba_ids() {
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
    assert_eq!(
        MigrationPlan::new(
            1,
            SchemaIdentity::of::<AccountV1>(),
            SchemaIdentity::of::<AccountV2>(),
            7,
            7,
        ),
        Err(MigrationError::ReusedRegionId)
    );
}
