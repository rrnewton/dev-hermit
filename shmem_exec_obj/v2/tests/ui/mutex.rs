#[derive(shmem_pod::PodValue)]
struct ContainsMutex {
    value: std::sync::Mutex<u64>,
}

fn main() {}
