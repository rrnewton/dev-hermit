#![cfg(target_has_atomic = "64")]

use std::sync::{Arc, Barrier};
use std::thread;

use shmem_pod::admission::CloseableSnzi;
use shmem_pod::csnzi::Csnzi;
use shmem_pod::snzi::Snzi;
use shmem_pod::sync::ProcessSpinMutex;

const WORKERS: usize = 4;

#[test]
fn spin_mutex_serializes_thread_updates() {
    let value = Arc::new(ProcessSpinMutex::new(0_u64));
    let mut workers = Vec::new();
    for _ in 0..WORKERS {
        let value = Arc::clone(&value);
        workers.push(thread::spawn(move || {
            for _ in 0..500 {
                *value.lock() += 1;
            }
        }));
    }
    for worker in workers {
        worker.join().unwrap();
    }
    assert_eq!(*value.lock(), (WORKERS * 500) as u64);
}

#[test]
fn standalone_snzi_balances_contended_leaf_activity() {
    let snzi = Arc::new(Snzi::<20>::new());
    let start = Arc::new(Barrier::new(WORKERS));
    let mut workers = Vec::new();
    for worker in 0..WORKERS {
        let snzi = Arc::clone(&snzi);
        let start = Arc::clone(&start);
        workers.push(thread::spawn(move || {
            start.wait();
            for iteration in 0..250 {
                let leaf = (worker + iteration) % snzi.leaf_count();
                let token = snzi.arrive(leaf).unwrap();
                std::hint::spin_loop();
                token.depart().unwrap();
            }
        }));
    }
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(!snzi.query());
    assert!(snzi.is_quiescent());
}

#[test]
fn closeable_snzi_drains_after_inflight_threads_depart() {
    let admission = Arc::new(CloseableSnzi::<20>::new());
    let entered = Arc::new(Barrier::new(WORKERS + 1));
    let release = Arc::new(Barrier::new(WORKERS + 1));
    let mut workers = Vec::new();
    for worker in 0..WORKERS {
        let admission = Arc::clone(&admission);
        let entered = Arc::clone(&entered);
        let release = Arc::clone(&release);
        workers.push(thread::spawn(move || {
            let token = admission.try_enter(worker).unwrap();
            entered.wait();
            release.wait();
            token.depart().unwrap();
        }));
    }
    entered.wait();
    assert!(admission.close());
    assert!(!admission.is_drained());
    release.wait();
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(admission.is_drained());
}

#[test]
fn csnzi_drains_after_inflight_threads_depart() {
    let admission = Arc::new(Csnzi::<20>::new());
    let entered = Arc::new(Barrier::new(WORKERS + 1));
    let release = Arc::new(Barrier::new(WORKERS + 1));
    let mut workers = Vec::new();
    for worker in 0..WORKERS {
        let admission = Arc::clone(&admission);
        let entered = Arc::clone(&entered);
        let release = Arc::clone(&release);
        workers.push(thread::spawn(move || {
            let token = admission.try_enter(worker).unwrap();
            entered.wait();
            release.wait();
            token.depart().unwrap();
        }));
    }
    entered.wait();
    let _ = admission.close().unwrap();
    assert!(!admission.is_drained());
    release.wait();
    for worker in workers {
        worker.join().unwrap();
    }
    assert!(admission.is_drained());
}
