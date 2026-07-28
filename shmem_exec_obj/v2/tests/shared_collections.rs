#![cfg(all(target_os = "linux", target_has_atomic = "64"))]

use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};

use shmem_pod::collections::{SharedBox, SharedVec};
use shmem_pod::reloc_allocator::{
    RelocAllocator, RelocError, RelocRegion, RELOC_SLOT_ALIGNMENT,
};

const MAPPING_LEN: usize = 32 * 1024;
const REGION_ID: u64 = 0x51a7_edc0_11ec_7101;
const SLOT_SIZE: usize = 256;
type Allocator = RelocAllocator<8>;

fn align_up(value: usize, alignment: usize) -> usize {
    value.checked_add(alignment - 1).unwrap() & !(alignment - 1)
}

struct Fixture {
    base: *mut u8,
}

impl Fixture {
    fn new() -> Self {
        // SAFETY: create one shared writable mapping owned by this helper.
        let base = unsafe {
            libc::mmap(
                ptr::null_mut(),
                MAPPING_LEN,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(base, libc::MAP_FAILED);
        let base = base.cast::<u8>();
        // SAFETY: one-time allocator construction in exclusive mapping bytes.
        unsafe { base.cast::<Allocator>().write(Allocator::new()) };
        Self { base }
    }

    fn region(&self) -> RelocRegion<'_, 8> {
        let allocator = unsafe { &*self.base.cast::<Allocator>() };
        let arena = align_up(std::mem::size_of::<Allocator>(), RELOC_SLOT_ALIGNMENT);
        // SAFETY: the fixture owns one live mapping and calls this once per test.
        unsafe {
            allocator
                .initialize(
                    self.base,
                    MAPPING_LEN,
                    REGION_ID,
                    arena as u64,
                    SLOT_SIZE,
                )
                .unwrap()
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // SAFETY: fixture owns the complete live mapping.
        assert_eq!(unsafe { libc::munmap(self.base.cast(), MAPPING_LEN) }, 0);
    }
}

#[test]
fn shared_box_and_vec_use_checked_explicit_lifecycles() {
    assert!(!std::mem::needs_drop::<SharedBox<u64>>());
    assert!(!std::mem::needs_drop::<SharedVec<u64>>());
    let fixture = Fixture::new();
    let mut region = fixture.region();

    let mut boxed = SharedBox::new(&region, 41_u64).unwrap();
    assert_eq!(*boxed.get(&region).unwrap(), 41);
    unsafe { *boxed.get_mut(&mut region).unwrap() += 1 };
    assert_eq!(*boxed.get(&region).unwrap(), 42);
    assert_eq!(unsafe { boxed.destroy(&mut region) }.unwrap(), 42);
    assert!(matches!(boxed.get(&region), Err(RelocError::Empty)));

    let mut values = SharedVec::with_capacity(&region, 4).unwrap();
    for value in [10_u64, 20, 30, 40] {
        unsafe { values.push(&mut region, value) }.unwrap();
    }
    assert_eq!(values.as_slice(&region).unwrap(), &[10, 20, 30, 40]);
    assert!(matches!(
        unsafe { values.push(&mut region, 50) },
        Err(RelocError::CapacityExceeded)
    ));
    unsafe { values.as_mut_slice(&mut region).unwrap()[1] = 25 };
    assert_eq!(unsafe { values.pop(&mut region) }.unwrap(), Some(40));
    assert_eq!(values.as_slice(&region).unwrap(), &[10, 25, 30]);
    unsafe { values.destroy(&mut region) }.unwrap();
    assert!(values.is_empty());
    assert_eq!(values.capacity(), 0);
}

#[test]
fn overflow_capacity_corruption_and_stale_vector_are_rejected() {
    let fixture = Fixture::new();
    let mut region = fixture.region();
    assert!(matches!(
        SharedVec::<u64>::with_capacity(&region, usize::MAX),
        Err(RelocError::LengthOverflow)
    ));
    assert!(matches!(
        SharedVec::<u64>::with_capacity(&region, SLOT_SIZE / 8 + 1),
        Err(RelocError::AllocationTooLarge { .. })
    ));

    let mut values = SharedVec::with_capacity(&region, 4).unwrap();
    unsafe { values.push(&mut region, 1_u64) }.unwrap();
    let raw = values.raw_parts();
    // SAFETY: deliberately corrupt logical length for checked rejection.
    let corrupt = unsafe { SharedVec::<u64>::from_raw_parts(raw.0, 5, 4) };
    assert!(matches!(
        corrupt.as_slice(&region),
        Err(RelocError::LengthOverflow)
    ));
    unsafe { values.destroy(&mut region) }.unwrap();

    // SAFETY: emulates an old persisted descriptor after explicit destruction.
    let stale = unsafe { SharedVec::<u64>::from_raw_parts(raw.0, raw.1, raw.2) };
    assert!(matches!(
        stale.as_slice(&region),
        Err(RelocError::StaleGeneration { .. })
    ));
}

#[test]
fn forked_processes_update_atomic_vector_elements() {
    const WORKERS: usize = 6;
    const ELEMENTS: usize = 8;
    const ITERATIONS: usize = 2_000;

    let fixture = Fixture::new();
    let mut region = fixture.region();
    let mut values = SharedVec::with_capacity(&region, ELEMENTS).unwrap();
    for _ in 0..ELEMENTS {
        unsafe { values.push(&mut region, AtomicU64::new(0)) }.unwrap();
    }

    let mut children = Vec::new();
    for worker in 0..WORKERS {
        // SAFETY: child performs only atomic element access, then _exit without
        // dropping inherited logical ownership descriptors.
        let child = unsafe { libc::fork() };
        assert!(child >= 0);
        if child == 0 {
            let slice = values.as_slice(&region).unwrap();
            for iteration in 0..ITERATIONS {
                slice[(worker + iteration) % ELEMENTS].fetch_add(1, Ordering::Relaxed);
            }
            unsafe { libc::_exit(0) };
        }
        children.push(child);
    }
    for child in children {
        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }
    let total: u64 = values
        .as_slice(&region)
        .unwrap()
        .iter()
        .map(|value| value.load(Ordering::Relaxed))
        .sum();
    assert_eq!(total, (WORKERS * ITERATIONS) as u64);
    unsafe { values.destroy(&mut region) }.unwrap();
}
