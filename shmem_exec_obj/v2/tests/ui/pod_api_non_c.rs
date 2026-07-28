#[shmem_pod::pod(namespace = "bad.abi", bindings = Bindings, descriptor = API)]
unsafe extern "system" {
    #[pod_method(id = 1, symbol = "bad_abi")]
    pub fn call() -> u64;
}
