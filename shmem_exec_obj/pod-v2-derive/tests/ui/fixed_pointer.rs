#[derive(shmem_pod_macros::PodValue)]
struct Target(u8);

#[derive(shmem_pod_macros::FixedAddressPodValue)]
struct ContainsPointer {
    pointer: *const Target,
}

fn main() {}
