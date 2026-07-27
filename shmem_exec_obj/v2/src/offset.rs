//! Typed, bounds-checked relative offsets for relocatable shared state.
//!
//! An [`Offset`] stores no address. Each process resolves it relative to its own
//! mapping through a [`PodRegion`], so the same state can be mapped at different
//! virtual addresses.

use core::marker::PhantomData;
use core::mem::{align_of, offset_of, size_of};
use core::ptr::NonNull;

use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const NULL_OFFSET: u64 = u64::MAX;

/// A nullable relative offset to one `T` in the same shared region.
///
/// `u64::MAX` is the null encoding, leaving offset zero available for an object
/// at the start of a mapping. The type parameter has no runtime representation.
#[repr(transparent)]
pub struct Offset<T> {
    raw: u64,
    marker: PhantomData<fn() -> T>,
}

impl<T> Offset<T> {
    /// Creates an offset from a byte displacement relative to a region base.
    ///
    /// Returns `None` only when the displacement is reserved as the null value.
    pub const fn new(bytes: u64) -> Option<Self> {
        if bytes == NULL_OFFSET {
            None
        } else {
            Some(Self::from_raw(bytes))
        }
    }

    /// Creates a null offset.
    pub const fn null() -> Self {
        Self::from_raw(NULL_OFFSET)
    }

    /// Reconstructs an offset from its stored integer representation.
    ///
    /// Every bit pattern is accepted; resolution still checks null, target
    /// width, alignment, and mapping bounds.
    pub const fn from_raw(raw: u64) -> Self {
        Self {
            raw,
            marker: PhantomData,
        }
    }

    /// Returns the stored integer representation.
    pub const fn to_raw(self) -> u64 {
        self.raw
    }

    /// Returns whether this offset has the null encoding.
    pub const fn is_null(self) -> bool {
        self.raw == NULL_OFFSET
    }

    /// Changes the compile-time target type without changing the displacement.
    ///
    /// Resolution performs the new type's alignment and extent checks. The
    /// mapping contract must still guarantee that the target contains a valid
    /// initialized value of the new type.
    ///
    /// # Safety
    ///
    /// Any later resolution must target a properly initialized value of `U`.
    pub const unsafe fn cast<U>(self) -> Offset<U> {
        Offset::from_raw(self.raw)
    }
}

impl<T> Clone for Offset<T> {
    fn clone(&self) -> Self {
        *self
    }
}

impl<T> Copy for Offset<T> {}

impl<T> core::fmt::Debug for Offset<T> {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        if self.is_null() {
            formatter.write_str("Offset(NULL)")
        } else {
            formatter.debug_tuple("Offset").field(&self.raw).finish()
        }
    }
}

impl<T> PartialEq for Offset<T> {
    fn eq(&self, other: &Self) -> bool {
        self.raw == other.raw
    }
}

impl<T> Eq for Offset<T> {}

/// A nullable relative offset and element count describing a `[T]`.
pub struct OffsetSlice<T> {
    offset: Offset<T>,
    len: u64,
}

impl<T> OffsetSlice<T> {
    /// Creates a non-null slice descriptor.
    pub const fn new(offset: Offset<T>, len: u64) -> Option<Self> {
        if offset.is_null() {
            None
        } else {
            Some(Self { offset, len })
        }
    }

    /// Creates a null slice descriptor.
    pub const fn null() -> Self {
        Self {
            offset: Offset::null(),
            len: 0,
        }
    }

    /// Reconstructs a descriptor from stored integer fields.
    ///
    /// Resolution rejects a null offset paired with a nonzero length.
    pub const fn from_raw(offset: u64, len: u64) -> Self {
        Self {
            offset: Offset::from_raw(offset),
            len,
        }
    }

    /// Returns the start offset.
    pub const fn offset(self) -> Offset<T> {
        self.offset
    }

    /// Returns the number of elements.
    pub const fn len(self) -> u64 {
        self.len
    }

    /// Returns whether this descriptor is null.
    pub const fn is_null(self) -> bool {
        self.offset.is_null()
    }

    /// Returns whether this descriptor contains zero elements.
    pub const fn is_empty(self) -> bool {
        self.len == 0
    }

    /// Returns the stored `(offset, length)` fields.
    pub const fn to_raw(self) -> (u64, u64) {
        (self.offset.to_raw(), self.len)
    }
}

impl<T> Clone for OffsetSlice<T> {
    fn clone(&self) -> Self {
        *self
    }
}

impl<T> Copy for OffsetSlice<T> {}

impl<T> core::fmt::Debug for OffsetSlice<T> {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("OffsetSlice")
            .field("offset", &self.offset)
            .field("len", &self.len)
            .finish()
    }
}

impl<T> PartialEq for OffsetSlice<T> {
    fn eq(&self, other: &Self) -> bool {
        self.offset == other.offset && self.len == other.len
    }
}

impl<T> Eq for OffsetSlice<T> {}

/// A checked view of one live shared mapping.
///
/// The view stores a process-local base pointer and is therefore not itself a
/// [`PodValue`]. Only integer [`Offset`] and [`OffsetSlice`] descriptors belong
/// in relocatable shared state.
pub struct PodRegion<'a> {
    base: NonNull<u8>,
    len: usize,
    marker: PhantomData<&'a [u8]>,
}

impl PodRegion<'_> {
    /// Creates a resolver for `base..base + len`.
    ///
    /// # Safety
    ///
    /// The range must be one live allocation or mapping for `'a`, must not move,
    /// and must be readable. The typed resolution methods have additional
    /// safety contracts for value validity and cross-process synchronization.
    pub unsafe fn from_raw_parts(base: *mut u8, len: usize) -> Result<Self, ResolveError> {
        let base = NonNull::new(base).ok_or(ResolveError::NullRegion)?;
        if len > isize::MAX as usize {
            return Err(ResolveError::RegionTooLarge);
        }
        (base.as_ptr() as usize)
            .checked_add(len)
            .ok_or(ResolveError::AddressOverflow)?;
        Ok(Self {
            base,
            len,
            marker: PhantomData,
        })
    }

    /// Returns this process's mapping base.
    pub const fn base(&self) -> NonNull<u8> {
        self.base
    }

    /// Returns the complete accessible mapping length.
    pub const fn len(&self) -> usize {
        self.len
    }

    /// Returns whether the mapping has zero accessible bytes.
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Resolves a nullable typed offset after alignment and extent checks.
    ///
    /// # Safety
    ///
    /// A non-null target must contain a properly initialized, valid `T` for the
    /// returned borrow. Every conflicting process access must follow `T`'s
    /// synchronization protocol.
    pub unsafe fn get<T: PodValue>(&self, offset: Offset<T>) -> Result<Option<&T>, ResolveError> {
        let Some(address) = self.address(offset.to_raw(), size_of::<T>(), align_of::<T>())? else {
            return Ok(None);
        };
        // SAFETY: address() established alignment and complete mapping bounds;
        // the unsafe constructor contract establishes initialized T validity.
        Ok(Some(unsafe { &*address.cast::<T>().as_ptr() }))
    }

    /// Resolves a nullable typed slice after all size, alignment, and extent checks.
    ///
    /// # Safety
    ///
    /// A non-null target must contain `len` properly initialized, valid `T`
    /// values for the returned borrow. Every conflicting process access must
    /// follow `T`'s synchronization protocol.
    pub unsafe fn get_slice<T: PodValue>(
        &self,
        slice: OffsetSlice<T>,
    ) -> Result<Option<&[T]>, ResolveError> {
        if slice.offset.is_null() {
            return if slice.len == 0 {
                Ok(None)
            } else {
                Err(ResolveError::NullWithNonzeroLength)
            };
        }
        let len = usize::try_from(slice.len).map_err(|_| ResolveError::LengthOverflow)?;
        if len > isize::MAX as usize {
            return Err(ResolveError::LengthOverflow);
        }
        let bytes = size_of::<T>()
            .checked_mul(len)
            .ok_or(ResolveError::LengthOverflow)?;
        if bytes > isize::MAX as usize {
            return Err(ResolveError::LengthOverflow);
        }
        let address = self
            .address(slice.offset.to_raw(), bytes, align_of::<T>())?
            .expect("non-null offset was checked above");
        // SAFETY: address() checked the complete slice extent and alignment;
        // the unsafe constructor contract establishes element validity.
        Ok(Some(unsafe {
            core::slice::from_raw_parts(address.cast::<T>().as_ptr(), len)
        }))
    }

    /// Resolves a mutable value after alignment and extent checks.
    ///
    /// # Safety
    ///
    /// A non-null target must contain a properly initialized, valid `T` and be
    /// writable. For the returned borrow, the caller must exclude every access
    /// that could conflict with mutable `T` access, including access from other
    /// processes.
    pub unsafe fn get_mut<T: PodValue>(
        &mut self,
        offset: Offset<T>,
    ) -> Result<Option<&mut T>, ResolveError> {
        let Some(address) = self.address(offset.to_raw(), size_of::<T>(), align_of::<T>())? else {
            return Ok(None);
        };
        // SAFETY: address() and the constructor establish pointer validity; the
        // caller establishes exclusive access for this borrow.
        Ok(Some(unsafe { &mut *address.cast::<T>().as_ptr() }))
    }

    /// Resolves a mutable slice after all size, alignment, and extent checks.
    ///
    /// # Safety
    ///
    /// A non-null target must contain `len` properly initialized, valid `T`
    /// values and be writable. For the returned borrow, the caller must exclude
    /// every access that could conflict with mutable `[T]` access, including
    /// access from other processes.
    pub unsafe fn get_slice_mut<T: PodValue>(
        &mut self,
        slice: OffsetSlice<T>,
    ) -> Result<Option<&mut [T]>, ResolveError> {
        if slice.offset.is_null() {
            return if slice.len == 0 {
                Ok(None)
            } else {
                Err(ResolveError::NullWithNonzeroLength)
            };
        }
        let len = usize::try_from(slice.len).map_err(|_| ResolveError::LengthOverflow)?;
        if len > isize::MAX as usize {
            return Err(ResolveError::LengthOverflow);
        }
        let bytes = size_of::<T>()
            .checked_mul(len)
            .ok_or(ResolveError::LengthOverflow)?;
        if bytes > isize::MAX as usize {
            return Err(ResolveError::LengthOverflow);
        }
        let address = self
            .address(slice.offset.to_raw(), bytes, align_of::<T>())?
            .expect("non-null offset was checked above");
        // SAFETY: bounds and initialized validity follow from the resolver;
        // the caller guarantees exclusive cross-process access.
        Ok(Some(unsafe {
            core::slice::from_raw_parts_mut(address.cast::<T>().as_ptr(), len)
        }))
    }

    fn address(
        &self,
        raw_offset: u64,
        byte_len: usize,
        alignment: usize,
    ) -> Result<Option<NonNull<u8>>, ResolveError> {
        if raw_offset == NULL_OFFSET {
            return Ok(None);
        }
        let offset = usize::try_from(raw_offset).map_err(|_| ResolveError::OffsetTooLarge)?;
        let end = offset
            .checked_add(byte_len)
            .ok_or(ResolveError::ExtentOverflow)?;
        if end > self.len {
            return Err(ResolveError::OutOfBounds {
                offset: raw_offset,
                byte_len,
                region_len: self.len,
            });
        }
        // SAFETY: offset <= end <= len, and the constructor limits len to
        // isize::MAX within one live mapping.
        let address = unsafe { self.base.as_ptr().add(offset) };
        if address as usize % alignment != 0 {
            return Err(ResolveError::Misaligned {
                address: address as usize,
                required: alignment,
            });
        }
        Ok(Some(
            NonNull::new(address).expect("non-null base plus in-bounds offset"),
        ))
    }
}

/// Failure to construct a region or resolve a relative descriptor.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResolveError {
    /// A region base was null.
    NullRegion,
    /// The region exceeds Rust's maximum allocation/object extent.
    RegionTooLarge,
    /// Computing the region's numeric end address overflowed.
    AddressOverflow,
    /// A stored offset cannot be represented as `usize` on this target.
    OffsetTooLarge,
    /// Adding the target byte length to its offset overflowed.
    ExtentOverflow,
    /// A slice's element count or byte length overflowed supported limits.
    LengthOverflow,
    /// A null slice descriptor carried a nonzero element count.
    NullWithNonzeroLength,
    /// The target address does not meet its Rust type's alignment.
    Misaligned {
        /// Numeric target address.
        address: usize,
        /// Required byte alignment.
        required: usize,
    },
    /// The complete target extent is not inside the region.
    OutOfBounds {
        /// Stored byte offset.
        offset: u64,
        /// Requested target size in bytes.
        byte_len: usize,
        /// Accessible region size in bytes.
        region_len: usize,
    },
}

impl core::fmt::Display for ResolveError {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::NullRegion => formatter.write_str("shared region base is null"),
            Self::RegionTooLarge => formatter.write_str("shared region exceeds isize::MAX"),
            Self::AddressOverflow => formatter.write_str("shared region address overflows usize"),
            Self::OffsetTooLarge => formatter.write_str("stored offset does not fit usize"),
            Self::ExtentOverflow => formatter.write_str("resolved target extent overflows usize"),
            Self::LengthOverflow => formatter.write_str("resolved slice length is too large"),
            Self::NullWithNonzeroLength => {
                formatter.write_str("null slice offset has a nonzero length")
            }
            Self::Misaligned { address, required } => write!(
                formatter,
                "resolved address 0x{address:x} is not aligned to {required} bytes"
            ),
            Self::OutOfBounds {
                offset,
                byte_len,
                region_len,
            } => write!(
                formatter,
                "resolved extent offset={offset} size={byte_len} exceeds region length {region_len}"
            ),
        }
    }
}

impl core::error::Error for ResolveError {}

// SAFETY: Offset contains only a u64 displacement and zero-sized type marker.
unsafe impl<T: FixedAddressPodValue> FixedAddressPodValue for Offset<T> {
    const FINGERPRINT: u128 = {
        let state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-relative-offset-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let mut state = __private::mix_usize(state, align_of::<Self>());

        state = __private::mix_bytes(state, b"raw");
        state = __private::mix_usize(state, offset_of!(Self, raw));
        state = __private::mix_usize(state, size_of::<u64>());
        state = __private::mix_usize(state, align_of::<u64>());
        state = __private::mix_u128(state, u64::FINGERPRINT);

        state = __private::mix_bytes(state, b"marker");
        state = __private::mix_usize(state, offset_of!(Self, marker));
        state = __private::mix_usize(state, size_of::<PhantomData<fn() -> T>>());
        state = __private::mix_usize(state, align_of::<PhantomData<fn() -> T>>());
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: Offset stores no absolute address.
unsafe impl<T: PodValue> PodValue for Offset<T> {}

// SAFETY: Offset exposes no mutation and consists of a scalar word.
unsafe impl<T: FixedAddressPodValue> PodSync for Offset<T> {}

// SAFETY: OffsetSlice consists only of two scalar words and a zero-sized marker.
unsafe impl<T: FixedAddressPodValue> FixedAddressPodValue for OffsetSlice<T> {
    const FINGERPRINT: u128 = {
        let state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-relative-slice-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let mut state = __private::mix_usize(state, align_of::<Self>());

        state = __private::mix_bytes(state, b"offset");
        state = __private::mix_usize(state, offset_of!(Self, offset));
        state = __private::mix_usize(state, size_of::<Offset<T>>());
        state = __private::mix_usize(state, align_of::<Offset<T>>());
        state = __private::mix_u128(state, Offset::<T>::FINGERPRINT);

        state = __private::mix_bytes(state, b"len");
        state = __private::mix_usize(state, offset_of!(Self, len));
        state = __private::mix_usize(state, size_of::<u64>());
        state = __private::mix_usize(state, align_of::<u64>());
        __private::finish(__private::mix_u128(state, u64::FINGERPRINT))
    };
}

// SAFETY: OffsetSlice stores no absolute address.
unsafe impl<T: PodValue> PodValue for OffsetSlice<T> {}

// SAFETY: OffsetSlice exposes no mutation and consists of scalar words.
unsafe impl<T: FixedAddressPodValue> PodSync for OffsetSlice<T> {}
