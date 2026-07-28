#![cfg(all(feature = "linux-futex", target_os = "linux"))]

use shmem_pod::sync::ProcessFutexMutex;
use shmem_pod::{PodSync, PodValue};
use std::hint::spin_loop;
use std::mem::size_of;
use std::ops::Deref;
use std::process::{Child, Command, ExitStatus};
use std::ptr;
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
use std::sync::atomic::AtomicU64;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

struct SharedMapping<T> {
    pointer: *mut T,
    descriptor: Option<libc::c_int>,
}

impl<T> SharedMapping<T> {
    fn new(value: T) -> Self {
        // SAFETY: mmap returns page-aligned storage large enough for T. The
        // mapping is private to this test until value has been initialized.
        let pointer = unsafe {
            libc::mmap(
                ptr::null_mut(),
                size_of::<T>(),
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(pointer, libc::MAP_FAILED, "mmap failed");
        let pointer = pointer.cast::<T>();
        // SAFETY: pointer refers to writable, properly aligned, uninitialized
        // storage of at least size_of::<T>() bytes.
        unsafe { pointer.write(value) };
        Self {
            pointer,
            descriptor: None,
        }
    }

    fn new_memfd(value: T) -> Self {
        // SAFETY: the name is NUL-terminated and flags=0 deliberately leaves
        // the descriptor open across exec for this test.
        let descriptor = unsafe { libc::memfd_create(c"shmem-pod-futex-test".as_ptr(), 0) };
        assert!(descriptor >= 0, "memfd_create failed");
        // SAFETY: descriptor is a writable memfd and the length fits off_t on
        // the supported Linux targets.
        assert_eq!(
            unsafe { libc::ftruncate(descriptor, size_of::<T>() as libc::off_t) },
            0,
            "ftruncate failed"
        );
        // SAFETY: mmap creates a shared, page-aligned mapping of the memfd.
        let pointer = unsafe {
            libc::mmap(
                ptr::null_mut(),
                size_of::<T>(),
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                descriptor,
                0,
            )
        };
        assert_ne!(pointer, libc::MAP_FAILED, "memfd mmap failed");
        let pointer = pointer.cast::<T>();
        // SAFETY: pointer refers to writable, properly aligned, uninitialized
        // storage of at least size_of::<T>() bytes.
        unsafe { pointer.write(value) };
        Self {
            pointer,
            descriptor: Some(descriptor),
        }
    }

    fn descriptor(&self) -> libc::c_int {
        self.descriptor.expect("mapping is not memfd-backed")
    }
}

impl<T> Deref for SharedMapping<T> {
    type Target = T;

    fn deref(&self) -> &Self::Target {
        // SAFETY: the mapping remains live for self's lifetime. Concurrent
        // mutation is confined to atomics and ProcessFutexMutex.
        unsafe { &*self.pointer }
    }
}

impl<T> Drop for SharedMapping<T> {
    fn drop(&mut self) {
        // SAFETY: this is the original address and length returned by mmap.
        let result = unsafe { libc::munmap(self.pointer.cast(), size_of::<T>()) };
        assert_eq!(result, 0, "munmap failed");
        if let Some(descriptor) = self.descriptor {
            // SAFETY: descriptor is owned by this mapping and still open.
            assert_eq!(unsafe { libc::close(descriptor) }, 0, "close failed");
        }
    }
}

fn wait_until(timeout: Duration, mut predicate: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    predicate()
}

fn wait_for_command(child: &mut Child, timeout: Duration) -> ExitStatus {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait().expect("poll exec worker") {
            return status;
        }
        if Instant::now() >= deadline {
            let pid = child.id();
            let kill_result = child.kill();
            let status = child.wait().expect("reap timed-out exec worker");
            panic!(
                "exec worker {pid} did not exit within {timeout:?}; kill={kill_result:?}, status={status}"
            );
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

fn terminate_and_reap(children: &[libc::pid_t]) {
    for &child in children {
        // SAFETY: child is a direct child PID. ESRCH merely means it exited
        // between the last nonblocking poll and this cleanup pass.
        let _ = unsafe { libc::kill(child, libc::SIGKILL) };
    }
    for &child in children {
        let mut status = 0;
        loop {
            // SAFETY: child is a direct child and status is writable. A killed
            // futex waiter cannot remain asleep after SIGKILL.
            let result = unsafe { libc::waitpid(child, &mut status, 0) };
            if result == child {
                break;
            }
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            // ECHILD means a racing nonblocking poll already reaped it. Other
            // failures cannot be repaired here, but the cleanup stays bounded.
            break;
        }
    }
}

fn wait_for_children(
    children: Vec<libc::pid_t>,
    timeout: Duration,
) -> Vec<(libc::pid_t, libc::c_int)> {
    let deadline = Instant::now() + timeout;
    let mut pending = children;
    let mut completed = Vec::with_capacity(pending.len());

    while !pending.is_empty() {
        let mut index = 0;
        while index < pending.len() {
            let child = pending[index];
            let mut status = 0;
            // SAFETY: child is a direct child, status is writable, and WNOHANG
            // makes this poll nonblocking if the child remains alive.
            let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) };
            if result == child {
                pending.swap_remove(index);
                completed.push((child, status));
            } else if result == 0 {
                index += 1;
            } else {
                let error = std::io::Error::last_os_error();
                terminate_and_reap(&pending);
                panic!("waitpid({child}) failed: {error}");
            }
        }

        if pending.is_empty() {
            break;
        }
        if Instant::now() >= deadline {
            let timed_out = pending.clone();
            terminate_and_reap(&pending);
            panic!("children {timed_out:?} did not exit within {timeout:?}");
        }
        std::thread::sleep(Duration::from_millis(1));
    }

    completed
}

#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn wait_for_child_stop(child: libc::pid_t, timeout: Duration) -> libc::c_int {
    let deadline = Instant::now() + timeout;
    loop {
        let mut status = 0;
        // SAFETY: child is a direct child, status is writable, and these flags
        // report a SIGSTOP without waiting indefinitely.
        let result = unsafe { libc::waitpid(child, &mut status, libc::WNOHANG | libc::WUNTRACED) };
        if result == child {
            return status;
        }
        if result < 0 {
            let error = std::io::Error::last_os_error();
            terminate_and_reap(&[child]);
            panic!("waitpid({child}, WUNTRACED) failed: {error}");
        }
        if Instant::now() >= deadline {
            terminate_and_reap(&[child]);
            panic!("child {child} did not stop within {timeout:?}");
        }
        std::thread::sleep(Duration::from_millis(1));
    }
}

fn require_process_shared<T: PodValue + PodSync>() {}

#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
static DELIVERED_SIGNALS: AtomicU32 = AtomicU32::new(0);

#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
extern "C" fn count_signal_handler(_: libc::c_int) {
    DELIVERED_SIGNALS.fetch_add(1, Ordering::Relaxed);
}

const WORKER_FD_ENV: &str = "SHMEM_POD_FUTEX_TEST_FD";
const PARENT_ADDRESS_ENV: &str = "SHMEM_POD_FUTEX_PARENT_ADDRESS";

struct ExecShared {
    mutex: ProcessFutexMutex<u64>,
    started: AtomicU32,
    acquired: AtomicU32,
    worker_address: AtomicUsize,
}

#[cfg(target_pointer_width = "64")]
const WORKER_ADDRESSES: [usize; 4] = [
    0x2000_0000_0000,
    0x3000_0000_0000,
    0x4000_0000_0000,
    0x5000_0000_0000,
];

#[cfg(target_pointer_width = "32")]
const WORKER_ADDRESSES: [usize; 4] = [0x3000_0000, 0x5000_0000, 0x7000_0000, 0x9000_0000];

fn map_worker_at_different_address(
    descriptor: libc::c_int,
    parent_address: usize,
) -> *mut ExecShared {
    for address in WORKER_ADDRESSES {
        if address == parent_address {
            continue;
        }
        // SAFETY: each candidate is page-aligned. MAP_FIXED_NOREPLACE ensures
        // an existing worker mapping is never overwritten.
        let mapping = unsafe {
            libc::mmap(
                address as *mut libc::c_void,
                size_of::<ExecShared>(),
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_FIXED_NOREPLACE,
                descriptor,
                0,
            )
        };
        if mapping == libc::MAP_FAILED {
            continue;
        }
        if mapping as usize == address {
            return mapping.cast();
        }
        // Old kernels may ignore an unknown MAP_FIXED_NOREPLACE flag. Reject a
        // mapping returned at an address other than the requested one.
        // SAFETY: mapping and length came from the successful mmap above.
        assert_eq!(unsafe { libc::munmap(mapping, size_of::<ExecShared>()) }, 0);
    }
    panic!("could not reserve a distinct worker mapping address");
}

#[test]
fn exec_worker_maps_shared_futex() {
    let Ok(descriptor) = std::env::var(WORKER_FD_ENV) else {
        // The ordinary test-harness invocation is not the exec worker.
        return;
    };
    let descriptor = descriptor.parse::<libc::c_int>().unwrap();
    let parent_address = std::env::var(PARENT_ADDRESS_ENV)
        .unwrap()
        .parse::<usize>()
        .unwrap();
    let mapping = map_worker_at_different_address(descriptor, parent_address);
    // SAFETY: mapping points to the initialized shared memfd object.
    let shared = unsafe { &*mapping };
    shared
        .worker_address
        .store(mapping as usize, Ordering::Release);
    shared.started.store(1, Ordering::Release);
    {
        let mut guard = shared.mutex.lock();
        *guard += 1;
        shared.acquired.store(1, Ordering::Release);
    }
    // SAFETY: the guard is gone and this worker will no longer access mapping.
    assert_eq!(
        unsafe { libc::munmap(mapping.cast(), size_of::<ExecShared>()) },
        0
    );
}

#[test]
fn contended_waiter_sleeps_and_wakes_across_processes() {
    require_process_shared::<ProcessFutexMutex<u64>>();
    let shared = SharedMapping::new_memfd(ExecShared {
        mutex: ProcessFutexMutex::new(0),
        started: AtomicU32::new(0),
        acquired: AtomicU32::new(0),
        worker_address: AtomicUsize::new(0),
    });

    let parent_guard = shared.mutex.lock();
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "exec_worker_maps_shared_futex", "--nocapture"])
        .env(WORKER_FD_ENV, shared.descriptor().to_string())
        .env(PARENT_ADDRESS_ENV, (shared.pointer as usize).to_string())
        .spawn()
        .expect("spawn exec worker");
    let child_pid = child.id() as libc::pid_t;

    let started = wait_until(Duration::from_secs(2), || {
        shared.started.load(Ordering::Acquire) == 1
    });
    let marked_contended = wait_until(Duration::from_secs(2), || shared.mutex.is_contended());
    let wchan_path = format!("/proc/{child_pid}/wchan");
    let slept_in_futex = wait_until(Duration::from_secs(2), || {
        std::fs::read_to_string(&wchan_path)
            .is_ok_and(|wait_channel| wait_channel.contains("futex"))
    });
    let acquired_while_held = shared.acquired.load(Ordering::Acquire);

    drop(parent_guard);
    let status = wait_for_command(&mut child, Duration::from_secs(5));

    assert!(started, "worker did not begin lock acquisition");
    assert!(marked_contended, "worker did not mark the futex contended");
    assert!(
        slept_in_futex,
        "worker never appeared in a futex wait channel"
    );
    assert_eq!(acquired_while_held, 0, "worker bypassed the held lock");
    assert!(status.success(), "exec worker failed: {status}");
    assert_ne!(
        shared.worker_address.load(Ordering::Acquire),
        shared.pointer as usize,
        "worker did not use a different virtual address"
    );
    assert_eq!(shared.acquired.load(Ordering::Acquire), 1);
    assert_eq!(*shared.mutex.lock(), 1);
}

#[test]
fn forked_workers_produce_exact_total_under_contention() {
    const CHILDREN: u32 = 6;
    const INCREMENTS: u64 = 20_000;

    struct Shared {
        counter: ProcessFutexMutex<u64>,
        ready: AtomicU32,
        start: AtomicU32,
    }

    let shared = SharedMapping::new(Shared {
        counter: ProcessFutexMutex::new(0),
        ready: AtomicU32::new(0),
        start: AtomicU32::new(0),
    });
    let mut children = Vec::new();

    for _ in 0..CHILDREN {
        // SAFETY: children use only shared atomics/the mutex and then _exit.
        let child = unsafe { libc::fork() };
        if child < 0 {
            shared.start.store(1, Ordering::Release);
            terminate_and_reap(&children);
            panic!("fork failed: {}", std::io::Error::last_os_error());
        }
        if child == 0 {
            shared.ready.fetch_add(1, Ordering::AcqRel);
            while shared.start.load(Ordering::Acquire) == 0 {
                spin_loop();
            }
            for _ in 0..INCREMENTS {
                *shared.counter.lock() += 1;
            }
            // SAFETY: bypass Rust destructors in the post-fork child.
            unsafe { libc::_exit(0) };
        }
        children.push(child);
    }

    let all_ready = wait_until(Duration::from_secs(2), || {
        shared.ready.load(Ordering::Acquire) == CHILDREN
    });
    shared.start.store(1, Ordering::Release);
    if !all_ready {
        terminate_and_reap(&children);
        panic!("workers did not reach the start gate");
    }

    for (child, status) in wait_for_children(children, Duration::from_secs(10)) {
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0, "child {child} failed");
    }

    assert_eq!(*shared.counter.lock(), u64::from(CHILDREN) * INCREMENTS);
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn zero_timeout_is_an_immediate_attempt() {
    let shared = SharedMapping::new(ProcessFutexMutex::new(7_u64));
    let guard = shared.lock();
    assert!(
        shared
            .try_lock_for(Duration::ZERO)
            .expect("zero-duration attempt")
            .is_none()
    );
    drop(guard);
    assert_eq!(
        *shared
            .try_lock_for(Duration::ZERO)
            .expect("uncontended zero-duration attempt")
            .expect("available mutex must be acquired"),
        7
    );
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn unrepresentable_timeout_is_reported() {
    let shared = SharedMapping::new(ProcessFutexMutex::new(()));
    let guard = shared.lock();
    let error = match shared.try_lock_for(Duration::MAX) {
        Err(error) => error,
        Ok(Some(second_guard)) => {
            drop(second_guard);
            panic!("unrepresentable timeout bypassed the held mutex");
        }
        Ok(None) => panic!("unrepresentable timeout was mistaken for expiration"),
    };
    drop(guard);

    assert_eq!(error.raw_os_error(), libc::EOVERFLOW);
    assert!(shared.try_lock().is_some());
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn timeout_unlock_boundary_preserves_one_owner() {
    const ROUNDS: usize = 64;

    for _ in 0..ROUNDS {
        let mutex = std::sync::Arc::new(ProcessFutexMutex::new(0_u32));
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let owner = mutex.lock();

        let waiter_mutex = std::sync::Arc::clone(&mutex);
        let waiter_barrier = std::sync::Arc::clone(&barrier);
        let waiter = std::thread::spawn(move || {
            waiter_barrier.wait();
            match waiter_mutex.try_lock_for(Duration::from_millis(1)) {
                Ok(Some(mut guard)) => {
                    *guard += 1;
                    true
                }
                Ok(None) => false,
                Err(error) => panic!("timed wait failed: {error}"),
            }
        });

        barrier.wait();
        std::thread::sleep(Duration::from_millis(1));
        drop(owner);
        let waiter_acquired = waiter.join().expect("boundary waiter panicked");

        let final_guard = mutex
            .lock_fallible()
            .expect("mutex stuck after boundary race");
        assert_eq!(*final_guard, u32::from(waiter_acquired));
    }
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn timed_waiter_acquires_when_owner_releases() {
    struct Shared {
        mutex: ProcessFutexMutex<u64>,
        start: AtomicU32,
        result: AtomicU32,
    }

    let shared = SharedMapping::new(Shared {
        mutex: ProcessFutexMutex::new(0),
        start: AtomicU32::new(0),
        result: AtomicU32::new(0),
    });

    // SAFETY: no guard exists across fork; the child uses only shared POD
    // synchronization and exits without running copied test-harness drops.
    let child = unsafe { libc::fork() };
    assert!(
        child >= 0,
        "fork failed: {}",
        std::io::Error::last_os_error()
    );
    if child == 0 {
        while shared.start.load(Ordering::Acquire) == 0 {
            spin_loop();
        }
        let result = match shared.mutex.try_lock_for(Duration::from_secs(2)) {
            Ok(Some(mut guard)) => {
                *guard += 1;
                1
            }
            Ok(None) => 2,
            Err(error) => 1_000 + error.raw_os_error() as u32,
        };
        shared.result.store(result, Ordering::Release);
        // SAFETY: the child has dropped any acquired guard and must bypass
        // copied test-harness destructors.
        unsafe { libc::_exit(0) };
    }

    let guard = shared.mutex.lock();
    shared.start.store(1, Ordering::Release);
    let contended = wait_until(Duration::from_secs(2), || shared.mutex.is_contended());
    if !contended {
        drop(guard);
        terminate_and_reap(&[child]);
        panic!("timed waiter did not enter the futex slow path");
    }
    drop(guard);

    let completed = wait_for_children(vec![child], Duration::from_secs(5));
    assert!(libc::WIFEXITED(completed[0].1));
    assert_eq!(libc::WEXITSTATUS(completed[0].1), 0);
    assert_eq!(shared.result.load(Ordering::Acquire), 1);
    assert_eq!(*shared.mutex.lock(), 1);
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn signals_do_not_restart_the_timeout_budget() {
    struct Shared {
        mutex: ProcessFutexMutex<u64>,
        ready: AtomicU32,
        start: AtomicU32,
        result: AtomicU32,
        elapsed_ns: AtomicU64,
        delivered_signals: AtomicU32,
    }

    let shared = SharedMapping::new(Shared {
        mutex: ProcessFutexMutex::new(0),
        ready: AtomicU32::new(0),
        start: AtomicU32::new(0),
        result: AtomicU32::new(0),
        elapsed_ns: AtomicU64::new(0),
        delivered_signals: AtomicU32::new(0),
    });

    // SAFETY: no guard exists across fork. The child installs a private signal
    // disposition, touches shared atomics/the mutex, and exits without unwind.
    let child = unsafe { libc::fork() };
    assert!(
        child >= 0,
        "fork failed: {}",
        std::io::Error::last_os_error()
    );
    if child == 0 {
        // SAFETY: a zeroed sigaction is a valid starting representation on
        // Linux; the mask is initialized before the action is installed.
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        DELIVERED_SIGNALS.store(0, Ordering::Relaxed);
        action.sa_sigaction = count_signal_handler as *const () as usize;
        action.sa_flags = 0;
        // SAFETY: action and its mask are valid writable objects.
        if unsafe { libc::sigemptyset(&mut action.sa_mask) } != 0
            || unsafe { libc::sigaction(libc::SIGUSR1, &action, ptr::null_mut()) } != 0
        {
            unsafe { libc::_exit(3) };
        }
        shared.ready.store(1, Ordering::Release);
        while shared.start.load(Ordering::Acquire) == 0 {
            spin_loop();
        }

        let started = Instant::now();
        let result = match shared.mutex.try_lock_for(Duration::from_millis(100)) {
            Ok(Some(_guard)) => 2,
            Ok(None) => 1,
            Err(error) => 1_000 + error.raw_os_error() as u32,
        };
        let elapsed = started.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64;
        shared
            .delivered_signals
            .store(DELIVERED_SIGNALS.load(Ordering::Relaxed), Ordering::Release);
        shared.elapsed_ns.store(elapsed, Ordering::Release);
        shared.result.store(result, Ordering::Release);
        // SAFETY: any guard from the result match has been dropped.
        unsafe { libc::_exit(0) };
    }

    if !wait_until(Duration::from_secs(2), || {
        shared.ready.load(Ordering::Acquire) == 1
    }) {
        terminate_and_reap(&[child]);
        panic!("signal worker did not initialize");
    }
    let guard = shared.mutex.lock();
    shared.start.store(1, Ordering::Release);
    if !wait_until(Duration::from_secs(2), || shared.mutex.is_contended()) {
        drop(guard);
        terminate_and_reap(&[child]);
        panic!("signal worker did not enter the futex slow path");
    }

    let signal_deadline = Instant::now() + Duration::from_millis(500);
    while shared.result.load(Ordering::Acquire) == 0 && Instant::now() < signal_deadline {
        // SAFETY: child cannot be PID-reused before it is reaped. ESRCH merely
        // means it has already completed the timed wait and exited.
        let _ = unsafe { libc::kill(child, libc::SIGUSR1) };
        std::thread::sleep(Duration::from_millis(5));
    }
    let completed_before_signals_stopped = shared.result.load(Ordering::Acquire) != 0;
    if !completed_before_signals_stopped {
        drop(guard);
        terminate_and_reap(&[child]);
        panic!("repeated EINTR restarted the timeout budget");
    }
    let completed = wait_for_children(vec![child], Duration::from_secs(5));
    drop(guard);

    let elapsed = Duration::from_nanos(shared.elapsed_ns.load(Ordering::Acquire));
    assert!(libc::WIFEXITED(completed[0].1));
    assert_eq!(libc::WEXITSTATUS(completed[0].1), 0);
    assert_eq!(shared.result.load(Ordering::Acquire), 1);
    assert!(
        shared.delivered_signals.load(Ordering::Acquire) > 0,
        "the signal handler never ran, so EINTR was not exercised"
    );
    assert!(
        elapsed >= Duration::from_millis(70),
        "timeout expired unexpectedly early after {elapsed:?}"
    );
    assert!(
        elapsed < Duration::from_millis(500),
        "signals extended the absolute deadline to {elapsed:?}"
    );
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn timeout_does_not_steal_from_a_paused_owner() {
    struct Shared {
        mutex: ProcessFutexMutex<u64>,
        held: AtomicU32,
    }

    let shared = SharedMapping::new(Shared {
        mutex: ProcessFutexMutex::new(0),
        held: AtomicU32::new(0),
    });

    // SAFETY: no guard exists across fork. The child is the sole mutex owner
    // until it publishes `held` and stops itself.
    let child = unsafe { libc::fork() };
    assert!(
        child >= 0,
        "fork failed: {}",
        std::io::Error::last_os_error()
    );
    if child == 0 {
        let mut guard = shared.mutex.lock();
        *guard = 41;
        shared.held.store(1, Ordering::Release);
        // SAFETY: SIGSTOP pauses this process until the parent explicitly
        // resumes it; it does not run signal-handler code while holding guard.
        if unsafe { libc::raise(libc::SIGSTOP) } != 0 {
            unsafe { libc::_exit(3) };
        }
        *guard = 42;
        drop(guard);
        // SAFETY: all shared guards are gone; bypass copied harness drops.
        unsafe { libc::_exit(0) };
    }

    if !wait_until(Duration::from_secs(2), || {
        shared.held.load(Ordering::Acquire) == 1
    }) {
        terminate_and_reap(&[child]);
        panic!("owner did not acquire the mutex");
    }
    let stopped_status = wait_for_child_stop(child, Duration::from_secs(2));
    if !libc::WIFSTOPPED(stopped_status) {
        terminate_and_reap(&[child]);
        panic!("owner exited instead of stopping: {stopped_status:#x}");
    }

    let started = Instant::now();
    let attempt = shared
        .mutex
        .try_lock_for(Duration::from_millis(80))
        .expect("timed futex wait");
    let elapsed = started.elapsed();
    if attempt.is_some() {
        // Keep the erroneous second guard away from a resumed first owner.
        // SAFETY: child is stopped and is a direct child process.
        let _ = unsafe { libc::kill(child, libc::SIGKILL) };
        let _ = wait_for_children(vec![child], Duration::from_secs(5));
        drop(attempt);
        panic!("timeout stole the mutex from a paused live owner");
    }
    assert!(shared.mutex.is_locked());

    // SAFETY: child is stopped and remains the legitimate mutex owner.
    assert_eq!(unsafe { libc::kill(child, libc::SIGCONT) }, 0);
    let completed = wait_for_children(vec![child], Duration::from_secs(5));

    assert!(
        elapsed >= Duration::from_millis(50),
        "deadline expired unexpectedly early after {elapsed:?}"
    );
    assert!(libc::WIFEXITED(completed[0].1));
    assert_eq!(libc::WEXITSTATUS(completed[0].1), 0);
    assert_eq!(*shared.mutex.lock(), 42);
}

#[test]
#[cfg(all(
    target_pointer_width = "64",
    any(target_arch = "x86_64", target_arch = "aarch64")
))]
fn killed_owner_remains_locked_without_a_robust_protocol() {
    struct Shared {
        mutex: ProcessFutexMutex<u64>,
        held: AtomicU32,
    }

    let shared = SharedMapping::new(Shared {
        mutex: ProcessFutexMutex::new(0),
        held: AtomicU32::new(0),
    });

    // SAFETY: no guard exists across fork. The child deliberately exits while
    // holding the non-robust mutex to verify the documented fail-closed state.
    let child = unsafe { libc::fork() };
    assert!(
        child >= 0,
        "fork failed: {}",
        std::io::Error::last_os_error()
    );
    if child == 0 {
        let _guard = shared.mutex.lock();
        shared.held.store(1, Ordering::Release);
        loop {
            // SAFETY: wait for the parent's SIGKILL without touching state.
            unsafe { libc::pause() };
        }
    }

    if !wait_until(Duration::from_secs(2), || {
        shared.held.load(Ordering::Acquire) == 1
    }) {
        terminate_and_reap(&[child]);
        panic!("owner did not acquire the mutex");
    }
    // SAFETY: child is a direct child and is intentionally killed while it
    // holds the mutex.
    assert_eq!(unsafe { libc::kill(child, libc::SIGKILL) }, 0);
    let completed = wait_for_children(vec![child], Duration::from_secs(5));
    assert!(libc::WIFSIGNALED(completed[0].1));
    assert_eq!(libc::WTERMSIG(completed[0].1), libc::SIGKILL);

    assert!(
        shared
            .mutex
            .try_lock_for(Duration::from_millis(50))
            .expect("timed wait after owner death")
            .is_none(),
        "a plain futex timeout must not infer owner death"
    );
    assert!(shared.mutex.try_lock().is_none());
    assert!(shared.mutex.is_locked());
}
