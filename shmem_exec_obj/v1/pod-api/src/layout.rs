use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

pub const IMAGE_MAGIC: u64 = u64::from_le_bytes(*b"RVPODIMG");
pub const STATE_MAGIC: u64 = u64::from_le_bytes(*b"RVPODSTA");
pub const ABI_VERSION: u32 = 1;
pub const TARGET_ARCH_X86_64: u16 = 0x3e;
pub const IMAGE_PAGE_SIZE: u32 = 4096;
pub const COUNTER_COUNT: usize = 4;
pub const MAX_CONNECTIONS: usize = 512;
pub const METHOD_COUNT: usize = 4;

pub const STATUS_OK: i32 = 0;
pub const STATUS_NULL: i32 = -1;
pub const STATUS_BAD_INDEX: i32 = -2;
pub const STATUS_BAD_STATE: i32 = -3;
pub const STATUS_CONNECTIONS_FULL: i32 = -4;

pub const METHOD_REGISTER: usize = 0;
pub const METHOD_COARSE_ADD: usize = 1;
pub const METHOD_FINE_ADD: usize = 2;
pub const METHOD_ATOMIC_ADD: usize = 3;

pub const METHOD_SYMBOLS: [&str; METHOD_COUNT] = [
    "pod_register",
    "pod_coarse_add",
    "pod_fine_add",
    "pod_atomic_add",
];

#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct PodMethod {
    pub offset: u64,
    pub size: u32,
    pub reserved: u32,
}

#[repr(C, align(64))]
#[derive(Clone, Copy, Debug)]
pub struct PodImageHeader {
    pub magic: u64,
    pub abi_version: u32,
    pub target_arch: u16,
    pub method_count: u16,
    pub header_size: u32,
    pub page_size: u32,
    pub image_len: u64,
    pub state_offset: u64,
    pub state_len: u64,
    pub methods: [PodMethod; METHOD_COUNT],
    pub reserved: [u8; 16],
}

impl PodImageHeader {
    pub const fn empty() -> Self {
        Self {
            magic: IMAGE_MAGIC,
            abi_version: ABI_VERSION,
            target_arch: TARGET_ARCH_X86_64,
            method_count: METHOD_COUNT as u16,
            header_size: core::mem::size_of::<Self>() as u32,
            page_size: IMAGE_PAGE_SIZE,
            image_len: 0,
            state_offset: 0,
            state_len: 0,
            methods: [PodMethod {
                offset: 0,
                size: 0,
                reserved: 0,
            }; METHOD_COUNT],
            reserved: [0; 16],
        }
    }
}

#[repr(C, align(64))]
pub struct StateHeader {
    pub magic: u64,
    pub abi_version: u32,
    pub state_size: u32,
    pub counter_count: u32,
    pub max_connections: u32,
    pub reserved: [u8; 40],
}

impl StateHeader {
    fn new() -> Self {
        Self {
            magic: STATE_MAGIC,
            abi_version: ABI_VERSION,
            state_size: core::mem::size_of::<PodState>() as u32,
            counter_count: COUNTER_COUNT as u32,
            max_connections: MAX_CONNECTIONS as u32,
            reserved: [0; 40],
        }
    }
}

#[repr(C, align(64))]
pub struct ControlBlock {
    pub connection_count: AtomicU64,
    pub ready_count: AtomicU64,
    pub start_flag: AtomicU32,
    pub failure_count: AtomicU32,
    pub reserved: [u8; 40],
}

impl ControlBlock {
    fn new() -> Self {
        Self {
            connection_count: AtomicU64::new(0),
            ready_count: AtomicU64::new(0),
            start_flag: AtomicU32::new(0),
            failure_count: AtomicU32::new(0),
            reserved: [0; 40],
        }
    }
}

#[repr(C, align(64))]
pub struct CachePaddedLock {
    pub word: AtomicU32,
    pub reserved: [u8; 60],
}

impl CachePaddedLock {
    fn new() -> Self {
        Self {
            word: AtomicU32::new(0),
            reserved: [0; 60],
        }
    }
}

#[repr(C, align(64))]
pub struct CoarseValues {
    pub values: [UnsafeCell<u64>; COUNTER_COUNT],
    pub reserved: [u8; 32],
}

impl CoarseValues {
    fn new() -> Self {
        Self {
            values: core::array::from_fn(|_| UnsafeCell::new(0)),
            reserved: [0; 32],
        }
    }
}

#[repr(C, align(64))]
pub struct FineCounter {
    pub lock: AtomicU32,
    pub reserved0: [u8; 4],
    pub value: UnsafeCell<u64>,
    pub reserved1: [u8; 48],
}

impl FineCounter {
    fn new() -> Self {
        Self {
            lock: AtomicU32::new(0),
            reserved0: [0; 4],
            value: UnsafeCell::new(0),
            reserved1: [0; 48],
        }
    }
}

#[repr(C, align(64))]
pub struct AtomicCounter {
    pub value: AtomicU64,
    pub reserved: [u8; 56],
}

impl AtomicCounter {
    fn new() -> Self {
        Self {
            value: AtomicU64::new(0),
            reserved: [0; 56],
        }
    }
}

#[repr(C, align(64))]
pub struct ConnectionRecord {
    pub ready: AtomicU32,
    pub mode: AtomicU32,
    pub pid: AtomicU64,
    pub code_base: AtomicU64,
    pub state_base: AtomicU64,
    pub reserved: [u8; 32],
}

impl ConnectionRecord {
    fn new() -> Self {
        Self {
            ready: AtomicU32::new(0),
            mode: AtomicU32::new(0),
            pid: AtomicU64::new(0),
            code_base: AtomicU64::new(0),
            state_base: AtomicU64::new(0),
            reserved: [0; 32],
        }
    }
}

#[repr(C, align(64))]
pub struct PodState {
    pub header: StateHeader,
    pub control: ControlBlock,
    pub coarse_lock: CachePaddedLock,
    pub coarse_values: CoarseValues,
    pub fine_values: [FineCounter; COUNTER_COUNT],
    pub atomic_values: [AtomicCounter; COUNTER_COUNT],
    pub connections: [ConnectionRecord; MAX_CONNECTIONS],
}

pub type PodRegisterFn = unsafe extern "C" fn(*mut PodState, u64, u64, u64, u32) -> i32;
pub type PodAddFn = unsafe extern "C" fn(*mut PodState, u32, u64) -> i32;

impl PodState {
    pub fn new() -> Self {
        Self {
            header: StateHeader::new(),
            control: ControlBlock::new(),
            coarse_lock: CachePaddedLock::new(),
            coarse_values: CoarseValues::new(),
            fine_values: core::array::from_fn(|_| FineCounter::new()),
            atomic_values: core::array::from_fn(|_| AtomicCounter::new()),
            connections: core::array::from_fn(|_| ConnectionRecord::new()),
        }
    }

    pub fn is_compatible(&self) -> bool {
        self.header.magic == STATE_MAGIC
            && self.header.abi_version == ABI_VERSION
            && self.header.state_size as usize == core::mem::size_of::<Self>()
            && self.header.counter_count as usize == COUNTER_COUNT
            && self.header.max_connections as usize == MAX_CONNECTIONS
    }

    /// Reads a non-atomic counter after all writers have stopped.
    ///
    /// # Safety
    ///
    /// No pod method may concurrently access the selected coarse or fine counter.
    pub unsafe fn counter_after_quiescence(&self, mode: PodMode, index: usize) -> Option<u64> {
        if index >= COUNTER_COUNT {
            return None;
        }

        Some(match mode {
            PodMode::Coarse => {
                // The caller must ensure no pod method can be concurrently writing.
                unsafe { self.coarse_values.values[index].get().read() }
            }
            PodMode::Fine => {
                // The caller must ensure no pod method can be concurrently writing.
                unsafe { self.fine_values[index].value.get().read() }
            }
            PodMode::Atomic => self.atomic_values[index].value.load(Ordering::Acquire),
        })
    }
}

impl Default for PodState {
    fn default() -> Self {
        Self::new()
    }
}

#[repr(u32)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PodMode {
    Coarse = 0,
    Fine = 1,
    Atomic = 2,
}

impl PodMode {
    pub const ALL: [Self; 3] = [Self::Coarse, Self::Fine, Self::Atomic];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Coarse => "coarse",
            Self::Fine => "fine",
            Self::Atomic => "atomic",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "coarse" => Some(Self::Coarse),
            "fine" => Some(Self::Fine),
            "atomic" => Some(Self::Atomic),
            _ => None,
        }
    }
}

pub const fn align_up(value: usize, alignment: usize) -> usize {
    value.div_ceil(alignment) * alignment
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_layout_has_expected_alignment_and_sizes() {
        assert_eq!(core::mem::size_of::<PodImageHeader>(), 128);
        assert_eq!(core::mem::align_of::<PodImageHeader>(), 64);
        assert_eq!(core::mem::size_of::<StateHeader>(), 64);
        assert_eq!(core::mem::size_of::<ControlBlock>(), 64);
        assert_eq!(core::mem::size_of::<CachePaddedLock>(), 64);
        assert_eq!(core::mem::size_of::<CoarseValues>(), 64);
        assert_eq!(core::mem::size_of::<FineCounter>(), 64);
        assert_eq!(core::mem::size_of::<AtomicCounter>(), 64);
        assert_eq!(core::mem::size_of::<ConnectionRecord>(), 64);
        assert_eq!(core::mem::align_of::<PodState>(), 64);
        assert_eq!(core::mem::size_of::<PodState>(), 33_536);
    }
}
