use std::env;
use std::error::Error;
use std::ffi::c_void;
use std::hint::black_box;
use std::process::{Child, Command};

type BarrierFn = unsafe extern "C" fn(u64) -> i32;

#[derive(Clone, Copy)]
struct Options {
    depth: u32,
    fanout: u32,
    threads: u32,
    iterations: u64,
    barrier_timeout_ms: u64,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("pod-guest: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    let mut children = spawn_children(options)?;
    wait_at_barrier(options.barrier_timeout_ms)?;

    std::thread::scope(|scope| {
        let mut workers = Vec::with_capacity(options.threads as usize);
        for _ in 0..options.threads {
            workers.push(scope.spawn(|| exercise_hooks(options.iterations)));
        }
        for worker in workers {
            black_box(worker.join().expect("credential hook worker panicked"));
        }
    });

    wait_for_children(&mut children)?;
    Ok(())
}

fn spawn_children(options: Options) -> Result<Vec<Child>, Box<dyn Error>> {
    if options.depth == 0 {
        return Ok(Vec::new());
    }
    let executable = env::current_exe()?;
    let mut children = Vec::with_capacity(options.fanout as usize);
    for _ in 0..options.fanout {
        children.push(
            Command::new(&executable)
                .args([
                    "--depth",
                    &(options.depth - 1).to_string(),
                    "--fanout",
                    &options.fanout.to_string(),
                    "--threads",
                    &options.threads.to_string(),
                    "--iterations",
                    &options.iterations.to_string(),
                    "--barrier-timeout-ms",
                    &options.barrier_timeout_ms.to_string(),
                ])
                .spawn()?,
        );
    }
    Ok(children)
}

fn wait_at_barrier(timeout_ms: u64) -> Result<(), Box<dyn Error>> {
    const SYMBOL: &[u8] = b"pod_preload_barrier\0";
    let symbol = unsafe { libc::dlsym(libc::RTLD_DEFAULT, SYMBOL.as_ptr().cast::<libc::c_char>()) };
    if symbol.is_null() {
        return Err("pod_preload_barrier is absent; was LD_PRELOAD configured?".into());
    }
    let barrier: BarrierFn = unsafe { std::mem::transmute::<*mut c_void, BarrierFn>(symbol) };
    let status = unsafe { barrier(timeout_ms) };
    if status != 0 {
        return Err(format!("shared process barrier returned {status}").into());
    }
    Ok(())
}

fn exercise_hooks(iterations: u64) -> u64 {
    let mut checksum = 0_u64;
    for _ in 0..iterations {
        unsafe {
            checksum = checksum.wrapping_add(libc::getuid() as u64);
            checksum = checksum.wrapping_add(libc::geteuid() as u64);
            checksum = checksum.wrapping_add(libc::getgid() as u64);
            checksum = checksum.wrapping_add(libc::getegid() as u64);
        }
    }
    checksum
}

fn wait_for_children(children: &mut [Child]) -> Result<(), Box<dyn Error>> {
    for child in children {
        let status = child.wait()?;
        if !status.success() {
            return Err(format!("child {} exited with {status}", child.id()).into());
        }
    }
    Ok(())
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut options = Options {
        depth: 0,
        fanout: 0,
        threads: 1,
        iterations: 1,
        barrier_timeout_ms: 30_000,
    };
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| format!("{argument} requires a value"))?;
        match argument.as_str() {
            "--depth" => options.depth = value.parse()?,
            "--fanout" => options.fanout = value.parse()?,
            "--threads" => options.threads = value.parse()?,
            "--iterations" => options.iterations = value.parse()?,
            "--barrier-timeout-ms" => options.barrier_timeout_ms = value.parse()?,
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    if options.threads == 0 || options.iterations == 0 {
        return Err("threads and iterations must both be nonzero".into());
    }
    Ok(options)
}
