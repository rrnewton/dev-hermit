use shmem_pod::injection::{BOOTSTRAP_FD_ENV, BootstrapContext, BootstrapFlags, ConnectorKind};
use shmem_pod_runtime::{PodArtifact, PodImage};
use std::env;
use std::error::Error;
use std::ffi::CString;
use std::fs;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod ptrace;

const CALL_KEY: u64 = 0x7072_656c_6f61_6401;
const ATTACH_KEY: u64 = 0x7072_656c_6f61_6402;
const MFD_NOEXEC_SEAL: libc::c_uint = 0x0008;
const MFD_EXEC: libc::c_uint = 0x0010;
const F_SEAL_FUTURE_WRITE: libc::c_int = 0x0010;
const F_SEAL_EXEC: libc::c_int = 0x0020;
const PTRACE_READY_ENV: &str = "INJECTION_FIXTURE_READY_FD";
const PTRACE_RESUME_ENV: &str = "INJECTION_FIXTURE_RESUME_FD";

struct Options {
    mode: Mode,
    fault: Option<Fault>,
    image: PathBuf,
    sha256: String,
    shim: PathBuf,
    guest: PathBuf,
    depth: u32,
    fanout: u32,
    threads: u32,
    calls: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Mode {
    Preload,
    Ptrace,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Fault {
    BadContextDigest,
    UnsealedArtifact,
    ShortCode,
    BadCodeBytes,
    ReadOnlyState,
    WriteSealedState,
    FutureWriteSealedState,
    BadApiFingerprint,
    BadStateGeneration,
    FixedCodeCollision,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("shmem-pod-preload-host: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut options = parse_options()?;
    if options.mode == Mode::Preload
        && (options.fanout == 0 || options.threads == 0 || options.calls == 0)
    {
        return Err("fanout, threads, and calls must all be nonzero".into());
    }
    options.image = options.image.canonicalize()?;
    options.shim = options.shim.canonicalize()?;
    options.guest = options.guest.canonicalize()?;

    let artifact = PodArtifact::open(&options.image, &options.sha256)?;
    let image = unsafe { PodImage::create_trusted(&artifact, None)? };
    let state = image.create_state(None)?;
    image.verify_runtime_permissions(&state)?;

    let code_fd = image.duplicate_code_fd_for_exec()?;
    let state_fd = state.duplicate_fd_for_exec()?;
    let artifact_fd = artifact.duplicate_artifact_fd_for_exec()?;
    let fault_artifact_fd = if options.fault == Some(Fault::UnsealedArtifact) {
        Some(create_unsealed_artifact_fd(&options.image)?)
    } else {
        None
    };
    let fault_code_fd = match options.fault {
        Some(Fault::ShortCode) => Some(create_short_code_fd()?),
        Some(Fault::BadCodeBytes) => Some(create_bad_code_fd(
            &options.image,
            artifact.header().code_offset,
            artifact.header().code_len,
        )?),
        _ => None,
    };
    let fault_state_fd = match options.fault {
        Some(Fault::ReadOnlyState) => Some(reopen_read_only_for_exec(state_fd.as_raw_fd())?),
        Some(Fault::WriteSealedState) => Some(create_write_sealed_state_fd(
            state_fd.as_raw_fd(),
            image.state_file_len(),
            libc::F_SEAL_WRITE,
            "write-sealed",
        )?),
        Some(Fault::FutureWriteSealedState) => Some(create_write_sealed_state_fd(
            state_fd.as_raw_fd(),
            image.state_file_len(),
            F_SEAL_FUTURE_WRITE,
            "future-write-sealed",
        )?),
        _ => None,
    };
    let flags = BootstrapFlags::REQUIRED.union(BootstrapFlags::INHERIT_ACROSS_EXEC);
    let mut context = BootstrapContext::new(
        match options.mode {
            Mode::Preload => ConnectorKind::Preload,
            Mode::Ptrace => ConnectorKind::Ptrace,
        },
        flags,
        fault_artifact_fd
            .as_ref()
            .unwrap_or(&artifact_fd)
            .as_raw_fd(),
        fault_code_fd.as_ref().unwrap_or(&code_fd).as_raw_fd(),
        fault_state_fd.as_ref().unwrap_or(&state_fd).as_raw_fd(),
        artifact.len() as u64,
        image.state_file_len(),
        state.generation(),
        image.api_fingerprint(),
        artifact.digest(),
        random_nonce()?,
    )?;
    if options.fault == Some(Fault::BadContextDigest) {
        context.artifact_sha256[0] ^= 0xff;
        if context.artifact_sha256.iter().all(|byte| *byte == 0) {
            context.artifact_sha256[0] = 1;
        }
        context.validate()?;
    }
    if options.fault == Some(Fault::BadStateGeneration) {
        context.generation = context.generation.checked_add(1).unwrap_or(1);
        context.validate()?;
    }
    if options.fault == Some(Fault::BadApiFingerprint) {
        context.api_fingerprint[0] ^= 0xff;
        if context.api_fingerprint.iter().all(|byte| *byte == 0) {
            context.api_fingerprint[0] = 1;
        }
        context.validate()?;
    }
    if options.mode == Mode::Preload && options.fault == Some(Fault::FixedCodeCollision) {
        return Err("fixed-code-collision is a ptrace-only fault probe".into());
    }
    let mut command = Command::new(&options.guest);
    let ptrace_fixture = if options.mode == Mode::Ptrace {
        let fixture = PtraceFixture::new()?;
        fixture.configure(&mut command);
        Some(fixture)
    } else {
        None
    };
    let bootstrap_fd = if options.mode == Mode::Preload {
        let bootstrap_fd = create_bootstrap_fd(&context)?;
        command
            .arg("--depth")
            .arg(options.depth.to_string())
            .arg("--fanout")
            .arg(options.fanout.to_string())
            .arg("--threads")
            .arg(options.threads.to_string())
            .arg("--calls")
            .arg(options.calls.to_string())
            .env("LD_PRELOAD", &options.shim)
            .env(BOOTSTRAP_FD_ENV, bootstrap_fd.as_raw_fd().to_string());
        Some(bootstrap_fd)
    } else {
        None
    };
    command.process_group(0);
    let mut guest = command.spawn()?;
    let ptrace_fixture = ptrace_fixture.map(PtraceFixture::into_parent);
    // Keep the descriptor live until every recursively executed preload guest
    // exits. The ptrace connector passes the context directly instead.
    let _bootstrap_fd = bootstrap_fd;
    let guest_group =
        libc::pid_t::try_from(guest.id()).map_err(|_| "guest PID does not fit pid_t")?;

    if options.mode == Mode::Ptrace {
        #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
        {
            let Some(fixture) = ptrace_fixture.as_ref() else {
                return Err(cleanup_spawned_guest(
                    &mut guest,
                    guest_group,
                    "ptrace fixture descriptors were not configured".into(),
                ));
            };
            if let Err(error) = fixture.wait_ready() {
                return Err(cleanup_spawned_guest(&mut guest, guest_group, error));
            }
            if let Err(error) = ptrace::inject(
                guest_group,
                &options.shim,
                &context,
                options.fault == Some(Fault::FixedCodeCollision),
            ) {
                return Err(cleanup_spawned_guest(&mut guest, guest_group, error));
            }
            if let Err(error) = fixture.resume() {
                return Err(cleanup_spawned_guest(&mut guest, guest_group, error));
            }
        }
        #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
        {
            return Err(cleanup_spawned_guest(
                &mut guest,
                guest_group,
                "ptrace demo supports Linux x86-64 only".into(),
            ));
        }
    }
    let status = guest.wait()?;
    if !status.success() {
        // The guest is a process-group leader and every recursive Command
        // remains in that group. If a required hook calls _exit from one
        // process, terminate any descendants which could otherwise outlive the
        // host and retain the shared state.
        let kill_result = unsafe { libc::kill(-guest_group, libc::SIGKILL) };
        if kill_result != 0 {
            let cleanup_error = std::io::Error::last_os_error();
            if cleanup_error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!(
                    "guest tree failed with {status}; process-group cleanup failed: {cleanup_error}"
                )
                .into());
            }
        }
        return Err(format!("guest tree failed with {status}").into());
    }

    image.validate(&state)?;
    image.verify_runtime_permissions(&state)?;
    let process_count = if options.mode == Mode::Preload {
        process_count(options.depth, options.fanout)?
    } else {
        1
    };
    let expected_calls = if options.mode == Mode::Preload {
        process_count
            .checked_mul(
                (options.threads as u64)
                    .checked_mul(options.calls)
                    .and_then(|value| value.checked_add(1))
                    .ok_or("expected call count overflow")?,
            )
            .ok_or("expected call count overflow")?
    } else {
        1
    };
    let actual_calls = image.get(&state, CALL_KEY)?.unwrap_or(0);
    let actual_attachments = image.get(&state, ATTACH_KEY)?.unwrap_or(0);
    if actual_calls != expected_calls {
        return Err(
            format!("hook count mismatch: expected {expected_calls}, got {actual_calls}").into(),
        );
    }
    if actual_attachments != process_count {
        return Err(format!(
            "attachment count mismatch: expected {process_count}, got {actual_attachments}"
        )
        .into());
    }
    if image.len(&state)? != 2 {
        return Err("preload demo created an unexpected table entry".into());
    }

    match options.mode {
        Mode::Preload => println!(
            "preload-ok artifact={} processes={} threads_per_process={} calls_per_thread={} intercepted_calls={} attachments={} code_va=0x{:x} state_va=0x{:x}",
            artifact.digest_hex(),
            process_count,
            options.threads,
            options.calls,
            actual_calls,
            actual_attachments,
            image.code_address(),
            state.state_address(),
        ),
        Mode::Ptrace => println!(
            "ptrace-ok artifact={} target_pid={} bootstrap_calls={} attachments={} injector_detached=true code_va=0x{:x} state_va=0x{:x}",
            artifact.digest_hex(),
            guest_group,
            actual_calls,
            actual_attachments,
            image.code_address(),
            state.state_address(),
        ),
    }
    Ok(())
}

fn create_bootstrap_fd(context: &BootstrapContext) -> Result<OwnedFd, Box<dyn Error>> {
    let name = CString::new("shmem-pod-bootstrap")?;
    let fd = unsafe {
        libc::memfd_create(
            name.as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_NOEXEC_SEAL,
        )
    };
    if fd < 0 {
        return Err(format!(
            "memfd_create bootstrap: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let encoded = context.encode();
    if unsafe { libc::ftruncate(fd.as_raw_fd(), encoded.len() as libc::off_t) } != 0 {
        return Err(format!("ftruncate bootstrap: {}", std::io::Error::last_os_error()).into());
    }
    write_all_at(fd.as_raw_fd(), &encoded)?;
    let seals = libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL;
    if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_ADD_SEALS, seals) } != 0 {
        return Err(format!("seal bootstrap: {}", std::io::Error::last_os_error()).into());
    }
    let actual = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GET_SEALS) };
    if actual < 0 || actual & (seals | F_SEAL_EXEC) != seals | F_SEAL_EXEC {
        return Err("bootstrap memfd lacks required immutable/no-exec seals".into());
    }
    let inherited = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_DUPFD, 3) };
    if inherited < 0 {
        return Err(format!(
            "duplicate bootstrap FD: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(inherited) })
}

fn create_unsealed_artifact_fd(path: &PathBuf) -> Result<OwnedFd, Box<dyn Error>> {
    let bytes = fs::read(path)?;
    let name = CString::new("shmem-pod-unsealed-artifact-probe")?;
    let fd = unsafe {
        libc::memfd_create(
            name.as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_NOEXEC_SEAL,
        )
    };
    if fd < 0 {
        return Err(format!(
            "memfd_create unsealed artifact probe: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    if unsafe { libc::ftruncate(fd.as_raw_fd(), bytes.len() as libc::off_t) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    write_all_at(fd.as_raw_fd(), &bytes)?;
    duplicate_for_exec(fd.as_raw_fd(), "unsealed artifact probe")
}

fn create_short_code_fd() -> Result<OwnedFd, Box<dyn Error>> {
    let name = CString::new("shmem-pod-short-code-probe")?;
    let fd = unsafe {
        libc::memfd_create(
            name.as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_EXEC,
        )
    };
    if fd < 0 {
        return Err(format!(
            "memfd_create short code probe: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    if unsafe { libc::ftruncate(fd.as_raw_fd(), 1) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let seals = libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL;
    if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_ADD_SEALS, seals) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    duplicate_for_exec(fd.as_raw_fd(), "short code probe")
}

fn create_bad_code_fd(
    path: &PathBuf,
    code_offset: u64,
    code_len: u64,
) -> Result<OwnedFd, Box<dyn Error>> {
    let artifact = fs::read(path)?;
    let offset = usize::try_from(code_offset)?;
    let length = usize::try_from(code_len)?;
    let end = offset.checked_add(length).ok_or("code extent overflow")?;
    let mut code = artifact
        .get(offset..end)
        .ok_or("code extent outside artifact")?
        .to_vec();
    code[0] ^= 0xff;
    let mapped_len = length
        .checked_add(4095)
        .map(|value| value & !4095)
        .ok_or("code page rounding overflow")?;
    let name = CString::new("shmem-pod-bad-code-probe")?;
    let fd = unsafe {
        libc::memfd_create(
            name.as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_EXEC,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    if unsafe { libc::ftruncate(fd.as_raw_fd(), mapped_len as libc::off_t) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    write_all_at(fd.as_raw_fd(), &code)?;
    let seals = libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL;
    if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_ADD_SEALS, seals) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    duplicate_for_exec(fd.as_raw_fd(), "bad code bytes probe")
}

fn reopen_read_only_for_exec(fd: RawFd) -> Result<OwnedFd, Box<dyn Error>> {
    let path = CString::new(format!("/proc/self/fd/{fd}"))?;
    let read_only = unsafe { libc::open(path.as_ptr(), libc::O_RDONLY | libc::O_CLOEXEC) };
    if read_only < 0 {
        return Err(format!(
            "reopen state descriptor read-only: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let read_only = unsafe { OwnedFd::from_raw_fd(read_only) };
    duplicate_for_exec(read_only.as_raw_fd(), "read-only state probe")
}

fn create_write_sealed_state_fd(
    source_fd: RawFd,
    state_len: u64,
    write_seal: libc::c_int,
    label: &str,
) -> Result<OwnedFd, Box<dyn Error>> {
    let length = usize::try_from(state_len)?;
    let mut bytes = vec![0_u8; length];
    read_exact_at(source_fd, &mut bytes)?;
    let name = CString::new(format!("shmem-pod-{label}-state-probe"))?;
    let fd = unsafe {
        libc::memfd_create(
            name.as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_NOEXEC_SEAL,
        )
    };
    if fd < 0 {
        return Err(format!(
            "memfd_create {label} state probe: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    if unsafe { libc::ftruncate(fd.as_raw_fd(), length as libc::off_t) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    write_all_at(fd.as_raw_fd(), &bytes)?;
    let seals = libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL | write_seal;
    if unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_ADD_SEALS, seals) } != 0 {
        return Err(format!(
            "seal {label} state probe: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    let actual = unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GET_SEALS) };
    let expected = seals | F_SEAL_EXEC;
    if actual < 0 || actual & expected != expected {
        return Err(format!("{label} state probe lacks requested seals").into());
    }
    duplicate_for_exec(fd.as_raw_fd(), label)
}

struct PtraceFixture {
    ready_read: OwnedFd,
    ready_child: OwnedFd,
    resume_child: OwnedFd,
    resume_write: OwnedFd,
}

struct PtraceParent {
    ready_read: OwnedFd,
    resume_write: OwnedFd,
}

impl PtraceFixture {
    fn new() -> Result<Self, Box<dyn Error>> {
        let (ready_read, ready_write) = cloexec_pipe()?;
        let (resume_read, resume_write) = cloexec_pipe()?;
        let ready_child = duplicate_for_exec(ready_write.as_raw_fd(), "fixture ready")?;
        let resume_child = duplicate_for_exec(resume_read.as_raw_fd(), "fixture resume")?;
        Ok(Self {
            ready_read,
            ready_child,
            resume_child,
            resume_write,
        })
    }

    fn configure(&self, command: &mut Command) {
        command
            .env(PTRACE_READY_ENV, self.ready_child.as_raw_fd().to_string())
            .env(PTRACE_RESUME_ENV, self.resume_child.as_raw_fd().to_string());
    }

    fn into_parent(self) -> PtraceParent {
        let Self {
            ready_read,
            ready_child,
            resume_child,
            resume_write,
        } = self;
        drop(ready_child);
        drop(resume_child);
        PtraceParent {
            ready_read,
            resume_write,
        }
    }
}

impl PtraceParent {
    fn wait_ready(&self) -> Result<(), Box<dyn Error>> {
        let mut descriptor = libc::pollfd {
            fd: self.ready_read.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        loop {
            let result = unsafe { libc::poll(&mut descriptor, 1, 10_000) };
            if result < 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(format!("poll ptrace fixture readiness: {error}").into());
            }
            if result == 0 {
                return Err("ptrace fixture did not reach its safe point within 10 seconds".into());
            }
            break;
        }
        let mut byte = 0_u8;
        let read = loop {
            let read = unsafe {
                libc::read(
                    self.ready_read.as_raw_fd(),
                    (&mut byte as *mut u8).cast(),
                    1,
                )
            };
            if read < 0 && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted
            {
                continue;
            }
            break read;
        };
        if read != 1 || byte != b'R' {
            return Err(format!(
                "ptrace fixture readiness protocol failed: read={read}, byte=0x{byte:02x}"
            )
            .into());
        }
        Ok(())
    }

    fn resume(&self) -> Result<(), Box<dyn Error>> {
        let byte = b'G';
        let written = loop {
            let written = unsafe {
                libc::write(
                    self.resume_write.as_raw_fd(),
                    (&byte as *const u8).cast(),
                    1,
                )
            };
            if written < 0
                && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted
            {
                continue;
            }
            break written;
        };
        if written != 1 {
            return Err(format!(
                "ptrace fixture resume protocol failed: {}",
                std::io::Error::last_os_error()
            )
            .into());
        }
        Ok(())
    }
}

fn cloexec_pipe() -> Result<(OwnedFd, OwnedFd), Box<dyn Error>> {
    let mut descriptors = [-1; 2];
    if unsafe { libc::pipe2(descriptors.as_mut_ptr(), libc::O_CLOEXEC) } != 0 {
        return Err(format!("pipe2: {}", std::io::Error::last_os_error()).into());
    }
    Ok(unsafe {
        (
            OwnedFd::from_raw_fd(descriptors[0]),
            OwnedFd::from_raw_fd(descriptors[1]),
        )
    })
}

fn duplicate_for_exec(fd: RawFd, label: &str) -> Result<OwnedFd, Box<dyn Error>> {
    let duplicate = unsafe { libc::fcntl(fd, libc::F_DUPFD, 3) };
    if duplicate < 0 {
        return Err(format!(
            "duplicate {label} descriptor: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(duplicate) })
}

fn read_exact_at(fd: RawFd, mut bytes: &mut [u8]) -> Result<(), Box<dyn Error>> {
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
            return Err(error.into());
        }
        if read == 0 {
            return Err("pread state probe ended early".into());
        }
        let read = read as usize;
        bytes = &mut bytes[read..];
        offset += read;
    }
    Ok(())
}

fn write_all_at(fd: RawFd, mut bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    let mut offset = 0;
    while !bytes.is_empty() {
        let written = unsafe {
            libc::pwrite(
                fd,
                bytes.as_ptr().cast(),
                bytes.len(),
                offset as libc::off_t,
            )
        };
        if written < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error.into());
        }
        if written == 0 {
            return Err("pwrite bootstrap made no progress".into());
        }
        let written = written as usize;
        bytes = &bytes[written..];
        offset += written;
    }
    Ok(())
}

fn cleanup_spawned_guest(
    guest: &mut Child,
    guest_group: libc::pid_t,
    cause: Box<dyn Error>,
) -> Box<dyn Error> {
    match kill_guest_group_and_reap(guest, guest_group) {
        Ok(()) => cause,
        Err(cleanup) => format!("{cause}; guest cleanup also failed: {cleanup}").into(),
    }
}

fn kill_guest_group_and_reap(guest: &mut Child, guest_group: libc::pid_t) -> Result<(), String> {
    let kill_failure = if unsafe { libc::kill(-guest_group, libc::SIGKILL) } == 0 {
        None
    } else {
        let error = std::io::Error::last_os_error();
        (error.raw_os_error() != Some(libc::ESRCH)).then(|| error.to_string())
    };
    let wait_failure = guest.wait().err().map(|error| error.to_string());
    match (kill_failure, wait_failure) {
        (None, None) => Ok(()),
        (Some(kill), None) => Err(format!("process-group kill failed: {kill}")),
        (None, Some(wait)) => Err(format!("child reap failed: {wait}")),
        (Some(kill), Some(wait)) => Err(format!(
            "process-group kill failed: {kill}; child reap failed: {wait}"
        )),
    }
}

fn random_nonce() -> Result<[u8; 16], Box<dyn Error>> {
    let mut nonce = [0_u8; 16];
    let mut filled = 0;
    while filled < nonce.len() {
        let result = unsafe {
            libc::getrandom(nonce[filled..].as_mut_ptr().cast(), nonce.len() - filled, 0)
        };
        if result < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error.into());
        }
        if result == 0 {
            return Err("getrandom made no progress".into());
        }
        filled += result as usize;
    }
    if nonce.iter().all(|byte| *byte == 0) {
        return Err("getrandom returned an all-zero nonce".into());
    }
    Ok(nonce)
}

fn process_count(depth: u32, fanout: u32) -> Result<u64, Box<dyn Error>> {
    let mut total = 0_u64;
    let mut level = 1_u64;
    for _ in 0..=depth {
        total = total.checked_add(level).ok_or("process count overflow")?;
        level = level
            .checked_mul(fanout as u64)
            .ok_or("process count overflow")?;
    }
    Ok(total)
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut image = None;
    let mut sha256 = None;
    let mut shim = None;
    let mut guest = None;
    let mut depth = 2;
    let mut fanout = 2;
    let mut threads = 2;
    let mut calls = 100;
    let mut mode = Mode::Preload;
    let mut fault = None;
    let mut args = env::args_os().skip(1);
    while let Some(argument) = args.next() {
        let argument = argument
            .into_string()
            .map_err(|_| "arguments must be valid UTF-8")?;
        if matches!(argument.as_str(), "-h" | "--help") {
            println!(
                "usage: shmem-pod-preload-host --image FILE --sha256 HEX --shim FILE --guest FILE [--mode preload|ptrace] [--depth N] [--fanout N] [--threads N] [--calls N] [--fault bad-context-digest|unsealed-artifact|short-code|bad-code-bytes|read-only-state|write-sealed-state|future-write-sealed-state|bad-api-fingerprint|bad-state-generation|fixed-code-collision]"
            );
            std::process::exit(0);
        }
        let value = args
            .next()
            .ok_or_else(|| format!("{argument} requires a value"))?;
        match argument.as_str() {
            "--image" => image = Some(PathBuf::from(value)),
            "--sha256" => sha256 = Some(value.into_string().map_err(|_| "SHA-256 must be UTF-8")?),
            "--shim" => shim = Some(PathBuf::from(value)),
            "--guest" => guest = Some(PathBuf::from(value)),
            "--depth" => depth = utf8_value(value, "--depth")?.parse()?,
            "--fanout" => fanout = utf8_value(value, "--fanout")?.parse()?,
            "--threads" => threads = utf8_value(value, "--threads")?.parse()?,
            "--calls" => calls = utf8_value(value, "--calls")?.parse()?,
            "--mode" => {
                mode = match utf8_value(value, "--mode")?.as_str() {
                    "preload" => Mode::Preload,
                    "ptrace" => Mode::Ptrace,
                    other => return Err(format!("unknown connector mode {other:?}").into()),
                }
            }
            "--fault" => {
                fault = Some(match utf8_value(value, "--fault")?.as_str() {
                    "bad-context-digest" => Fault::BadContextDigest,
                    "unsealed-artifact" => Fault::UnsealedArtifact,
                    "short-code" => Fault::ShortCode,
                    "bad-code-bytes" => Fault::BadCodeBytes,
                    "read-only-state" => Fault::ReadOnlyState,
                    "write-sealed-state" => Fault::WriteSealedState,
                    "future-write-sealed-state" => Fault::FutureWriteSealedState,
                    "bad-api-fingerprint" => Fault::BadApiFingerprint,
                    "bad-state-generation" => Fault::BadStateGeneration,
                    "fixed-code-collision" => Fault::FixedCodeCollision,
                    other => return Err(format!("unknown injected fault {other:?}").into()),
                })
            }
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    Ok(Options {
        mode,
        fault,
        image: image.ok_or("missing --image")?,
        sha256: sha256.ok_or("missing --sha256")?,
        shim: shim.ok_or("missing --shim")?,
        guest: guest.ok_or("missing --guest")?,
        depth,
        fanout,
        threads,
        calls,
    })
}

fn utf8_value(value: std::ffi::OsString, option: &str) -> Result<String, Box<dyn Error>> {
    value
        .into_string()
        .map_err(|_| format!("{option} must be valid UTF-8").into())
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    #[test]
    fn pre_ready_cleanup_kills_group_and_reaps_child() {
        let mut child = Command::new("/bin/sleep")
            .arg("60")
            .process_group(0)
            .spawn()
            .unwrap();
        let group = libc::pid_t::try_from(child.id()).unwrap();
        kill_guest_group_and_reap(&mut child, group).unwrap();
        assert!(child.try_wait().unwrap().is_some());
        assert_eq!(unsafe { libc::kill(-group, 0) }, -1);
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ESRCH)
        );
    }
}
