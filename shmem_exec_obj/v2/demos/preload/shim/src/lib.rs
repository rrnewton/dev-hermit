//! `LD_PRELOAD` bootstrap for the executable shared-memory pod demo.
//!
//! The shim intentionally exposes only a libc ABI. The injected program does
//! does not link to the pod SDK or to this shim.

use shmem_pod_runtime::{PodArtifact, PodImage, PodState};
use std::cell::Cell;
use std::env;
use std::ffi::OsStr;
use std::os::fd::{FromRawFd, OwnedFd, RawFd};
use std::panic::{self, AssertUnwindSafe};
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};

const IMAGE_ENV: &str = "SHMEM_POD_PRELOAD_IMAGE";
const SHA256_ENV: &str = "SHMEM_POD_PRELOAD_SHA256";
const CODE_FD_ENV: &str = "SHMEM_POD_PRELOAD_CODE_FD";
const STATE_FD_ENV: &str = "SHMEM_POD_PRELOAD_STATE_FD";
const REQUIRED_ENV: &str = "SHMEM_POD_PRELOAD_REQUIRED";

// These keys form the tiny application ABI between this demo shim and host.
const CALL_KEY: u64 = 0x7072_656c_6f61_6401;
const ATTACH_KEY: u64 = 0x7072_656c_6f61_6402;
const FAILURE_EXIT_CODE: libc::c_int = 125;

static CONTEXT: OnceLock<Result<Context, String>> = OnceLock::new();
static FAILURE_REPORTED: AtomicBool = AtomicBool::new(false);

thread_local! {
    static INSIDE_HOOK: Cell<bool> = const { Cell::new(false) };
}

struct Context {
    image: PodImage,
    state: PodState,
}

struct HookGuard;

impl HookGuard {
    fn enter() -> Option<Self> {
        INSIDE_HOOK
            .try_with(|inside| {
                if inside.replace(true) {
                    None
                } else {
                    Some(Self)
                }
            })
            .ok()
            .flatten()
    }
}

impl Drop for HookGuard {
    fn drop(&mut self) {
        let _ = INSIDE_HOOK.try_with(|inside| inside.set(false));
    }
}

/// Interposes libc's `getuid` while preserving its observable return value and
/// errno behavior. The real operation uses the raw syscall, so resolution does
/// not recurse through the dynamic linker or this symbol.
///
/// # Safety
///
/// The caller must invoke this symbol with libc's `getuid` C ABI. There are no
/// pointer or lifetime preconditions.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn getuid() -> libc::uid_t {
    let Some(_guard) = HookGuard::enter() else {
        return raw_getuid();
    };
    let errno = unsafe { libc::__errno_location() };
    let saved_errno = unsafe { *errno };
    let result = raw_getuid();

    let update = panic::catch_unwind(AssertUnwindSafe(record_call));
    match update {
        Ok(Ok(())) => {}
        Ok(Err(error)) => report_failure(&error),
        Err(_) => report_failure("panic while initializing or calling the pod"),
    }

    // `getuid` does not modify errno. Pod setup and method dispatch may do so,
    // so restore the caller's value before crossing back through the libc ABI.
    unsafe { *errno = saved_errno };
    result
}

#[cfg(all(
    target_os = "linux",
    target_arch = "x86_64",
    target_pointer_width = "64"
))]
#[inline]
fn raw_getuid() -> libc::uid_t {
    let mut result = 102_usize;
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
            in("x8") 174_usize,
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

fn record_call() -> Result<(), String> {
    let context = match CONTEXT.get_or_init(initialize_context) {
        Ok(context) => context,
        Err(error) => return Err(error.clone()),
    };
    context
        .image
        .upsert(&context.state, CALL_KEY, 1)
        .map_err(|error| format!("pod call failed: {error}"))
}

fn initialize_context() -> Result<Context, String> {
    let image_path = env::var_os(IMAGE_ENV).ok_or_else(|| format!("{IMAGE_ENV} is not set"))?;
    let sha256 = env::var(SHA256_ENV).map_err(|_| format!("{SHA256_ENV} is not valid UTF-8"))?;
    let code_fd = inherited_fd(CODE_FD_ENV)?;
    let state_fd = inherited_fd(STATE_FD_ENV)?;
    if code_fd == state_fd {
        return Err("code and state descriptors alias".into());
    }

    let artifact = PodArtifact::open(image_path, &sha256)
        .map_err(|error| format!("artifact authentication failed: {error}"))?;

    // Ownership moves into the runtime. The descriptors intentionally remain
    // non-CLOEXEC so a recursively executed child can attach to the same memfds.
    let code_fd = unsafe { OwnedFd::from_raw_fd(code_fd) };
    let state_fd = unsafe { OwnedFd::from_raw_fd(state_fd) };
    let image = unsafe { PodImage::attach_trusted(&artifact, code_fd, None) }
        .map_err(|error| format!("code attachment failed: {error}"))?;
    let state = image
        .attach_state(state_fd, None)
        .map_err(|error| format!("state attachment failed: {error}"))?;
    image
        .verify_runtime_permissions(&state)
        .map_err(|error| format!("mapping permission check failed: {error}"))?;
    image
        .upsert(&state, ATTACH_KEY, 1)
        .map_err(|error| format!("attachment record failed: {error}"))?;

    Ok(Context { image, state })
}

fn inherited_fd(name: &str) -> Result<RawFd, String> {
    let value = env::var(name).map_err(|_| format!("{name} is not valid UTF-8"))?;
    let fd = value
        .parse::<RawFd>()
        .map_err(|_| format!("{name} is not a file descriptor"))?;
    if fd <= libc::STDERR_FILENO {
        return Err(format!("{name} aliases standard I/O"));
    }
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
    if flags < 0 {
        return Err(format!(
            "{name} is invalid: {}",
            std::io::Error::last_os_error()
        ));
    }
    if flags & libc::FD_CLOEXEC != 0 {
        return Err(format!("{name} is unexpectedly close-on-exec"));
    }
    Ok(fd)
}

fn report_failure(error: &str) {
    if !FAILURE_REPORTED.swap(true, Ordering::AcqRel) {
        raw_write(b"shmem-pod preload shim: ");
        raw_write(error.as_bytes());
        raw_write(b"\n");
    }
    if env::var_os(REQUIRED_ENV).as_deref() == Some(OsStr::new("1")) {
        unsafe { libc::_exit(FAILURE_EXIT_CODE) }
    }
}

fn raw_write(mut bytes: &[u8]) {
    while !bytes.is_empty() {
        let written =
            unsafe { libc::write(libc::STDERR_FILENO, bytes.as_ptr().cast(), bytes.len()) };
        if written > 0 {
            bytes = &bytes[written as usize..];
        } else if written < 0
            && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted
        {
            continue;
        } else {
            break;
        }
    }
}
