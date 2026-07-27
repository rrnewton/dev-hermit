#[derive(shmem_pod::FixedAddressPodValue)]
union Unsupported {
    integer: u64,
    float: f64,
}

fn main() {}
