#[shmem_pod::pod(
    namespace = "ui.duplicate-method-key",
    bindings = Bindings,
    descriptor = API
)]
unsafe extern "C" {
    #[pod_method(id = 1, id = 2, symbol = "read")]
    pub fn read(state: *mut u8) -> u64;
}
