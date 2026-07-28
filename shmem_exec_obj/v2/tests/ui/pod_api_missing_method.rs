#[shmem_pod::pod(namespace = "bad.missing", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    pub fn call() -> u64;
}
