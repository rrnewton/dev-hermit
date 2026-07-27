#[derive(shmem_pod_macros::FixedAddressPodValue)]
union Unsupported {
    integer: u64,
    float: f64,
}

fn main() {}
