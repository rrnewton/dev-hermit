#![no_std]
#![cfg_attr(csnzi_freestanding, no_main)]
#![allow(unexpected_cfgs)]

//! Close and drain a scalable C-SNZI shared by forked worker processes.
//!
//! Every child creates and consumes only its own linear token. The parent closes
//! admission after all workers are present and reclaims the mapping only after
//! the children exit and the barrier reports stable terminal drain.

#[cfg(not(csnzi_freestanding))]
extern crate std;

#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
use core::mem::size_of;
#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
use core::ptr;
#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
use core::sync::atomic::{AtomicU32, Ordering};
#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::csnzi::{CloseOutcome, Csnzi, DepartOutcome};

#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
const WORKERS: usize = 8;

#[cfg(all(not(csnzi_freestanding), target_os = "linux", target_has_atomic = "64"))]
fn main() {
    struct Shared {
        ready: AtomicU32,
        barrier: Csnzi<84>,
    }

    // SAFETY: create one page-aligned shared mapping before any process uses it.
    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            size_of::<Shared>(),
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    let shared = mapping.cast::<Shared>();
    // SAFETY: the mapping is exclusive, writable, and correctly aligned.
    unsafe {
        shared.write(Shared {
            ready: AtomicU32::new(0),
            barrier: Csnzi::new(),
        })
    };

    let mut children = [0; WORKERS];
    for (worker, child_slot) in children.iter_mut().enumerate() {
        // SAFETY: each child uses only shared atomics, C-SNZI methods, and _exit.
        let child = unsafe { libc::fork() };
        assert!(child >= 0, "fork failed");
        if child == 0 {
            // SAFETY: the inherited mapping remains live until the parent reaps us.
            let state = unsafe { &*shared };
            let token = match state.barrier.try_enter(worker) {
                Ok(token) => token,
                Err(_) => unsafe { libc::_exit(10) },
            };
            state.ready.fetch_add(1, Ordering::AcqRel);
            while !state.barrier.is_closed() {
                core::hint::spin_loop();
            }
            let status = match token.depart() {
                Ok(DepartOutcome::Active | DepartOutcome::Drained) => 0,
                Err(_) => 11,
            };
            unsafe { libc::_exit(status) };
        }
        *child_slot = child;
    }

    // SAFETY: initialized shared mapping remains live through every child exit.
    let state = unsafe { &*shared };
    while state.ready.load(Ordering::Acquire) != WORKERS as u32 {
        core::hint::spin_loop();
    }
    assert_eq!(state.barrier.debug_snapshot().root_count, 1);
    assert_eq!(state.barrier.close().unwrap(), CloseOutcome::Pending);

    for child in children {
        let mut status = 0;
        // SAFETY: wait for and reap one direct child.
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }
    assert!(state.barrier.is_drained());
    assert!(state.barrier.debug_snapshot().appears_drained());

    // SAFETY: all processes and tokens are gone. Terminal drain permits payload
    // reclamation, and reaping every child establishes the barrier-page lifetime.
    assert_eq!(unsafe { libc::munmap(mapping, size_of::<Shared>()) }, 0);
    std::println!("PASS csnzi workers={WORKERS} root_activations=1 drained=true");
}

#[cfg(all(
    not(csnzi_freestanding),
    not(all(target_os = "linux", target_has_atomic = "64"))
))]
fn main() {
    std::eprintln!("csnzi requires Linux and 64-bit atomics");
}

#[cfg(csnzi_freestanding)]
type FreestandingCsnzi = shmem_pod::csnzi::Csnzi<20>;

#[cfg(csnzi_freestanding)]
#[inline]
unsafe fn freestanding_object<'a>(state: *mut u8) -> Option<&'a FreestandingCsnzi> {
    if state.is_null() || (state as usize) & (core::mem::align_of::<FreestandingCsnzi>() - 1) != 0 {
        return None;
    }
    // SAFETY: The executable-pod ABI requires a live initialized state region.
    Some(unsafe { &*state.cast::<FreestandingCsnzi>() })
}

#[cfg(csnzi_freestanding)]
fn output_overlaps_state(state: *mut u8, output: *mut u64) -> bool {
    let state_start = state as usize;
    let output_start = output as usize;
    let Some(state_end) = state_start.checked_add(core::mem::size_of::<FreestandingCsnzi>()) else {
        return true;
    };
    let Some(output_end) = output_start.checked_add(core::mem::size_of::<u64>()) else {
        return true;
    };
    state_start < output_end && output_start < state_end
}

/// Freestanding ABI: returns the required state size.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub extern "C" fn shmem_pod_layout_size() -> u64 {
    core::mem::size_of::<FreestandingCsnzi>() as u64
}

/// Freestanding ABI: returns the required state alignment.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub extern "C" fn shmem_pod_layout_align() -> u64 {
    core::mem::align_of::<FreestandingCsnzi>() as u64
}

/// Freestanding ABI: initializes a C-SNZI directly in final storage.
///
/// # Safety
///
/// `state` must name an exclusively writable region of at least `region_len`
/// bytes which remains live for the initialized object's lifetime.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_init(state: *mut u8, region_len: u64) -> i32 {
    if state.is_null()
        || region_len < core::mem::size_of::<FreestandingCsnzi>() as u64
        || (state as usize) & (core::mem::align_of::<FreestandingCsnzi>() - 1) != 0
    {
        return -1;
    }
    // SAFETY: The ABI check above establishes size/alignment; the loader grants
    // exclusive uninitialized storage for this initialization call.
    unsafe { FreestandingCsnzi::initialize_at(state.cast()) };
    0
}

/// Freestanding ABI: admits one participant and writes its raw token.
///
/// # Safety
///
/// `state` must name a live initialized `FreestandingCsnzi`. `output` must be
/// writable for eight bytes and must not overlap that state object.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_csnzi_enter(state: *mut u8, leaf: u64, output: *mut u64) -> i32 {
    if output.is_null() || leaf > usize::MAX as u64 || output_overlaps_state(state, output) {
        return -1;
    }
    // SAFETY: The ABI requires state to remain live for the complete call.
    let Some(object) = (unsafe { freestanding_object(state) }) else {
        return -1;
    };
    match object.try_enter(leaf as usize) {
        Ok(token) => {
            // SAFETY: The ABI requires eight writable, non-overlapping bytes;
            // write_unaligned accepts every non-null byte alignment.
            unsafe { output.write_unaligned(token.into_raw()) };
            0
        }
        Err(shmem_pod::csnzi::CsnziError::Closed) => -2,
        Err(shmem_pod::csnzi::CsnziError::DepartureTailBusy) => -3,
        Err(_) => -4,
    }
}

/// Freestanding ABI: consumes one raw token.
///
/// # Safety
///
/// `state` must name the live initialized object which issued `token`, and the
/// caller must own the sole unconsumed copy of that token.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_csnzi_depart(state: *mut u8, token: u64) -> i32 {
    // SAFETY: The ABI requires state to remain live for the complete call.
    let Some(object) = (unsafe { freestanding_object(state) }) else {
        return -1;
    };
    // SAFETY: The caller owns the sole token for this exact object generation.
    match unsafe { object.depart_raw(token) } {
        Ok(shmem_pod::csnzi::DepartOutcome::Active) => 0,
        Ok(shmem_pod::csnzi::DepartOutcome::Drained) => 1,
        Err(_) => -4,
    }
}

/// Freestanding ABI: permanently closes admission.
///
/// # Safety
///
/// `state` must name a live initialized `FreestandingCsnzi` for the whole call.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_csnzi_close(state: *mut u8) -> i32 {
    // SAFETY: The ABI requires state to remain live for the complete call.
    let Some(object) = (unsafe { freestanding_object(state) }) else {
        return -1;
    };
    match object.close() {
        Ok(shmem_pod::csnzi::CloseOutcome::Pending) => 0,
        Ok(shmem_pod::csnzi::CloseOutcome::Drained) => 1,
        Ok(shmem_pod::csnzi::CloseOutcome::AlreadyClosed) => 2,
        Err(_) => -4,
    }
}

/// Freestanding ABI: reports possible represented presence.
///
/// # Safety
///
/// `state` must name a live initialized `FreestandingCsnzi` for the whole call.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_csnzi_query(state: *mut u8) -> u64 {
    // SAFETY: The ABI requires state to remain live for the complete call.
    unsafe { freestanding_object(state) }
        .is_some_and(FreestandingCsnzi::query)
        .into()
}

/// Freestanding ABI: reports stable terminal drain.
///
/// # Safety
///
/// `state` must name a live initialized `FreestandingCsnzi` for the whole call.
#[cfg(csnzi_freestanding)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_csnzi_drained(state: *mut u8) -> u64 {
    // SAFETY: The ABI requires state to remain live for the complete call.
    unsafe { freestanding_object(state) }
        .is_some_and(FreestandingCsnzi::is_drained)
        .into()
}
