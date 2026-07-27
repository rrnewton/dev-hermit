#[derive(shmem_pod_macros::PodValue)]
struct ContainsVec {
    values: Vec<u64>,
}

fn main() {}
