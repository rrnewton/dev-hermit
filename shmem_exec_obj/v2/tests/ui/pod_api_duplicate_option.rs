#[shmem_pod::pod(
    namespace = "ui.first",
    namespace = "ui.second",
    bindings = Bindings,
    descriptor = API
)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "read")]
    pub fn read(state: *mut u8) -> u64;
}
