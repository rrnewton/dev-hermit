//! Upgrade one drained schema into a fresh allocator generation.
//!
//! The example uses page-aligned local byte arrays so the protocol is easy to
//! inspect. A real host places the same control and generation objects in
//! authenticated shared mappings and publishes the target file descriptor only
//! after `commit`.

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
use shmem_pod::admission::CloseableSnzi;
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
use shmem_pod::collections::SharedBox;
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
use shmem_pod::migration::{
    AdmissionQuiescence, AuthorityIdentity, BackingIdentity, GenerationIdentity, MigrationControl,
    MigrationPlan, MigrationSchema, PrecommitTargetBacking,
};
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
use shmem_pod::reloc_allocator::{RelocAllocator, RelocRegion};

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
const BYTES: usize = 64 * 1024;
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
const SLOTS: usize = 4;

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
#[repr(align(4096))]
struct Pages([u8; BYTES]);

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
struct SourceAuthority<'mapping> {
    region: RelocRegion<'mapping, SLOTS>,
    root: SharedBox<CounterV1>,
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
struct LocalTargetBacking<'mapping> {
    region: RelocRegion<'mapping, SLOTS>,
    root: SharedBox<CounterV2>,
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

// SAFETY: this single-process example moves the complete target region and root
// authority into the backing and exposes no attach path. It models the named
// supervisor as retaining the page owner; a production host must instead wrap
// an independently live authenticated memfd/shm handle.
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
unsafe impl PrecommitTargetBacking for LocalTargetBacking<'_> {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        self.region.len() == BYTES
    }
}

// The payload schemas intentionally use native Rust layout. Their exact
// compiled layouts are carried by PodValue fingerprints.
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
#[derive(Clone, Copy, shmem_pod::PodValue, shmem_pod::PodSync)]
struct CounterV1 {
    successes: u64,
    failures: u64,
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
impl MigrationSchema for CounterV1 {
    const VERSION: u64 = 1;
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
#[derive(Clone, Copy, shmem_pod::PodValue, shmem_pod::PodSync)]
struct CounterV2 {
    attempts: u64,
    successes: u64,
    failures: u64,
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
impl MigrationSchema for CounterV2 {
    const VERSION: u64 = 2;
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
unsafe fn initialize_region(pages: &mut Pages, region_id: u64) -> RelocRegion<'_, SLOTS> {
    let base = pages.0.as_mut_ptr();
    let allocator = base.cast::<RelocAllocator<SLOTS>>();
    // SAFETY: pages are exclusive zeroed final storage, and the allocator lies
    // before the arena which begins at byte 4096.
    unsafe { allocator.write(RelocAllocator::new()) };
    // SAFETY: the page array remains live, the arena is aligned/in bounds, and
    // this is the unique initialization of its allocator control object.
    unsafe {
        (*allocator)
            .initialize(base, BYTES, region_id, 4096, 4096)
            .unwrap()
    }
}

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
fn main() {
    const SOURCE_REGION: u64 = 1001;
    const TARGET_REGION: u64 = 1002;

    let mut source_pages = Pages([0; BYTES]);
    let mut target_pages = Pages([0; BYTES]);
    let source_region = unsafe { initialize_region(&mut source_pages, SOURCE_REGION) };
    let target_region = unsafe { initialize_region(&mut target_pages, TARGET_REGION) };

    let source = SharedBox::new(
        &source_region,
        CounterV1 {
            successes: 90,
            failures: 10,
        },
    )
    .unwrap();

    let admission = CloseableSnzi::<20>::new();
    assert!(admission.close());
    assert!(admission.is_drained());
    let old = *source.get(&source_region).unwrap();

    let source_generation = GenerationIdentity::for_schema::<CounterV1>(
        SOURCE_REGION,
        100,
        BackingIdentity::new([0x31; 32]),
    );
    let target_generation = GenerationIdentity::for_schema::<CounterV2>(
        TARGET_REGION,
        101,
        BackingIdentity::new([0x32; 32]),
    );
    let target_authority = AuthorityIdentity::new([0x55; 16]);
    let plan =
        MigrationPlan::new(77, source_generation, target_generation, target_authority).unwrap();
    let control = MigrationControl::new();
    let source_authority = SourceAuthority {
        region: source_region,
        root: source,
    };
    // SAFETY: the gate is terminal and bound to the exact source generation.
    // Moving source_authority consumes the remaining safe allocator and root
    // access paths, so only the staged process-local copy can be read below.
    let source_quiescence = unsafe {
        AdmissionQuiescence::bind_with_authority(&admission, source_generation, source_authority)
    }
    .unwrap();
    let migration = control
        .begin_with_quiescent_source(source_quiescence, plan)
        .unwrap();

    let target = SharedBox::new(
        &target_region,
        CounterV2 {
            attempts: old.successes + old.failures,
            successes: old.successes,
            failures: old.failures,
        },
    )
    .unwrap();
    assert_eq!(target.get(&target_region).unwrap().attempts, 100);

    let target_backing = LocalTargetBacking {
        region: target_region,
        root: target,
        generation: target_generation,
        authority: target_authority,
    };
    // SAFETY: every target root is initialized and resolved successfully, all
    // writes completed in this thread, and target_backing now owns the only safe
    // target region/root mutation authority. No bootstrap path can expose the
    // target before commit.
    let ready = unsafe { migration.mark_target_ready(target_backing) }.unwrap();
    let committed = ready.commit().unwrap();

    // SAFETY: this is the exact source barrier, it is terminally drained, and
    // this example owns every source descriptor and byte range.
    let permit = unsafe { control.authorize_reclamation(committed.source()) }.unwrap();
    let (source_quiescence, target_backing) = committed.into_capabilities();
    let mut source_authority = source_quiescence.into_authority(permit).unwrap();
    // SAFETY: reclamation was fenced and no other source reference exists.
    let retired = unsafe { source_authority.root.destroy(&mut source_authority.region) }.unwrap();

    println!(
        "PASS schema_migration attempts={} retired_successes={}",
        target_backing
            .root
            .get(&target_backing.region)
            .unwrap()
            .attempts,
        retired.successes
    );
}

#[cfg(not(all(feature = "derive", target_has_atomic = "64")))]
fn main() {
    eprintln!("schema_migration requires the derive feature and 64-bit atomics");
}
