#![cfg(feature = "fixed-allocator")]

use allocator_api2::alloc::{Allocator, Layout};
use allocator_api2::vec::Vec;
use core::mem::size_of;
use core::ptr::NonNull;
use core::sync::atomic::{AtomicUsize, Ordering};
use shmem_pod::fixed_allocator::{FixedRegionAllocator, FixedRegionError, FixedRegionState};
use shmem_pod::{FixedAddressPodValue, PodSync};

fn require_fixed_sync<T: FixedAddressPodValue + PodSync>() {}

struct MappedRegion {
    base: NonNull<u8>,
    size: usize,
}

impl MappedRegion {
    fn page_size() -> usize {
        let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
        assert!(page_size > 0);
        let page_size = page_size as usize;
        assert!(page_size.is_power_of_two());
        page_size
    }

    fn new(minimum_size: usize) -> Self {
        let page_size = Self::page_size();
        let size = minimum_size.div_ceil(page_size) * page_size;
        let base = unsafe {
            libc::mmap(
                core::ptr::null_mut(),
                size,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(base, libc::MAP_FAILED);
        Self {
            base: NonNull::new(base.cast()).unwrap(),
            size,
        }
    }

    fn base(&self) -> *mut u8 {
        self.base.as_ptr()
    }

    fn size(&self) -> usize {
        self.size
    }
}

impl Drop for MappedRegion {
    fn drop(&mut self) {
        let result = unsafe { libc::munmap(self.base.as_ptr().cast(), self.size) };
        assert_eq!(result, 0);
    }
}

#[test]
fn vec_buffer_grows_and_shrinks_inside_mapped_pages() {
    require_fixed_sync::<FixedRegionAllocator>();
    assert_ne!(FixedRegionAllocator::FINGERPRINT, 0);
    let mapping = MappedRegion::new(256 * 1024);
    let allocator = FixedRegionAllocator::new();
    let handle = unsafe { allocator.initialize(mapping.base(), mapping.size()) }.unwrap();

    let mut values = Vec::new_in(handle);
    values.try_reserve(64).unwrap();
    values.extend(0..8_192_u32);
    assert_eq!(values[4_096], 4_096);
    assert!(
        handle
            .region()
            .contains(values.as_ptr().cast(), values.capacity() * size_of::<u32>())
    );

    values.truncate(32);
    values.shrink_to_fit();
    assert_eq!(values.len(), 32);
    assert!(values.iter().copied().eq(0..32));
    assert!(
        handle
            .region()
            .contains(values.as_ptr().cast(), values.capacity() * size_of::<u32>())
    );
    drop(values);

    assert_eq!(allocator.state(), FixedRegionState::Ready);
    assert_eq!(allocator.region().unwrap().base().as_ptr(), mapping.base());
}

#[test]
fn allocator_delegates_allocate_grow_shrink_and_deallocate() {
    let mapping = MappedRegion::new(128 * 1024);
    let allocator = FixedRegionAllocator::new();
    let handle = unsafe { allocator.initialize(mapping.base(), mapping.size()) }.unwrap();

    let small = Layout::from_size_align(128, 64).unwrap();
    let allocation = handle.allocate(small).unwrap();
    assert_eq!(allocation.len(), small.size());
    let mut pointer = allocation.cast::<u8>();
    assert!(handle.region().contains(pointer.as_ptr(), allocation.len()));
    unsafe { pointer.as_ptr().write_bytes(0x5a, small.size()) };

    let large = Layout::from_size_align(4_096, 64).unwrap();
    let grown = unsafe { handle.grow(pointer, small, large) }.unwrap();
    assert_eq!(grown.len(), large.size());
    pointer = grown.cast::<u8>();
    assert!(handle.region().contains(pointer.as_ptr(), grown.len()));
    for offset in 0..small.size() {
        assert_eq!(unsafe { pointer.as_ptr().add(offset).read() }, 0x5a);
    }

    let reduced = Layout::from_size_align(96, 64).unwrap();
    let shrunk = unsafe { handle.shrink(pointer, large, reduced) }.unwrap();
    assert_eq!(shrunk.len(), reduced.size());
    pointer = shrunk.cast::<u8>();
    assert!(handle.region().contains(pointer.as_ptr(), shrunk.len()));
    unsafe { handle.deallocate(pointer, reduced) };

    let zero = Layout::from_size_align(0, 4_096).unwrap();
    let dangling = handle.allocate(zero).unwrap().cast::<u8>();
    assert_eq!(dangling.as_ptr() as usize % zero.align(), 0);
    unsafe { handle.deallocate(dangling, zero) };

    let allocation = handle.allocate(small).unwrap().cast::<u8>();
    let dangling = unsafe { handle.shrink(allocation, small, zero) }
        .unwrap()
        .cast::<u8>();
    assert_eq!(dangling.as_ptr() as usize % zero.align(), 0);

    let zeroed_layout = Layout::from_size_align(257, 128).unwrap();
    let zeroed = handle.allocate_zeroed(zeroed_layout).unwrap();
    assert_eq!(zeroed.len(), zeroed_layout.size());
    assert!(unsafe { zeroed.as_ref() }.iter().all(|byte| *byte == 0));
    unsafe { handle.deallocate(zeroed.cast(), zeroed_layout) };

    let old_layout = Layout::from_size_align(64, 8).unwrap();
    let old = handle.allocate(old_layout).unwrap().cast::<u8>();
    unsafe { old.as_ptr().write_bytes(0xa5, old_layout.size()) };
    let realigned_layout = Layout::from_size_align(512, 256).unwrap();
    let realigned = unsafe { handle.grow_zeroed(old, old_layout, realigned_layout) }.unwrap();
    assert_eq!(realigned.len(), realigned_layout.size());
    assert_eq!(
        realigned.as_ptr() as *mut u8 as usize % realigned_layout.align(),
        0
    );
    let realigned_bytes = unsafe { realigned.as_ref() };
    assert!(
        realigned_bytes[..old_layout.size()]
            .iter()
            .all(|byte| *byte == 0xa5)
    );
    assert!(
        realigned_bytes[old_layout.size()..]
            .iter()
            .all(|byte| *byte == 0)
    );
    unsafe { handle.deallocate(realigned.cast(), realigned_layout) };

    let old_zero = Layout::from_size_align(0, 8).unwrap();
    let old_dangling = handle.allocate(old_zero).unwrap().cast::<u8>();
    let from_zero_layout = Layout::from_size_align(32, 256).unwrap();
    let from_zero = unsafe { handle.grow(old_dangling, old_zero, from_zero_layout) }.unwrap();
    assert_eq!(from_zero.len(), from_zero_layout.size());
    assert_eq!(
        from_zero.as_ptr() as *mut u8 as usize % from_zero_layout.align(),
        0
    );
    unsafe { handle.deallocate(from_zero.cast(), from_zero_layout) };
}

#[test]
fn exact_attach_and_double_initialization_are_enforced() {
    let mapping = MappedRegion::new(64 * 1024);
    let allocator = FixedRegionAllocator::new();
    unsafe { allocator.initialize(mapping.base(), mapping.size()) }.unwrap();

    assert!(matches!(
        unsafe { allocator.initialize(mapping.base(), mapping.size()) },
        Err(FixedRegionError::AlreadyInitialized)
    ));
    assert!(unsafe { allocator.attach(mapping.base(), mapping.size()) }.is_ok());

    let shifted_base = unsafe { mapping.base().add(FixedRegionAllocator::REGION_ALIGNMENT) };
    let shifted_size = mapping.size() - FixedRegionAllocator::REGION_ALIGNMENT;
    assert!(matches!(
        unsafe { allocator.attach(shifted_base, shifted_size) },
        Err(FixedRegionError::RegionMismatch { .. })
    ));
}

#[test]
fn concurrent_initializers_have_exactly_one_winner() {
    let mapping = MappedRegion::new(64 * 1024);
    let allocator = FixedRegionAllocator::new();
    let successes = AtomicUsize::new(0);
    let base_address = mapping.base() as usize;
    let size = mapping.size();

    std::thread::scope(|scope| {
        for _ in 0..8 {
            scope.spawn(|| {
                let result = unsafe { allocator.initialize(base_address as *mut u8, size) };
                match result {
                    Ok(_) => {
                        successes.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(FixedRegionError::InitializationInProgress)
                    | Err(FixedRegionError::AlreadyInitialized) => {}
                    Err(error) => panic!("unexpected initialization result: {error:?}"),
                }
            });
        }
    });

    assert_eq!(successes.load(Ordering::Relaxed), 1);
    assert_eq!(allocator.state(), FixedRegionState::Ready);
    assert!(unsafe { allocator.attach(mapping.base(), mapping.size()) }.is_ok());
}

#[test]
fn concurrent_vectors_share_the_region_allocator() {
    let mapping = MappedRegion::new(256 * 1024);
    let allocator = FixedRegionAllocator::new();
    let handle = unsafe { allocator.initialize(mapping.base(), mapping.size()) }.unwrap();

    std::thread::scope(|scope| {
        for worker in 0..8_u64 {
            scope.spawn(move || {
                for round in 0..64_u64 {
                    let mut values = Vec::new_in(handle);
                    values.try_reserve(256).unwrap();
                    values.extend((0..256).map(|value| value + worker + round));
                    assert_eq!(values[128], 128 + worker + round);
                    assert!(
                        handle
                            .region()
                            .contains(values.as_ptr().cast(), values.capacity() * size_of::<u64>())
                    );
                }
            });
        }
    });
}

#[cfg(target_family = "unix")]
#[test]
fn shared_control_object_attaches_after_fork() {
    let page_size = MappedRegion::page_size();
    let control_size = size_of::<FixedRegionAllocator>().div_ceil(page_size) * page_size;
    let mapping = MappedRegion::new(control_size + 256 * 1024);
    let control = mapping.base().cast::<FixedRegionAllocator>();
    let arena_base = unsafe { mapping.base().add(control_size) };
    let arena_size = mapping.size() - control_size;
    let layout = Layout::from_size_align(4_096, 256).unwrap();

    unsafe { control.write(FixedRegionAllocator::new()) };
    {
        let allocator = unsafe { &*control };
        let parent_handle = unsafe { allocator.initialize(arena_base, arena_size) }.unwrap();
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");

        if child == 0 {
            let exit_code = match unsafe { allocator.attach(arena_base, arena_size) } {
                Ok(child_handle) => match child_handle.allocate(layout) {
                    Ok(allocation) => {
                        let pointer = allocation.cast::<u8>();
                        if !child_handle
                            .region()
                            .contains(pointer.as_ptr(), allocation.len())
                        {
                            3
                        } else {
                            unsafe {
                                pointer.as_ptr().write_bytes(0x3c, layout.size());
                                child_handle.deallocate(pointer, layout);
                            }
                            0
                        }
                    }
                    Err(_) => 2,
                },
                Err(_) => 1,
            };
            unsafe { libc::_exit(exit_code) };
        }

        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);

        let allocation = parent_handle.allocate(layout).unwrap();
        assert!(
            parent_handle
                .region()
                .contains(allocation.as_ptr().cast(), allocation.len())
        );
        unsafe { parent_handle.deallocate(allocation.cast(), layout) };
    }
    unsafe { core::ptr::drop_in_place(control) };
}

#[test]
fn invalid_geometry_is_rejected_without_consuming_initialization() {
    let mapping = MappedRegion::new(64 * 1024);
    let allocator = FixedRegionAllocator::new();

    assert!(matches!(
        unsafe { allocator.attach(mapping.base(), mapping.size()) },
        Err(FixedRegionError::NotInitialized)
    ));
    assert!(matches!(
        unsafe { allocator.initialize(core::ptr::null_mut(), mapping.size()) },
        Err(FixedRegionError::NullBase)
    ));
    assert!(matches!(
        unsafe { allocator.initialize(mapping.base().wrapping_add(1), mapping.size()) },
        Err(FixedRegionError::MisalignedBase { .. })
    ));
    assert!(matches!(
        unsafe { allocator.initialize(mapping.base(), mapping.size() - 1) },
        Err(FixedRegionError::MisalignedSize { .. })
    ));
    assert!(matches!(
        unsafe {
            allocator.initialize(
                mapping.base(),
                FixedRegionAllocator::MIN_REGION_SIZE - FixedRegionAllocator::REGION_ALIGNMENT,
            )
        },
        Err(FixedRegionError::RegionTooSmall { .. })
    ));
    let high_aligned_address = usize::MAX & !(FixedRegionAllocator::REGION_ALIGNMENT - 1);
    assert!(matches!(
        unsafe {
            allocator.initialize(
                high_aligned_address as *mut u8,
                FixedRegionAllocator::MIN_REGION_SIZE,
            )
        },
        Err(FixedRegionError::AddressOverflow)
    ));
    assert_eq!(allocator.state(), FixedRegionState::Uninitialized);

    unsafe { allocator.initialize(mapping.base(), mapping.size()) }.unwrap();
}
