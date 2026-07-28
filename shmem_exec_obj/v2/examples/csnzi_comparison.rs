//! Compare the three shared-presence primitives under one bounded workload.
//!
//! This is a reproducible shape comparison, not a universal performance claim.
//! Run a release build and retain the emitted JSON lines with host metadata:
//!
//! ```console
//! cargo run --release --example csnzi_comparison -- 8 100000
//! ```

use core::sync::atomic::{AtomicU64, Ordering};
use shmem_pod::admission::CloseableSnzi;
use shmem_pod::csnzi::{CloseOutcome, Csnzi, CsnziError};
use shmem_pod::snzi::Snzi;
use std::hint::black_box;
use std::sync::Barrier;
use std::time::{Duration, Instant};

const NODES: usize = 84;
const LEAVES: usize = 64;

#[derive(Clone, Copy)]
enum Topology {
    Hot,
    Sharded,
}

impl Topology {
    const fn name(self) -> &'static str {
        match self {
            Self::Hot => "hot",
            Self::Sharded => "sharded",
        }
    }

    const fn leaf(self, worker: usize) -> usize {
        match self {
            Self::Hot => 0,
            Self::Sharded => worker % LEAVES,
        }
    }
}

fn measure(
    primitive: &'static str,
    topology: Topology,
    threads: usize,
    iterations: usize,
    operation: impl Fn(usize) + Sync,
) -> Duration {
    let start_gate = Barrier::new(threads + 1);
    let completed = AtomicU64::new(0);
    let elapsed = std::thread::scope(|scope| {
        let mut workers = Vec::with_capacity(threads);
        for worker in 0..threads {
            let start_gate = &start_gate;
            let completed = &completed;
            let operation = &operation;
            workers.push(scope.spawn(move || {
                let leaf = topology.leaf(worker);
                start_gate.wait();
                for _ in 0..iterations {
                    operation(leaf);
                }
                completed.fetch_add(iterations as u64, Ordering::Relaxed);
            }));
        }

        let started = Instant::now();
        start_gate.wait();
        for worker in workers {
            worker.join().expect("benchmark worker panicked");
        }
        started.elapsed()
    });

    let expected = (threads as u64)
        .checked_mul(iterations as u64)
        .expect("operation count overflow");
    assert_eq!(completed.load(Ordering::Relaxed), expected);
    let elapsed_ns = elapsed.as_nanos().max(1);
    let operations_per_second = (expected as u128 * 1_000_000_000) / elapsed_ns;
    println!(
        "{{\"schema\":\"shmem-pod-csnzi-comparison-v1\",\"primitive\":\"{primitive}\",\"topology\":\"{}\",\"threads\":{threads},\"iterations_per_thread\":{iterations},\"operations\":{expected},\"elapsed_ns\":{elapsed_ns},\"operations_per_second\":{operations_per_second},\"verified\":true}}",
        topology.name()
    );
    elapsed
}

fn run_snzi(topology: Topology, threads: usize, iterations: usize) {
    let presence = Snzi::<NODES>::new();
    measure("snzi", topology, threads, iterations, |leaf| {
        let token = presence.arrive(leaf).expect("SNZI arrival");
        black_box(token).depart().expect("SNZI departure");
    });
    assert!(!presence.query());
    assert!(presence.is_quiescent());
    assert_eq!(presence.poison_reason(), None);
}

fn run_closeable(topology: Topology, threads: usize, iterations: usize) {
    let presence = CloseableSnzi::<NODES>::new();
    measure("closeable_snzi", topology, threads, iterations, |leaf| {
        let token = presence.try_enter(leaf).expect("closeable SNZI entry");
        black_box(token).depart().expect("closeable SNZI departure");
    });
    assert!(presence.close());
    assert!(presence.is_drained());
}

fn run_csnzi(topology: Topology, threads: usize, iterations: usize) {
    let presence = Csnzi::<NODES>::new();
    measure("csnzi", topology, threads, iterations, |leaf| {
        loop {
            match presence.try_enter(leaf) {
                Ok(token) => {
                    black_box(token).depart().expect("C-SNZI departure");
                    break;
                }
                Err(CsnziError::DepartureTailBusy) => core::hint::spin_loop(),
                Err(error) => panic!("C-SNZI entry failed: {error}"),
            }
        }
    });
    assert_eq!(presence.close().unwrap(), CloseOutcome::Drained);
    assert!(presence.is_drained());
}

fn parse_positive(argument: Option<String>, default: usize, name: &str) -> usize {
    let value = argument.map_or(default, |value| value.parse().expect(name));
    assert!(value != 0, "{name} must be nonzero");
    value
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let default_threads = std::thread::available_parallelism()
        .map(usize::from)
        .unwrap_or(1)
        .min(8);
    let threads = parse_positive(arguments.next(), default_threads, "threads");
    let iterations = parse_positive(arguments.next(), 100_000, "iterations");
    assert!(
        arguments.next().is_none(),
        "expected: [threads] [iterations]"
    );
    assert!(threads <= 256, "threads must not exceed 256");

    println!(
        "{{\"schema\":\"shmem-pod-csnzi-comparison-v1\",\"kind\":\"environment\",\"arch\":\"{}\",\"os\":\"{}\",\"threads\":{threads},\"iterations_per_thread\":{iterations},\"debug_assertions\":{}}}",
        std::env::consts::ARCH,
        std::env::consts::OS,
        cfg!(debug_assertions)
    );
    for topology in [Topology::Hot, Topology::Sharded] {
        run_snzi(topology, threads, iterations);
        run_closeable(topology, threads, iterations);
        run_csnzi(topology, threads, iterations);
    }
}
