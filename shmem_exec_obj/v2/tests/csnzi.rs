#![cfg(target_has_atomic = "64")]

use core::mem::{MaybeUninit, align_of};
use shmem_pod::csnzi::{
    CloseOutcome, Csnzi, CsnziError, CsnziPhase, CsnziPoisonReason, DepartOutcome,
};
use shmem_pod::{PodSync, PodValue};
use std::sync::{Arc, Barrier};
use std::thread;

fn require_pod<T: PodValue>() {}
fn require_sync<T: PodSync>() {}

#[test]
fn layout_and_one_shot_lifecycle() {
    assert!(!Csnzi::<0>::is_valid_node_count());
    assert!(!Csnzi::<5>::is_valid_node_count());
    assert!(Csnzi::<4>::is_valid_node_count());
    assert!(Csnzi::<20>::is_valid_node_count());
    assert!(Csnzi::<84>::is_valid_node_count());

    let barrier = Csnzi::<20>::new();
    require_pod::<Csnzi<20>>();
    require_sync::<Csnzi<20>>();
    assert_eq!(align_of::<Csnzi<20>>(), 64);
    assert_eq!(barrier.leaf_count(), 16);
    assert!(!barrier.is_closed());
    assert!(!barrier.is_drained());

    let first = barrier.try_enter(2).unwrap();
    let second = barrier.try_enter(9).unwrap();
    assert!(barrier.query());
    assert_eq!(barrier.close().unwrap(), CloseOutcome::Pending);
    assert_eq!(barrier.close().unwrap(), CloseOutcome::AlreadyClosed);
    assert_eq!(barrier.try_enter(3), Err(CsnziError::Closed));

    assert_eq!(first.depart().unwrap(), DepartOutcome::Active);
    assert!(!barrier.is_drained());
    assert_eq!(second.depart().unwrap(), DepartOutcome::Drained);
    assert!(barrier.is_drained());
    assert!(barrier.is_closed());
    assert!(!barrier.query());
    assert!(barrier.debug_snapshot().appears_drained());
}

#[test]
fn close_empty_seals_immediately_and_stably() {
    let barrier = Csnzi::<4>::new();
    assert_eq!(barrier.close().unwrap(), CloseOutcome::Drained);
    for _ in 0..100 {
        assert!(barrier.is_drained());
        assert_eq!(barrier.try_enter(0), Err(CsnziError::Closed));
        assert_eq!(barrier.close().unwrap(), CloseOutcome::AlreadyClosed);
    }
}

#[test]
fn initializes_in_place_without_moving_the_aggregate() {
    let mut storage = MaybeUninit::<Csnzi<84>>::uninit();
    // SAFETY: storage is exclusive, aligned, and exactly large enough.
    unsafe { Csnzi::<84>::initialize_at(storage.as_mut_ptr()) };
    // SAFETY: initialize_at initialized every field and padding byte.
    let barrier = unsafe { storage.assume_init() };
    let token = barrier.try_enter(63).unwrap();
    assert_eq!(barrier.close().unwrap(), CloseOutcome::Pending);
    assert_eq!(token.depart().unwrap(), DepartOutcome::Drained);
    assert!(barrier.is_drained());
}

#[test]
fn raw_tokens_are_linear_generation_tagged_capabilities() {
    let barrier = Csnzi::<4>::new();
    let first_raw = barrier.try_enter(2).unwrap().into_raw();
    assert_eq!(first_raw & 0xffff, 2);
    // SAFETY: this is the sole raw encoding from this exact object.
    unsafe { barrier.depart_raw(first_raw) }.unwrap();
    assert_eq!(
        unsafe { barrier.depart_raw(first_raw) },
        Err(CsnziError::InactiveToken {
            leaf: 2,
            generation: 1,
        })
    );

    let second = barrier.try_enter(2).unwrap();
    assert_eq!(second.generation(), 2);
    assert_eq!(
        unsafe { barrier.depart_raw(first_raw) },
        Err(CsnziError::GenerationMismatch {
            leaf: 2,
            token_generation: 1,
            current_generation: 2,
        })
    );
    second.depart().unwrap();

    assert_eq!(
        unsafe { barrier.depart_raw(0) },
        Err(CsnziError::MalformedToken)
    );
    assert_eq!(
        unsafe { barrier.depart_raw(1_u64 << 63) },
        Err(CsnziError::MalformedToken)
    );
    let out_of_range = (1_u64 << 16) | 4;
    assert_eq!(
        unsafe { barrier.depart_raw(out_of_range) },
        Err(CsnziError::InvalidLeaf {
            leaf: 4,
            leaf_count: 4,
        })
    );
}

#[test]
fn same_leaf_activity_keeps_one_root_contribution() {
    const THREADS: usize = 12;
    const ITERATIONS: usize = 4_000;

    let barrier = Arc::new(Csnzi::<84>::new());
    let sentinel = barrier.try_enter(7).unwrap();
    let start = Arc::new(Barrier::new(THREADS + 1));
    let done = Arc::new(Barrier::new(THREADS + 1));
    let mut workers = Vec::new();

    for _ in 0..THREADS {
        let barrier = Arc::clone(&barrier);
        let start = Arc::clone(&start);
        let done = Arc::clone(&done);
        workers.push(thread::spawn(move || {
            start.wait();
            for _ in 0..ITERATIONS {
                let token = barrier.try_enter(7).unwrap();
                token.depart().unwrap();
            }
            done.wait();
        }));
    }

    start.wait();
    done.wait();
    let snapshot = barrier.debug_snapshot();
    assert_eq!(snapshot.root_count, 1);
    assert_eq!(
        snapshot.active_nodes, 3,
        "one active path in a 3-level tree"
    );
    assert!(snapshot.local_count_sum >= 3);
    assert_eq!(snapshot.poison, None);

    assert_eq!(barrier.close().unwrap(), CloseOutcome::Pending);
    assert_eq!(sentinel.depart().unwrap(), DepartOutcome::Drained);
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(barrier.is_drained());
}

#[test]
fn close_races_accept_or_reject_without_losing_participants() {
    const WORKERS: usize = 32;
    const ROUNDS: usize = 50;

    for _ in 0..ROUNDS {
        let barrier = Arc::new(Csnzi::<84>::new());
        let start = Arc::new(Barrier::new(WORKERS + 1));
        let release = Arc::new(Barrier::new(WORKERS + 1));
        let mut workers = Vec::new();
        for worker in 0..WORKERS {
            let barrier = Arc::clone(&barrier);
            let start = Arc::clone(&start);
            let release = Arc::clone(&release);
            workers.push(thread::spawn(move || {
                start.wait();
                let admitted = barrier.try_enter(worker % barrier.leaf_count()).ok();
                release.wait();
                if let Some(token) = admitted {
                    token.depart().unwrap();
                }
            }));
        }

        start.wait();
        let _ = barrier.close().unwrap();
        release.wait();
        for worker in workers {
            worker.join().unwrap();
        }
        assert!(barrier.is_drained());
        assert_eq!(barrier.try_enter(0), Err(CsnziError::Closed));
    }
}

#[test]
fn node_count_overflow_poison_is_fail_closed() {
    let barrier = Csnzi::<4>::new();
    let mut tokens = Vec::with_capacity(Csnzi::<4>::MAX_NODE_COUNT as usize);
    for _ in 0..Csnzi::<4>::MAX_NODE_COUNT {
        tokens.push(barrier.try_enter(0).unwrap());
    }
    assert_eq!(
        barrier.try_enter(0),
        Err(CsnziError::Poisoned(CsnziPoisonReason::NodeCountOverflow))
    );
    assert_eq!(
        barrier.poison_reason(),
        Some(CsnziPoisonReason::NodeCountOverflow)
    );
    assert!(barrier.query());
    assert!(!barrier.is_drained());
    drop(tokens);
}

#[cfg(target_os = "linux")]
mod linux_process_tests {
    use super::*;
    use core::mem::size_of;
    use core::ptr;
    use core::sync::atomic::{AtomicU32, Ordering};
    use std::time::{Duration, Instant};

    struct SharedState {
        ready: AtomicU32,
        barrier: Csnzi<20>,
    }

    struct SharedMapping(*mut SharedState);

    impl SharedMapping {
        fn new() -> Self {
            // SAFETY: create private-to-test shared storage before any fork.
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
            assert_ne!(mapping, libc::MAP_FAILED);
            let pointer = mapping.cast::<SharedState>();
            // SAFETY: the new mapping is exclusive and sufficiently aligned.
            unsafe {
                pointer.write(SharedState {
                    ready: AtomicU32::new(0),
                    barrier: Csnzi::new(),
                })
            };
            Self(pointer)
        }

        fn get(&self) -> &SharedState {
            // SAFETY: mapping lifetime surrounds every caller and mutations are atomic.
            unsafe { &*self.0 }
        }
    }

    impl Drop for SharedMapping {
        fn drop(&mut self) {
            // SAFETY: each test reaps its child before dropping the mapping.
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
        assert!(condition(), "condition not met within {timeout:?}");
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
                // SAFETY: bounded cleanup of our direct test child.
                let _ = unsafe { libc::kill(child, libc::SIGKILL) };
                let _ = unsafe { libc::waitpid(child, ptr::null_mut(), 0) };
                panic!("child {child} did not change state");
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    #[test]
    fn stopped_participant_is_never_lease_stolen() {
        let shared = SharedMapping::new();
        // SAFETY: child uses atomics and async-signal-safe syscalls before _exit.
        let child = unsafe { libc::fork() };
        assert!(child >= 0);
        if child == 0 {
            let state = shared.get();
            let token = match state.barrier.try_enter(1) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            state.ready.store(1, Ordering::Release);
            unsafe { libc::raise(libc::SIGSTOP) };
            let ok = token.depart().is_ok();
            unsafe { libc::_exit(if ok { 0 } else { 11 }) };
        }

        wait_until(Duration::from_secs(2), || {
            shared.get().ready.load(Ordering::Acquire) == 1
        });
        let status = waitpid(child, libc::WUNTRACED);
        assert!(libc::WIFSTOPPED(status));
        assert_eq!(shared.get().barrier.close().unwrap(), CloseOutcome::Pending);
        assert!(!shared.get().barrier.is_drained());

        // SAFETY: child is stopped and is our direct child.
        assert_eq!(unsafe { libc::kill(child, libc::SIGCONT) }, 0);
        let status = waitpid(child, 0);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
        assert!(shared.get().barrier.is_drained());
    }

    #[test]
    fn killed_participant_permanently_prevents_false_drain() {
        let shared = SharedMapping::new();
        // SAFETY: child intentionally leaks its token and is terminated by parent.
        let child = unsafe { libc::fork() };
        assert!(child >= 0);
        if child == 0 {
            let state = shared.get();
            let _token = match state.barrier.try_enter(4) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            state.ready.store(1, Ordering::Release);
            loop {
                unsafe { libc::pause() };
            }
        }

        wait_until(Duration::from_secs(2), || {
            shared.get().ready.load(Ordering::Acquire) == 1
        });
        // SAFETY: child is live and directly owned by this test.
        assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
        let status = waitpid(child, 0);
        assert!(libc::WIFSIGNALED(status));
        assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);

        assert_eq!(shared.get().barrier.close().unwrap(), CloseOutcome::Pending);
        assert!(shared.get().barrier.query());
        assert!(!shared.get().barrier.is_drained());
    }

    #[test]
    fn two_different_virtual_addresses_share_one_pointer_free_object() {
        let name = b"shmem-pod-csnzi\0";
        // SAFETY: name is a valid terminated C string and flags are valid.
        let fd = unsafe { libc::memfd_create(name.as_ptr().cast(), libc::MFD_CLOEXEC) };
        assert!(
            fd >= 0,
            "memfd_create failed: {}",
            std::io::Error::last_os_error()
        );
        let length = size_of::<Csnzi<20>>();
        // SAFETY: fd is live and the requested length is representable here.
        assert_eq!(unsafe { libc::ftruncate(fd, length as libc::off_t) }, 0);

        let map = |hint: *mut libc::c_void| {
            // SAFETY: map the same complete memfd range as shared read-write storage.
            unsafe {
                libc::mmap(
                    hint,
                    length,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_SHARED,
                    fd,
                    0,
                )
            }
        };
        let first = map(ptr::null_mut());
        assert_ne!(first, libc::MAP_FAILED);
        let second = map(ptr::null_mut());
        assert_ne!(second, libc::MAP_FAILED);
        assert_ne!(first, second);
        let first = first.cast::<Csnzi<20>>();
        let second = second.cast::<Csnzi<20>>();
        // SAFETY: first is exclusive during initialization and both mappings are aligned.
        unsafe { Csnzi::<20>::initialize_at(first) };
        // SAFETY: both addresses map the initialized object for the whole test.
        let a = unsafe { &*first };
        let b = unsafe { &*second };

        let token_a = a.try_enter(0).unwrap();
        let token_b = b.try_enter(15).unwrap();
        assert_eq!(b.debug_snapshot().root_count, 2);
        assert_eq!(b.close().unwrap(), CloseOutcome::Pending);
        token_a.depart().unwrap();
        assert!(!a.is_drained());
        assert_eq!(token_b.depart().unwrap(), DepartOutcome::Drained);
        assert!(a.is_drained());
        assert!(b.is_drained());

        // SAFETY: no token or process can access either mapping now.
        assert_eq!(unsafe { libc::munmap(first.cast(), length) }, 0);
        assert_eq!(unsafe { libc::munmap(second.cast(), length) }, 0);
        assert_eq!(unsafe { libc::close(fd) }, 0);
    }

    #[test]
    fn fork_happens_before_tokens_exist() {
        let shared = SharedMapping::new();
        // SAFETY: no token exists at fork; child consumes only its own token.
        let child = unsafe { libc::fork() };
        assert!(child >= 0);
        if child == 0 {
            let token = match shared.get().barrier.try_enter(3) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            shared.get().ready.store(1, Ordering::Release);
            while !shared.get().barrier.is_closed() {
                core::hint::spin_loop();
            }
            let ok = token.depart().is_ok();
            unsafe { libc::_exit(if ok { 0 } else { 11 }) };
        }
        wait_until(Duration::from_secs(2), || {
            shared.get().ready.load(Ordering::Acquire) == 1
        });
        shared.get().barrier.close().unwrap();
        let status = waitpid(child, 0);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
        assert!(shared.get().barrier.is_drained());
        assert_eq!(
            shared.get().barrier.debug_snapshot().phase,
            CsnziPhase::Drained
        );
    }
}
