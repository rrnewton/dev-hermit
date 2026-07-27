//! A fixed-address allocator for a caller-provided shared-memory region.
//!
//! [`FixedRegionAllocator`] uses Talc metadata containing absolute pointers.
//! Every process using an initialized allocator must therefore map the entire
//! claimed region at the same numeric virtual-address range. One process must
//! exclusively construct the allocator in its final shared location and call
//! [`FixedRegionAllocator::initialize`] before any other process calls
//! [`FixedRegionAllocator::attach`]. Constructing the control object by writing
//! zeroes, concurrently reconstructing it, or claiming the same region through
//! another allocator is not supported.
//!
//! The claimed bytes are allocator-owned metadata except while a block is
//! allocated to the caller. They must not be mutated through another mapping or
//! allocator. The process-shared spin lock is not robust: a process that exits
//! while holding it can permanently block every other user, including during
//! initialization.
//! Separately, initialization has no owner-recovery protocol. A process that
//! exits after winning the initialization transition but before publishing
//! readiness leaves the control object permanently in `Initializing`, even if
//! it was not holding the spin lock when it exited.
//!
//! Allocator-aware collections such as [`allocator_api2::vec::Vec`] and
//! [`allocator_api2::boxed::Box`] allocate their buffers here, but their headers
//! still contain absolute pointers and drop ownership. Storing an owning
//! collection in shared state requires a separate `ManuallyDrop`-style,
//! single-owner lifecycle protocol and external synchronization. All live
//! allocations must be deallocated through this allocator before the region is
//! unmapped, reused, or otherwise made inaccessible.
//!
//! `fork` duplicates process-local owning collection headers. Parent and child
//! dropping the two headers would deallocate the same shared block twice. Fork
//! only while allocator operations are quiescent and no allocator-backed owner
//! will be observed by both processes, or require the child to immediately
//! `exec` or `_exit` without unwinding or dropping inherited Rust values. The
//! examples fork only with the initialized control object and copyable allocator
//! handle live; they create every owning collection after the fork.
//!
//! Talc and `lock_api` use private Rust layouts. Before attaching, participants
//! must authenticate that they use the same complete SDK/code build, compiler
//! target and toolchain, locked dependency graph, and feature set. The
//! structural fingerprint includes the exposed wrapper layout and is versioned
//! for Talc 5.0.4, but it cannot introspect Talc's private field offsets. It is
//! an exact-build compatibility check, not a stable allocator ABI.
//!
//! # Strict-provenance status
//!
//! This module is experimental. The Linux tests establish that Talc's persisted
//! absolute pointers work operationally when every process maps the same pages
//! at the same numeric addresses. Rust's strict-provenance model does not yet
//! clearly guarantee that a typed pointer written by one process may be
//! dereferenced by another process, even at that address. Prefer pointer-free
//! integer offsets when the stronger contract is required.
//!
//! # Shared mapping example
//!
//! This example places both the allocator control object and its arena in one
//! anonymous shared mapping inherited across `fork`; it is a fork-only example.
//! The `Vec` header remains process-local on the stack and only its buffer uses
//! the shared arena.
//!
//! ```no_run
//! use core::{mem::size_of, ptr};
//! use shmem_pod::fixed_allocator::{
//!     allocator_api2::vec::Vec, FixedRegionAllocator,
//! };
//!
//! # #[cfg(target_os = "linux")]
//! # unsafe {
//! let page = libc::sysconf(libc::_SC_PAGESIZE) as usize;
//! let control_len = size_of::<FixedRegionAllocator>().div_ceil(page) * page;
//! let arena_len = 64 * 1024;
//! let mapping_len = control_len + arena_len;
//! let mapping = libc::mmap(
//!     ptr::null_mut(),
//!     mapping_len,
//!     libc::PROT_READ | libc::PROT_WRITE,
//!     libc::MAP_SHARED | libc::MAP_ANONYMOUS,
//!     -1,
//!     0,
//! );
//! assert_ne!(mapping, libc::MAP_FAILED);
//!
//! let control = mapping.cast::<FixedRegionAllocator>();
//! let arena = mapping.cast::<u8>().add(control_len);
//! control.write(FixedRegionAllocator::new());
//! let allocator = &*control;
//! let handle = allocator.initialize(arena, arena_len).unwrap();
//!
//! let mut values = Vec::new_in(handle);
//! values.extend([10_u64, 20, 30]);
//! assert!(handle.region().contains(
//!     values.as_ptr().cast(),
//!     values.capacity() * size_of::<u64>(),
//! ));
//! drop(values);
//!
//! // An attacher must supply the identical physical control/arena mappings,
//! // numeric addresses, length, and complete authenticated code build.
//! let attached = allocator.attach(arena, arena_len).unwrap();
//! assert_eq!(attached.region(), handle.region());
//!
//! core::ptr::drop_in_place(control);
//! assert_eq!(libc::munmap(mapping, mapping_len), 0);
//! # }
//! ```
//!
//! # Independent exec attachment
//!
//! `MAP_SHARED | MAP_ANONYMOUS` cannot be rediscovered after `exec`. A Linux
//! loader for independently exec'd processes needs to:
//!
//! 1. create file-backed pages with `memfd_create`, `shm_open`, or an equivalent;
//! 2. choose and record a page-aligned required address, control offset, arena
//!    offset, and arena length in a stable bootstrap header;
//! 3. initialize one `MAP_SHARED` mapping, then pass or inherit a descriptor
//!    across exec without `CLOEXEC`;
//! 4. attach with `MAP_SHARED | MAP_FIXED_NOREPLACE`, require the returned
//!    address to equal the recorded address, and reject collisions rather than
//!    replacing an existing mapping with `MAP_FIXED`;
//! 5. decode and validate [`crate::layout::LayoutDescriptor`], the authenticated
//!    code/build identity, mapping extents, and readiness before forming `self`
//!    or calling [`FixedRegionAllocator::attach`]; and
//! 6. unmap only after admission has closed, every process has detached, and
//!    every allocation has been returned.
//!
//! A useful stable bootstrap header binds at least: format/version, lifecycle
//! state, code artifact digest, SDK/build identity, encoded layout descriptor,
//! required virtual address, control offset, arena offset, and arena length.
//! This crate supplies the layout and allocator primitives, not that Linux
//! policy/transport layer.

#[doc(no_inline)]
pub use allocator_api2;

use allocator_api2::alloc::{AllocError, Allocator, Layout};
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::ptr::NonNull;
use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use talc::source::Manual;
use talc::{DefaultBinning, TalcLock};

use crate::sync::ProcessSpinRawMutex;

const UNINITIALIZED: u32 = 0;
const INITIALIZING: u32 = 1;
const READY: u32 = 2;
const POISONED: u32 = 3;

type SharedTalc = TalcLock<ProcessSpinRawMutex, Manual>;

/// Observable lifecycle state of a [`FixedRegionAllocator`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FixedRegionState {
    /// No process has started initialization.
    Uninitialized,
    /// A process won initialization but has not published a usable allocator.
    Initializing,
    /// The region was claimed and allocator handles may be created.
    Ready,
    /// Initialization failed and this control object cannot be reused.
    Poisoned,
}

/// A validated fixed-address region claimed by a [`FixedRegionAllocator`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FixedRegion {
    base: NonNull<u8>,
    size: usize,
}

impl FixedRegion {
    /// Returns the first byte of the claimed region.
    pub const fn base(self) -> NonNull<u8> {
        self.base
    }

    /// Returns the exact number of claimed bytes.
    pub const fn size(self) -> usize {
        self.size
    }

    /// Returns the address immediately after the claimed region.
    pub fn end_address(self) -> usize {
        self.base.as_ptr() as usize + self.size
    }

    /// Returns whether `pointer..pointer + size` lies wholly in this region.
    pub fn contains(self, pointer: *const u8, size: usize) -> bool {
        let start = pointer as usize;
        let Some(end) = start.checked_add(size) else {
            return false;
        };

        start >= self.base.as_ptr() as usize && end <= self.end_address()
    }
}

/// Failure to initialize or attach to a fixed shared allocator.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FixedRegionError {
    /// The supplied region starts at the null address.
    NullBase,
    /// The supplied base does not meet Talc's chunk alignment.
    MisalignedBase {
        /// Required base alignment in bytes.
        required: usize,
    },
    /// The supplied size is not a multiple of Talc's chunk alignment.
    MisalignedSize {
        /// Required size multiple in bytes.
        required: usize,
    },
    /// The supplied region cannot contain Talc's first-heap metadata.
    RegionTooSmall {
        /// Smallest accepted region size in bytes.
        minimum: usize,
    },
    /// Computing the address after the supplied region overflowed `usize`.
    AddressOverflow,
    /// The claimed region overlaps its allocator control object.
    OverlapsAllocator,
    /// Another process or thread is currently initializing this control object.
    InitializationInProgress,
    /// No process has initialized this control object yet.
    NotInitialized,
    /// This control object already owns a region.
    AlreadyInitialized,
    /// An earlier initialization attempt failed after winning the state transition.
    Poisoned,
    /// An attaching process supplied a different virtual-address range.
    RegionMismatch {
        /// Base address recorded by the initializing process.
        expected_base: usize,
        /// Region size recorded by the initializing process.
        expected_size: usize,
        /// Base address supplied by the attaching process.
        actual_base: usize,
        /// Region size supplied by the attaching process.
        actual_size: usize,
    },
    /// Talc rejected a region that passed the wrapper's geometry checks.
    ClaimFailed,
    /// Talc did not claim the exact aligned end supplied by the caller.
    ClaimDidNotCoverRegion {
        /// End address returned by Talc.
        claimed_end: usize,
        /// Exact end address requested by the caller.
        expected_end: usize,
    },
}

impl core::fmt::Display for FixedRegionError {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::NullBase => formatter.write_str("allocator region base is null"),
            Self::MisalignedBase { required } => {
                write!(
                    formatter,
                    "allocator region base is not {required}-byte aligned"
                )
            }
            Self::MisalignedSize { required } => write!(
                formatter,
                "allocator region size is not a multiple of {required} bytes"
            ),
            Self::RegionTooSmall { minimum } => write!(
                formatter,
                "allocator region is smaller than the {minimum}-byte minimum"
            ),
            Self::AddressOverflow => formatter.write_str("allocator region address overflows"),
            Self::OverlapsAllocator => {
                formatter.write_str("allocator arena overlaps its control object")
            }
            Self::InitializationInProgress => {
                formatter.write_str("allocator initialization is already in progress")
            }
            Self::NotInitialized => formatter.write_str("allocator is not initialized"),
            Self::AlreadyInitialized => formatter.write_str("allocator is already initialized"),
            Self::Poisoned => formatter.write_str("allocator initialization is poisoned"),
            Self::RegionMismatch {
                expected_base,
                expected_size,
                actual_base,
                actual_size,
            } => write!(
                formatter,
                "allocator region mismatch: expected 0x{expected_base:x}+{expected_size}, got 0x{actual_base:x}+{actual_size}"
            ),
            Self::ClaimFailed => formatter.write_str("Talc rejected the allocator region"),
            Self::ClaimDidNotCoverRegion {
                claimed_end,
                expected_end,
            } => write!(
                formatter,
                "Talc claimed through 0x{claimed_end:x}, expected 0x{expected_end:x}"
            ),
        }
    }
}

impl core::error::Error for FixedRegionError {}

/// Shared control state for one fixed-address allocation region.
///
/// This object contains both an initialization state machine and Talc's locked
/// allocator state. It must be constructed exactly once in shared storage. A
/// failed Talc claim permanently poisons the object because Talc may already
/// have written metadata before reporting an unexpected result.
pub struct FixedRegionAllocator {
    state: AtomicU32,
    region_base: AtomicUsize,
    region_size: AtomicUsize,
    allocator: SharedTalc,
}

impl FixedRegionAllocator {
    /// Required alignment of both the region base and its size.
    pub const REGION_ALIGNMENT: usize = talc::base::CHUNK_UNIT;

    /// Smallest aligned region size accepted for a first Talc heap.
    pub const MIN_REGION_SIZE: usize = align_up(
        talc::min_first_heap_size::<DefaultBinning>(),
        Self::REGION_ALIGNMENT,
    );

    /// Creates an uninitialized allocator control object.
    ///
    /// Creating a value is safe, but placing it into shared storage must be an
    /// exclusive operation completed before any process observes that storage.
    pub const fn new() -> Self {
        Self {
            state: AtomicU32::new(UNINITIALIZED),
            region_base: AtomicUsize::new(0),
            region_size: AtomicUsize::new(0),
            allocator: TalcLock::new(Manual),
        }
    }

    /// Returns the allocator's current lifecycle state.
    ///
    /// An unrecognized state word is treated as poisoned. Such a word indicates
    /// that the shared control bytes were mutated outside this API.
    pub fn state(&self) -> FixedRegionState {
        match self.state.load(Ordering::Acquire) {
            UNINITIALIZED => FixedRegionState::Uninitialized,
            INITIALIZING => FixedRegionState::Initializing,
            READY => FixedRegionState::Ready,
            POISONED => FixedRegionState::Poisoned,
            _ => FixedRegionState::Poisoned,
        }
    }

    /// Returns the initialized region, or `None` until initialization succeeds.
    pub fn region(&self) -> Option<FixedRegion> {
        if self.state.load(Ordering::Acquire) != READY {
            return None;
        }

        // The release store of READY publishes these relaxed stores.
        let base = self.region_base.load(Ordering::Relaxed) as *mut u8;
        let size = self.region_size.load(Ordering::Relaxed);
        NonNull::new(base).map(|base| FixedRegion { base, size })
    }

    /// Claims exactly `base..base + size` and returns an allocator handle.
    ///
    /// Validation errors do not change the lifecycle state. Once this method
    /// wins the `Uninitialized -> Initializing` transition, any failed or
    /// truncated Talc claim poisons the object. Concurrent losing callers get
    /// [`FixedRegionError::InitializationInProgress`] or
    /// [`FixedRegionError::AlreadyInitialized`].
    ///
    /// # Safety
    ///
    /// If the supplied geometry passes validation, the entire range must be a
    /// live, writable, exclusively allocator-managed shared mapping. It must
    /// remain mapped at this exact virtual address in every attached process
    /// until all allocations are deallocated and allocator use has stopped.
    /// The range must not overlap any other allocator region or live object and
    /// must not be mutated externally except through currently allocated blocks.
    /// The storage containing `self` must likewise stay mapped, initialized,
    /// fixed in place, and unreplaced until every process has dropped all
    /// handles and deallocated all blocks.
    pub unsafe fn initialize(
        &self,
        base: *mut u8,
        size: usize,
    ) -> Result<FixedRegionAllocatorHandle<'_>, FixedRegionError> {
        let expected_end = self.validate_region(base, size)?;

        if let Err(observed) = self.state.compare_exchange(
            UNINITIALIZED,
            INITIALIZING,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            return Err(match observed {
                INITIALIZING => FixedRegionError::InitializationInProgress,
                READY => FixedRegionError::AlreadyInitialized,
                POISONED => FixedRegionError::Poisoned,
                _ => FixedRegionError::Poisoned,
            });
        }

        let mut poison = PoisonOnDrop::new(&self.state);
        let claimed_end = {
            let mut allocator = self.allocator.lock();
            // SAFETY: The caller guarantees the validated range is exclusively
            // writable allocator storage for the full required lifetime.
            unsafe { allocator.claim(base, size) }
        };

        let Some(claimed_end) = claimed_end else {
            return Err(FixedRegionError::ClaimFailed);
        };

        let claimed_end = claimed_end.as_ptr() as usize;
        if claimed_end != expected_end {
            return Err(FixedRegionError::ClaimDidNotCoverRegion {
                claimed_end,
                expected_end,
            });
        }

        self.region_base.store(base as usize, Ordering::Relaxed);
        self.region_size.store(size, Ordering::Relaxed);
        poison.disarm();
        self.state.store(READY, Ordering::Release);

        Ok(FixedRegionAllocatorHandle { owner: self })
    }

    /// Attaches to the already initialized region at exactly `base` and `size`.
    ///
    /// The absolute range is compared with the values published by the
    /// initializer. A relocated or differently sized mapping is rejected.
    ///
    /// # Safety
    ///
    /// The supplied range must identify the same shared physical storage that
    /// the initializing process claimed, mapped writable at the identical
    /// numeric virtual address. `self` must likewise be the same physical
    /// allocator control object mapped at its identical address. Every process
    /// must authenticate the same complete SDK/code build, compiler target and
    /// toolchain, locked dependency graph, and feature set. Both mappings must
    /// outlive the returned handle and every allocation made through it, remain
    /// fixed in place, initialized, and unreplaced for that entire interval.
    /// The caller also accepts the module's documented cross-process
    /// strict-provenance limitation for Talc's persisted typed pointers.
    pub unsafe fn attach(
        &self,
        base: *mut u8,
        size: usize,
    ) -> Result<FixedRegionAllocatorHandle<'_>, FixedRegionError> {
        self.validate_region(base, size)?;

        match self.state.load(Ordering::Acquire) {
            UNINITIALIZED => return Err(FixedRegionError::NotInitialized),
            INITIALIZING => return Err(FixedRegionError::InitializationInProgress),
            READY => {}
            POISONED => return Err(FixedRegionError::Poisoned),
            _ => return Err(FixedRegionError::Poisoned),
        }

        // The acquire load of READY observes the initializing process's stores.
        let expected_base = self.region_base.load(Ordering::Relaxed);
        let expected_size = self.region_size.load(Ordering::Relaxed);
        let actual_base = base as usize;
        if actual_base != expected_base || size != expected_size {
            return Err(FixedRegionError::RegionMismatch {
                expected_base,
                expected_size,
                actual_base,
                actual_size: size,
            });
        }

        Ok(FixedRegionAllocatorHandle { owner: self })
    }

    fn validate_region(&self, base: *mut u8, size: usize) -> Result<usize, FixedRegionError> {
        if base.is_null() {
            return Err(FixedRegionError::NullBase);
        }

        let base_address = base as usize;
        if base_address % Self::REGION_ALIGNMENT != 0 {
            return Err(FixedRegionError::MisalignedBase {
                required: Self::REGION_ALIGNMENT,
            });
        }
        if size % Self::REGION_ALIGNMENT != 0 {
            return Err(FixedRegionError::MisalignedSize {
                required: Self::REGION_ALIGNMENT,
            });
        }
        if size < Self::MIN_REGION_SIZE {
            return Err(FixedRegionError::RegionTooSmall {
                minimum: Self::MIN_REGION_SIZE,
            });
        }

        let end = base_address
            .checked_add(size)
            .ok_or(FixedRegionError::AddressOverflow)?;
        let allocator_start = self as *const Self as usize;
        let allocator_end = allocator_start
            .checked_add(size_of::<Self>())
            .ok_or(FixedRegionError::AddressOverflow)?;
        if base_address < allocator_end && allocator_start < end {
            return Err(FixedRegionError::OverlapsAllocator);
        }

        Ok(end)
    }
}

impl Default for FixedRegionAllocator {
    fn default() -> Self {
        Self::new()
    }
}

// SAFETY: The control object has no destructor or process-local resource. Its
// initialized Talc state deliberately contains absolute pointers into the
// fixed-address region, and its transitive synchronization state is stored in
// the shared object itself.
unsafe impl crate::FixedAddressPodValue for FixedRegionAllocator {
    const FINGERPRINT: u128 = {
        assert!(
            !needs_drop::<Self>(),
            "fixed region allocators must not need drop"
        );
        let state = crate::__private::mix_bytes(
            crate::__private::FINGERPRINT_SEED,
            b"shmem-pod-fixed-region-allocator-talc-5.0.4-v1",
        );
        let state = crate::__private::mix_usize(state, size_of::<Self>());
        let mut state = crate::__private::mix_usize(state, align_of::<Self>());

        state = crate::__private::mix_bytes(state, b"state");
        state = crate::__private::mix_usize(state, offset_of!(Self, state));
        state = crate::__private::mix_usize(state, size_of::<AtomicU32>());
        state = crate::__private::mix_usize(state, align_of::<AtomicU32>());

        state = crate::__private::mix_bytes(state, b"region_base");
        state = crate::__private::mix_usize(state, offset_of!(Self, region_base));
        state = crate::__private::mix_usize(state, size_of::<AtomicUsize>());
        state = crate::__private::mix_usize(state, align_of::<AtomicUsize>());

        state = crate::__private::mix_bytes(state, b"region_size");
        state = crate::__private::mix_usize(state, offset_of!(Self, region_size));
        state = crate::__private::mix_usize(state, size_of::<AtomicUsize>());
        state = crate::__private::mix_usize(state, align_of::<AtomicUsize>());

        state = crate::__private::mix_bytes(state, b"allocator");
        state = crate::__private::mix_usize(state, offset_of!(Self, allocator));
        state = crate::__private::mix_usize(state, size_of::<SharedTalc>());
        state = crate::__private::mix_usize(state, align_of::<SharedTalc>());
        crate::__private::finish(state)
    };
}

// SAFETY: Initialization is published atomically and every safe allocator
// mutation is serialized by the process-shared raw mutex. The documented
// owner-death limitation affects progress, not data-race freedom.
unsafe impl crate::PodSync for FixedRegionAllocator {}

/// A copyable allocator-api2 handle to a ready fixed shared region.
///
/// The handle borrows the shared control object and implements
/// [`Allocator`]. It contains an absolute reference, so it must not be copied
/// into storage observed at a different virtual address. Dropping a handle does
/// not release the claimed region.
#[derive(Clone, Copy)]
pub struct FixedRegionAllocatorHandle<'a> {
    owner: &'a FixedRegionAllocator,
}

impl FixedRegionAllocatorHandle<'_> {
    /// Returns the exact region backing this handle.
    pub fn region(self) -> FixedRegion {
        // Handles are only constructed after observing READY, which is permanent.
        self.owner
            .region()
            .expect("ready allocator lost its region")
    }
}

// SAFETY: Every operation delegates to the same process-shared TalcLock. The
// handle's lifetime keeps the control object borrowed, and construction is only
// possible after a successful region claim or an exact-address attachment.
unsafe impl Allocator for FixedRegionAllocatorHandle<'_> {
    fn allocate(&self, layout: Layout) -> Result<NonNull<[u8]>, AllocError> {
        if layout.size() == 0 {
            return Ok(dangling_slice(layout));
        }
        Allocator::allocate(&self.owner.allocator, layout)
    }

    fn allocate_zeroed(&self, layout: Layout) -> Result<NonNull<[u8]>, AllocError> {
        if layout.size() == 0 {
            return Ok(dangling_slice(layout));
        }
        Allocator::allocate_zeroed(&self.owner.allocator, layout)
    }

    unsafe fn deallocate(&self, pointer: NonNull<u8>, layout: Layout) {
        if layout.size() != 0 {
            // SAFETY: The caller upholds Allocator::deallocate's contract.
            unsafe { Allocator::deallocate(&self.owner.allocator, pointer, layout) };
        }
    }

    unsafe fn grow(
        &self,
        pointer: NonNull<u8>,
        old_layout: Layout,
        new_layout: Layout,
    ) -> Result<NonNull<[u8]>, AllocError> {
        if old_layout.size() == 0 {
            return self.allocate(new_layout);
        }
        // SAFETY: The caller upholds Allocator::grow's pointer and layout contract.
        unsafe { Allocator::grow(&self.owner.allocator, pointer, old_layout, new_layout) }
    }

    unsafe fn grow_zeroed(
        &self,
        pointer: NonNull<u8>,
        old_layout: Layout,
        new_layout: Layout,
    ) -> Result<NonNull<[u8]>, AllocError> {
        if old_layout.size() == 0 {
            return self.allocate_zeroed(new_layout);
        }
        // SAFETY: The caller upholds Allocator::grow_zeroed's pointer and layout contract.
        unsafe { Allocator::grow_zeroed(&self.owner.allocator, pointer, old_layout, new_layout) }
    }

    unsafe fn shrink(
        &self,
        pointer: NonNull<u8>,
        old_layout: Layout,
        new_layout: Layout,
    ) -> Result<NonNull<[u8]>, AllocError> {
        if new_layout.size() == 0 {
            if old_layout.size() != 0 {
                // SAFETY: The caller supplied a currently allocated block fitting old_layout.
                unsafe {
                    Allocator::deallocate(&self.owner.allocator, pointer, old_layout);
                }
            }
            return Ok(dangling_slice(new_layout));
        }

        // SAFETY: The caller upholds Allocator::shrink's pointer and layout contract.
        unsafe { Allocator::shrink(&self.owner.allocator, pointer, old_layout, new_layout) }
    }
}

struct PoisonOnDrop<'a> {
    state: &'a AtomicU32,
    armed: bool,
}

impl<'a> PoisonOnDrop<'a> {
    fn new(state: &'a AtomicU32) -> Self {
        Self { state, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for PoisonOnDrop<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.state.store(POISONED, Ordering::Release);
        }
    }
}

const fn align_up(value: usize, alignment: usize) -> usize {
    (value + alignment - 1) & !(alignment - 1)
}

fn dangling_slice(layout: Layout) -> NonNull<[u8]> {
    // Layout alignment is a nonzero power of two, and therefore a non-null
    // aligned integer is a valid dangling address for a zero-sized allocation.
    let data = NonNull::new(layout.align() as *mut u8).expect("layout alignment is nonzero");
    NonNull::slice_from_raw_parts(data, 0)
}
