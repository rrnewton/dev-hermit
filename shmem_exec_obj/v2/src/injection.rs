#![doc = include_str!("../docs/injection.md")]

use core::fmt;
use core::sync::atomic::{AtomicU32, Ordering};

const MAGIC_RANGE: core::ops::Range<usize> = 0..8;
const VERSION_RANGE: core::ops::Range<usize> = 8..10;
const SIZE_RANGE: core::ops::Range<usize> = 10..12;
const FLAGS_RANGE: core::ops::Range<usize> = 12..16;
const CONNECTOR_RANGE: core::ops::Range<usize> = 16..20;
const RESERVED_WORD_RANGE: core::ops::Range<usize> = 20..24;
const ARTIFACT_FD_RANGE: core::ops::Range<usize> = 24..28;
const CODE_FD_RANGE: core::ops::Range<usize> = 28..32;
const STATE_FD_RANGE: core::ops::Range<usize> = 32..36;
const CONTROL_FD_RANGE: core::ops::Range<usize> = 36..40;
const ARTIFACT_LEN_RANGE: core::ops::Range<usize> = 40..48;
const STATE_LEN_RANGE: core::ops::Range<usize> = 48..56;
const CODE_ADDRESS_RANGE: core::ops::Range<usize> = 56..64;
const STATE_ADDRESS_RANGE: core::ops::Range<usize> = 64..72;
const GENERATION_RANGE: core::ops::Range<usize> = 72..80;
const API_FINGERPRINT_RANGE: core::ops::Range<usize> = 80..96;
const ARTIFACT_SHA256_RANGE: core::ops::Range<usize> = 96..128;
const INSTANCE_NONCE_RANGE: core::ops::Range<usize> = 128..144;
const RESERVED_BYTES_RANGE: core::ops::Range<usize> = 144..160;

const DISABLED_BIT: u32 = 1 << 31;
const ACTIVE_MASK: u32 = DISABLED_BIT - 1;

/// Name of the sole environment variable used by the inherited-FD transport.
///
/// Its value is a strict decimal file descriptor number naming a sealed file
/// that contains one encoded [`BootstrapContext`]. The environment is only a
/// locator. An external launcher must establish descriptor provenance; seals
/// prevent mutation and the digest checks artifact identity, but neither can
/// authenticate an attacker who can replace the complete descriptor set.
pub const BOOTSTRAP_FD_ENV: &str = "SHMEM_POD_BOOTSTRAP_FD";

/// Lowest supported page size for executable pod artifacts and state files.
pub const BOOTSTRAP_PAGE_SIZE: u64 = 4096;

/// Defensive maximum accepted artifact length (256 MiB).
pub const MAX_ARTIFACT_LEN: u64 = 256 * 1024 * 1024;

/// Defensive maximum accepted shared-state length (1 TiB).
pub const MAX_STATE_LEN: u64 = 1024 * 1024 * 1024 * 1024;

/// Stable identity of the adapter that consumed a bootstrap context.
#[repr(u32)]
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum ConnectorKind {
    /// A host which was compiled to load the pod directly.
    Cooperative = 1,
    /// A DSO loaded by the ELF loader through `LD_PRELOAD`.
    Preload = 2,
    /// A bootstrap DSO or entry point invoked by a ptracer.
    Ptrace = 3,
    /// An entry point reached from a validated binary-patch trampoline.
    Trampoline = 4,
}

impl ConnectorKind {
    /// Decodes a stable connector identifier.
    #[must_use]
    pub const fn from_u32(value: u32) -> Option<Self> {
        Some(match value {
            1 => Self::Cooperative,
            2 => Self::Preload,
            3 => Self::Ptrace,
            4 => Self::Trampoline,
            _ => return None,
        })
    }
}

/// Policy bits carried by a [`BootstrapContext`].
#[repr(transparent)]
#[derive(Clone, Copy, Debug, Default, Eq, Hash, PartialEq)]
pub struct BootstrapFlags(u32);

impl BootstrapFlags {
    /// An adapter failure must terminate or reject the target, not silently
    /// continue without instrumentation.
    pub const REQUIRED: Self = Self(1 << 0);

    /// The transport descriptors intentionally survive `exec`; adapters must
    /// duplicate them before taking Rust ownership of a descriptor.
    pub const INHERIT_ACROSS_EXEC: Self = Self(1 << 1);

    /// `code_address` is a mandatory page-aligned virtual address.
    pub const FIXED_CODE_ADDRESS: Self = Self(1 << 2);

    /// `state_address` is a mandatory page-aligned virtual address.
    pub const FIXED_STATE_ADDRESS: Self = Self(1 << 3);

    /// The caller asserts that the data descriptors were received over the
    /// Unix socket named by `control_fd` using `SCM_RIGHTS`.
    ///
    /// This is provenance metadata, not proof: [`BootstrapContext::validate`]
    /// cannot authenticate the peer or reconstruct the `recvmsg` operation.
    /// The adapter which receives the descriptors must verify the socket, peer,
    /// message framing, and descriptor roles before setting this bit. Adapters
    /// which do not implement that protocol must reject it.
    pub const SCM_RIGHTS_TRANSPORT: Self = Self(1 << 4);

    /// Every flag understood by this ABI revision.
    pub const KNOWN: Self = Self(
        Self::REQUIRED.0
            | Self::INHERIT_ACROSS_EXEC.0
            | Self::FIXED_CODE_ADDRESS.0
            | Self::FIXED_STATE_ADDRESS.0
            | Self::SCM_RIGHTS_TRANSPORT.0,
    );

    /// Constructs flags from raw bits without validating unknown bits.
    #[must_use]
    pub const fn from_bits(bits: u32) -> Self {
        Self(bits)
    }

    /// Returns the raw stable bit representation.
    #[must_use]
    pub const fn bits(self) -> u32 {
        self.0
    }

    /// Returns whether every bit in `other` is present.
    #[must_use]
    pub const fn contains(self, other: Self) -> bool {
        self.0 & other.0 == other.0
    }

    /// Combines two policy sets.
    #[must_use]
    pub const fn union(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }
}

/// Allocation-free, versioned process-bootstrap contract.
///
/// The representation is C-compatible and contains no pointers. Integer fields
/// use native representation when a C caller passes this object directly. The
/// stable encoded form returned by [`encode`](Self::encode) is little-endian.
/// This revision supports little-endian 64-bit Linux targets.
///
/// File descriptor integers are meaningful only in the receiving process. A
/// consumer must validate the complete context, verify the descriptor types,
/// lengths and seals, and duplicate every descriptor before constructing an
/// owning language object. In particular, `OwnedFd::from_raw_fd` must never be
/// called on an inherited descriptor recorded here.
#[repr(C)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BootstrapContext {
    /// Stable [`MAGIC`](Self::MAGIC).
    pub magic: [u8; 8],
    /// Stable [`ABI_VERSION`](Self::ABI_VERSION).
    pub abi_version: u16,
    /// Exact byte length of this ABI revision.
    pub struct_size: u16,
    /// Raw [`BootstrapFlags`] bits.
    pub flags: u32,
    /// Raw [`ConnectorKind`] value.
    pub connector: u32,
    /// Reserved; must be zero.
    pub reserved_word: u32,
    /// Sealed complete artifact descriptor.
    pub artifact_fd: i32,
    /// Sealed executable-code descriptor.
    pub code_fd: i32,
    /// Shared mutable state descriptor.
    pub state_fd: i32,
    /// Optional caller-verified `SCM_RIGHTS` control socket, or `-1`.
    pub control_fd: i32,
    /// Exact complete artifact length.
    pub artifact_len: u64,
    /// Exact shared-state file length.
    pub state_len: u64,
    /// Mandatory code address when `FIXED_CODE_ADDRESS` is set; zero otherwise.
    pub code_address: u64,
    /// Mandatory state address when `FIXED_STATE_ADDRESS` is set; zero otherwise.
    pub state_address: u64,
    /// Nonzero state generation selected by the trusted creator.
    pub generation: u64,
    /// Exact generated pod API fingerprint, in little-endian byte order.
    pub api_fingerprint: [u8; 16],
    /// SHA-256 of the complete artifact.
    pub artifact_sha256: [u8; 32],
    /// Nonzero per-launch identity used to reject accidental context reuse.
    pub instance_nonce: [u8; 16],
    /// Reserved for a future ABI revision; must be all zero.
    pub reserved: [u8; 16],
}

impl BootstrapContext {
    /// Magic bytes at the front of every context.
    pub const MAGIC: [u8; 8] = *b"SHPODBC\0";

    /// Initial bootstrap ABI revision.
    pub const ABI_VERSION: u16 = 1;

    /// Exact stable encoded length.
    pub const ENCODED_LEN: usize = 160;

    /// Creates and validates a context with no fixed-address or control-socket
    /// fields.
    ///
    /// Only [`BootstrapFlags::REQUIRED`] and
    /// [`BootstrapFlags::INHERIT_ACROSS_EXEC`] are accepted here. Use
    /// [`with_fixed_addresses`](Self::with_fixed_addresses),
    /// [`with_fixed_code_address`](Self::with_fixed_code_address),
    /// [`with_fixed_state_address`](Self::with_fixed_state_address), or
    /// [`with_scm_rights_provenance`](Self::with_scm_rights_provenance) for
    /// fields whose value and policy bit must change together. This constructor
    /// never returns an incoherent intermediate context.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        connector: ConnectorKind,
        flags: BootstrapFlags,
        artifact_fd: i32,
        code_fd: i32,
        state_fd: i32,
        artifact_len: u64,
        state_len: u64,
        generation: u64,
        api_fingerprint: u128,
        artifact_sha256: [u8; 32],
        instance_nonce: [u8; 16],
    ) -> Result<Self, BootstrapError> {
        let builder_managed = BootstrapFlags::FIXED_CODE_ADDRESS.bits()
            | BootstrapFlags::FIXED_STATE_ADDRESS.bits()
            | BootstrapFlags::SCM_RIGHTS_TRANSPORT.bits();
        if flags.bits() & builder_managed != 0 {
            return Err(BootstrapError::BuilderManagedFlags(
                flags.bits() & builder_managed,
            ));
        }
        let context = Self {
            magic: Self::MAGIC,
            abi_version: Self::ABI_VERSION,
            struct_size: Self::ENCODED_LEN as u16,
            flags: flags.bits(),
            connector: connector as u32,
            reserved_word: 0,
            artifact_fd,
            code_fd,
            state_fd,
            control_fd: -1,
            artifact_len,
            state_len,
            code_address: 0,
            state_address: 0,
            generation,
            api_fingerprint: api_fingerprint.to_le_bytes(),
            artifact_sha256,
            instance_nonce,
            reserved: [0; 16],
        };
        context.validate()?;
        Ok(context)
    }

    /// Records caller-verified `SCM_RIGHTS` provenance and its Unix socket.
    ///
    /// The caller must have already received `artifact_fd`, `code_fd`, and
    /// `state_fd` in a single authenticated protocol exchange on `control_fd`.
    /// This method checks representation coherence only. In particular, it
    /// cannot prove peer credentials or that those descriptors came from the
    /// socket. The receiving adapter remains responsible for those checks.
    pub fn with_scm_rights_provenance(mut self, control_fd: i32) -> Result<Self, BootstrapError> {
        self.flags |= BootstrapFlags::SCM_RIGHTS_TRANSPORT.bits();
        self.control_fd = control_fd;
        self.validate()?;
        Ok(self)
    }

    /// Adds a mandatory code virtual address and its policy bit.
    pub fn with_fixed_code_address(mut self, code_address: u64) -> Result<Self, BootstrapError> {
        self.flags |= BootstrapFlags::FIXED_CODE_ADDRESS.bits();
        self.code_address = code_address;
        self.validate()?;
        Ok(self)
    }

    /// Adds a mandatory state virtual address and its policy bit.
    pub fn with_fixed_state_address(mut self, state_address: u64) -> Result<Self, BootstrapError> {
        self.flags |= BootstrapFlags::FIXED_STATE_ADDRESS.bits();
        self.state_address = state_address;
        self.validate()?;
        Ok(self)
    }

    /// Adds mandatory code and state virtual addresses and their policy bits.
    pub fn with_fixed_addresses(
        mut self,
        code_address: u64,
        state_address: u64,
    ) -> Result<Self, BootstrapError> {
        self.flags |=
            BootstrapFlags::FIXED_CODE_ADDRESS.bits() | BootstrapFlags::FIXED_STATE_ADDRESS.bits();
        self.code_address = code_address;
        self.state_address = state_address;
        self.validate()?;
        Ok(self)
    }

    /// Returns the validated connector kind, if known.
    #[must_use]
    pub const fn connector_kind(&self) -> Option<ConnectorKind> {
        ConnectorKind::from_u32(self.connector)
    }

    /// Returns the context's policy flags.
    #[must_use]
    pub const fn bootstrap_flags(&self) -> BootstrapFlags {
        BootstrapFlags::from_bits(self.flags)
    }

    /// Returns the generated API fingerprint.
    #[must_use]
    pub const fn api_fingerprint(&self) -> u128 {
        u128::from_le_bytes(self.api_fingerprint)
    }

    /// Validates all representation-level invariants.
    ///
    /// OS adapters must additionally authenticate and inspect every referenced
    /// descriptor before mapping or executing it.
    pub fn validate(&self) -> Result<(), BootstrapError> {
        if self.magic != Self::MAGIC {
            return Err(BootstrapError::BadMagic);
        }
        if self.abi_version != Self::ABI_VERSION {
            return Err(BootstrapError::UnsupportedVersion(self.abi_version));
        }
        if usize::from(self.struct_size) != Self::ENCODED_LEN {
            return Err(BootstrapError::WrongSize(self.struct_size));
        }
        if self.flags & !BootstrapFlags::KNOWN.bits() != 0 {
            return Err(BootstrapError::UnknownFlags(self.flags));
        }
        if self.connector_kind().is_none() {
            return Err(BootstrapError::UnknownConnector(self.connector));
        }
        if self.reserved_word != 0 || self.reserved.iter().any(|byte| *byte != 0) {
            return Err(BootstrapError::NonzeroReserved);
        }
        validate_fd(self.artifact_fd, DescriptorRole::Artifact)?;
        validate_fd(self.code_fd, DescriptorRole::Code)?;
        validate_fd(self.state_fd, DescriptorRole::State)?;
        if self.artifact_fd == self.code_fd
            || self.artifact_fd == self.state_fd
            || self.code_fd == self.state_fd
        {
            return Err(BootstrapError::AliasedDescriptors);
        }
        if self.control_fd < -1 {
            return Err(BootstrapError::InvalidControlDescriptor(self.control_fd));
        }
        if self.control_fd >= 0 {
            validate_fd(self.control_fd, DescriptorRole::Control)?;
            if self.control_fd == self.artifact_fd
                || self.control_fd == self.code_fd
                || self.control_fd == self.state_fd
            {
                return Err(BootstrapError::AliasedDescriptors);
            }
        }
        let claims_scm_rights = self
            .bootstrap_flags()
            .contains(BootstrapFlags::SCM_RIGHTS_TRANSPORT);
        if claims_scm_rights != (self.control_fd >= 0) {
            return Err(BootstrapError::IncoherentControlTransport);
        }
        if self.artifact_len < BOOTSTRAP_PAGE_SIZE || self.artifact_len > MAX_ARTIFACT_LEN {
            return Err(BootstrapError::InvalidArtifactLength(self.artifact_len));
        }
        if self.state_len < BOOTSTRAP_PAGE_SIZE
            || self.state_len > MAX_STATE_LEN
            || self.state_len % BOOTSTRAP_PAGE_SIZE != 0
        {
            return Err(BootstrapError::InvalidStateLength(self.state_len));
        }
        validate_address(
            self.code_address,
            self.bootstrap_flags()
                .contains(BootstrapFlags::FIXED_CODE_ADDRESS),
            AddressRole::Code,
        )?;
        validate_address(
            self.state_address,
            self.bootstrap_flags()
                .contains(BootstrapFlags::FIXED_STATE_ADDRESS),
            AddressRole::State,
        )?;
        if self.code_address != 0 && self.code_address == self.state_address {
            return Err(BootstrapError::AliasedFixedAddresses(self.code_address));
        }
        if self.generation == 0 {
            return Err(BootstrapError::ZeroGeneration);
        }
        if self.api_fingerprint.iter().all(|byte| *byte == 0) {
            return Err(BootstrapError::ZeroApiFingerprint);
        }
        if self.artifact_sha256.iter().all(|byte| *byte == 0) {
            return Err(BootstrapError::ZeroArtifactDigest);
        }
        if self.instance_nonce.iter().all(|byte| *byte == 0) {
            return Err(BootstrapError::ZeroInstanceNonce);
        }
        Ok(())
    }

    /// Encodes this context in the stable little-endian format.
    #[must_use]
    pub fn encode(&self) -> [u8; Self::ENCODED_LEN] {
        let mut output = [0_u8; Self::ENCODED_LEN];
        output[MAGIC_RANGE].copy_from_slice(&self.magic);
        output[VERSION_RANGE].copy_from_slice(&self.abi_version.to_le_bytes());
        output[SIZE_RANGE].copy_from_slice(&self.struct_size.to_le_bytes());
        output[FLAGS_RANGE].copy_from_slice(&self.flags.to_le_bytes());
        output[CONNECTOR_RANGE].copy_from_slice(&self.connector.to_le_bytes());
        output[RESERVED_WORD_RANGE].copy_from_slice(&self.reserved_word.to_le_bytes());
        output[ARTIFACT_FD_RANGE].copy_from_slice(&self.artifact_fd.to_le_bytes());
        output[CODE_FD_RANGE].copy_from_slice(&self.code_fd.to_le_bytes());
        output[STATE_FD_RANGE].copy_from_slice(&self.state_fd.to_le_bytes());
        output[CONTROL_FD_RANGE].copy_from_slice(&self.control_fd.to_le_bytes());
        output[ARTIFACT_LEN_RANGE].copy_from_slice(&self.artifact_len.to_le_bytes());
        output[STATE_LEN_RANGE].copy_from_slice(&self.state_len.to_le_bytes());
        output[CODE_ADDRESS_RANGE].copy_from_slice(&self.code_address.to_le_bytes());
        output[STATE_ADDRESS_RANGE].copy_from_slice(&self.state_address.to_le_bytes());
        output[GENERATION_RANGE].copy_from_slice(&self.generation.to_le_bytes());
        output[API_FINGERPRINT_RANGE].copy_from_slice(&self.api_fingerprint);
        output[ARTIFACT_SHA256_RANGE].copy_from_slice(&self.artifact_sha256);
        output[INSTANCE_NONCE_RANGE].copy_from_slice(&self.instance_nonce);
        output[RESERVED_BYTES_RANGE].copy_from_slice(&self.reserved);
        output
    }

    /// Decodes and fully validates one stable little-endian context.
    pub fn decode(encoded: &[u8]) -> Result<Self, BootstrapError> {
        if encoded.len() != Self::ENCODED_LEN {
            return Err(BootstrapError::EncodedLength(encoded.len()));
        }
        let mut context = Self {
            magic: array(encoded, MAGIC_RANGE),
            abi_version: u16::from_le_bytes(array(encoded, VERSION_RANGE)),
            struct_size: u16::from_le_bytes(array(encoded, SIZE_RANGE)),
            flags: u32::from_le_bytes(array(encoded, FLAGS_RANGE)),
            connector: u32::from_le_bytes(array(encoded, CONNECTOR_RANGE)),
            reserved_word: u32::from_le_bytes(array(encoded, RESERVED_WORD_RANGE)),
            artifact_fd: i32::from_le_bytes(array(encoded, ARTIFACT_FD_RANGE)),
            code_fd: i32::from_le_bytes(array(encoded, CODE_FD_RANGE)),
            state_fd: i32::from_le_bytes(array(encoded, STATE_FD_RANGE)),
            control_fd: i32::from_le_bytes(array(encoded, CONTROL_FD_RANGE)),
            artifact_len: u64::from_le_bytes(array(encoded, ARTIFACT_LEN_RANGE)),
            state_len: u64::from_le_bytes(array(encoded, STATE_LEN_RANGE)),
            code_address: u64::from_le_bytes(array(encoded, CODE_ADDRESS_RANGE)),
            state_address: u64::from_le_bytes(array(encoded, STATE_ADDRESS_RANGE)),
            generation: u64::from_le_bytes(array(encoded, GENERATION_RANGE)),
            api_fingerprint: array(encoded, API_FINGERPRINT_RANGE),
            artifact_sha256: array(encoded, ARTIFACT_SHA256_RANGE),
            instance_nonce: array(encoded, INSTANCE_NONCE_RANGE),
            reserved: array(encoded, RESERVED_BYTES_RANGE),
        };
        context.validate()?;
        // Keep this assignment explicit so a future larger accepted encoding
        // cannot accidentally preserve an untrusted size in the C object.
        context.struct_size = Self::ENCODED_LEN as u16;
        Ok(context)
    }
}

const _: () = assert!(core::mem::size_of::<BootstrapContext>() == BootstrapContext::ENCODED_LEN);
const _: () = assert!(core::mem::align_of::<BootstrapContext>() == 8);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, magic) == 0);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, abi_version) == 8);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, struct_size) == 10);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, flags) == 12);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, connector) == 16);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, reserved_word) == 20);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, artifact_fd) == 24);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, code_fd) == 28);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, state_fd) == 32);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, control_fd) == 36);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, artifact_len) == 40);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, state_len) == 48);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, code_address) == 56);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, state_address) == 64);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, generation) == 72);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, api_fingerprint) == 80);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, artifact_sha256) == 96);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, instance_nonce) == 128);
const _: () = assert!(core::mem::offset_of!(BootstrapContext, reserved) == 144);

/// Descriptor role used in validation diagnostics.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DescriptorRole {
    /// Complete authenticated artifact.
    Artifact,
    /// Immutable executable code.
    Code,
    /// Mutable shared state.
    State,
    /// Optional control channel.
    Control,
}

/// Address role used in validation diagnostics.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AddressRole {
    /// Executable code mapping.
    Code,
    /// Shared state mapping.
    State,
}

/// Failure to decode or validate a bootstrap context.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BootstrapError {
    /// The encoded byte slice was not exactly the current ABI length.
    EncodedLength(usize),
    /// The magic did not identify this protocol.
    BadMagic,
    /// The ABI version is not supported.
    UnsupportedVersion(u16),
    /// `struct_size` did not identify this exact revision.
    WrongSize(u16),
    /// At least one unknown policy bit was set.
    UnknownFlags(u32),
    /// A constructor received a flag whose field must be set by a coherent
    /// builder method.
    BuilderManagedFlags(u32),
    /// The connector value is unknown.
    UnknownConnector(u32),
    /// A reserved byte or word was nonzero.
    NonzeroReserved,
    /// A required descriptor was standard I/O, negative, or otherwise invalid.
    InvalidDescriptor(DescriptorRole, i32),
    /// The optional control descriptor was less than `-1`.
    InvalidControlDescriptor(i32),
    /// Two roles named the same descriptor.
    AliasedDescriptors,
    /// The SCM_RIGHTS policy bit and optional control descriptor disagreed.
    IncoherentControlTransport,
    /// Artifact length was outside defensive bounds.
    InvalidArtifactLength(u64),
    /// State length was unaligned or outside defensive bounds.
    InvalidStateLength(u64),
    /// A fixed address was missing, unaligned, or unexpectedly present.
    InvalidAddress(AddressRole, u64),
    /// Code and state requested the same fixed virtual address.
    AliasedFixedAddresses(u64),
    /// The state generation was zero.
    ZeroGeneration,
    /// The generated API fingerprint was all zero.
    ZeroApiFingerprint,
    /// The expected artifact digest was all zero.
    ZeroArtifactDigest,
    /// The per-launch nonce was all zero.
    ZeroInstanceNonce,
}

impl fmt::Display for BootstrapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EncodedLength(length) => write!(
                formatter,
                "bootstrap context is {length} bytes, expected {}",
                BootstrapContext::ENCODED_LEN
            ),
            Self::BadMagic => formatter.write_str("bad bootstrap context magic"),
            Self::UnsupportedVersion(version) => {
                write!(formatter, "unsupported bootstrap ABI version {version}")
            }
            Self::WrongSize(size) => write!(formatter, "bootstrap struct size is {size}"),
            Self::UnknownFlags(flags) => write!(formatter, "unknown bootstrap flags 0x{flags:x}"),
            Self::BuilderManagedFlags(flags) => write!(
                formatter,
                "bootstrap flags 0x{flags:x} require a coherent builder method"
            ),
            Self::UnknownConnector(connector) => {
                write!(formatter, "unknown connector kind {connector}")
            }
            Self::NonzeroReserved => formatter.write_str("reserved bootstrap bytes are nonzero"),
            Self::InvalidDescriptor(role, fd) => {
                write!(formatter, "invalid {role:?} descriptor {fd}")
            }
            Self::InvalidControlDescriptor(fd) => {
                write!(formatter, "invalid optional control descriptor {fd}")
            }
            Self::AliasedDescriptors => formatter.write_str("bootstrap descriptors alias"),
            Self::IncoherentControlTransport => {
                formatter.write_str("SCM_RIGHTS flag and control descriptor disagree")
            }
            Self::InvalidArtifactLength(length) => {
                write!(formatter, "invalid artifact length {length}")
            }
            Self::InvalidStateLength(length) => write!(formatter, "invalid state length {length}"),
            Self::InvalidAddress(role, address) => {
                write!(formatter, "invalid {role:?} address 0x{address:x}")
            }
            Self::AliasedFixedAddresses(address) => {
                write!(
                    formatter,
                    "code and state both require address 0x{address:x}"
                )
            }
            Self::ZeroGeneration => formatter.write_str("bootstrap generation is zero"),
            Self::ZeroApiFingerprint => formatter.write_str("pod API fingerprint is zero"),
            Self::ZeroArtifactDigest => formatter.write_str("artifact SHA-256 is zero"),
            Self::ZeroInstanceNonce => formatter.write_str("bootstrap instance nonce is zero"),
        }
    }
}

impl core::error::Error for BootstrapError {}

/// Failure to parse [`BOOTSTRAP_FD_ENV`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BootstrapFdError {
    /// The value was empty.
    Empty,
    /// A byte was not an ASCII decimal digit.
    NonDecimal,
    /// The value had a redundant leading zero.
    LeadingZero,
    /// The value exceeded `i32::MAX`.
    Overflow,
    /// The descriptor aliases standard input, output, or error.
    StandardIo(i32),
}

impl fmt::Display for BootstrapFdError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => formatter.write_str("bootstrap FD is empty"),
            Self::NonDecimal => formatter.write_str("bootstrap FD is not strict ASCII decimal"),
            Self::LeadingZero => formatter.write_str("bootstrap FD has a leading zero"),
            Self::Overflow => formatter.write_str("bootstrap FD exceeds i32::MAX"),
            Self::StandardIo(fd) => write!(formatter, "bootstrap FD {fd} aliases standard I/O"),
        }
    }
}

impl core::error::Error for BootstrapFdError {}

/// Parses the allocation-free inherited-descriptor environment syntax.
///
/// The grammar is `[1-9][0-9]*`. Signs, whitespace, NULs, non-ASCII bytes,
/// leading zeroes, overflow, and descriptors `0..=2` are rejected.
pub fn parse_bootstrap_fd(value: &[u8]) -> Result<i32, BootstrapFdError> {
    if value.is_empty() {
        return Err(BootstrapFdError::Empty);
    }
    if value.len() > 1 && value[0] == b'0' {
        return Err(BootstrapFdError::LeadingZero);
    }
    let mut parsed = 0_u32;
    for byte in value {
        if !byte.is_ascii_digit() {
            return Err(BootstrapFdError::NonDecimal);
        }
        parsed = parsed
            .checked_mul(10)
            .and_then(|number| number.checked_add(u32::from(*byte - b'0')))
            .ok_or(BootstrapFdError::Overflow)?;
        if parsed > i32::MAX as u32 {
            return Err(BootstrapFdError::Overflow);
        }
    }
    let fd = parsed as i32;
    if fd <= 2 {
        Err(BootstrapFdError::StandardIo(fd))
    } else {
        Ok(fd)
    }
}

/// C-callable bootstrap callback status.
#[repr(i32)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BootstrapStatus {
    /// The adapter accepted and published the context.
    Ok = 0,
    /// The pointer or representation was invalid.
    InvalidContext = -1,
    /// Descriptor provenance, seals, type, length, or duplication failed.
    InvalidTransport = -2,
    /// Artifact digest, header, or generated method metadata did not match.
    IncompatibleImage = -3,
    /// The adapter is already draining or permanently disabled.
    Disabled = -4,
    /// The same process attempted recursive initialization.
    Reentrant = -5,
    /// Adapter-specific initialization failed.
    InitializationFailed = -6,
}

/// C ABI implemented by ptrace bootstrap DSOs and trampoline adapters.
///
/// # Safety
///
/// The pointer must be aligned, readable, and live for the duration of the
/// call. The callee must copy the value and duplicate referenced descriptors
/// before returning; it must not retain this pointer.
pub type BootstrapEntry = unsafe extern "C" fn(*const BootstrapContext) -> i32;

/// Allocation-free process-local admission gate for injected adapter calls.
///
/// The high bit permanently disables new calls. Remaining bits count active
/// entries. This prevents an adapter from reclaiming its process-local context
/// while an admitted hook is using it; it cannot make `dlclose` safe while
/// external trampolines still target the DSO's text.
#[derive(Debug)]
pub struct AdapterCallGate {
    state: AtomicU32,
}

impl AdapterCallGate {
    /// Creates an enabled gate with no active calls.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            state: AtomicU32::new(0),
        }
    }

    /// Attempts to admit one adapter call.
    pub fn try_enter(&self) -> Option<AdapterCall<'_>> {
        let mut observed = self.state.load(Ordering::Acquire);
        loop {
            if observed & DISABLED_BIT != 0 || observed & ACTIVE_MASK == ACTIVE_MASK {
                return None;
            }
            match self.state.compare_exchange_weak(
                observed,
                observed + 1,
                Ordering::Acquire,
                Ordering::Relaxed,
            ) {
                Ok(_) => return Some(AdapterCall { gate: self }),
                Err(actual) => observed = actual,
            }
        }
    }

    /// Prevents new calls and returns the active-call count.
    ///
    /// Ordinary lifecycle code treats this as permanent. A serialized at-fork
    /// handler may reset the disabled, drained copy with
    /// [`reset_after_fork`](Self::reset_after_fork).
    pub fn disable(&self) -> u32 {
        self.state.fetch_or(DISABLED_BIT, Ordering::AcqRel) & ACTIVE_MASK
    }

    /// Returns the current number of admitted calls.
    #[must_use]
    pub fn active_calls(&self) -> u32 {
        self.state.load(Ordering::Acquire) & ACTIVE_MASK
    }

    /// Returns whether new calls are permanently disabled.
    #[must_use]
    pub fn is_disabled(&self) -> bool {
        self.state.load(Ordering::Acquire) & DISABLED_BIT != 0
    }

    /// Resets a disabled, quiescent gate after an at-fork barrier.
    ///
    /// # Safety
    ///
    /// The gate must first be disabled and `active_calls() == 0`. In a child,
    /// the surviving forking thread must not retain a live [`AdapterCall`]
    /// token. Calling this without that prepare-side quiescence can invalidate
    /// a token and underflow the counter when it is dropped. The same barrier
    /// permits the parent handler to re-enable its copied gate.
    ///
    /// Even with the safety precondition, this returns an error unless the
    /// observable state is exactly disabled and quiescent. That fail-closed
    /// check catches incomplete prepare handlers without erasing a live count.
    pub unsafe fn reset_after_fork(&self) -> Result<(), GateResetError> {
        self.state
            .compare_exchange(DISABLED_BIT, 0, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| ())
            .map_err(|state| GateResetError {
                disabled: state & DISABLED_BIT != 0,
                active_calls: state & ACTIVE_MASK,
            })
    }
}

/// A fork handler attempted to reset a gate that was not disabled and drained.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GateResetError {
    disabled: bool,
    active_calls: u32,
}

impl GateResetError {
    /// Whether the gate was disabled when reset was attempted.
    #[must_use]
    pub const fn was_disabled(self) -> bool {
        self.disabled
    }

    /// Number of live admission tokens observed at reset.
    #[must_use]
    pub const fn active_calls(self) -> u32 {
        self.active_calls
    }
}

impl fmt::Display for GateResetError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "adapter gate reset requires disabled+quiescent state (disabled={}, active_calls={})",
            self.disabled, self.active_calls
        )
    }
}

impl core::error::Error for GateResetError {}

impl Default for AdapterCallGate {
    fn default() -> Self {
        Self::new()
    }
}

/// Linear admission token returned by [`AdapterCallGate::try_enter`].
#[derive(Debug)]
pub struct AdapterCall<'a> {
    gate: &'a AdapterCallGate,
}

impl Drop for AdapterCall<'_> {
    fn drop(&mut self) {
        let previous = self.gate.state.fetch_sub(1, Ordering::Release);
        debug_assert!(previous & ACTIVE_MASK != 0);
    }
}

fn validate_fd(fd: i32, role: DescriptorRole) -> Result<(), BootstrapError> {
    if fd <= 2 {
        Err(BootstrapError::InvalidDescriptor(role, fd))
    } else {
        Ok(())
    }
}

fn validate_address(address: u64, required: bool, role: AddressRole) -> Result<(), BootstrapError> {
    if required {
        if address == 0 || address % BOOTSTRAP_PAGE_SIZE != 0 {
            return Err(BootstrapError::InvalidAddress(role, address));
        }
    } else if address != 0 {
        return Err(BootstrapError::InvalidAddress(role, address));
    }
    Ok(())
}

fn array<const N: usize>(bytes: &[u8], range: core::ops::Range<usize>) -> [u8; N] {
    bytes[range]
        .try_into()
        .expect("bootstrap wire ranges have fixed audited lengths")
}
