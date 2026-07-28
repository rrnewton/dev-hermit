//! Reproducible, dependency-light benchmark harness for shmem-pod.
//!
//! This binary is compiled by `scripts/run-benchmarks.sh` in a temporary Cargo
//! package. Keeping it outside the published crate avoids adding benchmark-only
//! dependencies or targets to the release package.

#![cfg(all(target_os = "linux", target_arch = "x86_64"))]

use shmem_pod::admission::CloseableSnzi;
use shmem_pod::collections::{SharedBox, SharedVec};
use shmem_pod::csnzi::{CloseOutcome, Csnzi, CsnziError};
use shmem_pod::reloc_allocator::{RelocAllocator, RelocRegion};
use shmem_pod::snzi::Snzi;
use shmem_pod::sync::{ProcessFutexMutex, ProcessSpinMutex};
use shmem_pod_runtime::{PodArtifact, PodImage};
use std::alloc::{Layout, alloc_zeroed, dealloc, handle_alloc_error};
use std::env;
use std::error::Error;
use std::ffi::{c_int, c_long, c_void};
use std::fs::{self, File};
use std::hint::{black_box, spin_loop};
use std::io::{self, BufWriter, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

const MAX_WORKERS: usize = 64;
const PRESENCE_NODES: usize = 84;
const PRESENCE_LEAVES: usize = 64;
const RELOC_SLOTS: usize = 64;
const RELOC_SLOT_SIZE: usize = 256;
const PAGE_SIZE: usize = 4096;
const SYS_GETTID: c_long = 186;
const PROT_READ: c_int = 0x1;
const PROT_WRITE: c_int = 0x2;
const MAP_SHARED: c_int = 0x01;
const MAP_ANONYMOUS: c_int = 0x20;
const SIGKILL: c_int = 9;

unsafe extern "C" {
    fn syscall(number: c_long, ...) -> c_long;
    fn mmap(
        address: *mut c_void,
        length: usize,
        protection: c_int,
        flags: c_int,
        fd: c_int,
        offset: isize,
    ) -> *mut c_void;
    fn munmap(address: *mut c_void, length: usize) -> c_int;
    fn fork() -> c_int;
    fn waitpid(pid: c_int, status: *mut c_int, options: c_int) -> c_int;
    fn kill(pid: c_int, signal: c_int) -> c_int;
    fn _exit(status: c_int) -> !;
}

#[derive(Debug)]
struct Config {
    artifact: PathBuf,
    artifact_sha256: String,
    output_dir: PathBuf,
    warmup: usize,
    iterations: usize,
    samples: usize,
    workers: usize,
    mode: String,
}

impl Config {
    fn parse() -> Result<Self, Box<dyn Error>> {
        let mut artifact = None;
        let mut artifact_sha256 = None;
        let mut output_dir = None;
        let mut warmup = None;
        let mut iterations = None;
        let mut samples = None;
        let mut workers = None;
        let mut mode = None;
        let mut arguments = env::args().skip(1);
        while let Some(argument) = arguments.next() {
            let value = arguments
                .next()
                .ok_or_else(|| format!("missing value after {argument}"))?;
            match argument.as_str() {
                "--artifact" => artifact = Some(PathBuf::from(value)),
                "--sha256" => artifact_sha256 = Some(value),
                "--output-dir" => output_dir = Some(PathBuf::from(value)),
                "--warmup" => warmup = Some(value.parse::<usize>()?),
                "--iterations" => iterations = Some(value.parse::<usize>()?),
                "--samples" => samples = Some(value.parse::<usize>()?),
                "--workers" => workers = Some(value.parse::<usize>()?),
                "--mode" => mode = Some(value),
                other => return Err(format!("unknown harness option {other}").into()),
            }
        }
        let config = Self {
            artifact: artifact.ok_or("--artifact is required")?,
            artifact_sha256: artifact_sha256.ok_or("--sha256 is required")?,
            output_dir: output_dir.ok_or("--output-dir is required")?,
            warmup: warmup.ok_or("--warmup is required")?,
            iterations: iterations.ok_or("--iterations is required")?,
            samples: samples.ok_or("--samples is required")?,
            workers: workers.ok_or("--workers is required")?,
            mode: mode.ok_or("--mode is required")?,
        };
        if config.iterations == 0 || config.samples == 0 || config.workers == 0 {
            return Err("iterations, samples, and workers must be nonzero".into());
        }
        if config.workers > MAX_WORKERS {
            return Err(format!("workers must not exceed {MAX_WORKERS}").into());
        }
        if config.artifact_sha256.len() != 64
            || !config
                .artifact_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err("--sha256 must be 64 hexadecimal digits".into());
        }
        Ok(config)
    }
}

struct ResultWriter {
    json: BufWriter<File>,
    csv: BufWriter<File>,
    run_id: String,
    rows: usize,
}

struct Measurement<'a> {
    category: &'a str,
    benchmark: &'a str,
    variant: &'a str,
    topology: &'a str,
    workers: usize,
    sample: usize,
    operations: u64,
    elapsed: Duration,
}

impl ResultWriter {
    fn create(output_dir: &Path, run_id: String) -> io::Result<Self> {
        fs::create_dir_all(output_dir)?;
        let json = BufWriter::new(File::create(output_dir.join("results.jsonl"))?);
        let mut csv = BufWriter::new(File::create(output_dir.join("results.csv"))?);
        writeln!(
            csv,
            "schema,run_id,category,benchmark,variant,topology,workers,sample,operations,elapsed_ns,operations_per_second,verified"
        )?;
        Ok(Self {
            json,
            csv,
            run_id,
            rows: 0,
        })
    }

    fn record(&mut self, measurement: Measurement<'_>) -> io::Result<()> {
        assert_ne!(measurement.operations, 0);
        let elapsed_ns = measurement.elapsed.as_nanos().max(1);
        let operations_per_second = u128::from(measurement.operations) * 1_000_000_000 / elapsed_ns;
        writeln!(
            self.json,
            "{{\"schema\":\"shmem-pod-benchmark-result-v1\",\"run_id\":\"{}\",\"category\":\"{}\",\"benchmark\":\"{}\",\"variant\":\"{}\",\"topology\":\"{}\",\"workers\":{},\"sample\":{},\"operations\":{},\"elapsed_ns\":{},\"operations_per_second\":{},\"verified\":true}}",
            json_escape(&self.run_id),
            measurement.category,
            measurement.benchmark,
            measurement.variant,
            measurement.topology,
            measurement.workers,
            measurement.sample,
            measurement.operations,
            elapsed_ns,
            operations_per_second,
        )?;
        writeln!(
            self.csv,
            "shmem-pod-benchmark-result-v1,{},{},{},{},{},{},{},{},{},{},true",
            self.run_id,
            measurement.category,
            measurement.benchmark,
            measurement.variant,
            measurement.topology,
            measurement.workers,
            measurement.sample,
            measurement.operations,
            elapsed_ns,
            operations_per_second,
        )?;
        self.rows += 1;
        Ok(())
    }

    fn finish(mut self) -> io::Result<usize> {
        self.json.flush()?;
        self.csv.flush()?;
        Ok(self.rows)
    }
}

fn json_escape(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character.is_control() => {
                use std::fmt::Write as _;
                write!(output, "\\u{:04x}", character as u32).unwrap();
            }
            character => output.push(character),
        }
    }
    output
}

fn environment(name: &str) -> String {
    env::var(name).unwrap_or_else(|_| "unknown".to_owned())
}

fn proc_field(path: &str, field: &str) -> String {
    fs::read_to_string(path)
        .ok()
        .and_then(|contents| {
            contents.lines().find_map(|line| {
                line.strip_prefix(field)
                    .map(|value| value.trim().to_owned())
            })
        })
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

struct CgroupMetadata {
    path: String,
    cpu_max: String,
    cpu_source: String,
    memory_max: String,
    memory_source: String,
    swap_max: String,
    swap_source: String,
    cpuset: String,
    cpuset_source: String,
}

fn cgroup_source(directory: &Path) -> String {
    directory
        .strip_prefix("/sys/fs/cgroup")
        .ok()
        .map(|path| format!("/{}", path.display()))
        .unwrap_or_else(|| "unknown".to_owned())
}

fn inherited_numeric_limit(ancestors: &[PathBuf], name: &str) -> (String, String) {
    let mut best: Option<(u128, String)> = None;
    for directory in ancestors {
        let Ok(contents) = fs::read_to_string(directory.join(name)) else {
            continue;
        };
        let value = contents.trim();
        let Ok(numeric) = value.parse::<u128>() else {
            continue;
        };
        if best.as_ref().is_none_or(|(limit, _)| numeric < *limit) {
            best = Some((numeric, cgroup_source(directory)));
        }
    }
    best.map_or_else(
        || ("max".to_owned(), "none".to_owned()),
        |(limit, source)| (limit.to_string(), source),
    )
}

fn inherited_cpu_limit(ancestors: &[PathBuf]) -> (String, String) {
    let mut best: Option<(u128, u128, String)> = None;
    for directory in ancestors {
        let Ok(contents) = fs::read_to_string(directory.join("cpu.max")) else {
            continue;
        };
        let mut fields = contents.split_whitespace();
        let Some(quota) = fields.next().and_then(|value| value.parse::<u128>().ok()) else {
            continue;
        };
        let Some(period) = fields.next().and_then(|value| value.parse::<u128>().ok()) else {
            continue;
        };
        if period == 0 || fields.next().is_some() {
            continue;
        }
        let tighter = best
            .as_ref()
            .is_none_or(|(best_quota, best_period, _)| quota * best_period < best_quota * period);
        if tighter {
            best = Some((quota, period, cgroup_source(directory)));
        }
    }
    best.map_or_else(
        || ("max".to_owned(), "none".to_owned()),
        |(quota, period, source)| (format!("{quota} {period}"), source),
    )
}

fn cgroup_metadata() -> CgroupMetadata {
    let path = fs::read_to_string("/proc/self/cgroup")
        .ok()
        .and_then(|contents| {
            contents
                .lines()
                .find_map(|line| line.strip_prefix("0::").map(str::to_owned))
        })
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned());
    if path == "unknown" {
        return CgroupMetadata {
            path,
            cpu_max: "unknown".to_owned(),
            cpu_source: "unknown".to_owned(),
            memory_max: "unknown".to_owned(),
            memory_source: "unknown".to_owned(),
            swap_max: "unknown".to_owned(),
            swap_source: "unknown".to_owned(),
            cpuset: "unknown".to_owned(),
            cpuset_source: "unknown".to_owned(),
        };
    }
    let root = PathBuf::from("/sys/fs/cgroup");
    let mut directory = root.join(path.trim_start_matches('/'));
    let mut ancestors = Vec::new();
    loop {
        ancestors.push(directory.clone());
        if directory == root || !directory.pop() {
            break;
        }
    }
    let (cpu_max, cpu_source) = inherited_cpu_limit(&ancestors);
    let (memory_max, memory_source) = inherited_numeric_limit(&ancestors, "memory.max");
    let (swap_max, swap_source) = inherited_numeric_limit(&ancestors, "memory.swap.max");
    let (cpuset, cpuset_source) = ancestors
        .iter()
        .find_map(|directory| {
            fs::read_to_string(directory.join("cpuset.cpus.effective"))
                .ok()
                .map(|value| value.trim().to_owned())
                .filter(|value| !value.is_empty())
                .map(|value| (value, cgroup_source(directory)))
        })
        .unwrap_or_else(|| ("unknown".to_owned(), "unknown".to_owned()));
    CgroupMetadata {
        path,
        cpu_max,
        cpu_source,
        memory_max,
        memory_source,
        swap_max,
        swap_source,
        cpuset,
        cpuset_source,
    }
}

fn write_environment(config: &Config, run_id: &str, result_rows: usize) -> io::Result<()> {
    let available = thread::available_parallelism().map_or(1, usize::from);
    let affinity = proc_field("/proc/self/status", "Cpus_allowed_list:");
    let cgroup = cgroup_metadata();
    let path = config.output_dir.join("environment.json");
    let mut output = BufWriter::new(File::create(path)?);
    writeln!(
        output,
        concat!(
            "{{\n",
            "  \"schema\": \"shmem-pod-benchmark-environment-v1\",\n",
            "  \"run_id\": \"{}\",\n",
            "  \"complete\": true,\n",
            "  \"result_rows\": {},\n",
            "  \"source_revision\": \"{}\",\n",
            "  \"source_dirty\": {},\n",
            "  \"cargo_lock_sha256\": \"{}\",\n",
            "  \"harness_lock_sha256\": \"{}\",\n",
            "  \"host\": {{\"hostname\": \"{}\", \"kernel\": \"{}\", \"cpu_model\": \"{}\", \"available_parallelism\": {}, \"os\": \"{}\", \"arch\": \"{}\"}},\n",
            "  \"execution_limits\": {{\"cpu_affinity_list\": \"{}\", \"cgroup_v2_path\": \"{}\", \"inherited_cpu_max\": \"{}\", \"inherited_cpu_max_source\": \"{}\", \"inherited_memory_max\": \"{}\", \"inherited_memory_max_source\": \"{}\", \"inherited_memory_swap_max\": \"{}\", \"inherited_memory_swap_max_source\": \"{}\", \"effective_cpuset\": \"{}\", \"effective_cpuset_source\": \"{}\"}},\n",
            "  \"toolchain\": {{\"rustc\": \"{}\", \"cargo\": \"{}\"}},\n",
            "  \"artifact\": {{\"path\": \"{}\", \"sha256\": \"{}\"}},\n",
            "  \"configuration\": {{\"mode\": \"{}\", \"profile\": \"release\", \"warmup_operations_per_worker\": {}, \"iterations_per_worker\": {}, \"samples\": {}, \"workers\": {}, \"timer\": \"std::time::Instant\"}},\n",
            "  \"interpretation\": \"One-host observations only; compare rows within a controlled run and do not treat them as portable performance claims.\"\n",
            "}}"
        ),
        json_escape(run_id),
        result_rows,
        json_escape(&environment("SHMEM_POD_BENCH_GIT_SHA")),
        environment("SHMEM_POD_BENCH_GIT_DIRTY") == "1",
        json_escape(&environment("SHMEM_POD_BENCH_LOCK_SHA256")),
        json_escape(&environment("SHMEM_POD_BENCH_HARNESS_LOCK_SHA256")),
        json_escape(&environment("SHMEM_POD_BENCH_HOSTNAME")),
        json_escape(&environment("SHMEM_POD_BENCH_KERNEL")),
        json_escape(&environment("SHMEM_POD_BENCH_CPU_MODEL")),
        available,
        env::consts::OS,
        env::consts::ARCH,
        json_escape(&affinity),
        json_escape(&cgroup.path),
        json_escape(&cgroup.cpu_max),
        json_escape(&cgroup.cpu_source),
        json_escape(&cgroup.memory_max),
        json_escape(&cgroup.memory_source),
        json_escape(&cgroup.swap_max),
        json_escape(&cgroup.swap_source),
        json_escape(&cgroup.cpuset),
        json_escape(&cgroup.cpuset_source),
        json_escape(&environment("SHMEM_POD_BENCH_RUSTC")),
        json_escape(&environment("SHMEM_POD_BENCH_CARGO")),
        json_escape(&config.artifact.display().to_string()),
        json_escape(&config.artifact_sha256),
        json_escape(&config.mode),
        config.warmup,
        config.iterations,
        config.samples,
        config.workers,
    )?;
    output.flush()
}

fn operation_count(iterations: usize, workers: usize) -> u64 {
    u64::try_from(iterations)
        .unwrap()
        .checked_mul(u64::try_from(workers).unwrap())
        .expect("operation count overflow")
}

#[inline(never)]
fn direct_rust_call(counter: &AtomicU64) -> u64 {
    counter.fetch_add(1, Ordering::Relaxed)
}

fn benchmark_direct(config: &Config, writer: &mut ResultWriter) -> io::Result<()> {
    let counter = AtomicU64::new(0);
    for _ in 0..config.warmup {
        black_box(direct_rust_call(black_box(&counter)));
    }
    let mut expected = config.warmup as u64;
    for sample in 0..config.samples {
        let started = Instant::now();
        for _ in 0..config.iterations {
            black_box(direct_rust_call(black_box(&counter)));
        }
        let elapsed = started.elapsed();
        expected = expected.checked_add(config.iterations as u64).unwrap();
        assert_eq!(counter.load(Ordering::Relaxed), expected);
        writer.record(Measurement {
            category: "latency",
            benchmark: "call",
            variant: "direct_rust_atomic_increment",
            topology: "single_thread",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    Ok(())
}

fn benchmark_syscall(config: &Config, writer: &mut ResultWriter) -> io::Result<()> {
    // SAFETY: gettid takes no arguments and has no memory preconditions.
    let expected_tid = unsafe { syscall(SYS_GETTID) };
    assert!(expected_tid > 0);
    for _ in 0..config.warmup {
        // SAFETY: gettid takes no arguments and has no memory preconditions.
        assert_eq!(unsafe { syscall(SYS_GETTID) }, expected_tid);
    }
    for sample in 0..config.samples {
        let mut checksum = 0_i64;
        let started = Instant::now();
        for _ in 0..config.iterations {
            // SAFETY: gettid takes no arguments and has no memory preconditions.
            checksum = checksum.wrapping_add(unsafe { syscall(SYS_GETTID) });
        }
        let elapsed = started.elapsed();
        assert_eq!(
            checksum,
            expected_tid.wrapping_mul(config.iterations as i64)
        );
        writer.record(Measurement {
            category: "latency",
            benchmark: "kernel_entry",
            variant: "gettid_syscall",
            topology: "single_thread",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    Ok(())
}

fn benchmark_pod(config: &Config, writer: &mut ResultWriter) -> Result<(), Box<dyn Error>> {
    let artifact = PodArtifact::open(&config.artifact, &config.artifact_sha256)?;
    // SAFETY: the script just built the authenticated image from this checkout.
    let image = unsafe { PodImage::create_trusted(&artifact, None)? };
    let state = image.create_state(None)?;
    image.verify_runtime_permissions(&state)?;
    for _ in 0..config.warmup {
        image.upsert(&state, 0, 1)?;
    }
    let mut expected = config.warmup as u64;
    for sample in 0..config.samples {
        let started = Instant::now();
        for _ in 0..config.iterations {
            image.upsert(&state, 0, 1)?;
        }
        let elapsed = started.elapsed();
        expected = expected.checked_add(config.iterations as u64).unwrap();
        assert_eq!(image.get(&state, 0)?, Some(expected));
        writer.record(Measurement {
            category: "latency",
            benchmark: "call",
            variant: "authenticated_executable_pod_upsert",
            topology: "single_process_rx_code_rw_state",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    image.validate(&state)?;
    Ok(())
}

fn benchmark_lock_latency(config: &Config, writer: &mut ResultWriter) -> io::Result<()> {
    let spin = ProcessSpinMutex::new(0_u64);
    for _ in 0..config.warmup {
        *spin.lock() += 1;
    }
    let mut expected = config.warmup as u64;
    for sample in 0..config.samples {
        let started = Instant::now();
        for _ in 0..config.iterations {
            *spin.lock() += 1;
        }
        let elapsed = started.elapsed();
        expected += config.iterations as u64;
        assert_eq!(*spin.lock(), expected);
        writer.record(Measurement {
            category: "latency",
            benchmark: "mutex",
            variant: "process_spin_mutex",
            topology: "single_thread_uncontended",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }

    let futex = ProcessFutexMutex::new(0_u64);
    for _ in 0..config.warmup {
        *futex.lock() += 1;
    }
    expected = config.warmup as u64;
    for sample in 0..config.samples {
        let started = Instant::now();
        for _ in 0..config.iterations {
            *futex.lock() += 1;
        }
        let elapsed = started.elapsed();
        expected += config.iterations as u64;
        assert_eq!(*futex.lock(), expected);
        writer.record(Measurement {
            category: "latency",
            benchmark: "mutex",
            variant: "process_futex_mutex",
            topology: "single_thread_uncontended",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    Ok(())
}

fn benchmark_ipc(config: &Config, writer: &mut ResultWriter) -> Result<(), Box<dyn Error>> {
    let (mut client, mut server) = UnixStream::pair()?;
    let exchanges = config
        .warmup
        .checked_add(config.iterations.checked_mul(config.samples).unwrap())
        .unwrap();
    let responder = thread::spawn(move || -> io::Result<()> {
        let mut message = [0_u8; 8];
        for _ in 0..exchanges {
            server.read_exact(&mut message)?;
            server.write_all(&message)?;
        }
        Ok(())
    });
    let message = 0x5a5a_a5a5_1234_5678_u64.to_ne_bytes();
    let mut reply = [0_u8; 8];
    for _ in 0..config.warmup {
        client.write_all(&message)?;
        client.read_exact(&mut reply)?;
        assert_eq!(reply, message);
    }
    for sample in 0..config.samples {
        let started = Instant::now();
        for _ in 0..config.iterations {
            client.write_all(&message)?;
            client.read_exact(&mut reply)?;
        }
        let elapsed = started.elapsed();
        assert_eq!(reply, message);
        writer.record(Measurement {
            category: "latency",
            benchmark: "kernel_ipc",
            variant: "unix_stream_8_byte_round_trip",
            topology: "two_threads_one_process",
            workers: 2,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    client.shutdown(Shutdown::Both)?;
    responder
        .join()
        .map_err(|_| "UnixStream responder panicked")??;
    Ok(())
}

struct ProcessState {
    ready: AtomicU32,
    start: AtomicU32,
    done: AtomicU32,
    release: AtomicU32,
    spin: ProcessSpinMutex<u64>,
    futex: ProcessFutexMutex<u64>,
    coarse: ProcessFutexMutex<[u64; MAX_WORKERS]>,
    fine: [ProcessFutexMutex<u64>; MAX_WORKERS],
    atomic: [AtomicU64; MAX_WORKERS],
}

impl ProcessState {
    const fn new() -> Self {
        Self {
            ready: AtomicU32::new(0),
            start: AtomicU32::new(0),
            done: AtomicU32::new(0),
            release: AtomicU32::new(0),
            spin: ProcessSpinMutex::new(0),
            futex: ProcessFutexMutex::new(0),
            coarse: ProcessFutexMutex::new([0; MAX_WORKERS]),
            fine: [const { ProcessFutexMutex::new(0) }; MAX_WORKERS],
            atomic: [const { AtomicU64::new(0) }; MAX_WORKERS],
        }
    }
}

struct SharedMapping<T> {
    pointer: NonNull<T>,
    length: usize,
}

impl<T> SharedMapping<T> {
    fn new(value: T) -> io::Result<Self> {
        let length = align_up(std::mem::size_of::<T>().max(1), PAGE_SIZE);
        // SAFETY: anonymous shared mmap has no borrowed inputs and is checked.
        let address = unsafe {
            mmap(
                std::ptr::null_mut(),
                length,
                PROT_READ | PROT_WRITE,
                MAP_SHARED | MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        if address as isize == -1 {
            return Err(io::Error::last_os_error());
        }
        let pointer = NonNull::new(address.cast::<T>()).expect("mmap returned null");
        // SAFETY: mmap returned writable storage large and aligned enough for T.
        unsafe { pointer.as_ptr().write(value) };
        Ok(Self { pointer, length })
    }

    fn get(&self) -> &T {
        // SAFETY: the mapping owns one initialized T until Drop.
        unsafe { self.pointer.as_ref() }
    }
}

impl<T> Drop for SharedMapping<T> {
    fn drop(&mut self) {
        // SAFETY: children have been reaped; the parent uniquely tears down T.
        unsafe {
            self.pointer.as_ptr().drop_in_place();
            assert_eq!(
                munmap(self.pointer.as_ptr().cast::<c_void>(), self.length),
                0
            );
        }
    }
}

#[derive(Clone, Copy)]
enum ProcessWorkload {
    SpinMutex,
    FutexMutex,
    CoarseCounter,
    FineHot,
    FineSharded,
    AtomicHot,
    AtomicSharded,
}

impl ProcessWorkload {
    const fn benchmark(self) -> &'static str {
        match self {
            Self::SpinMutex | Self::FutexMutex => "mutex",
            _ => "counter_table",
        }
    }

    const fn variant(self) -> &'static str {
        match self {
            Self::SpinMutex => "process_spin_mutex",
            Self::FutexMutex => "process_futex_mutex",
            Self::CoarseCounter => "coarse_futex_lock",
            Self::FineHot | Self::FineSharded => "fine_grained_futex_locks",
            Self::AtomicHot | Self::AtomicSharded => "atomic_fetch_add",
        }
    }

    const fn topology(self) -> &'static str {
        match self {
            Self::SpinMutex | Self::FutexMutex => "forked_processes_hot",
            Self::CoarseCounter => "forked_processes_sharded_keys_one_lock",
            Self::FineHot | Self::AtomicHot => "forked_processes_hot_key",
            Self::FineSharded | Self::AtomicSharded => "forked_processes_sharded_keys",
        }
    }
}

fn process_operation(state: &ProcessState, workload: ProcessWorkload, worker: usize) {
    match workload {
        ProcessWorkload::SpinMutex => *state.spin.lock() += 1,
        ProcessWorkload::FutexMutex => *state.futex.lock() += 1,
        ProcessWorkload::CoarseCounter => state.coarse.lock()[worker] += 1,
        ProcessWorkload::FineHot => *state.fine[0].lock() += 1,
        ProcessWorkload::FineSharded => *state.fine[worker].lock() += 1,
        ProcessWorkload::AtomicHot => {
            state.atomic[0].fetch_add(1, Ordering::Relaxed);
        }
        ProcessWorkload::AtomicSharded => {
            state.atomic[worker].fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn child_process(
    state: &ProcessState,
    workload: ProcessWorkload,
    worker: usize,
    warmup: usize,
    iterations: usize,
) -> ! {
    for _ in 0..warmup {
        process_operation(state, workload, worker);
    }
    state.ready.fetch_add(1, Ordering::Release);
    while state.start.load(Ordering::Acquire) == 0 {
        spin_loop();
    }
    for _ in 0..iterations {
        process_operation(state, workload, worker);
    }
    state.done.fetch_add(1, Ordering::Release);
    while state.release.load(Ordering::Acquire) == 0 {
        spin_loop();
    }
    // SAFETY: child intentionally bypasses inherited Rust destructors after fork.
    unsafe { _exit(0) }
}

fn terminate_children(children: &[c_int]) {
    for &child in children {
        // SAFETY: best-effort cleanup of PIDs returned by fork.
        unsafe {
            kill(child, SIGKILL);
        }
    }
    for &child in children {
        let mut status = 0;
        // SAFETY: status is writable and child is a fork result.
        unsafe {
            waitpid(child, &mut status, 0);
        }
    }
}

fn wait_for_count(counter: &AtomicU32, expected: u32, phase: &str) -> io::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(30);
    while counter.load(Ordering::Acquire) != expected {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!("timed out waiting for process benchmark {phase}"),
            ));
        }
        spin_loop();
    }
    Ok(())
}

fn validate_process_total(
    state: &ProcessState,
    workload: ProcessWorkload,
    expected: u64,
    workers: usize,
) {
    let actual = match workload {
        ProcessWorkload::SpinMutex => *state.spin.lock(),
        ProcessWorkload::FutexMutex => *state.futex.lock(),
        ProcessWorkload::CoarseCounter => state.coarse.lock()[..workers].iter().sum(),
        ProcessWorkload::FineHot => *state.fine[0].lock(),
        ProcessWorkload::FineSharded => (0..workers).map(|slot| *state.fine[slot].lock()).sum(),
        ProcessWorkload::AtomicHot => state.atomic[0].load(Ordering::Relaxed),
        ProcessWorkload::AtomicSharded => (0..workers)
            .map(|slot| state.atomic[slot].load(Ordering::Relaxed))
            .sum(),
    };
    assert_eq!(actual, expected);
}

fn process_sample(config: &Config, workload: ProcessWorkload) -> Result<Duration, Box<dyn Error>> {
    let mapping = SharedMapping::new(ProcessState::new())?;
    let state = mapping.get();
    let mut children = Vec::with_capacity(config.workers);
    for worker in 0..config.workers {
        // SAFETY: no other threads are live at this phase. The child touches only
        // preinitialized shared atomics/locks and exits with _exit.
        let child = unsafe { fork() };
        if child == 0 {
            child_process(state, workload, worker, config.warmup, config.iterations);
        }
        if child < 0 {
            let error = io::Error::last_os_error();
            terminate_children(&children);
            return Err(error.into());
        }
        children.push(child);
    }

    let expected_workers = config.workers as u32;
    if let Err(error) = wait_for_count(&state.ready, expected_workers, "warmup") {
        state.start.store(1, Ordering::Release);
        state.release.store(1, Ordering::Release);
        terminate_children(&children);
        return Err(error.into());
    }
    let started = Instant::now();
    state.start.store(1, Ordering::Release);
    if let Err(error) = wait_for_count(&state.done, expected_workers, "completion") {
        state.release.store(1, Ordering::Release);
        terminate_children(&children);
        return Err(error.into());
    }
    let elapsed = started.elapsed();
    state.release.store(1, Ordering::Release);
    for child in children {
        let mut status = 0;
        // SAFETY: status is writable and child is a live fork result.
        if unsafe { waitpid(child, &mut status, 0) } != child || status != 0 {
            return Err(format!("benchmark child {child} failed with wait status {status}").into());
        }
    }
    let expected = operation_count(
        config.warmup.checked_add(config.iterations).unwrap(),
        config.workers,
    );
    validate_process_total(state, workload, expected, config.workers);
    Ok(elapsed)
}

fn benchmark_process_contention(
    config: &Config,
    writer: &mut ResultWriter,
) -> Result<(), Box<dyn Error>> {
    for workload in [
        ProcessWorkload::SpinMutex,
        ProcessWorkload::FutexMutex,
        ProcessWorkload::CoarseCounter,
        ProcessWorkload::FineHot,
        ProcessWorkload::FineSharded,
        ProcessWorkload::AtomicHot,
        ProcessWorkload::AtomicSharded,
    ] {
        for sample in 0..config.samples {
            let elapsed = process_sample(config, workload)?;
            writer.record(Measurement {
                category: "throughput",
                benchmark: workload.benchmark(),
                variant: workload.variant(),
                topology: workload.topology(),
                workers: config.workers,
                sample,
                operations: operation_count(config.iterations, config.workers),
                elapsed,
            })?;
        }
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum PresenceTopology {
    Hot,
    Sharded,
}

impl PresenceTopology {
    const fn name(self) -> &'static str {
        match self {
            Self::Hot => "threads_hot_leaf",
            Self::Sharded => "threads_sharded_leaves",
        }
    }

    const fn leaf(self, worker: usize) -> usize {
        match self {
            Self::Hot => 0,
            Self::Sharded => worker % PRESENCE_LEAVES,
        }
    }
}

fn threaded_sample(
    workers: usize,
    warmup: usize,
    iterations: usize,
    operation: impl Fn(usize) + Sync,
) -> Duration {
    let ready = AtomicUsize::new(0);
    let start = AtomicBool::new(false);
    let done = AtomicUsize::new(0);
    let executed = AtomicU64::new(0);
    let elapsed = thread::scope(|scope| {
        let mut handles = Vec::with_capacity(workers);
        for worker in 0..workers {
            let operation = &operation;
            let ready = &ready;
            let start = &start;
            let done = &done;
            let executed = &executed;
            handles.push(scope.spawn(move || {
                for _ in 0..warmup {
                    operation(worker);
                }
                ready.fetch_add(1, Ordering::Release);
                while !start.load(Ordering::Acquire) {
                    spin_loop();
                }
                for _ in 0..iterations {
                    operation(worker);
                }
                executed.fetch_add(iterations as u64, Ordering::Relaxed);
                done.fetch_add(1, Ordering::Release);
            }));
        }
        while ready.load(Ordering::Acquire) != workers {
            spin_loop();
        }
        let started = Instant::now();
        start.store(true, Ordering::Release);
        while done.load(Ordering::Acquire) != workers {
            spin_loop();
        }
        let elapsed = started.elapsed();
        for handle in handles {
            handle.join().expect("benchmark thread panicked");
        }
        elapsed
    });
    assert_eq!(
        executed.load(Ordering::Relaxed),
        operation_count(iterations, workers)
    );
    elapsed
}

fn benchmark_presence(config: &Config, writer: &mut ResultWriter) -> io::Result<()> {
    for topology in [PresenceTopology::Hot, PresenceTopology::Sharded] {
        for sample in 0..config.samples {
            let presence = Snzi::<PRESENCE_NODES>::new();
            let elapsed =
                threaded_sample(config.workers, config.warmup, config.iterations, |worker| {
                    let token = presence
                        .arrive(topology.leaf(worker))
                        .expect("SNZI arrival");
                    black_box(token).depart().expect("SNZI departure");
                });
            assert!(!presence.query());
            assert!(presence.is_quiescent());
            assert_eq!(presence.poison_reason(), None);
            writer.record(Measurement {
                category: "throughput",
                benchmark: "presence_cycle",
                variant: "snzi",
                topology: topology.name(),
                workers: config.workers,
                sample,
                operations: operation_count(config.iterations, config.workers),
                elapsed,
            })?;

            let presence = CloseableSnzi::<PRESENCE_NODES>::new();
            let elapsed =
                threaded_sample(config.workers, config.warmup, config.iterations, |worker| {
                    let token = presence
                        .try_enter(topology.leaf(worker))
                        .expect("closeable SNZI entry");
                    black_box(token).depart().expect("closeable SNZI departure");
                });
            assert!(presence.close());
            assert!(presence.is_drained());
            writer.record(Measurement {
                category: "throughput",
                benchmark: "presence_cycle",
                variant: "closeable_snzi",
                topology: topology.name(),
                workers: config.workers,
                sample,
                operations: operation_count(config.iterations, config.workers),
                elapsed,
            })?;

            let presence = Csnzi::<PRESENCE_NODES>::new();
            let elapsed =
                threaded_sample(config.workers, config.warmup, config.iterations, |worker| {
                    loop {
                        match presence.try_enter(topology.leaf(worker)) {
                            Ok(token) => {
                                black_box(token).depart().expect("C-SNZI departure");
                                break;
                            }
                            Err(CsnziError::DepartureTailBusy) => spin_loop(),
                            Err(error) => panic!("C-SNZI entry failed: {error}"),
                        }
                    }
                });
            assert_eq!(presence.close().unwrap(), CloseOutcome::Drained);
            assert!(presence.is_drained());
            writer.record(Measurement {
                category: "throughput",
                benchmark: "presence_cycle",
                variant: "csnzi",
                topology: topology.name(),
                workers: config.workers,
                sample,
                operations: operation_count(config.iterations, config.workers),
                elapsed,
            })?;
        }
    }
    Ok(())
}

struct RelocBacking {
    pointer: NonNull<u8>,
    layout: Layout,
    arena_offset: usize,
}

impl RelocBacking {
    fn new() -> Self {
        let arena_offset = align_up(std::mem::size_of::<RelocAllocator<RELOC_SLOTS>>(), 64);
        let length = arena_offset + RELOC_SLOTS * RELOC_SLOT_SIZE;
        let layout = Layout::from_size_align(length, 64).unwrap();
        // SAFETY: layout is nonzero and valid; allocation is checked below.
        let raw = unsafe { alloc_zeroed(layout) };
        let pointer = NonNull::new(raw).unwrap_or_else(|| handle_alloc_error(layout));
        // SAFETY: the allocation begins with aligned storage for the allocator.
        unsafe {
            pointer
                .as_ptr()
                .cast::<RelocAllocator<RELOC_SLOTS>>()
                .write(RelocAllocator::new());
        }
        Self {
            pointer,
            layout,
            arena_offset,
        }
    }

    fn region(&self) -> RelocRegion<'_, RELOC_SLOTS> {
        // SAFETY: the allocator was initialized at this allocation's base and
        // the backing remains uniquely owned for the returned region lifetime.
        unsafe {
            self.allocator().initialize(
                self.pointer.as_ptr(),
                self.layout.size(),
                0xbec4_0000_0000_0001,
                self.arena_offset as u64,
                RELOC_SLOT_SIZE,
            )
        }
        .expect("initialize relocatable benchmark arena")
    }

    fn allocator(&self) -> &RelocAllocator<RELOC_SLOTS> {
        // SAFETY: new initialized this exact object at the allocation base.
        unsafe { &*self.pointer.as_ptr().cast::<RelocAllocator<RELOC_SLOTS>>() }
    }
}

impl Drop for RelocBacking {
    fn drop(&mut self) {
        // SAFETY: all regions and collections are gone before the backing drops.
        unsafe {
            self.pointer
                .as_ptr()
                .cast::<RelocAllocator<RELOC_SLOTS>>()
                .drop_in_place();
            dealloc(self.pointer.as_ptr(), self.layout);
        }
    }
}

fn align_up(value: usize, alignment: usize) -> usize {
    value
        .checked_add(alignment - 1)
        .expect("alignment overflow")
        & !(alignment - 1)
}

fn benchmark_relocatable(config: &Config, writer: &mut ResultWriter) -> Result<(), Box<dyn Error>> {
    let backing = RelocBacking::new();
    let mut region = backing.region();
    for index in 0..config.warmup {
        let mut shared = SharedBox::new(&region, index as u64)?;
        assert_eq!(*shared.get(&region)?, index as u64);
        // SAFETY: this single-thread benchmark owns every descriptor/reference.
        assert_eq!(unsafe { shared.destroy(&mut region)? }, index as u64);
    }
    for sample in 0..config.samples {
        let mut checksum = 0_u64;
        let started = Instant::now();
        for index in 0..config.iterations {
            let mut shared = SharedBox::new(&region, index as u64)?;
            // SAFETY: this single-thread benchmark owns every descriptor/reference.
            checksum = checksum.wrapping_add(unsafe { shared.destroy(&mut region)? });
        }
        let elapsed = started.elapsed();
        let expected = (0..config.iterations as u64).fold(0_u64, u64::wrapping_add);
        assert_eq!(checksum, expected);
        assert_eq!(backing.allocator().snapshot().allocated(), 0);
        writer.record(Measurement {
            category: "latency",
            benchmark: "reloc_allocator",
            variant: "shared_box_allocate_destroy_pair",
            topology: "single_thread_exclusive",
            workers: 1,
            sample,
            operations: (config.iterations as u64).checked_mul(2).unwrap(),
            elapsed,
        })?;
    }

    let mut shared = SharedBox::new(&region, 0xfeed_f00d_u64)?;
    for _ in 0..config.warmup {
        assert_eq!(*black_box(shared.get(&region)?), 0xfeed_f00d);
    }
    for sample in 0..config.samples {
        let mut checksum = 0_u64;
        let started = Instant::now();
        for _ in 0..config.iterations {
            checksum ^= *black_box(shared.get(&region)?);
        }
        let elapsed = started.elapsed();
        black_box(checksum);
        writer.record(Measurement {
            category: "latency",
            benchmark: "shared_box",
            variant: "checked_get",
            topology: "single_thread_shared_read",
            workers: 1,
            sample,
            operations: config.iterations as u64,
            elapsed,
        })?;
    }
    // SAFETY: no copies or outstanding references exist.
    assert_eq!(unsafe { shared.destroy(&mut region)? }, 0xfeed_f00d);

    let mut vector = SharedVec::<u64>::with_capacity(&region, 1)?;
    for index in 0..config.warmup {
        // SAFETY: the benchmark has exclusive access to vector and region.
        unsafe { vector.push(&mut region, index as u64)? };
        // SAFETY: the benchmark has exclusive access to vector and region.
        assert_eq!(unsafe { vector.pop(&mut region)? }, Some(index as u64));
    }
    for sample in 0..config.samples {
        let mut checksum = 0_u64;
        let started = Instant::now();
        for index in 0..config.iterations {
            // SAFETY: the benchmark has exclusive access to vector and region.
            unsafe { vector.push(&mut region, index as u64)? };
            // SAFETY: the benchmark has exclusive access to vector and region.
            checksum = checksum.wrapping_add(
                unsafe { vector.pop(&mut region)? }.expect("just-pushed vector value is present"),
            );
        }
        let elapsed = started.elapsed();
        let expected = (0..config.iterations as u64).fold(0_u64, u64::wrapping_add);
        assert_eq!(checksum, expected);
        assert!(vector.is_empty());
        writer.record(Measurement {
            category: "latency",
            benchmark: "shared_vec",
            variant: "checked_push_pop_pair",
            topology: "single_thread_exclusive",
            workers: 1,
            sample,
            operations: (config.iterations as u64).checked_mul(2).unwrap(),
            elapsed,
        })?;
    }
    // SAFETY: no copies or outstanding references exist.
    unsafe { vector.destroy(&mut region)? };
    assert_eq!(backing.allocator().snapshot().allocated(), 0);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("shmem-pod benchmark failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let config = Config::parse()?;
    let run_id = environment("SHMEM_POD_BENCH_RUN_ID");
    let mut writer = ResultWriter::create(&config.output_dir, run_id.clone())?;

    benchmark_direct(&config, &mut writer)?;
    benchmark_syscall(&config, &mut writer)?;
    benchmark_pod(&config, &mut writer)?;
    benchmark_lock_latency(&config, &mut writer)?;

    // Forked-process benchmarks run before any benchmark helper thread exists.
    benchmark_process_contention(&config, &mut writer)?;

    benchmark_ipc(&config, &mut writer)?;
    benchmark_presence(&config, &mut writer)?;
    benchmark_relocatable(&config, &mut writer)?;

    let rows = writer.finish()?;
    // The environment file is also the completion marker. A failed run may
    // leave individually verified rows, but never metadata claiming the whole
    // configured matrix succeeded.
    write_environment(&config, &run_id, rows)?;
    println!(
        "benchmark-ok run_id={run_id} rows={rows} output={} warmup={} iterations={} samples={} workers={} verified=true",
        config.output_dir.display(),
        config.warmup,
        config.iterations,
        config.samples,
        config.workers,
    );
    Ok(())
}
