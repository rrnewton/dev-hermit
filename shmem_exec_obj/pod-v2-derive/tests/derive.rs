use core::mem::{align_of, offset_of, size_of};
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use pod_v2_types::{FixedAddressPodValue, PodSync, PodValue};

#[derive(pod_v2_derive::PodValue)]
struct Inner {
    sequence: u64,
    flags: [u32; 2],
}

#[derive(pod_v2_derive::PodValue)]
struct Outer {
    byte: u8,
    inner: Inner,
}

#[derive(pod_v2_derive::PodValue)]
struct Reordered {
    inner: Inner,
    byte: u8,
}

#[derive(pod_v2_derive::FixedAddressPodValue)]
struct FixedOnly {
    address_word: usize,
}

#[derive(pod_v2_derive::PodValue, pod_v2_derive::PodSync)]
struct SharedCounters {
    epoch: u32,
    completed: AtomicU64,
    state: AtomicU32,
}

#[derive(pod_v2_derive::PodValue)]
struct Generic<T: 'static> {
    value: T,
}

#[derive(pod_v2_derive::PodValue)]
struct Tuple(u8, u64);

#[derive(pod_v2_derive::PodValue)]
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
    require_pod::<Generic<[u64; 2]>>();
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
    use pod_v2_types::__private::{FINGERPRINT_SEED, finish, mix_bytes, mix_u128, mix_usize};

    let mut state = mix_bytes(FINGERPRINT_SEED, b"pod-v2-derived-value");
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
