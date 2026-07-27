use core::mem::{align_of, offset_of, size_of};
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use shmem_pod::{FixedAddressPodValue, PodSync, PodValue};

#[derive(shmem_pod_macros::PodValue)]
struct Inner {
    sequence: u64,
    flags: [u32; 2],
}

#[derive(shmem_pod_macros::PodValue)]
struct Outer {
    byte: u8,
    inner: Inner,
}

#[derive(shmem_pod_macros::PodValue)]
struct Reordered {
    inner: Inner,
    byte: u8,
}

#[derive(shmem_pod_macros::FixedAddressPodValue)]
struct FixedOnly {
    address_word: usize,
}

#[derive(shmem_pod_macros::PodValue, shmem_pod_macros::PodSync)]
struct SharedCounters {
    epoch: u32,
    completed: AtomicU64,
    state: AtomicU32,
}

#[derive(shmem_pod_macros::PodValue)]
struct Tuple(u8, u64);

#[derive(shmem_pod_macros::PodValue)]
struct Unit;

fn require_pod<T: PodValue>() {}
fn require_fixed<T: FixedAddressPodValue>() {}
fn require_sync<T: PodSync>() {}

#[test]
fn derives_are_recursive_without_repr_c() {
    require_pod::<Inner>();
    require_pod::<Outer>();
    require_fixed::<FixedOnly>();
    require_sync::<SharedCounters>();
    require_pod::<Tuple>();
    require_pod::<Unit>();

    assert_ne!(Outer::FINGERPRINT, Reordered::FINGERPRINT);
    assert_ne!(Outer::FINGERPRINT, Inner::FINGERPRINT);
    assert_eq!(Outer::FINGERPRINT, Outer::FINGERPRINT);
    assert_eq!(Outer::FINGERPRINT, expected_outer_fingerprint());

    let counters = SharedCounters {
        epoch: 7,
        completed: AtomicU64::new(0),
        state: AtomicU32::new(1),
    };
    counters.completed.fetch_add(2, Ordering::Relaxed);
    assert_eq!(counters.completed.load(Ordering::Relaxed), 2);
    assert_eq!(counters.epoch, 7);
    assert_eq!(counters.state.load(Ordering::Relaxed), 1);

    let fixed = FixedOnly { address_word: 1 };
    assert_eq!(fixed.address_word, 1);
}

const fn expected_outer_fingerprint() -> u128 {
    use shmem_pod::__private::{FINGERPRINT_SEED, finish, mix_bytes, mix_u128, mix_usize};

    let mut state = mix_bytes(FINGERPRINT_SEED, b"shmem-pod-derived-value-v1");
    state = mix_bytes(state, concat!(module_path!(), "::Outer").as_bytes());
    state = mix_usize(state, size_of::<Outer>());
    state = mix_usize(state, align_of::<Outer>());
    state = mix_usize(state, 2);

    state = mix_bytes(state, b"byte");
    state = mix_usize(state, offset_of!(Outer, byte));
    state = mix_usize(state, size_of::<u8>());
    state = mix_usize(state, align_of::<u8>());
    state = mix_u128(state, u8::FINGERPRINT);

    state = mix_bytes(state, b"inner");
    state = mix_usize(state, offset_of!(Outer, inner));
    state = mix_usize(state, size_of::<Inner>());
    state = mix_usize(state, align_of::<Inner>());
    state = mix_u128(state, Inner::FINGERPRINT);
    finish(state)
}
