//! Initialize, attach to, and drain typed state in caller-mapped shared pages.

#[cfg(target_os = "linux")]
use std::ptr;
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(target_os = "linux")]
use shmem_pod::mapping::{BuildIdentity, InstanceIdentity, RawMapping};

#[cfg(target_os = "linux")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct State {
    calls: AtomicU64,
}

#[cfg(target_os = "linux")]
fn main() {
    let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    assert!(page_size > 0, "sysconf(_SC_PAGESIZE) failed");
    let mapping_len = usize::try_from(page_size).expect("page size does not fit usize");
    // SAFETY: creates one page of shared writable anonymous storage.
    let base = unsafe {
        libc::mmap(
            ptr::null_mut(),
            mapping_len,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(base, libc::MAP_FAILED, "mmap failed");
    let base = base.cast::<u8>();
    let build = BuildIdentity::new([0x42; 32]);
    let instance = InstanceIdentity::new([0x17; 16]);

    // SAFETY: this process owns initial preparation of the live mapping.
    let mapping =
        unsafe { RawMapping::from_raw_parts(base, mapping_len).unwrap() }.prepare(build, instance);
    let owner = mapping
        .try_initialize(State {
            calls: AtomicU64::new(0),
        })
        .unwrap();

    // The child creates its own counted attachment. It carries no Rust guard or
    // attachment across fork and exits without running duplicated destructors.
    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        let attachment = mapping.attach::<State>().unwrap();
        attachment
            .try_enter()
            .unwrap()
            .calls
            .fetch_add(1, Ordering::Relaxed);
        drop(attachment);
        unsafe { libc::_exit(0) };
    }

    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status));
    assert_eq!(libc::WEXITSTATUS(status), 0);
    assert_eq!(owner.try_enter().unwrap().calls.load(Ordering::Relaxed), 1);

    let draining = owner.begin_drain().unwrap();
    assert!(draining.is_drained().unwrap());
    {
        let closed = draining.try_close().unwrap();
        assert_eq!(closed.base().as_ptr(), base);
    }
    // SAFETY: all typed handles are gone and the lifecycle is closed.
    assert_eq!(unsafe { libc::munmap(base.cast(), mapping_len) }, 0);
    println!("PASS typed_mapping calls=1");
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("typed_mapping requires Linux");
}
