#[derive(shmem_pod::PodValue)]
struct ContainsBox {
    value: Box<u64>,
}

fn main() {}
