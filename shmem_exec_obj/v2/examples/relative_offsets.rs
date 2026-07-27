//! Resolve one shared graph through two different virtual mapping addresses.
//!
//! The header stores only an `OffsetSlice<AtomicU64>`. Two `PodRegion` values
//! supply different process-local bases for the same `memfd` pages, so the
//! integer descriptor reaches the same physical counters from either view.

#[cfg(target_os = "linux")]
use core::ptr;
#[cfg(target_os = "linux")]
use core::sync::atomic::{AtomicU64, Ordering};
#[cfg(target_os = "linux")]
use shmem_pod::offset::{Offset, OffsetSlice, PodRegion};

#[cfg(target_os = "linux")]
const COUNTERS_OFFSET: usize = 128;
#[cfg(target_os = "linux")]
const COUNTERS: usize = 4;

#[cfg(target_os = "linux")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct Header {
    counters: OffsetSlice<AtomicU64>,
}

#[cfg(target_os = "linux")]
fn map(fd: libc::c_int, len: usize) -> *mut u8 {
    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            len,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            fd,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    mapping.cast()
}

#[cfg(target_os = "linux")]
fn main() {
    let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    assert!(page_size > 0, "sysconf(_SC_PAGESIZE) failed");
    let mapping_len = usize::try_from(page_size).expect("page size does not fit usize");
    assert!(core::mem::size_of::<Header>() <= COUNTERS_OFFSET);
    assert!(COUNTERS_OFFSET + COUNTERS * core::mem::size_of::<AtomicU64>() <= mapping_len);
    assert_eq!(COUNTERS_OFFSET % core::mem::align_of::<AtomicU64>(), 0);

    let fd = unsafe { libc::memfd_create(c"shmem-pod-offsets".as_ptr(), libc::MFD_CLOEXEC) };
    assert!(fd >= 0, "memfd_create failed");
    assert_eq!(
        unsafe { libc::ftruncate(fd, mapping_len as libc::off_t) },
        0
    );

    let first = map(fd, mapping_len);
    let second = map(fd, mapping_len);
    assert_ne!(first, second, "simultaneous mappings unexpectedly overlap");
    assert_eq!(first as usize % core::mem::align_of::<Header>(), 0);
    assert_eq!(second as usize % core::mem::align_of::<Header>(), 0);
    assert_eq!(unsafe { libc::close(fd) }, 0);

    let counters = OffsetSlice::new(
        Offset::<AtomicU64>::new(COUNTERS_OFFSET as u64).unwrap(),
        COUNTERS as u64,
    )
    .unwrap();
    unsafe {
        first.cast::<Header>().write(Header { counters });
        let values = first.add(COUNTERS_OFFSET).cast::<AtomicU64>();
        for index in 0..COUNTERS {
            values.add(index).write(AtomicU64::new(0));
        }
    }

    {
        let first_region =
            unsafe { PodRegion::from_raw_parts(first, mapping_len) }.expect("first region");
        let second_region =
            unsafe { PodRegion::from_raw_parts(second, mapping_len) }.expect("second region");
        let header_offset = Offset::<Header>::new(0).unwrap();

        let first_header = unsafe { first_region.get(header_offset) }
            .expect("first header bounds")
            .expect("non-null first header");
        let second_header = unsafe { second_region.get(header_offset) }
            .expect("second header bounds")
            .expect("non-null second header");
        assert_eq!(
            first_header.counters.to_raw(),
            second_header.counters.to_raw()
        );

        let first_counters = unsafe { first_region.get_slice(first_header.counters) }
            .expect("first counter bounds")
            .expect("non-null first counters");
        let second_counters = unsafe { second_region.get_slice(second_header.counters) }
            .expect("second counter bounds")
            .expect("non-null second counters");

        first_counters[2].fetch_add(10, Ordering::Relaxed);
        second_counters[2].fetch_add(20, Ordering::Relaxed);
        assert_eq!(first_counters[2].load(Ordering::Relaxed), 30);
        assert_eq!(second_counters[2].load(Ordering::Relaxed), 30);
    }

    assert_eq!(
        unsafe { libc::munmap(first.cast(), mapping_len) },
        0,
        "first munmap failed"
    );
    assert_eq!(
        unsafe { libc::munmap(second.cast(), mapping_len) },
        0,
        "second munmap failed"
    );
    println!("PASS relative_offsets first={first:p} second={second:p} counter=30");
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("relative_offsets requires Linux");
}
