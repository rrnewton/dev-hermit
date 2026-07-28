//! Allocate pointer-free `SharedBox` and `SharedVec` descriptors in shared pages.

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use std::ptr;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::collections::{SharedBox, SharedVec};
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::reloc_allocator::{RELOC_SLOT_ALIGNMENT, RelocAllocator};

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
fn align_up(value: usize, alignment: usize) -> usize {
    value.checked_add(alignment - 1).unwrap() & !(alignment - 1)
}

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
fn main() {
    const MAPPING_LEN: usize = 32 * 1024;
    const REGION_ID: u64 = 0x7265_6c6f_635f_0001;
    type Allocator = RelocAllocator<8>;

    // SAFETY: create one shared writable mapping for allocator metadata and arena.
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
    let allocator_pointer = base.cast::<Allocator>();
    // SAFETY: this process exclusively constructs the control object once.
    unsafe { allocator_pointer.write(Allocator::new()) };
    // SAFETY: the object remains live until the mapping is unmapped.
    let allocator = unsafe { &*allocator_pointer };
    let arena_offset = align_up(std::mem::size_of::<Allocator>(), RELOC_SLOT_ALIGNMENT);
    // SAFETY: geometry is live, writable, disjoint, and exclusively initialized.
    let mut region = unsafe {
        allocator
            .initialize(base, MAPPING_LEN, REGION_ID, arena_offset as u64, 256)
            .unwrap()
    };

    let mut calls = SharedBox::new(&region, AtomicU64::new(0)).unwrap();
    let mut labels = SharedVec::with_capacity(&region, 4).unwrap();
    for label in [10_u64, 20, 30, 40] {
        // SAFETY: setup is single-process and holds exclusive vector access.
        unsafe { labels.push(&mut region, label) }.unwrap();
    }

    // The descriptors contain offsets rather than pointers. A fork is used here
    // only to keep the example short; independent exec attachers may map the
    // backing file elsewhere and call RelocAllocator::attach with their base.
    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        calls.get(&region).unwrap().fetch_add(1, Ordering::Relaxed);
        assert_eq!(labels.as_slice(&region).unwrap(), &[10, 20, 30, 40]);
        unsafe { libc::_exit(0) };
    }
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status));
    assert_eq!(libc::WEXITSTATUS(status), 0);
    assert_eq!(calls.get(&region).unwrap().load(Ordering::Relaxed), 1);

    // The surrounding lifecycle has reaped the only guest, so exclusive
    // destruction is now established.
    unsafe { labels.destroy(&mut region) }.unwrap();
    let calls = unsafe { calls.destroy(&mut region) }.unwrap();
    assert_eq!(calls.load(Ordering::Relaxed), 1);
    assert_eq!(allocator.snapshot().allocated(), 0);
    // SAFETY: all resolved references and allocation descriptors are inactive.
    assert_eq!(unsafe { libc::munmap(base.cast(), MAPPING_LEN) }, 0);
    println!("PASS relocatable_collections calls=1 labels=4");
}

#[cfg(not(all(target_os = "linux", target_has_atomic = "64")))]
fn main() {
    eprintln!("relocatable_collections requires Linux and 64-bit atomics");
}
