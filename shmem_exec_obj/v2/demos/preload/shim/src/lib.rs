//! `LD_PRELOAD`, ptrace, and trampoline bootstrap adapter for the demo pod.
//!
//! The unaware guest sees only the interposed libc ABI. Injectors can instead
//! call [`shmem_pod_bootstrap_v1`] with the same versioned context.

use shmem_pod::injection::{
    AdapterCallGate, BOOTSTRAP_FD_ENV, BootstrapContext, BootstrapFlags, BootstrapStatus,
    ConnectorKind, parse_bootstrap_fd,
};
use shmem_pod_runtime::{PodArtifact, PodImage, PodState, hex};
use std::cell::{Cell, UnsafeCell};
use std::ffi::c_char;
use std::fmt;
use std::mem::MaybeUninit;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU8, Ordering};

const CALL_KEY: u64 = 0x7072_656c_6f61_6401;
const ATTACH_KEY: u64 = 0x7072_656c_6f61_6402;
const FAILURE_EXIT_CODE: libc::c_int = 125;
const INIT_EMPTY: u8 = 0;
const INIT_BUSY: u8 = 1;
const INIT_READY: u8 = 2;
const INIT_FAILED: u8 = 3;
const ATTACH_BUSY: i32 = -1;
const MAX_ENV_FD_BYTES: usize = 10;
const F_SEAL_EXEC: libc::c_int = 0x0020;

static INIT_STATE: AtomicU8 = AtomicU8::new(INIT_EMPTY);
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
    message: String,
}

impl AdapterError {
    fn new(kind: FailureKind, message: impl Into<String>) -> Self {
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
    state: &'a AtomicU8,
    published: bool,
}

struct ForkBarrier {
    locked: AtomicBool,
    reenable: AtomicBool,
}

impl ForkBarrier {
    const fn new() -> Self {
        Self {
            locked: AtomicBool::new(false),
            reenable: AtomicBool::new(false),
        }
    }

    fn prepare(&self, gate: &AdapterCallGate) {
        while self
            .locked
            .compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed)
            .is_err()
        {
            std::hint::spin_loop();
        }
        self.reenable.store(!gate.is_disabled(), Ordering::Relaxed);
        let _ = gate.disable();
        while gate.active_calls() != 0 {
            std::hint::spin_loop();
        }
    }

    fn finish(&self, gate: &AdapterCallGate) -> Result<(), ()> {
        let reset = if self.reenable.load(Ordering::Relaxed) {
            // SAFETY: prepare disabled this same gate and waited for the last
            // token before the fork copied the barrier and gate state.
            unsafe { gate.reset_after_fork() }.map_err(|_| ())
        } else {
            Ok(())
        };
        self.reenable.store(false, Ordering::Relaxed);
        self.locked.store(false, Ordering::Release);
        reset
    }
}

impl<'a> InitClaim<'a> {
    fn new(state: &'a AtomicU8) -> Self {
        Self {
            state,
            published: false,
        }
    }

    fn publish_ready(mut self) {
        self.state.store(INIT_READY, Ordering::Release);
        self.published = true;
    }
}

impl Drop for InitClaim<'_> {
    fn drop(&mut self) {
        if !self.published {
            self.state.store(INIT_FAILED, Ordering::Release);
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

/// Interposes libc's `getuid` while preserving its return value and `errno`.
///
/// # Safety
///
/// The caller must use libc's `getuid` C ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn getuid() -> libc::uid_t {
    let result = raw_getuid();
    let Some(_call) = CALL_GATE.try_enter() else {
        return result;
    };
    let Some(_hook) = HookGuard::enter() else {
        return result;
    };
    let errno = unsafe { libc::__errno_location() };
    let saved_errno = unsafe { *errno };

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
    let Some(_call) = CALL_GATE.try_enter() else {
        return BootstrapStatus::Disabled as i32;
    };
    let Some(_hook) = HookGuard::enter() else {
        return BootstrapStatus::Reentrant as i32;
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
    let mut busy_observations = 0_u32;
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
                busy_observations = busy_observations.saturating_add(1);
                if busy_observations == 1_000_000 {
                    return Err(AdapterError::new(
                        FailureKind::Initialization,
                        "adapter initialization remained busy",
                    ));
                }
                std::hint::spin_loop();
                if busy_observations % 4096 == 0 {
                    std::thread::yield_now();
                }
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
    let digest = hex(&bootstrap.artifact_sha256);
    let artifact =
        PodArtifact::open_sealed_fd(artifact_fd.as_raw_fd(), &digest).map_err(|error| {
            AdapterError::new(
                FailureKind::IncompatibleImage,
                format!("artifact authentication failed: {error}"),
            )
        })?;
    if artifact.len() as u64 != bootstrap.artifact_len {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "artifact length differs from bootstrap context",
        ));
    }
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
    let image =
        unsafe { PodImage::attach_trusted(&artifact, code_fd, code_address) }.map_err(|error| {
            AdapterError::new(
                FailureKind::Initialization,
                format!("code attachment failed: {error}"),
            )
        })?;
    if image.api_fingerprint() != bootstrap.api_fingerprint()
        || image.state_file_len() != bootstrap.state_len
    {
        return Err(AdapterError::new(
            FailureKind::IncompatibleImage,
            "authenticated image metadata differs from bootstrap context",
        ));
    }
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
            "state generation differs from bootstrap context",
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

fn record_attachment(context: &Context) -> Result<(), AdapterError> {
    let pid = raw_getpid();
    loop {
        let attached = ATTACHED_PID.load(Ordering::Acquire);
        if attached == pid {
            return Ok(());
        }
        if attached == ATTACH_BUSY {
            std::hint::spin_loop();
            continue;
        }
        if ATTACHED_PID
            .compare_exchange(attached, ATTACH_BUSY, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
        {
            let result = context
                .image
                .upsert(&context.state, ATTACH_KEY, 1)
                .map_err(|error| {
                    AdapterError::new(
                        FailureKind::Initialization,
                        format!("attachment record failed: {error}"),
                    )
                });
            ATTACHED_PID.store(if result.is_ok() { pid } else { 0 }, Ordering::Release);
            return result;
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

fn read_exact_at(fd: RawFd, mut bytes: &mut [u8]) -> Result<(), String> {
    let mut offset = 0;
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

unsafe extern "C" fn atfork_child() {
    finish_fork_barrier();
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
}

unsafe extern "C" fn atfork_prepare() {
    // Multiple application threads may call fork concurrently. Keep one
    // process-local owner across prepare -> parent/child so their copied state
    // cannot overwrite another fork's re-enable decision.
    FORK_BARRIER.prepare(&CALL_GATE);
}

unsafe extern "C" fn atfork_parent() {
    finish_fork_barrier();
}

fn finish_fork_barrier() {
    if FORK_BARRIER.finish(&CALL_GATE).is_err() {
        FAIL_CLOSED.store(true, Ordering::Release);
        raw_write(b"shmem-pod injected adapter: corrupt at-fork gate state\n");
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
}

extern "C" fn initialize_adapter() {
    // Registration only; mapping and allocation are deferred until a normal
    // hook safe point rather than performed under the dynamic-loader lock.
    let result = unsafe {
        libc::pthread_atfork(
            Some(atfork_prepare),
            Some(atfork_parent),
            Some(atfork_child),
        )
    };
    if result != 0 {
        FAIL_CLOSED.store(true, Ordering::Release);
        report_failure("pthread_atfork registration failed");
    }
}

extern "C" fn disable_adapter() {
    // Stop admission and drain existing hooks before the loader may unmap this
    // DSO's text. Context deliberately remains mapped: dropping it here would
    // race any external trampoline which was not removed before dlclose.
    let _ = CALL_GATE.disable();
    while CALL_GATE.active_calls() != 0 {
        std::hint::spin_loop();
    }
}

#[used]
#[unsafe(link_section = ".init_array")]
static INITIALIZER: extern "C" fn() = initialize_adapter;

#[used]
#[unsafe(link_section = ".fini_array")]
static FINALIZER: extern "C" fn() = disable_adapter;

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn unpublished_initialization_claim_marks_state_failed_on_panic() {
        let state = AtomicU8::new(INIT_BUSY);
        let result = panic::catch_unwind(AssertUnwindSafe(|| {
            let _claim = InitClaim::new(&state);
            panic!("injected initializer panic");
        }));
        assert!(result.is_err());
        assert_eq!(state.load(Ordering::Acquire), INIT_FAILED);
    }

    #[test]
    fn published_initialization_claim_marks_state_ready() {
        let state = AtomicU8::new(INIT_BUSY);
        InitClaim::new(&state).publish_ready();
        assert_eq!(state.load(Ordering::Acquire), INIT_READY);
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
        let worker_gate = Arc::clone(&gate);
        let worker_barrier = Arc::clone(&barrier);
        let prepare = std::thread::spawn(move || worker_barrier.prepare(&worker_gate));

        for _ in 0..1_000_000 {
            if gate.is_disabled() {
                break;
            }
            std::hint::spin_loop();
        }
        assert!(gate.is_disabled());
        assert_eq!(gate.active_calls(), 1);
        drop(live);
        prepare.join().unwrap();
        assert_eq!(gate.active_calls(), 0);
        assert!(gate.is_disabled());
        barrier.finish(&gate).unwrap();
        assert!(!gate.is_disabled());
        assert!(gate.try_enter().is_some());
    }
}
