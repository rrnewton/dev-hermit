//! Publish and attach typed shared state through an untyped bootstrap header.
//!
//! The ready word is initialized before `fork`. The child waits without forming
//! a payload reference. The parent writes the descriptor and constructs the
//! payload, then publishes readiness with release ordering. After an acquire
//! load, the child validates the descriptor, extent, and alignment before
//! creating `&SharedState`.
//!
//! A production loader must additionally authenticate the code/build identity
//! bound to the descriptor. A layout fingerprint is not a signature.

#[cfg(target_os = "linux")]
use core::mem::{align_of, size_of};
#[cfg(target_os = "linux")]
use core::ptr;
#[cfg(target_os = "linux")]
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};
#[cfg(target_os = "linux")]
use shmem_pod::layout::LayoutDescriptor;
#[cfg(target_os = "linux")]
use shmem_pod::sync::ProcessSpinMutex;

#[cfg(target_os = "linux")]
const READY_OFFSET: usize = LayoutDescriptor::ENCODED_LEN;
#[cfg(target_os = "linux")]
const HEADER_LEN: usize = 64;
#[cfg(target_os = "linux")]
const READY: u32 = 1;

#[cfg(target_os = "linux")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct SharedState {
    calls: AtomicU64,
    total: ProcessSpinMutex<u64>,
}

#[cfg(target_os = "linux")]
impl SharedState {
    const fn new() -> Self {
        Self {
            calls: AtomicU64::new(0),
            total: ProcessSpinMutex::new(0),
        }
    }
}

#[cfg(target_os = "linux")]
fn payload_offset() -> usize {
    let alignment = align_of::<SharedState>();
    HEADER_LEN
        .checked_add(alignment - 1)
        .expect("payload offset overflow")
        & !(alignment - 1)
}

#[cfg(target_os = "linux")]
fn attach(base: *mut u8, mapping_len: usize) -> bool {
    let ready = unsafe { &*base.add(READY_OFFSET).cast::<AtomicU32>() };
    while ready.load(Ordering::Acquire) != READY {
        core::hint::spin_loop();
    }

    // Everything except the already initialized ready word remains untyped
    // until these checks succeed.
    let encoded = unsafe { core::slice::from_raw_parts(base, LayoutDescriptor::ENCODED_LEN) };
    let Ok(received) = LayoutDescriptor::decode(encoded) else {
        return false;
    };
    if received.validate::<SharedState>().is_err() {
        return false;
    }

    let offset = payload_offset();
    let Some(end) = offset.checked_add(received.size() as usize) else {
        return false;
    };
    if end > mapping_len {
        return false;
    }
    let payload = unsafe { base.add(offset) };
    if payload as usize % received.alignment() as usize != 0 {
        return false;
    }

    // The parent constructed SharedState before the release store observed
    // above, and the descriptor and geometry now match the local type.
    let state = unsafe { &*payload.cast::<SharedState>() };
    state.calls.fetch_add(1, Ordering::Relaxed);
    *state.total.lock() += 7;
    true
}

#[cfg(target_os = "linux")]
fn main() {
    let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    assert!(page_size > 0, "sysconf(_SC_PAGESIZE) failed");
    let mapping_len = usize::try_from(page_size).expect("page size does not fit usize");
    assert!(
        payload_offset() + size_of::<SharedState>() <= mapping_len,
        "state does not fit in one page"
    );

    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            mapping_len,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    let base = mapping.cast::<u8>();

    // Only the waitable bootstrap word must exist before the child starts.
    unsafe {
        base.add(READY_OFFSET)
            .cast::<AtomicU32>()
            .write(AtomicU32::new(0));
    }

    let child = unsafe { libc::fork() };
    assert!(child >= 0, "fork failed");
    if child == 0 {
        let ok = attach(base, mapping_len);
        unsafe { libc::_exit(if ok { 0 } else { 1 }) };
    }

    let descriptor = LayoutDescriptor::of::<SharedState>().encode();
    unsafe {
        ptr::copy_nonoverlapping(descriptor.as_ptr(), base, descriptor.len());
        base.add(payload_offset())
            .cast::<SharedState>()
            .write(SharedState::new());
    }
    let ready = unsafe { &*base.add(READY_OFFSET).cast::<AtomicU32>() };
    ready.store(READY, Ordering::Release);

    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    assert!(libc::WIFEXITED(status));
    assert_eq!(libc::WEXITSTATUS(status), 0);

    let state = unsafe { &*base.add(payload_offset()).cast::<SharedState>() };
    assert_eq!(state.calls.load(Ordering::Relaxed), 1);
    assert_eq!(*state.total.lock(), 7);

    unsafe { ptr::drop_in_place(base.add(payload_offset()).cast::<SharedState>()) };
    assert_eq!(unsafe { libc::munmap(mapping, mapping_len) }, 0);
    println!(
        "PASS layout_handshake descriptor_bytes={}",
        descriptor.len()
    );
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("layout_handshake requires Linux");
}
