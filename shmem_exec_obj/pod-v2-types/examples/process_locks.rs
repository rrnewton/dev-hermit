//! Self-checking coarse- and fine-lock example using shared pages across `fork`.

#[cfg(target_os = "linux")]
use core::mem::size_of;
#[cfg(target_os = "linux")]
use core::ptr;
#[cfg(target_os = "linux")]
use shmem_pod::sync::ProcessSpinMutex;

#[cfg(target_os = "linux")]
const WORKERS: usize = 8;
#[cfg(target_os = "linux")]
const ITERATIONS: usize = 25_000;

#[cfg(target_os = "linux")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct SharedState {
    coarse: ProcessSpinMutex<[u64; 4]>,
    fine: [ProcessSpinMutex<u64>; 4],
}

#[cfg(target_os = "linux")]
impl SharedState {
    fn new() -> Self {
        Self {
            coarse: ProcessSpinMutex::new([0; 4]),
            fine: [
                ProcessSpinMutex::new(0),
                ProcessSpinMutex::new(0),
                ProcessSpinMutex::new(0),
                ProcessSpinMutex::new(0),
            ],
        }
    }
}

#[cfg(target_os = "linux")]
fn main() {
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
    let state = mapping.cast::<SharedState>();
    unsafe { state.write(SharedState::new()) };

    let mut children = [0; WORKERS];
    for (worker, child) in children.iter_mut().enumerate() {
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork failed");
        if pid == 0 {
            let state = unsafe { &*state };
            let shard = worker % 4;
            for _ in 0..ITERATIONS {
                state.coarse.lock()[shard] += 1;
                *state.fine[shard].lock() += 1;
            }
            unsafe { libc::_exit(0) };
        }
        *child = pid;
    }

    for child in children {
        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }

    let state_ref = unsafe { &*state };
    let expected = [2 * ITERATIONS as u64; 4];
    assert_eq!(*state_ref.coarse.lock(), expected);
    for (lock, expected) in state_ref.fine.iter().zip(expected) {
        assert_eq!(*lock.lock(), expected);
    }

    unsafe { ptr::drop_in_place(state) };
    assert_eq!(
        unsafe { libc::munmap(mapping, size_of::<SharedState>()) },
        0
    );
    println!(
        "PASS process_locks workers={WORKERS} iterations={ITERATIONS} updates={}",
        WORKERS * ITERATIONS * 2
    );
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("process_locks requires Linux");
}
