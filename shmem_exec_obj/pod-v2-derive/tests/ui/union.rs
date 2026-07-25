#[derive(pod_v2_derive::FixedAddressPodValue)]
union Unsupported {
    integer: u64,
    float: f64,
}

fn main() {}
