#![no_std]
#![forbid(unsafe_op_in_unsafe_fn)]
#![deny(missing_docs)]
#![doc = include_str!("../README.md")]

extern crate self as shmem_pod;

#[cfg(feature = "fixed-allocator")]
pub mod fixed_allocator;
pub mod injection;
pub mod layout;
#[cfg(target_has_atomic = "64")]
pub mod mapping;
pub mod offset;
#[cfg(target_has_atomic = "64")]
pub mod snzi;
pub mod sync;

#[cfg(feature = "derive")]
#[doc(inline)]
pub use shmem_pod_macros::{FixedAddressPodValue, PodSync, PodValue};

use core::mem::{align_of, needs_drop, size_of};

/// A structural representation that can be stored in shared memory when every
/// process maps it at the same virtual address.
///
/// This is the weaker storage tier. Unlike [`PodValue`], an implementation may
/// contain process-independent absolute addresses whose validity depends on a
/// fixed-address mapping policy.
///
/// # Safety
///
/// Implementors must have no destructor and every transitive field must meet
/// the same fixed-address storage contract. This is a representation capability
/// only: it does not certify the meaning of scalar fields, arbitrary methods, or
/// external resources. For example, deriving it for an integer field does not
/// make a stored file descriptor meaningful in another process. Executable pod
/// APIs need a separate unsafe audit.
pub unsafe trait FixedAddressPodValue: Sized + 'static {
    /// Structural fingerprint of the exact compiled layout.
    ///
    /// The fingerprint is a compatibility check, not a cryptographic digest.
    /// Image generation must materialize this constant for every accepted type;
    /// doing so evaluates the generated no-drop assertion. Loaders must also
    /// authenticate the complete executable image.
    const FINGERPRINT: u128;
}

/// An address-independent structural representation that can be stored in
/// shared memory mapped at different virtual addresses.
///
/// # Safety
///
/// In addition to [`FixedAddressPodValue`]'s requirements, the stored fields must
/// contain no typed absolute pointers, references, function pointers, vtables,
/// or allocator headers. Links represented by the type must use checked relative
/// offsets. Integer scalars are treated as data by this capability; it does not
/// certify methods or unsafe code that reinterpret an integer as an address.
pub unsafe trait PodValue: FixedAddressPodValue {}

/// Structural capability for shared typed references while the same storage may
/// be accessed by other threads or processes.
///
/// This capability is deliberately separate from [`PodValue`]. A type may have
/// an address-independent representation without supporting concurrent access.
///
/// # Safety
///
/// The type must be Rust [`Sync`] and every transitive field's ordinary typed
/// access must remain data-race-free in process-shared memory. Non-atomic
/// mutation requires a process-shared synchronization field. This structural
/// marker does not audit arbitrary methods or unsafe blocks, make raw concurrent
/// writes safe, or provide crash recovery or owner-death handling.
pub unsafe trait PodSync: FixedAddressPodValue + Sync {}

/// Implementation details used by generated derives.
#[doc(hidden)]
pub mod __private {
    /// Initial state for a structural fingerprint.
    pub const FINGERPRINT_SEED: u128 = 0x6c62_272e_07bb_0142_62b8_2175_6295_c58d;

    const FINGERPRINT_MULTIPLIER: u128 = 0x0000_0000_0100_0000_0000_0000_0000_013b;

    /// Mixes one integer into a structural fingerprint.
    pub const fn mix_u128(state: u128, value: u128) -> u128 {
        state
            .rotate_left(29)
            .wrapping_add(value ^ 0xa076_1d64_78bd_642f_6eed_0e9d_a4d9_4a4f)
            .wrapping_mul(FINGERPRINT_MULTIPLIER)
    }

    /// Mixes one machine-sized integer into a structural fingerprint.
    pub const fn mix_usize(state: u128, value: usize) -> u128 {
        mix_u128(state, value as u128)
    }

    /// Mixes bytes into a structural fingerprint.
    pub const fn mix_bytes(mut state: u128, bytes: &[u8]) -> u128 {
        let mut index = 0;
        while index < bytes.len() {
            state = mix_u128(state, bytes[index] as u128);
            index += 1;
        }
        mix_u128(state, bytes.len() as u128)
    }

    /// Finishes a structural fingerprint.
    pub const fn finish(state: u128) -> u128 {
        let state = state ^ (state >> 47);
        let state = state.wrapping_mul(0x9e37_79b9_7f4a_7c15_6eed_0e9d_a4d9_4a4f);
        state ^ (state >> 53)
    }

    /// Computes a primitive type fingerprint.
    pub const fn primitive(tag: &[u8], size: usize, alignment: usize) -> u128 {
        let state = mix_bytes(FINGERPRINT_SEED, b"shmem-pod-value-primitive-v1");
        let state = mix_bytes(state, tag);
        let state = mix_usize(state, size);
        finish(mix_usize(state, alignment))
    }
}

macro_rules! pod_primitives {
    ($($type:ty => $tag:literal),+ $(,)?) => {$(
        // SAFETY: Primitive scalars have no destructor, allocation, or pointer.
        unsafe impl FixedAddressPodValue for $type {
            const FINGERPRINT: u128 =
                __private::primitive($tag, size_of::<Self>(), align_of::<Self>());
        }

        // SAFETY: Primitive scalar values do not encode an address by type.
        unsafe impl PodValue for $type {}

        // SAFETY: Shared references expose no non-atomic mutation operation.
        unsafe impl PodSync for $type {}
    )+};
}

pod_primitives! {
    () => b"unit",
    bool => b"bool",
    char => b"char",
    u8 => b"u8",
    u16 => b"u16",
    u32 => b"u32",
    u64 => b"u64",
    u128 => b"u128",
    usize => b"usize",
    i8 => b"i8",
    i16 => b"i16",
    i32 => b"i32",
    i64 => b"i64",
    i128 => b"i128",
    isize => b"isize",
    f32 => b"f32",
    f64 => b"f64",
}

// SAFETY: The array has no metadata beyond its elements, and the bound carries
// the fixed-address and no-destructor requirements recursively.
unsafe impl<T: FixedAddressPodValue, const N: usize> FixedAddressPodValue for [T; N] {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod values must not need drop");
        let state = __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-array-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        let state = __private::mix_usize(state, N);
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: Every array element is address-independent by the recursive bound.
unsafe impl<T: PodValue, const N: usize> PodValue for [T; N] {}

// SAFETY: Every array element supports process-shared typed access.
unsafe impl<T: PodSync, const N: usize> PodSync for [T; N] {}

macro_rules! pod_atomic {
    ($cfg:literal, $type:path, $tag:literal) => {
        #[cfg(target_has_atomic = $cfg)]
        // SAFETY: Core atomic scalars have no destructor or stored pointer.
        unsafe impl FixedAddressPodValue for $type {
            const FINGERPRINT: u128 =
                __private::primitive($tag, size_of::<Self>(), align_of::<Self>());
        }

        #[cfg(target_has_atomic = $cfg)]
        // SAFETY: The atomic's stored scalar is address-independent.
        unsafe impl PodValue for $type {}

        #[cfg(target_has_atomic = $cfg)]
        // SAFETY: Core atomics provide race-free process-shared machine operations.
        unsafe impl PodSync for $type {}
    };
}

pod_atomic!("8", core::sync::atomic::AtomicBool, b"AtomicBool");
pod_atomic!("8", core::sync::atomic::AtomicU8, b"AtomicU8");
pod_atomic!("8", core::sync::atomic::AtomicI8, b"AtomicI8");
pod_atomic!("16", core::sync::atomic::AtomicU16, b"AtomicU16");
pod_atomic!("16", core::sync::atomic::AtomicI16, b"AtomicI16");
pod_atomic!("32", core::sync::atomic::AtomicU32, b"AtomicU32");
pod_atomic!("32", core::sync::atomic::AtomicI32, b"AtomicI32");
pod_atomic!("64", core::sync::atomic::AtomicU64, b"AtomicU64");
pod_atomic!("64", core::sync::atomic::AtomicI64, b"AtomicI64");
pod_atomic!("ptr", core::sync::atomic::AtomicUsize, b"AtomicUsize");
pod_atomic!("ptr", core::sync::atomic::AtomicIsize, b"AtomicIsize");
