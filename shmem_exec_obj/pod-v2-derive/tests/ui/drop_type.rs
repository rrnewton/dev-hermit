#[derive(shmem_pod_macros::PodValue)]
struct NeedsDrop {
    value: u64,
}

impl Drop for NeedsDrop {
    fn drop(&mut self) {}
}

fn main() {}
