pub use shmem_pod::pod_api::{
    BindError, MethodResolver, MethodSignature, MethodSpec, PodApiDescriptor,
};
use shmem_pod_macros::pod;
use std::fmt;

pub const IMAGE_MAGIC: [u8; 8] = *b"SHPODI2\0";
pub const IMAGE_VERSION: u16 = 2;
pub const IMAGE_ABI_REVISION: u32 = 2;
pub const HEADER_SIZE: usize = 4096;
pub const PAGE_SIZE: usize = 4096;
pub const METHOD_ENTRY_SIZE: usize = 32;
pub const METHOD_TABLE_OFFSET: usize = 320;
pub const MAX_METHODS: usize = (HEADER_SIZE - METHOD_TABLE_OFFSET) / METHOD_ENTRY_SIZE;

pub const TARGET_OS_LINUX: u16 = 1;
pub const TARGET_ARCH_X86_64: u16 = 0x3e;
pub const ENDIAN_LITTLE: u8 = 1;
pub const POINTER_WIDTH_64: u8 = 64;

pub const STATE_ENVELOPE_SIZE: usize = PAGE_SIZE;
pub const DEFAULT_PAYLOAD_LEN: usize = 1024 * 1024;
pub const DEFAULT_STATE_FILE_LEN: usize = STATE_ENVELOPE_SIZE + DEFAULT_PAYLOAD_LEN;

pub const CAP_OFFSET_ARENA: u64 = 1 << 0;
pub const CAP_REQUIRES_SAME_VA: u64 = 1 << 1;
pub const CAP_ALLOCATOR_API: u64 = 1 << 2;
pub const KNOWN_CAPABILITIES: u64 = CAP_OFFSET_ARENA | CAP_REQUIRES_SAME_VA | CAP_ALLOCATOR_API;
pub const FLAG_OFFSET_ARENA: u64 = CAP_OFFSET_ARENA;
pub const FLAG_REQUIRES_SAME_VA: u64 = CAP_REQUIRES_SAME_VA;
pub const FLAG_ALLOCATOR_API: u64 = CAP_ALLOCATOR_API;

pub const HARDENING_W_X: u64 = 1 << 0;
pub const HARDENING_NX_STATE: u64 = 1 << 1;
pub const KNOWN_HARDENING: u64 = HARDENING_W_X | HARDENING_NX_STATE;
pub const CPU_X86_64_BASELINE: u64 = 1 << 0;
pub const KNOWN_CPU_FEATURES: u64 = CPU_X86_64_BASELINE;

pub const STATE_MAGIC: [u8; 8] = *b"SHPODS2\0";
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
pub const ENVELOPE_API_FINGERPRINT_OFFSET: usize = 160;
pub const ENVELOPE_STATE_FINGERPRINT_OFFSET: usize = 176;

#[pod(
    namespace = "shmem-pod.example.offset-table.v2",
    bindings = DemoPodBindings,
    descriptor = DEMO_POD_API
)]
unsafe extern "C" {
    #[pod_method(id = 1, symbol = "shmem_pod_layout_size")]
    pub fn layout_size() -> u64;
    #[pod_method(id = 2, symbol = "shmem_pod_layout_align")]
    pub fn layout_align() -> u64;
    #[pod_method(id = 3, symbol = "shmem_pod_layout_hash")]
    pub fn layout_hash() -> u64;
    #[pod_method(id = 10, symbol = "shmem_pod_init")]
    pub fn init(state: *mut u8, region_len: u64) -> i32;
    #[pod_method(id = 11, symbol = "shmem_pod_validate")]
    pub fn validate(state: *mut u8, region_len: u64) -> i32;
    #[pod_method(id = 20, symbol = "shmem_pod_upsert")]
    pub fn upsert(state: *mut u8, key: u64, delta: u64) -> i32;
    #[pod_method(id = 21, symbol = "shmem_pod_get")]
    pub fn get(state: *mut u8, key: u64, output: *mut u64) -> i32;
    #[pod_method(id = 22, symbol = "shmem_pod_len")]
    pub fn len(state: *mut u8) -> u64;
    #[pod_method(id = 23, symbol = "shmem_pod_allocated")]
    pub fn allocated(state: *mut u8) -> u64;
    #[pod_method(id = 24, symbol = "shmem_pod_capacity")]
    pub fn capacity(state: *mut u8) -> u64;
    #[pod_method(id = 30, symbol = "shmem_pod_snzi_leaf_count")]
    pub fn snzi_leaf_count() -> u64;
    #[pod_method(id = 31, symbol = "shmem_pod_snzi_arrive")]
    pub fn snzi_arrive(state: *mut u8, leaf: u64, output: *mut u64) -> i32;
    #[pod_method(id = 32, symbol = "shmem_pod_snzi_depart")]
    pub fn snzi_depart(state: *mut u8, token: u64) -> i32;
    #[pod_method(id = 33, symbol = "shmem_pod_snzi_query")]
    pub fn snzi_query(state: *mut u8) -> u64;
    #[pod_method(id = 34, symbol = "shmem_pod_snzi_quiescent")]
    pub fn snzi_quiescent(state: *mut u8) -> u64;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ImageMetadata {
    pub api_fingerprint: u128,
    pub state_fingerprint: u128,
    pub build_sha256: [u8; 32],
    pub provenance_sha256: [u8; 32],
    pub required_capabilities: u64,
    pub optional_capabilities: u64,
    pub required_hardening: u64,
    pub required_cpu_features: u64,
    pub required_state_address: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct MethodEntry {
    pub id: u32,
    pub signature: u16,
    pub flags: u16,
    pub offset: u64,
    pub size: u32,
    pub alignment: u32,
    pub reserved: u64,
}

impl MethodEntry {
    pub fn decoded_signature(&self) -> Result<MethodSignature, HeaderError> {
        MethodSignature::from_u16(self.signature)
            .ok_or_else(|| HeaderError(format!("method {} has unknown signature", self.id)))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ImageHeader {
    pub image_len: u64,
    pub code_offset: u64,
    pub code_len: u64,
    pub code_alignment: u64,
    pub state_file_len: u64,
    pub payload_offset: u64,
    pub payload_len: u64,
    pub state_alignment: u64,
    pub code_sha256: [u8; 32],
    pub metadata: ImageMetadata,
    pub methods: Vec<MethodEntry>,
}

impl ImageHeader {
    pub fn new(
        code_len: usize,
        code_alignment: u64,
        code_sha256: [u8; 32],
        metadata: ImageMetadata,
        methods: Vec<MethodEntry>,
    ) -> Result<Self, HeaderError> {
        let image_len = HEADER_SIZE
            .checked_add(code_len)
            .ok_or_else(|| HeaderError("image length overflow".into()))?;
        let header = Self {
            image_len: image_len as u64,
            code_offset: HEADER_SIZE as u64,
            code_len: code_len as u64,
            code_alignment,
            state_file_len: DEFAULT_STATE_FILE_LEN as u64,
            payload_offset: STATE_ENVELOPE_SIZE as u64,
            payload_len: DEFAULT_PAYLOAD_LEN as u64,
            state_alignment: PAGE_SIZE as u64,
            code_sha256,
            metadata,
            methods,
        };
        header.validate()?;
        Ok(header)
    }

    pub fn encode(&self) -> Result<[u8; HEADER_SIZE], HeaderError> {
        self.validate()?;
        let mut bytes = [0_u8; HEADER_SIZE];
        bytes[0..8].copy_from_slice(&IMAGE_MAGIC);
        put_u16(&mut bytes, 8, IMAGE_VERSION);
        bytes[10] = ENDIAN_LITTLE;
        bytes[11] = POINTER_WIDTH_64;
        put_u32(&mut bytes, 12, HEADER_SIZE as u32);
        put_u16(&mut bytes, 16, TARGET_OS_LINUX);
        put_u16(&mut bytes, 18, TARGET_ARCH_X86_64);
        put_u32(&mut bytes, 20, PAGE_SIZE as u32);
        put_u16(&mut bytes, 24, METHOD_ENTRY_SIZE as u16);
        put_u16(&mut bytes, 26, self.methods.len() as u16);
        put_u32(&mut bytes, 28, IMAGE_ABI_REVISION);
        put_u64(&mut bytes, 32, self.image_len);
        put_u64(&mut bytes, 40, self.code_offset);
        put_u64(&mut bytes, 48, self.code_len);
        put_u64(&mut bytes, 56, self.code_alignment);
        put_u64(&mut bytes, 64, self.state_file_len);
        put_u64(&mut bytes, 72, self.payload_offset);
        put_u64(&mut bytes, 80, self.payload_len);
        put_u64(&mut bytes, 88, self.state_alignment);
        put_u64(&mut bytes, 96, self.metadata.required_state_address);
        put_u64(&mut bytes, 104, self.metadata.required_capabilities);
        put_u64(&mut bytes, 112, self.metadata.optional_capabilities);
        put_u64(&mut bytes, 120, self.metadata.required_hardening);
        put_u64(&mut bytes, 128, self.metadata.required_cpu_features);
        put_u128(&mut bytes, 136, self.metadata.api_fingerprint);
        put_u128(&mut bytes, 152, self.metadata.state_fingerprint);
        bytes[168..200].copy_from_slice(&self.code_sha256);
        bytes[200..232].copy_from_slice(&self.metadata.provenance_sha256);
        bytes[232..264].copy_from_slice(&self.metadata.build_sha256);
        put_u32(&mut bytes, 264, METHOD_TABLE_OFFSET as u32);
        put_u32(
            &mut bytes,
            268,
            (self.methods.len() * METHOD_ENTRY_SIZE) as u32,
        );
        for (index, method) in self.methods.iter().enumerate() {
            let offset = METHOD_TABLE_OFFSET + index * METHOD_ENTRY_SIZE;
            put_u32(&mut bytes, offset, method.id);
            put_u16(&mut bytes, offset + 4, method.signature);
            put_u16(&mut bytes, offset + 6, method.flags);
            put_u64(&mut bytes, offset + 8, method.offset);
            put_u32(&mut bytes, offset + 16, method.size);
            put_u32(&mut bytes, offset + 20, method.alignment);
            put_u64(&mut bytes, offset + 24, method.reserved);
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
        if get_u16(bytes, 8) != IMAGE_VERSION
            || bytes[10] != ENDIAN_LITTLE
            || bytes[11] != POINTER_WIDTH_64
            || get_u32(bytes, 12) as usize != HEADER_SIZE
            || get_u16(bytes, 16) != TARGET_OS_LINUX
            || get_u16(bytes, 18) != TARGET_ARCH_X86_64
            || get_u32(bytes, 20) as usize != PAGE_SIZE
            || get_u16(bytes, 24) as usize != METHOD_ENTRY_SIZE
            || get_u32(bytes, 28) != IMAGE_ABI_REVISION
        {
            return Err(HeaderError("unsupported image target or ABI".into()));
        }
        if get_u32(bytes, 264) as usize != METHOD_TABLE_OFFSET {
            return Err(HeaderError("invalid method table offset".into()));
        }
        let method_count = get_u16(bytes, 26) as usize;
        let method_bytes = method_count
            .checked_mul(METHOD_ENTRY_SIZE)
            .ok_or_else(|| HeaderError("method table length overflow".into()))?;
        if method_count == 0
            || method_count > MAX_METHODS
            || get_u32(bytes, 268) as usize != method_bytes
            || METHOD_TABLE_OFFSET + method_bytes > HEADER_SIZE
        {
            return Err(HeaderError("invalid method table extent".into()));
        }
        if bytes[272..METHOD_TABLE_OFFSET]
            .iter()
            .any(|byte| *byte != 0)
            || bytes[METHOD_TABLE_OFFSET + method_bytes..HEADER_SIZE]
                .iter()
                .any(|byte| *byte != 0)
        {
            return Err(HeaderError("reserved header bytes are nonzero".into()));
        }

        let mut code_sha256 = [0_u8; 32];
        code_sha256.copy_from_slice(&bytes[168..200]);
        let mut provenance_sha256 = [0_u8; 32];
        provenance_sha256.copy_from_slice(&bytes[200..232]);
        let mut build_sha256 = [0_u8; 32];
        build_sha256.copy_from_slice(&bytes[232..264]);
        let mut methods = Vec::with_capacity(method_count);
        for index in 0..method_count {
            let offset = METHOD_TABLE_OFFSET + index * METHOD_ENTRY_SIZE;
            methods.push(MethodEntry {
                id: get_u32(bytes, offset),
                signature: get_u16(bytes, offset + 4),
                flags: get_u16(bytes, offset + 6),
                offset: get_u64(bytes, offset + 8),
                size: get_u32(bytes, offset + 16),
                alignment: get_u32(bytes, offset + 20),
                reserved: get_u64(bytes, offset + 24),
            });
        }
        let header = Self {
            image_len: get_u64(bytes, 32),
            code_offset: get_u64(bytes, 40),
            code_len: get_u64(bytes, 48),
            code_alignment: get_u64(bytes, 56),
            state_file_len: get_u64(bytes, 64),
            payload_offset: get_u64(bytes, 72),
            payload_len: get_u64(bytes, 80),
            state_alignment: get_u64(bytes, 88),
            code_sha256,
            metadata: ImageMetadata {
                required_state_address: get_u64(bytes, 96),
                required_capabilities: get_u64(bytes, 104),
                optional_capabilities: get_u64(bytes, 112),
                required_hardening: get_u64(bytes, 120),
                required_cpu_features: get_u64(bytes, 128),
                api_fingerprint: get_u128(bytes, 136),
                state_fingerprint: get_u128(bytes, 152),
                provenance_sha256,
                build_sha256,
            },
            methods,
        };
        header.validate()?;
        Ok(header)
    }

    pub fn validate_for_host(&self, expected_api: &PodApiDescriptor) -> Result<(), HeaderError> {
        self.validate()?;
        if !cfg!(all(
            target_os = "linux",
            target_arch = "x86_64",
            target_endian = "little",
            target_pointer_width = "64"
        )) {
            return Err(HeaderError(
                "this runtime does not support the image target".into(),
            ));
        }
        if self.metadata.api_fingerprint != expected_api.fingerprint {
            return Err(HeaderError("pod API fingerprint mismatch".into()));
        }
        for expected in expected_api.methods {
            let actual = self
                .method(expected.id)
                .ok_or_else(|| HeaderError(format!("required method {} is absent", expected.id)))?;
            if actual.decoded_signature()? != expected.signature {
                return Err(HeaderError(format!(
                    "method {} signature does not match generated bindings",
                    expected.id
                )));
            }
        }
        if self.methods.len() != expected_api.methods.len() {
            return Err(HeaderError(
                "image contains methods outside the generated API".into(),
            ));
        }
        Ok(())
    }

    pub fn method(&self, id: u32) -> Option<&MethodEntry> {
        self.methods
            .binary_search_by_key(&id, |method| method.id)
            .ok()
            .map(|index| &self.methods[index])
    }

    pub fn flags(&self) -> u64 {
        self.metadata.required_capabilities | self.metadata.optional_capabilities
    }

    pub fn required_state_address(&self) -> u64 {
        self.metadata.required_state_address
    }

    pub fn validate(&self) -> Result<(), HeaderError> {
        let code_end = self
            .code_offset
            .checked_add(self.code_len)
            .ok_or_else(|| HeaderError("code extent overflow".into()))?;
        if self.code_offset as usize != HEADER_SIZE
            || self.image_len != code_end
            || self.code_len == 0
            || self.code_alignment == 0
            || !self.code_alignment.is_power_of_two()
            || self.code_alignment > PAGE_SIZE as u64
        {
            return Err(HeaderError("invalid code extent or alignment".into()));
        }
        let payload_end = self
            .payload_offset
            .checked_add(self.payload_len)
            .ok_or_else(|| HeaderError("state extent overflow".into()))?;
        if self.state_file_len as usize != DEFAULT_STATE_FILE_LEN
            || self.payload_offset as usize != STATE_ENVELOPE_SIZE
            || self.payload_len as usize != DEFAULT_PAYLOAD_LEN
            || payload_end != self.state_file_len
            || self.state_alignment == 0
            || !self.state_alignment.is_power_of_two()
            || self.state_alignment > PAGE_SIZE as u64
        {
            return Err(HeaderError("invalid state extent or alignment".into()));
        }
        if self.code_sha256.iter().all(|byte| *byte == 0)
            || self.metadata.build_sha256.iter().all(|byte| *byte == 0)
            || self
                .metadata
                .provenance_sha256
                .iter()
                .all(|byte| *byte == 0)
            || self.metadata.api_fingerprint == 0
            || self.metadata.state_fingerprint == 0
        {
            return Err(HeaderError(
                "authenticated image metadata contains zero identity".into(),
            ));
        }
        let capabilities =
            self.metadata.required_capabilities | self.metadata.optional_capabilities;
        if self.metadata.required_capabilities == 0
            || capabilities & !KNOWN_CAPABILITIES != 0
            || self.metadata.required_hardening == 0
            || self.metadata.required_hardening & !KNOWN_HARDENING != 0
            || self.metadata.required_cpu_features == 0
            || self.metadata.required_cpu_features & !KNOWN_CPU_FEATURES != 0
        {
            return Err(HeaderError(
                "image requirements contain unknown or empty bits".into(),
            ));
        }
        if capabilities & CAP_REQUIRES_SAME_VA != 0 {
            if self.metadata.required_state_address == 0
                || self.metadata.required_state_address % PAGE_SIZE as u64 != 0
            {
                return Err(HeaderError("required state address is invalid".into()));
            }
        } else if self.metadata.required_state_address != 0 {
            return Err(HeaderError(
                "relocatable state declares a fixed address".into(),
            ));
        }
        if self.methods.is_empty() || self.methods.len() > MAX_METHODS {
            return Err(HeaderError("invalid method count".into()));
        }
        let mut previous_id = 0;
        for method in &self.methods {
            let end = method
                .offset
                .checked_add(method.size as u64)
                .ok_or_else(|| HeaderError(format!("method {} extent overflow", method.id)))?;
            if method.id == 0
                || method.id <= previous_id
                || method.offset < self.code_offset
                || method.size == 0
                || end > self.image_len
                || method.alignment == 0
                || !method.alignment.is_power_of_two()
                || method.alignment as u64 > self.code_alignment
                || method.offset % method.alignment as u64 != 0
                || method.flags != 0
                || method.reserved != 0
            {
                return Err(HeaderError(format!("method {} is malformed", method.id)));
            }
            method.decoded_signature()?;
            previous_id = method.id;
        }
        for (index, left) in self.methods.iter().enumerate() {
            let left_end = left.offset + left.size as u64;
            for right in &self.methods[index + 1..] {
                let right_end = right.offset + right.size as u64;
                if left.offset < right_end && right.offset < left_end {
                    return Err(HeaderError(format!(
                        "methods {} and {} overlap",
                        left.id, right.id
                    )));
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

pub fn method_spec(id: u32) -> Option<&'static MethodSpec> {
    DEMO_POD_API.method(id)
}

fn put_u16(bytes: &mut [u8], offset: usize, value: u16) {
    bytes[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32(bytes: &mut [u8], offset: usize, value: u32) {
    bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

fn put_u64(bytes: &mut [u8], offset: usize, value: u64) {
    bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
}

fn put_u128(bytes: &mut [u8], offset: usize, value: u128) {
    bytes[offset..offset + 16].copy_from_slice(&value.to_le_bytes());
}

fn get_u16(bytes: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(
        bytes[offset..offset + 2]
            .try_into()
            .expect("u16 wire field"),
    )
}

fn get_u32(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(
        bytes[offset..offset + 4]
            .try_into()
            .expect("u32 wire field"),
    )
}

fn get_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("u64 wire field"),
    )
}

fn get_u128(bytes: &[u8], offset: usize) -> u128 {
    u128::from_le_bytes(
        bytes[offset..offset + 16]
            .try_into()
            .expect("u128 wire field"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metadata() -> ImageMetadata {
        ImageMetadata {
            api_fingerprint: DEMO_POD_API.fingerprint,
            state_fingerprint: 7,
            build_sha256: [8; 32],
            provenance_sha256: [9; 32],
            required_capabilities: CAP_OFFSET_ARENA,
            optional_capabilities: 0,
            required_hardening: HARDENING_W_X | HARDENING_NX_STATE,
            required_cpu_features: CPU_X86_64_BASELINE,
            required_state_address: 0,
        }
    }

    fn header() -> ImageHeader {
        let methods = DEMO_POD_API
            .methods
            .iter()
            .enumerate()
            .map(|(index, spec)| MethodEntry {
                id: spec.id,
                signature: spec.signature as u16,
                offset: (HEADER_SIZE + index * 32) as u64,
                size: 16,
                alignment: 16,
                ..MethodEntry::default()
            })
            .collect();
        ImageHeader::new(1024, 16, [7; 32], metadata(), methods).unwrap()
    }

    #[test]
    fn round_trips_and_looks_up_methods_by_id() {
        let original = header();
        let encoded = original.encode().unwrap();
        let decoded = ImageHeader::decode(&encoded).unwrap();
        decoded.validate_for_host(&DEMO_POD_API).unwrap();
        assert_eq!(decoded, original);
        assert_eq!(
            decoded.method(20).unwrap().decoded_signature().unwrap(),
            MethodSignature::StateU64U64Status
        );
    }

    #[test]
    fn rejects_target_identity_and_method_corruption() {
        let mut encoded = header().encode().unwrap();
        encoded[10] = 2;
        assert!(ImageHeader::decode(&encoded).is_err());

        let mut encoded = header().encode().unwrap();
        put_u32(&mut encoded, METHOD_TABLE_OFFSET, 0);
        assert!(ImageHeader::decode(&encoded).is_err());

        let mut encoded = header().encode().unwrap();
        put_u16(&mut encoded, METHOD_TABLE_OFFSET + 4, u16::MAX);
        assert!(ImageHeader::decode(&encoded).is_err());

        let mut encoded = header().encode().unwrap();
        encoded[HEADER_SIZE - 1] = 1;
        assert!(ImageHeader::decode(&encoded).is_err());
    }

    #[test]
    fn rejects_api_mismatch_before_binding() {
        let mut header = header();
        header.metadata.api_fingerprint ^= 1;
        assert!(header.validate_for_host(&DEMO_POD_API).is_err());
    }
}
