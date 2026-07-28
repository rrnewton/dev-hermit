#![cfg(target_has_atomic = "64")]

use core::mem::{MaybeUninit, align_of};
use shmem_pod::admission::{CloseableSnzi, TryEnterError};
use shmem_pod::{PodSync, PodValue};
use std::sync::{Arc, Barrier};
use std::thread;

fn require_pod<T: PodValue>() {}
fn require_sync<T: PodSync>() {}

#[test]
fn lifecycle_is_one_shot_and_pointer_free() {
    let barrier = CloseableSnzi::<20>::new();
    require_pod::<CloseableSnzi<20>>();
    require_sync::<CloseableSnzi<20>>();
    assert_eq!(align_of::<CloseableSnzi<20>>(), 64);
    assert_eq!(barrier.leaf_count(), 16);
    assert!(!barrier.is_closed());
    assert!(!barrier.is_drained(), "an open barrier is never drained");

    let first = barrier.try_enter(2).unwrap();
    let second = barrier.try_enter(9).unwrap();
    assert!(barrier.query());
    assert!(barrier.close());
    assert!(!barrier.close(), "close is idempotent");
    assert_eq!(barrier.try_enter(3), Err(TryEnterError::Closed));
    assert!(!barrier.is_drained());

    first.depart().unwrap();
    assert!(!barrier.is_drained());
    second.depart().unwrap();
    assert!(barrier.is_drained());
    assert!(barrier.debug_snapshot().appears_drained());
}

#[test]
fn initializes_in_place_and_raw_departure_round_trips() {
    let mut storage = MaybeUninit::<CloseableSnzi<20>>::uninit();
    // SAFETY: storage is exclusive, aligned, and large enough for the object.
    unsafe { CloseableSnzi::<20>::initialize_at(storage.as_mut_ptr()) };
    // SAFETY: initialize_at initialized every field and padding byte.
    let barrier = unsafe { storage.assume_init() };

    let raw = barrier.try_enter(7).unwrap().into_raw();
    barrier.close();
    assert!(!barrier.is_drained());
    // SAFETY: raw is the sole unconsumed token from this exact barrier.
    unsafe { barrier.depart_raw(raw) }.unwrap();
    assert!(barrier.is_drained());
}

#[test]
fn close_racing_with_entries_accepts_only_pre_close_reservations() {
    const WORKERS: usize = 24;
    let barrier = Arc::new(CloseableSnzi::<84>::new());
    let start = Arc::new(Barrier::new(WORKERS + 1));
    let finish = Arc::new(Barrier::new(WORKERS + 1));
    let mut workers = Vec::new();

    for worker in 0..WORKERS {
        let barrier = Arc::clone(&barrier);
        let start = Arc::clone(&start);
        let finish = Arc::clone(&finish);
        workers.push(thread::spawn(move || {
            start.wait();
            let token = barrier.try_enter(worker % barrier.leaf_count()).ok();
            finish.wait();
            if let Some(token) = token {
                token.depart().unwrap();
            }
        }));
    }

    start.wait();
    barrier.close();
    finish.wait();
    assert_eq!(barrier.try_enter(0), Err(TryEnterError::Closed));
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(barrier.is_drained());
}

#[cfg(target_os = "linux")]
mod process_tests {
    use super::*;
    use core::mem::size_of;
    use core::ptr;
    use core::sync::atomic::{AtomicU32, Ordering};
    use std::time::{Duration, Instant};

    struct SharedState {
        ready: AtomicU32,
        barrier: CloseableSnzi<20>,
    }

    struct SharedMapping(*mut SharedState);

    impl SharedMapping {
        fn new() -> Self {
            // SAFETY: this creates page-aligned shared storage large enough for
            // SharedState and no process can observe it before initialization.
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
            let pointer = mapping.cast::<SharedState>();
            // SAFETY: the mapping is exclusive and properly aligned here.
            unsafe {
                pointer.write(SharedState {
                    ready: AtomicU32::new(0),
                    barrier: CloseableSnzi::new(),
                })
            };
            Self(pointer)
        }

        fn get(&self) -> &SharedState {
            // SAFETY: the mapping remains live and all shared mutations use atomics.
            unsafe { &*self.0 }
        }
    }

    impl Drop for SharedMapping {
        fn drop(&mut self) {
            // SAFETY: no child remains and this is the original live mapping.
            assert_eq!(
                unsafe { libc::munmap(self.0.cast(), size_of::<SharedState>()) },
                0
            );
        }
    }

    fn wait_until(timeout: Duration, mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if condition() {
                return;
            }
            thread::sleep(Duration::from_millis(1));
        }
        assert!(condition(), "condition was not met within {timeout:?}");
    }

    fn waitpid(child: libc::pid_t, options: libc::c_int) -> libc::c_int {
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            let mut status = 0;
            // SAFETY: child is a direct child and status is writable.
            let result = unsafe { libc::waitpid(child, &mut status, options | libc::WNOHANG) };
            if result == child {
                return status;
            }
            assert_eq!(
                result,
                0,
                "waitpid failed: {}",
                std::io::Error::last_os_error()
            );
            if Instant::now() >= deadline {
                // SAFETY: bounded cleanup for our direct child.
                let _ = unsafe { libc::kill(child, libc::SIGKILL) };
                // SAFETY: reap the child just killed above.
                let _ = unsafe { libc::waitpid(child, ptr::null_mut(), 0) };
                panic!("child {child} did not change state");
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn stopped_participant_is_not_stolen_and_can_finish() {
        let shared = SharedMapping::new();
        // SAFETY: fork duplicates this test process. The child performs only
        // atomic pod operations and async-signal-safe syscalls before _exit.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            let state = shared.get();
            let token = match state.barrier.try_enter(1) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            state.ready.store(1, Ordering::Release);
            // SAFETY: stopping the current process is async-signal-safe.
            unsafe { libc::raise(libc::SIGSTOP) };
            let ok = token.depart().is_ok();
            unsafe { libc::_exit(if ok { 0 } else { 11 }) };
        }

        wait_until(Duration::from_secs(2), || {
            shared.get().ready.load(Ordering::Acquire) == 1
        });
        let stopped = waitpid(child, libc::WUNTRACED);
        assert!(libc::WIFSTOPPED(stopped));
        shared.get().barrier.close();
        assert!(!shared.get().barrier.is_drained());

        // SAFETY: child is stopped and remains our direct child.
        assert_eq!(unsafe { libc::kill(child, libc::SIGCONT) }, 0);
        let status = waitpid(child, 0);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
        assert!(shared.get().barrier.is_drained());
    }

    #[test]
    fn killed_participant_leaks_presence_and_prevents_false_drain() {
        let shared = SharedMapping::new();
        // SAFETY: see the stopped-participant test. The child intentionally
        // exits without consuming its token.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            let state = shared.get();
            let _token = match state.barrier.try_enter(4) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            state.ready.store(1, Ordering::Release);
            loop {
                // SAFETY: pause is async-signal-safe and SIGKILL terminates it.
                unsafe { libc::pause() };
            }
        }

        wait_until(Duration::from_secs(2), || {
            shared.get().ready.load(Ordering::Acquire) == 1
        });
        // SAFETY: child is a live direct child.
        assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
        let status = waitpid(child, 0);
        assert!(libc::WIFSIGNALED(status));
        assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);

        shared.get().barrier.close();
        assert!(shared.get().barrier.query());
        assert!(!shared.get().barrier.is_drained());
        assert!(!shared.get().barrier.debug_snapshot().appears_drained());
    }
}
