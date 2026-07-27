//! Unmodified-style guest for the preload demo.
//!
//! This binary knows only about libc's `getuid`. It does not parse bootstrap
//! environment variables, descriptors, pod layouts, or synchronization state.

use std::env;
use std::error::Error;
use std::process::{Child, Command};
use std::thread;

#[derive(Clone, Copy)]
struct Options {
    depth: u32,
    fanout: u32,
    threads: u32,
    calls: u64,
}

unsafe extern "C" {
    fn getuid() -> u32;
}

fn main() {
    if let Err(error) = run() {
        eprintln!("preload guest: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let options = parse_options()?;
    if options.fanout == 0 || options.threads == 0 || options.calls == 0 {
        return Err("fanout, threads, and calls must all be nonzero".into());
    }

    // Exercise the same ordinary libc API before creating descendants. A
    // required preload adapter therefore succeeds or terminates this process
    // before it can orphan a subtree during bootstrap failure.
    std::hint::black_box(unsafe { getuid() });
    let mut children = spawn_descendants(options)?;
    let local_result = run_threads(options);
    let child_result = wait_for_children(&mut children);
    local_result?;
    child_result?;
    Ok(())
}

fn spawn_descendants(options: Options) -> Result<Vec<Child>, Box<dyn Error>> {
    if options.depth == 0 {
        return Ok(Vec::new());
    }
    let executable = env::current_exe()?;
    let mut children = Vec::with_capacity(options.fanout as usize);
    for _ in 0..options.fanout {
        children.push(
            Command::new(&executable)
                .arg("--depth")
                .arg((options.depth - 1).to_string())
                .arg("--fanout")
                .arg(options.fanout.to_string())
                .arg("--threads")
                .arg(options.threads.to_string())
                .arg("--calls")
                .arg(options.calls.to_string())
                .spawn()?,
        );
    }
    Ok(children)
}

fn run_threads(options: Options) -> Result<(), Box<dyn Error>> {
    let mut handles = Vec::with_capacity(options.threads as usize);
    for _ in 0..options.threads {
        handles.push(thread::spawn(move || -> Result<u32, String> {
            let mut observed = None;
            for _ in 0..options.calls {
                let uid = unsafe { getuid() };
                if let Some(expected) = observed {
                    if uid != expected {
                        return Err(format!(
                            "getuid changed within one thread: {expected} -> {uid}"
                        ));
                    }
                } else {
                    observed = Some(uid);
                }
                std::hint::black_box(uid);
            }
            observed.ok_or_else(|| "thread made no calls".into())
        }));
    }

    let mut process_uid = None;
    for handle in handles {
        let uid = handle
            .join()
            .map_err(|_| "guest worker thread panicked")?
            .map_err(|error| format!("guest worker failed: {error}"))?;
        if let Some(expected) = process_uid {
            if uid != expected {
                return Err("getuid disagreed between guest threads".into());
            }
        } else {
            process_uid = Some(uid);
        }
    }
    Ok(())
}

fn wait_for_children(children: &mut [Child]) -> Result<(), Box<dyn Error>> {
    let mut failures = Vec::new();
    for child in children {
        let pid = child.id();
        let status = child.wait()?;
        if !status.success() {
            failures.push(format!("pid {pid}: {status}"));
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(format!("descendants failed: {}", failures.join(", ")).into())
    }
}

fn parse_options() -> Result<Options, Box<dyn Error>> {
    let mut options = Options {
        depth: 2,
        fanout: 2,
        threads: 2,
        calls: 100,
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
            "--calls" => options.calls = value.parse()?,
            _ => return Err(format!("unknown argument {argument:?}").into()),
        }
    }
    Ok(options)
}
