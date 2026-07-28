//! Closeable, crash-conservative admission built on [`crate::snzi::Snzi`].
//!
//! [`CloseableSnzi`] turns an SNZI presence indicator into a one-shot teardown
//! barrier. An entrant first reserves publication in a gate word, publishes its
//! SNZI arrival, and only then releases the reservation. Departures also reserve
//! the gate through their final shared-memory access. The terminal drain scan
//! blocks new departures, proves the tree quiescent, and seals the generation.
//!
//! The protocol deliberately fails closed after process death. A process which
//! dies during an entry or departure leaks a reservation; one which dies while
//! admitted leaks an arrival. A process dying during the terminal scan leaves
//! the gate in its checking state. Every case prevents
//! [`CloseableSnzi::is_drained`] from returning true. There is no lease-based
//! stealing: a paused process may resume, so elapsed time alone cannot safely
//! transfer ownership of arbitrary Rust state. Applications which require
//! recovery must discard or repair the whole shared generation under an external
//! supervisor and fencing protocol.

use core::fmt;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::sync::atomic::{AtomicU64, Ordering};

use crate::snzi::{ArrivalToken, PoisonReason, Snzi, SnziError, SnziSnapshot};
use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const CACHE_LINE: usize = 64;
const CLOSED_BIT: u64 = 1_u64 << 63;
const POISONED_BIT: u64 = 1_u64 << 62;
const CHECKING_BIT: u64 = 1_u64 << 61;
const DRAINED_BIT: u64 = 1_u64 << 60;
const RESERVATION_MASK: u64 = DRAINED_BIT - 1;

#[repr(align(64))]
struct CacheAlignedGate {
    value: AtomicU64,
    _padding: [u8; CACHE_LINE - size_of::<AtomicU64>()],
}

impl CacheAlignedGate {
    const fn new() -> Self {
        Self {
            value: AtomicU64::new(0),
            _padding: [0; CACHE_LINE - size_of::<AtomicU64>()],
        }
    }
}

/// An error returned while trying to enter a [`CloseableSnzi`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TryEnterError {
    /// Admission has closed permanently.
    Closed,
    /// The admission gate is terminally poisoned.
    Poisoned,
    /// The 60-bit transient-operation count was exhausted.
    ///
    /// Reaching this state poisons the gate so teardown cannot mistake damaged
    /// accounting for quiescence.
    ReservationExhausted,
    /// The underlying SNZI rejected or poisoned the arrival.
    Snzi(SnziError),
}

impl fmt::Display for TryEnterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Closed => formatter.write_str("admission is closed"),
            Self::Poisoned => formatter.write_str("admission is poisoned"),
            Self::ReservationExhausted => {
                formatter.write_str("admission publication reservations are exhausted")
            }
            Self::Snzi(error) => write!(formatter, "SNZI admission failed: {error}"),
        }
    }
}

impl core::error::Error for TryEnterError {}

/// An error returned while departing a [`CloseableSnzi`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DepartError {
    /// Admission accounting is terminally poisoned.
    Poisoned,
    /// The barrier has already sealed its drained state.
    ///
    /// Safe linear token use cannot encounter this state. It indicates an
    /// unsafe raw-token ownership violation or damaged shared bytes.
    AlreadyDrained,
    /// The 60-bit transient-operation count was exhausted.
    ReservationExhausted,
    /// The underlying SNZI rejected or poisoned the departure.
    Snzi(SnziError),
}

impl fmt::Display for DepartError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Poisoned => formatter.write_str("admission is poisoned"),
            Self::AlreadyDrained => formatter.write_str("admission is already drained"),
            Self::ReservationExhausted => {
                formatter.write_str("admission operation reservations are exhausted")
            }
            Self::Snzi(error) => write!(formatter, "SNZI departure failed: {error}"),
        }
    }
}

impl core::error::Error for DepartError {}

/// A best-effort diagnostic snapshot of a closeable admission barrier.
///
/// The gate word is observed atomically, but the SNZI snapshot is collected
/// field by field. Use [`CloseableSnzi::is_drained`] for the synchronization
/// decision; this type is intended for diagnostics after stopping callers.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AdmissionSnapshot {
    /// Whether the one-shot admission gate has closed.
    pub closed: bool,
    /// Whether admission accounting has terminally poisoned.
    pub poisoned: bool,
    /// Whether one process is performing the terminal quiescence scan.
    pub checking_drain: bool,
    /// Whether the terminal drained state has been sealed.
    pub drained: bool,
    /// Entry publications or departures which have not completely returned.
    pub transient_reservations: u64,
    /// The underlying SNZI diagnostic snapshot.
    pub snzi: SnziSnapshot,
}

impl AdmissionSnapshot {
    /// Returns whether this diagnostic sample appears fully drained.
    ///
    /// This is not a substitute for [`CloseableSnzi::is_drained`] while
    /// departures may still be running.
    #[inline]
    pub const fn appears_drained(&self) -> bool {
        self.closed
            && !self.poisoned
            && !self.checking_drain
            && self.drained
            && self.transient_reservations == 0
            && self.snzi.is_quiescent()
    }
}

/// Linear evidence for one admitted participant.
///
/// The token is neither [`Copy`] nor [`Clone`], and departure consumes it. It
/// intentionally has no `Drop` implementation: implicit destruction after
/// `fork`, cancellation, or panic cannot decide which process owns the logical
/// departure. Dropping or losing a token leaks presence and makes teardown fail
/// closed.
#[must_use = "an admitted participant must eventually depart"]
pub struct AdmissionToken<'a, const NODES: usize> {
    issuer: &'a CloseableSnzi<NODES>,
    inner: ArrivalToken<'a, NODES>,
}

impl<const NODES: usize> AdmissionToken<'_, NODES> {
    /// Returns the zero-based SNZI leaf selected by this participant.
    #[inline]
    pub const fn leaf(&self) -> usize {
        self.inner.leaf()
    }

    /// Returns the SNZI activation generation carried by this token.
    #[inline]
    pub const fn generation(&self) -> u64 {
        self.inner.generation()
    }

    /// Performs this participant's matching departure.
    #[inline]
    pub fn depart(self) -> Result<(), DepartError> {
        let reservation = self.issuer.reserve_departure()?;
        let result = self.inner.depart().map_err(DepartError::Snzi);
        // Keep the reservation until after the SNZI method has performed its
        // final shared-memory access and returned.
        drop(reservation);
        result
    }

    /// Encodes this token for an audited scalar C ABI.
    ///
    /// The resulting value has the same exact-instance and single-consumption
    /// requirements as [`Snzi::depart_raw`].
    #[inline]
    pub const fn into_raw(self) -> u64 {
        self.inner.into_raw()
    }
}

impl<const NODES: usize> fmt::Debug for AdmissionToken<'_, NODES> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AdmissionToken")
            .field("leaf", &self.leaf())
            .field("generation", &self.generation())
            .finish_non_exhaustive()
    }
}

impl<const NODES: usize> PartialEq for AdmissionToken<'_, NODES> {
    fn eq(&self, other: &Self) -> bool {
        core::ptr::eq(self.issuer, other.issuer) && self.inner == other.inner
    }
}

impl<const NODES: usize> Eq for AdmissionToken<'_, NODES> {}

/// A one-shot admission gate plus hierarchical presence indicator.
///
/// `NODES` has the same complete four-way-tree requirements as [`Snzi`]. The
/// type contains no pointers and can be mapped at different virtual addresses.
/// It deliberately has Rust's native layout rather than `repr(C)`; its complete
/// compiled layout is bound into [`FixedAddressPodValue::FINGERPRINT`]. Foreign
/// code should call an audited executable-pod ABI instead of reproducing this
/// private field layout.
pub struct CloseableSnzi<const NODES: usize> {
    gate: CacheAlignedGate,
    snzi: Snzi<NODES>,
}

impl<const NODES: usize> CloseableSnzi<NODES> {
    /// Creates an open, empty admission barrier.
    ///
    /// # Panics
    ///
    /// Panics if `NODES` is not a supported [`Snzi`] tree size.
    pub const fn new() -> Self {
        Self {
            gate: CacheAlignedGate::new(),
            snzi: Snzi::new(),
        }
    }

    /// Initializes a barrier directly in its final shared storage.
    ///
    /// This avoids compiler-emitted `memcpy` or `memset` calls in freestanding
    /// executable-pod code.
    ///
    /// # Safety
    ///
    /// `destination` must be non-null, aligned for `Self`, exclusively writable
    /// for `size_of::<Self>()` bytes, and valid for the completed object's full
    /// lifetime. `NODES` must satisfy [`Snzi::is_valid_node_count`]. The storage
    /// must be uninitialized or belong to an old generation which no process can
    /// still access.
    #[inline]
    pub unsafe fn initialize_at(destination: *mut Self) {
        assert!(
            Snzi::<NODES>::is_valid_node_count(),
            "SNZI node count must describe complete 4-ary levels with at most 65,536 leaves"
        );

        // SAFETY: The caller grants exclusive final storage. Every byte of the
        // gate is initialized here and Snzi initializes all of its own bytes.
        unsafe {
            initialize_gate(core::ptr::addr_of_mut!((*destination).gate));
            Snzi::<NODES>::initialize_at(core::ptr::addr_of_mut!((*destination).snzi));
        }
    }

    /// Returns the number of selectable SNZI leaves.
    #[inline]
    pub const fn leaf_count(&self) -> usize {
        self.snzi.leaf_count()
    }

    /// Attempts to admit one participant.
    ///
    /// The operation reserves the gate before beginning the potentially
    /// multi-step SNZI arrival. Once [`Self::close`] linearizes, later
    /// reservations are rejected. A reservation which linearized first may
    /// finish and returns a token which must be departed before drain completes.
    pub fn try_enter(&self, leaf: usize) -> Result<AdmissionToken<'_, NODES>, TryEnterError> {
        let reservation = self.reserve()?;
        let arrival = match self.snzi.arrive(leaf) {
            Ok(arrival) => arrival,
            Err(error) => {
                if matches!(error, SnziError::Poisoned(_)) {
                    self.poison_gate();
                }
                return Err(TryEnterError::Snzi(error));
            }
        };

        // Releasing the transient reservation only after `arrive` returns is
        // the publication edge used by `is_drained`.
        drop(reservation);
        Ok(AdmissionToken {
            issuer: self,
            inner: arrival,
        })
    }

    /// Permanently closes admission.
    ///
    /// Returns `true` only to the caller which changed the gate from open to
    /// closed. Calls after the first are idempotent and return `false`.
    #[inline]
    pub fn close(&self) -> bool {
        self.gate.value.fetch_or(CLOSED_BIT, Ordering::SeqCst) & CLOSED_BIT == 0
    }

    /// Returns whether admission has closed.
    #[inline]
    pub fn is_closed(&self) -> bool {
        self.gate.value.load(Ordering::SeqCst) & CLOSED_BIT != 0
    }

    /// Returns whether the barrier is closed, healthy, and fully quiescent.
    ///
    /// A true result is stable for the lifetime of this one-shot generation:
    /// closing and reservation use the same atomic word, so no later entrant can
    /// begin; zero reservations rule out a hidden publication or a departure
    /// which has not returned; and the final SNZI scan rules out unmatched
    /// arrivals or poison. The scan temporarily blocks departure starts and then
    /// seals a terminal `DRAINED` state. This prevents reclamation from racing
    /// the last departure's method tail after it zeroes the SNZI root.
    ///
    /// Process death before the terminal state is sealed leaves an arrival, a
    /// reservation, or the checking state behind and therefore fails closed.
    /// This method is a state transition, not a read-only diagnostic operation.
    #[inline]
    pub fn is_drained(&self) -> bool {
        let mut gate = self.gate.value.load(Ordering::SeqCst);
        loop {
            if gate & DRAINED_BIT != 0 {
                return true;
            }
            if gate & (CLOSED_BIT | POISONED_BIT | CHECKING_BIT) != CLOSED_BIT
                || gate & RESERVATION_MASK != 0
            {
                return false;
            }

            // A completed active token should get an immediate opportunity to
            // reserve its departure. Repeated scanners must not continually
            // seize CHECKING and starve it. Closed+count(0) makes this query
            // stable against new or hidden arrivals.
            if self.snzi.query() {
                return false;
            }

            let checking = gate | CHECKING_BIT;
            match self.gate.value.compare_exchange_weak(
                gate,
                checking,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => {
                    let quiescent = self.snzi.is_quiescent();
                    let next = if quiescent {
                        (checking & !CHECKING_BIT) | DRAINED_BIT
                    } else {
                        checking & !CHECKING_BIT
                    };
                    if self
                        .gate
                        .value
                        .compare_exchange(checking, next, Ordering::SeqCst, Ordering::SeqCst)
                        .is_err()
                    {
                        // Only terminal poison may legitimately race this
                        // private checking state. Preserve every other bit.
                        self.gate.value.fetch_and(!CHECKING_BIT, Ordering::SeqCst);
                        return false;
                    }
                    return quiescent;
                }
                Err(observed) => gate = observed,
            }
        }
    }

    /// Returns whether a completed or poisoned SNZI arrival may be present.
    ///
    /// This does not include transient reservations. Use [`Self::is_drained`]
    /// for teardown decisions.
    #[inline]
    pub fn query(&self) -> bool {
        self.snzi.query()
    }

    /// Returns the underlying terminal SNZI poison reason, if any.
    #[inline]
    pub fn poison_reason(&self) -> Option<PoisonReason> {
        self.snzi.poison_reason()
    }

    /// Collects a best-effort diagnostic snapshot.
    pub fn debug_snapshot(&self) -> AdmissionSnapshot {
        let gate = self.gate.value.load(Ordering::SeqCst);
        AdmissionSnapshot {
            closed: gate & CLOSED_BIT != 0,
            poisoned: gate & POISONED_BIT != 0,
            checking_drain: gate & CHECKING_BIT != 0,
            drained: gate & DRAINED_BIT != 0,
            transient_reservations: gate & RESERVATION_MASK,
            snzi: self.snzi.debug_snapshot(),
        }
    }

    /// Decodes and departs a raw token issued by this exact barrier.
    ///
    /// # Safety
    ///
    /// `raw` must be the sole unconsumed scalar token returned by
    /// [`AdmissionToken::into_raw`] for this exact instance. The integer itself
    /// cannot enforce instance identity or linear ownership.
    #[inline]
    pub unsafe fn depart_raw(&self, raw: u64) -> Result<(), DepartError> {
        let reservation = self.reserve_departure()?;
        // SAFETY: Forwarded exact-instance and single-consumption requirements
        // are stated by this method's contract.
        let result = unsafe { self.snzi.depart_raw(raw) }.map_err(DepartError::Snzi);
        drop(reservation);
        result
    }

    fn reserve(&self) -> Result<Reservation<'_, NODES>, TryEnterError> {
        let mut state = self.gate.value.load(Ordering::SeqCst);
        loop {
            if state & CLOSED_BIT != 0 {
                return Err(TryEnterError::Closed);
            }
            if state & POISONED_BIT != 0 {
                return Err(TryEnterError::Poisoned);
            }
            if state & RESERVATION_MASK == RESERVATION_MASK {
                self.poison_gate();
                return Err(TryEnterError::ReservationExhausted);
            }

            match self.gate.value.compare_exchange_weak(
                state,
                state + 1,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return Ok(Reservation { owner: self }),
                Err(observed) => state = observed,
            }
        }
    }

    fn reserve_departure(&self) -> Result<Reservation<'_, NODES>, DepartError> {
        let mut state = self.gate.value.load(Ordering::SeqCst);
        loop {
            if state & POISONED_BIT != 0 {
                return Err(DepartError::Poisoned);
            }
            if state & DRAINED_BIT != 0 {
                return Err(DepartError::AlreadyDrained);
            }
            if state & CHECKING_BIT != 0 {
                // A bounded tree scan normally makes this brief. If the
                // checking process dies, spinning fails closed; a supervisor
                // must discard the mapping generation.
                core::hint::spin_loop();
                state = self.gate.value.load(Ordering::SeqCst);
                continue;
            }
            if state & RESERVATION_MASK == RESERVATION_MASK {
                self.poison_gate();
                return Err(DepartError::ReservationExhausted);
            }

            match self.gate.value.compare_exchange_weak(
                state,
                state + 1,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return Ok(Reservation { owner: self }),
                Err(observed) => state = observed,
            }
        }
    }

    fn release_reservation(&self) {
        let mut state = self.gate.value.load(Ordering::SeqCst);
        loop {
            if state & RESERVATION_MASK == 0 {
                self.poison_gate();
                debug_assert!(false, "admission reservation underflow");
                return;
            }

            match self.gate.value.compare_exchange_weak(
                state,
                state - 1,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return,
                Err(observed) => state = observed,
            }
        }
    }

    #[inline]
    fn poison_gate(&self) {
        self.gate.value.fetch_or(POISONED_BIT, Ordering::SeqCst);
    }
}

impl<const NODES: usize> Default for CloseableSnzi<NODES> {
    fn default() -> Self {
        Self::new()
    }
}

struct Reservation<'a, const NODES: usize> {
    owner: &'a CloseableSnzi<NODES>,
}

impl<const NODES: usize> Drop for Reservation<'_, NODES> {
    fn drop(&mut self) {
        self.owner.release_reservation();
    }
}

#[inline(always)]
unsafe fn initialize_gate(destination: *mut CacheAlignedGate) {
    // SAFETY: The caller provides one exclusive, aligned cache-line object. The
    // volatile byte loop prevents lowering to a freestanding-hostile memset.
    unsafe {
        core::ptr::addr_of_mut!((*destination).value).write(AtomicU64::new(0));
        let padding = core::ptr::addr_of_mut!((*destination)._padding).cast::<u8>();
        let mut index = 0;
        while index < CACHE_LINE - size_of::<AtomicU64>() {
            padding.add(index).write_volatile(0);
            index += 1;
        }
    }
}

// SAFETY: The type contains only an atomic scalar, byte padding, and a
// pointer-free Snzi. Its native Rust field layout is captured by the fingerprint.
unsafe impl<const NODES: usize> FixedAddressPodValue for CloseableSnzi<NODES> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod values must not need drop");
        let state =
            __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-closeable-snzi-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        let state = __private::mix_usize(state, NODES);
        let state = __private::mix_usize(state, offset_of!(Self, gate));
        let state = __private::mix_usize(state, offset_of!(Self, snzi));
        let state = __private::mix_usize(state, offset_of!(CacheAlignedGate, value));
        let state = __private::mix_u128(state, AtomicU64::FINGERPRINT);
        __private::finish(__private::mix_u128(state, Snzi::<NODES>::FINGERPRINT))
    };
}

// SAFETY: All persisted fields are address-independent counters, flags, and
// byte padding.
unsafe impl<const NODES: usize> PodValue for CloseableSnzi<NODES> {}

// SAFETY: The safe shared API mutates persisted state only with 64-bit atomics.
unsafe impl<const NODES: usize> PodSync for CloseableSnzi<NODES> {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transient_reservation_blocks_drain() {
        let barrier = CloseableSnzi::<4>::new();
        let reservation = barrier.reserve().unwrap();
        assert!(barrier.close());
        assert!(!barrier.is_drained());
        assert_eq!(barrier.debug_snapshot().transient_reservations, 1);

        drop(reservation);
        assert!(barrier.is_drained());
    }

    #[test]
    fn departure_tail_reservation_blocks_terminal_drain() {
        let barrier = CloseableSnzi::<4>::new();
        let token = barrier.try_enter(0).unwrap();
        barrier.close();

        let departure = barrier.reserve_departure().unwrap();
        token.inner.depart().unwrap();
        assert!(!barrier.snzi.query());
        assert!(
            !barrier.is_drained(),
            "the operation reservation must cover the departure method tail"
        );

        drop(departure);
        assert!(barrier.is_drained());
    }

    #[test]
    fn gate_poison_is_fail_closed() {
        let barrier = CloseableSnzi::<4>::new();
        barrier.poison_gate();
        assert_eq!(barrier.try_enter(0), Err(TryEnterError::Poisoned));
        barrier.close();
        assert!(!barrier.is_drained());
        assert!(barrier.debug_snapshot().poisoned);
    }
}
