//! `LD_PRELOAD`, ptrace, and trampoline bootstrap adapter for the demo pod.
//!
//! The unaware guest sees only the interposed libc ABI. Injectors can instead
//! call [`shmem_pod_bootstrap_v1`] with the same versioned context.

use sha2::{Digest, Sha256};
use shmem_pod::injection::{
    AdapterCallGate, BOOTSTRAP_FD_ENV, BootstrapContext, BootstrapFlags, BootstrapStatus,
    ConnectorKind, parse_bootstrap_fd,
};
use shmem_pod_image_api::{
    ENVELOPE_API_FINGERPRINT_OFFSET, ENVELOPE_ARTIFACT_HASH_OFFSET, ENVELOPE_CODE_HASH_OFFSET,
    ENVELOPE_FAILURE_OFFSET, ENVELOPE_FLAGS_OFFSET, ENVELOPE_GENERATION_OFFSET,
    ENVELOPE_LAYOUT_ALIGN_OFFSET, ENVELOPE_LAYOUT_HASH_OFFSET, ENVELOPE_LAYOUT_SIZE_OFFSET,
    ENVELOPE_MAGIC_OFFSET, ENVELOPE_OWNER_PID_OFFSET, ENVELOPE_PAYLOAD_LEN_OFFSET,
    ENVELOPE_REQUIRED_ADDRESS_OFFSET, ENVELOPE_STATE_FINGERPRINT_OFFSET, ENVELOPE_STATUS_OFFSET,
    ENVELOPE_VERSION_OFFSET, PAGE_SIZE, STATE_MAGIC, STATE_STATUS_READY, STATE_VERSION,
};
use shmem_pod_runtime::{PodArtifact, PodImage, PodState};
use std::borrow::Cow;
use std::cell::{Cell, UnsafeCell};
use std::ffi::c_char;
use std::fmt;
use std::mem::MaybeUninit;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, AtomicU64, Ordering};

const CALL_KEY: u64 = 0x7072_656c_6f61_6401;
const ATTACH_KEY: u64 = 0x7072_656c_6f61_6402;
const FAILURE_EXIT_CODE: libc::c_int = 125;
const INIT_EMPTY: u32 = 0;
const INIT_BUSY: u32 = 1;
const INIT_READY: u32 = 2;
const INIT_FAILED: u32 = 3;
const ATFORK_STATE_MASK: u32 = 0b11;
const ATFORK_MAX_OWNER_PID: u32 = u32::MAX >> 2;
const PROCESS_EPOCH_BUSY: i32 = -1;
const PROCESS_EPOCH_FAILED: i32 = -2;
const ATTACH_BUSY: i32 = -1;
const MAX_ENV_FD_BYTES: usize = 10;
const F_SEAL_FUTURE_WRITE: libc::c_int = 0x0010;
const F_SEAL_EXEC: libc::c_int = 0x0020;

static INIT_STATE: AtomicU32 = AtomicU32::new(INIT_EMPTY);
static ATFORK_CLAIM: AtomicU32 = AtomicU32::new(INIT_EMPTY);
static PROCESS_EPOCH: AtomicI32 = AtomicI32::new(0);
static ATTACHED_PID: AtomicI32 = AtomicI32::new(0);
static FAILURE_REPORTED: AtomicBool = AtomicBool::new(false);
static FAIL_CLOSED: AtomicBool = AtomicBool::new(false);
static CALL_GATE: AdapterCallGate = AdapterCallGate::new();
static FORK_BARRIER: ForkBarrier = ForkBarrier::new();
static CONTEXT: ContextCell = ContextCell(UnsafeCell::new(MaybeUninit::uninit()));

thread_local! {
    // High bit means this thread is in the hook; low bits cache its PID. PID
    // mismatch clears inherited TLS automatically after fork.
    static HOOK_STATE: Cell<u64> = const { Cell::new(0) };
}

struct ContextCell(UnsafeCell<MaybeUninit<Context>>);

// SAFETY: INIT_STATE publishes the one write with release/acquire ordering;
// after publication the Context is immutable and its runtime types are Sync.
unsafe impl Sync for ContextCell {}

struct Context {
    bootstrap: BootstrapContext,
    image: PodImage,
    state: PodState,
}

struct HookGuard;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FailureKind {
    InvalidContext,
    InvalidTransport,
    IncompatibleImage,
    Initialization,
}

struct AdapterError {
    kind: FailureKind,
    message: Cow<'static, str>,
}

impl AdapterError {
    fn new(kind: FailureKind, message: impl Into<Cow<'static, str>>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    fn status(&self) -> BootstrapStatus {
        match self.kind {
            FailureKind::InvalidContext => BootstrapStatus::InvalidContext,
            FailureKind::InvalidTransport => BootstrapStatus::InvalidTransport,
            FailureKind::IncompatibleImage => BootstrapStatus::IncompatibleImage,
            FailureKind::Initialization => BootstrapStatus::InitializationFailed,
        }
    }
}

impl fmt::Display for AdapterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

struct InitClaim<'a> {
    state: &'a AtomicU32,
    published: bool,
}

struct AtforkRegistrationClaim<'a> {
    state: &'a AtomicU32,
    busy_word: u32,
    published: bool,
}

struct ProcessEpochClaim<'a> {
    epoch: &'a AtomicI32,
    published: bool,
}

struct AttachmentClaim<'a> {
    state: &'a AtomicI32,
    published: bool,
}

struct ForkBarrier {
    owner: AtomicU64,
    reenable: AtomicBool,
}

impl ForkBarrier {
    const fn new() -> Self {
        Self {
            owner: AtomicU64::new(0),
            reenable: AtomicBool::new(false),
        }
    }

    fn prepare(&self, gate: &AdapterCallGate) {
        let identity = match fork_barrier_identity() {
            Some(identity) => identity,
            None => {
                fork_barrier_fail_stop(
                    b"shmem-pod injected adapter: invalid process/thread identity in at-fork prepare\n",
                );
            }
        };
        let pid = fork_barrier_pid(identity);

        let mut observed = self.owner.load(Ordering::Acquire);
        loop {
            if observed == identity {
                fork_barrier_fail_stop(
                    b"shmem-pod injected adapter: nested libc fork on at-fork owner thread\n",
                );
            }
            if observed != 0 && (fork_barrier_pid(observed) == 0 || fork_barrier_tid(observed) == 0)
            {
                fork_barrier_fail_stop(
                    b"shmem-pod injected adapter: corrupt at-fork barrier owner\n",
                );
            }
            if observed != 0 && fork_barrier_pid(observed) != pid {
                fork_barrier_fail_stop(
                    b"shmem-pod injected adapter: inherited at-fork barrier owner\n",
                );
            }
            if observed == 0 {
                match self.owner.compare_exchange_weak(
                    0,
                    identity,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => break,
                    Err(current) => observed = current,
                }
                continue;
            }
            std::hint::spin_loop();
            observed = self.owner.load(Ordering::Acquire);
        }

        // The atomic PID/TID claim is published before any gate state changes.
        // A signal which recursively calls libc fork can therefore identify
        // this same thread and fail stop instead of waiting on itself.
        self.reenable.store(!gate.is_disabled(), Ordering::Relaxed);
        let _ = gate.disable();
        while gate.active_calls() != 0 {
            std::hint::spin_loop();
        }
    }

    fn finish_parent(&self, gate: &AdapterCallGate) -> Result<(), ()> {
        let identity = fork_barrier_identity().ok_or(())?;
        if self.owner.load(Ordering::Acquire) != identity {
            return Err(());
        }
        self.reset_gate(gate)?;
        self.owner
            .compare_exchange(identity, 0, Ordering::Release, Ordering::Relaxed)
            .map(|_| ())
            .map_err(|_| ())
    }

    fn finish_child(&self, gate: &AdapterCallGate) -> Result<(), ()> {
        // The child is a private address-space copy with a new Linux PID/TID.
        // Rebind the complete identity before touching the gate. A signal at
        // any earlier point sees a foreign PID and fails stop rather than
        // spinning on the one surviving thread.
        let child_identity = self.rebind_child_owner()?;
        self.reset_gate(gate)?;
        self.owner
            .compare_exchange(child_identity, 0, Ordering::Release, Ordering::Relaxed)
            .map(|_| ())
            .map_err(|_| ())
    }

    fn rebind_child_owner(&self) -> Result<u64, ()> {
        let child_identity = fork_barrier_identity().ok_or(())?;
        let inherited_owner = self.owner.load(Ordering::Acquire);
        if inherited_owner == 0
            || fork_barrier_pid(inherited_owner) == 0
            || fork_barrier_tid(inherited_owner) == 0
            || fork_barrier_pid(inherited_owner) == fork_barrier_pid(child_identity)
        {
            return Err(());
        }
        self.owner
            .compare_exchange(
                inherited_owner,
                child_identity,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .map(|_| child_identity)
            .map_err(|_| ())
    }

    fn reset_gate(&self, gate: &AdapterCallGate) -> Result<(), ()> {
        let reset = if self.reenable.load(Ordering::Relaxed) {
            // SAFETY: prepare disabled this same gate and waited for the last
            // token before the fork copied the barrier and gate state.
            unsafe { gate.reset_after_fork() }.map_err(|_| ())
        } else {
            Ok(())
        };
        self.reenable.store(false, Ordering::Relaxed);
        reset
    }

    /// Discards a barrier copied from parent threads after a skipped callback.
    ///
    /// # Safety
    ///
    /// The caller must be the one serialized PID-epoch recovery in a post-fork
    /// child, before any child hook admission. The surviving thread must not
    /// own this barrier. Parent owners cannot observe these private stores.
    unsafe fn force_reset_in_fork_child(&self) {
        self.reenable.store(false, Ordering::Relaxed);
        self.owner.store(0, Ordering::Release);
    }
}

fn fork_barrier_identity() -> Option<u64> {
    let pid = u32::try_from(raw_getpid()).ok().filter(|pid| *pid != 0)?;
    let tid = u32::try_from(raw_gettid()).ok().filter(|tid| *tid != 0)?;
    Some((u64::from(pid) << 32) | u64::from(tid))
}

fn fork_barrier_pid(identity: u64) -> u32 {
    (identity >> 32) as u32
}

fn fork_barrier_tid(identity: u64) -> u32 {
    identity as u32
}

fn fork_barrier_fail_stop(message: &'static [u8]) -> ! {
    raw_write(message);
    unsafe { libc::_exit(FAILURE_EXIT_CODE) }
}

impl<'a> InitClaim<'a> {
    fn new(state: &'a AtomicU32) -> Self {
        Self {
            state,
            published: false,
        }
    }

    fn publish_ready(mut self) {
        self.state.store(INIT_READY, Ordering::Release);
        self.published = true;
        futex_wake_u32(self.state, i32::MAX);
    }
}

impl Drop for InitClaim<'_> {
    fn drop(&mut self) {
        if !self.published {
            self.state.store(INIT_FAILED, Ordering::Release);
            futex_wake_u32(self.state, i32::MAX);
        }
    }
}

impl<'a> AtforkRegistrationClaim<'a> {
    fn new(state: &'a AtomicU32, busy_word: u32) -> Self {
        Self {
            state,
            busy_word,
            published: false,
        }
    }

    fn publish_ready(mut self) -> Result<(), AdapterError> {
        let ready_word = (self.busy_word & !ATFORK_STATE_MASK) | INIT_READY;
        self.state
            .compare_exchange(
                self.busy_word,
                ready_word,
                Ordering::Release,
                Ordering::Acquire,
            )
            .map_err(|_| {
                AdapterError::new(
                    FailureKind::Initialization,
                    "pthread_atfork registration claim changed before publication",
                )
            })?;
        self.published = true;
        futex_wake_u32(self.state, i32::MAX);
        Ok(())
    }
}

impl<'a> ProcessEpochClaim<'a> {
    fn new(epoch: &'a AtomicI32) -> Self {
        Self {
            epoch,
            published: false,
        }
    }

    fn publish(mut self, pid: i32) -> Result<(), AdapterError> {
        self.epoch
            .compare_exchange(
                PROCESS_EPOCH_BUSY,
                pid,
                Ordering::Release,
                Ordering::Acquire,
            )
            .map_err(|_| {
                AdapterError::new(
                    FailureKind::Initialization,
                    "process PID epoch changed during child recovery",
                )
            })?;
        self.published = true;
        futex_wake_i32(self.epoch, i32::MAX);
        Ok(())
    }
}

impl Drop for ProcessEpochClaim<'_> {
    fn drop(&mut self) {
        if !self.published
            && self
                .epoch
                .compare_exchange(
                    PROCESS_EPOCH_BUSY,
                    PROCESS_EPOCH_FAILED,
                    Ordering::Release,
                    Ordering::Acquire,
                )
                .is_ok()
        {
            futex_wake_i32(self.epoch, i32::MAX);
        }
    }
}

impl Drop for AtforkRegistrationClaim<'_> {
    fn drop(&mut self) {
        if !self.published
            && self
                .state
                .compare_exchange(
                    self.busy_word,
                    INIT_FAILED,
                    Ordering::Release,
                    Ordering::Acquire,
                )
                .is_ok()
        {
            futex_wake_u32(self.state, i32::MAX);
        }
    }
}

impl<'a> AttachmentClaim<'a> {
    fn new(state: &'a AtomicI32) -> Self {
        Self {
            state,
            published: false,
        }
    }

    fn publish(mut self, pid: i32) {
        self.state.store(pid, Ordering::Release);
        self.published = true;
        futex_wake_i32(self.state, i32::MAX);
    }
}

impl Drop for AttachmentClaim<'_> {
    fn drop(&mut self) {
        if !self.published {
            self.state.store(0, Ordering::Release);
            futex_wake_i32(self.state, i32::MAX);
        }
    }
}

impl HookGuard {
    fn enter() -> Option<Self> {
        let pid = raw_getpid() as u32 as u64;
        HOOK_STATE
            .try_with(|state| {
                let mut observed = state.get();
                if observed & 0x7fff_ffff != pid {
                    observed = pid;
                }
                if observed >> 63 != 0 {
                    None
                } else {
                    state.set(observed | (1 << 63));
                    Some(Self)
                }
            })
            .ok()
            .flatten()
    }
}

impl Drop for HookGuard {
    fn drop(&mut self) {
        let _ = HOOK_STATE.try_with(|state| state.set(state.get() & !(1 << 63)));
    }
}

fn ensure_process_epoch() -> Result<(), AdapterError> {
    let pid = raw_getpid();
    loop {
        let observed = PROCESS_EPOCH.load(Ordering::Acquire);
        if observed == pid {
            return Ok(());
        }
        match observed {
            0 => {
                if PROCESS_EPOCH
                    .compare_exchange(0, pid, Ordering::AcqRel, Ordering::Acquire)
                    .is_ok()
                {
                    return Ok(());
                }
            }
            PROCESS_EPOCH_BUSY => wait_while_i32(&PROCESS_EPOCH, PROCESS_EPOCH_BUSY),
            PROCESS_EPOCH_FAILED => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "an earlier fork-child PID epoch recovery failed",
                ));
            }
            parent_pid if parent_pid > 0 => {
                if PROCESS_EPOCH
                    .compare_exchange(
                        parent_pid,
                        PROCESS_EPOCH_BUSY,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    )
                    .is_err()
                {
                    continue;
                }
                let claim = ProcessEpochClaim::new(&PROCESS_EPOCH);
                if let Err(error) = recover_skipped_atfork_child(parent_pid, pid) {
                    FAIL_CLOSED.store(true, Ordering::Release);
                    return Err(error);
                }
                claim.publish(pid)?;
                return Ok(());
            }
            _ => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "process PID epoch is corrupt",
                ));
            }
        }
    }
}

fn recover_skipped_atfork_child(parent_pid: i32, child_pid: i32) -> Result<(), AdapterError> {
    let child_owner = atfork_pid(child_pid)?;
    let registration = ATFORK_CLAIM.load(Ordering::Acquire);
    match atfork_state(registration) {
        INIT_EMPTY if registration == INIT_EMPTY => {}
        INIT_READY if atfork_owner_pid(registration) == parent_pid as u32 => {}
        INIT_BUSY => {
            return Err(AdapterError::new(
                FailureKind::Initialization,
                "fork child inherited an ambiguous pthread_atfork registration",
            ));
        }
        _ => {
            return Err(AdapterError::new(
                FailureKind::Initialization,
                "fork child inherited a corrupt pthread_atfork registration",
            ));
        }
    }

    // SAFETY: PROCESS_EPOCH_BUSY grants this thread exclusive child recovery
    // before CALL_GATE admission. The address space is a private fork copy and
    // a supported fork cannot originate while the surviving thread holds a
    // hook token or the shim fork barrier.
    unsafe {
        CALL_GATE.force_reset_in_fork_child();
        FORK_BARRIER.force_reset_in_fork_child();
    }
    ATTACHED_PID.store(0, Ordering::Release);
    FAILURE_REPORTED.store(false, Ordering::Release);

    match INIT_STATE.load(Ordering::Acquire) {
        INIT_EMPTY => FAIL_CLOSED.store(false, Ordering::Release),
        INIT_READY => {
            let context = unsafe { (&*CONTEXT.0.get()).assume_init_ref() };
            FAIL_CLOSED.store(
                context
                    .bootstrap
                    .bootstrap_flags()
                    .contains(BootstrapFlags::REQUIRED),
                Ordering::Release,
            );
        }
        INIT_BUSY => {
            INIT_STATE.store(INIT_FAILED, Ordering::Release);
            futex_wake_u32(&INIT_STATE, i32::MAX);
            return Err(AdapterError::new(
                FailureKind::Initialization,
                "fork child copied an incomplete adapter initialization",
            ));
        }
        INIT_FAILED => {
            return Err(AdapterError::new(
                FailureKind::Initialization,
                "fork child copied a failed adapter initialization",
            ));
        }
        _ => {
            return Err(AdapterError::new(
                FailureKind::Initialization,
                "fork child copied a corrupt adapter initialization state",
            ));
        }
    }

    if atfork_state(registration) == INIT_READY {
        let child_ready = (child_owner << 2) | INIT_READY;
        ATFORK_CLAIM
            .compare_exchange(
                registration,
                child_ready,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .map_err(|_| {
                AdapterError::new(
                    FailureKind::Initialization,
                    "pthread_atfork registration changed during child recovery",
                )
            })?;
        futex_wake_u32(&ATFORK_CLAIM, i32::MAX);
    }
    Ok(())
}

/// Interposes libc's `getuid` while preserving its return value and `errno`.
///
/// # Safety
///
/// The caller must use libc's `getuid` C ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn getuid() -> libc::uid_t {
    let Some(_hook) = HookGuard::enter() else {
        return raw_getuid();
    };
    let result = raw_getuid();
    let errno = unsafe { libc::__errno_location() };
    let saved_errno = unsafe { *errno };
    if let Err(error) = ensure_process_epoch() {
        report_failure(&error.message);
        unsafe { *errno = saved_errno };
        return result;
    }
    let Some(_call) = CALL_GATE.try_enter() else {
        unsafe { *errno = saved_errno };
        return result;
    };

    let update = panic::catch_unwind(AssertUnwindSafe(|| record_call(None)));
    match update {
        Ok(Ok(())) => {}
        Ok(Err(error)) => report_failure(&error.message),
        Err(_) => report_failure("panic while initializing or calling the pod"),
    }
    unsafe { *errno = saved_errno };
    result
}

/// Initializes an injected adapter from the common C-compatible context and
/// proves dispatch by recording one pod call.
///
/// This is the stable entry used by the ptrace demonstration. A binary-patch
/// trampoline can call the same symbol once it has preserved the platform ABI.
///
/// # Safety
///
/// `context` must be aligned, readable, and live for this call. Referenced file
/// descriptors must belong to this process. The function copies the context
/// and duplicates all descriptors before returning.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_bootstrap_v1(context: *const BootstrapContext) -> i32 {
    let Some(_hook) = HookGuard::enter() else {
        return BootstrapStatus::Reentrant as i32;
    };
    if let Err(error) = ensure_process_epoch() {
        return error.status() as i32;
    }
    let Some(_call) = CALL_GATE.try_enter() else {
        return BootstrapStatus::Disabled as i32;
    };
    if context.is_null() || (context as usize) % align_of::<BootstrapContext>() != 0 {
        return BootstrapStatus::InvalidContext as i32;
    }
    let context = unsafe { context.read() };
    if context.validate().is_err()
        || !matches!(
            context.connector_kind(),
            Some(ConnectorKind::Ptrace | ConnectorKind::Trampoline | ConnectorKind::Cooperative)
        )
    {
        return BootstrapStatus::InvalidContext as i32;
    }
    match panic::catch_unwind(AssertUnwindSafe(|| record_call(Some(context)))) {
        Ok(Ok(())) => BootstrapStatus::Ok as i32,
        Ok(Err(error)) => error.status() as i32,
        Err(_) => BootstrapStatus::InitializationFailed as i32,
    }
}

fn record_call(provided: Option<BootstrapContext>) -> Result<(), AdapterError> {
    let context = get_or_initialize(provided)?;
    record_attachment(context)?;
    context
        .image
        .upsert(&context.state, CALL_KEY, 1)
        .map_err(|error| {
            AdapterError::new(
                FailureKind::Initialization,
                format!("pod call failed: {error}"),
            )
        })
}

fn get_or_initialize(provided: Option<BootstrapContext>) -> Result<&'static Context, AdapterError> {
    ensure_atfork_registered()?;
    loop {
        match INIT_STATE.load(Ordering::Acquire) {
            INIT_READY => {
                let context = unsafe { (&*CONTEXT.0.get()).assume_init_ref() };
                if let Some(provided) = provided {
                    if context.bootstrap != provided {
                        return Err(AdapterError::new(
                            FailureKind::InvalidContext,
                            "adapter is already bound to a different bootstrap context",
                        ));
                    }
                }
                return Ok(context);
            }
            INIT_FAILED => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "an earlier adapter initialization failed",
                ));
            }
            INIT_EMPTY => {
                if INIT_STATE
                    .compare_exchange(INIT_EMPTY, INIT_BUSY, Ordering::Acquire, Ordering::Relaxed)
                    .is_err()
                {
                    continue;
                }
                // This guard is intentionally outside catch_unwind. Any future
                // panic between acquisition and publication leaves FAILED,
                // never an INIT_BUSY value which would spin every later hook.
                let claim = InitClaim::new(&INIT_STATE);
                let initialized = panic::catch_unwind(AssertUnwindSafe(|| match provided {
                    Some(context) => initialize_context(context),
                    None => context_from_environment().and_then(initialize_context),
                }));
                match initialized {
                    Ok(Ok(context)) => {
                        unsafe { (&mut *CONTEXT.0.get()).write(context) };
                        claim.publish_ready();
                        return Ok(unsafe { (&*CONTEXT.0.get()).assume_init_ref() });
                    }
                    Ok(Err(error)) => return Err(error),
                    Err(_) => {
                        return Err(AdapterError::new(
                            FailureKind::Initialization,
                            "panic during adapter initialization",
                        ));
                    }
                }
            }
            INIT_BUSY => {
                wait_while_u32(&INIT_STATE, INIT_BUSY);
            }
            _ => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "adapter initialization state is corrupt",
                ));
            }
        }
    }
}

fn context_from_environment() -> Result<BootstrapContext, AdapterError> {
    let name = b"SHMEM_POD_BOOTSTRAP_FD\0";
    let pointer = unsafe { libc::getenv(name.as_ptr().cast::<c_char>()) };
    if pointer.is_null() {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{BOOTSTRAP_FD_ENV} is not set"),
        ));
    }
    // Presence means this adapter was deliberately configured. A malformed
    // locator is therefore fail-closed even before its context can be read.
    FAIL_CLOSED.store(true, Ordering::Release);
    let mut length = 0;
    while length <= MAX_ENV_FD_BYTES && unsafe { *pointer.add(length) } != 0 {
        length += 1;
    }
    if length == 0 || length > MAX_ENV_FD_BYTES {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{BOOTSTRAP_FD_ENV} has invalid length"),
        ));
    }
    let bytes = unsafe { core::slice::from_raw_parts(pointer.cast::<u8>(), length) };
    let inherited_fd = parse_bootstrap_fd(bytes).map_err(|error| {
        AdapterError::new(
            FailureKind::InvalidTransport,
            format!("invalid {BOOTSTRAP_FD_ENV}: {error}"),
        )
    })?;
    require_inheritable(inherited_fd, "bootstrap")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    require_sealed_file(inherited_fd, BootstrapContext::ENCODED_LEN, "bootstrap")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let descriptor_fd = duplicate_cloexec(inherited_fd, "bootstrap")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let mut encoded = [0_u8; BootstrapContext::ENCODED_LEN];
    read_exact_at(descriptor_fd.as_raw_fd(), &mut encoded)
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let context = BootstrapContext::decode(&encoded).map_err(|error| {
        AdapterError::new(
            FailureKind::InvalidContext,
            format!("invalid bootstrap context: {error}"),
        )
    })?;
    if context.connector_kind() != Some(ConnectorKind::Preload) {
        return Err(AdapterError::new(
            FailureKind::InvalidContext,
            "inherited environment context is not a preload context",
        ));
    }
    Ok(context)
}

fn initialize_context(bootstrap: BootstrapContext) -> Result<Context, AdapterError> {
    bootstrap.validate().map_err(|error| {
        AdapterError::new(
            FailureKind::InvalidContext,
            format!("invalid bootstrap context: {error}"),
        )
    })?;
    FAIL_CLOSED.store(
        bootstrap
            .bootstrap_flags()
            .contains(BootstrapFlags::REQUIRED),
        Ordering::Release,
    );
    if bootstrap
        .bootstrap_flags()
        .contains(BootstrapFlags::SCM_RIGHTS_TRANSPORT)
    {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            "this demo adapter does not implement SCM_RIGHTS receipt",
        ));
    }
    for (fd, label) in [
        (bootstrap.artifact_fd, "artifact"),
        (bootstrap.code_fd, "code"),
        (bootstrap.state_fd, "state"),
    ] {
        if bootstrap
            .bootstrap_flags()
            .contains(BootstrapFlags::INHERIT_ACROSS_EXEC)
        {
            require_inheritable(fd, label)
                .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
        }
    }
    reject_same_file(&bootstrap)
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;

    let artifact_fd = duplicate_cloexec(bootstrap.artifact_fd, "artifact")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let code_fd = duplicate_cloexec(bootstrap.code_fd, "code")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let state_fd = duplicate_cloexec(bootstrap.state_fd, "state")
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    let artifact_len = usize::try_from(bootstrap.artifact_len).map_err(|_| {
        AdapterError::new(
            FailureKind::InvalidTransport,
            "artifact length does not fit this process",
        )
    })?;
    let artifact_bytes = read_transport_file(
        artifact_fd.as_raw_fd(),
        artifact_len,
        libc::F_SEAL_WRITE
            | libc::F_SEAL_GROW
            | libc::F_SEAL_SHRINK
            | libc::F_SEAL_SEAL
            | F_SEAL_EXEC,
        0,
        DescriptorAccess::Readable,
        "artifact",
    )?;
    let artifact =
        PodArtifact::from_bytes(artifact_bytes, bootstrap.artifact_sha256).map_err(|error| {
            AdapterError::new(
                FailureKind::IncompatibleImage,
                format!("artifact authentication failed: {error}"),
            )
        })?;
    let code_address = optional_address(
        bootstrap.code_address,
        bootstrap
            .bootstrap_flags()
            .contains(BootstrapFlags::FIXED_CODE_ADDRESS),
    )
    .map_err(|error| AdapterError::new(FailureKind::InvalidContext, error))?;
    let state_address = optional_address(
        bootstrap.state_address,
        bootstrap
            .bootstrap_flags()
            .contains(BootstrapFlags::FIXED_STATE_ADDRESS),
    )
    .map_err(|error| AdapterError::new(FailureKind::InvalidContext, error))?;
    let code_len = usize::try_from(artifact.header().code_len).map_err(|_| {
        AdapterError::new(
            FailureKind::IncompatibleImage,
            "authenticated code length does not fit this process",
        )
    })?;
    let mapped_code_len = page_round(code_len).ok_or_else(|| {
        AdapterError::new(
            FailureKind::IncompatibleImage,
            "authenticated code length overflows page rounding",
        )
    })?;
    let code_bytes = read_transport_file(
        code_fd.as_raw_fd(),
        mapped_code_len,
        libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
        0,
        DescriptorAccess::Readable,
        "code",
    )?;
    let code_digest: [u8; 32] = Sha256::digest(&code_bytes[..code_len]).into();
    if code_digest != artifact.header().code_sha256
        || code_bytes[code_len..].iter().any(|byte| *byte != 0)
    {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "sealed code bytes differ from the authenticated artifact",
        ));
    }
    if artifact.header().metadata.api_fingerprint != bootstrap.api_fingerprint()
        || artifact.header().state_file_len != bootstrap.state_len
    {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "authenticated image metadata differs from bootstrap context",
        ));
    }
    let state_len = usize::try_from(bootstrap.state_len).map_err(|_| {
        AdapterError::new(
            FailureKind::InvalidTransport,
            "state length does not fit this process",
        )
    })?;
    inspect_transport_file(
        state_fd.as_raw_fd(),
        state_len,
        libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL | F_SEAL_EXEC,
        libc::F_SEAL_WRITE | F_SEAL_FUTURE_WRITE,
        DescriptorAccess::ReadWrite,
        "state",
    )?;
    let state_envelope = read_transport_range(
        state_fd.as_raw_fd(),
        ENVELOPE_STATE_FINGERPRINT_OFFSET + 16,
        0,
        "state",
    )?;
    validate_state_identity_before_mapping(&state_envelope, &artifact, &bootstrap)?;
    let image =
        unsafe { PodImage::attach_trusted(&artifact, code_fd, code_address) }.map_err(|error| {
            AdapterError::new(
                FailureKind::Initialization,
                format!("code attachment failed: {error}"),
            )
        })?;
    validate_state_layout_identity(&state_envelope, &image)?;
    let state = image
        .attach_state(state_fd, state_address)
        .map_err(|error| {
            AdapterError::new(
                FailureKind::Initialization,
                format!("state attachment failed: {error}"),
            )
        })?;
    if state.generation() != bootstrap.generation {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "state generation changed during attachment",
        ));
    }
    image.verify_runtime_permissions(&state).map_err(|error| {
        AdapterError::new(
            FailureKind::Initialization,
            format!("mapping permission check failed: {error}"),
        )
    })?;
    Ok(Context {
        bootstrap,
        image,
        state,
    })
}

#[derive(Clone, Copy)]
enum DescriptorAccess {
    Readable,
    ReadWrite,
}

fn read_transport_file(
    fd: RawFd,
    expected_len: usize,
    required_seals: libc::c_int,
    forbidden_seals: libc::c_int,
    access: DescriptorAccess,
    label: &str,
) -> Result<Vec<u8>, AdapterError> {
    inspect_transport_file(
        fd,
        expected_len,
        required_seals,
        forbidden_seals,
        access,
        label,
    )?;
    read_transport_range(fd, expected_len, 0, label)
}

fn inspect_transport_file(
    fd: RawFd,
    expected_len: usize,
    required_seals: libc::c_int,
    forbidden_seals: libc::c_int,
    access: DescriptorAccess,
    label: &str,
) -> Result<(), AdapterError> {
    let stat = descriptor_stat(fd, label)
        .map_err(|error| AdapterError::new(FailureKind::InvalidTransport, error))?;
    if stat.st_mode & libc::S_IFMT != libc::S_IFREG {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{label} descriptor is not a regular memfd"),
        ));
    }
    if stat.st_size < 0 || usize::try_from(stat.st_size).ok() != Some(expected_len) {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{label} descriptor has the wrong length"),
        ));
    }
    let seals = unsafe { libc::fcntl(fd, libc::F_GET_SEALS) };
    if seals < 0 || seals & required_seals != required_seals {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{label} descriptor lacks required seals"),
        ));
    }
    if seals & forbidden_seals != 0 {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{label} descriptor carries a forbidden write seal"),
        ));
    }
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!(
                "cannot inspect {label} descriptor access: {}",
                std::io::Error::last_os_error()
            ),
        ));
    }
    let mode = flags & libc::O_ACCMODE;
    let valid_access = match access {
        DescriptorAccess::Readable => mode != libc::O_WRONLY,
        DescriptorAccess::ReadWrite => mode == libc::O_RDWR,
    };
    if !valid_access {
        return Err(AdapterError::new(
            FailureKind::InvalidTransport,
            format!("{label} descriptor has incompatible access mode"),
        ));
    }
    Ok(())
}

fn read_transport_range(
    fd: RawFd,
    length: usize,
    offset: usize,
    label: &str,
) -> Result<Vec<u8>, AdapterError> {
    let mut bytes = vec![0_u8; length];
    read_exact_at_offset(fd, &mut bytes, offset).map_err(|error| {
        AdapterError::new(
            FailureKind::InvalidTransport,
            format!("cannot read {label} descriptor: {error}"),
        )
    })?;
    Ok(bytes)
}

fn validate_state_identity_before_mapping(
    bytes: &[u8],
    artifact: &PodArtifact,
    bootstrap: &BootstrapContext,
) -> Result<(), AdapterError> {
    if read_u32(bytes, ENVELOPE_STATUS_OFFSET) != STATE_STATUS_READY {
        return Ok(());
    }
    let header = artifact.header();
    let mismatch = bytes[ENVELOPE_MAGIC_OFFSET..ENVELOPE_MAGIC_OFFSET + STATE_MAGIC.len()]
        != STATE_MAGIC
        || read_u32(bytes, ENVELOPE_VERSION_OFFSET) != STATE_VERSION
        || read_u32(bytes, ENVELOPE_FAILURE_OFFSET) != 0
        || bytes[ENVELOPE_CODE_HASH_OFFSET..ENVELOPE_CODE_HASH_OFFSET + 32] != header.code_sha256
        || read_u64(bytes, ENVELOPE_PAYLOAD_LEN_OFFSET) != header.payload_len
        || read_u64(bytes, ENVELOPE_GENERATION_OFFSET) != bootstrap.generation
        || read_u64(bytes, ENVELOPE_OWNER_PID_OFFSET) == 0
        || bytes[ENVELOPE_ARTIFACT_HASH_OFFSET..ENVELOPE_ARTIFACT_HASH_OFFSET + 32]
            != bootstrap.artifact_sha256
        || read_u64(bytes, ENVELOPE_FLAGS_OFFSET) != header.flags()
        || read_u64(bytes, ENVELOPE_REQUIRED_ADDRESS_OFFSET) != header.required_state_address()
        || read_u128(bytes, ENVELOPE_API_FINGERPRINT_OFFSET) != bootstrap.api_fingerprint()
        || read_u128(bytes, ENVELOPE_STATE_FINGERPRINT_OFFSET) != header.metadata.state_fingerprint;
    if mismatch {
        Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "state identity envelope differs from the authenticated image or bootstrap context",
        ))
    } else {
        Ok(())
    }
}

fn validate_state_layout_identity(bytes: &[u8], image: &PodImage) -> Result<(), AdapterError> {
    if read_u32(bytes, ENVELOPE_STATUS_OFFSET) != STATE_STATUS_READY {
        return Ok(());
    }
    if read_u64(bytes, ENVELOPE_LAYOUT_HASH_OFFSET) != image.layout_hash()
        || read_u64(bytes, ENVELOPE_LAYOUT_SIZE_OFFSET) != image.layout_size()
        || read_u64(bytes, ENVELOPE_LAYOUT_ALIGN_OFFSET) != image.layout_align()
    {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "state layout identity differs from the authenticated image",
        ));
    }
    Ok(())
}

fn read_u32(bytes: &[u8], offset: usize) -> u32 {
    u32::from_ne_bytes(
        bytes[offset..offset + 4]
            .try_into()
            .expect("audited offset"),
    )
}

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_ne_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("audited offset"),
    )
}

fn read_u128(bytes: &[u8], offset: usize) -> u128 {
    u128::from_ne_bytes(
        bytes[offset..offset + 16]
            .try_into()
            .expect("audited offset"),
    )
}

fn page_round(length: usize) -> Option<usize> {
    length
        .checked_add(PAGE_SIZE - 1)
        .map(|value| value & !(PAGE_SIZE - 1))
}

fn record_attachment(context: &Context) -> Result<(), AdapterError> {
    let pid = raw_getpid();
    loop {
        let attached = ATTACHED_PID.load(Ordering::Acquire);
        if attached == pid {
            return Ok(());
        }
        if attached == ATTACH_BUSY {
            wait_while_i32(&ATTACHED_PID, ATTACH_BUSY);
            continue;
        }
        if ATTACHED_PID
            .compare_exchange(attached, ATTACH_BUSY, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
        {
            let claim = AttachmentClaim::new(&ATTACHED_PID);
            context
                .image
                .upsert(&context.state, ATTACH_KEY, 1)
                .map_err(|error| {
                    AdapterError::new(
                        FailureKind::Initialization,
                        format!("attachment record failed: {error}"),
                    )
                })?;
            claim.publish(pid);
            return Ok(());
        }
    }
}

fn duplicate_cloexec(fd: RawFd, label: &str) -> Result<OwnedFd, String> {
    let duplicate = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 3) };
    if duplicate < 0 {
        return Err(format!(
            "cannot duplicate {label} descriptor: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(unsafe { OwnedFd::from_raw_fd(duplicate) })
}

fn require_inheritable(fd: RawFd, label: &str) -> Result<(), String> {
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
    if flags < 0 {
        return Err(format!(
            "invalid inherited {label} descriptor: {}",
            std::io::Error::last_os_error()
        ));
    }
    if flags & libc::FD_CLOEXEC != 0 {
        return Err(format!("inherited {label} descriptor is close-on-exec"));
    }
    Ok(())
}

fn require_sealed_file(fd: RawFd, expected_len: usize, label: &str) -> Result<(), String> {
    let seals = unsafe { libc::fcntl(fd, libc::F_GET_SEALS) };
    let required = libc::F_SEAL_WRITE
        | libc::F_SEAL_GROW
        | libc::F_SEAL_SHRINK
        | libc::F_SEAL_SEAL
        | F_SEAL_EXEC;
    if seals < 0 || seals & required != required {
        return Err(format!(
            "{label} descriptor is not an immutable no-exec memfd"
        ));
    }
    let stat = descriptor_stat(fd, label)?;
    if stat.st_size != expected_len as libc::off_t {
        return Err(format!("{label} descriptor has the wrong length"));
    }
    Ok(())
}

fn reject_same_file(context: &BootstrapContext) -> Result<(), String> {
    let artifact = descriptor_stat(context.artifact_fd, "artifact")?;
    let code = descriptor_stat(context.code_fd, "code")?;
    let state = descriptor_stat(context.state_fd, "state")?;
    for (left, right, label) in [
        (&artifact, &code, "artifact/code"),
        (&artifact, &state, "artifact/state"),
        (&code, &state, "code/state"),
    ] {
        if left.st_dev == right.st_dev && left.st_ino == right.st_ino {
            return Err(format!("{label} descriptors reference the same file"));
        }
    }
    Ok(())
}

fn descriptor_stat(fd: RawFd, label: &str) -> Result<libc::stat, String> {
    let mut stat = MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, stat.as_mut_ptr()) } != 0 {
        return Err(format!(
            "cannot stat {label} descriptor: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(unsafe { stat.assume_init() })
}

fn read_exact_at(fd: RawFd, bytes: &mut [u8]) -> Result<(), String> {
    read_exact_at_offset(fd, bytes, 0)
}

fn read_exact_at_offset(fd: RawFd, mut bytes: &mut [u8], mut offset: usize) -> Result<(), String> {
    while !bytes.is_empty() {
        let read = unsafe {
            libc::pread(
                fd,
                bytes.as_mut_ptr().cast(),
                bytes.len(),
                offset as libc::off_t,
            )
        };
        if read < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(format!("cannot read bootstrap descriptor: {error}"));
        }
        if read == 0 {
            return Err("bootstrap descriptor ended early".into());
        }
        let read = read as usize;
        bytes = &mut bytes[read..];
        offset += read;
    }
    Ok(())
}

fn wait_while_u32(state: &AtomicU32, expected: u32) {
    while state.load(Ordering::Acquire) == expected {
        futex_wait(state.as_ptr().cast(), expected);
    }
}

fn wait_while_i32(state: &AtomicI32, expected: i32) {
    while state.load(Ordering::Acquire) == expected {
        futex_wait(state.as_ptr().cast(), expected as u32);
    }
}

fn futex_wait(address: *const u32, expected: u32) {
    loop {
        let result = unsafe {
            libc::syscall(
                libc::SYS_futex,
                address,
                libc::FUTEX_WAIT | libc::FUTEX_PRIVATE_FLAG,
                expected,
                core::ptr::null::<libc::timespec>(),
            )
        };
        if result == 0 {
            return;
        }
        match std::io::Error::last_os_error().raw_os_error() {
            Some(libc::EINTR) => continue,
            Some(libc::EAGAIN) => return,
            _ => {
                // A kernel without private futex support is outside the demo's
                // supported platform. Keep correctness by polling indefinitely
                // instead of manufacturing an initialization failure.
                std::thread::yield_now();
                return;
            }
        }
    }
}

fn futex_wake_u32(state: &AtomicU32, count: i32) {
    futex_wake(state.as_ptr().cast(), count);
}

fn futex_wake_i32(state: &AtomicI32, count: i32) {
    futex_wake(state.as_ptr().cast(), count);
}

fn futex_wake(address: *const u32, count: i32) {
    let _ = unsafe {
        libc::syscall(
            libc::SYS_futex,
            address,
            libc::FUTEX_WAKE | libc::FUTEX_PRIVATE_FLAG,
            count,
        )
    };
}

fn optional_address(address: u64, required: bool) -> Result<Option<usize>, String> {
    if !required {
        return Ok(None);
    }
    usize::try_from(address)
        .map(Some)
        .map_err(|_| "fixed address does not fit usize".into())
}

fn report_failure(error: &str) {
    if !FAILURE_REPORTED.swap(true, Ordering::AcqRel) {
        raw_write(b"shmem-pod injected adapter: ");
        raw_write(error.as_bytes());
        raw_write(b"\n");
    }
    if FAIL_CLOSED.load(Ordering::Acquire) {
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
}

fn raw_write(mut bytes: &[u8]) {
    while !bytes.is_empty() {
        let written =
            unsafe { libc::write(libc::STDERR_FILENO, bytes.as_ptr().cast(), bytes.len()) };
        if written > 0 {
            bytes = &bytes[written as usize..];
        } else if written < 0 && unsafe { *libc::__errno_location() } == libc::EINTR {
            continue;
        } else {
            break;
        }
    }
}

#[cfg(all(
    target_os = "linux",
    target_arch = "x86_64",
    target_pointer_width = "64"
))]
#[inline]
fn raw_getuid() -> libc::uid_t {
    let mut result = libc::SYS_getuid as usize;
    unsafe {
        core::arch::asm!(
            "syscall",
            inlateout("rax") result,
            lateout("rcx") _,
            lateout("r11") _,
            options(nostack),
        );
    }
    result as libc::uid_t
}

#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
#[inline]
fn raw_getuid() -> libc::uid_t {
    let mut result = 0_usize;
    unsafe {
        core::arch::asm!(
            "svc 0",
            inlateout("x0") result,
            in("x8") libc::SYS_getuid as usize,
            options(nostack),
        );
    }
    result as libc::uid_t
}

#[cfg(all(
    target_os = "linux",
    not(any(
        all(target_arch = "x86_64", target_pointer_width = "64"),
        target_arch = "aarch64"
    ))
))]
#[inline]
fn raw_getuid() -> libc::uid_t {
    unsafe { libc::syscall(libc::SYS_getuid) as libc::uid_t }
}

#[inline]
fn raw_getpid() -> libc::pid_t {
    unsafe { libc::syscall(libc::SYS_getpid) as libc::pid_t }
}

#[inline]
fn raw_gettid() -> libc::pid_t {
    unsafe { libc::syscall(libc::SYS_gettid) as libc::pid_t }
}

unsafe extern "C" fn atfork_child() {
    finish_fork_barrier_child();
    let pid = raw_getpid();
    let pid_word = match u32::try_from(pid) {
        Ok(pid @ 1..=ATFORK_MAX_OWNER_PID) => pid,
        _ => {
            raw_write(b"shmem-pod injected adapter: child PID does not fit claim\n");
            unsafe { libc::_exit(FAILURE_EXIT_CODE) }
        }
    };
    let registration = ATFORK_CLAIM.load(Ordering::Acquire);
    if !matches!(atfork_state(registration), INIT_BUSY | INIT_READY)
        || atfork_owner_pid(registration) == 0
    {
        raw_write(b"shmem-pod injected adapter: corrupt child at-fork claim\n");
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
    // Running this callback proves that pthread_atfork installed the handler
    // before this fork's callback snapshot. It may therefore promote either a
    // published or still-BUSY registration claim without registering again.
    ATFORK_CLAIM.store((pid_word << 2) | INIT_READY, Ordering::Release);
    futex_wake_u32(&ATFORK_CLAIM, i32::MAX);
    ATTACHED_PID.store(0, Ordering::Release);
    // A successful prepare drained the initializer before fork. Observing BUSY
    // here therefore means a caller bypassed the adapter's admission contract;
    // fail closed instead of publishing an unwritten Context in the child.
    if INIT_STATE.load(Ordering::Acquire) == INIT_BUSY {
        INIT_STATE.store(INIT_FAILED, Ordering::Release);
        FAIL_CLOSED.store(true, Ordering::Release);
        raw_write(b"shmem-pod injected adapter: copied busy initializer across fork\n");
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
    FAILURE_REPORTED.store(false, Ordering::Release);
    PROCESS_EPOCH.store(pid, Ordering::Release);
    futex_wake_i32(&PROCESS_EPOCH, i32::MAX);
}

unsafe extern "C" fn atfork_prepare() {
    // Multiple application threads may call fork concurrently. Keep one
    // process-local owner across prepare -> parent/child so their copied state
    // cannot overwrite another fork's re-enable decision.
    FORK_BARRIER.prepare(&CALL_GATE);
}

unsafe extern "C" fn atfork_parent() {
    finish_fork_barrier_parent();
}

fn finish_fork_barrier_parent() {
    if FORK_BARRIER.finish_parent(&CALL_GATE).is_err() {
        FAIL_CLOSED.store(true, Ordering::Release);
        raw_write(b"shmem-pod injected adapter: corrupt parent at-fork gate state\n");
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
}

fn finish_fork_barrier_child() {
    if FORK_BARRIER.finish_child(&CALL_GATE).is_err() {
        FAIL_CLOSED.store(true, Ordering::Release);
        raw_write(b"shmem-pod injected adapter: corrupt child at-fork gate state\n");
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
}

fn atfork_state(word: u32) -> u32 {
    word & ATFORK_STATE_MASK
}

fn atfork_owner_pid(word: u32) -> u32 {
    word >> 2
}

fn atfork_pid(pid: i32) -> Result<u32, AdapterError> {
    let pid = u32::try_from(pid).ok();
    match pid {
        Some(pid @ 1..=ATFORK_MAX_OWNER_PID) => Ok(pid),
        _ => Err(AdapterError::new(
            FailureKind::Initialization,
            "process ID does not fit the packed pthread_atfork claim",
        )),
    }
}

fn atfork_busy_word(pid: u32) -> u32 {
    debug_assert!((1..=ATFORK_MAX_OWNER_PID).contains(&pid));
    (pid << 2) | INIT_BUSY
}

fn try_claim_atfork_registration(state: &AtomicU32, pid: u32) -> Option<u32> {
    let busy_word = atfork_busy_word(pid);
    state
        .compare_exchange(INIT_EMPTY, busy_word, Ordering::AcqRel, Ordering::Acquire)
        .ok()
        .map(|_| busy_word)
}

fn ensure_atfork_registered() -> Result<(), AdapterError> {
    loop {
        let observed = ATFORK_CLAIM.load(Ordering::Acquire);
        match observed {
            INIT_FAILED => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "an earlier pthread_atfork registration failed",
                ));
            }
            INIT_EMPTY => {
                let pid = atfork_pid(raw_getpid())?;
                let Some(busy_word) = try_claim_atfork_registration(&ATFORK_CLAIM, pid) else {
                    continue;
                };
                let claim = AtforkRegistrationClaim::new(&ATFORK_CLAIM, busy_word);
                let result = unsafe {
                    libc::pthread_atfork(
                        Some(atfork_prepare),
                        Some(atfork_parent),
                        Some(atfork_child),
                    )
                };
                if result != 0 {
                    FAIL_CLOSED.store(true, Ordering::Release);
                    return Err(AdapterError::new(
                        FailureKind::Initialization,
                        format!("pthread_atfork registration failed with status {result}"),
                    ));
                }
                if let Err(error) = claim.publish_ready() {
                    FAIL_CLOSED.store(true, Ordering::Release);
                    return Err(error);
                }
                return Ok(());
            }
            word if atfork_state(word) == INIT_READY && atfork_owner_pid(word) != 0 => {
                let pid = atfork_pid(raw_getpid())?;
                if atfork_owner_pid(word) == pid {
                    return Ok(());
                }
                FAIL_CLOSED.store(true, Ordering::Release);
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "pthread_atfork READY claim belongs to another process epoch",
                ));
            }
            word if atfork_state(word) == INIT_BUSY && atfork_owner_pid(word) != 0 => {
                let pid = atfork_pid(raw_getpid())?;
                if atfork_owner_pid(word) != pid {
                    FAIL_CLOSED.store(true, Ordering::Release);
                    return Err(AdapterError::new(
                        FailureKind::Initialization,
                        "fork child inherited an ambiguous pthread_atfork registration",
                    ));
                }
                wait_while_u32(&ATFORK_CLAIM, word);
            }
            _ => {
                return Err(AdapterError::new(
                    FailureKind::Initialization,
                    "pthread_atfork registration state is corrupt",
                ));
            }
        }
    }
}

/// Returns the stable bootstrap ABI revision without touching adapter state.
///
/// The ELF DSO carries `DF_1_NODELETE`, so a pointer to this entry remains
/// callable after a matching `dlclose` for the lifetime of the process.
#[unsafe(no_mangle)]
pub extern "C" fn shmem_pod_adapter_abi_version_v1() -> u16 {
    BootstrapContext::ABI_VERSION
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::process::Command;
    use std::process::Stdio;
    use std::sync::Arc;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    static THIRD_PARTY_PREPARE_ENTERED: AtomicBool = AtomicBool::new(false);
    static THIRD_PARTY_PREPARE_RELEASE: AtomicBool = AtomicBool::new(false);
    static NESTED_FORK_ATTEMPTED: AtomicBool = AtomicBool::new(false);
    const NESTED_FORK_HELPER_ENV: &str = "SHMEM_POD_NESTED_FORK_HELPER";
    const FOREIGN_OWNER_HELPER_ENV: &str = "SHMEM_POD_FOREIGN_OWNER_HELPER";

    fn foreign_owner_for(identity: u64) -> u64 {
        let pid = fork_barrier_pid(identity);
        let foreign_pid = if pid == u32::MAX { pid - 1 } else { pid + 1 };
        (u64::from(foreign_pid) << 32) | u64::from(fork_barrier_tid(identity))
    }

    fn assert_fail_stop_helper(test_name: &str, environment: &str, expected: &str) {
        let executable = std::env::current_exe().expect("resolve test executable");
        let mut child = Command::new(executable)
            .arg("--exact")
            .arg(test_name)
            .arg("--nocapture")
            .env(environment, "1")
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn fail-stop helper");
        let deadline = Instant::now() + Duration::from_secs(3);
        let status = loop {
            if let Some(status) = child.try_wait().expect("poll fail-stop helper") {
                break status;
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("fail-stop helper hung in at-fork prepare");
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        assert_eq!(
            status.code(),
            Some(FAILURE_EXIT_CODE),
            "at-fork violation must fail stop with the adapter status"
        );
        let mut diagnostic = String::new();
        child
            .stderr
            .take()
            .expect("capture fail-stop helper stderr")
            .read_to_string(&mut diagnostic)
            .expect("read fail-stop helper stderr");
        assert!(
            diagnostic.contains(expected),
            "at-fork violation must report {expected:?}: {diagnostic:?}"
        );
    }

    unsafe extern "C" fn paused_third_party_prepare() {
        THIRD_PARTY_PREPARE_ENTERED.store(true, Ordering::Release);
        while !THIRD_PARTY_PREPARE_RELEASE.load(Ordering::Acquire) {
            std::hint::spin_loop();
        }
    }

    unsafe extern "C" fn nested_fork_third_party_prepare() {
        if !NESTED_FORK_ATTEMPTED.swap(true, Ordering::AcqRel) {
            // This older handler runs after the newer shim prepare has claimed
            // its barrier. The nested prepare must fail stop before fork can
            // return. Any return is an explicit regression failure.
            let _ = unsafe { libc::fork() };
            unsafe { libc::_exit(92) }
        }
    }

    #[test]
    fn older_prepare_nested_libc_fork_fails_stop_without_hanging() {
        if std::env::var_os(NESTED_FORK_HELPER_ENV).is_some() {
            if ATFORK_CLAIM.load(Ordering::Acquire) != INIT_EMPTY {
                unsafe { libc::_exit(93) }
            }
            assert!(ensure_process_epoch().is_ok());
            assert_eq!(
                unsafe { libc::pthread_atfork(Some(nested_fork_third_party_prepare), None, None) },
                0
            );
            assert!(ensure_atfork_registered().is_ok());

            // Prepare callbacks run in reverse registration order: the shim
            // owns its barrier before the older handler attempts this fork.
            let _ = unsafe { libc::fork() };
            unsafe { libc::_exit(94) }
        }

        assert_fail_stop_helper(
            "tests::older_prepare_nested_libc_fork_fails_stop_without_hanging",
            NESTED_FORK_HELPER_ENV,
            "nested libc fork on at-fork owner thread",
        );
    }

    #[test]
    fn copied_foreign_owner_fails_stop_without_hanging() {
        if std::env::var_os(FOREIGN_OWNER_HELPER_ENV).is_some() {
            let identity = fork_barrier_identity().unwrap();
            FORK_BARRIER
                .owner
                .store(foreign_owner_for(identity), Ordering::Release);
            FORK_BARRIER.prepare(&CALL_GATE);
            unsafe { libc::_exit(95) }
        }

        assert_fail_stop_helper(
            "tests::copied_foreign_owner_fails_stop_without_hanging",
            FOREIGN_OWNER_HELPER_ENV,
            "inherited at-fork barrier owner",
        );
    }

    #[test]
    fn registration_added_after_fork_snapshot_recovers_child_epoch() {
        assert_eq!(ATFORK_CLAIM.load(Ordering::Acquire), INIT_EMPTY);
        assert!(ensure_process_epoch().is_ok());
        let parent_pid = raw_getpid();
        assert_eq!(PROCESS_EPOCH.load(Ordering::Acquire), parent_pid);
        assert_eq!(
            unsafe { libc::pthread_atfork(Some(paused_third_party_prepare), None, None) },
            0
        );

        let (forked_tx, forked_rx) = mpsc::channel();
        let fork_thread = std::thread::spawn(move || {
            let fork_result = unsafe { libc::fork() };
            if fork_result == 0 {
                let child_pid = raw_getpid();
                let recovered = ensure_process_epoch().is_ok();
                let registration = ATFORK_CLAIM.load(Ordering::Acquire);
                let valid = recovered
                    && PROCESS_EPOCH.load(Ordering::Acquire) == child_pid
                    && CALL_GATE.active_calls() == 0
                    && !CALL_GATE.is_disabled()
                    && atfork_state(registration) == INIT_READY
                    && atfork_owner_pid(registration) == child_pid as u32;
                unsafe { libc::_exit(if valid { 0 } else { 90 }) }
            }
            forked_tx.send(fork_result).unwrap();
        });

        let deadline = Instant::now() + Duration::from_secs(2);
        while !THIRD_PARTY_PREPARE_ENTERED.load(Ordering::Acquire) {
            assert!(Instant::now() < deadline, "third-party prepare did not run");
            std::thread::yield_now();
        }

        // This token belongs to a thread which vanishes from the child. Since
        // registration occurs after the fork callback snapshot, our prepare
        // handler cannot drain it for this fork; PID-epoch recovery must use
        // the explicit child-only force reset instead.
        let mut phantom_parent_call = Some(CALL_GATE.try_enter().unwrap());
        assert!(ensure_atfork_registered().is_ok());
        let ready = ATFORK_CLAIM.load(Ordering::Acquire);
        assert_eq!(atfork_state(ready), INIT_READY);
        assert_eq!(atfork_owner_pid(ready), parent_pid as u32);
        THIRD_PARTY_PREPARE_RELEASE.store(true, Ordering::Release);

        let child_pid = match forked_rx.recv_timeout(Duration::from_secs(2)) {
            Ok(pid) => pid,
            Err(error) => {
                // Avoid hanging the test if a libc implementation included a
                // handler registered after its prepare snapshot.
                drop(phantom_parent_call.take());
                let pid = forked_rx.recv_timeout(Duration::from_secs(2)).unwrap();
                let _ = fork_thread.join();
                let mut status = 0;
                unsafe { libc::waitpid(pid, &mut status, 0) };
                panic!("fork did not bypass the late shim callback: {error}");
            }
        };
        drop(phantom_parent_call.take());
        fork_thread.join().unwrap();

        assert!(child_pid > 0);
        let mut status = 0;
        assert_eq!(
            unsafe { libc::waitpid(child_pid, &mut status, 0) },
            child_pid
        );
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
        assert_eq!(CALL_GATE.active_calls(), 0);

        // A BUSY claim cannot reveal whether pthread_atfork inserted the
        // callback before the creating fork's snapshot. Model that child-only
        // copy and require immediate fail-stop without changing or replacing
        // the ambiguous registration claim.
        let ambiguous_child = unsafe { libc::fork() };
        if ambiguous_child == 0 {
            let inherited_busy = atfork_busy_word(parent_pid as u32);
            ATFORK_CLAIM.store(inherited_busy, Ordering::Release);
            PROCESS_EPOCH.store(parent_pid, Ordering::Release);
            let rejected = ensure_process_epoch().is_err();
            let valid = rejected
                && PROCESS_EPOCH.load(Ordering::Acquire) == PROCESS_EPOCH_FAILED
                && ATFORK_CLAIM.load(Ordering::Acquire) == inherited_busy;
            unsafe { libc::_exit(if valid { 0 } else { 91 }) }
        }
        assert!(ambiguous_child > 0);
        let mut ambiguous_status = 0;
        assert_eq!(
            unsafe { libc::waitpid(ambiguous_child, &mut ambiguous_status, 0) },
            ambiguous_child
        );
        assert!(libc::WIFEXITED(ambiguous_status));
        assert_eq!(libc::WEXITSTATUS(ambiguous_status), 0);
    }

    #[test]
    fn unpublished_initialization_claim_marks_state_failed_on_panic() {
        let state = AtomicU32::new(INIT_BUSY);
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            let _claim = InitClaim::new(&state);
            panic!("injected initializer panic");
        }));
        assert!(result.is_err());
        assert_eq!(state.load(Ordering::Acquire), INIT_FAILED);
    }

    #[test]
    fn published_initialization_claim_marks_state_ready() {
        let state = AtomicU32::new(INIT_BUSY);
        InitClaim::new(&state).publish_ready();
        assert_eq!(state.load(Ordering::Acquire), INIT_READY);
    }

    #[test]
    fn delayed_live_initializer_does_not_make_waiter_fail() {
        let state = Arc::new(AtomicU32::new(INIT_BUSY));
        let (started_tx, started_rx) = mpsc::channel();
        let (finished_tx, finished_rx) = mpsc::channel();
        let waiter_state = Arc::clone(&state);
        let waiter = std::thread::spawn(move || {
            started_tx.send(()).unwrap();
            wait_while_u32(&waiter_state, INIT_BUSY);
            finished_tx
                .send(waiter_state.load(Ordering::Acquire))
                .unwrap();
        });
        started_rx.recv().unwrap();
        assert!(finished_rx.recv_timeout(Duration::from_millis(50)).is_err());
        InitClaim::new(&state).publish_ready();
        assert_eq!(
            finished_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
            INIT_READY
        );
        waiter.join().unwrap();
    }

    #[test]
    fn unpublished_atfork_claim_marks_state_failed_on_unwind() {
        const PID: u32 = 42;
        let state = AtomicU32::new(INIT_EMPTY);
        let busy_word = try_claim_atfork_registration(&state, PID).unwrap();
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            let _claim = AtforkRegistrationClaim::new(&state, busy_word);
            panic!("injected pthread_atfork registration panic");
        }));
        assert!(result.is_err());
        assert_eq!(state.load(Ordering::Acquire), INIT_FAILED);
    }

    #[test]
    fn attachment_claim_resets_busy_on_unwind() {
        let state = AtomicI32::new(ATTACH_BUSY);
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            let _claim = AttachmentClaim::new(&state);
            panic!("injected attachment panic");
        }));
        assert!(result.is_err());
        assert_eq!(state.load(Ordering::Acquire), 0);
    }

    #[test]
    fn attachment_claim_publishes_pid() {
        let state = AtomicI32::new(ATTACH_BUSY);
        AttachmentClaim::new(&state).publish(42);
        assert_eq!(state.load(Ordering::Acquire), 42);
    }

    #[test]
    fn hook_guard_rejects_same_thread_reentrancy() {
        let outer = HookGuard::enter().unwrap();
        assert!(HookGuard::enter().is_none());
        drop(outer);
        assert!(HookGuard::enter().is_some());
    }

    #[test]
    fn fork_barrier_disables_drains_and_reenables() {
        let gate = Arc::new(AdapterCallGate::new());
        let barrier = Arc::new(ForkBarrier::new());
        let live = gate.try_enter().unwrap();
        let (prepared_tx, prepared_rx) = mpsc::channel();
        let (finish_tx, finish_rx) = mpsc::channel();
        let worker_gate = Arc::clone(&gate);
        let worker_barrier = Arc::clone(&barrier);
        let prepare = std::thread::spawn(move || {
            worker_barrier.prepare(&worker_gate);
            prepared_tx.send(()).unwrap();
            finish_rx.recv().unwrap();
            worker_barrier.finish_parent(&worker_gate).unwrap();
        });

        for _ in 0..1_000_000 {
            if gate.is_disabled() {
                break;
            }
            std::hint::spin_loop();
        }
        assert!(gate.is_disabled());
        assert_eq!(gate.active_calls(), 1);
        drop(live);
        prepared_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(gate.active_calls(), 0);
        assert!(gate.is_disabled());
        finish_tx.send(()).unwrap();
        prepare.join().unwrap();
        assert!(!gate.is_disabled());
        assert!(gate.try_enter().is_some());
    }

    #[test]
    fn fork_child_rebinds_inherited_owner_before_gate_reset() {
        let barrier = ForkBarrier::new();
        let child_identity = fork_barrier_identity().unwrap();
        let inherited_parent_identity = foreign_owner_for(child_identity);
        barrier
            .owner
            .store(inherited_parent_identity, Ordering::Release);

        let rebound = barrier.rebind_child_owner().unwrap();
        assert_eq!(rebound, child_identity);
        assert_eq!(barrier.owner.load(Ordering::Acquire), child_identity);
        barrier
            .owner
            .compare_exchange(child_identity, 0, Ordering::Release, Ordering::Relaxed)
            .unwrap();
    }
}
