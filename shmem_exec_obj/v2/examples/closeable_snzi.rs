//! Close admission and drain a scalable presence indicator across processes.
//!
//! Workers repeatedly enter and depart until the parent closes the one-shot
//! gate. Any arrival reserved before close is allowed to finish; every later
//! attempt is rejected. The parent may reclaim the generation only after all
//! successful arrivals have departed and `is_drained` returns true.

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::mem::size_of;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::ptr;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::sync::atomic::{AtomicU32, Ordering};
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::admission::{CloseableSnzi, TryEnterError};

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
const WORKERS: usize = 6;

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
fn main() {
    struct Shared {
        start: AtomicU32,
        barrier: CloseableSnzi<84>,
    }

    // SAFETY: create one anonymous shared mapping and initialize it before fork.
    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            size_of::<Shared>(),
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    let shared = mapping.cast::<Shared>();
    // SAFETY: shared points to exclusive, aligned, writable storage.
    unsafe {
        shared.write(Shared {
            start: AtomicU32::new(0),
            barrier: CloseableSnzi::new(),
        })
    };

    let mut children = [0; WORKERS];
    for (worker, child_slot) in children.iter_mut().enumerate() {
        // SAFETY: the child uses only atomics, pod methods, and _exit.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            // SAFETY: the initialized mapping is inherited and remains live.
            let state = unsafe { &*shared };
            while state.start.load(Ordering::Acquire) == 0 {
                core::hint::spin_loop();
            }

            let mut operations = 0_u32;
            loop {
                match state.barrier.try_enter(worker % state.barrier.leaf_count()) {
                    Ok(token) => {
                        operations += 1;
                        if token.depart().is_err() {
                            unsafe { libc::_exit(20) };
                        }
                    }
                    Err(TryEnterError::Closed) => break,
                    Err(_) => unsafe { libc::_exit(21) },
                }
            }
            unsafe { libc::_exit(if operations != 0 { 0 } else { 22 }) };
        }
        *child_slot = child;
    }

    // SAFETY: the initialized mapping remains live until every child exits.
    let state = unsafe { &*shared };
    state.start.store(1, Ordering::Release);
    for _ in 0..50_000 {
        core::hint::spin_loop();
    }
    assert!(state.barrier.close());

    for child in children {
        let mut status = 0;
        // SAFETY: wait for and reap one direct child.
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }
    assert!(state.barrier.is_drained());

    // SAFETY: no process or token can access the mapping after all children
    // exited and the barrier proved drained.
    assert_eq!(unsafe { libc::munmap(mapping, size_of::<Shared>()) }, 0);
    println!("PASS closeable_snzi workers={WORKERS} drained=true");
}

#[cfg(not(all(target_os = "linux", target_has_atomic = "64")))]
fn main() {
    eprintln!("closeable_snzi requires Linux and 64-bit atomics");
}
