#[derive(shmem_pod_macros::PodValue)]
struct ContainsMutex {
    value: std::sync::Mutex<u64>,
}

fn main() {}
