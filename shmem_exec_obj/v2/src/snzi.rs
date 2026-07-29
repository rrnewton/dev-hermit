//! A pointer-free hierarchical scalable nonzero indicator (SNZI).
//!
//! [`Snzi`] implements the tree algorithm from Figure 7 of Ellen, Lev,
//! Luchangco, and Moir, *SNZI: Scalable NonZero Indicators* (PODC 2007).
//! The tree is stored breadth first, has a fanout of four, and terminates in a
//! centralized [`AtomicU64`] root.  An arrival which activates an idle node
//! first installs the paper's `HALF` state, represents the activation at the
//! parent, and only then publishes a count of one.  Other arrivals help a
//! `HALF` node and compensate redundant parent arrivals.  In particular, this
//! is not the incorrect "publish 0 -> 1, then update the parent" algorithm.
//!
//! The indicator reports presence, not an exact population.  A successful
//! [`Snzi::arrive`] must eventually be matched by exactly one
//! [`ArrivalToken::depart`] using its returned, linear token. [`Snzi::query`] is wait-free;
//! arrival and departure are lock-free under the usual lock-free guarantees of
//! the target's 64-bit atomics.  All algorithm atomics currently use
//! [`Ordering::SeqCst`], matching the paper's sequentially consistent model.
//!
//! # Shared-memory and lifecycle contract
//!
//! The data structure contains no pointers and needs no process-local runtime
//! state, so the same initialized object may be mapped at different virtual
//! addresses. A typed token borrows its issuing instance, preventing safe
//! cross-instance departure. Its scalar C ABI encoding contains only a leaf
//! ordinal and activation generation, so raw token use has an explicit unsafe
//! exact-instance and linear-ownership contract.
//!
//! Process death cannot leave a held lock, but it can permanently leak an
//! arrival or an in-progress helper contribution.  This type provides neither
//! owner-death recovery nor leases.  It is also not, by itself, a reclamation
//! barrier: a false query does not stop a new arrival from starting immediately.
//! Close an external admission gate and wait for all entrants before reclaiming
//! memory protected by an SNZI.

use core::fmt;
#[cfg(not(shmem_pod_loom))]
use core::mem::{align_of, needs_drop, offset_of, size_of};

use crate::model_atomic::{AtomicU64, Ordering};
#[cfg(not(shmem_pod_loom))]
use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const FANOUT: usize = 4;
#[cfg(not(shmem_pod_loom))]
const CACHE_LINE: usize = 64;

const COUNT_BITS: u32 = 16;
const COUNT_MASK: u64 = (1_u64 << COUNT_BITS) - 1;
const HALF_BIT: u64 = 1_u64 << COUNT_BITS;
const VERSION_SHIFT: u32 = COUNT_BITS + 1;
const VERSION_BITS: u32 = u64::BITS - VERSION_SHIFT;
const VERSION_MASK: u64 = (1_u64 << VERSION_BITS) - 1;

const TOKEN_LEAF_BITS: u32 = 16;
const TOKEN_LEAF_MASK: u64 = (1_u64 << TOKEN_LEAF_BITS) - 1;
const TOKEN_GENERATION_SHIFT: u32 = TOKEN_LEAF_BITS;
const TOKEN_RESERVED_BIT: u64 = 1_u64 << 63;
const MAX_LEAVES: usize = 1_usize << TOKEN_LEAF_BITS;

const POISON_NONE: u64 = 0;
const POISON_NODE_COUNT: u64 = 1;
const POISON_VERSION: u64 = 2;
const POISON_ROOT_COUNT: u64 = 3;
const POISON_INVARIANT: u64 = 4;
const POISON_COMPENSATION: u64 = 5;

/// Why an SNZI entered its terminal poisoned state.
///
/// Poisoning is fail-closed: once poisoned, all mutating operations return
/// [`SnziError::Poisoned`] and [`Snzi::query`] returns `true` forever.  This
/// avoids turning capacity exhaustion or a damaged invariant into a false
/// indication of quiescence.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PoisonReason {
    /// A packed node's 16-bit local count could not be incremented.
    NodeCountOverflow,
    /// A packed node exhausted its 47-bit activation generation.
    GenerationExhausted,
    /// The centralized root count could not be incremented.
    RootCountOverflow,
    /// An internal departure or packed state violated the tree invariants.
    InvariantViolation,
    /// A helper's local redundant-parent-arrival tally overflowed.
    CompensationOverflow,
}

impl PoisonReason {
    const fn code(self) -> u64 {
        match self {
            Self::NodeCountOverflow => POISON_NODE_COUNT,
            Self::GenerationExhausted => POISON_VERSION,
            Self::RootCountOverflow => POISON_ROOT_COUNT,
            Self::InvariantViolation => POISON_INVARIANT,
            Self::CompensationOverflow => POISON_COMPENSATION,
        }
    }

    const fn from_code(code: u64) -> Option<Self> {
        match code {
            POISON_NONE => None,
            POISON_NODE_COUNT => Some(Self::NodeCountOverflow),
            POISON_VERSION => Some(Self::GenerationExhausted),
            POISON_ROOT_COUNT => Some(Self::RootCountOverflow),
            POISON_INVARIANT => Some(Self::InvariantViolation),
            POISON_COMPENSATION => Some(Self::CompensationOverflow),
            _ => Some(Self::InvariantViolation),
        }
    }
}

/// An error returned by an SNZI operation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SnziError {
    /// The leaf ordinal is outside this tree's leaf range.
    InvalidLeaf {
        /// The rejected zero-based leaf ordinal.
        leaf: usize,
        /// The number of leaves in the tree.
        leaf_count: usize,
    },
    /// A scalar token has nonzero reserved bits or an impossible generation.
    MalformedToken,
    /// The token names an older or otherwise different leaf activation.
    GenerationMismatch {
        /// The zero-based leaf ordinal carried by the token.
        leaf: usize,
        /// The generation carried by the token.
        token_generation: u64,
        /// The generation currently stored at the leaf.
        current_generation: u64,
    },
    /// The token's generation matches, but that leaf currently has no arrival.
    InactiveToken {
        /// The zero-based leaf ordinal carried by the token.
        leaf: usize,
        /// The inactive activation generation.
        generation: u64,
    },
    /// The instance is terminally poisoned.
    Poisoned(PoisonReason),
}

impl fmt::Display for SnziError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLeaf { leaf, leaf_count } => {
                write!(formatter, "SNZI leaf {leaf} is outside 0..{leaf_count}")
            }
            Self::MalformedToken => formatter.write_str("malformed SNZI arrival token"),
            Self::GenerationMismatch {
                leaf,
                token_generation,
                current_generation,
            } => write!(
                formatter,
                "SNZI leaf {leaf} is at generation {current_generation}, not {token_generation}"
            ),
            Self::InactiveToken { leaf, generation } => write!(
                formatter,
                "SNZI leaf {leaf} has no active arrival in generation {generation}"
            ),
            Self::Poisoned(reason) => write!(formatter, "SNZI is poisoned: {reason:?}"),
        }
    }
}

impl core::error::Error for SnziError {}

/// Linear evidence for one successful arrival.
///
/// The token is deliberately neither [`Copy`] nor [`Clone`]. Calling
/// [`ArrivalToken::depart`] consumes the sole safe Rust capability and uses the
/// issuing instance borrowed by the token. The raw scalar form is intended for
/// a C ABI or shared command slot; code using that form must preserve the same
/// exact-instance and linear-ownership discipline.
///
/// `fork` physically duplicates a live token without running Rust ownership
/// logic. Only one process may consume the arrival. Fork only after draining
/// tokens, or guarantee that the child never uses the inherited token and ends
/// with `exec` or `_exit` instead of unwinding it.
#[must_use = "a successful arrival must eventually be departed"]
pub struct ArrivalToken<'a, const NODES: usize> {
    issuer: &'a Snzi<NODES>,
    leaf: u16,
    generation: u64,
}

impl<const NODES: usize> ArrivalToken<'_, NODES> {
    /// Returns the zero-based leaf ordinal used by this arrival.
    #[inline]
    pub const fn leaf(&self) -> usize {
        self.leaf as usize
    }

    /// Returns the leaf activation generation used by this arrival.
    #[inline]
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    /// Performs the matching departure on the exact instance that issued this token.
    ///
    /// Consuming `self` preserves one-departure ownership in safe Rust.
    #[inline]
    pub fn depart(self) -> Result<(), SnziError> {
        self.issuer.depart_token(self.leaf(), self.generation)
    }

    /// Encodes this token as a stable scalar suitable for an executable C ABI.
    ///
    /// Bits 0..=15 hold the leaf, bits 16..=62 hold the activation generation,
    /// and bit 63 is reserved and zero.  Decoding and departing are combined in
    /// [`Snzi::depart_raw`] so the leaf can be checked against the destination
    /// tree before it is used.
    #[inline]
    pub const fn into_raw(self) -> u64 {
        (self.generation << TOKEN_GENERATION_SHIFT) | self.leaf as u64
    }

    const fn decode_raw(raw: u64) -> Result<(usize, u64), SnziError> {
        if raw & TOKEN_RESERVED_BIT != 0 {
            return Err(SnziError::MalformedToken);
        }

        let generation = raw >> TOKEN_GENERATION_SHIFT;
        if generation == 0 || generation > VERSION_MASK {
            return Err(SnziError::MalformedToken);
        }

        Ok(((raw & TOKEN_LEAF_MASK) as usize, generation))
    }
}

impl<const NODES: usize> fmt::Debug for ArrivalToken<'_, NODES> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ArrivalToken")
            .field("leaf", &self.leaf)
            .field("generation", &self.generation)
            .finish_non_exhaustive()
    }
}

impl<const NODES: usize> PartialEq for ArrivalToken<'_, NODES> {
    fn eq(&self, other: &Self) -> bool {
        core::ptr::eq(self.issuer, other.issuer)
            && self.leaf == other.leaf
            && self.generation == other.generation
    }
}

impl<const NODES: usize> Eq for ArrivalToken<'_, NODES> {}

/// A best-effort diagnostic snapshot of an SNZI tree.
///
/// Each field is loaded atomically, but the complete snapshot is not a single
/// linearizable observation when operations run concurrently.  It is intended
/// for post-quiescence validation and diagnostics, not synchronization.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SnziSnapshot {
    /// The centralized root count.
    pub root_count: u64,
    /// The number of packed nodes whose integer count is nonzero.
    pub active_nodes: usize,
    /// The number of packed nodes currently in the transient `HALF` state.
    pub half_nodes: usize,
    /// The saturating sum of all packed-node local counts.
    pub local_count_sum: u64,
    /// The number of packed nodes observed in an invalid state.
    pub invalid_nodes: usize,
    /// The terminal poison reason, if one has been recorded.
    pub poison: Option<PoisonReason>,
}

impl SnziSnapshot {
    /// Returns whether this snapshot contains no activity or poison.
    #[inline]
    pub const fn is_quiescent(&self) -> bool {
        self.root_count == 0
            && self.active_nodes == 0
            && self.half_nodes == 0
            && self.invalid_nodes == 0
            && self.poison.is_none()
    }
}

#[cfg_attr(not(shmem_pod_loom), repr(align(64)))]
struct CacheAlignedAtomicU64 {
    value: AtomicU64,
    #[cfg(not(shmem_pod_loom))]
    _padding: [u8; CACHE_LINE - size_of::<AtomicU64>()],
}

impl CacheAlignedAtomicU64 {
    #[cfg(not(shmem_pod_loom))]
    const fn new(value: u64) -> Self {
        Self {
            value: AtomicU64::new(value),
            _padding: [0; CACHE_LINE - size_of::<AtomicU64>()],
        }
    }

    #[cfg(shmem_pod_loom)]
    fn new(value: u64) -> Self {
        Self {
            value: AtomicU64::new(value),
        }
    }
}

/// A four-way hierarchical scalable nonzero indicator.
///
/// `NODES` counts packed tree nodes and excludes the centralized root.  It must
/// describe complete breadth-first levels: `4`, `20`, `84`, `340`, and so on.
/// The final level contains the addressable leaves.  At most 65,536 leaves are
/// supported so an [`ArrivalToken`] fits in one `u64` without shortening the
/// packed node's 47-bit anti-ABA generation.
///
/// Constructing an invalid layout panics, including during constant evaluation.
/// Use [`Snzi::is_valid_node_count`] to validate a generic value first.
pub struct Snzi<const NODES: usize> {
    root: CacheAlignedAtomicU64,
    poison: CacheAlignedAtomicU64,
    nodes: [CacheAlignedAtomicU64; NODES],
}

impl<const NODES: usize> Snzi<NODES> {
    /// The largest local count representable by one packed node.
    pub const MAX_NODE_COUNT: u64 = COUNT_MASK;

    /// The largest activation generation representable by one packed node.
    pub const MAX_GENERATION: u64 = VERSION_MASK;

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

    /// Creates an empty SNZI.
    ///
    /// # Panics
    ///
    /// Panics if `NODES` does not describe complete breadth-first four-way
    /// levels or if its last level has more than 65,536 leaves.
    #[cfg(not(shmem_pod_loom))]
    pub const fn new() -> Self {
        assert!(
            Self::is_valid_node_count(),
            "SNZI node count must describe complete 4-ary levels with at most 65,536 leaves"
        );

        Self {
            root: CacheAlignedAtomicU64::new(0),
            poison: CacheAlignedAtomicU64::new(POISON_NONE),
            nodes: [const { CacheAlignedAtomicU64::new(0) }; NODES],
        }
    }

    /// Creates an empty SNZI for the dedicated Loom model build.
    #[cfg(shmem_pod_loom)]
    pub fn new() -> Self {
        assert!(
            Self::is_valid_node_count(),
            "SNZI node count must describe complete 4-ary levels with at most 65,536 leaves"
        );

        Self {
            root: CacheAlignedAtomicU64::new(0),
            poison: CacheAlignedAtomicU64::new(POISON_NONE),
            nodes: core::array::from_fn(|_| CacheAlignedAtomicU64::new(0)),
        }
    }

    /// Initializes an SNZI directly in its final storage without constructing
    /// or moving a large aggregate value.
    ///
    /// This is useful in freestanding PIC code. Some rustc/LLVM versions lower
    /// a move of [`Snzi::new`] to `memset`/`memcpy` libcalls even in `no_std`
    /// code. This routine writes every atomic and padding byte in place and does
    /// not require a runtime memory intrinsic.
    ///
    /// # Safety
    ///
    /// `destination` must be non-null, aligned for `Self`, exclusively writable
    /// for `size_of::<Self>()` bytes, and valid for the completed object's full
    /// lifetime. `NODES` must satisfy [`Snzi::is_valid_node_count`]. The storage
    /// must be uninitialized or contain an old `Snzi` that no process can still
    /// access. On return it contains a fully initialized, empty instance.
    #[inline]
    #[cfg(not(shmem_pod_loom))]
    pub unsafe fn initialize_at(destination: *mut Self) {
        // SAFETY: The caller provides complete exclusive storage. Every field is
        // written exactly once before the initialized object can be observed.
        unsafe {
            initialize_cacheline(core::ptr::addr_of_mut!((*destination).root), 0);
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

    /// Returns the number of addressable leaves in this tree.
    #[inline]
    pub const fn leaf_count(&self) -> usize {
        Self::layout_leaf_count()
    }

    /// Records one arrival at `leaf` and returns its linear departure token.
    ///
    /// Leaf collisions are correct and only reduce scalability.  A successful
    /// call is represented at the root before it returns, so a concurrent query
    /// cannot miss the completed unmatched arrival.
    #[inline]
    pub fn arrive(&self, leaf: usize) -> Result<ArrivalToken<'_, NODES>, SnziError> {
        let node = self.leaf_node(leaf)?;
        self.ensure_healthy()?;
        let generation = self.arrive_node(node)?;
        self.ensure_healthy()?;
        Ok(ArrivalToken {
            issuer: self,
            leaf: leaf as u16,
            generation,
        })
    }

    fn depart_token(&self, leaf: usize, generation: u64) -> Result<(), SnziError> {
        let node = self.leaf_node(leaf)?;
        self.ensure_healthy()?;
        self.depart_node(node, Some((leaf, generation)))?;
        self.ensure_healthy()
    }

    /// Decodes a stable scalar arrival token and performs its departure.
    ///
    /// This parses and validates bit 63, generation zero, out-of-range leaves,
    /// stale generations, and inactive tokens. The raw scalar does not encode
    /// the issuing instance and is trivially copyable.
    ///
    /// # Safety
    ///
    /// `raw` must come from consuming a token issued by this exact instance,
    /// and the caller must arrange at most one successful departure for that
    /// arrival. Duplicating a raw token can incorrectly consume a different
    /// same-leaf arrival in the same activation generation.
    #[inline]
    pub unsafe fn depart_raw(&self, raw: u64) -> Result<(), SnziError> {
        let (leaf, generation) = ArrivalToken::<NODES>::decode_raw(raw)?;
        self.depart_token(leaf, generation)
    }

    /// Returns whether the SNZI may contain an unmatched arrival.
    ///
    /// This operation is wait-free and constant time.  A poisoned instance
    /// returns `true` permanently.  A false result is only a point-in-time
    /// observation; it does not prevent a concurrent or future arrival.
    #[inline]
    pub fn query(&self) -> bool {
        if self.root.value.load(Ordering::SeqCst) != 0 {
            return true;
        }
        self.poison.value.load(Ordering::SeqCst) != POISON_NONE
    }

    /// Returns the terminal poison reason, if any.
    #[inline]
    pub fn poison_reason(&self) -> Option<PoisonReason> {
        PoisonReason::from_code(self.poison.value.load(Ordering::SeqCst))
    }

    /// Collects a wait-free, best-effort diagnostic snapshot.
    pub fn debug_snapshot(&self) -> SnziSnapshot {
        let mut active_nodes = 0_usize;
        let mut half_nodes = 0_usize;
        let mut local_count_sum = 0_u64;
        let mut invalid_nodes = 0_usize;

        let mut index = 0;
        while index < NODES {
            let state = self.nodes[index].value.load(Ordering::SeqCst);
            let count = state_count(state);
            let half = state_is_half(state);
            if half {
                half_nodes += 1;
                if count != 0 {
                    invalid_nodes += 1;
                }
            } else if count != 0 {
                active_nodes += 1;
                local_count_sum = local_count_sum.saturating_add(count);
            }
            index += 1;
        }

        SnziSnapshot {
            root_count: self.root.value.load(Ordering::SeqCst),
            active_nodes,
            half_nodes,
            local_count_sum,
            invalid_nodes,
            poison: self.poison_reason(),
        }
    }

    /// Returns whether a diagnostic scan observes a fully idle, healthy tree.
    ///
    /// This is intended for tests and teardown after external admission has
    /// stopped.  With concurrent operations it is only a best-effort scan; use
    /// [`Snzi::query`] for the linearizable nonzero indication.
    #[inline]
    pub fn is_quiescent(&self) -> bool {
        self.debug_snapshot().is_quiescent()
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

    #[inline]
    fn leaf_node(&self, leaf: usize) -> Result<usize, SnziError> {
        let leaf_count = self.leaf_count();
        if leaf >= leaf_count {
            return Err(SnziError::InvalidLeaf { leaf, leaf_count });
        }
        Ok(Self::leaf_start() + leaf)
    }

    fn arrive_node(&self, node: usize) -> Result<u64, SnziError> {
        let mut arrived_generation = None;
        let mut redundant_parent_arrivals = 0_usize;

        while arrived_generation.is_none() {
            self.ensure_healthy()?;
            let state = self.nodes[node].value.load(Ordering::SeqCst);
            let count = state_count(state);
            let generation = state_generation(state);

            if state_is_half(state) {
                if count != 0 {
                    return Err(self.poison_with(PoisonReason::InvariantViolation));
                }

                self.arrive_parent(node)?;
                let active = state_with_count(generation, 1);
                match self.nodes[node].value.compare_exchange(
                    state,
                    active,
                    Ordering::SeqCst,
                    Ordering::SeqCst,
                ) {
                    Ok(_) => test_fault!(SnziNodePublished, node),
                    Err(_) => {
                        redundant_parent_arrivals = redundant_parent_arrivals
                            .checked_add(1)
                            .ok_or_else(|| self.poison_with(PoisonReason::CompensationOverflow))?;
                    }
                }
                continue;
            }

            if count == 0 {
                if generation == VERSION_MASK {
                    return Err(self.poison_with(PoisonReason::GenerationExhausted));
                }

                let next_generation = generation + 1;
                let half = state_half(next_generation);
                if self.nodes[node]
                    .value
                    .compare_exchange(state, half, Ordering::SeqCst, Ordering::SeqCst)
                    .is_ok()
                {
                    test_fault!(SnziHalfPublished, node);
                    arrived_generation = Some(next_generation);
                    self.arrive_parent(node)?;
                    let active = state_with_count(next_generation, 1);
                    match self.nodes[node].value.compare_exchange(
                        half,
                        active,
                        Ordering::SeqCst,
                        Ordering::SeqCst,
                    ) {
                        Ok(_) => test_fault!(SnziNodePublished, node),
                        Err(_) => {
                            redundant_parent_arrivals =
                                redundant_parent_arrivals.checked_add(1).ok_or_else(|| {
                                    self.poison_with(PoisonReason::CompensationOverflow)
                                })?;
                        }
                    }
                }
                continue;
            }

            if count == COUNT_MASK {
                return Err(self.poison_with(PoisonReason::NodeCountOverflow));
            }

            let incremented = state_with_count(generation, count + 1);
            if self.nodes[node]
                .value
                .compare_exchange(state, incremented, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                test_fault!(SnziNodeIncremented, node);
                arrived_generation = Some(generation);
            }
        }

        while redundant_parent_arrivals != 0 {
            test_fault!(SnziBeforeCompensation, node);
            self.depart_parent(node)?;
            redundant_parent_arrivals -= 1;
        }

        match arrived_generation {
            Some(generation) => Ok(generation),
            None => Err(self.poison_with(PoisonReason::InvariantViolation)),
        }
    }

    fn depart_node(&self, node: usize, expected: Option<(usize, u64)>) -> Result<(), SnziError> {
        loop {
            self.ensure_healthy()?;
            let state = self.nodes[node].value.load(Ordering::SeqCst);
            let count = state_count(state);
            let generation = state_generation(state);
            let half = state_is_half(state);

            if half && count != 0 {
                return Err(self.poison_with(PoisonReason::InvariantViolation));
            }

            if let Some((leaf, expected_generation)) = expected {
                if generation != expected_generation {
                    return Err(SnziError::GenerationMismatch {
                        leaf,
                        token_generation: expected_generation,
                        current_generation: generation,
                    });
                }
                if half || count == 0 {
                    return Err(SnziError::InactiveToken {
                        leaf,
                        generation: expected_generation,
                    });
                }
            } else if half || count == 0 {
                return Err(self.poison_with(PoisonReason::InvariantViolation));
            }

            let decremented = state_with_count(generation, count - 1);
            if self.nodes[node]
                .value
                .compare_exchange(state, decremented, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                test_fault!(SnziNodeDecremented, node);
                if count == 1 {
                    self.depart_parent(node)?;
                }
                return Ok(());
            }
        }
    }

    #[inline]
    fn arrive_parent(&self, node: usize) -> Result<(), SnziError> {
        if node < FANOUT {
            self.root_arrive()
        } else {
            self.arrive_node((node - FANOUT) / FANOUT).map(|_| ())
        }
    }

    #[inline]
    fn depart_parent(&self, node: usize) -> Result<(), SnziError> {
        if node < FANOUT {
            self.root_depart()
        } else {
            self.depart_node((node - FANOUT) / FANOUT, None)
        }
    }

    fn root_arrive(&self) -> Result<(), SnziError> {
        loop {
            self.ensure_healthy()?;
            let count = self.root.value.load(Ordering::SeqCst);
            if count == u64::MAX {
                return Err(self.poison_with(PoisonReason::RootCountOverflow));
            }
            if self
                .root
                .value
                .compare_exchange(count, count + 1, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                test_fault!(SnziRootArrived, 0);
                return Ok(());
            }
        }
    }

    fn root_depart(&self) -> Result<(), SnziError> {
        loop {
            self.ensure_healthy()?;
            let count = self.root.value.load(Ordering::SeqCst);
            if count == 0 {
                return Err(self.poison_with(PoisonReason::InvariantViolation));
            }
            if self
                .root
                .value
                .compare_exchange(count, count - 1, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                test_fault!(SnziRootDeparted, 0);
                return Ok(());
            }
        }
    }

    #[inline]
    fn ensure_healthy(&self) -> Result<(), SnziError> {
        match self.poison_reason() {
            Some(reason) => Err(SnziError::Poisoned(reason)),
            None => Ok(()),
        }
    }

    fn poison_with(&self, reason: PoisonReason) -> SnziError {
        let _ = self.poison.value.compare_exchange(
            POISON_NONE,
            reason.code(),
            Ordering::SeqCst,
            Ordering::SeqCst,
        );
        SnziError::Poisoned(self.poison_reason().unwrap_or(reason))
    }
}

#[cfg(not(shmem_pod_loom))]
impl<const NODES: usize> Default for Snzi<NODES> {
    fn default() -> Self {
        Self::new()
    }
}

const fn state_count(state: u64) -> u64 {
    state & COUNT_MASK
}

const fn state_is_half(state: u64) -> bool {
    state & HALF_BIT != 0
}

const fn state_generation(state: u64) -> u64 {
    state >> VERSION_SHIFT
}

const fn state_with_count(generation: u64, count: u64) -> u64 {
    (generation << VERSION_SHIFT) | count
}

const fn state_half(generation: u64) -> u64 {
    (generation << VERSION_SHIFT) | HALF_BIT
}

#[inline(always)]
#[cfg(not(shmem_pod_loom))]
unsafe fn initialize_cacheline(destination: *mut CacheAlignedAtomicU64, value: u64) {
    // SAFETY: The caller provides one exclusive, aligned cacheline object. The
    // volatile byte loop deliberately prevents LLVM from replacing it with a
    // freestanding-hostile memset call.
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

// SAFETY: Snzi contains only aligned AtomicU64 values and byte padding.  It has
// no destructor, allocation, process-local resource, or stored address.
#[cfg(not(shmem_pod_loom))]
unsafe impl<const NODES: usize> FixedAddressPodValue for Snzi<NODES> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod values must not need drop");
        let state = __private::mix_bytes(__private::FINGERPRINT_SEED, b"shmem-pod-snzi-v1");
        let state = __private::mix_usize(state, size_of::<Self>());
        let state = __private::mix_usize(state, align_of::<Self>());
        let state = __private::mix_usize(state, NODES);
        let state = __private::mix_usize(state, offset_of!(Self, root));
        let state = __private::mix_usize(state, offset_of!(Self, poison));
        let state = __private::mix_usize(state, offset_of!(Self, nodes));
        let state = __private::mix_usize(state, offset_of!(CacheAlignedAtomicU64, value));
        __private::finish(__private::mix_u128(
            state,
            <AtomicU64 as FixedAddressPodValue>::FINGERPRINT,
        ))
    };
}

// SAFETY: Snzi's integers are counters and indices, never addresses, and its
// atomics and padding contain no pointers.
#[cfg(not(shmem_pod_loom))]
unsafe impl<const NODES: usize> PodValue for Snzi<NODES> {}

// SAFETY: Every shared mutation performed by the safe API is a 64-bit atomic
// operation.  Padding is initialized once and never mutated.
#[cfg(not(shmem_pod_loom))]
unsafe impl<const NODES: usize> PodSync for Snzi<NODES> {}

#[cfg(all(test, not(shmem_pod_loom)))]
mod tests {
    use super::*;

    #[test]
    fn half_helper_publishes_initiator_then_counts_itself_once() {
        let snzi = Snzi::<4>::new();
        let leaf_node = Snzi::<4>::leaf_start();

        // Model an initiator immediately after its 0 -> HALF transition. A
        // helper must publish count 1 for that initiator, then loop and add its
        // own arrival as count 2.
        snzi.nodes[leaf_node]
            .value
            .store(state_half(1), Ordering::SeqCst);
        let helper = snzi.arrive(0).unwrap();

        // Complete the initiator's delayed redundant parent arrival and
        // compensation after its HALF -> 1 CAS loses to the helper.
        snzi.root_arrive().unwrap();
        snzi.root_depart().unwrap();

        let snapshot = snzi.debug_snapshot();
        assert_eq!(snapshot.root_count, 1);
        assert_eq!(snapshot.active_nodes, 1);
        assert_eq!(snapshot.local_count_sum, 2);

        snzi.depart_token(0, 1).unwrap();
        assert!(snzi.query());
        helper.depart().unwrap();
        assert!(!snzi.query());
        assert!(snzi.is_quiescent());
    }
}
