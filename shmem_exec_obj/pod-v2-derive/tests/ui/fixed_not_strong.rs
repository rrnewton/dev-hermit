use shmem_pod::PodValue;

#[derive(shmem_pod_macros::FixedAddressPodValue)]
struct FixedOnly {
    address_word: usize,
}

fn require_strong<T: PodValue>() {}

fn main() {
    require_strong::<FixedOnly>();
}
