#[shmem_pod::pod(namespace = "bad.generic", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "bad_generic")]
    pub fn call<T>() -> u64;
}
