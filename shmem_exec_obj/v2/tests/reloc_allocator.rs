#![cfg(all(target_os = "linux", target_has_atomic = "64"))]

use std::process::{Child, Command, ExitStatus};
use std::ptr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use shmem_pod::collections::SharedBox;
use shmem_pod::reloc_allocator::{
    AllocationDescriptor, RELOC_SLOT_ALIGNMENT, RelocAllocator, RelocAllocatorState, RelocError,
};

const MAPPING_LEN: usize = 64 * 1024;
const SLOT_SIZE: usize = 256;
const REGION_ID: u64 = 0x91ca_7e01_5eed_0001;
const EXEC_FD: &str = "SHMEM_POD_RELOC_FD";
const EXEC_PARENT_ADDRESS: &str = "SHMEM_POD_RELOC_PARENT_ADDRESS";

type TestAllocator = RelocAllocator<16>;

fn align_up(value: usize, alignment: usize) -> usize {
    value.checked_add(alignment - 1).unwrap() & !(alignment - 1)
}

fn arena_offset() -> usize {
    align_up(
        std::mem::size_of::<TestAllocator>() + std::mem::size_of::<AllocationDescriptor>(),
        RELOC_SLOT_ALIGNMENT,
    )
}

struct SharedMapping {
    base: *mut u8,
    descriptor: Option<libc::c_int>,
}

#[repr(align(128))]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct OverAligned;

impl SharedMapping {
    fn anonymous() -> Self {
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
        Self {
            base: base.cast(),
            descriptor: None,
        }
    }

    fn memfd() -> Self {
        // SAFETY: name is terminated; omitting CLOEXEC is intentional.
        let descriptor = unsafe { libc::memfd_create(c"reloc-allocator-test".as_ptr(), 0) };
        assert!(descriptor >= 0);
        // SAFETY: descriptor is writable and length is representable.
        assert_eq!(unsafe { libc::ftruncate(descriptor, MAPPING_LEN as _) }, 0);
        // SAFETY: map the complete file as shared writable storage.
        let base = unsafe {
            libc::mmap(
                ptr::null_mut(),
                MAPPING_LEN,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                descriptor,
                0,
            )
        };
        assert_ne!(base, libc::MAP_FAILED);
        Self {
            base: base.cast(),
            descriptor: Some(descriptor),
        }
    }

    unsafe fn construct_allocator(&self) -> &TestAllocator {
        let pointer = self.base.cast::<TestAllocator>();
        // SAFETY: the mapping is exclusive during one-time construction.
        unsafe { pointer.write(TestAllocator::new()) };
        // SAFETY: the initialized object remains live with the mapping.
        unsafe { &*pointer }
    }
}

impl Drop for SharedMapping {
    fn drop(&mut self) {
        // SAFETY: this helper owns the complete live mapping.
        assert_eq!(unsafe { libc::munmap(self.base.cast(), MAPPING_LEN) }, 0);
        if let Some(descriptor) = self.descriptor {
            // SAFETY: this helper owns the descriptor.
            assert_eq!(unsafe { libc::close(descriptor) }, 0);
        }
    }
}

fn wait_for_child(child: &mut Child) -> ExitStatus {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("exec worker timed out");
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

fn initialized(
    mapping: &SharedMapping,
    region_id: u64,
) -> (
    &TestAllocator,
    shmem_pod::reloc_allocator::RelocRegion<'_, 16>,
) {
    // SAFETY: one-time construction and initialization in the live mapping.
    let allocator = unsafe { mapping.construct_allocator() };
    // SAFETY: mapping geometry is live, writable, disjoint, and exclusively initialized.
    let region = unsafe {
        allocator
            .initialize(
                mapping.base,
                MAPPING_LEN,
                region_id,
                arena_offset() as u64,
                SLOT_SIZE,
            )
            .unwrap()
    };
    (allocator, region)
}

#[test]
fn exhaustion_reuse_stale_double_and_wrong_region_are_rejected() {
    let first_mapping = SharedMapping::anonymous();
    let (allocator, mut region) = initialized(&first_mapping, REGION_ID);
    let mut values = Vec::new();
    for value in 0..16_u64 {
        values.push(SharedBox::new(&region, value).unwrap());
    }
    assert!(matches!(
        SharedBox::new(&region, 17_u64),
        Err(RelocError::Exhausted)
    ));

    let stale_descriptor = values[5].descriptor();
    let removed = unsafe { values[5].destroy(&mut region) }.unwrap();
    assert_eq!(removed, 5);
    let replacement = SharedBox::new(&region, 99_u64).unwrap();
    assert_eq!(replacement.descriptor().slot(), stale_descriptor.slot());
    assert_ne!(
        replacement.descriptor().generation(),
        stale_descriptor.generation()
    );
    // SAFETY: reconstruction is deliberate stale-descriptor validation only.
    let stale = unsafe { SharedBox::<u64>::from_descriptor(stale_descriptor) };
    assert!(matches!(
        stale.get(&region),
        Err(RelocError::StaleGeneration { .. })
    ));

    let duplicate_descriptor = replacement.descriptor();
    let mut replacement = replacement;
    assert_eq!(unsafe { replacement.destroy(&mut region) }.unwrap(), 99);
    // SAFETY: this emulates a raw duplicate; destroy must reject it before access.
    let mut duplicate = unsafe { SharedBox::<u64>::from_descriptor(duplicate_descriptor) };
    assert!(matches!(
        unsafe { duplicate.destroy(&mut region) },
        Err(RelocError::StaleGeneration { .. })
    ));

    let second_mapping = SharedMapping::anonymous();
    let (_, second_region) = initialized(&second_mapping, REGION_ID + 1);
    // SAFETY: descriptor is valid in the first region but intentionally wrong here.
    let wrong = unsafe { SharedBox::<u64>::from_descriptor(values[0].descriptor()) };
    assert!(matches!(
        wrong.get(&second_region),
        Err(RelocError::WrongRegion { .. })
    ));

    for value in &mut values {
        if value.descriptor() != stale_descriptor {
            let _ = unsafe { value.destroy(&mut region) };
        }
    }
    assert_eq!(allocator.snapshot().allocated(), 0);
}

#[test]
fn corrupt_descriptor_geometry_is_rejected_before_pointer_formation() {
    let mapping = SharedMapping::anonymous();
    let (_, region) = initialized(&mapping, REGION_ID);
    let value = SharedBox::new(&region, 7_u64).unwrap();
    let descriptor = value.descriptor();

    let bad_offset = AllocationDescriptor::from_raw(
        descriptor.region_id(),
        descriptor.slot(),
        descriptor.generation(),
        descriptor.offset() + 1,
        descriptor.byte_len(),
        descriptor.alignment(),
        descriptor.fingerprint() as u64,
        (descriptor.fingerprint() >> 64) as u64,
    );
    // SAFETY: arbitrary integer descriptors are accepted only for checked rejection.
    let bad_offset = unsafe { SharedBox::<u64>::from_descriptor(bad_offset) };
    assert!(matches!(
        bad_offset.get(&region),
        Err(RelocError::DescriptorMismatch { .. })
    ));

    let bad_slot = AllocationDescriptor::from_raw(
        descriptor.region_id(),
        u32::MAX,
        descriptor.generation(),
        descriptor.offset(),
        descriptor.byte_len(),
        descriptor.alignment(),
        descriptor.fingerprint() as u64,
        (descriptor.fingerprint() >> 64) as u64,
    );
    // SAFETY: same checked-rejection setup.
    let bad_slot = unsafe { SharedBox::<u64>::from_descriptor(bad_slot) };
    assert!(matches!(
        bad_slot.get(&region),
        Err(RelocError::SlotOutOfRange { .. })
    ));
}

#[test]
fn initialization_and_request_geometry_are_checked_before_mutation() {
    let mapping = SharedMapping::anonymous();
    // SAFETY: one-time construction in live exclusive mapping bytes.
    let allocator = unsafe { mapping.construct_allocator() };
    assert!(matches!(
        unsafe {
            allocator.initialize(
                mapping.base,
                MAPPING_LEN,
                0,
                arena_offset() as u64,
                SLOT_SIZE,
            )
        },
        Err(RelocError::ZeroRegionId)
    ));
    assert!(matches!(
        unsafe {
            allocator.initialize(
                mapping.base,
                MAPPING_LEN,
                REGION_ID,
                arena_offset() as u64,
                RELOC_SLOT_ALIGNMENT - 1,
            )
        },
        Err(RelocError::SlotSizeAlignment { .. })
    ));
    assert!(matches!(
        unsafe { allocator.initialize(mapping.base, MAPPING_LEN, REGION_ID, 0, 64) },
        Err(RelocError::ArenaOverlapsAllocator)
    ));
    assert!(matches!(
        unsafe { allocator.initialize(mapping.base, MAPPING_LEN, REGION_ID, u64::MAX, SLOT_SIZE,) },
        Err(RelocError::GeometryOverflow)
    ));
    // SAFETY: prior validation failures did not claim initialization.
    let region = unsafe {
        allocator
            .initialize(
                mapping.base,
                MAPPING_LEN,
                REGION_ID,
                arena_offset() as u64,
                SLOT_SIZE,
            )
            .unwrap()
    };
    assert!(matches!(
        SharedBox::new(&region, OverAligned),
        Err(RelocError::UnsupportedAlignment { .. })
    ));
    assert_eq!(allocator.snapshot().allocated(), 0);
}

#[test]
fn randomized_model_preserves_values_capacity_and_generations() {
    let mapping = SharedMapping::anonymous();
    let (allocator, mut region) = initialized(&mapping, REGION_ID);
    let mut live: Vec<SharedBox<u64>> = Vec::new();
    let mut seed = 0x1234_5678_9abc_def0_u64;

    for step in 0..10_000_u64 {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        let allocate = live.is_empty() || (live.len() < 16 && seed & 1 == 0);
        if allocate {
            let value = seed ^ step;
            live.push(SharedBox::new(&region, value).unwrap());
            assert_eq!(*live.last().unwrap().get(&region).unwrap(), value);
        } else {
            let index = (seed as usize) % live.len();
            let expected = *live[index].get(&region).unwrap();
            let mut value = live.swap_remove(index);
            assert_eq!(unsafe { value.destroy(&mut region) }.unwrap(), expected);
        }
        assert_eq!(allocator.snapshot().allocated(), live.len());
    }
    for mut value in live {
        unsafe { value.destroy(&mut region) }.unwrap();
    }
    assert_eq!(allocator.snapshot().allocated(), 0);
}

#[test]
fn concurrent_alloc_free_is_bounded_and_leak_free() {
    let mapping = SharedMapping::anonymous();
    let (allocator, _region) = initialized(&mapping, REGION_ID);
    let base_address = mapping.base as usize;
    std::thread::scope(|scope| {
        for worker in 0..8_u64 {
            scope.spawn(move || {
                // SAFETY: independent local view of the same authenticated mapping.
                let mut region = unsafe {
                    allocator
                        .attach(base_address as *mut u8, MAPPING_LEN, REGION_ID)
                        .unwrap()
                };
                for iteration in 0..500_u64 {
                    let expected = worker << 32 | iteration;
                    let mut value = loop {
                        match SharedBox::new(&region, expected) {
                            Ok(value) => break value,
                            Err(RelocError::Busy | RelocError::Exhausted) => {
                                std::thread::yield_now();
                            }
                            Err(error) => panic!("allocation failed: {error}"),
                        }
                    };
                    loop {
                        match value.get(&region) {
                            Ok(found) => {
                                assert_eq!(*found, expected);
                                break;
                            }
                            Err(RelocError::Busy) => std::thread::yield_now(),
                            Err(error) => panic!("resolve failed: {error}"),
                        }
                    }
                    loop {
                        match unsafe { value.destroy(&mut region) } {
                            Ok(found) => {
                                assert_eq!(found, expected);
                                break;
                            }
                            Err(RelocError::Busy) => std::thread::yield_now(),
                            Err(error) => panic!("destroy failed: {error}"),
                        }
                    }
                }
            });
        }
    });
    assert_eq!(allocator.snapshot().allocated(), 0);
    assert_eq!(allocator.state(), RelocAllocatorState::Ready);
}

#[test]
fn killed_transaction_stays_bounded_until_supervisor_poison() {
    let mapping = SharedMapping::anonymous();
    let (allocator, region) = initialized(&mapping, REGION_ID);
    let mut pipe = [0; 2];
    // SAFETY: writable two-element descriptor array.
    assert_eq!(unsafe { libc::pipe(pipe.as_mut_ptr()) }, 0);
    // SAFETY: child deliberately dies during an allocator transaction.
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        let _ = unsafe { libc::close(pipe[0]) };
        let _ = unsafe {
            SharedBox::<u64>::try_new_in_place(&region, |target| {
                target.write(1);
                let byte = [1_u8];
                let _ = libc::write(pipe[1], byte.as_ptr().cast(), 1);
                loop {
                    std::hint::spin_loop();
                }
            })
        };
        unsafe { libc::_exit(1) };
    }
    // SAFETY: parent owns these descriptor ends and waits for one-byte readiness.
    assert_eq!(unsafe { libc::close(pipe[1]) }, 0);
    let mut byte = [0_u8];
    assert_eq!(
        unsafe { libc::read(pipe[0], byte.as_mut_ptr().cast(), 1) },
        1
    );
    assert_eq!(unsafe { libc::close(pipe[0]) }, 0);
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFSIGNALED(status));

    assert!(matches!(
        SharedBox::new(&region, 2_u64),
        Err(RelocError::Busy)
    ));
    assert!(allocator.snapshot().operation_locked());
    allocator.poison();
    assert!(matches!(
        SharedBox::new(&region, 3_u64),
        Err(RelocError::Poisoned)
    ));
}

#[test]
fn unwinding_transaction_poisons_and_releases_local_lock() {
    let mapping = SharedMapping::anonymous();
    let (allocator, region) = initialized(&mapping, REGION_ID);
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        let _ = SharedBox::<u64>::try_new_in_place(&region, |target| {
            target.write(1);
            panic!("deliberate initializer unwind");
        });
    }));
    assert!(result.is_err());
    assert_eq!(allocator.state(), RelocAllocatorState::Poisoned);
    assert!(!allocator.snapshot().operation_locked());
    assert!(matches!(
        SharedBox::new(&region, 2_u64),
        Err(RelocError::Poisoned)
    ));
}

#[cfg(target_pointer_width = "64")]
const WORKER_ADDRESSES: [usize; 4] = [
    0x2600_0000_0000,
    0x3600_0000_0000,
    0x4600_0000_0000,
    0x5600_0000_0000,
];

#[cfg(target_pointer_width = "32")]
const WORKER_ADDRESSES: [usize; 3] = [0x3000_0000, 0x5000_0000, 0x7000_0000];

fn map_exec_worker(descriptor: libc::c_int, parent_address: usize) -> *mut u8 {
    for address in WORKER_ADDRESSES {
        if address == parent_address {
            continue;
        }
        // SAFETY: NOREPLACE cannot overwrite an existing mapping.
        let base = unsafe {
            libc::mmap(
                address as *mut _,
                MAPPING_LEN,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_FIXED_NOREPLACE,
                descriptor,
                0,
            )
        };
        if base != libc::MAP_FAILED {
            return base.cast();
        }
    }
    panic!("no distinct exec mapping address was available");
}

#[test]
fn exec_worker_resolves_integer_descriptor_at_different_address() {
    let Ok(descriptor) = std::env::var(EXEC_FD) else {
        return;
    };
    let descriptor = descriptor.parse::<libc::c_int>().unwrap();
    let parent_address = std::env::var(EXEC_PARENT_ADDRESS)
        .unwrap()
        .parse::<usize>()
        .unwrap();
    let base = map_exec_worker(descriptor, parent_address);
    let allocator = unsafe { &*base.cast::<TestAllocator>() };
    // SAFETY: parent authenticated, initialized, and published the complete memfd.
    let mut region = unsafe { allocator.attach(base, MAPPING_LEN, REGION_ID).unwrap() };
    let descriptor_offset = std::mem::size_of::<TestAllocator>();
    // SAFETY: parent wrote this copy before spawning the exec worker.
    let allocation = unsafe {
        base.add(descriptor_offset)
            .cast::<AllocationDescriptor>()
            .read()
    };
    // SAFETY: copied descriptor names the live box and this child never destroys it.
    let value = unsafe { SharedBox::<AtomicU64>::from_descriptor(allocation) };
    value.get(&region).unwrap().fetch_add(1, Ordering::Relaxed);

    let mut local = loop {
        match SharedBox::new(&region, 10_u64) {
            Ok(value) => break value,
            Err(RelocError::Busy) => std::thread::yield_now(),
            Err(error) => panic!("exec allocation failed: {error}"),
        }
    };
    assert_eq!(*local.get(&region).unwrap(), 10);
    unsafe { *local.get_mut(&mut region).unwrap() = 11 };
    loop {
        match unsafe { local.destroy(&mut region) } {
            Ok(value) => {
                assert_eq!(value, 11);
                break;
            }
            Err(RelocError::Busy) => std::thread::yield_now(),
            Err(error) => panic!("exec destruction failed: {error}"),
        }
    }
    assert_ne!(base as usize, parent_address);
    // SAFETY: local borrows and process-local descriptors are no longer used.
    assert_eq!(unsafe { libc::munmap(base.cast(), MAPPING_LEN) }, 0);
}

#[test]
fn independent_exec_maps_elsewhere_and_updates_shared_box() {
    if std::env::var_os(EXEC_FD).is_some() {
        return;
    }
    let mapping = SharedMapping::memfd();
    let (allocator, mut region) = initialized(&mapping, REGION_ID);
    let mut value = SharedBox::new(&region, AtomicU64::new(41)).unwrap();
    let descriptor_offset = std::mem::size_of::<TestAllocator>();
    // SAFETY: descriptor storage is outside allocator metadata and arena.
    unsafe {
        mapping
            .base
            .add(descriptor_offset)
            .cast::<AllocationDescriptor>()
            .write(value.descriptor());
    }
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "exec_worker_resolves_integer_descriptor_at_different_address",
            "--nocapture",
        ])
        .env(EXEC_FD, mapping.descriptor.unwrap().to_string())
        .env(EXEC_PARENT_ADDRESS, (mapping.base as usize).to_string())
        .spawn()
        .unwrap();
    let status = wait_for_child(&mut child);
    assert!(status.success(), "exec worker failed: {status}");
    assert_eq!(value.get(&region).unwrap().load(Ordering::Relaxed), 42);
    let final_value = unsafe { value.destroy(&mut region) }.unwrap();
    assert_eq!(final_value.load(Ordering::Relaxed), 42);
    assert_eq!(allocator.snapshot().allocated(), 0);
}
