#[shmem_pod::pod(namespace = "bad.type", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "bad_type")]
    pub fn call(value: u32) -> u64;
}
