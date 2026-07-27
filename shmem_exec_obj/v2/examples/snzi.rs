//! Exercise a hierarchical scalable nonzero indicator across processes.
//!
//! SNZI answers whether any unmatched arrival exists; it is not an exact
//! counter. Workers deliberately collide on a small set of leaves while the
//! parent holds a sentinel arrival, so every concurrent `query` must be true.
//! The sentinel is created only after all forks: no live `ArrivalToken` is ever
//! duplicated into a child.

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::mem::size_of;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::ptr;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::sync::atomic::{AtomicU32, Ordering};
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::snzi::Snzi;

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
const WORKERS: usize = 8;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
const ITERATIONS: usize = 20_000;

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
fn main() {
    type SharedSnzi = Snzi<84>; // 4-ary tree with 64 leaves.

    struct SharedState {
        start: AtomicU32,
        snzi: SharedSnzi,
    }

    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            size_of::<SharedState>(),
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    let shared = mapping.cast::<SharedState>();
    unsafe {
        shared.write(SharedState {
            start: AtomicU32::new(0),
            snzi: SharedSnzi::new(),
        })
    };

    let mut children = [0; WORKERS];
    for (worker, child) in children.iter_mut().enumerate() {
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork failed");
        if pid == 0 {
            let state = unsafe { &*shared };
            while state.start.load(Ordering::Acquire) == 0 {
                core::hint::spin_loop();
            }

            let mut ok = true;
            for iteration in 0..ITERATIONS {
                // Deliberate collisions exercise helping and parent filtering.
                // Leaf zero is reserved for the parent's sentinel.
                let leaf = 1 + (worker * 7 + iteration) % 8;
                match state.snzi.arrive(leaf) {
                    Ok(token) => {
                        ok &= state.snzi.query();
                        ok &= token.depart().is_ok();
                    }
                    Err(_) => {
                        ok = false;
                        break;
                    }
                }
            }
            unsafe { libc::_exit(if ok { 0 } else { 1 }) };
        }
        *child = pid;
    }

    // All children are waiting at the start gate, so this token was not live in
    // any process when fork duplicated the parent's address space.
    let state = unsafe { &*shared };
    let sentinel = state.snzi.arrive(0).expect("sentinel arrival");
    state.start.store(1, Ordering::Release);

    for child in children {
        let mut status = 0;
        loop {
            let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) };
            if result == child {
                break;
            }
            assert_eq!(result, 0, "waitpid failed");
            assert!(state.snzi.query(), "query missed the held sentinel");
            core::hint::spin_loop();
        }
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }

    assert!(state.snzi.query());
    sentinel.depart().expect("sentinel departure");
    assert!(!state.snzi.query());
    assert!(state.snzi.is_quiescent());
    assert_eq!(state.snzi.poison_reason(), None);

    unsafe { ptr::drop_in_place(shared) };
    assert_eq!(
        unsafe { libc::munmap(mapping, size_of::<SharedState>()) },
        0
    );
    println!(
        "PASS snzi workers={WORKERS} operations={} leaves=64",
        WORKERS * ITERATIONS
    );
}

#[cfg(not(all(target_os = "linux", target_has_atomic = "64")))]
fn main() {
    eprintln!("snzi example requires Linux and 64-bit atomics");
}
