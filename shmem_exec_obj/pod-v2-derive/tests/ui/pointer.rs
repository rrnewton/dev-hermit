#[derive(pod_v2_derive::PodValue)]
struct Target(u8);

#[derive(pod_v2_derive::PodValue)]
struct ContainsPointer {
    pointer: *const Target,
}

fn main() {}
