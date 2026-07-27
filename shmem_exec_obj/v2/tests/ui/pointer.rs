#[derive(shmem_pod::PodValue)]
struct Target(u8);

#[derive(shmem_pod::PodValue)]
struct ContainsPointer {
    pointer: *const Target,
}

fn main() {}
