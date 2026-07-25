use pod_v2_api::FLAG_REQUIRES_SAME_VA;
use pod_v2_runtime::{PodArtifact, PodImage, PodState};
use std::env;
use std::error::Error;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

const CODE_BASE: usize = 0x3000_0000_0000;
const STATE_BASE: usize = 0x4000_0000_0000;
const ADDRESS_STRIDE: usize = 0x0000_1000_0000;

#[derive(Debug)]
struct ParentOptions {
    image: PathBuf,
    sha256: String,
    workers: usize,
    threads: usize,
    iterations: u64,
}

#[derive(Debug)]
struct WorkerOptions {
    image: PathBuf,
    sha256: String,
    code_fd: RawFd,
    state_fd: RawFd,
    worker_id: usize,
    threads: usize,
    iterations: u64,
    code_address: usize,
    state_address: usize,
}

enum Mode {
    Parent(ParentOptions),
    Worker(WorkerOptions),
}

fn main() {
    if let Err(error) = run() {
        eprintln!("pod-v2-host: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    match parse_options()? {
        Mode::Parent(options) => run_parent(options),
        Mode::Worker(options) => run_worker(options),
    }
}

fn run_parent(mut options: ParentOptions) -> Result<(), Box<dyn Error>> {
    if options.workers == 0 || options.threads == 0 || options.iterations == 0 {
        return Err("workers, threads, and iterations must all be nonzero".into());
    }
    options.image = options.image.canonicalize()?;
    let artifact = PodArtifact::open(&options.image, &options.sha256)?;
    let fixed_state = artifact.header().flags & FLAG_REQUIRES_SAME_VA != 0;
    let parent_state_address = if fixed_state {
        artifact.header().required_state_address as usize
    } else {
        STATE_BASE
    };
    let image = unsafe { PodImage::create_trusted(&artifact, Some(CODE_BASE))? };
    let state = image.create_state(Some(parent_state_address))?;
    image.verify_runtime_permissions(&state)?;

    let executable = env::current_exe()?;
    let mut children = Vec::with_capacity(options.workers);
    for worker_id in 0..options.workers {
        let code_fd = image.duplicate_code_fd_for_exec()?;
        let state_fd = state.duplicate_fd_for_exec()?;
        let address_index = worker_id + 1;
        let code_address = checked_address(CODE_BASE, address_index)?;
        let state_address = if fixed_state {
            parent_state_address
        } else {
            checked_address(STATE_BASE, address_index)?
        };
        let child = Command::new(&executable)
            .arg("--worker-mode")
            .arg("--image")
            .arg(&options.image)
            .arg("--sha256")
            .arg(&options.sha256)
            .arg("--code-fd")
            .arg(code_fd.as_raw_fd().to_string())
            .arg("--state-fd")
            .arg(state_fd.as_raw_fd().to_string())
            .arg("--worker-id")
            .arg(worker_id.to_string())
            .arg("--threads")
            .arg(options.threads.to_string())
            .arg("--iterations")
            .arg(options.iterations.to_string())
            .arg("--code-address")
            .arg(format!("0x{code_address:x}"))
            .arg("--state-address")
            .arg(format!("0x{state_address:x}"))
            .spawn()?;
        children.push(child);
    }

    if let Err(error) = wait_until_ready(&state, &mut children, options.workers) {
        terminate(&mut children);
        return Err(error);
    }
    state.start();

    let mut failed = Vec::new();
    for (worker_id, child) in children.iter_mut().enumerate() {
        let status = child.wait()?;
        if !status.success() {
            failed.push(format!("worker {worker_id}: {status}"));
        }
    }
    if !failed.is_empty() {
        return Err(format!("workers failed: {}", failed.join(", ")).into());
    }

    image.validate(&state)?;
    let expected_shared = (options.workers as u64)
        .checked_mul(options.threads as u64)
        .and_then(|value| value.checked_mul(options.iterations))
        .ok_or("expected counter overflow")?;
    let actual_shared = image.get(&state, 0)?.ok_or("shared key is absent")?;
    if actual_shared != expected_shared {
        return Err(format!(
            "shared counter mismatch: expected {expected_shared}, got {actual_shared}"
        )
        .into());
    }
    for worker_id in 0..options.workers {
        for thread_id in 0..options.threads {
            let key = unique_key(worker_id, thread_id, options.threads)?;
            let actual = image.get(&state, key)?.ok_or("unique key is absent")?;
            if actual != options.iterations {
                return Err(format!(
                    "key {key} mismatch: expected {}, got {actual}",
                    options.iterations
                )
                .into());
            }
        }
    }
    let expected_len = options
        .workers
        .checked_mul(options.threads)
        .and_then(|value| value.checked_add(1))
        .ok_or("expected table length overflow")? as u64;
    let len = image.len(&state)?;
    if len != expected_len {
        return Err(format!("table length mismatch: expected {expected_len}, got {len}").into());
    }

    println!(
        "v2-ok artifact={} processes={} threads_per_process={} iterations={} calls={} entries={} capacity={} allocated={} layout=size:{}/align:{}/hash:{:016x} code_va=0x{:x} state_va=0x{:x} payload_va=0x{:x}",
        artifact.digest_hex(),
        options.workers + 1,
        options.threads,
        options.iterations,
        expected_shared * 2,
        len,
        image.capacity(&state)?,
        image.allocated(&state)?,
        image.layout_size(),
        image.layout_align(),
        image.layout_hash(),
        image.code_address(),
        state.state_address(),
        state.payload_address(),
    );
    Ok(())
}

fn run_worker(options: WorkerOptions) -> Result<(), Box<dyn Error>> {
    if options.code_fd < 0 || options.state_fd < 0 {
        return Err("worker received a negative fd".into());
    }
    let artifact = PodArtifact::open(&options.image, &options.sha256)?;
    let code_fd = unsafe { OwnedFd::from_raw_fd(options.code_fd) };
    let state_fd = unsafe { OwnedFd::from_raw_fd(options.state_fd) };
    let image = Arc::new(unsafe {
        PodImage::attach_trusted(&artifact, code_fd, Some(options.code_address))?
    });
    let state = Arc::new(image.attach_state(state_fd, Some(options.state_address))?);
    image.verify_runtime_permissions(&state)?;
    state.announce_ready();
    state.wait_for_start(Duration::from_secs(30))?;

    let mut threads = Vec::with_capacity(options.threads);
    for thread_id in 0..options.threads {
        let image = Arc::clone(&image);
        let state = Arc::clone(&state);
        let key = unique_key(options.worker_id, thread_id, options.threads)?;
        let iterations = options.iterations;
        threads.push(thread::spawn(move || -> Result<(), String> {
            for _ in 0..iterations {
                image
                    .upsert(&state, 0, 1)
                    .map_err(|error| error.to_string())?;
                image
                    .upsert(&state, key, 1)
                    .map_err(|error| error.to_string())?;
            }
            Ok(())
        }));
    }
    for handle in threads {
        handle
            .join()
            .map_err(|_| "worker thread panicked")?
            .map_err(|error| format!("worker thread failed: {error}"))?;
    }
    image.validate(&state)?;
    println!(
        "worker-ok id={} pid={} code_va=0x{:x} state_va=0x{:x} payload_va=0x{:x}",
        options.worker_id,
        std::process::id(),
        image.code_address(),
        state.state_address(),
        state.payload_address(),
    );
    Ok(())
}

fn wait_until_ready(
    state: &PodState,
    children: &mut [Child],
    expected: usize,
) -> Result<(), Box<dyn Error>> {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let ready = state.ready_count() as usize;
        if ready == expected {
            return Ok(());
        }
        if ready > expected {
            return Err(format!("ready count exceeded worker count: {ready} > {expected}").into());
        }
        for (worker_id, child) in children.iter_mut().enumerate() {
            if let Some(status) = child.try_wait()? {
                return Err(
                    format!("worker {worker_id} exited before the barrier with {status}").into(),
                );
            }
        }
        if Instant::now() >= deadline {
            return Err(format!("timed out with {ready}/{expected} workers ready").into());
        }
        thread::sleep(Duration::from_millis(1));
    }
}

fn terminate(children: &mut [Child]) {
    for child in children.iter_mut() {
        let _ = child.kill();
    }
    for child in children.iter_mut() {
        let _ = child.wait();
    }
}

fn unique_key(worker_id: usize, thread_id: usize, threads: usize) -> Result<u64, Box<dyn Error>> {
    let value = worker_id
        .checked_mul(threads)
        .and_then(|value| value.checked_add(thread_id))
        .and_then(|value| value.checked_add(1))
        .ok_or("unique key overflow")?;
    Ok(u64::try_from(value)?)
}

fn checked_address(base: usize, index: usize) -> Result<usize, Box<dyn Error>> {
    base.checked_add(
        index
            .checked_mul(ADDRESS_STRIDE)
            .ok_or("address stride overflow")?,
    )
    .ok_or_else(|| "mapping address overflow".into())
}

fn parse_options() -> Result<Mode, Box<dyn Error>> {
    let arguments: Vec<String> = env::args().skip(1).collect();
    let worker_mode = arguments.iter().any(|argument| argument == "--worker-mode");
    let value = |name: &str| -> Result<String, Box<dyn Error>> {
        let position = arguments
            .iter()
            .position(|argument| argument == name)
            .ok_or_else(|| format!("missing {name}"))?;
        arguments
            .get(position + 1)
            .cloned()
            .ok_or_else(|| format!("{name} requires a value").into())
    };
    if arguments
        .iter()
        .any(|argument| argument == "-h" || argument == "--help")
    {
        println!(
            "usage: pod-v2-host --image FILE --sha256 HEX [--workers N] [--threads N] [--iterations N]"
        );
        std::process::exit(0);
    }
    let image = PathBuf::from(value("--image")?);
    let sha256 = value("--sha256")?;
    if worker_mode {
        Ok(Mode::Worker(WorkerOptions {
            image,
            sha256,
            code_fd: value("--code-fd")?.parse()?,
            state_fd: value("--state-fd")?.parse()?,
            worker_id: value("--worker-id")?.parse()?,
            threads: value("--threads")?.parse()?,
            iterations: value("--iterations")?.parse()?,
            code_address: parse_usize(&value("--code-address")?)?,
            state_address: parse_usize(&value("--state-address")?)?,
        }))
    } else {
        Ok(Mode::Parent(ParentOptions {
            image,
            sha256,
            workers: optional(&arguments, "--workers")?.unwrap_or(4),
            threads: optional(&arguments, "--threads")?.unwrap_or(4),
            iterations: optional(&arguments, "--iterations")?.unwrap_or(2_000),
        }))
    }
}

fn optional<T: std::str::FromStr>(
    arguments: &[String],
    name: &str,
) -> Result<Option<T>, Box<dyn Error>>
where
    T::Err: Error + 'static,
{
    let Some(position) = arguments.iter().position(|argument| argument == name) else {
        return Ok(None);
    };
    let value = arguments
        .get(position + 1)
        .ok_or_else(|| format!("{name} requires a value"))?;
    Ok(Some(value.parse()?))
}

fn parse_usize(value: &str) -> Result<usize, Box<dyn Error>> {
    if let Some(value) = value.strip_prefix("0x") {
        Ok(usize::from_str_radix(value, 16)?)
    } else {
        Ok(value.parse()?)
    }
}
