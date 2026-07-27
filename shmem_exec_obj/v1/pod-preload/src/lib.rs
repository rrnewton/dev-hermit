use pod_api::{PodMode, STATUS_OK};
use pod_loader::MappedPod;
use std::env;
use std::path::Path;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

struct Client {
    pod: MappedPod,
    mode: PodMode,
    expected_processes: u64,
    pid: u64,
}

static CLIENT: OnceLock<Result<Client, String>> = OnceLock::new();
static INITIALIZING: AtomicBool = AtomicBool::new(false);

fn load_client() -> Result<Client, String> {
    let path = env::var_os("POD_INSTANCE").ok_or("POD_INSTANCE is not set")?;
    let mode_value = env::var("POD_MODE").map_err(|error| error.to_string())?;
    let mode = PodMode::parse(&mode_value)
        .ok_or_else(|| format!("POD_MODE {mode_value:?} is not supported"))?;
    let expected_processes = env::var("POD_EXPECTED_PROCESSES")
        .map_err(|error| error.to_string())?
        .parse::<u64>()
        .map_err(|error| error.to_string())?;
    if expected_processes == 0 || expected_processes > pod_api::MAX_CONNECTIONS as u64 {
        return Err(format!(
            "POD_EXPECTED_PROCESSES {expected_processes} is out of range"
        ));
    }

    let pid = current_pid();
    let pod = MappedPod::open_with_address_tag(Path::new(&path), Some(pid))
        .map_err(|error| error.to_string())?;
    // The host creates this image from the reviewed built-in pod source. The
    // relocation gate alone would not make an arbitrary image safe to execute.
    unsafe { pod.register(pid, mode) }.map_err(|error| error.to_string())?;
    Ok(Client {
        pod,
        mode,
        expected_processes,
        pid,
    })
}

fn client() -> Option<&'static Client> {
    if let Some(result) = CLIENT.get() {
        return result
            .as_ref()
            .ok()
            .filter(|client| client.pid == current_pid());
    }
    if INITIALIZING
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .is_err()
    {
        return None;
    }
    let result = load_client();
    let _ = CLIENT.set(result);
    INITIALIZING.store(false, Ordering::Release);
    CLIENT
        .get()
        .and_then(|result| result.as_ref().ok())
        .filter(|client| client.pid == current_pid())
}

fn current_pid() -> u64 {
    unsafe { libc::syscall(libc::SYS_getpid) as u64 }
}

fn record_hook(index: u32) {
    let Some(client) = client() else {
        return;
    };
    if client
        .pod
        .state()
        .control
        .start_flag
        .load(Ordering::Acquire)
        == 0
    {
        return;
    }
    // See load_client: only the built-in, reviewed pod image is trusted here.
    if unsafe { client.pod.add(client.mode, index, 1) }.is_err() {
        client
            .pod
            .state()
            .control
            .failure_count
            .fetch_add(1, Ordering::Relaxed);
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn pod_preload_barrier(timeout_ms: u64) -> i32 {
    let Some(client) = client() else {
        return -1;
    };
    let started = Instant::now();
    loop {
        let ready = client
            .pod
            .state()
            .control
            .ready_count
            .load(Ordering::Acquire);
        if ready == client.expected_processes {
            client
                .pod
                .state()
                .control
                .start_flag
                .store(1, Ordering::Release);
            return STATUS_OK;
        }
        if ready > client.expected_processes {
            return -2;
        }
        if started.elapsed() >= Duration::from_millis(timeout_ms) {
            return -3;
        }
        if started.elapsed() < Duration::from_millis(2) {
            std::hint::spin_loop();
        } else {
            std::thread::sleep(Duration::from_micros(100));
        }
    }
}

unsafe fn raw_credential_call(system_call: libc::c_long, index: u32) -> libc::c_uint {
    let result = unsafe { libc::syscall(system_call) as libc::c_uint };
    let errno = unsafe { libc::__errno_location() };
    let saved_errno = unsafe { errno.read() };
    record_hook(index);
    unsafe {
        errno.write(saved_errno);
    }
    result
}

#[unsafe(no_mangle)]
/// Interposes libc `getuid`.
///
/// # Safety
///
/// This function has the same calling contract as libc `getuid`.
pub unsafe extern "C" fn getuid() -> libc::uid_t {
    unsafe { raw_credential_call(libc::SYS_getuid, 0) as libc::uid_t }
}

#[unsafe(no_mangle)]
/// Interposes libc `geteuid`.
///
/// # Safety
///
/// This function has the same calling contract as libc `geteuid`.
pub unsafe extern "C" fn geteuid() -> libc::uid_t {
    unsafe { raw_credential_call(libc::SYS_geteuid, 1) as libc::uid_t }
}

#[unsafe(no_mangle)]
/// Interposes libc `getgid`.
///
/// # Safety
///
/// This function has the same calling contract as libc `getgid`.
pub unsafe extern "C" fn getgid() -> libc::gid_t {
    unsafe { raw_credential_call(libc::SYS_getgid, 2) as libc::gid_t }
}

#[unsafe(no_mangle)]
/// Interposes libc `getegid`.
///
/// # Safety
///
/// This function has the same calling contract as libc `getegid`.
pub unsafe extern "C" fn getegid() -> libc::gid_t {
    unsafe { raw_credential_call(libc::SYS_getegid, 3) as libc::gid_t }
}
