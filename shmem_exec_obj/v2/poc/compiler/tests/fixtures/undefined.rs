#![no_std]

unsafe extern "C" {
    fn shmem_pod_missing_dependency() -> u64;
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn shmem_pod_init(_state: *mut u8, _len: u64) -> i32 {
    unsafe { shmem_pod_missing_dependency() as i32 }
}
