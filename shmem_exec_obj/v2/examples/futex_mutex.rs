//! Shares one sleeping mutex among forked processes.
//!
//! `ProcessFutexMutex` keeps all persistent state in the shared mapping. It
//! briefly spins under contention, then waits with Linux's process-shared futex
//! operation. This makes it a better fit than a pure spin lock when critical
//! sections may be delayed or oversubscribed.
//!
//! The mutex is deliberately non-robust: a process that exits while holding a
//! guard leaves it locked. Applications that must recover from owner death need
//! a separately designed recovery protocol.

use shmem_pod::sync::ProcessFutexMutex;
use std::hint::spin_loop;
use std::mem::size_of;
use std::ptr;
use std::sync::atomic::{AtomicU32, Ordering};

const WORKERS: u32 = 4;
const INCREMENTS_PER_WORKER: u64 = 25_000;

struct SharedState {
    counter: ProcessFutexMutex<u64>,
    ready: AtomicU32,
    start: AtomicU32,
}

fn main() {
    // MAP_SHARED is essential. FUTEX_PRIVATE operations and MAP_PRIVATE pages
    // cannot synchronize independent processes.
    // SAFETY: mmap supplies page-aligned writable storage for SharedState.
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
    // SAFETY: the mapping is not yet published and has enough aligned space.
    unsafe {
        shared.write(SharedState {
            counter: ProcessFutexMutex::new(0),
            ready: AtomicU32::new(0),
            start: AtomicU32::new(0),
        });
    }

    let mut children = Vec::new();
    for _ in 0..WORKERS {
        // SAFETY: each child confines itself to shared atomics/the mutex and
        // calls _exit without touching process-local runtime state.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            // SAFETY: shared remains mapped for the child's lifetime.
            let shared = unsafe { &*shared };
            shared.ready.fetch_add(1, Ordering::AcqRel);
            while shared.start.load(Ordering::Acquire) == 0 {
                spin_loop();
            }
            for _ in 0..INCREMENTS_PER_WORKER {
                *shared.counter.lock() += 1;
            }
            // SAFETY: avoid running inherited process-local destructors.
            unsafe { libc::_exit(0) };
        }
        children.push(child);
    }

    // SAFETY: the initialized mapping remains valid until munmap below.
    let shared_ref = unsafe { &*shared };
    while shared_ref.ready.load(Ordering::Acquire) != WORKERS {
        std::thread::yield_now();
    }
    shared_ref.start.store(1, Ordering::Release);

    for child in children {
        let mut status = 0;
        // SAFETY: child is a live direct child and status is writable.
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }

    let expected = u64::from(WORKERS) * INCREMENTS_PER_WORKER;
    let observed = *shared_ref.counter.lock();
    assert_eq!(observed, expected);
    println!("counter={observed} across {WORKERS} processes");

    // SAFETY: no process uses the mapping now; address and length match mmap.
    assert_eq!(
        unsafe { libc::munmap(mapping, size_of::<SharedState>()) },
        0
    );
}
