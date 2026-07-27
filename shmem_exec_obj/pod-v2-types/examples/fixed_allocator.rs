//! Self-checking Talc allocation from fixed shared pages across forked processes.

#[cfg(target_os = "linux")]
use allocator_api2::vec::Vec;
#[cfg(target_os = "linux")]
use core::mem::size_of;
#[cfg(target_os = "linux")]
use core::ptr;
#[cfg(target_os = "linux")]
use shmem_pod::fixed_allocator::FixedRegionAllocator;

#[cfg(target_os = "linux")]
const WORKERS: usize = 6;
#[cfg(target_os = "linux")]
const ROUNDS: usize = 200;

#[cfg(target_os = "linux")]
fn exercise(allocator: &FixedRegionAllocator, arena: *mut u8, arena_len: usize, seed: u64) -> bool {
    let Ok(handle) = (unsafe { allocator.attach(arena, arena_len) }) else {
        return false;
    };
    for round in 0..ROUNDS as u64 {
        let mut values = Vec::new_in(handle);
        if values.try_reserve(512).is_err() {
            return false;
        }
        values.extend((0..512_u64).map(|value| value ^ seed ^ round));
        if values[257] != 257 ^ seed ^ round
            || !handle
                .region()
                .contains(values.as_ptr().cast(), values.capacity() * size_of::<u64>())
        {
            return false;
        }
        // Vec drops before the next round and returns its buffer through the
        // shared process-spin-locked Talc instance.
    }
    true
}

#[cfg(target_os = "linux")]
fn main() {
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) } as usize;
    assert!(page.is_power_of_two());
    let control_len = size_of::<FixedRegionAllocator>().div_ceil(page) * page;
    let arena_len = 2 * 1024 * 1024;
    let mapping_len = control_len + arena_len;
    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            mapping_len,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");

    let control = mapping.cast::<FixedRegionAllocator>();
    let arena = unsafe { mapping.cast::<u8>().add(control_len) };
    unsafe { control.write(FixedRegionAllocator::new()) };
    let allocator = unsafe { &*control };
    let parent_handle =
        unsafe { allocator.initialize(arena, arena_len) }.expect("initialize exact shared arena");
    assert_eq!(parent_handle.region().base().as_ptr(), arena);

    let mut children = [0; WORKERS];
    for (worker, child) in children.iter_mut().enumerate() {
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork failed");
        if pid == 0 {
            let allocator = unsafe { &*control };
            let ok = exercise(allocator, arena, arena_len, worker as u64 + 1);
            unsafe { libc::_exit(if ok { 0 } else { 1 }) };
        }
        *child = pid;
    }

    assert!(exercise(allocator, arena, arena_len, 0));
    for child in children {
        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }

    unsafe { ptr::drop_in_place(control) };
    assert_eq!(unsafe { libc::munmap(mapping, mapping_len) }, 0);
    println!(
        "PASS fixed_allocator processes={} vector_rounds={} arena_bytes={arena_len}",
        WORKERS + 1,
        (WORKERS + 1) * ROUNDS,
    );
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("fixed_allocator example requires Linux");
}
