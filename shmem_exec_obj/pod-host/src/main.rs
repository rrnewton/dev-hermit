use pod_api::{COUNTER_COUNT, MAX_CONNECTIONS, PodMode, STATUS_BAD_INDEX};
use pod_loader::{MappedPod, create_instance};
use std::collections::HashSet;
use std::env;
use std::error::Error;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::Ordering;
use std::time::Instant;

struct Options {
    image: PathBuf,
    instance: PathBuf,
    preload: PathBuf,
    guest: PathBuf,
    mode: PodMode,
    depth: u32,
    fanout: u32,
    threads: u32,
    iterations: u64,
    barrier_timeout_ms: u64,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("pod-host: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    let process_count = tree_process_count(options.depth, options.fanout)?;
    if process_count > MAX_CONNECTIONS as u64 {
        return Err(format!(
            "process tree has {process_count} members but the state holds {MAX_CONNECTIONS}"
        )
        .into());
    }
    let expected_per_counter = process_count
        .checked_mul(options.threads as u64)
        .and_then(|value| value.checked_mul(options.iterations))
        .ok_or("expected counter total overflow")?;

    create_instance(&options.image, &options.instance)?;
    let probe = MappedPod::open(&options.instance)?;
    let (code_permissions, state_permissions) = probe.mapping_permissions()?;
    if code_permissions != "r-xs" || state_permissions != "rw-s" {
        return Err(format!(
            "unexpected mappings: code={code_permissions:?}, state={state_permissions:?}"
        )
        .into());
    }
    assert_rx_mapping_rejects_writes(&probe)?;
    let bounds_error = probe
        .add(options.mode, COUNTER_COUNT as u32, 1)
        .expect_err("out-of-bounds pod call unexpectedly succeeded");
    if bounds_error.status() != Some(STATUS_BAD_INDEX) {
        return Err(format!("unexpected bounds status: {bounds_error}").into());
    }
    drop(probe);

    let instance = options.instance.canonicalize()?;
    let preload = options.preload.canonicalize()?;
    let guest = options.guest.canonicalize()?;
    let started = Instant::now();
    let status = Command::new(&guest)
        .args([
            "--depth",
            &options.depth.to_string(),
            "--fanout",
            &options.fanout.to_string(),
            "--threads",
            &options.threads.to_string(),
            "--iterations",
            &options.iterations.to_string(),
            "--barrier-timeout-ms",
            &options.barrier_timeout_ms.to_string(),
        ])
        .env("LD_PRELOAD", &preload)
        .env("POD_INSTANCE", &instance)
        .env("POD_MODE", options.mode.as_str())
        .env("POD_EXPECTED_PROCESSES", process_count.to_string())
        .status()?;
    let elapsed = started.elapsed();
    if !status.success() {
        return Err(format!("guest process tree exited with {status}").into());
    }

    let pod = MappedPod::open(&instance)?;
    verify_state(&pod, options.mode, process_count, expected_per_counter)?;
    println!(
        "PASS mode={} processes={} threads/process={} calls/counter={} code_mappings={} state_mappings={} code_perms={} state_perms={} elapsed_ms={}",
        options.mode.as_str(),
        process_count,
        options.threads,
        expected_per_counter,
        process_count,
        process_count,
        code_permissions,
        state_permissions,
        elapsed.as_millis(),
    );
    Ok(())
}

fn verify_state(
    pod: &MappedPod,
    mode: PodMode,
    process_count: u64,
    expected_per_counter: u64,
) -> Result<(), Box<dyn Error>> {
    let state = pod.state();
    let connections = state.control.connection_count.load(Ordering::Acquire);
    let ready = state.control.ready_count.load(Ordering::Acquire);
    let failures = state.control.failure_count.load(Ordering::Acquire);
    let started = state.control.start_flag.load(Ordering::Acquire);
    if connections != process_count || ready != process_count || failures != 0 || started != 1 {
        return Err(format!(
            "control mismatch: connections={connections}, ready={ready}, failures={failures}, start={started}"
        )
        .into());
    }

    for candidate_mode in PodMode::ALL {
        for index in 0..COUNTER_COUNT {
            let value = unsafe { state.counter_after_quiescence(candidate_mode, index) }
                .ok_or("counter index unexpectedly absent")?;
            let expected = if candidate_mode == mode {
                expected_per_counter
            } else {
                0
            };
            if value != expected {
                return Err(format!(
                    "{} counter {index} is {value}, expected {expected}",
                    candidate_mode.as_str()
                )
                .into());
            }
        }
    }

    let mut pids = HashSet::new();
    let mut code_bases = HashSet::new();
    let mut state_bases = HashSet::new();
    for record in &state.connections[..process_count as usize] {
        if record.ready.load(Ordering::Acquire) != 1
            || record.mode.load(Ordering::Relaxed) != mode as u32
        {
            return Err("connection record was not published with the expected mode".into());
        }
        pids.insert(record.pid.load(Ordering::Relaxed));
        code_bases.insert(record.code_base.load(Ordering::Relaxed));
        state_bases.insert(record.state_base.load(Ordering::Relaxed));
    }
    if pids.len() as u64 != process_count
        || code_bases.len() as u64 != process_count
        || state_bases.len() as u64 != process_count
    {
        return Err(format!(
            "mappings were not independent: pids={}, code_bases={}, state_bases={}, expected={process_count}",
            pids.len(),
            code_bases.len(),
            state_bases.len()
        )
        .into());
    }
    Ok(())
}

fn assert_rx_mapping_rejects_writes(pod: &MappedPod) -> Result<(), Box<dyn Error>> {
    let child = unsafe { libc::fork() };
    if child < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    if child == 0 {
        let limit = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        unsafe {
            libc::setrlimit(libc::RLIMIT_CORE, &limit);
            (pod.code_base() as *mut u8).write_volatile(0xcc);
            libc::_exit(0);
        }
    }

    let mut status = 0;
    if unsafe { libc::waitpid(child, &mut status, 0) } != child {
        return Err(std::io::Error::last_os_error().into());
    }
    if !libc::WIFSIGNALED(status) || !matches!(libc::WTERMSIG(status), libc::SIGSEGV | libc::SIGBUS)
    {
        return Err(format!(
            "writing the RX code mapping did not fault as expected (wait status=0x{status:x})"
        )
        .into());
    }
    Ok(())
}

fn tree_process_count(depth: u32, fanout: u32) -> Result<u64, Box<dyn Error>> {
    let mut level = 1_u64;
    let mut total = 1_u64;
    for _ in 0..depth {
        level = level
            .checked_mul(fanout as u64)
            .ok_or("process count overflow")?;
        total = total.checked_add(level).ok_or("process count overflow")?;
    }
    Ok(total)
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut image = None;
    let mut instance = None;
    let mut preload = None;
    let mut guest = None;
    let mut mode = None;
    let mut depth = 2;
    let mut fanout = 2;
    let mut threads = 2;
    let mut iterations = 10_000;
    let mut barrier_timeout_ms = 30_000;
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| format!("{argument} requires a value"))?;
        match argument.as_str() {
            "--image" => image = Some(value.into()),
            "--instance" => instance = Some(value.into()),
            "--preload" => preload = Some(value.into()),
            "--guest" => guest = Some(value.into()),
            "--mode" => {
                mode = Some(
                    PodMode::parse(&value)
                        .ok_or_else(|| format!("mode {value:?} is not supported"))?,
                )
            }
            "--depth" => depth = value.parse()?,
            "--fanout" => fanout = value.parse()?,
            "--threads" => threads = value.parse()?,
            "--iterations" => iterations = value.parse()?,
            "--barrier-timeout-ms" => barrier_timeout_ms = value.parse()?,
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    if threads == 0 || iterations == 0 {
        return Err("threads and iterations must both be nonzero".into());
    }
    Ok(Options {
        image: image.ok_or("--image is required")?,
        instance: instance.ok_or("--instance is required")?,
        preload: preload.ok_or("--preload is required")?,
        guest: guest.ok_or("--guest is required")?,
        mode: mode.ok_or("--mode is required")?,
        depth,
        fanout,
        threads,
        iterations,
        barrier_timeout_ms,
    })
}
