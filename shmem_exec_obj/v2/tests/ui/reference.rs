#[derive(shmem_pod::PodValue)]
struct Target(u8);

#[derive(shmem_pod::PodValue)]
struct ContainsReference {
    value: &'static Target,
}

fn main() {}
