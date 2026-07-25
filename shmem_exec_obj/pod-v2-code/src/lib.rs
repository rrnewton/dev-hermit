#![no_std]

use core::mem::{align_of, offset_of, size_of};
use core::ptr::addr_of;
use core::sync::atomic::{AtomicU32, Ordering};

const STATE_MAGIC: u64 = u64::from_le_bytes(*b"POD2RUST");
const STATUS_OK: i32 = 0;
const STATUS_NULL: i32 = -1;
const STATUS_BAD_STATE: i32 = -2;
const STATUS_OUT_OF_MEMORY: i32 = -3;
const STATUS_NOT_FOUND: i32 = -4;
const STATUS_BAD_ARGUMENT: i32 = -5;
const MIN_CAPACITY: u32 = 4;
const INVALID_U64: u64 = u64::MAX;

static LAYOUT_TAG: [u8; 16] = *b"offset-arena-v2!";

// Deliberately repr(Rust). Only this exact linked code image interprets it.
struct State {
    magic: u64,
    layout_hash: u64,
    region_len: u32,
    lock: AtomicU32,
    arena: OffsetArena,
    entries: OffsetVec,
    operations: u64,
}

struct OffsetArena {
    first: u32,
    next: u32,
    end: u32,
    allocations: u32,
}

struct OffsetVec {
    offset: u32,
    len: u32,
    capacity: u32,
    generations: u32,
}

#[derive(Clone, Copy)]
struct Entry {
    key: u64,
    value: u64,
}

type LayoutFn = unsafe extern "C" fn() -> u64;
type StateLenFn = unsafe extern "C" fn(*mut u8, u64) -> i32;
type UpsertFn = unsafe extern "C" fn(*mut u8, u64, u64) -> i32;
type GetFn = unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32;
type StateU64Fn = unsafe extern "C" fn(*mut u8) -> u64;

impl State {
    fn new(region_len: u32) -> Self {
        let first = align_up(size_of::<Self>() as u32, align_of::<Entry>() as u32);
        Self {
            magic: STATE_MAGIC,
            layout_hash: layout_hash_impl(),
            region_len,
            lock: AtomicU32::new(0),
            arena: OffsetArena {
                first,
                next: first,
                end: region_len,
                allocations: 0,
            },
            entries: OffsetVec {
                offset: 0,
                len: 0,
                capacity: 0,
                generations: 0,
            },
            operations: 0,
        }
    }
}

#[inline(always)]
fn align_up(value: u32, alignment: u32) -> u32 {
    value.wrapping_add(alignment - 1) & !(alignment - 1)
}

#[inline(always)]
fn mix(hash: u64, value: u64) -> u64 {
    (hash ^ value).wrapping_mul(0x100_0000_01b3)
}

#[inline(never)]
fn layout_hash_impl() -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325;
    let mut index = 0;
    while index < LAYOUT_TAG.len() {
        hash = mix(hash, LAYOUT_TAG[index] as u64);
        index += 1;
    }
    hash = mix(hash, size_of::<State>() as u64);
    hash = mix(hash, align_of::<State>() as u64);
    hash = mix(hash, offset_of!(State, magic) as u64);
    hash = mix(hash, offset_of!(State, layout_hash) as u64);
    hash = mix(hash, offset_of!(State, region_len) as u64);
    hash = mix(hash, offset_of!(State, lock) as u64);
    hash = mix(hash, offset_of!(State, arena) as u64);
    hash = mix(hash, offset_of!(State, entries) as u64);
    hash = mix(hash, offset_of!(State, operations) as u64);
    hash = mix(hash, size_of::<OffsetArena>() as u64);
    hash = mix(hash, size_of::<OffsetVec>() as u64);
    hash = mix(hash, size_of::<Entry>() as u64);
    hash
}

#[inline(always)]
fn acquire(lock: &AtomicU32) {
    loop {
        if lock
            .compare_exchange_weak(0, 1, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
        {
            return;
        }
        while lock.load(Ordering::Relaxed) != 0 {
            core::hint::spin_loop();
        }
    }
}

#[inline(always)]
fn release(lock: &AtomicU32) {
    lock.store(0, Ordering::Release);
}

#[inline(always)]
unsafe fn checked_state(state: *mut u8) -> Result<*mut State, i32> {
    if state.is_null() || (state as usize) & (align_of::<State>() - 1) != 0 {
        return Err(STATUS_NULL);
    }
    let state = state.cast::<State>();
    if unsafe { (*state).magic } != STATE_MAGIC
        || unsafe { (*state).layout_hash } != layout_hash_impl()
    {
        return Err(STATUS_BAD_STATE);
    }
    Ok(state)
}

#[inline(never)]
unsafe fn validate_inner(state: *mut State, supplied_len: u64) -> bool {
    let region_len = unsafe { (*state).region_len as u64 };
    if region_len != supplied_len
        || region_len < size_of::<State>() as u64
        || region_len > u32::MAX as u64
    {
        return false;
    }
    let arena = unsafe { &(*state).arena };
    if arena.first < size_of::<State>() as u32
        || arena.first > arena.next
        || arena.next > arena.end
        || arena.end as u64 != region_len
    {
        return false;
    }
    let vector = unsafe { &(*state).entries };
    if vector.len > vector.capacity {
        return false;
    }
    if vector.capacity == 0 {
        return vector.offset == 0 && vector.len == 0;
    }
    let bytes = (vector.capacity as u64).wrapping_mul(size_of::<Entry>() as u64);
    vector.offset >= arena.first
        && vector.offset as u64 + bytes <= arena.next as u64
        && vector.offset as usize % align_of::<Entry>() == 0
}

#[inline(never)]
unsafe fn arena_allocate(state: *mut State, size: u32, alignment: u32) -> Option<u32> {
    let arena = unsafe { &mut (*state).arena };
    let aligned = align_up(arena.next, alignment);
    if aligned < arena.next {
        return None;
    }
    let end = aligned.wrapping_add(size);
    if end < aligned || end > arena.end {
        return None;
    }
    arena.next = end;
    arena.allocations = arena.allocations.wrapping_add(1);
    Some(aligned)
}

#[inline(never)]
unsafe fn grow_vector(base: *mut u8, state: *mut State) -> bool {
    let vector = unsafe { &(*state).entries };
    let new_capacity = if vector.capacity == 0 {
        MIN_CAPACITY
    } else {
        vector.capacity.wrapping_mul(2)
    };
    if new_capacity <= vector.capacity {
        return false;
    }
    let bytes_u64 = (new_capacity as u64).wrapping_mul(size_of::<Entry>() as u64);
    if bytes_u64 > u32::MAX as u64 {
        return false;
    }
    let Some(new_offset) =
        (unsafe { arena_allocate(state, bytes_u64 as u32, align_of::<Entry>() as u32) })
    else {
        return false;
    };

    let old_offset = vector.offset;
    let len = vector.len;
    let mut index = 0_u32;
    while index < len {
        let old = unsafe {
            base.add(old_offset as usize)
                .cast::<Entry>()
                .add(index as usize)
                .read()
        };
        unsafe {
            base.add(new_offset as usize)
                .cast::<Entry>()
                .add(index as usize)
                .write(old);
        }
        index += 1;
    }

    let vector = unsafe { &mut (*state).entries };
    vector.offset = new_offset;
    vector.capacity = new_capacity;
    vector.generations = vector.generations.wrapping_add(1);
    true
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_layout_size() -> u64 {
    size_of::<State>() as u64
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_layout_align() -> u64 {
    align_of::<State>() as u64
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_layout_hash() -> u64 {
    layout_hash_impl()
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_init(state: *mut u8, region_len: u64) -> i32 {
    if state.is_null() || (state as usize) & (align_of::<State>() - 1) != 0 {
        return STATUS_NULL;
    }
    if region_len < size_of::<State>() as u64 || region_len > u32::MAX as u64 {
        return STATUS_BAD_ARGUMENT;
    }
    unsafe {
        state.cast::<State>().write(State::new(region_len as u32));
    }
    STATUS_OK
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_validate(state: *mut u8, region_len: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    acquire(lock);
    let valid = unsafe { validate_inner(state, region_len) };
    release(lock);
    if valid { STATUS_OK } else { STATUS_BAD_STATE }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_upsert(state: *mut u8, key: u64, delta: u64) -> i32 {
    let base = state;
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    acquire(lock);
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
        release(lock);
        return STATUS_BAD_STATE;
    }

    let mut index = 0_u32;
    while index < unsafe { (*state).entries.len } {
        let entry = unsafe {
            base.add((*state).entries.offset as usize)
                .cast::<Entry>()
                .add(index as usize)
        };
        if unsafe { (*entry).key } == key {
            unsafe {
                (*entry).value = (*entry).value.wrapping_add(delta);
                (*state).operations = (*state).operations.wrapping_add(1);
            }
            release(lock);
            return STATUS_OK;
        }
        index += 1;
    }

    if unsafe { (*state).entries.len == (*state).entries.capacity }
        && !unsafe { grow_vector(base, state) }
    {
        release(lock);
        return STATUS_OUT_OF_MEMORY;
    }
    let index = unsafe { (*state).entries.len };
    let entry = unsafe {
        base.add((*state).entries.offset as usize)
            .cast::<Entry>()
            .add(index as usize)
    };
    unsafe {
        entry.write(Entry { key, value: delta });
        (*state).entries.len = index + 1;
        (*state).operations = (*state).operations.wrapping_add(1);
    }
    release(lock);
    STATUS_OK
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_get(state: *mut u8, key: u64, output: *mut u64) -> i32 {
    if output.is_null() {
        return STATUS_BAD_ARGUMENT;
    }
    let base = state;
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    acquire(lock);
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
        release(lock);
        return STATUS_BAD_STATE;
    }
    let mut index = 0_u32;
    while index < unsafe { (*state).entries.len } {
        let entry = unsafe {
            base.add((*state).entries.offset as usize)
                .cast::<Entry>()
                .add(index as usize)
        };
        if unsafe { (*entry).key } == key {
            unsafe {
                output.write((*entry).value);
            }
            release(lock);
            return STATUS_OK;
        }
        index += 1;
    }
    release(lock);
    STATUS_NOT_FOUND
}

#[inline(always)]
unsafe fn read_stat(state: *mut u8, which: u32) -> u64 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(_) => return INVALID_U64,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    acquire(lock);
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
        release(lock);
        return INVALID_U64;
    }
    let value = match which {
        0 => unsafe { (*state).entries.len as u64 },
        1 => unsafe { (*state).arena.next as u64 },
        _ => unsafe { (*state).entries.capacity as u64 },
    };
    release(lock);
    value
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_len(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 0) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_allocated(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 1) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn pod_v2_capacity(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 2) }
}

const _: LayoutFn = pod_v2_layout_size;
const _: LayoutFn = pod_v2_layout_align;
const _: LayoutFn = pod_v2_layout_hash;
const _: StateLenFn = pod_v2_init;
const _: StateLenFn = pod_v2_validate;
const _: UpsertFn = pod_v2_upsert;
const _: GetFn = pod_v2_get;
const _: StateU64Fn = pod_v2_len;
const _: StateU64Fn = pod_v2_allocated;
const _: StateU64Fn = pod_v2_capacity;
