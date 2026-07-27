use core::mem::{MaybeUninit, align_of};
use core::sync::atomic::{AtomicBool, Ordering};
use shmem_pod::snzi::{PoisonReason, Snzi, SnziError};
use shmem_pod::{PodSync, PodValue};
use std::sync::{Arc, Barrier};
use std::thread;

fn require_pod<T: PodValue>() {}
fn require_sync<T: PodSync>() {}

#[test]
fn layout_validation_and_basic_lifecycle() {
    assert!(!Snzi::<0>::is_valid_node_count());
    assert!(!Snzi::<5>::is_valid_node_count());
    assert!(Snzi::<4>::is_valid_node_count());
    assert!(Snzi::<20>::is_valid_node_count());
    assert!(Snzi::<84>::is_valid_node_count());

    let snzi = Snzi::<20>::new();
    require_pod::<Snzi<20>>();
    require_sync::<Snzi<20>>();
    assert_eq!(align_of::<Snzi<20>>(), 64);
    assert_eq!(snzi.leaf_count(), 16);
    assert!(!snzi.query());
    assert!(snzi.is_quiescent());

    let first = snzi.arrive(0).unwrap();
    let second = snzi.arrive(15).unwrap();
    assert_eq!(first.leaf(), 0);
    assert_eq!(second.leaf(), 15);
    assert!(snzi.query());
    assert!(!snzi.is_quiescent());

    first.depart().unwrap();
    assert!(snzi.query());
    second.depart().unwrap();
    assert!(!snzi.query());
    assert!(snzi.is_quiescent());
    assert_eq!(snzi.debug_snapshot().root_count, 0);
}

#[test]
fn initializes_directly_in_final_storage() {
    let mut storage = MaybeUninit::<Snzi<20>>::uninit();
    unsafe { Snzi::<20>::initialize_at(storage.as_mut_ptr()) };
    let snzi = unsafe { storage.assume_init() };
    assert!(snzi.is_quiescent());
    let token = snzi.arrive(5).unwrap();
    assert!(snzi.query());
    token.depart().unwrap();
    assert!(snzi.is_quiescent());
}

#[test]
fn raw_token_round_trip_and_malformed_values() {
    let snzi = Snzi::<4>::new();
    let token = snzi.arrive(2).unwrap();
    let raw = token.into_raw();
    assert_eq!(raw & 0xffff, 2);
    unsafe { snzi.depart_raw(raw) }.unwrap();
    assert!(snzi.is_quiescent());

    assert_eq!(
        unsafe { snzi.depart_raw(0) },
        Err(SnziError::MalformedToken)
    );
    assert_eq!(
        unsafe { snzi.depart_raw(1_u64 << 63) },
        Err(SnziError::MalformedToken)
    );

    let out_of_range_leaf = (1_u64 << 16) | 4;
    assert_eq!(
        unsafe { snzi.depart_raw(out_of_range_leaf) },
        Err(SnziError::InvalidLeaf {
            leaf: 4,
            leaf_count: 4,
        })
    );
}

#[test]
fn duplicate_and_stale_raw_generations_are_rejected_when_detectable() {
    let snzi = Snzi::<4>::new();
    let first = snzi.arrive(0).unwrap();
    let stale_raw = first.into_raw();
    unsafe { snzi.depart_raw(stale_raw) }.unwrap();
    assert_eq!(
        unsafe { snzi.depart_raw(stale_raw) },
        Err(SnziError::InactiveToken {
            leaf: 0,
            generation: 1,
        })
    );

    let generation_two = snzi.arrive(0).unwrap();
    assert_eq!(generation_two.generation(), 2);
    assert_eq!(
        unsafe { snzi.depart_raw(stale_raw) },
        Err(SnziError::GenerationMismatch {
            leaf: 0,
            token_generation: 1,
            current_generation: 2,
        })
    );
    generation_two.depart().unwrap();

    assert_eq!(
        snzi.arrive(snzi.leaf_count()),
        Err(SnziError::InvalidLeaf {
            leaf: 4,
            leaf_count: 4,
        })
    );
}

#[test]
fn same_leaf_contention_drains_to_quiescence() {
    const THREADS: usize = 12;
    const ITERATIONS: usize = 2_000;

    let snzi = Arc::new(Snzi::<84>::new());
    let start = Arc::new(Barrier::new(THREADS + 1));
    let arrived = Arc::new(Barrier::new(THREADS + 1));
    let departing = Arc::new(Barrier::new(THREADS + 1));
    let mut workers = Vec::new();

    for _ in 0..THREADS {
        let snzi = Arc::clone(&snzi);
        let start = Arc::clone(&start);
        let arrived = Arc::clone(&arrived);
        let departing = Arc::clone(&departing);
        workers.push(thread::spawn(move || {
            start.wait();
            let held = snzi.arrive(7).unwrap();
            arrived.wait();
            departing.wait();
            held.depart().unwrap();

            for _ in 0..ITERATIONS {
                let token = snzi.arrive(7).unwrap();
                token.depart().unwrap();
            }
        }));
    }

    start.wait();
    arrived.wait();
    assert!(snzi.query());
    departing.wait();
    for worker in workers {
        worker.join().unwrap();
    }

    assert!(!snzi.query());
    assert!(snzi.is_quiescent());
    assert_eq!(snzi.poison_reason(), None);
}

#[test]
fn held_sentinel_is_never_reported_absent() {
    const WORKERS: usize = 8;
    const ITERATIONS: usize = 10_000;

    let snzi = Arc::new(Snzi::<84>::new());
    let sentinel = snzi.arrive(0).unwrap();
    let start = Arc::new(Barrier::new(WORKERS + 2));
    let stop = Arc::new(AtomicBool::new(false));

    let observer = {
        let snzi = Arc::clone(&snzi);
        let start = Arc::clone(&start);
        let stop = Arc::clone(&stop);
        thread::spawn(move || {
            start.wait();
            while !stop.load(Ordering::SeqCst) {
                assert!(snzi.query(), "query missed the held sentinel arrival");
                thread::yield_now();
            }
            assert!(snzi.query());
        })
    };

    let mut workers = Vec::new();
    for worker in 0..WORKERS {
        let snzi = Arc::clone(&snzi);
        let start = Arc::clone(&start);
        workers.push(thread::spawn(move || {
            start.wait();
            for iteration in 0..ITERATIONS {
                let leaf = (worker * 7 + iteration) % snzi.leaf_count();
                let token = snzi.arrive(leaf).unwrap();
                assert!(snzi.query());
                token.depart().unwrap();
            }
        }));
    }

    start.wait();
    for worker in workers {
        worker.join().unwrap();
    }
    stop.store(true, Ordering::SeqCst);
    observer.join().unwrap();

    assert!(snzi.query());
    sentinel.depart().unwrap();
    assert!(!snzi.query());
    assert!(snzi.is_quiescent());
}

#[test]
fn local_count_overflow_poison_is_fail_closed() {
    let snzi = Snzi::<4>::new();
    let mut tokens = Vec::with_capacity(Snzi::<4>::MAX_NODE_COUNT as usize);
    for _ in 0..Snzi::<4>::MAX_NODE_COUNT {
        tokens.push(snzi.arrive(0).unwrap());
    }

    assert_eq!(
        snzi.arrive(0),
        Err(SnziError::Poisoned(PoisonReason::NodeCountOverflow))
    );
    assert_eq!(snzi.poison_reason(), Some(PoisonReason::NodeCountOverflow));
    assert!(snzi.query(), "poison must never be reported as quiescent");
    assert!(!snzi.is_quiescent());

    drop(tokens);
}
