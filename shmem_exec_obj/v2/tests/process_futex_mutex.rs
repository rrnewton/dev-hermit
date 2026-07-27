#![cfg(all(feature = "linux-futex", target_os = "linux"))]

use shmem_pod::sync::ProcessFutexMutex;
use shmem_pod::{PodSync, PodValue};
use std::hint::spin_loop;
use std::mem::size_of;
use std::ops::Deref;
use std::process::{Child, Command, ExitStatus};
use std::ptr;
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

fn require_process_shared<T: PodValue + PodSync>() {}

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
