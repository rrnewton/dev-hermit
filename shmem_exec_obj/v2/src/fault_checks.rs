//! Deterministic process-death checks at production atomic transitions.

extern crate std;

use core::mem::size_of;
use core::ptr;
use std::time::{Duration, Instant};

use crate::admission::CloseableSnzi;
use crate::csnzi::Csnzi;
use crate::fault_injection::{FaultPoint, any_detail, arm, arm_sequence};

struct Shared<T> {
    pointer: *mut T,
}

impl<T> Shared<T> {
    fn new(value: T) -> Self {
        // SAFETY: the anonymous shared mapping is private to this test until the
        // fully initialized value is written.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                size_of::<T>(),
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(mapping, libc::MAP_FAILED);
        let pointer = mapping.cast::<T>();
        // SAFETY: mmap returned aligned writable storage of the requested size.
        unsafe { pointer.write(value) };
        Self { pointer }
    }

    fn get(&self) -> &T {
        // SAFETY: the mapping remains live and shared mutation uses atomics.
        unsafe { &*self.pointer }
    }
}

impl<T> Drop for Shared<T> {
    fn drop(&mut self) {
        // SAFETY: all children are reaped before the fixture is dropped.
        assert_eq!(
            unsafe { libc::munmap(self.pointer.cast(), size_of::<T>()) },
            0
        );
    }
}

fn spawn_armed(point: FaultPoint, detail: usize, action: impl FnOnce()) -> libc::pid_t {
    // SAFETY: children perform only shared atomic operations and async-signal-
    // safe process control before _exit.
    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        arm(point, detail, 1);
        action();
        // SAFETY: a child which missed its cut must not unwind inherited state.
        unsafe { libc::_exit(90) };
    }
    child
}

fn wait_for_stop(child: libc::pid_t) {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let mut status = 0;
        // SAFETY: child is a direct child and status is writable.
        let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG | libc::WUNTRACED) };
        if result == child {
            assert!(
                libc::WIFSTOPPED(status),
                "child missed fault cut: {status:#x}"
            );
            assert_eq!(libc::WSTOPSIG(status), libc::SIGSTOP);
            return;
        }
        assert_eq!(
            result,
            0,
            "waitpid failed: {}",
            std::io::Error::last_os_error()
        );
        if Instant::now() >= deadline {
            // SAFETY: bounded cleanup of our direct child.
            let _ = unsafe { libc::kill(child, libc::SIGKILL) };
            let _ = unsafe { libc::waitpid(child, ptr::null_mut(), 0) };
            panic!("child did not reach fault cut");
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

fn kill_stopped(child: libc::pid_t) {
    // SAFETY: child is a stopped direct child owned by this test.
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let mut status = 0;
    // SAFETY: reap the direct child exactly once.
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFSIGNALED(status));
    assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);
}

fn wait_for_success(child: libc::pid_t) {
    let mut status = 0;
    // SAFETY: reap the direct child exactly once.
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status), "child status {status:#x}");
    assert_eq!(libc::WEXITSTATUS(status), 0);
}

#[test]
fn closeable_reservation_and_checker_death_fail_closed() {
    let shared = Shared::new(CloseableSnzi::<4>::new());
    let child = spawn_armed(FaultPoint::CloseableEntryReserved, 0, || {
        let _ = shared.get().try_enter(0);
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert_eq!(shared.get().debug_snapshot().transient_reservations, 1);
    assert!(shared.get().close());
    assert!(!shared.get().is_drained());

    let shared = Shared::new(CloseableSnzi::<4>::new());
    assert!(shared.get().close());
    let child = spawn_armed(FaultPoint::CloseableCheckingPublished, 0, || {
        let _ = shared.get().is_drained();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().debug_snapshot().checking_drain);
    assert!(!shared.get().is_drained());
}

#[test]
fn closeable_death_after_terminal_seal_preserves_drain() {
    let shared = Shared::new(CloseableSnzi::<4>::new());
    assert!(shared.get().close());
    let child = spawn_armed(FaultPoint::CloseableDrainSealed, 0, || {
        let _ = shared.get().is_drained();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().is_drained());
}

#[test]
fn csnzi_arrival_cuts_at_root_parent_and_leaf_fail_closed() {
    const INTERNAL: usize = 0;
    const LEAF: usize = 4;
    let cuts = [
        (FaultPoint::CsnziRootArrived, any_detail()),
        (FaultPoint::CsnziNodeArrived, INTERNAL),
        (FaultPoint::CsnziNodeArrived, LEAF),
    ];
    for (point, detail) in cuts {
        let shared = Shared::new(Csnzi::<20>::new());
        let child = spawn_armed(point, detail, || {
            let _ = shared.get().try_enter(0);
        });
        wait_for_stop(child);
        kill_stopped(child);
        let _ = shared.get().close().unwrap();
        assert!(!shared.get().is_drained(), "cut {point:?} detail {detail}");
    }
}

#[test]
fn csnzi_departure_cuts_at_leaf_parent_and_root_fail_closed() {
    const INTERNAL: usize = 0;
    const LEAF: usize = 4;
    let cuts = [
        (FaultPoint::CsnziNodeDeparted, LEAF),
        (FaultPoint::CsnziNodeDeparted, INTERNAL),
        (FaultPoint::CsnziRootDeparted, any_detail()),
    ];
    for (point, detail) in cuts {
        let shared = Shared::new(Csnzi::<20>::new());
        let raw = shared.get().try_enter(0).unwrap().into_raw();
        let child = spawn_armed(point, detail, || {
            // SAFETY: raw is the sole token from this exact shared object.
            let _ = unsafe { shared.get().depart_raw(raw) };
        });
        wait_for_stop(child);
        kill_stopped(child);
        let _ = shared.get().close().unwrap();
        assert!(!shared.get().is_drained(), "cut {point:?} detail {detail}");
    }
}

#[test]
fn csnzi_redundant_parent_compensation_cut_is_an_actual_rmw_path() {
    const INTERNAL: usize = 0;
    let shared = Shared::new(Csnzi::<20>::new());
    // SAFETY: see spawn_armed; this child deliberately has two stop points.
    let first = unsafe { libc::fork() };
    assert!(first >= 0);
    if first == 0 {
        arm_sequence(
            FaultPoint::CsnziRootArrived,
            any_detail(),
            FaultPoint::CsnziBeforeCompensation,
            INTERNAL,
        );
        let _ = shared.get().try_enter(0);
        unsafe { libc::_exit(91) };
    }
    wait_for_stop(first);

    // A second entrant activates the same path while the first has only its
    // root contribution. Its leaked token keeps every later result fail closed.
    let second = unsafe { libc::fork() };
    assert!(second >= 0);
    if second == 0 {
        let _raw = shared.get().try_enter(0).unwrap().into_raw();
        unsafe { libc::_exit(0) };
    }
    wait_for_success(second);
    // SAFETY: resume the first child so its production node CAS wins against an
    // already-active parent and reaches the compensation cut.
    assert_eq!(unsafe { libc::kill(first, libc::SIGCONT) }, 0);
    wait_for_stop(first);
    kill_stopped(first);

    let _ = shared.get().close().unwrap();
    assert!(!shared.get().is_drained());
    assert!(shared.get().debug_snapshot().root_count >= 2);
}

#[test]
fn csnzi_close_and_departure_tail_cuts_distinguish_wedge_from_commit() {
    let shared = Shared::new(Csnzi::<4>::new());
    let child = spawn_armed(FaultPoint::CsnziCloseClaimedEmpty, 0, || {
        let _ = shared.get().close();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(!shared.get().is_drained());

    let shared = Shared::new(Csnzi::<4>::new());
    let child = spawn_armed(FaultPoint::CsnziCloseSealed, 0, || {
        let _ = shared.get().close();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().is_drained());

    let shared = Shared::new(Csnzi::<4>::new());
    let raw = shared.get().try_enter(0).unwrap().into_raw();
    assert!(matches!(
        shared.get().close().unwrap(),
        crate::csnzi::CloseOutcome::Pending
    ));
    let child = spawn_armed(FaultPoint::CsnziDepartureTailSealed, 1, || {
        // SAFETY: raw is the sole token from this exact shared object.
        let _ = unsafe { shared.get().depart_raw(raw) };
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().is_drained());
}

#[cfg(feature = "linux-futex")]
#[test]
fn futex_owner_and_waiter_death_never_transfer_ownership() {
    use crate::sync::ProcessFutexMutex;

    let shared = Shared::new(ProcessFutexMutex::new(0_u32));
    let child = spawn_armed(FaultPoint::FutexAcquired, 0, || {
        let _guard = shared.get().lock();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().try_lock().is_none());

    let shared = Shared::new(ProcessFutexMutex::new(0_u32));
    let guard = shared.get().lock();
    let child = spawn_armed(FaultPoint::FutexContended, 1, || {
        let _guard = shared.get().lock_fallible().unwrap();
    });
    wait_for_stop(child);
    kill_stopped(child);
    drop(guard);
    assert!(shared.get().try_lock().is_some());
}
