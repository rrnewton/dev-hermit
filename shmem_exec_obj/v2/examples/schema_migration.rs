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
use shmem_pod::migration::{MigrationControl, MigrationPlan, MigrationSchema};
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
use shmem_pod::reloc_allocator::{RelocAllocator, RelocRegion};

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
const BYTES: usize = 64 * 1024;
#[cfg(all(feature = "derive", target_has_atomic = "64"))]
const SLOTS: usize = 4;

#[cfg(all(feature = "derive", target_has_atomic = "64"))]
#[repr(align(4096))]
struct Pages([u8; BYTES]);

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
        (&*allocator)
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
    let mut source_region = unsafe { initialize_region(&mut source_pages, SOURCE_REGION) };
    let target_region = unsafe { initialize_region(&mut target_pages, TARGET_REGION) };

    let mut source = SharedBox::new(
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

    let plan = MigrationPlan::for_schemas::<CounterV1, CounterV2>(77, SOURCE_REGION, TARGET_REGION)
        .unwrap();
    let control = MigrationControl::new();
    let mut migration = control
        .begin_after_admission_drain(&admission, plan)
        .unwrap();

    let old = *source.get(&source_region).unwrap();
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

    // SAFETY: every target root is initialized and resolved successfully, and
    // no writer or bootstrap path can expose target_pages before commit.
    unsafe { migration.mark_target_ready() }.unwrap();
    let _committed = migration.commit().unwrap();

    // SAFETY: this is the exact source barrier, it is terminally drained, and
    // this example owns every source descriptor and byte range.
    let _permit = unsafe { control.authorize_reclamation_after_admission(&admission) }.unwrap();
    // SAFETY: reclamation was fenced and no other source reference exists.
    let retired = unsafe { source.destroy(&mut source_region) }.unwrap();

    println!(
        "PASS schema_migration attempts={} retired_successes={}",
        target.get(&target_region).unwrap().attempts,
        retired.successes
    );
}

#[cfg(not(all(feature = "derive", target_has_atomic = "64")))]
fn main() {
    eprintln!("schema_migration requires the derive feature and 64-bit atomics");
}
