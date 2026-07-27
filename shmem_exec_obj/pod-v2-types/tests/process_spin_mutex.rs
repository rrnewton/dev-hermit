use shmem_pod::sync::ProcessSpinMutex;
#[cfg(feature = "derive")]
use shmem_pod::{PodSync, PodValue};
#[cfg(feature = "derive")]
use std::sync::Arc;

#[cfg(feature = "derive")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct SharedTable {
    values: ProcessSpinMutex<[u64; 4]>,
}

#[cfg(feature = "derive")]
fn require_pod<T: PodValue + PodSync>() {}

#[test]
#[cfg(feature = "derive")]
fn serializes_concurrent_mutation() {
    require_pod::<SharedTable>();
    let table = Arc::new(SharedTable {
        values: ProcessSpinMutex::new([0; 4]),
    });
    let mut workers = Vec::new();

    for worker in 0..8 {
        let table = Arc::clone(&table);
        workers.push(std::thread::spawn(move || {
            for _ in 0..20_000 {
                table.values.lock()[worker % 4] += 1;
            }
        }));
    }

    for worker in workers {
        worker.join().unwrap();
    }
    assert_eq!(*table.values.lock(), [40_000; 4]);
}

#[test]
fn try_lock_and_mutable_access_obey_lock_state() {
    let mut value = ProcessSpinMutex::new(10_u64);
    {
        let mut guard = value.try_lock().expect("initial lock");
        assert!(value.is_locked());
        assert!(value.try_lock().is_none());
        *guard += 5;
    }
    assert!(!value.is_locked());
    *value.get_mut() += 7;
    assert_eq!(value.into_inner(), 22);
}
