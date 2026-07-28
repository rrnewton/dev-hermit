#[shmem_pod::pod(namespace = "", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "read")]
    pub fn read(state: *mut u8) -> u64;
}
