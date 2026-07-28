#[shmem_pod::pod(namespace = "bad.id", bindings = Bindings, descriptor = API)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "bad_id_one")]
    pub fn one() -> u64;
    #[pod_method(id = 1, symbol = "bad_id_two")]
    pub fn two() -> u64;
}
