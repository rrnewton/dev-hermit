#[derive(shmem_pod_macros::PodValue)]
struct ContainsBox {
    value: Box<u64>,
}

fn main() {}
