// This trusted-source counterexample is compiled for audit only. Never execute it.
#![no_std]

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_register(
    _state: *mut u8,
    pid: u64,
    _code_base: u64,
    _state_base: u64,
    _mode: u32,
) -> i32 {
    unsafe {
        (1_usize as *mut u64).write_volatile(pid);
    }
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_coarse_add(_state: *mut u8, _index: u32, _delta: u64) -> i32 {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_fine_add(_state: *mut u8, _index: u32, _delta: u64) -> i32 {
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_atomic_add(_state: *mut u8, _index: u32, _delta: u64) -> i32 {
    0
}
