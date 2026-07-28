#![no_std]

use core::mem::{align_of, offset_of, size_of};
use core::ptr::{addr_of, addr_of_mut};
use shmem_pod::FixedAddressPodValue;
use shmem_pod::snzi::{Snzi, SnziError};
use shmem_pod::sync::ProcessFutexMutex;

#[path = "../../api/src/demo_methods.rs"]
mod demo_methods;

const STATE_MAGIC: u64 = u64::from_le_bytes(*b"POD2RUST");
const STATUS_OK: i32 = 0;
const STATUS_NULL: i32 = -1;
const STATUS_BAD_STATE: i32 = -2;
const STATUS_OUT_OF_MEMORY: i32 = -3;
const STATUS_NOT_FOUND: i32 = -4;
const STATUS_BAD_ARGUMENT: i32 = -5;
const STATUS_SNZI_INVALID_LEAF: i32 = -10;
const STATUS_SNZI_MALFORMED_TOKEN: i32 = -11;
const STATUS_SNZI_GENERATION_MISMATCH: i32 = -12;
const STATUS_SNZI_INACTIVE_TOKEN: i32 = -13;
const STATUS_SNZI_POISONED: i32 = -14;
const MIN_CAPACITY: u32 = 4;
const INVALID_U64: u64 = u64::MAX;
const SNZI_NODES: usize = 20;
const SNZI_LEAF_COUNT: u64 = SharedSnzi::new().leaf_count() as u64;
const SNZI_TOKEN_LEAF_MASK: u64 = (1_u64 << 16) - 1;
const SNZI_TOKEN_RESERVED_BIT: u64 = 1_u64 << 63;
const SNZI_TOKEN_GENERATION_SHIFT: u32 = 16;

static LAYOUT_TAG: [u8; 16] = *b"offset-snzi-v1!!";

type SharedSnzi = Snzi<SNZI_NODES>;

macro_rules! assert_demo_api_signatures {
    ($(($binding:ident, $export:ident, $id:literal, $symbol:literal, ($($argument:ident: $argument_type:ty),*) -> $output:ty)),* $(,)?) => {
        $(
            const _: unsafe extern "C" fn($($argument_type),*) -> $output = $export;
        )*
    };
}

shmem_pod_demo_methods!(assert_demo_api_signatures);

// Deliberately repr(Rust). Only this exact linked code image interprets it.
struct State {
    magic: u64,
    layout_hash: u64,
    region_len: u32,
    lock: ProcessFutexMutex<()>,
    arena: OffsetArena,
    entries: OffsetVec,
    operations: u64,
    snzi: SharedSnzi,
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
    hash = mix(hash, offset_of!(State, snzi) as u64);
    hash = mix(hash, size_of::<OffsetArena>() as u64);
    hash = mix(hash, size_of::<OffsetVec>() as u64);
    hash = mix(hash, size_of::<Entry>() as u64);
    hash = mix(hash, size_of::<SharedSnzi>() as u64);
    hash = mix(
        hash,
        <ProcessFutexMutex<()> as FixedAddressPodValue>::FINGERPRINT as u64,
    );
    hash = mix(
        hash,
        (<ProcessFutexMutex<()> as FixedAddressPodValue>::FINGERPRINT >> 64) as u64,
    );
    hash = mix(
        hash,
        <SharedSnzi as FixedAddressPodValue>::FINGERPRINT as u64,
    );
    hash = mix(
        hash,
        (<SharedSnzi as FixedAddressPodValue>::FINGERPRINT >> 64) as u64,
    );
    hash
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
    if unsafe { (*state).snzi.poison_reason().is_some() } {
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

#[inline(always)]
fn snzi_error_status(error: SnziError) -> i32 {
    match error {
        SnziError::InvalidLeaf { .. } => STATUS_SNZI_INVALID_LEAF,
        SnziError::MalformedToken => STATUS_SNZI_MALFORMED_TOKEN,
        SnziError::GenerationMismatch { .. } => STATUS_SNZI_GENERATION_MISMATCH,
        SnziError::InactiveToken { .. } => STATUS_SNZI_INACTIVE_TOKEN,
        SnziError::Poisoned(_) => STATUS_SNZI_POISONED,
    }
}

#[inline(always)]
fn validate_raw_snzi_token(raw: u64) -> Result<(), i32> {
    if raw & SNZI_TOKEN_RESERVED_BIT != 0 || raw >> SNZI_TOKEN_GENERATION_SHIFT == 0 {
        return Err(STATUS_SNZI_MALFORMED_TOKEN);
    }
    if raw & SNZI_TOKEN_LEAF_MASK >= SNZI_LEAF_COUNT {
        return Err(STATUS_SNZI_INVALID_LEAF);
    }
    Ok(())
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
/// Returns the opaque state size for this exact code image.
///
/// # Safety
///
/// This entry has no memory preconditions; the caller must use the declared C ABI.
pub unsafe extern "C" fn shmem_pod_layout_size() -> u64 {
    size_of::<State>() as u64
}

#[unsafe(no_mangle)]
/// Returns the opaque state alignment for this exact code image.
///
/// # Safety
///
/// This entry has no memory preconditions; the caller must use the declared C ABI.
pub unsafe extern "C" fn shmem_pod_layout_align() -> u64 {
    align_of::<State>() as u64
}

#[unsafe(no_mangle)]
/// Returns the opaque state layout fingerprint for this exact code image.
///
/// # Safety
///
/// This entry has no memory preconditions; the caller must use the declared C ABI.
pub unsafe extern "C" fn shmem_pod_layout_hash() -> u64 {
    layout_hash_impl()
}

#[unsafe(no_mangle)]
/// Initializes an opaque state region in place.
///
/// # Safety
///
/// `state` must identify an exclusively owned, writable `region_len`-byte mapping that remains
/// valid for the pod lifetime. The address must meet the returned layout alignment.
pub unsafe extern "C" fn shmem_pod_init(state: *mut u8, region_len: u64) -> i32 {
    if state.is_null() || (state as usize) & (align_of::<State>() - 1) != 0 {
        return STATUS_NULL;
    }
    if region_len < size_of::<State>() as u64 || region_len > u32::MAX as u64 {
        return STATUS_BAD_ARGUMENT;
    }
    let region_len = region_len as u32;
    let first = align_up(size_of::<State>() as u32, align_of::<Entry>() as u32);
    let state = state.cast::<State>();
    unsafe {
        addr_of_mut!((*state).region_len).write(region_len);
        addr_of_mut!((*state).lock).write(ProcessFutexMutex::new(()));
        addr_of_mut!((*state).arena.first).write(first);
        addr_of_mut!((*state).arena.next).write(first);
        addr_of_mut!((*state).arena.end).write(region_len);
        addr_of_mut!((*state).arena.allocations).write(0);
        addr_of_mut!((*state).entries.offset).write(0);
        addr_of_mut!((*state).entries.len).write(0);
        addr_of_mut!((*state).entries.capacity).write(0);
        addr_of_mut!((*state).entries.generations).write(0);
        addr_of_mut!((*state).operations).write(0);
        SharedSnzi::initialize_at(addr_of_mut!((*state).snzi));
        addr_of_mut!((*state).layout_hash).write(layout_hash_impl());
        addr_of_mut!((*state).magic).write(STATE_MAGIC);
    }
    STATUS_OK
}

#[unsafe(no_mangle)]
/// Validates an initialized opaque state region while holding its shared lock.
///
/// # Safety
///
/// `state` must identify a writable region previously initialized by this exact code image and
/// kept alive for the call. `region_len` must be its complete accessible length.
pub unsafe extern "C" fn shmem_pod_validate(state: *mut u8, region_len: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    let _guard = lock.lock();
    let valid = unsafe { validate_inner(state, region_len) };
    if valid { STATUS_OK } else { STATUS_BAD_STATE }
}

#[unsafe(no_mangle)]
/// Adds `delta` to `key`, inserting the key when necessary.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image. All access to the
/// region must follow this pod's synchronization protocol.
pub unsafe extern "C" fn shmem_pod_upsert(state: *mut u8, key: u64, delta: u64) -> i32 {
    let base = state;
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    let _guard = lock.lock();
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
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
            return STATUS_OK;
        }
        index += 1;
    }

    if unsafe { (*state).entries.len == (*state).entries.capacity }
        && !unsafe { grow_vector(base, state) }
    {
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
    STATUS_OK
}

#[unsafe(no_mangle)]
/// Reads the value associated with `key`.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image. `output` must be
/// nonnull, aligned, writable, and valid for one `u64` for the duration of the call.
pub unsafe extern "C" fn shmem_pod_get(state: *mut u8, key: u64, output: *mut u64) -> i32 {
    if output.is_null() {
        return STATUS_BAD_ARGUMENT;
    }
    let base = state;
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    let _guard = lock.lock();
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
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
            return STATUS_OK;
        }
        index += 1;
    }
    STATUS_NOT_FOUND
}

#[inline(always)]
unsafe fn read_stat(state: *mut u8, which: u32) -> u64 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(_) => return INVALID_U64,
    };
    let lock = unsafe { &*addr_of!((*state).lock) };
    let _guard = lock.lock();
    if !unsafe { validate_inner(state, (*state).region_len as u64) } {
        return INVALID_U64;
    }
    match which {
        0 => unsafe { (*state).entries.len as u64 },
        1 => unsafe { (*state).arena.next as u64 },
        _ => unsafe { (*state).entries.capacity as u64 },
    }
}

#[unsafe(no_mangle)]
/// Returns the number of table entries.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image.
pub unsafe extern "C" fn shmem_pod_len(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 0) }
}

#[unsafe(no_mangle)]
/// Returns the bump arena's current high-water offset.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image.
pub unsafe extern "C" fn shmem_pod_allocated(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 1) }
}

#[unsafe(no_mangle)]
/// Returns the current table capacity.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image.
pub unsafe extern "C" fn shmem_pod_capacity(state: *mut u8) -> u64 {
    unsafe { read_stat(state, 2) }
}

#[unsafe(no_mangle)]
/// Returns the number of addressable leaves in the embedded four-way SNZI.
///
/// # Safety
///
/// This entry has no memory preconditions; the caller must use the declared C ABI.
pub unsafe extern "C" fn shmem_pod_snzi_leaf_count() -> u64 {
    SNZI_LEAF_COUNT
}

#[unsafe(no_mangle)]
/// Records one SNZI arrival and writes its stable scalar departure token.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image. `output` must be
/// nonnull, aligned, writable, and valid for one `u64` for the duration of the call.
pub unsafe extern "C" fn shmem_pod_snzi_arrive(state: *mut u8, leaf: u64, output: *mut u64) -> i32 {
    if output.is_null() || (output as usize) & (align_of::<u64>() - 1) != 0 {
        return STATUS_BAD_ARGUMENT;
    }
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    let leaf = match usize::try_from(leaf) {
        Ok(leaf) => leaf,
        Err(_) => return STATUS_SNZI_INVALID_LEAF,
    };
    match unsafe { (*state).snzi.arrive(leaf) } {
        Ok(token) => {
            unsafe { output.write(token.into_raw()) };
            STATUS_OK
        }
        Err(error) => snzi_error_status(error),
    }
}

#[unsafe(no_mangle)]
/// Matches one SNZI arrival using its stable scalar token.
///
/// # Safety
///
/// `state` must identify the exact live region which issued `token`. The caller must submit each
/// successful arrival token exactly once.
pub unsafe extern "C" fn shmem_pod_snzi_depart(state: *mut u8, token: u64) -> i32 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(status) => return status,
    };
    if let Err(status) = validate_raw_snzi_token(token) {
        return status;
    }
    // SAFETY: The encoding and destination leaf were checked above. The C ABI caller promises
    // that this raw token came from this exact state and is submitted exactly once.
    match unsafe { (*state).snzi.depart_raw(token) } {
        Ok(()) => STATUS_OK,
        Err(error) => snzi_error_status(error),
    }
}

#[unsafe(no_mangle)]
/// Returns one when the SNZI may contain an unmatched arrival and zero otherwise.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image.
pub unsafe extern "C" fn shmem_pod_snzi_query(state: *mut u8) -> u64 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(_) => return INVALID_U64,
    };
    unsafe { (*state).snzi.query() as u64 }
}

#[unsafe(no_mangle)]
/// Returns one when a diagnostic scan observes a healthy, fully idle SNZI.
///
/// # Safety
///
/// `state` must identify a live region initialized by this exact code image. Callers use this only
/// after external admission has stopped and all arrival holders have joined.
pub unsafe extern "C" fn shmem_pod_snzi_quiescent(state: *mut u8) -> u64 {
    let state = match unsafe { checked_state(state) } {
        Ok(state) => state,
        Err(_) => return INVALID_U64,
    };
    unsafe { (*state).snzi.is_quiescent() as u64 }
}

const _: unsafe extern "C" fn() -> u64 = shmem_pod_layout_size;
const _: unsafe extern "C" fn() -> u64 = shmem_pod_layout_align;
const _: unsafe extern "C" fn() -> u64 = shmem_pod_layout_hash;
const _: unsafe extern "C" fn(*mut u8, u64) -> i32 = shmem_pod_init;
const _: unsafe extern "C" fn(*mut u8, u64) -> i32 = shmem_pod_validate;
const _: unsafe extern "C" fn(*mut u8, u64, u64) -> i32 = shmem_pod_upsert;
const _: unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32 = shmem_pod_get;
const _: unsafe extern "C" fn(*mut u8) -> u64 = shmem_pod_len;
const _: unsafe extern "C" fn(*mut u8) -> u64 = shmem_pod_allocated;
const _: unsafe extern "C" fn(*mut u8) -> u64 = shmem_pod_capacity;
const _: unsafe extern "C" fn() -> u64 = shmem_pod_snzi_leaf_count;
const _: unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32 = shmem_pod_snzi_arrive;
const _: unsafe extern "C" fn(*mut u8, u64) -> i32 = shmem_pod_snzi_depart;
const _: unsafe extern "C" fn(*mut u8) -> u64 = shmem_pod_snzi_query;
const _: unsafe extern "C" fn(*mut u8) -> u64 = shmem_pod_snzi_quiescent;

#[cfg(test)]
mod tests {
    use super::*;
    use core::mem::MaybeUninit;

    #[test]
    fn snzi_c_abi_validates_raw_leaves_and_tokens() {
        let mut state = MaybeUninit::<State>::uninit();
        let state_ptr = state.as_mut_ptr().cast::<u8>();
        assert_eq!(
            unsafe { shmem_pod_init(state_ptr, size_of::<State>() as u64) },
            STATUS_OK
        );

        let mut raw = INVALID_U64;
        assert_eq!(
            unsafe { shmem_pod_snzi_arrive(state_ptr, SNZI_LEAF_COUNT, &mut raw) },
            STATUS_SNZI_INVALID_LEAF
        );
        assert_eq!(raw, INVALID_U64);
        assert_eq!(
            unsafe { shmem_pod_snzi_depart(state_ptr, 0) },
            STATUS_SNZI_MALFORMED_TOKEN
        );
        assert_eq!(
            unsafe { shmem_pod_snzi_depart(state_ptr, 1_u64 << 63) },
            STATUS_SNZI_MALFORMED_TOKEN
        );

        assert_eq!(
            unsafe { shmem_pod_snzi_arrive(state_ptr, 3, &mut raw) },
            STATUS_OK
        );
        assert_ne!(raw, INVALID_U64);
        assert_eq!(unsafe { shmem_pod_snzi_query(state_ptr) }, 1);
        assert_eq!(unsafe { shmem_pod_snzi_quiescent(state_ptr) }, 0);
        assert_eq!(unsafe { shmem_pod_snzi_depart(state_ptr, raw) }, STATUS_OK);
        assert_eq!(unsafe { shmem_pod_snzi_query(state_ptr) }, 0);
        assert_eq!(unsafe { shmem_pod_snzi_quiescent(state_ptr) }, 1);
        assert_eq!(
            unsafe { shmem_pod_snzi_depart(state_ptr, raw) },
            STATUS_SNZI_INACTIVE_TOKEN
        );
        let future_generation = raw.wrapping_add(1_u64 << 16);
        assert_eq!(
            unsafe { shmem_pod_snzi_depart(state_ptr, future_generation) },
            STATUS_SNZI_GENERATION_MISMATCH
        );
    }
}
