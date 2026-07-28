//! Pointer-free owning descriptors for relocatable shared allocations.
//!
//! [`SharedBox`] and [`SharedVec`] persist only integer allocation metadata.
//! They intentionally have no `Drop`: a destructor in one process cannot prove
//! that another process has stopped using the allocation. Callers must close
//! admission through their surrounding shared-object lifecycle, establish
//! exclusive cross-process access, and then invoke the explicit unsafe
//! destruction methods.
//!
//! Resolution is checked on every call. It rejects another region, a stale
//! generation, a changed offset or extent, a layout mismatch, allocator
//! corruption, and an allocator transaction which is still active or was
//! abandoned by a dead process.

use core::marker::PhantomData;
use core::mem::{align_of, needs_drop, size_of};
use core::slice;

use crate::layout::LayoutDescriptor;
use crate::reloc_allocator::{AllocationDescriptor, RelocError, RelocRegion};
use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

struct BoxAllocation<T>(PhantomData<T>);
struct VecAllocation<T>(PhantomData<T>);

// SAFETY: these zero-sized tags exist only to create collection-specific exact
// layout identities. They are never persisted as values or resolved.
unsafe impl<T: PodValue> FixedAddressPodValue for BoxAllocation<T> {
    const FINGERPRINT: u128 = {
        let state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-shared-box-allocation-v1",
        );
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: see BoxAllocation.
unsafe impl<T: PodValue> FixedAddressPodValue for VecAllocation<T> {
    const FINGERPRINT: u128 = {
        let state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-shared-vec-allocation-v1",
        );
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

fn box_layout<T: PodValue>() -> LayoutDescriptor {
    LayoutDescriptor::of::<BoxAllocation<T>>()
}

fn vec_layout<T: PodValue>() -> LayoutDescriptor {
    LayoutDescriptor::of::<VecAllocation<T>>()
}

/// Pointer-free ownership descriptor for one initialized shared `T`.
///
/// This type has no destructor. Moving it moves logical ownership, but raw or
/// cross-process copies can still exist. Explicit destruction is therefore
/// unsafe and requires a surrounding admission/drain protocol.
pub struct SharedBox<T: PodValue> {
    allocation: AllocationDescriptor,
    marker: PhantomData<T>,
}

impl<T: PodValue> SharedBox<T> {
    /// Allocates and initializes `value` directly in one shared slot.
    pub fn new<const SLOTS: usize>(
        region: &RelocRegion<'_, SLOTS>,
        value: T,
    ) -> Result<Self, RelocError> {
        // SAFETY: ptr::write moves one complete valid T into the destination and
        // neither reads nor leaks the uninitialized pointer.
        unsafe { Self::try_new_in_place(region, |target| target.write(value)) }
    }

    /// Allocates one slot and initializes `T` at its final shared address.
    ///
    /// Unwinding poisons the allocator and releases its local lock. Process death
    /// leaves the bounded operation lock held; later operations return `Busy`
    /// until a supervisor poisons and replaces the complete generation.
    ///
    /// # Safety
    ///
    /// Before returning normally, `initialize` must write exactly one valid,
    /// fully initialized `T` without reading the destination. It must not leak
    /// the pointer or access the same slot through another handle. Abort,
    /// `execve`, or process death during the callback requires whole-generation
    /// restart rather than reuse.
    pub unsafe fn try_new_in_place<const SLOTS: usize>(
        region: &RelocRegion<'_, SLOTS>,
        initialize: impl FnOnce(*mut T),
    ) -> Result<Self, RelocError> {
        let allocation = region.allocate_with(
            box_layout::<T>(),
            size_of::<T>(),
            align_of::<T>(),
            |target| initialize(target.cast::<T>()),
        )?;
        Ok(Self {
            allocation,
            marker: PhantomData,
        })
    }

    /// Reconstructs an owning descriptor copied through authenticated storage.
    ///
    /// # Safety
    ///
    /// `allocation` must have been produced for a live `SharedBox<T>` allocation
    /// in the supplied region generation. The caller must account for every raw
    /// or cross-process duplicate and ensure that at most one is ever explicitly
    /// destroyed. Resolution still checks all integer metadata before access.
    pub const unsafe fn from_descriptor(allocation: AllocationDescriptor) -> Self {
        Self {
            allocation,
            marker: PhantomData,
        }
    }

    /// Returns a copyable integer-only allocation descriptor.
    pub const fn descriptor(&self) -> AllocationDescriptor {
        self.allocation
    }

    /// Resolves a checked shared reference in this process's mapping.
    pub fn get<'a, const SLOTS: usize>(
        &'a self,
        region: &'a RelocRegion<'_, SLOTS>,
    ) -> Result<&'a T, RelocError> {
        if self.allocation.is_null() {
            return Err(RelocError::Empty);
        }
        let pointer = region.resolve::<T>(self.allocation, 1, box_layout::<T>())?;
        // SAFETY: allocation publication initialized T; validation checked its
        // live generation, collection-specific fingerprint, extent and alignment.
        Ok(unsafe { pointer.as_ref() })
    }

    /// Resolves a checked mutable reference under exclusive cross-process access.
    ///
    /// # Safety
    ///
    /// The caller must exclude every other access to this allocation for the
    /// returned borrow, including access through raw descriptor copies in other
    /// processes.
    pub unsafe fn get_mut<'a, const SLOTS: usize>(
        &'a mut self,
        region: &'a mut RelocRegion<'_, SLOTS>,
    ) -> Result<&'a mut T, RelocError> {
        if self.allocation.is_null() {
            return Err(RelocError::Empty);
        }
        let mut pointer = region.resolve::<T>(self.allocation, 1, box_layout::<T>())?;
        // SAFETY: validation proves pointer validity and the caller provides
        // exclusive cross-process access.
        Ok(unsafe { pointer.as_mut() })
    }

    /// Moves out the value, invalidates the slot generation, and empties `self`.
    ///
    /// # Safety
    ///
    /// The caller must have closed admission and excluded every reference and
    /// duplicate descriptor in every process. The mapping must remain live until
    /// this operation completes. On success, stale copies are rejected by the
    /// incremented generation.
    pub unsafe fn destroy<const SLOTS: usize>(
        &mut self,
        region: &mut RelocRegion<'_, SLOTS>,
    ) -> Result<T, RelocError> {
        if self.allocation.is_null() {
            return Err(RelocError::Empty);
        }
        // SAFETY: forwarded exclusive destruction contract.
        let value = unsafe { region.take_value::<T>(self.allocation, box_layout::<T>())? };
        self.allocation = AllocationDescriptor::null();
        Ok(value)
    }
}

impl<T: PodValue> core::fmt::Debug for SharedBox<T> {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("SharedBox")
            .field("allocation", &self.allocation)
            .finish_non_exhaustive()
    }
}

// SAFETY: SharedBox stores only an integer descriptor and a zero-sized ownership
// marker. T is restricted to the relocatable storage tier.
unsafe impl<T: PodValue> FixedAddressPodValue for SharedBox<T> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "shared boxes must not need drop");
        let state = __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-shared-box-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: no absolute address is persisted.
unsafe impl<T: PodValue> PodValue for SharedBox<T> {}

// SAFETY: shared methods expose only &T, and T supports process-shared access.
unsafe impl<T: PodValue + PodSync> PodSync for SharedBox<T> {}

/// Pointer-free fixed-capacity vector stored in one allocator slot.
///
/// Capacity is chosen once. Element mutation and explicit destruction require
/// exclusive cross-process access. The descriptor itself has no `Drop` and
/// contains no persisted pointer or allocator reference.
pub struct SharedVec<T: PodValue> {
    allocation: AllocationDescriptor,
    len: u64,
    capacity: u64,
    marker: PhantomData<T>,
}

impl<T: PodValue> SharedVec<T> {
    /// Creates an empty zero-capacity descriptor without allocating.
    pub const fn new() -> Self {
        Self {
            allocation: AllocationDescriptor::null(),
            len: 0,
            capacity: 0,
            marker: PhantomData,
        }
    }

    /// Allocates room for exactly `capacity` elements in one shared slot.
    pub fn with_capacity<const SLOTS: usize>(
        region: &RelocRegion<'_, SLOTS>,
        capacity: usize,
    ) -> Result<Self, RelocError> {
        if capacity == 0 {
            return Ok(Self::new());
        }
        if capacity > isize::MAX as usize {
            return Err(RelocError::LengthOverflow);
        }
        let byte_len = size_of::<T>()
            .checked_mul(capacity)
            .ok_or(RelocError::LengthOverflow)?;
        let allocation =
            region.allocate_with(vec_layout::<T>(), byte_len, align_of::<T>(), |_| {})?;
        Ok(Self {
            allocation,
            len: 0,
            capacity: capacity as u64,
            marker: PhantomData,
        })
    }

    /// Reconstructs a vector descriptor copied through authenticated storage.
    ///
    /// # Safety
    ///
    /// The allocation must have been created for `SharedVec<T>` with this exact
    /// capacity, and the first `len` elements must be initialized valid `T`s.
    /// The caller must account for every duplicate so explicit mutation and
    /// destruction remain exclusive. Resolution rechecks allocator metadata.
    pub const unsafe fn from_raw_parts(
        allocation: AllocationDescriptor,
        len: u64,
        capacity: u64,
    ) -> Self {
        Self {
            allocation,
            len,
            capacity,
            marker: PhantomData,
        }
    }

    /// Returns the current element count.
    pub const fn len(&self) -> usize {
        self.len as usize
    }

    /// Returns whether the vector has no initialized elements.
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Returns the fixed element capacity.
    pub const fn capacity(&self) -> usize {
        self.capacity as usize
    }

    /// Returns the integer-only allocation descriptor.
    pub const fn descriptor(&self) -> AllocationDescriptor {
        self.allocation
    }

    /// Returns the persisted raw `(allocation, length, capacity)` fields.
    pub const fn raw_parts(&self) -> (AllocationDescriptor, u64, u64) {
        (self.allocation, self.len, self.capacity)
    }

    /// Resolves the initialized prefix as a checked shared slice.
    pub fn as_slice<'a, const SLOTS: usize>(
        &'a self,
        region: &'a RelocRegion<'_, SLOTS>,
    ) -> Result<&'a [T], RelocError> {
        let (len, capacity) = self.checked_lengths()?;
        if capacity == 0 {
            return Ok(&[]);
        }
        let pointer = region.resolve::<T>(self.allocation, capacity, vec_layout::<T>())?;
        // SAFETY: the constructor/mutator contract initializes exactly the
        // prefix, and allocator validation proves the complete capacity extent.
        Ok(unsafe { slice::from_raw_parts(pointer.as_ptr(), len) })
    }

    /// Resolves the initialized prefix mutably under exclusive access.
    ///
    /// # Safety
    ///
    /// The caller must exclude every other access to the vector elements and
    /// descriptor, including access in another process.
    pub unsafe fn as_mut_slice<'a, const SLOTS: usize>(
        &'a mut self,
        region: &'a mut RelocRegion<'_, SLOTS>,
    ) -> Result<&'a mut [T], RelocError> {
        let (len, capacity) = self.checked_lengths()?;
        if capacity == 0 {
            return Ok(&mut []);
        }
        let pointer = region.resolve::<T>(self.allocation, capacity, vec_layout::<T>())?;
        // SAFETY: allocator validation and the caller's exclusivity establish a
        // valid mutable initialized-prefix slice.
        Ok(unsafe { slice::from_raw_parts_mut(pointer.as_ptr(), len) })
    }

    /// Appends one value without changing the fixed capacity.
    ///
    /// # Safety
    ///
    /// The caller must hold exclusive cross-process access to the vector and its
    /// element allocation for the operation.
    pub unsafe fn push<const SLOTS: usize>(
        &mut self,
        region: &mut RelocRegion<'_, SLOTS>,
        value: T,
    ) -> Result<(), RelocError> {
        let (len, capacity) = self.checked_lengths()?;
        if len == capacity {
            return Err(RelocError::CapacityExceeded);
        }
        let pointer = region.resolve::<T>(self.allocation, capacity, vec_layout::<T>())?;
        // SAFETY: len < capacity and the caller excludes conflicting access. The
        // destination is the first uninitialized vector element.
        unsafe { pointer.as_ptr().add(len).write(value) };
        self.len += 1;
        Ok(())
    }

    /// Removes and returns the last value.
    ///
    /// # Safety
    ///
    /// The caller must hold exclusive cross-process access to the vector and its
    /// element allocation for the operation.
    pub unsafe fn pop<const SLOTS: usize>(
        &mut self,
        region: &mut RelocRegion<'_, SLOTS>,
    ) -> Result<Option<T>, RelocError> {
        let (len, capacity) = self.checked_lengths()?;
        if len == 0 {
            return Ok(None);
        }
        let pointer = region.resolve::<T>(self.allocation, capacity, vec_layout::<T>())?;
        let new_len = len - 1;
        // SAFETY: the initialized prefix includes new_len, and the caller holds
        // exclusive access. PodValue has no destructor.
        let value = unsafe { pointer.as_ptr().add(new_len).read() };
        self.len = new_len as u64;
        Ok(Some(value))
    }

    /// Invalidates the allocation generation and empties the descriptor.
    ///
    /// # Safety
    ///
    /// Admission must be closed and every reference or duplicate descriptor in
    /// every process must be excluded. `T: PodValue` has no destructor, so no
    /// per-element drop is required.
    pub unsafe fn destroy<const SLOTS: usize>(
        &mut self,
        region: &mut RelocRegion<'_, SLOTS>,
    ) -> Result<(), RelocError> {
        let (_, capacity) = self.checked_lengths()?;
        if capacity == 0 {
            self.len = 0;
            return Ok(());
        }
        let byte_len = size_of::<T>()
            .checked_mul(capacity)
            .ok_or(RelocError::LengthOverflow)?;
        // SAFETY: forwarded exclusive destruction contract.
        unsafe {
            region.release(
                self.allocation,
                vec_layout::<T>(),
                byte_len,
                align_of::<T>(),
            )?;
        }
        self.allocation = AllocationDescriptor::null();
        self.len = 0;
        self.capacity = 0;
        Ok(())
    }

    fn checked_lengths(&self) -> Result<(usize, usize), RelocError> {
        let len = usize::try_from(self.len).map_err(|_| RelocError::LengthOverflow)?;
        let capacity = usize::try_from(self.capacity).map_err(|_| RelocError::LengthOverflow)?;
        if capacity > isize::MAX as usize || len > capacity {
            return Err(RelocError::LengthOverflow);
        }
        if capacity == 0 {
            if len != 0 || !self.allocation.is_null() {
                return Err(RelocError::DescriptorMismatch { slot: usize::MAX });
            }
        } else if self.allocation.is_null() {
            return Err(RelocError::DescriptorMismatch { slot: usize::MAX });
        }
        Ok((len, capacity))
    }
}

impl<T: PodValue> Default for SharedVec<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: PodValue> core::fmt::Debug for SharedVec<T> {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("SharedVec")
            .field("allocation", &self.allocation)
            .field("len", &self.len)
            .field("capacity", &self.capacity)
            .finish_non_exhaustive()
    }
}

// SAFETY: SharedVec persists only integer descriptor/length fields and a
// zero-sized ownership marker. T is restricted to the relocatable tier.
unsafe impl<T: PodValue> FixedAddressPodValue for SharedVec<T> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "shared vectors must not need drop");
        let state = __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-shared-vec-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: no absolute address is persisted.
unsafe impl<T: PodValue> PodValue for SharedVec<T> {}

// SAFETY: shared methods expose only &[T], and T supports process-shared access.
// Mutation remains behind an explicit unsafe exclusive-access contract.
unsafe impl<T: PodValue + PodSync> PodSync for SharedVec<T> {}
