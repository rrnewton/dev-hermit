//! Relocatable fixed-capacity allocation over caller-mapped shared pages.
//!
//! Unlike the Talc-backed fixed-address allocator, this allocator persists no
//! pointer. Its geometry, bitmap, allocation descriptors, and slot metadata are
//! integers and atomics. Each process supplies its own mapping base through
//! [`RelocRegion`], so the same backing object may be mapped at different virtual
//! addresses.
//!
//! The arena is divided into `SLOTS` equal-size slots. One allocation consumes
//! one slot; a request must therefore fit the configured slot size and the
//! allocator's fixed 64-byte alignment ceiling. Reuse increments a generation
//! counter. Resolution checks the region identity, slot, generation, integer
//! offset, extent, alignment, layout fingerprint, and allocation bitmap before
//! deriving a local pointer.
//!
//! Allocation and destruction use a bounded process-shared lock. Contention
//! returns [`RelocError::Busy`] instead of spinning indefinitely. If a process
//! dies while holding that lock, subsequent operations remain fail-closed with
//! `Busy`; a supervisor must stop participants, call [`RelocAllocator::poison`]
//! for diagnostics, discard the complete mapping generation, and create a new
//! one. The lock is deliberately not stolen because a paused owner may resume.

use core::fmt;
use core::hint::spin_loop;
use core::marker::PhantomData;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::ptr::NonNull;
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use crate::layout::LayoutDescriptor;
use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const UNINITIALIZED: u32 = 0;
const INITIALIZING: u32 = 1;
const READY: u32 = 2;
const POISONED: u32 = 3;
const UNLOCKED: u32 = 0;
const LOCKED: u32 = 1;
const ALLOCATED_BIT: u64 = 1_u64 << 63;
const GENERATION_MASK: u64 = !ALLOCATED_BIT;
const INITIAL_GENERATION: u64 = 1;
const MAX_BITMAP_WORDS: usize = 16;
const BITS_PER_WORD: usize = u64::BITS as usize;
const LOCK_ATTEMPTS: usize = 128;

/// Maximum number of independently allocated slots in one allocator.
pub const MAX_RELOC_SLOTS: usize = MAX_BITMAP_WORDS * BITS_PER_WORD;

/// Required alignment of the arena start and every configured slot.
pub const RELOC_SLOT_ALIGNMENT: usize = 64;

/// Persistent lifecycle of a relocatable allocator.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelocAllocatorState {
    /// Geometry has not been published.
    Uninitialized,
    /// One participant is publishing geometry and initial generations.
    Initializing,
    /// Allocation, resolution, and destruction are permitted.
    Ready,
    /// An interrupted or corrupt generation must be discarded.
    Poisoned,
}

/// Integer-only identity of one allocation generation.
///
/// Every bit pattern can be constructed. Using a descriptor never directly
/// forms a pointer: [`RelocRegion`] validates all fields against live allocator
/// metadata first. This makes copying the descriptor through a stable bootstrap
/// format safe, although destruction still requires exclusive ownership.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct AllocationDescriptor {
    region_id: u64,
    slot: u32,
    generation: u64,
    offset: u64,
    byte_len: u64,
    alignment: u64,
    fingerprint_low: u64,
    fingerprint_high: u64,
}

impl AllocationDescriptor {
    /// Reconstructs a descriptor from untrusted integer fields.
    #[allow(clippy::too_many_arguments)]
    pub const fn from_raw(
        region_id: u64,
        slot: u32,
        generation: u64,
        offset: u64,
        byte_len: u64,
        alignment: u64,
        fingerprint_low: u64,
        fingerprint_high: u64,
    ) -> Self {
        Self {
            region_id,
            slot,
            generation,
            offset,
            byte_len,
            alignment,
            fingerprint_low,
            fingerprint_high,
        }
    }

    /// Returns an inert descriptor which names no allocation.
    pub const fn null() -> Self {
        Self::from_raw(0, u32::MAX, 0, u64::MAX, 0, 1, 0, 0)
    }

    /// Returns whether this is the canonical null descriptor.
    pub const fn is_null(self) -> bool {
        self.region_id == 0
            && self.slot == u32::MAX
            && self.generation == 0
            && self.offset == u64::MAX
    }

    /// Returns the allocator generation identity.
    pub const fn region_id(self) -> u64 {
        self.region_id
    }

    /// Returns the allocation slot index.
    pub const fn slot(self) -> u32 {
        self.slot
    }

    /// Returns the reuse generation recorded for the slot.
    pub const fn generation(self) -> u64 {
        self.generation
    }

    /// Returns the byte offset relative to the mapping base.
    pub const fn offset(self) -> u64 {
        self.offset
    }

    /// Returns the requested allocation extent.
    pub const fn byte_len(self) -> u64 {
        self.byte_len
    }

    /// Returns the requested alignment.
    pub const fn alignment(self) -> u64 {
        self.alignment
    }

    /// Returns the exact structural layout fingerprint.
    pub const fn fingerprint(self) -> u128 {
        (self.fingerprint_high as u128) << 64 | self.fingerprint_low as u128
    }
}

// SAFETY: the descriptor consists exclusively of fixed-width integers.
unsafe impl FixedAddressPodValue for AllocationDescriptor {
    const FINGERPRINT: u128 = {
        let state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-allocation-descriptor-v1",
        );
        let state = __private::mix_usize(state, size_of::<Self>());
        __private::finish(__private::mix_usize(state, align_of::<Self>()))
    };
}

// SAFETY: the descriptor stores no address.
unsafe impl PodValue for AllocationDescriptor {}

// SAFETY: immutable integer fields support shared references.
unsafe impl PodSync for AllocationDescriptor {}

struct SlotMetadata {
    token: AtomicU64,
    byte_len: AtomicU64,
    alignment: AtomicU64,
    fingerprint_low: AtomicU64,
    fingerprint_high: AtomicU64,
}

impl SlotMetadata {
    const fn new() -> Self {
        Self {
            token: AtomicU64::new(INITIAL_GENERATION),
            byte_len: AtomicU64::new(0),
            alignment: AtomicU64::new(1),
            fingerprint_low: AtomicU64::new(0),
            fingerprint_high: AtomicU64::new(0),
        }
    }

    fn reset(&self) {
        self.byte_len.store(0, Ordering::Relaxed);
        self.alignment.store(1, Ordering::Relaxed);
        self.fingerprint_low.store(0, Ordering::Relaxed);
        self.fingerprint_high.store(0, Ordering::Relaxed);
        self.token.store(INITIAL_GENERATION, Ordering::Release);
    }
}

/// Shared fixed-capacity allocator whose persistent state is address independent.
///
/// Construct this value exactly once in shared storage, then call
/// [`initialize`](Self::initialize) from one participant. `SLOTS` must be in
/// `1..=MAX_RELOC_SLOTS`. The object itself may have ordinary Rust layout: all
/// attachers must authenticate the exact build and validate its layout before
/// forming `&RelocAllocator`.
pub struct RelocAllocator<const SLOTS: usize> {
    state: AtomicU32,
    operation_lock: AtomicU32,
    region_id: AtomicU64,
    control_offset: AtomicU64,
    mapping_len: AtomicU64,
    arena_offset: AtomicU64,
    slot_size: AtomicU64,
    bitmap: [AtomicU64; MAX_BITMAP_WORDS],
    slots: [SlotMetadata; SLOTS],
}

impl<const SLOTS: usize> RelocAllocator<SLOTS> {
    /// Creates uninitialized allocator metadata.
    pub const fn new() -> Self {
        Self {
            state: AtomicU32::new(UNINITIALIZED),
            operation_lock: AtomicU32::new(UNLOCKED),
            region_id: AtomicU64::new(0),
            control_offset: AtomicU64::new(0),
            mapping_len: AtomicU64::new(0),
            arena_offset: AtomicU64::new(0),
            slot_size: AtomicU64::new(0),
            bitmap: [const { AtomicU64::new(0) }; MAX_BITMAP_WORDS],
            slots: [const { SlotMetadata::new() }; SLOTS],
        }
    }

    /// Returns the persistent lifecycle state.
    pub fn state(&self) -> RelocAllocatorState {
        match self.state.load(Ordering::Acquire) {
            UNINITIALIZED => RelocAllocatorState::Uninitialized,
            INITIALIZING => RelocAllocatorState::Initializing,
            READY => RelocAllocatorState::Ready,
            _ => RelocAllocatorState::Poisoned,
        }
    }

    /// Returns a diagnostic allocator snapshot.
    pub fn snapshot(&self) -> RelocAllocatorSnapshot {
        let mut allocated = 0_usize;
        let words = SLOTS.div_ceil(BITS_PER_WORD).min(MAX_BITMAP_WORDS);
        for word in &self.bitmap[..words] {
            allocated =
                allocated.saturating_add(word.load(Ordering::Acquire).count_ones() as usize);
        }
        RelocAllocatorSnapshot {
            state: self.state(),
            capacity: SLOTS,
            allocated: allocated.min(SLOTS),
            operation_locked: self.operation_lock.load(Ordering::Acquire) != UNLOCKED,
        }
    }

    /// Initializes geometry and returns this process's local region view.
    ///
    /// `region_id` must be nonzero and unique for the lifetime of any stale
    /// descriptor. `arena_offset` and `slot_size` must be 64-byte aligned. The
    /// complete `SLOTS * slot_size` arena must fit in the mapping without
    /// overlapping this allocator object.
    ///
    /// # Safety
    ///
    /// `base..base + mapping_len` must be one live writable shared mapping for
    /// the returned lifetime. `self` must be initialized shared storage wholly
    /// inside that mapping, and this caller must own the unique initialization
    /// attempt. The mapping and allocator object must not be moved, replaced, or
    /// unmapped while a region, descriptor, collection, or resolved borrow may
    /// be used. Participants must authenticate the complete backing object and
    /// exact code build before typed access.
    pub unsafe fn initialize(
        &self,
        base: *mut u8,
        mapping_len: usize,
        region_id: u64,
        arena_offset: u64,
        slot_size: usize,
    ) -> Result<RelocRegion<'_, SLOTS>, RelocError> {
        if region_id == 0 {
            return Err(RelocError::ZeroRegionId);
        }
        let geometry = self.validate_geometry(base, mapping_len, arena_offset, slot_size)?;
        if let Err(actual) = self.state.compare_exchange(
            UNINITIALIZED,
            INITIALIZING,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            return Err(state_error(actual));
        }

        let mut poison = InitializationPoison {
            state: &self.state,
            armed: true,
        };
        self.operation_lock.store(UNLOCKED, Ordering::Relaxed);
        self.region_id.store(region_id, Ordering::Relaxed);
        self.control_offset
            .store(geometry.control_offset, Ordering::Relaxed);
        self.mapping_len
            .store(mapping_len as u64, Ordering::Relaxed);
        self.arena_offset.store(arena_offset, Ordering::Relaxed);
        self.slot_size.store(slot_size as u64, Ordering::Relaxed);
        for word in &self.bitmap {
            word.store(0, Ordering::Relaxed);
        }
        for slot in &self.slots {
            slot.reset();
        }
        self.state.store(READY, Ordering::Release);
        poison.armed = false;
        Ok(RelocRegion::new(self, geometry.base, mapping_len))
    }

    /// Attaches a local mapping of an initialized backing object.
    ///
    /// # Safety
    ///
    /// The range must map the same authenticated shared object used by
    /// initialization, remain live and writable for the returned lifetime, and
    /// contain `self` at the recorded integer offset. `expected_region_id` must
    /// come from authenticated bootstrap metadata rather than from the mapping
    /// being checked. All participants must follow the allocation, collection,
    /// and cross-process synchronization contracts.
    pub unsafe fn attach(
        &self,
        base: *mut u8,
        mapping_len: usize,
        expected_region_id: u64,
    ) -> Result<RelocRegion<'_, SLOTS>, RelocError> {
        match self.state.load(Ordering::Acquire) {
            READY => {}
            actual => return Err(state_error(actual)),
        }
        let found_region = self.region_id.load(Ordering::Relaxed);
        if found_region != expected_region_id {
            return Err(RelocError::WrongRegion {
                expected: expected_region_id,
                found: found_region,
            });
        }
        let recorded_len = self.mapping_len.load(Ordering::Relaxed);
        if recorded_len != mapping_len as u64 {
            return Err(RelocError::MappingLength {
                expected: recorded_len,
                found: mapping_len as u64,
            });
        }
        let arena_offset = self.arena_offset.load(Ordering::Relaxed);
        let slot_size = usize::try_from(self.slot_size.load(Ordering::Relaxed))
            .map_err(|_| RelocError::GeometryOverflow)?;
        let geometry = self.validate_geometry(base, mapping_len, arena_offset, slot_size)?;
        let recorded_control = self.control_offset.load(Ordering::Relaxed);
        if geometry.control_offset != recorded_control {
            return Err(RelocError::ControlOffset {
                expected: recorded_control,
                found: geometry.control_offset,
            });
        }
        Ok(RelocRegion::new(self, geometry.base, mapping_len))
    }

    /// Permanently poisons this allocator generation.
    ///
    /// Poisoning does not steal or unlock a possibly live operation. Stop every
    /// participant before discarding the backing object.
    pub fn poison(&self) {
        self.state.store(POISONED, Ordering::Release);
    }

    fn validate_geometry(
        &self,
        base: *mut u8,
        mapping_len: usize,
        arena_offset: u64,
        slot_size: usize,
    ) -> Result<ValidatedGeometry, RelocError> {
        if SLOTS == 0 || SLOTS > MAX_RELOC_SLOTS {
            return Err(RelocError::UnsupportedSlotCount {
                requested: SLOTS,
                maximum: MAX_RELOC_SLOTS,
            });
        }
        let base = NonNull::new(base).ok_or(RelocError::NullBase)?;
        if mapping_len > isize::MAX as usize {
            return Err(RelocError::RegionTooLarge);
        }
        let base_address = base.as_ptr() as usize;
        let mapping_end = base_address
            .checked_add(mapping_len)
            .ok_or(RelocError::AddressOverflow)?;
        let control_start = self as *const Self as usize;
        let control_end = control_start
            .checked_add(size_of::<Self>())
            .ok_or(RelocError::AddressOverflow)?;
        if control_start < base_address || control_end > mapping_end {
            return Err(RelocError::AllocatorOutsideRegion);
        }
        let control_offset = (control_start - base_address) as u64;
        if slot_size == 0 || slot_size % RELOC_SLOT_ALIGNMENT != 0 {
            return Err(RelocError::SlotSizeAlignment {
                required: RELOC_SLOT_ALIGNMENT,
            });
        }
        let arena_offset_usize =
            usize::try_from(arena_offset).map_err(|_| RelocError::GeometryOverflow)?;
        let arena_start = base_address
            .checked_add(arena_offset_usize)
            .ok_or(RelocError::GeometryOverflow)?;
        if arena_start % RELOC_SLOT_ALIGNMENT != 0 {
            return Err(RelocError::ArenaAlignment {
                required: RELOC_SLOT_ALIGNMENT,
            });
        }
        let arena_len = slot_size
            .checked_mul(SLOTS)
            .ok_or(RelocError::GeometryOverflow)?;
        let arena_end = arena_start
            .checked_add(arena_len)
            .ok_or(RelocError::GeometryOverflow)?;
        if arena_end > mapping_end {
            return Err(RelocError::ArenaOutOfBounds {
                offset: arena_offset,
                byte_len: arena_len as u64,
                mapping_len: mapping_len as u64,
            });
        }
        if control_start < arena_end && arena_start < control_end {
            return Err(RelocError::ArenaOverlapsAllocator);
        }
        Ok(ValidatedGeometry {
            base,
            control_offset,
        })
    }

    fn try_operation(&self) -> Result<OperationGuard<'_, SLOTS>, RelocError> {
        for _ in 0..LOCK_ATTEMPTS {
            match self.state.load(Ordering::Acquire) {
                READY => {}
                actual => return Err(state_error(actual)),
            }
            if self
                .operation_lock
                .compare_exchange_weak(UNLOCKED, LOCKED, Ordering::Acquire, Ordering::Relaxed)
                .is_ok()
            {
                if self.state.load(Ordering::Acquire) != READY {
                    self.operation_lock.store(UNLOCKED, Ordering::Release);
                    return Err(RelocError::Poisoned);
                }
                return Ok(OperationGuard {
                    allocator: self,
                    poison_on_drop: false,
                });
            }
            spin_loop();
        }
        Err(RelocError::Busy)
    }

    fn word_and_mask(slot: usize) -> (usize, u64) {
        (slot / BITS_PER_WORD, 1_u64 << (slot % BITS_PER_WORD))
    }

    fn expected_offset(&self, slot: usize) -> Result<u64, RelocError> {
        let stride = self.slot_size.load(Ordering::Relaxed);
        let relative = stride
            .checked_mul(slot as u64)
            .ok_or(RelocError::GeometryOverflow)?;
        self.arena_offset
            .load(Ordering::Relaxed)
            .checked_add(relative)
            .ok_or(RelocError::GeometryOverflow)
    }
}

impl<const SLOTS: usize> Default for RelocAllocator<SLOTS> {
    fn default() -> Self {
        Self::new()
    }
}

// SAFETY: all persistent fields are integers or integer atomics; no address is
// stored. The const parameter is included in the exact-build fingerprint.
unsafe impl<const SLOTS: usize> FixedAddressPodValue for RelocAllocator<SLOTS> {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "relocatable allocators must not need drop"
        );
        assert!(
            SLOTS > 0 && SLOTS <= MAX_RELOC_SLOTS,
            "unsupported slot count"
        );
        let state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-reloc-allocator-v1");
        let state = __private::mix_usize(state, SLOTS);
        let state = __private::mix_usize(state, size_of::<Self>());
        __private::finish(__private::mix_usize(state, align_of::<Self>()))
    };
}

// SAFETY: every persistent field is address independent.
unsafe impl<const SLOTS: usize> PodValue for RelocAllocator<SLOTS> {}

// SAFETY: shared mutation is performed exclusively through atomics and the
// bounded process-shared operation lock.
unsafe impl<const SLOTS: usize> PodSync for RelocAllocator<SLOTS> {}

/// Diagnostic snapshot of allocator capacity and lifecycle.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RelocAllocatorSnapshot {
    state: RelocAllocatorState,
    capacity: usize,
    allocated: usize,
    operation_locked: bool,
}

impl RelocAllocatorSnapshot {
    /// Returns the lifecycle state.
    pub const fn state(self) -> RelocAllocatorState {
        self.state
    }

    /// Returns the compile-time allocation capacity.
    pub const fn capacity(self) -> usize {
        self.capacity
    }

    /// Returns the number of occupied bitmap slots.
    pub const fn allocated(self) -> usize {
        self.allocated
    }

    /// Returns whether an allocator transaction appears active or abandoned.
    pub const fn operation_locked(self) -> bool {
        self.operation_locked
    }
}

/// Process-local view of one relocatable allocator mapping.
///
/// This value contains a local pointer and must never be persisted in shared
/// memory. Persist [`AllocationDescriptor`] or the collection descriptors from
/// [`crate::collections`] instead.
pub struct RelocRegion<'mapping, const SLOTS: usize> {
    allocator: &'mapping RelocAllocator<SLOTS>,
    base: NonNull<u8>,
    len: usize,
    marker: PhantomData<&'mapping [u8]>,
}

// SAFETY: the local pointer remains tied to the mapping lifetime; all shared
// allocator metadata access is atomic or serialized.
unsafe impl<const SLOTS: usize> Send for RelocRegion<'_, SLOTS> {}
// SAFETY: shared operations do not expose mutable references without an unsafe
// exclusive-access contract.
unsafe impl<const SLOTS: usize> Sync for RelocRegion<'_, SLOTS> {}

impl<'mapping, const SLOTS: usize> RelocRegion<'mapping, SLOTS> {
    fn new(allocator: &'mapping RelocAllocator<SLOTS>, base: NonNull<u8>, len: usize) -> Self {
        Self {
            allocator,
            base,
            len,
            marker: PhantomData,
        }
    }

    /// Returns the local mapping base.
    pub const fn base(&self) -> NonNull<u8> {
        self.base
    }

    /// Returns the complete local mapping length.
    pub const fn len(&self) -> usize {
        self.len
    }

    /// Returns whether the mapping has no bytes.
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Returns the shared allocator metadata.
    pub const fn allocator(&self) -> &'mapping RelocAllocator<SLOTS> {
        self.allocator
    }

    pub(crate) fn allocate_with(
        &self,
        descriptor: LayoutDescriptor,
        byte_len: usize,
        alignment: usize,
        initialize: impl FnOnce(*mut u8),
    ) -> Result<AllocationDescriptor, RelocError> {
        self.validate_request(byte_len, alignment)?;
        let mut operation = self.allocator.try_operation()?;
        let slot = self.find_free_slot()?;
        let (word, mask) = RelocAllocator::<SLOTS>::word_and_mask(slot);
        let metadata = &self.allocator.slots[slot];
        let token = metadata.token.load(Ordering::Acquire);
        let generation = token & GENERATION_MASK;
        if token & ALLOCATED_BIT != 0 || generation == 0 {
            operation.poison_on_drop = true;
            return Err(RelocError::CorruptMetadata { slot });
        }
        let offset = self.allocator.expected_offset(slot)?;
        let fingerprint = descriptor.fingerprint();
        operation.poison_on_drop = true;
        metadata.byte_len.store(byte_len as u64, Ordering::Relaxed);
        metadata
            .alignment
            .store(alignment as u64, Ordering::Relaxed);
        metadata
            .fingerprint_low
            .store(fingerprint as u64, Ordering::Relaxed);
        metadata
            .fingerprint_high
            .store((fingerprint >> 64) as u64, Ordering::Relaxed);
        let pointer = self.pointer(offset, byte_len, alignment)?;
        initialize(pointer.as_ptr());
        metadata
            .token
            .store(ALLOCATED_BIT | generation, Ordering::Release);
        let previous = self.allocator.bitmap[word].fetch_or(mask, Ordering::AcqRel);
        if previous & mask != 0 {
            return Err(RelocError::CorruptMetadata { slot });
        }
        operation.poison_on_drop = false;
        Ok(AllocationDescriptor::from_raw(
            self.allocator.region_id.load(Ordering::Relaxed),
            slot as u32,
            generation,
            offset,
            byte_len as u64,
            alignment as u64,
            fingerprint as u64,
            (fingerprint >> 64) as u64,
        ))
    }

    pub(crate) fn resolve<T: PodValue>(
        &self,
        allocation: AllocationDescriptor,
        element_capacity: usize,
        descriptor: LayoutDescriptor,
    ) -> Result<NonNull<T>, RelocError> {
        let bytes = size_of::<T>()
            .checked_mul(element_capacity)
            .ok_or(RelocError::LengthOverflow)?;
        self.validate_request(bytes, align_of::<T>())?;
        self.validate_allocation(allocation, descriptor, bytes, align_of::<T>(), false)?;
        self.pointer(allocation.offset, bytes, align_of::<T>())
            .map(NonNull::cast)
    }

    pub(crate) unsafe fn take_value<T: PodValue>(
        &self,
        allocation: AllocationDescriptor,
        descriptor: LayoutDescriptor,
    ) -> Result<T, RelocError> {
        let mut operation = self.allocator.try_operation()?;
        self.validate_allocation(
            allocation,
            descriptor,
            size_of::<T>(),
            align_of::<T>(),
            true,
        )?;
        let pointer = self
            .pointer(allocation.offset, size_of::<T>(), align_of::<T>())?
            .cast::<T>();
        operation.poison_on_drop = true;
        // SAFETY: validation proved the allocation contains an initialized T;
        // the caller guarantees exclusive destruction.
        let value = unsafe { pointer.as_ptr().read() };
        self.release_locked(allocation)?;
        operation.poison_on_drop = false;
        Ok(value)
    }

    pub(crate) unsafe fn release(
        &self,
        allocation: AllocationDescriptor,
        descriptor: LayoutDescriptor,
        byte_len: usize,
        alignment: usize,
    ) -> Result<(), RelocError> {
        let mut operation = self.allocator.try_operation()?;
        self.validate_allocation(allocation, descriptor, byte_len, alignment, true)?;
        operation.poison_on_drop = true;
        self.release_locked(allocation)?;
        operation.poison_on_drop = false;
        Ok(())
    }

    fn validate_request(&self, byte_len: usize, alignment: usize) -> Result<(), RelocError> {
        if !alignment.is_power_of_two() || alignment > RELOC_SLOT_ALIGNMENT {
            return Err(RelocError::UnsupportedAlignment {
                requested: alignment,
                maximum: RELOC_SLOT_ALIGNMENT,
            });
        }
        if byte_len > isize::MAX as usize {
            return Err(RelocError::LengthOverflow);
        }
        let slot_size = usize::try_from(self.allocator.slot_size.load(Ordering::Relaxed))
            .map_err(|_| RelocError::GeometryOverflow)?;
        if byte_len > slot_size {
            return Err(RelocError::AllocationTooLarge {
                requested: byte_len,
                slot_size,
            });
        }
        Ok(())
    }

    fn find_free_slot(&self) -> Result<usize, RelocError> {
        for slot in 0..SLOTS {
            let (word, mask) = RelocAllocator::<SLOTS>::word_and_mask(slot);
            if self.allocator.bitmap[word].load(Ordering::Acquire) & mask == 0 {
                return Ok(slot);
            }
        }
        Err(RelocError::Exhausted)
    }

    fn validate_allocation(
        &self,
        allocation: AllocationDescriptor,
        descriptor: LayoutDescriptor,
        byte_len: usize,
        alignment: usize,
        lock_held: bool,
    ) -> Result<(), RelocError> {
        match self.allocator.state.load(Ordering::Acquire) {
            READY => {}
            actual => return Err(state_error(actual)),
        }
        if !lock_held && self.allocator.operation_lock.load(Ordering::Acquire) != UNLOCKED {
            return Err(RelocError::Busy);
        }
        let expected_region = self.allocator.region_id.load(Ordering::Relaxed);
        if allocation.region_id != expected_region {
            return Err(RelocError::WrongRegion {
                expected: expected_region,
                found: allocation.region_id,
            });
        }
        let slot = usize::try_from(allocation.slot).map_err(|_| RelocError::SlotOutOfRange {
            slot: allocation.slot,
            capacity: SLOTS,
        })?;
        if slot >= SLOTS {
            return Err(RelocError::SlotOutOfRange {
                slot: allocation.slot,
                capacity: SLOTS,
            });
        }
        let expected_offset = self.allocator.expected_offset(slot)?;
        let expected_fingerprint = descriptor.fingerprint();
        if allocation.offset != expected_offset
            || allocation.byte_len != byte_len as u64
            || allocation.alignment != alignment as u64
            || allocation.fingerprint() != expected_fingerprint
        {
            return Err(RelocError::DescriptorMismatch { slot });
        }
        let metadata = &self.allocator.slots[slot];
        let token = metadata.token.load(Ordering::Acquire);
        let observed_generation = token & GENERATION_MASK;
        if observed_generation != allocation.generation || token & ALLOCATED_BIT == 0 {
            return Err(RelocError::StaleGeneration {
                slot,
                expected: allocation.generation,
                found: observed_generation,
            });
        }
        let (word, mask) = RelocAllocator::<SLOTS>::word_and_mask(slot);
        if self.allocator.bitmap[word].load(Ordering::Acquire) & mask == 0 {
            self.allocator.poison();
            return Err(RelocError::CorruptMetadata { slot });
        }
        let found_fingerprint = (metadata.fingerprint_high.load(Ordering::Relaxed) as u128) << 64
            | metadata.fingerprint_low.load(Ordering::Relaxed) as u128;
        if metadata.byte_len.load(Ordering::Relaxed) != byte_len as u64
            || metadata.alignment.load(Ordering::Relaxed) != alignment as u64
            || found_fingerprint != expected_fingerprint
        {
            self.allocator.poison();
            return Err(RelocError::CorruptMetadata { slot });
        }
        if !lock_held && self.allocator.operation_lock.load(Ordering::Acquire) != UNLOCKED {
            return Err(RelocError::Busy);
        }
        let token_after = metadata.token.load(Ordering::Acquire);
        if token_after != token {
            return Err(RelocError::StaleGeneration {
                slot,
                expected: allocation.generation,
                found: token_after & GENERATION_MASK,
            });
        }
        Ok(())
    }

    fn release_locked(&self, allocation: AllocationDescriptor) -> Result<(), RelocError> {
        let slot = allocation.slot as usize;
        let metadata = &self.allocator.slots[slot];
        if allocation.generation == GENERATION_MASK {
            self.allocator.poison();
            return Err(RelocError::GenerationExhausted { slot });
        }
        let next_generation = allocation.generation + 1;
        metadata.token.store(next_generation, Ordering::Release);
        metadata.byte_len.store(0, Ordering::Relaxed);
        metadata.alignment.store(1, Ordering::Relaxed);
        metadata.fingerprint_low.store(0, Ordering::Relaxed);
        metadata.fingerprint_high.store(0, Ordering::Relaxed);
        let (word, mask) = RelocAllocator::<SLOTS>::word_and_mask(slot);
        let previous = self.allocator.bitmap[word].fetch_and(!mask, Ordering::AcqRel);
        if previous & mask == 0 {
            self.allocator.poison();
            return Err(RelocError::CorruptMetadata { slot });
        }
        Ok(())
    }

    fn pointer(
        &self,
        offset: u64,
        byte_len: usize,
        alignment: usize,
    ) -> Result<NonNull<u8>, RelocError> {
        let offset = usize::try_from(offset).map_err(|_| RelocError::GeometryOverflow)?;
        let end = offset
            .checked_add(byte_len)
            .ok_or(RelocError::GeometryOverflow)?;
        if end > self.len {
            return Err(RelocError::ArenaOutOfBounds {
                offset: offset as u64,
                byte_len: byte_len as u64,
                mapping_len: self.len as u64,
            });
        }
        // SAFETY: the checked offset is within one live mapping.
        let pointer = unsafe { self.base.as_ptr().add(offset) };
        if pointer as usize % alignment != 0 {
            return Err(RelocError::ResolvedMisalignment {
                address: pointer as usize,
                required: alignment,
            });
        }
        // SAFETY: a non-null mapping base plus an in-bounds offset is non-null.
        Ok(unsafe { NonNull::new_unchecked(pointer) })
    }
}

struct ValidatedGeometry {
    base: NonNull<u8>,
    control_offset: u64,
}

struct InitializationPoison<'a> {
    state: &'a AtomicU32,
    armed: bool,
}

impl Drop for InitializationPoison<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.state.store(POISONED, Ordering::Release);
        }
    }
}

struct OperationGuard<'a, const SLOTS: usize> {
    allocator: &'a RelocAllocator<SLOTS>,
    poison_on_drop: bool,
}

impl<const SLOTS: usize> Drop for OperationGuard<'_, SLOTS> {
    fn drop(&mut self) {
        if self.poison_on_drop {
            self.allocator.poison();
        }
        self.allocator
            .operation_lock
            .store(UNLOCKED, Ordering::Release);
    }
}

fn state_error(raw: u32) -> RelocError {
    match raw {
        UNINITIALIZED => RelocError::NotInitialized,
        INITIALIZING => RelocError::InitializationInProgress,
        READY => RelocError::AlreadyInitialized,
        _ => RelocError::Poisoned,
    }
}

const fn mix_field(
    mut state: u128,
    name: &[u8],
    offset: usize,
    size: usize,
    alignment: usize,
    fingerprint: u128,
) -> u128 {
    state = __private::mix_bytes(state, name);
    state = __private::mix_usize(state, offset);
    state = __private::mix_usize(state, size);
    state = __private::mix_usize(state, alignment);
    __private::mix_u128(state, fingerprint)
}

/// Failure to initialize, attach, allocate, resolve, or destroy shared storage.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelocError {
    /// The supplied mapping base is null.
    NullBase,
    /// The mapping exceeds Rust's maximum object extent.
    RegionTooLarge,
    /// Base plus length overflowed the address type.
    AddressOverflow,
    /// The allocator object is not wholly inside the mapping.
    AllocatorOutsideRegion,
    /// The compile-time slot count is unsupported.
    UnsupportedSlotCount {
        /// Requested count.
        requested: usize,
        /// Maximum supported count.
        maximum: usize,
    },
    /// Region identity zero is reserved for null descriptors.
    ZeroRegionId,
    /// Slot size is zero or not a multiple of the required alignment.
    SlotSizeAlignment {
        /// Required byte multiple.
        required: usize,
    },
    /// The local arena start is not sufficiently aligned.
    ArenaAlignment {
        /// Required alignment.
        required: usize,
    },
    /// The arena overlaps allocator control metadata.
    ArenaOverlapsAllocator,
    /// Arena geometry falls outside the mapping.
    ArenaOutOfBounds {
        /// Byte offset from the mapping base.
        offset: u64,
        /// Complete requested extent.
        byte_len: u64,
        /// Mapping length.
        mapping_len: u64,
    },
    /// Integer geometry arithmetic overflowed.
    GeometryOverflow,
    /// The mapping length differs from initialized metadata.
    MappingLength {
        /// Recorded length.
        expected: u64,
        /// Local length.
        found: u64,
    },
    /// The allocator appears at a different mapping-relative offset.
    ControlOffset {
        /// Recorded offset.
        expected: u64,
        /// Local offset.
        found: u64,
    },
    /// No participant initialized the allocator.
    NotInitialized,
    /// Initialization won but did not publish readiness.
    InitializationInProgress,
    /// Initialization was already completed.
    AlreadyInitialized,
    /// The complete allocator generation must be discarded.
    Poisoned,
    /// The bounded operation lock was not available.
    Busy,
    /// Every allocation slot is occupied.
    Exhausted,
    /// The requested allocation exceeds one configured slot.
    AllocationTooLarge {
        /// Requested bytes.
        requested: usize,
        /// Bytes available in one slot.
        slot_size: usize,
    },
    /// The requested alignment is invalid or exceeds the fixed ceiling.
    UnsupportedAlignment {
        /// Requested alignment.
        requested: usize,
        /// Maximum supported alignment.
        maximum: usize,
    },
    /// Element-count or byte-length arithmetic overflowed.
    LengthOverflow,
    /// A descriptor belongs to another mapping generation.
    WrongRegion {
        /// Region expected by the local allocator.
        expected: u64,
        /// Region carried by the descriptor or mapping.
        found: u64,
    },
    /// A descriptor names a slot beyond this allocator's capacity.
    SlotOutOfRange {
        /// Stored slot index.
        slot: u32,
        /// Local capacity.
        capacity: usize,
    },
    /// A slot was freed or reused after this descriptor was issued.
    StaleGeneration {
        /// Slot index.
        slot: usize,
        /// Generation carried by the descriptor.
        expected: u64,
        /// Current generation.
        found: u64,
    },
    /// Descriptor geometry or type identity disagrees with the request.
    DescriptorMismatch {
        /// Slot index.
        slot: usize,
    },
    /// Bitmap and per-slot metadata disagree.
    CorruptMetadata {
        /// Affected slot.
        slot: usize,
    },
    /// Reuse would wrap a generation and permit a stale descriptor to revive.
    GenerationExhausted {
        /// Affected slot.
        slot: usize,
    },
    /// A locally derived address fails the requested alignment.
    ResolvedMisalignment {
        /// Numeric local address.
        address: usize,
        /// Required alignment.
        required: usize,
    },
    /// A null collection descriptor has no value.
    Empty,
    /// A fixed-capacity vector has no remaining element space.
    CapacityExceeded,
}

impl fmt::Display for RelocError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            Self::NullBase => formatter.write_str("mapping base is null"),
            Self::RegionTooLarge => formatter.write_str("mapping exceeds isize::MAX"),
            Self::AddressOverflow => formatter.write_str("mapping address overflows"),
            Self::AllocatorOutsideRegion => formatter.write_str("allocator is outside the mapping"),
            Self::UnsupportedSlotCount { requested, maximum } => {
                write!(
                    formatter,
                    "slot count {requested} exceeds supported 1..={maximum}"
                )
            }
            Self::ZeroRegionId => formatter.write_str("region identity zero is reserved"),
            Self::SlotSizeAlignment { required } => {
                write!(
                    formatter,
                    "slot size must be a nonzero multiple of {required}"
                )
            }
            Self::ArenaAlignment { required } => {
                write!(formatter, "arena start must be aligned to {required}")
            }
            Self::ArenaOverlapsAllocator => {
                formatter.write_str("arena overlaps allocator metadata")
            }
            Self::ArenaOutOfBounds {
                offset,
                byte_len,
                mapping_len,
            } => write!(
                formatter,
                "arena extent {offset}..{} exceeds mapping length {mapping_len}",
                offset.saturating_add(byte_len)
            ),
            Self::GeometryOverflow => formatter.write_str("allocator geometry overflows"),
            Self::MappingLength { expected, found } => {
                write!(
                    formatter,
                    "mapping length mismatch: expected {expected}, found {found}"
                )
            }
            Self::ControlOffset { expected, found } => write!(
                formatter,
                "allocator control offset mismatch: expected {expected}, found {found}"
            ),
            Self::NotInitialized => formatter.write_str("allocator is not initialized"),
            Self::InitializationInProgress => {
                formatter.write_str("allocator initialization is in progress")
            }
            Self::AlreadyInitialized => formatter.write_str("allocator is already initialized"),
            Self::Poisoned => formatter.write_str("allocator generation is poisoned"),
            Self::Busy => formatter.write_str("allocator operation lock is busy"),
            Self::Exhausted => formatter.write_str("allocator has no free slots"),
            Self::AllocationTooLarge {
                requested,
                slot_size,
            } => write!(
                formatter,
                "allocation of {requested} bytes exceeds slot size {slot_size}"
            ),
            Self::UnsupportedAlignment { requested, maximum } => write!(
                formatter,
                "alignment {requested} is invalid or exceeds maximum {maximum}"
            ),
            Self::LengthOverflow => formatter.write_str("collection length or extent overflows"),
            Self::WrongRegion { expected, found } => {
                write!(
                    formatter,
                    "wrong allocator region: expected {expected}, found {found}"
                )
            }
            Self::SlotOutOfRange { slot, capacity } => {
                write!(
                    formatter,
                    "allocation slot {slot} exceeds capacity {capacity}"
                )
            }
            Self::StaleGeneration {
                slot,
                expected,
                found,
            } => write!(
                formatter,
                "stale allocation generation for slot {slot}: descriptor {expected}, current {found}"
            ),
            Self::DescriptorMismatch { slot } => {
                write!(formatter, "allocation descriptor mismatch for slot {slot}")
            }
            Self::CorruptMetadata { slot } => {
                write!(formatter, "corrupt allocator metadata for slot {slot}")
            }
            Self::GenerationExhausted { slot } => {
                write!(formatter, "generation counter exhausted for slot {slot}")
            }
            Self::ResolvedMisalignment { address, required } => write!(
                formatter,
                "resolved address 0x{address:x} is not aligned to {required}"
            ),
            Self::Empty => formatter.write_str("collection descriptor is empty"),
            Self::CapacityExceeded => formatter.write_str("shared vector capacity exceeded"),
        }
    }
}

impl core::error::Error for RelocError {}
