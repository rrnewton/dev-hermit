#![no_std]
#![allow(dead_code)]

#[path = "../../pod-api/src/layout.rs"]
mod pod_api;

use core::ptr::{addr_of, addr_of_mut};
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use pod_api::{
    COUNTER_COUNT, MAX_CONNECTIONS, PodState, STATE_MAGIC, STATUS_BAD_INDEX, STATUS_BAD_STATE,
    STATUS_CONNECTIONS_FULL, STATUS_NULL, STATUS_OK,
};

#[inline(always)]
unsafe fn checked_state(state: *mut PodState) -> Result<*mut PodState, i32> {
    if state.is_null() {
        return Err(STATUS_NULL);
    }

    // Keep validation scalar-only so the emitted method cannot gain a memcmp dependency.
    if unsafe { (*state).header.magic } != STATE_MAGIC
        || unsafe { (*state).header.abi_version } != pod_api::ABI_VERSION
        || unsafe { (*state).header.counter_count as usize } != COUNTER_COUNT
    {
        return Err(STATUS_BAD_STATE);
    }
    Ok(state)
}

#[inline(always)]
unsafe fn atomic_u32_at(pointer: *const AtomicU32) -> &'static AtomicU32 {
    unsafe { &*pointer }
}

#[inline(always)]
unsafe fn atomic_u64_at(pointer: *const AtomicU64) -> &'static AtomicU64 {
    unsafe { &*pointer }
}

#[inline(always)]
fn lock(word: &AtomicU32) {
    loop {
        if word
            .compare_exchange_weak(0, 1, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
        {
            return;
        }
        while word.load(Ordering::Relaxed) != 0 {
            core::hint::spin_loop();
        }
    }
}

#[inline(always)]
fn unlock(word: &AtomicU32) {
    word.store(0, Ordering::Release);
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_register(
    state: *mut PodState,
    pid: u64,
    code_base: u64,
    state_base: u64,
    mode: u32,
) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let count = unsafe {
        atomic_u64_at(addr_of!((*state).control.connection_count)).fetch_add(1, Ordering::Relaxed)
    };
    if count as usize >= MAX_CONNECTIONS {
        unsafe {
            atomic_u32_at(addr_of!((*state).control.failure_count)).fetch_add(1, Ordering::Relaxed);
        }
        return STATUS_CONNECTIONS_FULL;
    }

    let record = unsafe {
        addr_of_mut!((*state).connections)
            .cast::<pod_api::ConnectionRecord>()
            .add(count as usize)
    };
    unsafe {
        atomic_u32_at(addr_of!((*record).mode)).store(mode, Ordering::Relaxed);
        atomic_u64_at(addr_of!((*record).pid)).store(pid, Ordering::Relaxed);
        atomic_u64_at(addr_of!((*record).code_base)).store(code_base, Ordering::Relaxed);
        atomic_u64_at(addr_of!((*record).state_base)).store(state_base, Ordering::Relaxed);
        atomic_u32_at(addr_of!((*record).ready)).store(1, Ordering::Release);
        atomic_u64_at(addr_of!((*state).control.ready_count)).fetch_add(1, Ordering::Release);
    }
    STATUS_OK
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_coarse_add(state: *mut PodState, index: u32, delta: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    if index as usize >= COUNTER_COUNT {
        return STATUS_BAD_INDEX;
    }

    let lock_word = unsafe { atomic_u32_at(addr_of!((*state).coarse_lock.word)) };
    lock(lock_word);
    unsafe {
        let cell = addr_of_mut!((*state).coarse_values.values)
            .cast::<core::cell::UnsafeCell<u64>>()
            .add(index as usize);
        let value = (*cell).get();
        value.write(value.read().wrapping_add(delta));
    }
    unlock(lock_word);
    STATUS_OK
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_fine_add(state: *mut PodState, index: u32, delta: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    if index as usize >= COUNTER_COUNT {
        return STATUS_BAD_INDEX;
    }

    let counter = unsafe {
        addr_of_mut!((*state).fine_values)
            .cast::<pod_api::FineCounter>()
            .add(index as usize)
    };
    let lock_word = unsafe { atomic_u32_at(addr_of!((*counter).lock)) };
    lock(lock_word);
    unsafe {
        let value = (*counter).value.get();
        value.write(value.read().wrapping_add(delta));
    }
    unlock(lock_word);
    STATUS_OK
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_atomic_add(state: *mut PodState, index: u32, delta: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    if index as usize >= COUNTER_COUNT {
        return STATUS_BAD_INDEX;
    }

    let counter = unsafe {
        addr_of!((*state).atomic_values)
            .cast::<pod_api::AtomicCounter>()
            .add(index as usize)
    };
    unsafe {
        atomic_u64_at(addr_of!((*counter).value)).fetch_add(delta, Ordering::Relaxed);
    }
    STATUS_OK
}
