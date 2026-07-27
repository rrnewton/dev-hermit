use core::sync::atomic::AtomicU64;
use shmem_pod::{FixedAddressPodValue, PodSync, PodValue};

fn require_pod<T: PodValue>() {}
fn require_sync<T: PodSync>() {}

#[test]
fn primitive_and_array_fingerprints_are_structural() {
    require_pod::<u64>();
    require_pod::<[u64; 4]>();
    require_sync::<AtomicU64>();
    assert_ne!(u32::FINGERPRINT, u64::FINGERPRINT);
    assert_ne!(<[u64; 2]>::FINGERPRINT, <[u64; 4]>::FINGERPRINT);
    assert_eq!(<[u64; 4]>::FINGERPRINT, <[u64; 4]>::FINGERPRINT);
}
