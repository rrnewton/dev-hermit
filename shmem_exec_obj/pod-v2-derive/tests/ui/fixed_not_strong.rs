use pod_v2_types::PodValue;

#[derive(pod_v2_derive::FixedAddressPodValue)]
struct FixedOnly {
    address_word: usize,
}

fn require_strong<T: PodValue>() {}

fn main() {
    require_strong::<FixedOnly>();
}
