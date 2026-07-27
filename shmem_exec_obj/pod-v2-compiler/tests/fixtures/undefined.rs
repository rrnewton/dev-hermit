#![no_std]

unsafe extern "C" {
    fn pod_v2_missing_dependency() -> u64;
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_init(_state: *mut u8, _len: u64) -> i32 {
    unsafe { pod_v2_missing_dependency() as i32 }
}
