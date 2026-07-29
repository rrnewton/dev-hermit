//! Bounded interleaving checks over the production synchronization methods.

extern crate std;

use loom::model::Builder;
use loom::sync::Arc;
use loom::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use loom::thread;

use crate::admission::CloseableSnzi;
use crate::csnzi::Csnzi;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
use crate::sync::{FutexAtomicWord, futex_mark_contended, futex_release, futex_try_acquire};

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl FutexAtomicWord for loom::sync::atomic::AtomicU32 {
    fn compare_exchange(
        &self,
        current: u32,
        new: u32,
        success: Ordering,
        failure: Ordering,
    ) -> Result<u32, u32> {
        self.compare_exchange(current, new, success, failure)
    }

    fn swap(&self, value: u32, order: Ordering) -> u32 {
        self.swap(value, order)
    }
}

fn bounded_builder(max_threads: usize) -> Builder {
    let mut builder = Builder::new();
    builder.max_threads = max_threads;
    if builder.max_permutations.is_none() {
        builder.max_permutations = Some(10_000);
    }
    if builder.preemption_bound.is_none() {
        builder.preemption_bound = Some(2);
    }
    builder
}

#[test]
fn closeable_entry_close_depart_executes_production_protocol() {
    bounded_builder(3).check(|| {
        let barrier = Arc::new(CloseableSnzi::<4>::new());
        let active = Arc::new(AtomicBool::new(false));
        let admitted = Arc::new(AtomicUsize::new(0));
        let departed = Arc::new(AtomicUsize::new(0));

        let entrant = {
            let barrier = Arc::clone(&barrier);
            let active = Arc::clone(&active);
            let admitted = Arc::clone(&admitted);
            let departed = Arc::clone(&departed);
            thread::spawn(move || {
                if let Ok(token) = barrier.try_enter(0) {
                    admitted.fetch_add(1, Ordering::SeqCst);
                    active.store(true, Ordering::SeqCst);
                    thread::yield_now();
                    assert!(!barrier.is_drained());
                    active.store(false, Ordering::SeqCst);
                    token.depart().unwrap();
                    departed.fetch_add(1, Ordering::SeqCst);
                }
            })
        };

        let closer = {
            let barrier = Arc::clone(&barrier);
            let active = Arc::clone(&active);
            thread::spawn(move || {
                barrier.close();
                if barrier.is_drained() {
                    assert!(!active.load(Ordering::SeqCst));
                }
            })
        };

        entrant.join().unwrap();
        closer.join().unwrap();
        barrier.close();
        assert_eq!(
            admitted.load(Ordering::SeqCst),
            departed.load(Ordering::SeqCst)
        );
        assert!(barrier.is_drained());
    });
}

#[test]
fn closeable_same_leaf_help_and_compensation_execute_production_protocol() {
    bounded_builder(3).check(|| {
        let barrier = Arc::new(CloseableSnzi::<4>::new());
        let mut workers = std::vec::Vec::new();
        for _ in 0..2 {
            let barrier = Arc::clone(&barrier);
            workers.push(thread::spawn(move || {
                let token = barrier.try_enter(0).unwrap();
                thread::yield_now();
                token.depart().unwrap();
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        barrier.close();
        assert!(barrier.is_drained());
    });
}

#[test]
fn csnzi_entry_close_depart_executes_production_protocol() {
    bounded_builder(3).check(|| {
        let barrier = Arc::new(Csnzi::<4>::new());
        let active = Arc::new(AtomicBool::new(false));

        let entrant = {
            let barrier = Arc::clone(&barrier);
            let active = Arc::clone(&active);
            thread::spawn(move || {
                if let Ok(token) = barrier.try_enter(0) {
                    active.store(true, Ordering::SeqCst);
                    thread::yield_now();
                    assert!(!barrier.is_drained());
                    active.store(false, Ordering::SeqCst);
                    token.depart().unwrap();
                }
            })
        };

        let closer = {
            let barrier = Arc::clone(&barrier);
            let active = Arc::clone(&active);
            thread::spawn(move || {
                let _ = barrier.close().unwrap();
                if barrier.is_drained() {
                    assert!(!active.load(Ordering::SeqCst));
                }
            })
        };

        entrant.join().unwrap();
        closer.join().unwrap();
        let _ = barrier.close().unwrap();
        assert!(barrier.is_drained());
    });
}

#[test]
fn csnzi_same_leaf_compensation_executes_production_protocol() {
    bounded_builder(3).check(|| {
        let barrier = Arc::new(Csnzi::<4>::new());
        let mut workers = std::vec::Vec::new();
        for _ in 0..2 {
            let barrier = Arc::clone(&barrier);
            workers.push(thread::spawn(move || {
                if let Ok(token) = barrier.try_enter(0) {
                    thread::yield_now();
                    token.depart().unwrap();
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        let _ = barrier.close().unwrap();
        assert!(barrier.is_drained());
    });
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
#[test]
fn futex_word_acquisition_executes_production_transitions() {
    bounded_builder(3).check(|| {
        let word = Arc::new(loom::sync::atomic::AtomicU32::new(0));
        let holders = Arc::new(AtomicUsize::new(0));
        let mut workers = std::vec::Vec::new();
        for _ in 0..2 {
            let word = Arc::clone(&word);
            let holders = Arc::clone(&holders);
            workers.push(thread::spawn(move || {
                if futex_try_acquire(&*word) {
                    assert_eq!(holders.fetch_add(1, Ordering::SeqCst), 0);
                    thread::yield_now();
                    assert_eq!(holders.fetch_sub(1, Ordering::SeqCst), 1);
                    let _ = futex_release(&*word);
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(holders.load(Ordering::SeqCst), 0);
    });
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
#[test]
fn futex_contended_mark_or_unlock_requires_a_wake() {
    bounded_builder(3).check(|| {
        let word = Arc::new(loom::sync::atomic::AtomicU32::new(1));
        let waiter_acquired = Arc::new(AtomicBool::new(false));
        let owner_wake = Arc::new(AtomicBool::new(false));

        let waiter = {
            let word = Arc::clone(&word);
            let waiter_acquired = Arc::clone(&waiter_acquired);
            thread::spawn(move || {
                let acquired = futex_mark_contended(&*word);
                waiter_acquired.store(acquired, Ordering::SeqCst);
                if acquired {
                    let _ = futex_release(&*word);
                }
            })
        };

        let owner = {
            let word = Arc::clone(&word);
            let owner_wake = Arc::clone(&owner_wake);
            thread::spawn(move || {
                owner_wake.store(futex_release(&*word), Ordering::SeqCst);
            })
        };

        waiter.join().unwrap();
        owner.join().unwrap();
        assert!(
            waiter_acquired.load(Ordering::SeqCst) || owner_wake.load(Ordering::SeqCst),
            "the waiter must acquire an observed zero or the owner must observe contention"
        );
    });
}
