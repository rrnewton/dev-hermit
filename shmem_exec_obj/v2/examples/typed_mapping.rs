//! Initialize, attach to, and drain typed state in caller-mapped shared pages.

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use std::mem::ManuallyDrop;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use std::ptr;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::mapping::{BuildIdentity, InstanceIdentity, RawMapping};

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct State {
    calls: AtomicU64,
}

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
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
    // fork duplicates memory without running Rust ownership bookkeeping. Keep
    // the duplicated child-side owner destructor inert on every failure path;
    // the parent recovers its unique local owner after the child is reaped.
    let mut owner = ManuallyDrop::new(owner);

    // The child creates its own counted attachment and always exits without
    // unwinding inherited values.
    let child = unsafe { libc::fork() };
    if child < 0 {
        // SAFETY: fork failed, so this process still owns the only local copy.
        let owner = unsafe { ManuallyDrop::take(&mut owner) };
        drop(owner);
        panic!("fork failed");
    }
    if child == 0 {
        let exit_code = mapping
            .attach::<State>()
            .and_then(|attachment| {
                let guard = attachment.try_enter()?;
                guard.calls.fetch_add(1, Ordering::Relaxed);
                Ok(())
            })
            .map_or(1, |()| 0);
        unsafe { libc::_exit(exit_code) };
    }
    // SAFETY: only the parent reaches this point, and its ManuallyDrop still
    // contains the original unique close authority. Recover it before any
    // fallible parent work so unwinding retains poison-on-drop behavior.
    let owner = unsafe { ManuallyDrop::take(&mut owner) };

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

#[cfg(not(all(target_os = "linux", target_has_atomic = "64")))]
fn main() {
    eprintln!("typed_mapping requires Linux and lock-free 64-bit atomics");
}
