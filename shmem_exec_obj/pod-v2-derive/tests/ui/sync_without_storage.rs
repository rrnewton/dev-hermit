#[derive(shmem_pod_macros::PodSync)]
struct MissingStorageTier {
    value: u64,
}

fn main() {}
