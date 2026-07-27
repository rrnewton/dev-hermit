//! Stable wire descriptors for exact compiled layouts.
//!
//! A descriptor lets a producer and consumer check that they compiled the
//! same [`FixedAddressPodValue`] layout. It is only a compatibility check: it
//! neither authenticates code nor proves that stored bytes are initialized or
//! valid for the described Rust type.

use core::fmt;
use core::mem::{align_of, size_of};

use crate::FixedAddressPodValue;

const MAGIC_START: usize = 0;
const MAGIC_END: usize = 8;
const VERSION_START: usize = 8;
const VERSION_END: usize = 10;
const RESERVED_START: usize = 10;
const RESERVED_END: usize = 16;
const FINGERPRINT_START: usize = 16;
const FINGERPRINT_END: usize = 32;
const SIZE_START: usize = 32;
const SIZE_END: usize = 40;
const ALIGNMENT_START: usize = 40;
const ALIGNMENT_END: usize = 48;

/// An exact compiled-layout compatibility descriptor.
///
/// The descriptor records the structural fingerprint, byte size, and byte
/// alignment of a [`FixedAddressPodValue`]. Its encoded representation is
/// stable and independent of this Rust type's in-memory representation.
///
/// Equality only establishes that the recorded layout information agrees. It
/// does not authenticate an executable image and does not prove that any bytes
/// contain an initialized, valid value of the described type.
///
/// # Wire format
///
/// | Byte range | Contents |
/// | --- | --- |
/// | `0..8` | [`MAGIC`](Self::MAGIC) |
/// | `8..10` | [`VERSION`](Self::VERSION) as little-endian `u16` |
/// | `10..16` | Reserved; all bytes must be zero |
/// | `16..32` | Fingerprint as little-endian `u128` |
/// | `32..40` | Size as little-endian `u64` |
/// | `40..48` | Alignment as little-endian `u64` |
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct LayoutDescriptor {
    fingerprint: u128,
    size: u64,
    alignment: u64,
}

impl LayoutDescriptor {
    /// Magic bytes at the start of every encoded descriptor.
    pub const MAGIC: [u8; 8] = *b"SHMPODL\0";

    /// Version of the encoded descriptor format.
    pub const VERSION: u16 = 1;

    /// Length in bytes of every encoded descriptor.
    pub const ENCODED_LEN: usize = ALIGNMENT_END;

    /// Describes the exact compiled layout of `T`.
    ///
    /// Reading `T::FINGERPRINT` is intentional: generated implementations use
    /// evaluation of that constant to enforce their no-destructor assertion.
    #[must_use]
    pub const fn of<T: FixedAddressPodValue>() -> Self {
        Self {
            fingerprint: T::FINGERPRINT,
            size: size_of::<T>() as u64,
            alignment: align_of::<T>() as u64,
        }
    }

    /// Returns the structural fingerprint recorded by this descriptor.
    #[must_use]
    pub const fn fingerprint(&self) -> u128 {
        self.fingerprint
    }

    /// Returns the byte size recorded by this descriptor.
    #[must_use]
    pub const fn size(&self) -> u64 {
        self.size
    }

    /// Returns the byte alignment recorded by this descriptor.
    #[must_use]
    pub const fn alignment(&self) -> u64 {
        self.alignment
    }

    /// Returns whether this descriptor exactly matches `T`.
    ///
    /// This checks the fingerprint, size, and alignment. It does not validate
    /// bytes that might later be interpreted as `T`.
    #[must_use]
    pub const fn matches<T: FixedAddressPodValue>(&self) -> bool {
        let expected = Self::of::<T>();
        self.fingerprint == expected.fingerprint
            && self.size == expected.size
            && self.alignment == expected.alignment
    }

    /// Validates that this descriptor exactly matches `T`.
    ///
    /// The error identifies the first differing field in wire-field order and
    /// retains both complete descriptors for diagnostics.
    pub fn validate<T: FixedAddressPodValue>(&self) -> Result<(), LayoutMismatch> {
        let expected = Self::of::<T>();
        let field = if self.fingerprint != expected.fingerprint {
            LayoutField::Fingerprint
        } else if self.size != expected.size {
            LayoutField::Size
        } else if self.alignment != expected.alignment {
            LayoutField::Alignment
        } else {
            return Ok(());
        };

        Err(LayoutMismatch {
            field,
            expected,
            found: *self,
        })
    }

    /// Encodes this descriptor in the stable versioned wire format.
    ///
    /// Integer fields use little-endian byte order. Reserved bytes are zero.
    #[must_use]
    pub fn encode(&self) -> [u8; Self::ENCODED_LEN] {
        let mut encoded = [0; Self::ENCODED_LEN];
        encoded[MAGIC_START..MAGIC_END].copy_from_slice(&Self::MAGIC);
        encoded[VERSION_START..VERSION_END].copy_from_slice(&Self::VERSION.to_le_bytes());
        encoded[FINGERPRINT_START..FINGERPRINT_END]
            .copy_from_slice(&self.fingerprint.to_le_bytes());
        encoded[SIZE_START..SIZE_END].copy_from_slice(&self.size.to_le_bytes());
        encoded[ALIGNMENT_START..ALIGNMENT_END].copy_from_slice(&self.alignment.to_le_bytes());
        encoded
    }

    /// Decodes and validates one complete wire descriptor.
    ///
    /// Inputs must be exactly [`ENCODED_LEN`](Self::ENCODED_LEN) bytes. The
    /// decoder rejects unknown versions, nonzero reserved bytes, sizes that are
    /// not representable by this target's `usize`, and alignments that are zero,
    /// not powers of two, or not representable by `usize`.
    pub fn decode(encoded: &[u8]) -> Result<Self, DecodeError> {
        if encoded.len() < Self::ENCODED_LEN {
            return Err(DecodeError::Truncated {
                expected: Self::ENCODED_LEN,
                actual: encoded.len(),
            });
        }
        if encoded.len() > Self::ENCODED_LEN {
            return Err(DecodeError::TrailingBytes {
                expected: Self::ENCODED_LEN,
                actual: encoded.len(),
            });
        }

        let mut magic = [0; MAGIC_END - MAGIC_START];
        magic.copy_from_slice(&encoded[MAGIC_START..MAGIC_END]);
        if magic != Self::MAGIC {
            return Err(DecodeError::BadMagic { found: magic });
        }

        let version = u16::from_le_bytes(
            encoded[VERSION_START..VERSION_END]
                .try_into()
                .expect("version field has a fixed width"),
        );
        if version != Self::VERSION {
            return Err(DecodeError::UnsupportedVersion { found: version });
        }

        for (relative_index, value) in encoded[RESERVED_START..RESERVED_END]
            .iter()
            .copied()
            .enumerate()
        {
            if value != 0 {
                return Err(DecodeError::NonzeroReserved {
                    offset: RESERVED_START + relative_index,
                    value,
                });
            }
        }

        let fingerprint = u128::from_le_bytes(
            encoded[FINGERPRINT_START..FINGERPRINT_END]
                .try_into()
                .expect("fingerprint field has a fixed width"),
        );
        let size = u64::from_le_bytes(
            encoded[SIZE_START..SIZE_END]
                .try_into()
                .expect("size field has a fixed width"),
        );
        if size > usize::MAX as u64 {
            return Err(DecodeError::SizeOutOfRange { size });
        }
        let alignment = u64::from_le_bytes(
            encoded[ALIGNMENT_START..ALIGNMENT_END]
                .try_into()
                .expect("alignment field has a fixed width"),
        );
        if !alignment.is_power_of_two() || alignment > usize::MAX as u64 {
            return Err(DecodeError::InvalidAlignment { alignment });
        }

        Ok(Self {
            fingerprint,
            size,
            alignment,
        })
    }
}

/// A field that differs between two layout descriptors.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum LayoutField {
    /// The structural fingerprint differs.
    Fingerprint,
    /// The byte size differs.
    Size,
    /// The byte alignment differs.
    Alignment,
}

/// An exact-layout compatibility failure.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LayoutMismatch {
    field: LayoutField,
    expected: LayoutDescriptor,
    found: LayoutDescriptor,
}

impl LayoutMismatch {
    /// Returns the first field that differs.
    #[must_use]
    pub const fn field(&self) -> LayoutField {
        self.field
    }

    /// Returns the local descriptor expected for the requested Rust type.
    #[must_use]
    pub const fn expected(&self) -> LayoutDescriptor {
        self.expected
    }

    /// Returns the descriptor that was validated.
    #[must_use]
    pub const fn found(&self) -> LayoutDescriptor {
        self.found
    }
}

impl fmt::Display for LayoutMismatch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.field {
            LayoutField::Fingerprint => write!(
                formatter,
                "layout fingerprint mismatch: expected {:#034x}, found {:#034x}",
                self.expected.fingerprint, self.found.fingerprint
            ),
            LayoutField::Size => write!(
                formatter,
                "layout size mismatch: expected {}, found {}",
                self.expected.size, self.found.size
            ),
            LayoutField::Alignment => write!(
                formatter,
                "layout alignment mismatch: expected {}, found {}",
                self.expected.alignment, self.found.alignment
            ),
        }
    }
}

impl core::error::Error for LayoutMismatch {}

/// A malformed or unsupported encoded layout descriptor.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DecodeError {
    /// The input ends before a complete descriptor.
    Truncated {
        /// Required descriptor length.
        expected: usize,
        /// Supplied input length.
        actual: usize,
    },
    /// The input continues after the complete descriptor.
    TrailingBytes {
        /// Required descriptor length.
        expected: usize,
        /// Supplied input length.
        actual: usize,
    },
    /// The input does not begin with [`LayoutDescriptor::MAGIC`].
    BadMagic {
        /// Magic bytes found in the input.
        found: [u8; 8],
    },
    /// The format version is not supported by this decoder.
    UnsupportedVersion {
        /// Version found in the input.
        found: u16,
    },
    /// A byte reserved for future format versions is nonzero.
    NonzeroReserved {
        /// Byte offset from the start of the encoded descriptor.
        offset: usize,
        /// Nonzero value found at `offset`.
        value: u8,
    },
    /// The recorded byte size is not representable by this target's `usize`.
    SizeOutOfRange {
        /// Unrepresentable byte size found in the input.
        size: u64,
    },
    /// The recorded alignment cannot describe a local Rust type.
    InvalidAlignment {
        /// Invalid alignment found in the input.
        alignment: u64,
    },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            Self::Truncated { expected, actual } => write!(
                formatter,
                "layout descriptor is truncated: expected {expected} bytes, found {actual}"
            ),
            Self::TrailingBytes { expected, actual } => write!(
                formatter,
                "layout descriptor has trailing bytes: expected {expected} bytes, found {actual}"
            ),
            Self::BadMagic { found } => {
                write!(formatter, "invalid layout descriptor magic: {found:02x?}")
            }
            Self::UnsupportedVersion { found } => write!(
                formatter,
                "unsupported layout descriptor version {found}; expected {}",
                LayoutDescriptor::VERSION
            ),
            Self::NonzeroReserved { offset, value } => write!(
                formatter,
                "layout descriptor reserved byte at offset {offset} is nonzero ({value:#04x})"
            ),
            Self::SizeOutOfRange { size } => write!(
                formatter,
                "layout descriptor size {size} is not representable on this target"
            ),
            Self::InvalidAlignment { alignment } => {
                write!(formatter, "invalid layout descriptor alignment {alignment}")
            }
        }
    }
}

impl core::error::Error for DecodeError {}
