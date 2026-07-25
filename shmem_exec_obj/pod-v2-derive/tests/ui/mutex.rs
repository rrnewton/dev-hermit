#[derive(pod_v2_derive::PodValue)]
struct ContainsMutex {
    value: std::sync::Mutex<u64>,
}

fn main() {}
