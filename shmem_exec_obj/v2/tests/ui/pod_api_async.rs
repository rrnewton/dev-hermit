#[shmem_pod::pod(namespace = "bad.async", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "bad_async")]
    pub async fn call() -> u64;
}
