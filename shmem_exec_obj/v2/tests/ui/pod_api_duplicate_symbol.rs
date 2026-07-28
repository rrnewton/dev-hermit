#[shmem_pod::pod(namespace = "bad.symbol", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "same_symbol")]
    pub fn one() -> u64;
    #[pod_method(id = 2, symbol = "same_symbol")]
    pub fn two() -> u64;
}
