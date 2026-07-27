#[derive(shmem_pod_macros::PodValue)]
struct Target(u8);

#[derive(shmem_pod_macros::PodValue)]
struct ContainsReference {
    value: &'static Target,
}

fn main() {}
