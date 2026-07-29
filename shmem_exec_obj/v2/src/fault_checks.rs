//! Deterministic process-death checks at production atomic transitions.

extern crate std;

#[cfg(feature = "linux-futex")]
use core::mem::ManuallyDrop;
use core::mem::size_of;
use core::ptr;
#[cfg(feature = "linux-futex")]
use std::format;
use std::time::{Duration, Instant};

use crate::admission::CloseableSnzi;
use crate::csnzi::Csnzi;
use crate::fault_injection::{FaultPoint, any_detail, arm, arm_sequence};
use crate::migration::{
    AdmissionQuiescence, AuthorityIdentity, BackingIdentity, GenerationIdentity, MigrationControl,
    MigrationPhase, MigrationPlan, PrecommitTargetBacking, SchemaIdentity,
};
use crate::snzi::Snzi;

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

#[cfg(feature = "linux-futex")]
fn kill_running(child: libc::pid_t) {
    // SAFETY: child is a direct child owned by this test.
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let mut status = 0;
    // SAFETY: reap the direct child exactly once.
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFSIGNALED(status));
    assert_eq!(libc::WTERMSIG(status), libc::SIGKILL);
}

#[cfg(feature = "linux-futex")]
fn wait_for_futex_sleep(child: libc::pid_t) {
    let deadline = Instant::now() + Duration::from_secs(3);
    let path = format!("/proc/{child}/wchan");
    loop {
        let wait_channel = std::fs::read_to_string(&path).unwrap_or_default();
        if wait_channel.contains("futex") {
            return;
        }
        if Instant::now() >= deadline {
            kill_running(child);
            panic!("child never entered a kernel futex wait; last wchan={wait_channel:?}");
        }
        std::thread::sleep(Duration::from_millis(1));
    }
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
fn closeable_publication_departure_and_post_scan_death_fail_closed() {
    let shared = Shared::new(CloseableSnzi::<4>::new());
    let child = spawn_armed(FaultPoint::CloseableArrivalPublished, 0, || {
        let _ = shared.get().try_enter(0);
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.transient_reservations, 1);
    assert_eq!(snapshot.snzi.root_count, 1);
    assert!(shared.get().close());
    assert!(!shared.get().is_drained());

    let shared = Shared::new(CloseableSnzi::<4>::new());
    let raw = shared.get().try_enter(0).unwrap().into_raw();
    assert!(shared.get().close());
    let child = spawn_armed(FaultPoint::CloseableDepartureReserved, 0, || {
        // SAFETY: raw is the sole token from this exact shared object.
        let _ = unsafe { shared.get().depart_raw(raw) };
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.transient_reservations, 1);
    assert_eq!(snapshot.snzi.root_count, 1);
    assert!(!shared.get().is_drained());

    let shared = Shared::new(CloseableSnzi::<4>::new());
    assert!(shared.get().close());
    let child = spawn_armed(FaultPoint::CloseableDrainScanned, 1, || {
        let _ = shared.get().is_drained();
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(shared.get().debug_snapshot().checking_drain);
    assert!(!shared.get().is_drained());
}

#[test]
fn standalone_snzi_half_root_and_helper_publication_cuts_are_observable() {
    const LEAF: usize = 4;

    let shared = Shared::new(Snzi::<20>::new());
    let child = spawn_armed(FaultPoint::SnziHalfPublished, LEAF, || {
        let _ = shared.get().arrive(0);
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.half_nodes, 1);
    assert_eq!(snapshot.root_count, 0);
    assert!(!snapshot.is_quiescent());

    let shared = Shared::new(Snzi::<20>::new());
    let initiator = spawn_armed(FaultPoint::SnziHalfPublished, LEAF, || {
        let _ = shared.get().arrive(0);
    });
    wait_for_stop(initiator);
    let helper = spawn_armed(FaultPoint::SnziNodePublished, LEAF, || {
        let _ = shared.get().arrive(0);
    });
    wait_for_stop(helper);
    kill_stopped(helper);
    kill_stopped(initiator);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.root_count, 1);
    assert!(snapshot.active_nodes >= 2);
    assert!(shared.get().query());

    let shared = Shared::new(Snzi::<20>::new());
    let child = spawn_armed(FaultPoint::SnziRootArrived, 0, || {
        let _ = shared.get().arrive(0);
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.root_count, 1);
    assert!(snapshot.half_nodes >= 1);
    assert!(shared.get().query());
}

#[test]
fn standalone_snzi_increment_and_compensation_cuts_leak_presence() {
    const INTERNAL: usize = 0;
    const LEAF: usize = 4;

    let shared = Shared::new(Snzi::<20>::new());
    let _raw = shared.get().arrive(0).unwrap().into_raw();
    let child = spawn_armed(FaultPoint::SnziNodeIncremented, LEAF, || {
        let _ = shared.get().arrive(0);
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.root_count, 1);
    assert!(snapshot.local_count_sum >= 3);
    assert!(shared.get().query());

    let shared = Shared::new(Snzi::<20>::new());
    // SAFETY: this child deliberately has a two-stage production-path stop.
    let first = unsafe { libc::fork() };
    assert!(first >= 0);
    if first == 0 {
        arm_sequence(
            FaultPoint::SnziRootArrived,
            0,
            FaultPoint::SnziBeforeCompensation,
            INTERNAL,
        );
        let _ = shared.get().arrive(0);
        unsafe { libc::_exit(91) };
    }
    wait_for_stop(first);
    let second = unsafe { libc::fork() };
    assert!(second >= 0);
    if second == 0 {
        let _raw = shared.get().arrive(0).unwrap().into_raw();
        unsafe { libc::_exit(0) };
    }
    wait_for_success(second);
    // SAFETY: the helper has published the same path, forcing the resumed
    // initiator through the redundant-parent compensation branch.
    assert_eq!(unsafe { libc::kill(first, libc::SIGCONT) }, 0);
    wait_for_stop(first);
    kill_stopped(first);
    let snapshot = shared.get().debug_snapshot();
    assert!(snapshot.root_count >= 2);
    assert!(shared.get().query());
}

#[test]
fn standalone_snzi_local_and_root_departure_cuts_show_commit_boundary() {
    const LEAF: usize = 4;

    let shared = Shared::new(Snzi::<20>::new());
    let raw = shared.get().arrive(0).unwrap().into_raw();
    let child = spawn_armed(FaultPoint::SnziNodeDecremented, LEAF, || {
        // SAFETY: raw is the sole token from this exact shared object.
        let _ = unsafe { shared.get().depart_raw(raw) };
    });
    wait_for_stop(child);
    kill_stopped(child);
    let snapshot = shared.get().debug_snapshot();
    assert_eq!(snapshot.root_count, 1);
    assert!(shared.get().query());

    let shared = Shared::new(Snzi::<4>::new());
    let raw = shared.get().arrive(0).unwrap().into_raw();
    let child = spawn_armed(FaultPoint::SnziRootDeparted, 0, || {
        // SAFETY: raw is the sole token from this exact shared object.
        let _ = unsafe { shared.get().depart_raw(raw) };
    });
    wait_for_stop(child);
    kill_stopped(child);
    assert!(!shared.get().query());
    assert!(shared.get().is_quiescent());
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

#[cfg(feature = "linux-futex")]
#[test]
fn futex_releaser_death_after_unlock_can_leave_known_waiter_asleep() {
    use crate::sync::ProcessFutexMutex;

    let shared = Shared::new(ProcessFutexMutex::new(0_u32));
    let mut owner = ManuallyDrop::new(shared.get().lock());

    // SAFETY: the child enters the actual slow path, stops after changing the
    // word to CONTENDED, then continues into the kernel futex wait.
    let waiter = unsafe { libc::fork() };
    assert!(waiter >= 0);
    if waiter == 0 {
        arm_sequence(FaultPoint::FutexContended, 1, FaultPoint::FutexContended, 0);
        let _guard = shared.get().lock_fallible().unwrap();
        unsafe { libc::_exit(92) };
    }
    wait_for_stop(waiter);
    // SAFETY: advance the waiter from its observed contended swap to FUTEX_WAIT.
    assert_eq!(unsafe { libc::kill(waiter, libc::SIGCONT) }, 0);
    wait_for_futex_sleep(waiter);

    // SAFETY: fork duplicates the guard into the child. ManuallyDrop prevents
    // the parent copy from issuing a second unlock.
    let releaser = unsafe { libc::fork() };
    assert!(releaser >= 0);
    if releaser == 0 {
        arm(FaultPoint::FutexReleased, 2, 1);
        let inherited = unsafe { ManuallyDrop::take(&mut owner) };
        drop(inherited);
        unsafe { libc::_exit(93) };
    }
    wait_for_stop(releaser);

    // The release swap is committed, but the stopped process has not executed
    // FUTEX_WAKE. Another process can acquire the unlocked word while the known
    // kernel waiter remains asleep. This is fail-stop, not ownership transfer.
    let replacement = shared.get().try_lock().expect("word was not unlocked");
    wait_for_futex_sleep(waiter);
    kill_stopped(releaser);
    kill_running(waiter);
    drop(replacement);
}

struct CrashTarget {
    generation: GenerationIdentity,
    authority: AuthorityIdentity,
}

// SAFETY: this synthetic target has no payload, aliases, destructor, or
// publication path. It only drives the shared migration phase machine.
unsafe impl PrecommitTargetBacking for CrashTarget {
    fn generation(&self) -> GenerationIdentity {
        self.generation
    }

    fn recovery_authority(&self) -> AuthorityIdentity {
        self.authority
    }

    fn is_private(&self) -> bool {
        true
    }
}

#[test]
fn migration_reclaimed_death_resumes_idempotent_cleanup() {
    struct Fixture {
        control: MigrationControl,
        admission: CloseableSnzi<20>,
    }

    let shared = Shared::new(Fixture {
        control: MigrationControl::new(),
        admission: CloseableSnzi::new(),
    });
    assert!(shared.get().admission.close());
    assert!(shared.get().admission.is_drained());

    let authority = AuthorityIdentity::new([0x55; 16]);
    let plan = MigrationPlan::new(
        0xfeed,
        GenerationIdentity::new(
            SchemaIdentity::new(1, 0x11),
            41,
            100,
            BackingIdentity::new([0x41; 32]),
        ),
        GenerationIdentity::new(
            SchemaIdentity::new(2, 0x22),
            42,
            101,
            BackingIdentity::new([0x42; 32]),
        ),
        authority,
    )
    .unwrap();

    let child = spawn_armed(FaultPoint::MigrationReclaimed, 0, || {
        // SAFETY: the shared terminal gate is the only synthetic source path
        // and is bound to this fixture's unique control and complete plan.
        let source = unsafe { AdmissionQuiescence::bind(&shared.get().admission, plan) }.unwrap();
        let migration = shared
            .get()
            .control
            .begin_with_quiescent_source(source, plan)
            .unwrap();
        let target = CrashTarget {
            generation: plan.target(),
            authority,
        };
        // SAFETY: CrashTarget satisfies the private precommit contract above.
        let ready = unsafe { migration.mark_target_ready(target) }.unwrap();
        let committed = ready.commit().unwrap();
        // SAFETY: committed.source() is the exact terminal witness retained by
        // this transaction and the fixture has no uncounted access paths.
        let _ = unsafe {
            shared
                .get()
                .control
                .authorize_reclamation(committed.source())
        }
        .unwrap();
    });
    wait_for_stop(child);
    kill_stopped(child);

    assert_eq!(
        shared.get().control.snapshot().unwrap().phase(),
        MigrationPhase::Reclaimed
    );
    // SAFETY: the test owns and authenticates the unique shared control record;
    // no source bytes or external cleanup exist in this synthetic fixture.
    let resumed = unsafe { shared.get().control.resume_reclamation() }.unwrap();
    assert_eq!(resumed.plan(), plan);
    assert!(resumed.is_resume());
}
