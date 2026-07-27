#[derive(shmem_pod::PodValue)]
struct NeedsDrop {
    value: u64,
}

impl Drop for NeedsDrop {
    fn drop(&mut self) {}
}

fn main() {}
