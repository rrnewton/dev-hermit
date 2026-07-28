//! Typed lifecycle management for one caller-supplied shared mapping.
//!
//! The shared phase is monotonic:
//! `Uninitialized -> Initializing -> Open -> Draining -> Closed`. Any detected
//! failure may instead transition to `Poisoned`. Attachment and active-admission
//! counts share the same atomic word as the phase, so closing admission cannot
//! race a late entrant and falsely report that the payload is reclaimable.
//!
//! This module deliberately does not call `mmap`. A host maps and authenticates
//! bytes, then provides their address through [`RawMapping`]. Keeping the OS
//! operation outside the core API lets this crate remain `no_std` and makes the
//! byte-to-typed-value safety boundary explicit.

use core::cell::UnsafeCell;
use core::fmt;
use core::marker::PhantomData;
use core::mem::{align_of, size_of};
use core::ops::Deref;
use core::ptr::{self, NonNull};
use core::sync::atomic::{AtomicU64, Ordering};

use crate::layout::{DecodeError, LayoutDescriptor, LayoutMismatch};
use crate::{PodSync, PodValue};

const MAGIC: [u8; 8] = *b"SHMPODM\0";
const VERSION: u32 = 1;

const ATTACHMENT_BITS: u32 = 28;
const ACTIVE_BITS: u32 = 28;
const ATTACHMENT_MASK: u64 = (1_u64 << ATTACHMENT_BITS) - 1;
const ACTIVE_SHIFT: u32 = ATTACHMENT_BITS;
const ACTIVE_MASK: u64 = ((1_u64 << ACTIVE_BITS) - 1) << ACTIVE_SHIFT;
const PHASE_SHIFT: u32 = ATTACHMENT_BITS + ACTIVE_BITS;
const ATTACHMENT_ONE: u64 = 1;
const ACTIVE_ONE: u64 = 1_u64 << ACTIVE_SHIFT;
const MAX_COUNT: u32 = (1_u32 << ATTACHMENT_BITS) - 1;

/// Authenticated identity of the complete SDK build and feature set.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct BuildIdentity([u8; 32]);

impl BuildIdentity {
    /// Creates an identity from a caller-computed cryptographic digest.
    #[must_use]
    pub const fn new(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    /// Returns the digest bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

/// Unique identity of one initialized shared-memory instance.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct InstanceIdentity([u8; 16]);

impl InstanceIdentity {
    /// Creates an instance identity from unpredictable caller-provided bytes.
    #[must_use]
    pub const fn new(bytes: [u8; 16]) -> Self {
        Self(bytes)
    }

    /// Returns the identity bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

/// Shared lifecycle phase recorded in a mapping header.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
#[repr(u8)]
pub enum Phase {
    /// The header exists, but no payload has been constructed.
    Uninitialized = 0,
    /// One participant owns payload construction.
    Initializing = 1,
    /// Attachments and admissions are accepted.
    Open = 2,
    /// New attachments and admissions are rejected while existing users drain.
    Draining = 3,
    /// The payload is no longer accessible through this API.
    Closed = 4,
    /// An invariant may be incomplete; the whole instance must be discarded.
    Poisoned = 5,
}

impl Phase {
    const fn from_raw(raw: u8) -> Option<Self> {
        match raw {
            0 => Some(Self::Uninitialized),
            1 => Some(Self::Initializing),
            2 => Some(Self::Open),
            3 => Some(Self::Draining),
            4 => Some(Self::Closed),
            5 => Some(Self::Poisoned),
            _ => None,
        }
    }
}

/// Consistent snapshot of the packed shared lifecycle word.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LifecycleSnapshot {
    phase: Phase,
    attachments: u32,
    admissions: u32,
}

impl LifecycleSnapshot {
    /// Returns the shared phase.
    #[must_use]
    pub const fn phase(self) -> Phase {
        self.phase
    }

    /// Returns the number of live local attachment handles across participants.
    #[must_use]
    pub const fn attachments(self) -> u32 {
        self.attachments
    }

    /// Returns the number of live admission guards across participants.
    #[must_use]
    pub const fn admissions(self) -> u32 {
        self.admissions
    }
}

#[derive(Clone, Copy)]
#[repr(C)]
struct Geometry {
    payload_offset: u64,
    payload_len: u64,
    descriptor: [u8; LayoutDescriptor::ENCODED_LEN],
}

#[repr(C, align(64))]
struct MappingHeader {
    magic: [u8; 8],
    version: u32,
    header_len: u32,
    mapping_len: u64,
    build_identity: [u8; 32],
    instance_identity: [u8; 16],
    geometry: UnsafeCell<Geometry>,
    lifecycle: AtomicU64,
}

/// An untyped, process-local view of caller-owned mapping bytes.
///
/// It is intentionally neither `Copy` nor `Clone`: [`prepare`](Self::prepare)
/// consumes the unique capability used to construct the header. Validated
/// [`Mapping`] handles may be copied after preparation.
pub struct RawMapping<'mapping> {
    base: NonNull<u8>,
    len: usize,
    marker: PhantomData<&'mapping mut [u8]>,
}

impl fmt::Debug for RawMapping<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RawMapping")
            .field("base", &self.base)
            .field("len", &self.len)
            .finish()
    }
}

impl<'mapping> RawMapping<'mapping> {
    /// Creates a raw view over one live writable shared mapping.
    ///
    /// # Safety
    ///
    /// `base..base + len` must remain one live, writable mapping for
    /// `'mapping`. The caller must prevent unmapping and incompatible direct
    /// byte access while any handle or guard returned by this module exists.
    /// The mapping must be shared with mutually trusted participants; this API
    /// does not protect typed values from arbitrary raw writes.
    pub unsafe fn from_raw_parts(base: *mut u8, len: usize) -> Result<Self, MappingError> {
        let base = NonNull::new(base).ok_or(MappingError::NullMapping)?;
        if len > isize::MAX as usize {
            return Err(MappingError::MappingTooLarge);
        }
        (base.as_ptr() as usize)
            .checked_add(len)
            .ok_or(MappingError::AddressOverflow)?;
        if base.as_ptr() as usize % align_of::<MappingHeader>() != 0 {
            return Err(MappingError::MisalignedMapping {
                required: align_of::<MappingHeader>(),
                address: base.as_ptr() as usize,
            });
        }
        if len < size_of::<MappingHeader>() {
            return Err(MappingError::MappingTooSmall {
                required: size_of::<MappingHeader>(),
                actual: len,
            });
        }
        Ok(Self {
            base,
            len,
            marker: PhantomData,
        })
    }

    /// Constructs a fresh header in exclusively owned mapping bytes.
    ///
    /// The identity values are immutable for the lifetime of the mapping. A
    /// cryptographic build identity should cover the toolchain, target,
    /// dependencies, features, and executable image. Instance identities must
    /// not be reused while stale handles could exist.
    pub fn prepare(
        self,
        build_identity: BuildIdentity,
        instance_identity: InstanceIdentity,
    ) -> Mapping<'mapping> {
        let header = MappingHeader {
            magic: MAGIC,
            version: VERSION,
            header_len: size_of::<MappingHeader>() as u32,
            mapping_len: self.len as u64,
            build_identity: build_identity.0,
            instance_identity: instance_identity.0,
            geometry: UnsafeCell::new(Geometry {
                payload_offset: 0,
                payload_len: 0,
                descriptor: [0; LayoutDescriptor::ENCODED_LEN],
            }),
            lifecycle: AtomicU64::new(pack(Phase::Uninitialized, 0, 0)),
        };
        // SAFETY: RawMapping's contract gives exclusive writable bytes, and
        // constructor checks established header alignment and extent.
        unsafe { self.base.cast::<MappingHeader>().as_ptr().write(header) };
        Mapping::from_raw(self.base, self.len)
    }

    /// Opens a header prepared by a trusted participant.
    ///
    /// # Safety
    ///
    /// The caller must authenticate the complete backing object and guarantee
    /// that a prior [`prepare`](Self::prepare) finished before this call. A
    /// matching identity or layout descriptor alone does not prove that raw
    /// bytes contain valid Rust values. All participants must obey this
    /// module's synchronization and mapping-lifetime rules.
    pub unsafe fn open_existing(
        self,
        expected_build: BuildIdentity,
        expected_instance: InstanceIdentity,
    ) -> Result<Mapping<'mapping>, MappingError> {
        let mapping = Mapping::from_raw(self.base, self.len);
        mapping.validate_header(expected_build, expected_instance)?;
        Ok(mapping)
    }
}

/// A validated local handle to a prepared shared mapping.
///
/// Copying this value does not attach to the typed payload. Attachment counts
/// are acquired only by [`try_initialize`](Self::try_initialize) and
/// [`attach`](Self::attach).
#[derive(Clone, Copy)]
pub struct Mapping<'mapping> {
    base: NonNull<u8>,
    header: NonNull<MappingHeader>,
    len: usize,
    marker: PhantomData<&'mapping UnsafeCell<[u8]>>,
}

// SAFETY: Mapping exposes only atomic lifecycle operations and one-time
// publication. The unsafe constructors require the backing bytes to remain
// mapped and participants to follow the cross-process protocol.
unsafe impl Send for Mapping<'_> {}
// SAFETY: See the Send implementation; Mapping itself exposes no raw mutation.
unsafe impl Sync for Mapping<'_> {}

impl fmt::Debug for Mapping<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Mapping")
            .field("base", &self.base)
            .field("len", &self.len)
            .field("snapshot", &self.snapshot())
            .finish()
    }
}

impl<'mapping> Mapping<'mapping> {
    fn from_raw(base: NonNull<u8>, len: usize) -> Self {
        Self {
            base,
            header: base.cast(),
            len,
            marker: PhantomData,
        }
    }

    /// Returns the mapping base in this process.
    #[must_use]
    pub const fn base(self) -> NonNull<u8> {
        self.base
    }

    /// Returns the accessible mapping length.
    #[must_use]
    pub const fn len(self) -> usize {
        self.len
    }

    /// Returns whether the mapping has no accessible bytes.
    #[must_use]
    pub const fn is_empty(self) -> bool {
        self.len == 0
    }

    /// Loads one consistent lifecycle snapshot.
    pub fn snapshot(self) -> Result<LifecycleSnapshot, MappingError> {
        decode(self.header().lifecycle.load(Ordering::Acquire))
    }

    /// Initializes `T` directly in its final shared address.
    ///
    /// Exactly one racing caller can change `Uninitialized` to `Initializing`.
    /// Construction is published with release ordering only after the payload,
    /// geometry, and exact layout descriptor are complete. Unwinding during
    /// this operation poisons the mapping; abort or process death leaves it
    /// fail-stuck in `Initializing`.
    pub fn try_initialize<T: PodValue + PodSync>(
        self,
        value: T,
    ) -> Result<Owner<'mapping, T>, MappingError> {
        // SAFETY: writing a moved, valid T completely initializes the target
        // before the closure returns and no reference escapes.
        unsafe { self.try_initialize_in_place(|target: *mut T| target.write(value)) }
    }

    /// Initializes `T` in place without first materializing a complete value.
    ///
    /// This is useful for large fixed-capacity state where moving a temporary
    /// could make freestanding code generation lower to an unwanted `memcpy` or
    /// `memset` call. Exactly one racing caller obtains the destination.
    ///
    /// # Safety
    ///
    /// Before returning normally, `initialize` must write exactly one valid,
    /// fully initialized `T` to its pointer without reading the uninitialized
    /// destination. It must not leak a reference or pointer whose use can race
    /// publication, and it must not access the mapping through another typed
    /// handle. Unwinding poisons the mapping; abort, process death, or `execve`
    /// during the callback instead leaves `Initializing` and requires a
    /// supervisor to poison and discard the instance.
    pub unsafe fn try_initialize_in_place<T: PodValue + PodSync>(
        self,
        initialize: impl FnOnce(*mut T),
    ) -> Result<Owner<'mapping, T>, MappingError> {
        let payload = self.payload_for::<T>()?;
        let uninitialized = pack(Phase::Uninitialized, 0, 0);
        self.header()
            .lifecycle
            .compare_exchange(
                uninitialized,
                pack(Phase::Initializing, 0, 0),
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .map_err(|actual| wrong_phase(Phase::Uninitialized, actual))?;

        let mut poison = InitializationPoison {
            header: self.header.as_ptr(),
            armed: true,
        };
        let geometry = Geometry {
            payload_offset: (payload.as_ptr() as usize - self.base.as_ptr() as usize) as u64,
            payload_len: size_of::<T>() as u64,
            descriptor: LayoutDescriptor::of::<T>().encode(),
        };
        // SAFETY: this caller uniquely owns Initializing. Geometry and payload
        // are not read until the release publication below.
        unsafe {
            self.header().geometry.get().write(geometry);
        }
        initialize(payload.as_ptr());
        self.header()
            .lifecycle
            .compare_exchange(
                pack(Phase::Initializing, 0, 0),
                pack(Phase::Open, 1, 0),
                Ordering::Release,
                Ordering::Acquire,
            )
            .map_err(|actual| wrong_phase(Phase::Initializing, actual))?;
        poison.armed = false;

        Ok(Owner {
            attachment: Some(Attachment::new(self, payload)),
        })
    }

    /// Attaches to an initialized payload whose type and build were validated.
    ///
    /// This method rejects mappings once draining begins. Typed payload access
    /// is available only through [`AdmissionGuard`].
    pub fn attach<T: PodValue + PodSync>(self) -> Result<Attachment<'mapping, T>, MappingError> {
        let payload = self.validate_payload::<T>()?;
        let mut current = self.header().lifecycle.load(Ordering::Acquire);
        loop {
            let snapshot = decode(current)?;
            if snapshot.phase != Phase::Open {
                return Err(MappingError::WrongPhase {
                    expected: Phase::Open,
                    actual: snapshot.phase,
                });
            }
            if snapshot.attachments == MAX_COUNT {
                return Err(MappingError::AttachmentLimit);
            }
            match self.header().lifecycle.compare_exchange_weak(
                current,
                current + ATTACHMENT_ONE,
                Ordering::Acquire,
                Ordering::Acquire,
            ) {
                Ok(_) => return Ok(Attachment::new(self, payload)),
                Err(actual) => current = actual,
            }
        }
    }

    /// Permanently poisons this instance while preserving diagnostic counts.
    ///
    /// Poisoning is fail-closed. It wakes no sleepers by itself and does not
    /// repair an interrupted invariant; a supervisor must stop participants and
    /// recreate the complete mapping.
    pub fn poison(self) -> Result<(), MappingError> {
        poison_header(self.header())
    }

    fn validate_header(
        self,
        expected_build: BuildIdentity,
        expected_instance: InstanceIdentity,
    ) -> Result<(), MappingError> {
        let header = self.header();
        if header.magic != MAGIC {
            return Err(MappingError::BadMagic {
                found: header.magic,
            });
        }
        if header.version != VERSION {
            return Err(MappingError::UnsupportedVersion {
                found: header.version,
            });
        }
        if header.header_len as usize != size_of::<MappingHeader>() {
            return Err(MappingError::HeaderLength {
                expected: size_of::<MappingHeader>(),
                found: header.header_len as usize,
            });
        }
        if header.mapping_len != self.len as u64 {
            return Err(MappingError::MappingLength {
                expected: self.len as u64,
                found: header.mapping_len,
            });
        }
        if header.build_identity != expected_build.0 {
            return Err(MappingError::BuildIdentity);
        }
        if header.instance_identity != expected_instance.0 {
            return Err(MappingError::InstanceIdentity);
        }
        decode(header.lifecycle.load(Ordering::Acquire))?;
        Ok(())
    }

    fn validate_payload<T: PodValue + PodSync>(self) -> Result<NonNull<T>, MappingError> {
        let snapshot = self.snapshot()?;
        if snapshot.phase != Phase::Open {
            return Err(MappingError::WrongPhase {
                expected: Phase::Open,
                actual: snapshot.phase,
            });
        }
        // SAFETY: Initializing owns geometry writes and Open was acquired above.
        let geometry = unsafe { ptr::read(self.header().geometry.get()) };
        let descriptor =
            LayoutDescriptor::decode(&geometry.descriptor).map_err(MappingError::LayoutEncoding)?;
        descriptor
            .validate::<T>()
            .map_err(MappingError::LayoutMismatch)?;
        if geometry.payload_len != size_of::<T>() as u64 {
            return Err(MappingError::PayloadLength {
                expected: size_of::<T>() as u64,
                found: geometry.payload_len,
            });
        }
        self.checked_payload::<T>(geometry.payload_offset)
    }

    fn payload_for<T>(self) -> Result<NonNull<T>, MappingError> {
        let header_len = size_of::<MappingHeader>();
        let alignment = align_of::<T>();
        let offset = header_len
            .checked_add(alignment - 1)
            .ok_or(MappingError::GeometryOverflow)?
            & !(alignment - 1);
        self.checked_payload::<T>(offset as u64)
    }

    fn checked_payload<T>(self, raw_offset: u64) -> Result<NonNull<T>, MappingError> {
        let offset = usize::try_from(raw_offset).map_err(|_| MappingError::GeometryOverflow)?;
        let end = offset
            .checked_add(size_of::<T>())
            .ok_or(MappingError::GeometryOverflow)?;
        if offset < size_of::<MappingHeader>() || end > self.len {
            return Err(MappingError::PayloadExtent {
                offset: raw_offset,
                length: size_of::<T>() as u64,
                mapping_length: self.len as u64,
            });
        }
        // SAFETY: offset is within the validated mapping extent.
        let pointer = unsafe { self.base.as_ptr().add(offset) }.cast::<T>();
        if pointer as usize % align_of::<T>() != 0 {
            return Err(MappingError::MisalignedPayload {
                required: align_of::<T>(),
                address: pointer as usize,
            });
        }
        // SAFETY: a pointer derived from a non-null mapping stays non-null.
        Ok(unsafe { NonNull::new_unchecked(pointer) })
    }

    fn header(self) -> &'mapping MappingHeader {
        // SAFETY: constructors establish a live aligned header for 'mapping.
        unsafe { self.header.as_ref() }
    }
}

struct InitializationPoison {
    header: *const MappingHeader,
    armed: bool,
}

impl Drop for InitializationPoison {
    fn drop(&mut self) {
        if self.armed {
            // SAFETY: the initialization operation keeps the mapping live.
            let header = unsafe { &*self.header };
            header
                .lifecycle
                .store(pack(Phase::Poisoned, 0, 0), Ordering::Release);
        }
    }
}

/// Unique close authority returned by successful initialization.
///
/// Dropping this value without beginning drain poisons the mapping, because no
/// participant could otherwise prove it retains teardown authority.
pub struct Owner<'mapping, T: PodValue + PodSync> {
    attachment: Option<Attachment<'mapping, T>>,
}

impl<T: PodValue + PodSync> fmt::Debug for Owner<'_, T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Owner")
            .field("snapshot", &self.snapshot())
            .finish_non_exhaustive()
    }
}

impl<'mapping, T: PodValue + PodSync> Owner<'mapping, T> {
    /// Enters the payload while the mapping remains open.
    pub fn try_enter(&self) -> Result<AdmissionGuard<'_, 'mapping, T>, MappingError> {
        self.attachment
            .as_ref()
            .expect("live owner retains its attachment")
            .try_enter()
    }

    /// Stops new attachments and admissions and consumes close authority.
    pub fn begin_drain(mut self) -> Result<Draining<'mapping, T>, MappingError> {
        let attachment = self
            .attachment
            .as_ref()
            .expect("live owner retains its attachment");
        transition_to_draining(attachment.mapping.header())?;
        Ok(Draining {
            attachment: self.attachment.take(),
        })
    }

    /// Returns a consistent lifecycle snapshot.
    pub fn snapshot(&self) -> Result<LifecycleSnapshot, MappingError> {
        self.attachment
            .as_ref()
            .expect("live owner retains its attachment")
            .mapping
            .snapshot()
    }
}

impl<T: PodValue + PodSync> Drop for Owner<'_, T> {
    fn drop(&mut self) {
        if let Some(attachment) = self.attachment.as_ref() {
            let _ = poison_header(attachment.mapping.header());
        }
    }
}

/// Counted local attachment to an open typed payload.
///
/// This value is not `Clone`: each process must call [`Mapping::attach`] so the
/// shared count reflects its handle. After `fork`, an inherited attachment must
/// not be accessed or dropped: the child must immediately `exec`/`_exit` or
/// first suppress the inherited destructor and then establish a new counted
/// attachment.
pub struct Attachment<'mapping, T: PodValue + PodSync> {
    mapping: Mapping<'mapping>,
    payload: NonNull<T>,
}

// SAFETY: moving the local capability between threads is valid only when T is
// Send; Draining may eventually expose exclusive T access on the new thread.
unsafe impl<T: PodValue + PodSync + Send> Send for Attachment<'_, T> {}
// SAFETY: Shared access only creates guards through atomic admission.
unsafe impl<T: PodValue + PodSync> Sync for Attachment<'_, T> {}

impl<T: PodValue + PodSync> fmt::Debug for Attachment<'_, T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Attachment")
            .field("mapping_base", &self.mapping.base)
            .field("snapshot", &self.snapshot())
            .finish_non_exhaustive()
    }
}

impl<'mapping, T: PodValue + PodSync> Attachment<'mapping, T> {
    fn new(mapping: Mapping<'mapping>, payload: NonNull<T>) -> Self {
        Self { mapping, payload }
    }

    /// Enters the payload if drain has not begun.
    pub fn try_enter(&self) -> Result<AdmissionGuard<'_, 'mapping, T>, MappingError> {
        let header = self.mapping.header();
        let mut current = header.lifecycle.load(Ordering::Acquire);
        loop {
            let snapshot = decode(current)?;
            if snapshot.phase != Phase::Open {
                return Err(MappingError::WrongPhase {
                    expected: Phase::Open,
                    actual: snapshot.phase,
                });
            }
            if snapshot.admissions == MAX_COUNT {
                return Err(MappingError::AdmissionLimit);
            }
            match header.lifecycle.compare_exchange_weak(
                current,
                current + ACTIVE_ONE,
                Ordering::Acquire,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    return Ok(AdmissionGuard {
                        attachment: self,
                        marker: PhantomData,
                    });
                }
                Err(actual) => current = actual,
            }
        }
    }

    /// Returns a consistent lifecycle snapshot.
    pub fn snapshot(&self) -> Result<LifecycleSnapshot, MappingError> {
        self.mapping.snapshot()
    }

    /// Returns this process's mapping base.
    #[must_use]
    pub const fn mapping_base(&self) -> NonNull<u8> {
        self.mapping.base
    }
}

impl<T: PodValue + PodSync> Drop for Attachment<'_, T> {
    fn drop(&mut self) {
        let previous = self
            .mapping
            .header()
            .lifecycle
            .fetch_sub(ATTACHMENT_ONE, Ordering::Release);
        debug_assert_ne!(previous & ATTACHMENT_MASK, 0, "attachment underflow");
    }
}

/// Counted payload admission that prevents successful teardown.
pub struct AdmissionGuard<'attachment, 'mapping, T: PodValue + PodSync> {
    attachment: &'attachment Attachment<'mapping, T>,
    marker: PhantomData<&'attachment T>,
}

impl<T: PodValue + PodSync> fmt::Debug for AdmissionGuard<'_, '_, T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AdmissionGuard")
            .field("mapping_base", &self.attachment.mapping.base)
            .finish_non_exhaustive()
    }
}

impl<T: PodValue + PodSync> Deref for AdmissionGuard<'_, '_, T> {
    type Target = T;

    fn deref(&self) -> &Self::Target {
        // SAFETY: initialization published a valid T; this guard holds an
        // admission and T: PodSync permits shared process access.
        unsafe { self.attachment.payload.as_ref() }
    }
}

impl<T: PodValue + PodSync> Drop for AdmissionGuard<'_, '_, T> {
    fn drop(&mut self) {
        let previous = self
            .attachment
            .mapping
            .header()
            .lifecycle
            .fetch_sub(ACTIVE_ONE, Ordering::Release);
        debug_assert_ne!(previous & ACTIVE_MASK, 0, "admission underflow");
    }
}

/// Close authority after admission has been closed.
pub struct Draining<'mapping, T: PodValue + PodSync> {
    attachment: Option<Attachment<'mapping, T>>,
}

impl<T: PodValue + PodSync> fmt::Debug for Draining<'_, T> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Draining")
            .field("drained", &self.is_drained())
            .finish_non_exhaustive()
    }
}

impl<'mapping, T: PodValue + PodSync> Draining<'mapping, T> {
    /// Returns true once only the owner's attachment remains and all admissions
    /// have departed.
    pub fn is_drained(&self) -> Result<bool, MappingError> {
        let snapshot = self.attachment().mapping.snapshot()?;
        Ok(snapshot.phase == Phase::Draining
            && snapshot.attachments == 1
            && snapshot.admissions == 0)
    }

    /// Borrows the payload exclusively after all other handles and guards drain.
    ///
    /// The borrow prevents closing through this value until it ends.
    pub fn try_payload_mut(&mut self) -> Result<&mut T, MappingError> {
        if !self.is_drained()? {
            return Err(MappingError::NotDrained);
        }
        let attachment = self
            .attachment
            .as_mut()
            .expect("live draining authority retains attachment");
        // SAFETY: the packed state proves this is the only attachment and no
        // admission guard exists. No public API exposes an uncounted reference.
        Ok(unsafe { attachment.payload.as_mut() })
    }

    /// Closes a fully drained mapping.
    ///
    /// On failure, close authority is returned with the error so the caller may
    /// wait and retry. Success does not unmap bytes; the host may do that only
    /// after ensuring no participant retains raw mapping capabilities.
    pub fn try_close(mut self) -> Result<ClosedMapping<'mapping>, (MappingError, Self)> {
        let attachment = self.attachment();
        let header = attachment.mapping.header();
        let expected = pack(Phase::Draining, 1, 0);
        if let Err(actual) = header.lifecycle.compare_exchange(
            expected,
            pack(Phase::Closed, 0, 0),
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            let error = match decode(actual) {
                Ok(snapshot)
                    if snapshot.phase == Phase::Draining
                        && (snapshot.attachments != 1 || snapshot.admissions != 0) =>
                {
                    MappingError::NotDrained
                }
                Ok(snapshot) => MappingError::WrongPhase {
                    expected: Phase::Draining,
                    actual: snapshot.phase,
                },
                Err(error) => error,
            };
            return Err((error, self));
        }

        let attachment = self
            .attachment
            .take()
            .expect("live draining authority retains attachment");
        let closed = ClosedMapping {
            base: attachment.mapping.base,
            len: attachment.mapping.len,
            marker: PhantomData,
        };
        // The successful CAS consumed the final shared attachment count.
        core::mem::forget(attachment);
        Ok(closed)
    }

    fn attachment(&self) -> &Attachment<'mapping, T> {
        self.attachment
            .as_ref()
            .expect("live draining authority retains attachment")
    }
}

impl<T: PodValue + PodSync> Drop for Draining<'_, T> {
    fn drop(&mut self) {
        if let Some(attachment) = self.attachment.as_ref() {
            let _ = poison_header(attachment.mapping.header());
        }
    }
}

/// Proof that the typed lifecycle reached `Closed`.
///
/// This is not by itself permission to unmap: the unsafe mapping contract also
/// requires the host to prevent stale raw pointers and nonconforming guests.
pub struct ClosedMapping<'mapping> {
    base: NonNull<u8>,
    len: usize,
    marker: PhantomData<&'mapping mut [u8]>,
}

impl fmt::Debug for ClosedMapping<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ClosedMapping")
            .field("base", &self.base)
            .field("len", &self.len)
            .finish()
    }
}

impl ClosedMapping<'_> {
    /// Returns the mapping base in this process.
    #[must_use]
    pub const fn base(&self) -> NonNull<u8> {
        self.base
    }

    /// Returns the accessible mapping length.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.len
    }

    /// Returns whether the mapping has no accessible bytes.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }
}

fn transition_to_draining(header: &MappingHeader) -> Result<(), MappingError> {
    let mut current = header.lifecycle.load(Ordering::Acquire);
    loop {
        let snapshot = decode(current)?;
        if snapshot.phase != Phase::Open {
            return Err(MappingError::WrongPhase {
                expected: Phase::Open,
                actual: snapshot.phase,
            });
        }
        match header.lifecycle.compare_exchange_weak(
            current,
            with_phase(current, Phase::Draining),
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return Ok(()),
            Err(actual) => current = actual,
        }
    }
}

fn poison_header(header: &MappingHeader) -> Result<(), MappingError> {
    let mut current = header.lifecycle.load(Ordering::Acquire);
    loop {
        let snapshot = decode(current)?;
        if snapshot.phase == Phase::Poisoned {
            return Ok(());
        }
        if snapshot.phase == Phase::Closed {
            return Err(MappingError::WrongPhase {
                expected: Phase::Open,
                actual: Phase::Closed,
            });
        }
        match header.lifecycle.compare_exchange_weak(
            current,
            with_phase(current, Phase::Poisoned),
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return Ok(()),
            Err(actual) => current = actual,
        }
    }
}

const fn pack(phase: Phase, attachments: u32, admissions: u32) -> u64 {
    ((phase as u64) << PHASE_SHIFT) | ((admissions as u64) << ACTIVE_SHIFT) | attachments as u64
}

const fn with_phase(value: u64, phase: Phase) -> u64 {
    (value & ((1_u64 << PHASE_SHIFT) - 1)) | ((phase as u64) << PHASE_SHIFT)
}

fn decode(value: u64) -> Result<LifecycleSnapshot, MappingError> {
    let raw_phase = (value >> PHASE_SHIFT) as u8;
    let phase = Phase::from_raw(raw_phase).ok_or(MappingError::CorruptLifecycle { raw: value })?;
    Ok(LifecycleSnapshot {
        phase,
        attachments: (value & ATTACHMENT_MASK) as u32,
        admissions: ((value & ACTIVE_MASK) >> ACTIVE_SHIFT) as u32,
    })
}

fn wrong_phase(expected: Phase, actual: u64) -> MappingError {
    match decode(actual) {
        Ok(snapshot) => MappingError::WrongPhase {
            expected,
            actual: snapshot.phase,
        },
        Err(error) => error,
    }
}

/// Failure while preparing, validating, attaching, or closing a mapping.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MappingError {
    /// The mapping base was null.
    NullMapping,
    /// The mapping length exceeds the maximum pointer offset.
    MappingTooLarge,
    /// Base plus length overflowed the local address type.
    AddressOverflow,
    /// The mapping cannot contain a complete header.
    MappingTooSmall {
        /// Minimum required bytes.
        required: usize,
        /// Supplied bytes.
        actual: usize,
    },
    /// The mapping base does not meet the header alignment.
    MisalignedMapping {
        /// Required byte alignment.
        required: usize,
        /// Supplied numeric address.
        address: usize,
    },
    /// The header magic is not recognized.
    BadMagic {
        /// Bytes found in the header.
        found: [u8; 8],
    },
    /// The header version is unsupported.
    UnsupportedVersion {
        /// Version found in the header.
        found: u32,
    },
    /// The recorded header length does not match this SDK.
    HeaderLength {
        /// Required length.
        expected: usize,
        /// Recorded length.
        found: usize,
    },
    /// The local and recorded mapping lengths differ.
    MappingLength {
        /// Locally mapped length.
        expected: u64,
        /// Recorded length.
        found: u64,
    },
    /// The authenticated build identity differs.
    BuildIdentity,
    /// The requested instance identity differs.
    InstanceIdentity,
    /// The packed lifecycle word contains an unknown phase.
    CorruptLifecycle {
        /// Complete raw lifecycle word.
        raw: u64,
    },
    /// The operation is not permitted in the current phase.
    WrongPhase {
        /// Required phase.
        expected: Phase,
        /// Observed phase.
        actual: Phase,
    },
    /// Payload offset or extent arithmetic overflowed.
    GeometryOverflow,
    /// The payload lies outside the mapping.
    PayloadExtent {
        /// Recorded payload offset.
        offset: u64,
        /// Required payload length.
        length: u64,
        /// Accessible mapping length.
        mapping_length: u64,
    },
    /// The payload address does not satisfy the requested type alignment.
    MisalignedPayload {
        /// Required byte alignment.
        required: usize,
        /// Computed numeric address.
        address: usize,
    },
    /// The separately recorded payload length differs from the type size.
    PayloadLength {
        /// Required payload length.
        expected: u64,
        /// Recorded payload length.
        found: u64,
    },
    /// The encoded layout descriptor is malformed.
    LayoutEncoding(DecodeError),
    /// The encoded layout does not match the requested type.
    LayoutMismatch(LayoutMismatch),
    /// The shared attachment counter is exhausted.
    AttachmentLimit,
    /// The shared admission counter is exhausted.
    AdmissionLimit,
    /// Other attachments or admissions still prevent exclusive teardown.
    NotDrained,
}

impl fmt::Display for MappingError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NullMapping => formatter.write_str("mapping base is null"),
            Self::MappingTooLarge => formatter.write_str("mapping is larger than isize::MAX"),
            Self::AddressOverflow => formatter.write_str("mapping address range overflows usize"),
            Self::MappingTooSmall { required, actual } => write!(
                formatter,
                "mapping is too small: need {required} bytes, found {actual}"
            ),
            Self::MisalignedMapping { required, address } => write!(
                formatter,
                "mapping address {address:#x} is not aligned to {required}"
            ),
            Self::BadMagic { found } => write!(formatter, "invalid mapping magic: {found:02x?}"),
            Self::UnsupportedVersion { found } => {
                write!(formatter, "unsupported mapping version {found}")
            }
            Self::HeaderLength { expected, found } => write!(
                formatter,
                "mapping header length mismatch: expected {expected}, found {found}"
            ),
            Self::MappingLength { expected, found } => write!(
                formatter,
                "mapping length mismatch: expected {expected}, found {found}"
            ),
            Self::BuildIdentity => formatter.write_str("mapping build identity mismatch"),
            Self::InstanceIdentity => formatter.write_str("mapping instance identity mismatch"),
            Self::CorruptLifecycle { raw } => {
                write!(formatter, "corrupt mapping lifecycle word {raw:#018x}")
            }
            Self::WrongPhase { expected, actual } => write!(
                formatter,
                "mapping phase mismatch: expected {expected:?}, found {actual:?}"
            ),
            Self::GeometryOverflow => formatter.write_str("payload geometry overflows"),
            Self::PayloadExtent {
                offset,
                length,
                mapping_length,
            } => write!(
                formatter,
                "payload {offset}..{} exceeds mapping length {mapping_length}",
                offset.saturating_add(*length)
            ),
            Self::MisalignedPayload { required, address } => write!(
                formatter,
                "payload address {address:#x} is not aligned to {required}"
            ),
            Self::PayloadLength { expected, found } => write!(
                formatter,
                "payload length mismatch: expected {expected}, found {found}"
            ),
            Self::LayoutEncoding(error) => write!(formatter, "invalid payload layout: {error}"),
            Self::LayoutMismatch(error) => error.fmt(formatter),
            Self::AttachmentLimit => formatter.write_str("mapping attachment limit reached"),
            Self::AdmissionLimit => formatter.write_str("mapping admission limit reached"),
            Self::NotDrained => formatter.write_str("mapping still has attachments or admissions"),
        }
    }
}

impl core::error::Error for MappingError {}

#[cfg(test)]
mod tests {
    use super::*;
    use core::mem::forget;
    use core::sync::atomic::AtomicU64;

    const BUILD: BuildIdentity = BuildIdentity::new([0x42; 32]);
    const INSTANCE: InstanceIdentity = InstanceIdentity::new([0x17; 16]);

    #[repr(align(64))]
    struct AlignedBytes([u8; 512]);

    fn prepared(bytes: &mut AlignedBytes) -> Mapping<'_> {
        // SAFETY: this stack buffer is aligned, writable, and exclusively
        // borrowed for the returned mapping lifetime.
        unsafe { RawMapping::from_raw_parts(bytes.0.as_mut_ptr(), bytes.0.len()).unwrap() }
            .prepare(BUILD, INSTANCE)
    }

    fn initialized(bytes: &mut AlignedBytes) -> Mapping<'_> {
        let mapping = prepared(bytes);
        let owner = mapping.try_initialize(AtomicU64::new(1)).unwrap();
        // Keep the lifecycle Open while deliberately corrupting authenticated
        // bytes through private test access.
        forget(owner);
        mapping
    }

    #[test]
    fn rejects_corrupt_immutable_header_and_lifecycle_fields() {
        let mut bytes = AlignedBytes([0; 512]);
        let mapping = prepared(&mut bytes);
        // SAFETY: no typed payload or concurrent access exists. This emulates a
        // corrupted backing object before open_existing.
        unsafe { ptr::addr_of_mut!((*mapping.header.as_ptr()).version).write(VERSION + 1) };
        // SAFETY: the test intentionally passes authenticated-but-corrupt bytes.
        assert!(matches!(
            unsafe {
                RawMapping::from_raw_parts(mapping.base.as_ptr(), mapping.len)
                    .unwrap()
                    .open_existing(BUILD, INSTANCE)
            },
            Err(MappingError::UnsupportedVersion { .. })
        ));

        let mut bytes = AlignedBytes([0; 512]);
        let mapping = prepared(&mut bytes);
        // SAFETY: same isolated corruption setup as above.
        unsafe { ptr::addr_of_mut!((*mapping.header.as_ptr()).header_len).write(1) };
        // SAFETY: open_existing must reject before typed access.
        assert!(matches!(
            unsafe {
                RawMapping::from_raw_parts(mapping.base.as_ptr(), mapping.len)
                    .unwrap()
                    .open_existing(BUILD, INSTANCE)
            },
            Err(MappingError::HeaderLength { .. })
        ));

        let mut bytes = AlignedBytes([0; 512]);
        let mapping = prepared(&mut bytes);
        // SAFETY: same isolated corruption setup as above.
        unsafe { ptr::addr_of_mut!((*mapping.header.as_ptr()).mapping_len).write(511) };
        // SAFETY: open_existing must reject before typed access.
        assert!(matches!(
            unsafe {
                RawMapping::from_raw_parts(mapping.base.as_ptr(), mapping.len)
                    .unwrap()
                    .open_existing(BUILD, INSTANCE)
            },
            Err(MappingError::MappingLength { .. })
        ));

        let mut bytes = AlignedBytes([0; 512]);
        let mapping = prepared(&mut bytes);
        mapping
            .header()
            .lifecycle
            .store(u64::MAX, Ordering::Release);
        assert!(matches!(
            mapping.snapshot(),
            Err(MappingError::CorruptLifecycle { .. })
        ));
    }

    #[test]
    fn rejects_corrupt_payload_geometry_and_descriptor() {
        let mut bytes = AlignedBytes([0; 512]);
        let mapping = initialized(&mut bytes);
        // SAFETY: no attachment/guard is active; this deliberately corrupts
        // private authenticated metadata for rejection coverage.
        unsafe { (*mapping.header().geometry.get()).payload_offset = 0 };
        assert!(matches!(
            mapping.attach::<AtomicU64>(),
            Err(MappingError::PayloadExtent { .. })
        ));

        let mut bytes = AlignedBytes([0; 512]);
        let mapping = initialized(&mut bytes);
        // SAFETY: same isolated corruption setup as above.
        unsafe { (*mapping.header().geometry.get()).payload_len = 1 };
        assert!(matches!(
            mapping.attach::<AtomicU64>(),
            Err(MappingError::PayloadLength { .. })
        ));

        let mut bytes = AlignedBytes([0; 512]);
        let mapping = initialized(&mut bytes);
        // SAFETY: same isolated corruption setup as above.
        unsafe { (*mapping.header().geometry.get()).descriptor[0] ^= 0xff };
        assert!(matches!(
            mapping.attach::<AtomicU64>(),
            Err(MappingError::LayoutEncoding(DecodeError::BadMagic { .. }))
        ));
    }
}
