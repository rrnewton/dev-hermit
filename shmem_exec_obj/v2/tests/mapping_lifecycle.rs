#![cfg(all(
    feature = "derive",
    target_os = "linux",
    target_has_atomic = "64"
))]

use std::process::{Child, Command, ExitStatus};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use shmem_pod::mapping::{BuildIdentity, InstanceIdentity, MappingError, Phase, RawMapping};

const MAPPING_LEN: usize = 4096;
const BUILD: BuildIdentity = BuildIdentity::new([0x42; 32]);
const INSTANCE: InstanceIdentity = InstanceIdentity::new([0x17; 16]);
const EXEC_FD: &str = "SHMEM_POD_MAPPING_TEST_FD";
const EXEC_PARENT_ADDRESS: &str = "SHMEM_POD_MAPPING_TEST_PARENT_ADDRESS";

#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct SharedState {
    value: AtomicU64,
}

impl SharedState {
    const fn new(value: u64) -> Self {
        Self {
            value: AtomicU64::new(value),
        }
    }
}

#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct DifferentState {
    first: AtomicU64,
    second: AtomicU64,
}

struct SharedBytes {
    base: *mut u8,
    descriptor: Option<libc::c_int>,
}

impl SharedBytes {
    fn anonymous() -> Self {
        // SAFETY: a one-page shared anonymous mapping is writable and aligned.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                MAPPING_LEN,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        assert_ne!(mapping, libc::MAP_FAILED, "anonymous mmap failed");
        Self {
            base: mapping.cast(),
            descriptor: None,
        }
    }

    fn memfd() -> Self {
        // SAFETY: name is NUL terminated. Flags deliberately omit CLOEXEC so
        // the test worker can attach after exec.
        let descriptor = unsafe { libc::memfd_create(c"shmem-pod-mapping-test".as_ptr(), 0) };
        assert!(descriptor >= 0, "memfd_create failed");
        // SAFETY: descriptor is writable and MAPPING_LEN fits off_t here.
        assert_eq!(
            unsafe { libc::ftruncate(descriptor, MAPPING_LEN as libc::off_t) },
            0,
            "ftruncate failed"
        );
        // SAFETY: creates a writable shared mapping of the complete memfd.
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                MAPPING_LEN,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                descriptor,
                0,
            )
        };
        assert_ne!(mapping, libc::MAP_FAILED, "memfd mmap failed");
        Self {
            base: mapping.cast(),
            descriptor: Some(descriptor),
        }
    }

    unsafe fn raw(&self) -> RawMapping<'_> {
        // SAFETY: SharedBytes owns a live writable page for this borrow. Tests
        // serialize direct byte mutation against typed API operations.
        unsafe { RawMapping::from_raw_parts(self.base, MAPPING_LEN).unwrap() }
    }
}

impl Drop for SharedBytes {
    fn drop(&mut self) {
        // SAFETY: base and length are the live mapping created by this value.
        assert_eq!(unsafe { libc::munmap(self.base.cast(), MAPPING_LEN) }, 0);
        if let Some(descriptor) = self.descriptor {
            // SAFETY: this value owns the still-open descriptor.
            assert_eq!(unsafe { libc::close(descriptor) }, 0);
        }
    }
}

fn wait_for_child(child: &mut Child, timeout: Duration) -> ExitStatus {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait().expect("poll exec worker") {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let status = child.wait().expect("reap timed-out exec worker");
            panic!("exec worker timed out: {status}");
        }
        std::thread::sleep(Duration::from_millis(1));
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

fn wait_for_pids(mut pending: Vec<libc::pid_t>, timeout: Duration) -> Vec<libc::c_int> {
    let deadline = Instant::now() + timeout;
    let mut statuses = Vec::with_capacity(pending.len());
    while !pending.is_empty() {
        let mut index = 0;
        while index < pending.len() {
            let mut status = 0;
            // SAFETY: this polls a direct child without blocking.
            let result = unsafe { libc::waitpid(pending[index], &mut status, libc::WNOHANG) };
            if result == pending[index] {
                pending.swap_remove(index);
                statuses.push(status);
            } else if result == 0 {
                index += 1;
            } else {
                panic!("waitpid failed: {}", std::io::Error::last_os_error());
            }
        }
        if pending.is_empty() {
            break;
        }
        if Instant::now() >= deadline {
            for child in &pending {
                // SAFETY: bounded test cleanup for direct children.
                let _ = unsafe { libc::kill(*child, libc::SIGKILL) };
            }
            for child in pending {
                // SAFETY: reap every child killed above.
                let _ = unsafe { libc::waitpid(child, ptr::null_mut(), 0) };
            }
            panic!("mapping worker processes timed out");
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    statuses
}

#[test]
fn typed_lifecycle_closes_only_after_attachments_and_admissions_drain() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: this is the exclusive initial preparation of the page.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let owner = mapping.try_initialize(SharedState::new(7)).unwrap();
    let attachment = mapping.attach::<SharedState>().unwrap();
    let guard = attachment.try_enter().unwrap();
    guard.value.fetch_add(5, Ordering::Relaxed);

    let draining = owner.begin_drain().unwrap();
    assert_eq!(draining.is_drained(), Ok(false));
    assert!(matches!(
        mapping.attach::<SharedState>(),
        Err(MappingError::WrongPhase {
            actual: Phase::Draining,
            ..
        })
    ));
    assert!(matches!(
        attachment.try_enter(),
        Err(MappingError::WrongPhase {
            actual: Phase::Draining,
            ..
        })
    ));

    let (error, draining) = draining.try_close().unwrap_err();
    assert_eq!(error, MappingError::NotDrained);
    drop(guard);
    drop(attachment);
    assert_eq!(draining.is_drained(), Ok(true));

    let mut draining = draining;
    assert_eq!(
        draining
            .try_payload_mut()
            .unwrap()
            .value
            .load(Ordering::Relaxed),
        12
    );
    draining
        .try_payload_mut()
        .unwrap()
        .value
        .store(19, Ordering::Relaxed);
    let closed = draining.try_close().unwrap();
    assert_eq!(closed.base().as_ptr(), bytes.base);
    assert_eq!(closed.len(), MAPPING_LEN);
    assert!(matches!(
        mapping.attach::<SharedState>(),
        Err(MappingError::WrongPhase {
            actual: Phase::Closed,
            ..
        })
    ));
}

#[test]
fn identity_layout_and_owner_loss_fail_closed() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);

    // SAFETY: the header is fully prepared and remains live. No payload
    // reference is formed by a failed identity check.
    let wrong_build = unsafe {
        bytes
            .raw()
            .open_existing(BuildIdentity::new([0x99; 32]), INSTANCE)
    };
    assert!(matches!(wrong_build, Err(MappingError::BuildIdentity)));
    // SAFETY: same prepared-header justification as above.
    let wrong_instance = unsafe {
        bytes
            .raw()
            .open_existing(BUILD, InstanceIdentity::new([0x88; 16]))
    };
    assert!(matches!(
        wrong_instance,
        Err(MappingError::InstanceIdentity)
    ));

    let owner = mapping.try_initialize(SharedState::new(1)).unwrap();
    assert!(matches!(
        mapping.attach::<DifferentState>(),
        Err(MappingError::LayoutMismatch(_))
    ));
    drop(owner);
    let snapshot = mapping.snapshot().unwrap();
    assert_eq!(snapshot.phase(), Phase::Poisoned);
    assert_eq!(snapshot.attachments(), 0);
    assert!(matches!(
        mapping.attach::<SharedState>(),
        Err(MappingError::WrongPhase {
            actual: Phase::Poisoned,
            ..
        })
    ));
}

#[test]
fn exactly_one_racing_initializer_wins() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let mut results = Vec::new();
    std::thread::scope(|scope| {
        let mut workers = Vec::new();
        for value in 0..16 {
            workers.push(scope.spawn(move || mapping.try_initialize(SharedState::new(value))));
        }
        for worker in workers {
            results.push(worker.join().expect("initializer thread panicked"));
        }
    });

    let mut winner = None;
    let mut losers = 0;
    for result in results {
        match result {
            Ok(owner) => winner = Some(owner),
            Err(MappingError::WrongPhase { .. }) => losers += 1,
            Err(error) => panic!("unexpected initialization error: {error}"),
        }
    }
    assert_eq!(losers, 15);
    let owner = winner.expect("one initializer must win");
    let draining = owner.begin_drain().unwrap();
    assert!(draining.is_drained().unwrap());
    draining.try_close().unwrap();
}

#[test]
fn drain_linearizes_against_racing_attach_and_enter() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let owner = mapping.try_initialize(SharedState::new(0)).unwrap();
    let start = AtomicBool::new(false);

    let draining = std::thread::scope(|scope| {
        let mut workers = Vec::new();
        for _ in 0..8 {
            workers.push(scope.spawn(|| {
                while !start.load(Ordering::Acquire) {
                    std::hint::spin_loop();
                }
                loop {
                    let attachment = match mapping.attach::<SharedState>() {
                        Ok(attachment) => attachment,
                        Err(MappingError::WrongPhase {
                            actual: Phase::Draining,
                            ..
                        }) => break,
                        Err(error) => panic!("unexpected attach error: {error}"),
                    };
                    match attachment.try_enter() {
                        Ok(guard) => {
                            guard.value.fetch_add(1, Ordering::Relaxed);
                        }
                        Err(MappingError::WrongPhase {
                            actual: Phase::Draining,
                            ..
                        }) => {}
                        Err(error) => panic!("unexpected admission error: {error}"),
                    }
                }
            }));
        }
        start.store(true, Ordering::Release);
        while owner.snapshot().unwrap().attachments() == 1 {
            std::hint::spin_loop();
        }
        let draining = owner.begin_drain().unwrap();
        for worker in workers {
            worker.join().expect("attach worker panicked");
        }
        draining
    });

    assert!(draining.is_drained().unwrap());
    draining.try_close().unwrap();
}

#[test]
fn poison_wins_over_an_initializer_waiting_to_publish() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let entered = AtomicBool::new(false);
    let proceed = AtomicBool::new(false);

    std::thread::scope(|scope| {
        let initializer = scope.spawn(|| {
            // SAFETY: the callback writes one complete valid value before it
            // returns. Synchronization only delays publication.
            unsafe {
                mapping.try_initialize_in_place::<SharedState>(|target| {
                    target.write(SharedState::new(3));
                    entered.store(true, Ordering::Release);
                    while !proceed.load(Ordering::Acquire) {
                        std::hint::spin_loop();
                    }
                })
            }
        });
        assert!(wait_until(Duration::from_secs(2), || {
            entered.load(Ordering::Acquire)
        }));
        mapping.poison().unwrap();
        proceed.store(true, Ordering::Release);
        assert!(matches!(
            initializer.join().expect("initializer panicked"),
            Err(MappingError::WrongPhase {
                expected: Phase::Initializing,
                actual: Phase::Poisoned,
            })
        ));
    });
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Poisoned);
}

#[test]
fn dropping_drain_authority_poisons_instead_of_silently_stranding() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let owner = mapping.try_initialize(SharedState::new(0)).unwrap();
    let draining = owner.begin_drain().unwrap();
    drop(draining);
    let snapshot = mapping.snapshot().unwrap();
    assert_eq!(snapshot.phase(), Phase::Poisoned);
    assert_eq!(snapshot.attachments(), 0);
}

#[test]
fn independent_processes_race_initialization_with_exactly_one_winner() {
    const WORKERS: usize = 8;
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation before fork.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let mut children = Vec::new();
    for value in 0..WORKERS {
        // SAFETY: children access only the shared lifecycle/payload and exit
        // without unwinding duplicated process state.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            let exit_code = match mapping.try_initialize(SharedState::new(value as u64)) {
                Ok(owner) => {
                    // Model abrupt owner death without running the authority's
                    // poison-on-drop path.
                    core::mem::forget(owner);
                    10
                }
                Err(_) => 0,
            };
            unsafe { libc::_exit(exit_code) };
        }
        children.push(child);
    }
    let statuses = wait_for_pids(children, Duration::from_secs(5));
    assert_eq!(
        statuses
            .iter()
            .filter(|status| libc::WIFEXITED(**status) && libc::WEXITSTATUS(**status) == 10)
            .count(),
        1
    );
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Open);
    let attachment = mapping.attach::<SharedState>().unwrap();
    assert!(attachment.try_enter().is_ok());
    drop(attachment);
    // The winning process deliberately _exit'd with close authority. A
    // supervisor must poison and replace this otherwise valid generation.
    mapping.poison().unwrap();
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Poisoned);
}

#[test]
fn killed_initializer_and_admitted_process_fail_stuck_until_supervisor_poison() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation before fork.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    // SAFETY: child initializes only the shared mapping and is externally
    // killed after it has entered the callback.
    let initializer = unsafe { libc::fork() };
    assert!(initializer >= 0, "fork failed");
    if initializer == 0 {
        let _ = unsafe {
            mapping.try_initialize_in_place::<SharedState>(|target| {
                target.write(SharedState::new(0));
                loop {
                    core::hint::spin_loop();
                }
            })
        };
        unsafe { libc::_exit(1) };
    }
    assert!(wait_until(Duration::from_secs(2), || {
        mapping
            .snapshot()
            .is_ok_and(|state| state.phase() == Phase::Initializing)
    }));
    // SAFETY: direct child is intentionally killed at the failure point.
    assert_eq!(unsafe { libc::kill(initializer, libc::SIGKILL) }, 0);
    let status = wait_for_pids(vec![initializer], Duration::from_secs(5))[0];
    assert!(libc::WIFSIGNALED(status));
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Initializing);
    mapping.poison().unwrap();
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Poisoned);

    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive preparation for the admission death case.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let owner = mapping.try_initialize(SharedState::new(0)).unwrap();
    // SAFETY: child uses a fresh counted attachment and is killed without
    // destructors to model process death.
    let admitted = unsafe { libc::fork() };
    assert!(admitted >= 0, "fork failed");
    if admitted == 0 {
        let attachment = mapping.attach::<SharedState>().unwrap();
        let _guard = attachment.try_enter().unwrap();
        loop {
            core::hint::spin_loop();
        }
    }
    assert!(wait_until(Duration::from_secs(2), || {
        mapping
            .snapshot()
            .is_ok_and(|state| state.attachments() == 2 && state.admissions() == 1)
    }));
    // SAFETY: direct child is intentionally killed while admitted.
    assert_eq!(unsafe { libc::kill(admitted, libc::SIGKILL) }, 0);
    let status = wait_for_pids(vec![admitted], Duration::from_secs(5))[0];
    assert!(libc::WIFSIGNALED(status));
    let draining = owner.begin_drain().unwrap();
    assert!(!draining.is_drained().unwrap());
    drop(draining);
    assert_eq!(mapping.snapshot().unwrap().phase(), Phase::Poisoned);
}

#[test]
fn corrupt_and_truncated_headers_are_rejected_before_typed_access() {
    let bytes = SharedBytes::anonymous();
    // SAFETY: exclusive initial preparation.
    let _mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    // SAFETY: no typed attachment exists, and this deliberately corrupts only
    // the untyped magic byte for a negative validation test.
    unsafe { *bytes.base ^= 0xff };
    // SAFETY: open_existing is being exercised on authenticated-but-corrupted
    // test bytes; it must reject before forming a payload reference.
    assert!(matches!(
        unsafe { bytes.raw().open_existing(BUILD, INSTANCE) },
        Err(MappingError::BadMagic { .. })
    ));

    let short_len = 64;
    // SAFETY: the address is live, but the supplied extent is intentionally too
    // short to contain the header.
    assert!(matches!(
        unsafe { RawMapping::from_raw_parts(bytes.base, short_len) },
        Err(MappingError::MappingTooSmall { .. })
    ));
}

#[cfg(target_pointer_width = "64")]
const WORKER_ADDRESSES: [usize; 4] = [
    0x2400_0000_0000,
    0x3400_0000_0000,
    0x4400_0000_0000,
    0x5400_0000_0000,
];

#[cfg(target_pointer_width = "32")]
const WORKER_ADDRESSES: [usize; 4] = [0x3000_0000, 0x5000_0000, 0x7000_0000, 0x9000_0000];

fn map_worker(descriptor: libc::c_int, parent_address: usize) -> *mut u8 {
    for address in WORKER_ADDRESSES {
        if address == parent_address {
            continue;
        }
        // SAFETY: candidates are page aligned; NOREPLACE cannot overwrite an
        // existing VMA.
        let mapping = unsafe {
            libc::mmap(
                address as *mut libc::c_void,
                MAPPING_LEN,
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
        // SAFETY: successful mmap returned this address and length.
        assert_eq!(unsafe { libc::munmap(mapping, MAPPING_LEN) }, 0);
    }
    panic!("could not map exec worker at a distinct address");
}

#[test]
fn exec_worker_attaches_typed_mapping() {
    let Ok(descriptor) = std::env::var(EXEC_FD) else {
        return;
    };
    let descriptor = descriptor.parse::<libc::c_int>().unwrap();
    let parent_address = std::env::var(EXEC_PARENT_ADDRESS)
        .unwrap()
        .parse::<usize>()
        .unwrap();
    let base = map_worker(descriptor, parent_address);
    // SAFETY: parent prepared and initialized the authenticated memfd before
    // spawning this worker; mapping remains live through the operation.
    let mapping = unsafe {
        RawMapping::from_raw_parts(base, MAPPING_LEN)
            .unwrap()
            .open_existing(BUILD, INSTANCE)
            .unwrap()
    };
    let attachment = mapping.attach::<SharedState>().unwrap();
    attachment
        .try_enter()
        .unwrap()
        .value
        .fetch_add(1, Ordering::Relaxed);
    drop(attachment);
    // SAFETY: all local handles and guards are gone.
    assert_eq!(unsafe { libc::munmap(base.cast(), MAPPING_LEN) }, 0);
}

#[test]
fn independent_exec_attaches_at_a_different_virtual_address() {
    if std::env::var_os(EXEC_FD).is_some() {
        return;
    }
    let bytes = SharedBytes::memfd();
    // SAFETY: exclusive initial preparation.
    let mapping = unsafe { bytes.raw() }.prepare(BUILD, INSTANCE);
    let owner = mapping.try_initialize(SharedState::new(41)).unwrap();
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "exec_worker_attaches_typed_mapping",
            "--nocapture",
        ])
        .env(EXEC_FD, bytes.descriptor.unwrap().to_string())
        .env(EXEC_PARENT_ADDRESS, (bytes.base as usize).to_string())
        .spawn()
        .expect("spawn mapping exec worker");
    let status = wait_for_child(&mut child, Duration::from_secs(5));
    assert!(status.success(), "exec worker failed: {status}");

    let guard = owner.try_enter().unwrap();
    assert_eq!(guard.value.load(Ordering::Relaxed), 42);
    drop(guard);
    let draining = owner.begin_drain().unwrap();
    assert!(draining.is_drained().unwrap());
    draining.try_close().unwrap();
}
