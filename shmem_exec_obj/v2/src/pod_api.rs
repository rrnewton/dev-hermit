//! Declarative metadata for executable pod method tables.
//!
//! The native entry points themselves use C function ABIs. This module keeps
//! their numeric identities and signatures independent from Rust source order
//! and provides the narrow resolver boundary used by generated bindings.

use core::fmt;
use core::ptr::NonNull;

/// Stable numeric identity of a supported native pod method signature.
///
/// The initial ABI deliberately contains only the scalar shapes exercised by
/// the executable-object prototype. New shapes require a new explicit value;
/// arbitrary Rust types are never inferred as C-compatible.
#[repr(u16)]
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum MethodSignature {
    /// `unsafe extern "C" fn() -> u64`.
    NoArgsU64 = 1,
    /// `unsafe extern "C" fn(*mut u8, u64) -> i32`.
    StateU64Status = 2,
    /// `unsafe extern "C" fn(*mut u8, u64, u64) -> i32`.
    StateU64U64Status = 3,
    /// `unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32`.
    StateU64OutU64Status = 4,
    /// `unsafe extern "C" fn(*mut u8) -> u64`.
    StateU64 = 5,
}

impl MethodSignature {
    /// Decodes a wire signature identifier.
    pub const fn from_u16(value: u16) -> Option<Self> {
        Some(match value {
            1 => Self::NoArgsU64,
            2 => Self::StateU64Status,
            3 => Self::StateU64U64Status,
            4 => Self::StateU64OutU64Status,
            5 => Self::StateU64,
            _ => return None,
        })
    }
}

/// One generated method declaration before it is assigned an image offset.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MethodSpec {
    /// Stable nonzero method identifier.
    pub id: u32,
    /// Exact C calling signature expected at the entry point.
    pub signature: MethodSignature,
    /// Global ELF symbol which the image compiler must resolve.
    pub symbol: &'static str,
}

/// Generated description of one complete pod API.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PodApiDescriptor {
    /// Human-readable namespace included in the fingerprint.
    pub namespace: &'static str,
    /// Order-independent fingerprint of the namespace, IDs, and signatures.
    pub fingerprint: u128,
    /// Methods sorted by ascending numeric ID.
    pub methods: &'static [MethodSpec],
}

impl PodApiDescriptor {
    /// Finds a method by stable numeric identifier.
    pub fn method(&self, id: u32) -> Option<&MethodSpec> {
        self.methods
            .binary_search_by_key(&id, |method| method.id)
            .ok()
            .map(|index| &self.methods[index])
    }
}

/// Failure to bind generated typed entries to an executable image.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BindError {
    /// The image has no entry with the requested numeric ID.
    MissingMethod {
        /// Requested method ID.
        id: u32,
    },
    /// The image entry exists but declares a different C signature.
    SignatureMismatch {
        /// Requested method ID.
        id: u32,
        /// Signature generated into the caller.
        expected: MethodSignature,
        /// Signature declared by the loaded image.
        actual: MethodSignature,
    },
}

impl fmt::Display for BindError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingMethod { id } => write!(formatter, "pod method ID {id} is absent"),
            Self::SignatureMismatch {
                id,
                expected,
                actual,
            } => write!(
                formatter,
                "pod method ID {id} has signature {actual:?}, expected {expected:?}"
            ),
        }
    }
}

impl core::error::Error for BindError {}

/// Resolves authenticated method entries for generated typed bindings.
///
/// # Safety
///
/// An implementation must return an executable entry point which remains live
/// for every generated binding made from it and which obeys the requested C
/// signature. Checking only an address range is insufficient: the containing
/// image must be authenticated and its immutable descriptor must bind the
/// numeric ID to the signature.
pub unsafe trait MethodResolver {
    /// Resolves one method after checking its declared signature.
    fn resolve(&self, id: u32, signature: MethodSignature) -> Result<NonNull<()>, BindError>;
}

/// Implementation helpers used by the `#[pod]` macro.
#[doc(hidden)]
pub mod __private {
    /// Initial API fingerprint state.
    pub const API_FINGERPRINT_SEED: u128 = 0x95e4_78fc_a75b_1967_25b3_96a2_b04d_f711;

    /// Mixes one integer into an API fingerprint.
    pub const fn mix_u128(state: u128, value: u128) -> u128 {
        state
            .rotate_left(31)
            .wrapping_add(value ^ 0x517c_c1b7_2722_0a95_6eed_0e9d_a4d9_4a4f)
            .wrapping_mul(0x0000_0000_0100_0000_0000_0000_0000_013b)
    }

    /// Mixes a byte string into an API fingerprint.
    pub const fn mix_bytes(mut state: u128, bytes: &[u8]) -> u128 {
        let mut index = 0;
        while index < bytes.len() {
            state = mix_u128(state, bytes[index] as u128);
            index += 1;
        }
        mix_u128(state, bytes.len() as u128)
    }

    /// Finalizes an API fingerprint.
    pub const fn finish(state: u128) -> u128 {
        let state = state ^ (state >> 43);
        let state = state.wrapping_mul(0x9e37_79b9_7f4a_7c15_6eed_0e9d_a4d9_4a4f);
        state ^ (state >> 57)
    }
}
