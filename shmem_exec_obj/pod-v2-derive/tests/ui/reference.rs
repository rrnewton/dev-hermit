#[derive(pod_v2_derive::PodValue)]
struct Target(u8);

#[derive(pod_v2_derive::PodValue)]
struct ContainsReference {
    value: &'static Target,
}

fn main() {}
