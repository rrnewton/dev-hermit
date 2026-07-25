use pod_v2_types::FixedAddressPodValue;

#[derive(pod_v2_derive::PodValue)]
struct GenericDrop<T: 'static> {
    value: T,
}

impl<T> Drop for GenericDrop<T> {
    fn drop(&mut self) {}
}

const _: u128 = <GenericDrop<u64> as FixedAddressPodValue>::FINGERPRINT;

fn main() {}
