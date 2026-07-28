// One source of truth for the demonstration pod's native API. The image API
// expands it into #[pod] metadata; the no_std image expands the same list into
// exact function-pointer type assertions. Keep this file dependency-free so
// the restricted direct-rustc compiler can include it verbatim.
#[macro_export]
macro_rules! shmem_pod_demo_methods {
    ($callback:ident) => {
        $callback! {
            (layout_size, shmem_pod_layout_size, 1, "shmem_pod_layout_size", () -> u64),
            (layout_align, shmem_pod_layout_align, 2, "shmem_pod_layout_align", () -> u64),
            (layout_hash, shmem_pod_layout_hash, 3, "shmem_pod_layout_hash", () -> u64),
            (init, shmem_pod_init, 10, "shmem_pod_init", (state: *mut u8, region_len: u64) -> i32),
            (validate, shmem_pod_validate, 11, "shmem_pod_validate", (state: *mut u8, region_len: u64) -> i32),
            (upsert, shmem_pod_upsert, 20, "shmem_pod_upsert", (state: *mut u8, key: u64, delta: u64) -> i32),
            (get, shmem_pod_get, 21, "shmem_pod_get", (state: *mut u8, key: u64, output: *mut u64) -> i32),
            (len, shmem_pod_len, 22, "shmem_pod_len", (state: *mut u8) -> u64),
            (allocated, shmem_pod_allocated, 23, "shmem_pod_allocated", (state: *mut u8) -> u64),
            (capacity, shmem_pod_capacity, 24, "shmem_pod_capacity", (state: *mut u8) -> u64),
            (snzi_leaf_count, shmem_pod_snzi_leaf_count, 30, "shmem_pod_snzi_leaf_count", () -> u64),
            (snzi_arrive, shmem_pod_snzi_arrive, 31, "shmem_pod_snzi_arrive", (state: *mut u8, leaf: u64, output: *mut u64) -> i32),
            (snzi_depart, shmem_pod_snzi_depart, 32, "shmem_pod_snzi_depart", (state: *mut u8, token: u64) -> i32),
            (snzi_query, shmem_pod_snzi_query, 33, "shmem_pod_snzi_query", (state: *mut u8) -> u64),
            (snzi_quiescent, shmem_pod_snzi_quiescent, 34, "shmem_pod_snzi_quiescent", (state: *mut u8) -> u64),
        }
    };
}
