use std::fmt;

pub const IMAGE_MAGIC: [u8; 8] = *b"RVPODV2\0";
pub const IMAGE_VERSION: u32 = 2;
pub const HEADER_SIZE: usize = 512;
pub const PAGE_SIZE: usize = 4096;
pub const TARGET_ARCH_X86_64: u16 = 0x3e;
pub const STATE_ENVELOPE_SIZE: usize = PAGE_SIZE;
pub const DEFAULT_PAYLOAD_LEN: usize = 1024 * 1024;
pub const DEFAULT_STATE_FILE_LEN: usize = STATE_ENVELOPE_SIZE + DEFAULT_PAYLOAD_LEN;
pub const METHOD_COUNT: usize = 10;
pub const FLAG_OFFSET_ARENA: u64 = 1 << 0;
pub const FLAG_REQUIRES_SAME_VA: u64 = 1 << 1;
pub const FLAG_ALLOCATOR_API: u64 = 1 << 2;

pub const STATE_MAGIC: [u8; 8] = *b"RVSTATE2";
pub const STATE_VERSION: u32 = 2;
pub const STATE_STATUS_EMPTY: u32 = 0;
pub const STATE_STATUS_INITIALIZING: u32 = 1;
pub const STATE_STATUS_READY: u32 = 2;
pub const STATE_STATUS_POISONED: u32 = 3;

pub const ENVELOPE_MAGIC_OFFSET: usize = 0;
pub const ENVELOPE_VERSION_OFFSET: usize = 8;
pub const ENVELOPE_STATUS_OFFSET: usize = 16;
pub const ENVELOPE_READY_COUNT_OFFSET: usize = 20;
pub const ENVELOPE_START_FLAG_OFFSET: usize = 24;
pub const ENVELOPE_FAILURE_OFFSET: usize = 28;
pub const ENVELOPE_CODE_HASH_OFFSET: usize = 32;
pub const ENVELOPE_LAYOUT_HASH_OFFSET: usize = 64;
pub const ENVELOPE_LAYOUT_SIZE_OFFSET: usize = 72;
pub const ENVELOPE_LAYOUT_ALIGN_OFFSET: usize = 80;
pub const ENVELOPE_PAYLOAD_LEN_OFFSET: usize = 88;
pub const ENVELOPE_GENERATION_OFFSET: usize = 96;
pub const ENVELOPE_OWNER_PID_OFFSET: usize = 104;
pub const ENVELOPE_ARTIFACT_HASH_OFFSET: usize = 112;
pub const ENVELOPE_FLAGS_OFFSET: usize = 144;
pub const ENVELOPE_REQUIRED_ADDRESS_OFFSET: usize = 152;

pub const METHOD_LAYOUT_SIZE: usize = 0;
pub const METHOD_LAYOUT_ALIGN: usize = 1;
pub const METHOD_LAYOUT_HASH: usize = 2;
pub const METHOD_INIT: usize = 3;
pub const METHOD_VALIDATE: usize = 4;
pub const METHOD_UPSERT: usize = 5;
pub const METHOD_GET: usize = 6;
pub const METHOD_LEN: usize = 7;
pub const METHOD_ALLOCATED: usize = 8;
pub const METHOD_CAPACITY: usize = 9;

#[repr(u16)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Signature {
    NoArgsU64 = 1,
    StateLenStatus = 2,
    StateKeyDeltaStatus = 3,
    StateKeyOutStatus = 4,
    StateU64 = 5,
}

impl Signature {
    fn decode(value: u16) -> Option<Self> {
        Some(match value {
            1 => Self::NoArgsU64,
            2 => Self::StateLenStatus,
            3 => Self::StateKeyDeltaStatus,
            4 => Self::StateKeyOutStatus,
            5 => Self::StateU64,
            _ => return None,
        })
    }
}

pub const METHOD_SPECS: [(&str, Signature); METHOD_COUNT] = [
    ("pod_v2_layout_size", Signature::NoArgsU64),
    ("pod_v2_layout_align", Signature::NoArgsU64),
    ("pod_v2_layout_hash", Signature::NoArgsU64),
    ("pod_v2_init", Signature::StateLenStatus),
    ("pod_v2_validate", Signature::StateLenStatus),
    ("pod_v2_upsert", Signature::StateKeyDeltaStatus),
    ("pod_v2_get", Signature::StateKeyOutStatus),
    ("pod_v2_len", Signature::StateU64),
    ("pod_v2_allocated", Signature::StateU64),
    ("pod_v2_capacity", Signature::StateU64),
];

#[derive(Clone, Copy, Debug, Default)]
pub struct MethodEntry {
    pub offset: u64,
    pub size: u32,
    pub signature: u16,
    pub reserved: u16,
}

#[derive(Clone, Debug)]
pub struct ImageHeader {
    pub image_len: u64,
    pub code_offset: u64,
    pub code_len: u64,
    pub state_file_len: u64,
    pub payload_offset: u64,
    pub payload_len: u64,
    pub code_sha256: [u8; 32],
    pub flags: u64,
    pub required_state_address: u64,
    pub methods: [MethodEntry; METHOD_COUNT],
}

impl ImageHeader {
    pub fn new(code_len: usize, code_sha256: [u8; 32]) -> Result<Self, HeaderError> {
        let image_len = HEADER_SIZE
            .checked_add(code_len)
            .ok_or_else(|| HeaderError("image length overflow".into()))?;
        Ok(Self {
            image_len: image_len as u64,
            code_offset: HEADER_SIZE as u64,
            code_len: code_len as u64,
            state_file_len: DEFAULT_STATE_FILE_LEN as u64,
            payload_offset: STATE_ENVELOPE_SIZE as u64,
            payload_len: DEFAULT_PAYLOAD_LEN as u64,
            code_sha256,
            flags: 0,
            required_state_address: 0,
            methods: [MethodEntry::default(); METHOD_COUNT],
        })
    }

    pub fn encode(&self) -> Result<[u8; HEADER_SIZE], HeaderError> {
        self.validate()?;
        let mut bytes = [0_u8; HEADER_SIZE];
        bytes[0..8].copy_from_slice(&IMAGE_MAGIC);
        put_u32(&mut bytes, 8, IMAGE_VERSION);
        put_u32(&mut bytes, 12, HEADER_SIZE as u32);
        put_u16(&mut bytes, 16, TARGET_ARCH_X86_64);
        put_u16(&mut bytes, 18, METHOD_COUNT as u16);
        put_u32(&mut bytes, 20, PAGE_SIZE as u32);
        put_u64(&mut bytes, 24, self.image_len);
        put_u64(&mut bytes, 32, self.code_offset);
        put_u64(&mut bytes, 40, self.code_len);
        put_u64(&mut bytes, 48, self.state_file_len);
        put_u64(&mut bytes, 56, self.payload_offset);
        put_u64(&mut bytes, 64, self.payload_len);
        bytes[72..104].copy_from_slice(&self.code_sha256);
        put_u64(&mut bytes, 104, self.flags);
        put_u64(&mut bytes, 112, self.required_state_address);
        for (index, method) in self.methods.iter().enumerate() {
            let offset = 128 + index * 16;
            put_u64(&mut bytes, offset, method.offset);
            put_u32(&mut bytes, offset + 8, method.size);
            put_u16(&mut bytes, offset + 12, method.signature);
            put_u16(&mut bytes, offset + 14, method.reserved);
        }
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, HeaderError> {
        if bytes.len() < HEADER_SIZE {
            return Err(HeaderError("image header is truncated".into()));
        }
        if bytes[0..8] != IMAGE_MAGIC {
            return Err(HeaderError("bad image magic".into()));
        }
        if get_u32(bytes, 8) != IMAGE_VERSION
            || get_u32(bytes, 12) as usize != HEADER_SIZE
            || get_u16(bytes, 16) != TARGET_ARCH_X86_64
            || get_u16(bytes, 18) as usize != METHOD_COUNT
            || get_u32(bytes, 20) as usize != PAGE_SIZE
        {
            return Err(HeaderError("unsupported image ABI".into()));
        }
        let mut code_sha256 = [0_u8; 32];
        code_sha256.copy_from_slice(&bytes[72..104]);
        let mut methods = [MethodEntry::default(); METHOD_COUNT];
        for (index, method) in methods.iter_mut().enumerate() {
            let offset = 128 + index * 16;
            *method = MethodEntry {
                offset: get_u64(bytes, offset),
                size: get_u32(bytes, offset + 8),
                signature: get_u16(bytes, offset + 12),
                reserved: get_u16(bytes, offset + 14),
            };
        }
        if bytes[120..128].iter().any(|byte| *byte != 0)
            || bytes[128 + METHOD_COUNT * 16..HEADER_SIZE]
                .iter()
                .any(|byte| *byte != 0)
        {
            return Err(HeaderError("reserved header bytes are nonzero".into()));
        }
        let header = Self {
            image_len: get_u64(bytes, 24),
            code_offset: get_u64(bytes, 32),
            code_len: get_u64(bytes, 40),
            state_file_len: get_u64(bytes, 48),
            payload_offset: get_u64(bytes, 56),
            payload_len: get_u64(bytes, 64),
            code_sha256,
            flags: get_u64(bytes, 104),
            required_state_address: get_u64(bytes, 112),
            methods,
        };
        header.validate()?;
        Ok(header)
    }

    pub fn validate(&self) -> Result<(), HeaderError> {
        if self.code_offset as usize != HEADER_SIZE
            || self.image_len != self.code_offset.saturating_add(self.code_len)
            || self.code_len == 0
        {
            return Err(HeaderError("invalid code extent".into()));
        }
        if self.state_file_len as usize != DEFAULT_STATE_FILE_LEN
            || self.payload_offset as usize != STATE_ENVELOPE_SIZE
            || self.payload_len as usize != DEFAULT_PAYLOAD_LEN
        {
            return Err(HeaderError("invalid state extent".into()));
        }
        if self.code_sha256.iter().all(|byte| *byte == 0) {
            return Err(HeaderError("code hash is zero".into()));
        }
        let known_flags = FLAG_OFFSET_ARENA | FLAG_REQUIRES_SAME_VA | FLAG_ALLOCATOR_API;
        if self.flags == 0 || self.flags & !known_flags != 0 {
            return Err(HeaderError("image capability flags are invalid".into()));
        }
        if self.flags & FLAG_REQUIRES_SAME_VA != 0 {
            if self.required_state_address == 0
                || self.required_state_address % PAGE_SIZE as u64 != 0
            {
                return Err(HeaderError("required state address is invalid".into()));
            }
        } else if self.required_state_address != 0 {
            return Err(HeaderError(
                "relocatable state declares a fixed address".into(),
            ));
        }

        for (index, method) in self.methods.iter().enumerate() {
            let end = method
                .offset
                .checked_add(method.size as u64)
                .ok_or_else(|| HeaderError(format!("method {index} extent overflow")))?;
            if method.offset < self.code_offset || method.size == 0 || end > self.image_len {
                return Err(HeaderError(format!("method {index} is outside code")));
            }
            if Signature::decode(method.signature) != Some(METHOD_SPECS[index].1)
                || method.reserved != 0
            {
                return Err(HeaderError(format!("method {index} signature is invalid")));
            }
        }
        for left in 0..METHOD_COUNT {
            let left_end = self.methods[left].offset + self.methods[left].size as u64;
            for right in (left + 1)..METHOD_COUNT {
                let right_end = self.methods[right].offset + self.methods[right].size as u64;
                if self.methods[left].offset < right_end && self.methods[right].offset < left_end {
                    return Err(HeaderError(format!("methods {left} and {right} overlap")));
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug)]
pub struct HeaderError(pub String);

impl fmt::Display for HeaderError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for HeaderError {}

fn put_u16(bytes: &mut [u8], offset: usize, value: u16) {
    bytes[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32(bytes: &mut [u8], offset: usize, value: u32) {
    bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn put_u64(bytes: &mut [u8], offset: usize, value: u64) {
    bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
}

fn get_u16(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(
        bytes[offset..offset + 2]
            .try_into()
            .expect("fixed header field"),
    )
}

fn get_u32(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(
        bytes[offset..offset + 4]
            .try_into()
            .expect("fixed header field"),
    )
}

fn get_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("fixed header field"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn header() -> ImageHeader {
        let mut header = ImageHeader::new(1024, [7; 32]).unwrap();
        header.flags = FLAG_OFFSET_ARENA;
        for (index, method) in header.methods.iter_mut().enumerate() {
            method.offset = (HEADER_SIZE + index * 32) as u64;
            method.size = 16;
            method.signature = METHOD_SPECS[index].1 as u16;
        }
        header
    }

    #[test]
    fn round_trips_without_rust_layout_casts() {
        let original = header();
        let encoded = original.encode().unwrap();
        let decoded = ImageHeader::decode(&encoded).unwrap();
        assert_eq!(decoded.code_len, original.code_len);
        assert_eq!(decoded.code_sha256, original.code_sha256);
        assert_eq!(
            decoded.methods[5].signature,
            Signature::StateKeyDeltaStatus as u16
        );
    }

    #[test]
    fn rejects_reserved_bytes() {
        let mut encoded = header().encode().unwrap();
        encoded[HEADER_SIZE - 1] = 1;
        assert!(ImageHeader::decode(&encoded).is_err());
    }
}
