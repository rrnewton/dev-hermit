#[shmem_pod::pod(namespace = "bad.variadic", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "bad_variadic")]
    pub fn call(value: u64, ...) -> u64;
}
