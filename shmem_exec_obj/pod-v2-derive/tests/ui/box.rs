#[derive(pod_v2_derive::PodValue)]
struct ContainsBox {
    value: Box<u64>,
}

fn main() {}
