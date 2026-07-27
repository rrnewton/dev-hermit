use shmem_pod_runtime::{PodArtifact, PodImage};
use std::env;
use std::error::Error;
use std::os::fd::AsRawFd;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

const CALL_KEY: u64 = 0x7072_656c_6f61_6401;
const ATTACH_KEY: u64 = 0x7072_656c_6f61_6402;

struct Options {
    image: PathBuf,
    sha256: String,
    shim: PathBuf,
    guest: PathBuf,
    depth: u32,
    fanout: u32,
    threads: u32,
    calls: u64,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("shmem-pod-preload-host: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut options = parse_options()?;
    if options.fanout == 0 || options.threads == 0 || options.calls == 0 {
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
    if code_fd.as_raw_fd() == state_fd.as_raw_fd() {
        return Err("runtime returned aliased inherited descriptors".into());
    }

    // Injection is scoped to the spawned guest. The host itself never loads
    // the shim, and every descendant naturally inherits this environment and
    // the two non-CLOEXEC memfd descriptors.
    let mut command = Command::new(&options.guest);
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
        .env("SHMEM_POD_PRELOAD_IMAGE", &options.image)
        .env("SHMEM_POD_PRELOAD_SHA256", &options.sha256)
        .env("SHMEM_POD_PRELOAD_CODE_FD", code_fd.as_raw_fd().to_string())
        .env(
            "SHMEM_POD_PRELOAD_STATE_FD",
            state_fd.as_raw_fd().to_string(),
        )
        .env("SHMEM_POD_PRELOAD_REQUIRED", "1")
        .process_group(0);
    let mut guest = command.spawn()?;
    let guest_group =
        libc::pid_t::try_from(guest.id()).map_err(|_| "guest PID does not fit pid_t")?;
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
    let process_count = process_count(options.depth, options.fanout)?;
    let expected_calls = process_count
        .checked_mul(
            (options.threads as u64)
                .checked_mul(options.calls)
                .and_then(|value| value.checked_add(1))
                .ok_or("expected call count overflow")?,
        )
        .ok_or("expected call count overflow")?;
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

    println!(
        "preload-ok artifact={} processes={} threads_per_process={} calls_per_thread={} intercepted_calls={} attachments={} code_va=0x{:x} state_va=0x{:x}",
        artifact.digest_hex(),
        process_count,
        options.threads,
        options.calls,
        actual_calls,
        actual_attachments,
        image.code_address(),
        state.state_address(),
    );
    Ok(())
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
    let mut args = env::args_os().skip(1);
    while let Some(argument) = args.next() {
        let argument = argument
            .into_string()
            .map_err(|_| "arguments must be valid UTF-8")?;
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
            "-h" | "--help" => {
                println!(
                    "usage: shmem-pod-preload-host --image FILE --sha256 HEX --shim FILE --guest FILE [--depth N] [--fanout N] [--threads N] [--calls N]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    Ok(Options {
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
