//! A scalable, one-shot closable nonzero indicator (C-SNZI).
//!
//! [`Csnzi`] is based on Figure 2 of Lev, Luchangco, and Olszewski,
//! *Scalable Reader-Writer Locks* (SPAA 2009). Each tree node represents its
//! whole subtree at its parent with one arrival. A second arrival at an already
//! active leaf changes only that leaf's cache line; ancestor and root updates
//! are therefore amortized over leaf 0-to-1 and 1-to-0 transitions.
//!
//! The paper's parent-before-child activation order is essential. An operation
//! which observes the root open may later find it closed. If its leaf is idle,
//! it represents the activation at the parent before changing the leaf, so a
//! closed zero root rejects the operation without child cleanup. Concurrent
//! activators may temporarily over-represent a subtree and compensate after one
//! of them publishes the child count.
//!
//! This implementation is deliberately one-shot: [`Csnzi::close`] is permanent.
//! The root also has explicit departure-tail states. The operation which removes
//! the final root contribution first makes the root non-drainable, unwinds the
//! tree, verifies that every node is idle, and performs the final root CAS as
//! its last shared-memory access. Thus [`Csnzi::is_drained`] cannot race the
//! final departure's control-memory tail. The barrier's own pages remain under
//! an external lifetime protocol and must not be unmapped merely because the
//! protected payload may now be reclaimed. Non-final departures likewise make
//! their successful decrement their last shared-memory access, but an outer
//! attachment protocol is still required before unmapping the control object.
//!
//! # Crash and fork contract
//!
//! Process death is conservative. Death during activation can leak an ancestor
//! contribution; death during departure can leak a child or ancestor
//! contribution; and death after selecting the final departure leaves a tail
//! state. All prevent terminal drain. There is no timeout, lease expiry, or
//! ownership stealing because a stopped process may resume. Recovery requires
//! an external supervisor to fence old users and discard or repair the complete
//! generation.
//!
//! A typed [`CsnziToken`] is linear in safe Rust and borrows its issuing object.
//! `fork` can physically duplicate it anyway. Exactly one process may consume a
//! duplicated token; fork before admission, or make every non-owner proceed
//! directly to `exec` or `_exit` without using or unwinding the token.

use core::fmt;
use core::hint::spin_loop;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::sync::atomic::{AtomicU64, Ordering};

use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const FANOUT: usize = 4;
const CACHE_LINE: usize = 64;

const NODE_COUNT_BITS: u32 = 16;
const NODE_COUNT_MASK: u64 = (1_u64 << NODE_COUNT_BITS) - 1;
const NODE_GENERATION_SHIFT: u32 = NODE_COUNT_BITS;
const NODE_GENERATION_BITS: u32 = 47;
const NODE_GENERATION_MASK: u64 = (1_u64 << NODE_GENERATION_BITS) - 1;
const NODE_RESERVED_BIT: u64 = 1_u64 << 63;

const TOKEN_LEAF_BITS: u32 = 16;
const TOKEN_LEAF_MASK: u64 = (1_u64 << TOKEN_LEAF_BITS) - 1;
const TOKEN_GENERATION_SHIFT: u32 = TOKEN_LEAF_BITS;
const TOKEN_RESERVED_BIT: u64 = 1_u64 << 63;
const MAX_LEAVES: usize = 1_usize << TOKEN_LEAF_BITS;

const ROOT_STATUS_SHIFT: u32 = 61;
const ROOT_COUNT_MASK: u64 = (1_u64 << ROOT_STATUS_SHIFT) - 1;
const ROOT_OPEN: u64 = 0;
const ROOT_CLOSED: u64 = 1_u64 << ROOT_STATUS_SHIFT;
const ROOT_OPEN_TAIL: u64 = 2_u64 << ROOT_STATUS_SHIFT;
const ROOT_CLOSED_TAIL: u64 = 3_u64 << ROOT_STATUS_SHIFT;
const ROOT_DRAINED: u64 = 4_u64 << ROOT_STATUS_SHIFT;
const ROOT_CLOSING: u64 = 5_u64 << ROOT_STATUS_SHIFT;
const ROOT_STATUS_MASK: u64 = !ROOT_COUNT_MASK;

const POISON_NONE: u64 = 0;
const POISON_INVARIANT: u64 = 1;
const POISON_COMPENSATION: u64 = 2;
const POISON_ROOT_STATE: u64 = 3;

#[repr(align(64))]
struct CacheAlignedAtomicU64 {
    value: AtomicU64,
    _padding: [u8; CACHE_LINE - size_of::<AtomicU64>()],
}

impl CacheAlignedAtomicU64 {
    const fn new(value: u64) -> Self {
        Self {
            value: AtomicU64::new(value),
            _padding: [0; CACHE_LINE - size_of::<AtomicU64>()],
        }
    }
}

/// Why a C-SNZI entered its permanent fail-closed state.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CsnziPoisonReason {
    /// A node count, departure, or terminal scan violated a tree invariant.
    InvariantViolation,
    /// A redundant parent arrival unexpectedly selected the final root tail.
    CompensationSelectedTail,
    /// The packed root contains an impossible status/count combination.
    RootStateCorrupt,
}

impl CsnziPoisonReason {
    const fn code(self) -> u64 {
        match self {
            Self::InvariantViolation => POISON_INVARIANT,
            Self::CompensationSelectedTail => POISON_COMPENSATION,
            Self::RootStateCorrupt => POISON_ROOT_STATE,
        }
    }

    const fn from_code(code: u64) -> Option<Self> {
        match code {
            POISON_NONE => None,
            POISON_INVARIANT => Some(Self::InvariantViolation),
            POISON_COMPENSATION => Some(Self::CompensationSelectedTail),
            POISON_ROOT_STATE => Some(Self::RootStateCorrupt),
            _ => Some(Self::InvariantViolation),
        }
    }
}

/// A representational limit which rejected admission without leaking presence.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CsnziCapacity {
    /// A leaf or internal node already has the maximum 16-bit local count.
    NodeCount,
    /// The centralized root already has its maximum 61-bit contribution count.
    RootCount,
}

/// An error returned by a C-SNZI operation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CsnziError {
    /// The selected leaf is outside this tree's leaf range.
    InvalidLeaf {
        /// The rejected zero-based leaf ordinal.
        leaf: usize,
        /// The number of leaves in this tree.
        leaf_count: usize,
    },
    /// Admission has closed permanently.
    Closed,
    /// The final departure is completing while admission is still open.
    ///
    /// This transient state normally clears immediately. If its owner process
    /// died, it remains forever and deliberately fails closed. Retrying is an
    /// application policy decision; this method never lease-steals the state.
    DepartureTailBusy,
    /// A raw scalar token has reserved bits or an impossible generation.
    MalformedToken,
    /// The token names an older or otherwise different leaf activation.
    GenerationMismatch {
        /// The zero-based leaf carried by the token.
        leaf: usize,
        /// The generation carried by the token.
        token_generation: u64,
        /// The generation currently stored at the leaf.
        current_generation: u64,
    },
    /// The token's generation matches, but the leaf has no arrival to remove.
    InactiveToken {
        /// The zero-based leaf carried by the token.
        leaf: usize,
        /// The inactive generation carried by the token.
        generation: u64,
    },
    /// This arrival could not be represented, left no contribution, and did
    /// not poison the object.
    CapacityExhausted(CsnziCapacity),
    /// The object has entered a permanent fail-closed state.
    Poisoned(CsnziPoisonReason),
}

impl fmt::Display for CsnziError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLeaf { leaf, leaf_count } => {
                write!(formatter, "C-SNZI leaf {leaf} is outside 0..{leaf_count}")
            }
            Self::Closed => formatter.write_str("C-SNZI admission is closed"),
            Self::DepartureTailBusy => {
                formatter.write_str("C-SNZI final departure tail is still active")
            }
            Self::MalformedToken => formatter.write_str("malformed C-SNZI token"),
            Self::GenerationMismatch {
                leaf,
                token_generation,
                current_generation,
            } => write!(
                formatter,
                "C-SNZI leaf {leaf} is at generation {current_generation}, not {token_generation}"
            ),
            Self::InactiveToken { leaf, generation } => write!(
                formatter,
                "C-SNZI leaf {leaf} has no active arrival in generation {generation}"
            ),
            Self::CapacityExhausted(capacity) => {
                write!(formatter, "C-SNZI capacity exhausted: {capacity:?}")
            }
            Self::Poisoned(reason) => write!(formatter, "C-SNZI is poisoned: {reason:?}"),
        }
    }
}

impl core::error::Error for CsnziError {}

/// The result of permanently closing admission.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CloseOutcome {
    /// This call closed while represented activity or a departure tail remains.
    Pending,
    /// This call closed an empty object and sealed terminal drain.
    Drained,
    /// Admission had already been closed by another caller.
    AlreadyClosed,
}

/// The result of consuming one admitted participant's token.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DepartOutcome {
    /// Other represented activity may remain, or admission is still open.
    Active,
    /// This departure sealed the permanent closed-and-drained state.
    Drained,
}

/// The externally visible root phase in a diagnostic snapshot.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CsnziPhase {
    /// Admission is open.
    Open,
    /// Admission is closed while represented activity remains.
    Closed,
    /// Empty admission has been atomically closed and is being verified.
    Closing,
    /// A final departure is unwinding while admission remains logically open.
    OpenDepartureTail,
    /// A final departure is unwinding after admission closed.
    ClosedDepartureTail,
    /// The one-shot generation is terminally closed and drained.
    Drained,
    /// The packed root state is not a valid encoding.
    Corrupt,
}

/// A best-effort diagnostic snapshot of the complete tree.
///
/// Fields are loaded atomically but not as one transaction. Use
/// [`Csnzi::is_drained`] rather than this snapshot for reclamation decisions.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CsnziSnapshot {
    /// The root's logical phase.
    pub phase: CsnziPhase,
    /// The number of active or in-progress top-level subtree contributions.
    pub root_count: u64,
    /// The number of non-root tree nodes with a nonzero local count.
    pub active_nodes: usize,
    /// The saturating sum of all non-root local counts.
    pub local_count_sum: u64,
    /// The number of nodes whose packed state violates a local invariant.
    pub invalid_nodes: usize,
    /// The permanent poison reason, if one has been recorded.
    pub poison: Option<CsnziPoisonReason>,
}

impl CsnziSnapshot {
    /// Returns whether this sample resembles the valid terminal state.
    #[inline]
    pub const fn appears_drained(&self) -> bool {
        matches!(self.phase, CsnziPhase::Drained)
            && self.root_count == 0
            && self.active_nodes == 0
            && self.local_count_sum == 0
            && self.invalid_nodes == 0
            && self.poison.is_none()
    }
}

/// Linear evidence for one successful C-SNZI arrival.
///
/// This type is deliberately neither [`Copy`] nor [`Clone`] and has no `Drop`
/// implementation. Implicit cleanup cannot decide token ownership after
/// `fork`, cancellation, or process death. Losing a token leaks presence and
/// makes a later close fail closed.
#[must_use = "an admitted C-SNZI participant must eventually depart"]
pub struct CsnziToken<'a, const NODES: usize> {
    issuer: &'a Csnzi<NODES>,
    leaf: u16,
    generation: u64,
}

impl<const NODES: usize> CsnziToken<'_, NODES> {
    /// Returns the zero-based leaf selected by this arrival.
    #[inline]
    pub const fn leaf(&self) -> usize {
        self.leaf as usize
    }

    /// Returns the leaf activation generation carried by this token.
    #[inline]
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    /// Consumes this token and performs its matching departure.
    #[inline]
    pub fn depart(self) -> Result<DepartOutcome, CsnziError> {
        self.issuer.depart_token(self.leaf(), self.generation)
    }

    /// Encodes this token as a stable scalar for an audited C ABI.
    ///
    /// Bits 0..=15 hold the leaf, bits 16..=62 hold the generation, and bit 63
    /// is reserved and zero. The integer does not retain instance identity or
    /// linear ownership; decoding is therefore part of unsafe departure.
    #[inline]
    pub const fn into_raw(self) -> u64 {
        (self.generation << TOKEN_GENERATION_SHIFT) | self.leaf as u64
    }

    const fn decode_raw(raw: u64) -> Result<(usize, u64), CsnziError> {
        if raw & TOKEN_RESERVED_BIT != 0 {
            return Err(CsnziError::MalformedToken);
        }
        let generation = raw >> TOKEN_GENERATION_SHIFT;
        if generation == 0 || generation > NODE_GENERATION_MASK {
            return Err(CsnziError::MalformedToken);
        }
        Ok(((raw & TOKEN_LEAF_MASK) as usize, generation))
    }
}

impl<const NODES: usize> fmt::Debug for CsnziToken<'_, NODES> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CsnziToken")
            .field("leaf", &self.leaf())
            .field("generation", &self.generation())
            .finish_non_exhaustive()
    }
}

impl<const NODES: usize> PartialEq for CsnziToken<'_, NODES> {
    fn eq(&self, other: &Self) -> bool {
        core::ptr::eq(self.issuer, other.issuer)
            && self.leaf == other.leaf
            && self.generation == other.generation
    }
}

impl<const NODES: usize> Eq for CsnziToken<'_, NODES> {}

/// A pointer-free, scalable, one-shot closable nonzero indicator.
///
/// `NODES` excludes the centralized root and must describe complete
/// breadth-first four-way levels: `4`, `20`, `84`, `340`, and so on. The last
/// level contains the selectable leaves. At most 65,536 leaves are supported so
/// a raw token fits in one `u64` while preserving a 47-bit activation
/// generation.
///
/// The type intentionally uses native Rust layout rather than `repr(C)`. Its
/// exact compiled size, alignment, field offsets, and transitive atomic layout
/// are bound into [`FixedAddressPodValue::FINGERPRINT`]. Foreign callers should
/// use an audited executable-pod ABI instead of reproducing the fields.
pub struct Csnzi<const NODES: usize> {
    root: CacheAlignedAtomicU64,
    poison: CacheAlignedAtomicU64,
    nodes: [CacheAlignedAtomicU64; NODES],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RootPhase {
    Open,
    Closed,
    Closing,
    OpenTail,
    ClosedTail,
    Drained,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TreeDepart {
    Active,
    Tail,
}

impl<const NODES: usize> Csnzi<NODES> {
    /// The largest number of simultaneous arrivals representable at one node.
    pub const MAX_NODE_COUNT: u64 = NODE_COUNT_MASK;

    /// The largest activation generation representable before wrapping to one.
    pub const MAX_GENERATION: u64 = NODE_GENERATION_MASK;

    /// Returns whether `NODES` describes a supported complete four-way tree.
    pub const fn is_valid_node_count() -> bool {
        if NODES < FANOUT {
            return false;
        }

        let mut total = FANOUT;
        let mut level_width = FANOUT;
        loop {
            if total == NODES {
                return level_width <= MAX_LEAVES;
            }
            if total > NODES || level_width > MAX_LEAVES / FANOUT {
                return false;
            }
            level_width *= FANOUT;
            if level_width > NODES - total {
                return false;
            }
            total += level_width;
        }
    }

    /// Creates an open, empty C-SNZI generation.
    ///
    /// # Panics
    ///
    /// Panics if `NODES` is not a supported complete four-way tree.
    pub const fn new() -> Self {
        assert!(
            Self::is_valid_node_count(),
            "C-SNZI node count must describe complete 4-ary levels with at most 65,536 leaves"
        );
        Self {
            root: CacheAlignedAtomicU64::new(ROOT_OPEN),
            poison: CacheAlignedAtomicU64::new(POISON_NONE),
            nodes: [const { CacheAlignedAtomicU64::new(0) }; NODES],
        }
    }

    /// Initializes an object directly in its final shared storage.
    ///
    /// Every atomic and padding byte is written in place. Volatile byte writes
    /// prevent the padding loops from being lowered to freestanding-hostile
    /// `memset` calls in executable-pod code.
    ///
    /// # Safety
    ///
    /// `destination` must be non-null, aligned for `Self`, exclusively writable
    /// for `size_of::<Self>()` bytes, and valid for the completed object's full
    /// lifetime. The storage must be uninitialized or belong to an old
    /// generation which no process can access. `NODES` must pass
    /// [`Self::is_valid_node_count`].
    #[inline]
    pub unsafe fn initialize_at(destination: *mut Self) {
        assert!(
            Self::is_valid_node_count(),
            "C-SNZI node count must describe complete 4-ary levels with at most 65,536 leaves"
        );
        // SAFETY: The caller grants exclusive final storage. Every field and
        // padding byte is initialized before the object becomes observable.
        unsafe {
            initialize_cacheline(core::ptr::addr_of_mut!((*destination).root), ROOT_OPEN);
            initialize_cacheline(core::ptr::addr_of_mut!((*destination).poison), POISON_NONE);
            let nodes =
                core::ptr::addr_of_mut!((*destination).nodes).cast::<CacheAlignedAtomicU64>();
            let mut index = 0;
            while index < NODES {
                initialize_cacheline(nodes.add(index), 0);
                index += 1;
            }
        }
    }

    /// Returns the number of selectable leaves.
    #[inline]
    pub const fn leaf_count(&self) -> usize {
        Self::layout_leaf_count()
    }

    /// Attempts to admit one participant at `leaf`.
    ///
    /// An arrival first samples the root open. A successful same-leaf CAS can be
    /// ordered at that earlier sample with respect to close, as in the SPAA
    /// algorithm, but merely seeing open does not guarantee success: a final
    /// departure can win the leaf race and a subsequent parent activation can
    /// return [`CsnziError::Closed`] or [`CsnziError::DepartureTailBusy`]. The
    /// successful leaf CAS publishes the local count. If close intervenes
    /// before a successful join to already represented surplus, the abstract
    /// admission is ordered at the earlier open observation.
    ///
    /// An operation which already reserved a parent and then races with the
    /// exact 65,535-arrival local-count limit waits until a count departs. This
    /// pathological slow path preserves the operation's pre-close admission;
    /// killing it while it waits intentionally leaks the parent contribution.
    pub fn try_enter(&self, leaf: usize) -> Result<CsnziToken<'_, NODES>, CsnziError> {
        let node = self.leaf_node(leaf)?;
        self.ensure_healthy()?;
        self.observe_open()?;
        let generation = self.arrive_node(node)?;
        self.ensure_healthy()?;
        Ok(CsnziToken {
            issuer: self,
            leaf: leaf as u16,
            generation,
        })
    }

    /// Permanently closes this generation.
    ///
    /// This is idempotent. If the root is empty, close verifies the complete
    /// tree before atomically sealing terminal drain. If the final departure is
    /// already unwinding, close converts its open-tail state into a closed-tail
    /// state and that departure performs the eventual seal.
    pub fn close(&self) -> Result<CloseOutcome, CsnziError> {
        self.ensure_healthy()?;
        let mut root = self.root.value.load(Ordering::SeqCst);
        loop {
            let (phase, count) = self.decode_root(root)?;
            match phase {
                RootPhase::Open if count == 0 => {
                    match self.root.value.compare_exchange_weak(
                        root,
                        ROOT_CLOSING,
                        Ordering::SeqCst,
                        Ordering::SeqCst,
                    ) {
                        Ok(_) => {
                            self.verify_nodes_idle()?;
                            match self.root.value.compare_exchange(
                                ROOT_CLOSING,
                                ROOT_DRAINED,
                                Ordering::SeqCst,
                                Ordering::SeqCst,
                            ) {
                                // The final CAS is the close operation's last
                                // shared-memory access.
                                Ok(_) => return Ok(CloseOutcome::Drained),
                                Err(_) => {
                                    return Err(
                                        self.poison_with(CsnziPoisonReason::RootStateCorrupt)
                                    );
                                }
                            }
                        }
                        Err(observed) => root = observed,
                    }
                }
                RootPhase::Open => {
                    let closed = ROOT_CLOSED | count;
                    match self.root.value.compare_exchange_weak(
                        root,
                        closed,
                        Ordering::SeqCst,
                        Ordering::SeqCst,
                    ) {
                        Ok(_) => return Ok(CloseOutcome::Pending),
                        Err(observed) => root = observed,
                    }
                }
                RootPhase::OpenTail => match self.root.value.compare_exchange_weak(
                    root,
                    ROOT_CLOSED_TAIL,
                    Ordering::SeqCst,
                    Ordering::SeqCst,
                ) {
                    Ok(_) => return Ok(CloseOutcome::Pending),
                    Err(observed) => root = observed,
                },
                RootPhase::Closed
                | RootPhase::Closing
                | RootPhase::ClosedTail
                | RootPhase::Drained => {
                    return Ok(CloseOutcome::AlreadyClosed);
                }
            }
        }
    }

    /// Returns whether admission has closed permanently.
    #[inline]
    pub fn is_closed(&self) -> bool {
        let root = self.root.value.load(Ordering::SeqCst);
        matches!(
            root & ROOT_STATUS_MASK,
            ROOT_CLOSED | ROOT_CLOSING | ROOT_CLOSED_TAIL | ROOT_DRAINED
        )
    }

    /// Returns whether this generation has reached stable terminal drain.
    ///
    /// A true result is stable under the safe API: the root can never reopen,
    /// no new arrival can begin, every tree node was verified idle, and the
    /// final departure completed its last shared-memory access before sealing.
    /// This permits reclamation of the protected payload. It does **not** permit
    /// unmapping this `Csnzi` while any process can retain a reference or invoke
    /// even a diagnostic method; barrier-page lifetime is externally owned.
    #[inline]
    pub fn is_drained(&self) -> bool {
        self.root.value.load(Ordering::SeqCst) == ROOT_DRAINED
            && self.poison.value.load(Ordering::SeqCst) == POISON_NONE
    }

    /// Returns whether represented activity or poison may remain.
    ///
    /// A false result while open is only a point-in-time observation. Use
    /// [`Self::is_drained`] after close for a stable reclamation decision.
    #[inline]
    pub fn query(&self) -> bool {
        let root = self.root.value.load(Ordering::SeqCst);
        root & ROOT_COUNT_MASK != 0
            || matches!(root & ROOT_STATUS_MASK, ROOT_OPEN_TAIL | ROOT_CLOSED_TAIL)
            || self.poison.value.load(Ordering::SeqCst) != POISON_NONE
    }

    /// Returns the permanent poison reason, if any.
    #[inline]
    pub fn poison_reason(&self) -> Option<CsnziPoisonReason> {
        CsnziPoisonReason::from_code(self.poison.value.load(Ordering::SeqCst))
    }

    /// Collects a best-effort diagnostic snapshot of the complete tree.
    pub fn debug_snapshot(&self) -> CsnziSnapshot {
        let root = self.root.value.load(Ordering::SeqCst);
        let phase = match root & ROOT_STATUS_MASK {
            ROOT_OPEN => CsnziPhase::Open,
            ROOT_CLOSED => CsnziPhase::Closed,
            ROOT_CLOSING => CsnziPhase::Closing,
            ROOT_OPEN_TAIL => CsnziPhase::OpenDepartureTail,
            ROOT_CLOSED_TAIL => CsnziPhase::ClosedDepartureTail,
            ROOT_DRAINED => CsnziPhase::Drained,
            _ => CsnziPhase::Corrupt,
        };
        let mut active_nodes = 0;
        let mut local_count_sum = 0_u64;
        let mut invalid_nodes = 0;
        let mut index = 0;
        while index < NODES {
            let state = self.node_value(index).load(Ordering::SeqCst);
            let count = node_count(state);
            let generation = node_generation(state);
            if state & NODE_RESERVED_BIT != 0 || (count != 0 && generation == 0) {
                invalid_nodes += 1;
            }
            if count != 0 {
                active_nodes += 1;
                local_count_sum = local_count_sum.saturating_add(count);
            }
            index += 1;
        }
        CsnziSnapshot {
            phase,
            root_count: root & ROOT_COUNT_MASK,
            active_nodes,
            local_count_sum,
            invalid_nodes,
            poison: self.poison_reason(),
        }
    }

    /// Decodes and consumes a scalar token issued by this exact object.
    ///
    /// # Safety
    ///
    /// `raw` must be the sole unconsumed encoding returned by
    /// [`CsnziToken::into_raw`] for this exact `Csnzi` generation. The integer
    /// cannot enforce instance identity or single consumption. Duplicating it
    /// can consume an unrelated same-leaf arrival in the same activation.
    #[inline]
    pub unsafe fn depart_raw(&self, raw: u64) -> Result<DepartOutcome, CsnziError> {
        let (leaf, generation) = CsnziToken::<NODES>::decode_raw(raw)?;
        self.depart_token(leaf, generation)
    }

    const fn layout_leaf_count() -> usize {
        if !Self::is_valid_node_count() {
            return 0;
        }
        NODES - (NODES - FANOUT) / FANOUT
    }

    const fn leaf_start() -> usize {
        (NODES - FANOUT) / FANOUT
    }

    fn leaf_node(&self, leaf: usize) -> Result<usize, CsnziError> {
        let leaf_count = self.leaf_count();
        if leaf >= leaf_count {
            return Err(CsnziError::InvalidLeaf { leaf, leaf_count });
        }
        Ok(Self::leaf_start() + leaf)
    }

    #[inline(always)]
    fn node_value(&self, index: usize) -> &AtomicU64 {
        debug_assert!(index < NODES);
        // SAFETY: callers pass either a range-checked leaf, an index from a
        // `0..NODES` scan, or `(child - FANOUT) / FANOUT`, which is strictly
        // smaller than an already valid child index. Pointer access avoids a
        // rustc 1.85 bounds-check panic edge in freestanding PIC output.
        unsafe { &(*self.nodes.as_ptr().add(index)).value }
    }

    fn observe_open(&self) -> Result<(), CsnziError> {
        let root = self.root.value.load(Ordering::SeqCst);
        let (phase, _) = self.decode_root(root)?;
        match phase {
            RootPhase::Open => Ok(()),
            RootPhase::OpenTail => Err(CsnziError::DepartureTailBusy),
            RootPhase::Closed | RootPhase::Closing | RootPhase::ClosedTail | RootPhase::Drained => {
                Err(CsnziError::Closed)
            }
        }
    }

    fn arrive_node(&self, node: usize) -> Result<u64, CsnziError> {
        let mut arrived_at_parent = false;
        loop {
            self.ensure_healthy()?;
            let state = self.node_value(node).load(Ordering::SeqCst);
            self.validate_node_state(state)?;
            let count = node_count(state);
            let generation = node_generation(state);

            if count == 0 && !arrived_at_parent {
                self.arrive_parent(node)?;
                arrived_at_parent = true;
                continue;
            }

            if count == NODE_COUNT_MASK {
                if arrived_at_parent {
                    // This entry is already represented at its parent and may
                    // have been observed by query/close. It must eventually
                    // publish a token rather than complete as a failed no-op.
                    spin_loop();
                    continue;
                }
                return Err(CsnziError::CapacityExhausted(CsnziCapacity::NodeCount));
            }
            let next_generation = if count == 0 {
                if generation == NODE_GENERATION_MASK {
                    1
                } else {
                    generation + 1
                }
            } else {
                generation
            };
            let incremented = node_state(next_generation, count + 1);
            match self.node_value(node).compare_exchange_weak(
                state,
                incremented,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => {
                    if arrived_at_parent
                        && count != 0
                        && self.depart_parent(node)? == TreeDepart::Tail
                    {
                        return Err(self.poison_with(CsnziPoisonReason::CompensationSelectedTail));
                    }
                    return Ok(next_generation);
                }
                Err(_) => continue,
            }
        }
    }

    fn depart_token(&self, leaf: usize, generation: u64) -> Result<DepartOutcome, CsnziError> {
        let node = self.leaf_node(leaf)?;
        self.ensure_healthy()?;
        match self.depart_node(node, Some((leaf, generation)))? {
            // The successful node/ancestor CAS was this non-final departure's
            // last shared-memory access. A later final departure may seal drain
            // while only stack/register work remains here.
            TreeDepart::Active => Ok(DepartOutcome::Active),
            TreeDepart::Tail => self.finish_departure_tail(),
        }
    }

    fn depart_node(
        &self,
        node: usize,
        expected: Option<(usize, u64)>,
    ) -> Result<TreeDepart, CsnziError> {
        loop {
            self.ensure_healthy()?;
            let state = self.node_value(node).load(Ordering::SeqCst);
            self.validate_node_state(state)?;
            let count = node_count(state);
            let generation = node_generation(state);

            if let Some((leaf, expected_generation)) = expected {
                if generation != expected_generation {
                    return Err(CsnziError::GenerationMismatch {
                        leaf,
                        token_generation: expected_generation,
                        current_generation: generation,
                    });
                }
                if count == 0 {
                    return Err(CsnziError::InactiveToken {
                        leaf,
                        generation: expected_generation,
                    });
                }
            } else if count == 0 {
                return Err(self.poison_with(CsnziPoisonReason::InvariantViolation));
            }

            let decremented = node_state(generation, count - 1);
            match self.node_value(node).compare_exchange_weak(
                state,
                decremented,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) if count == 1 => return self.depart_parent(node),
                Ok(_) => return Ok(TreeDepart::Active),
                Err(_) => continue,
            }
        }
    }

    #[inline]
    fn arrive_parent(&self, node: usize) -> Result<(), CsnziError> {
        if node < FANOUT {
            self.root_arrive()
        } else {
            self.arrive_node((node - FANOUT) / FANOUT).map(|_| ())
        }
    }

    #[inline]
    fn depart_parent(&self, node: usize) -> Result<TreeDepart, CsnziError> {
        if node < FANOUT {
            self.root_depart()
        } else {
            self.depart_node((node - FANOUT) / FANOUT, None)
        }
    }

    fn root_arrive(&self) -> Result<(), CsnziError> {
        let mut root = self.root.value.load(Ordering::SeqCst);
        loop {
            self.ensure_healthy()?;
            let (phase, count) = self.decode_root(root)?;
            match phase {
                RootPhase::OpenTail => return Err(CsnziError::DepartureTailBusy),
                RootPhase::Closing | RootPhase::ClosedTail | RootPhase::Drained => {
                    return Err(CsnziError::Closed);
                }
                RootPhase::Closed if count == 0 => return Err(CsnziError::Closed),
                RootPhase::Open | RootPhase::Closed => {}
            }
            if count == ROOT_COUNT_MASK {
                return Err(CsnziError::CapacityExhausted(CsnziCapacity::RootCount));
            }
            let incremented = (root & ROOT_STATUS_MASK) | (count + 1);
            match self.root.value.compare_exchange_weak(
                root,
                incremented,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return Ok(()),
                Err(observed) => root = observed,
            }
        }
    }

    fn root_depart(&self) -> Result<TreeDepart, CsnziError> {
        let mut root = self.root.value.load(Ordering::SeqCst);
        loop {
            self.ensure_healthy()?;
            let (phase, count) = self.decode_root(root)?;
            if !matches!(phase, RootPhase::Open | RootPhase::Closed) || count == 0 {
                return Err(self.poison_with(CsnziPoisonReason::InvariantViolation));
            }
            let next = if count == 1 {
                match phase {
                    RootPhase::Open => ROOT_OPEN_TAIL,
                    RootPhase::Closed => ROOT_CLOSED_TAIL,
                    _ => unreachable!(),
                }
            } else {
                (root & ROOT_STATUS_MASK) | (count - 1)
            };
            match self.root.value.compare_exchange_weak(
                root,
                next,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) if count == 1 => return Ok(TreeDepart::Tail),
                Ok(_) => return Ok(TreeDepart::Active),
                Err(observed) => root = observed,
            }
        }
    }

    fn finish_departure_tail(&self) -> Result<DepartOutcome, CsnziError> {
        self.verify_nodes_idle()?;
        let mut root = self.root.value.load(Ordering::SeqCst);
        loop {
            self.ensure_healthy()?;
            let (phase, count) = self.decode_root(root)?;
            if count != 0 {
                return Err(self.poison_with(CsnziPoisonReason::InvariantViolation));
            }
            let (next, outcome) = match phase {
                RootPhase::OpenTail => (ROOT_OPEN, DepartOutcome::Active),
                RootPhase::ClosedTail => (ROOT_DRAINED, DepartOutcome::Drained),
                _ => return Err(self.poison_with(CsnziPoisonReason::InvariantViolation)),
            };
            match self.root.value.compare_exchange_weak(
                root,
                next,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                // This CAS is intentionally the operation's final access to
                // shared state. Only stack/register work remains on return.
                Ok(_) => return Ok(outcome),
                Err(observed) => root = observed,
            }
        }
    }

    fn verify_nodes_idle(&self) -> Result<(), CsnziError> {
        let mut index = 0;
        while index < NODES {
            let state = self.node_value(index).load(Ordering::SeqCst);
            self.validate_node_state(state)?;
            if node_count(state) != 0 {
                return Err(self.poison_with(CsnziPoisonReason::InvariantViolation));
            }
            index += 1;
        }
        Ok(())
    }

    fn validate_node_state(&self, state: u64) -> Result<(), CsnziError> {
        if state & NODE_RESERVED_BIT != 0 || (node_count(state) != 0 && node_generation(state) == 0)
        {
            return Err(self.poison_with(CsnziPoisonReason::InvariantViolation));
        }
        Ok(())
    }

    fn decode_root(&self, root: u64) -> Result<(RootPhase, u64), CsnziError> {
        let count = root & ROOT_COUNT_MASK;
        let phase = match root & ROOT_STATUS_MASK {
            ROOT_OPEN => RootPhase::Open,
            ROOT_CLOSED => RootPhase::Closed,
            ROOT_CLOSING if count == 0 => RootPhase::Closing,
            ROOT_OPEN_TAIL if count == 0 => RootPhase::OpenTail,
            ROOT_CLOSED_TAIL if count == 0 => RootPhase::ClosedTail,
            ROOT_DRAINED if count == 0 => RootPhase::Drained,
            _ => return Err(self.poison_with(CsnziPoisonReason::RootStateCorrupt)),
        };
        Ok((phase, count))
    }

    fn ensure_healthy(&self) -> Result<(), CsnziError> {
        match self.poison_reason() {
            Some(reason) => Err(CsnziError::Poisoned(reason)),
            None => Ok(()),
        }
    }

    fn poison_with(&self, reason: CsnziPoisonReason) -> CsnziError {
        let _ = self.poison.value.compare_exchange(
            POISON_NONE,
            reason.code(),
            Ordering::SeqCst,
            Ordering::SeqCst,
        );
        CsnziError::Poisoned(self.poison_reason().unwrap_or(reason))
    }
}

impl<const NODES: usize> Default for Csnzi<NODES> {
    fn default() -> Self {
        Self::new()
    }
}

const fn node_count(state: u64) -> u64 {
    state & NODE_COUNT_MASK
}

const fn node_generation(state: u64) -> u64 {
    (state >> NODE_GENERATION_SHIFT) & NODE_GENERATION_MASK
}

const fn node_state(generation: u64, count: u64) -> u64 {
    (generation << NODE_GENERATION_SHIFT) | count
}

#[inline(always)]
unsafe fn initialize_cacheline(destination: *mut CacheAlignedAtomicU64, value: u64) {
    // SAFETY: The caller provides one exclusive, aligned cache-line object.
    unsafe {
        core::ptr::addr_of_mut!((*destination).value).write(AtomicU64::new(value));
        let padding = core::ptr::addr_of_mut!((*destination)._padding).cast::<u8>();
        let mut index = 0;
        while index < CACHE_LINE - size_of::<AtomicU64>() {
            padding.add(index).write_volatile(0);
            index += 1;
        }
    }
}

// SAFETY: Csnzi contains only aligned atomic integers and byte padding. It has
// no destructor, allocation, process-local resource, or stored address.
unsafe impl<const NODES: usize> FixedAddressPodValue for Csnzi<NODES> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod values must not need drop");
        let state = __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-csnzi-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        let state = __private::mix_usize(state, NODES);
        let state = __private::mix_usize(state, offset_of!(Self, root));
        let state = __private::mix_usize(state, offset_of!(Self, poison));
        let state = __private::mix_usize(state, offset_of!(Self, nodes));
        let state = __private::mix_usize(state, offset_of!(CacheAlignedAtomicU64, value));
        __private::finish(__private::mix_u128(state, AtomicU64::FINGERPRINT))
    };
}

// SAFETY: Every persisted field is an address-independent integer or padding.
unsafe impl<const NODES: usize> PodValue for Csnzi<NODES> {}

// SAFETY: The shared API mutates persisted state only with 64-bit atomics.
unsafe impl<const NODES: usize> PodSync for Csnzi<NODES> {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn close_after_open_tail_cannot_pass_departure_tail() {
        let barrier = Csnzi::<4>::new();
        let token = barrier.try_enter(0).unwrap();
        let leaf = barrier.leaf_node(0).unwrap();

        let result = barrier
            .depart_node(leaf, Some((0, token.generation())))
            .unwrap();
        assert_eq!(result, TreeDepart::Tail);
        assert!(barrier.query());
        assert_eq!(barrier.close().unwrap(), CloseOutcome::Pending);
        assert!(barrier.query());
        assert!(!barrier.is_drained());
        assert_eq!(
            barrier.debug_snapshot().phase,
            CsnziPhase::ClosedDepartureTail
        );
        assert_eq!(
            barrier.finish_departure_tail().unwrap(),
            DepartOutcome::Drained
        );
        assert!(barrier.is_drained());
        assert!(!barrier.query());
    }

    #[test]
    fn parent_first_crash_cut_fails_closed() {
        let barrier = Csnzi::<4>::new();
        barrier.root_arrive().unwrap();
        assert_eq!(barrier.close().unwrap(), CloseOutcome::Pending);
        assert!(!barrier.is_drained());
        assert_eq!(barrier.debug_snapshot().root_count, 1);
    }

    #[test]
    fn child_first_departure_crash_cut_fails_closed() {
        let barrier = Csnzi::<4>::new();
        let token = barrier.try_enter(0).unwrap();
        barrier.close().unwrap();
        let leaf = barrier.leaf_node(0).unwrap();
        let state = barrier.nodes[leaf].value.load(Ordering::SeqCst);
        barrier.nodes[leaf]
            .value
            .compare_exchange(
                state,
                node_state(token.generation(), 0),
                Ordering::SeqCst,
                Ordering::SeqCst,
            )
            .unwrap();
        assert!(!barrier.is_drained());
        assert_eq!(barrier.debug_snapshot().root_count, 1);
    }

    #[test]
    fn closed_zero_root_rejects_delayed_parent_activation() {
        let barrier = Csnzi::<4>::new();
        barrier.observe_open().unwrap();
        barrier.close().unwrap();
        assert_eq!(barrier.arrive_node(0), Err(CsnziError::Closed));
        assert!(barrier.is_drained());
    }

    #[test]
    fn open_tail_is_bounded_busy_and_can_return_to_open() {
        let barrier = Csnzi::<4>::new();
        let token = barrier.try_enter(0).unwrap();
        let leaf = barrier.leaf_node(0).unwrap();
        assert_eq!(
            barrier
                .depart_node(leaf, Some((0, token.generation())))
                .unwrap(),
            TreeDepart::Tail
        );
        assert!(barrier.query());
        assert_eq!(barrier.try_enter(1), Err(CsnziError::DepartureTailBusy));
        assert_eq!(
            barrier.finish_departure_tail().unwrap(),
            DepartOutcome::Active
        );
        assert!(!barrier.query());
        let next = barrier.try_enter(1).unwrap();
        next.depart().unwrap();
    }

    #[test]
    fn closer_crash_after_claiming_empty_root_fails_closed() {
        let barrier = Csnzi::<4>::new();
        barrier
            .root
            .value
            .compare_exchange(ROOT_OPEN, ROOT_CLOSING, Ordering::SeqCst, Ordering::SeqCst)
            .unwrap();
        assert!(barrier.is_closed());
        assert!(!barrier.is_drained());
        assert_eq!(barrier.try_enter(0), Err(CsnziError::Closed));
        assert_eq!(barrier.close().unwrap(), CloseOutcome::AlreadyClosed);
    }

    #[test]
    fn redundant_parent_contribution_model_compensates_to_one() {
        let barrier = Csnzi::<4>::new();
        let leaf = barrier.leaf_node(0).unwrap();

        // Model the two recursive parent arrivals made by racing activators.
        barrier.root_arrive().unwrap();
        barrier.root_arrive().unwrap();
        barrier.nodes[leaf]
            .value
            .compare_exchange(0, node_state(1, 1), Ordering::SeqCst, Ordering::SeqCst)
            .unwrap();
        barrier.nodes[leaf]
            .value
            .compare_exchange(
                node_state(1, 1),
                node_state(1, 2),
                Ordering::SeqCst,
                Ordering::SeqCst,
            )
            .unwrap();
        assert_eq!(barrier.root_depart().unwrap(), TreeDepart::Active);
        assert_eq!(barrier.debug_snapshot().root_count, 1);
        assert_eq!(
            node_count(barrier.nodes[leaf].value.load(Ordering::SeqCst)),
            2
        );
    }

    #[test]
    fn packed_count_capacity_rejects_and_generation_wraps_without_poisoning() {
        let root_overflow = Csnzi::<4>::new();
        root_overflow
            .root
            .value
            .store(ROOT_OPEN | ROOT_COUNT_MASK, Ordering::SeqCst);
        assert_eq!(
            root_overflow.root_arrive(),
            Err(CsnziError::CapacityExhausted(CsnziCapacity::RootCount))
        );
        assert_eq!(root_overflow.poison_reason(), None);

        let generation_wrap = Csnzi::<4>::new();
        let leaf = generation_wrap.leaf_node(0).unwrap();
        generation_wrap.nodes[leaf]
            .value
            .store(node_state(NODE_GENERATION_MASK, 0), Ordering::SeqCst);
        let token = generation_wrap.try_enter(0).unwrap();
        assert_eq!(token.generation(), 1);
        assert_eq!(generation_wrap.poison_reason(), None);
        assert_eq!(token.depart().unwrap(), DepartOutcome::Active);
        assert!(!generation_wrap.query());
        assert_eq!(generation_wrap.close().unwrap(), CloseOutcome::Drained);
    }

    #[test]
    fn impossible_root_state_poison_is_fail_closed() {
        let barrier = Csnzi::<4>::new();
        barrier.root.value.store(ROOT_DRAINED | 1, Ordering::SeqCst);
        assert!(matches!(
            barrier.close(),
            Err(CsnziError::Poisoned(CsnziPoisonReason::RootStateCorrupt))
        ));
        assert!(!barrier.is_drained());
    }
}
