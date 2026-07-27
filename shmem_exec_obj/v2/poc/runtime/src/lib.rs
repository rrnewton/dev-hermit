//! Authenticated Linux loader for executable shared-memory pod images.
//!
//! The loader maps its state view without `PROT_EXEC` and checks that view in
//! `/proc/self/maps`. Linux `MFD_NOEXEC_SEAL` and `F_SEAL_EXEC` prevent changing
//! the inode's executable mode, but do not prevent a process holding the fd from
//! creating a separate `PROT_EXEC` mapping. This loader is an integrity boundary
//! for cooperating processes, not a sandbox for hostile native code.

use sha2::{Digest, Sha256};
use shmem_pod_image_api::{
    DEFAULT_STATE_FILE_LEN, ENVELOPE_ARTIFACT_HASH_OFFSET, ENVELOPE_CODE_HASH_OFFSET,
    ENVELOPE_FAILURE_OFFSET, ENVELOPE_FLAGS_OFFSET, ENVELOPE_GENERATION_OFFSET,
    ENVELOPE_LAYOUT_ALIGN_OFFSET, ENVELOPE_LAYOUT_HASH_OFFSET, ENVELOPE_LAYOUT_SIZE_OFFSET,
    ENVELOPE_MAGIC_OFFSET, ENVELOPE_OWNER_PID_OFFSET, ENVELOPE_PAYLOAD_LEN_OFFSET,
    ENVELOPE_READY_COUNT_OFFSET, ENVELOPE_REQUIRED_ADDRESS_OFFSET, ENVELOPE_START_FLAG_OFFSET,
    ENVELOPE_STATUS_OFFSET, ENVELOPE_VERSION_OFFSET, FLAG_REQUIRES_SAME_VA, HEADER_SIZE,
    ImageHeader, METHOD_ALLOCATED, METHOD_CAPACITY, METHOD_GET, METHOD_INIT, METHOD_LAYOUT_ALIGN,
    METHOD_LAYOUT_HASH, METHOD_LAYOUT_SIZE, METHOD_LEN, METHOD_SNZI_ARRIVE, METHOD_SNZI_DEPART,
    METHOD_SNZI_LEAF_COUNT, METHOD_SNZI_QUERY, METHOD_SNZI_QUIESCENT, METHOD_UPSERT,
    METHOD_VALIDATE, PAGE_SIZE, STATE_ENVELOPE_SIZE, STATE_MAGIC, STATE_STATUS_EMPTY,
    STATE_STATUS_INITIALIZING, STATE_STATUS_POISONED, STATE_STATUS_READY, STATE_VERSION,
};
use std::ffi::CString;
use std::fmt;
use std::fs;
use std::io;
use std::mem;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::path::Path;
use std::ptr::{self, NonNull};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

const MFD_NOEXEC_SEAL: libc::c_uint = 0x0008;
const MFD_EXEC: libc::c_uint = 0x0010;
const F_SEAL_EXEC: libc::c_int = 0x0020;
const INVALID_U64: u64 = u64::MAX;
const STATUS_SNZI_INVALID_LEAF: i32 = -10;
const STATUS_SNZI_MALFORMED_TOKEN: i32 = -11;
const STATUS_SNZI_GENERATION_MISMATCH: i32 = -12;
const STATUS_SNZI_INACTIVE_TOKEN: i32 = -13;
const STATUS_SNZI_POISONED: i32 = -14;
const SNZI_TOKEN_LEAF_MASK: u64 = (1_u64 << 16) - 1;
const SNZI_TOKEN_RESERVED_BIT: u64 = 1_u64 << 63;
const SNZI_TOKEN_GENERATION_SHIFT: u32 = 16;

type LayoutFn = unsafe extern "C" fn() -> u64;
type StateLenFn = unsafe extern "C" fn(*mut u8, u64) -> i32;
type UpsertFn = unsafe extern "C" fn(*mut u8, u64, u64) -> i32;
type GetFn = unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32;
type StateU64Fn = unsafe extern "C" fn(*mut u8) -> u64;
type SnziArriveFn = unsafe extern "C" fn(*mut u8, u64, *mut u64) -> i32;
type SnziDepartFn = unsafe extern "C" fn(*mut u8, u64) -> i32;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug)]
pub struct Error(String);

impl Error {
    fn message(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self(error.to_string())
    }
}

impl From<shmem_pod_image_api::HeaderError> for Error {
    fn from(error: shmem_pod_image_api::HeaderError) -> Self {
        Self(error.to_string())
    }
}

#[derive(Clone)]
pub struct PodArtifact {
    bytes: Vec<u8>,
    header: ImageHeader,
    digest: [u8; 32],
}

impl PodArtifact {
    pub fn open(path: impl AsRef<Path>, expected_sha256: &str) -> Result<Self> {
        let bytes = fs::read(path).map_err(Error::from)?;
        Self::from_bytes(bytes, parse_sha256(expected_sha256)?)
    }

    pub fn from_bytes(bytes: Vec<u8>, expected_digest: [u8; 32]) -> Result<Self> {
        if expected_digest.iter().all(|byte| *byte == 0) {
            return Err(Error::message(
                "refusing an all-zero trusted artifact digest",
            ));
        }
        let actual_digest: [u8; 32] = Sha256::digest(&bytes).into();
        if actual_digest != expected_digest {
            return Err(Error::message(format!(
                "artifact SHA-256 mismatch: expected {}, got {}",
                hex(&expected_digest),
                hex(&actual_digest)
            )));
        }
        if bytes.len() < HEADER_SIZE {
            return Err(Error::message("pod artifact is shorter than its header"));
        }
        let header = ImageHeader::decode(&bytes[..HEADER_SIZE])?;
        if usize::try_from(header.image_len).ok() != Some(bytes.len()) {
            return Err(Error::message(
                "pod artifact length differs from the authenticated header",
            ));
        }
        let code_start = usize::try_from(header.code_offset)
            .map_err(|_| Error::message("code offset does not fit usize"))?;
        let code_len = usize::try_from(header.code_len)
            .map_err(|_| Error::message("code length does not fit usize"))?;
        let code_end = code_start
            .checked_add(code_len)
            .ok_or_else(|| Error::message("code extent overflow"))?;
        let code = bytes
            .get(code_start..code_end)
            .ok_or_else(|| Error::message("code extent is outside the artifact"))?;
        let code_digest: [u8; 32] = Sha256::digest(code).into();
        if code_digest != header.code_sha256 {
            return Err(Error::message(
                "authenticated header has the wrong code hash",
            ));
        }
        Ok(Self {
            bytes,
            header,
            digest: actual_digest,
        })
    }

    pub fn header(&self) -> &ImageHeader {
        &self.header
    }

    pub fn digest(&self) -> [u8; 32] {
        self.digest
    }

    pub fn digest_hex(&self) -> String {
        hex(&self.digest)
    }

    fn code(&self) -> &[u8] {
        let start = self.header.code_offset as usize;
        &self.bytes[start..start + self.header.code_len as usize]
    }
}

pub struct PodImage {
    header: ImageHeader,
    artifact_digest: [u8; 32],
    code_fd: OwnedFd,
    code: GuardedMapping,
    layout_size: u64,
    layout_align: u64,
    layout_hash: u64,
    snzi_leaf_count: usize,
}

// The mapped code is immutable and every public state operation synchronizes in the pod.
unsafe impl Send for PodImage {}
unsafe impl Sync for PodImage {}

impl PodImage {
    /// Creates, seals, maps, and begins executing an authenticated native pod.
    ///
    /// # Safety
    ///
    /// The caller must trust the externally supplied digest to identify machine code that obeys
    /// the pod ABI. Hashing and sealing prove identity and immutability, not memory safety.
    pub unsafe fn create_trusted(
        artifact: &PodArtifact,
        code_address: Option<usize>,
    ) -> Result<Self> {
        let fd = create_code_fd(artifact.code())?;
        unsafe { Self::attach_trusted(artifact, fd, code_address) }
    }

    /// Maps an existing sealed code memfd after re-authenticating it against the artifact.
    ///
    /// # Safety
    ///
    /// `artifact` must identify trusted machine code satisfying the pod ABI.
    pub unsafe fn attach_trusted(
        artifact: &PodArtifact,
        code_fd: OwnedFd,
        code_address: Option<usize>,
    ) -> Result<Self> {
        let mapped_len = page_round(artifact.code().len())?;
        verify_code_fd(code_fd.as_raw_fd(), artifact.code(), mapped_len)?;
        let code = GuardedMapping::map(
            code_fd.as_raw_fd(),
            mapped_len,
            libc::PROT_READ | libc::PROT_EXEC,
            code_address,
        )?;
        let layout_size = unsafe { call_layout(&artifact.header, &code, METHOD_LAYOUT_SIZE) };
        let layout_align = unsafe { call_layout(&artifact.header, &code, METHOD_LAYOUT_ALIGN) };
        let layout_hash = unsafe { call_layout(&artifact.header, &code, METHOD_LAYOUT_HASH) };
        let snzi_leaf_count =
            unsafe { call_layout(&artifact.header, &code, METHOD_SNZI_LEAF_COUNT) };
        if layout_size == 0
            || layout_size > artifact.header.payload_len
            || layout_align == 0
            || !layout_align.is_power_of_two()
            || layout_align > PAGE_SIZE as u64
            || layout_hash == 0
            || snzi_leaf_count == 0
            || snzi_leaf_count > (u16::MAX as u64) + 1
        {
            return Err(Error::message(
                "pod returned invalid opaque layout metadata",
            ));
        }
        Ok(Self {
            header: artifact.header.clone(),
            artifact_digest: artifact.digest,
            code_fd,
            code,
            layout_size,
            layout_align,
            layout_hash,
            snzi_leaf_count: snzi_leaf_count as usize,
        })
    }

    pub fn create_state(&self, state_address: Option<usize>) -> Result<PodState> {
        let fd = create_state_fd(self.header.state_file_len as usize)?;
        let mut state = self.map_state(fd, state_address)?;
        self.initialize_state(&mut state)?;
        Ok(state)
    }

    pub fn attach_state(
        &self,
        state_fd: OwnedFd,
        state_address: Option<usize>,
    ) -> Result<PodState> {
        verify_state_fd(state_fd.as_raw_fd(), self.header.state_file_len as usize)?;
        let state = self.map_state(state_fd, state_address)?;
        self.validate_envelope(&state)?;
        self.validate(&state)?;
        Ok(state)
    }

    pub fn layout_size(&self) -> u64 {
        self.layout_size
    }

    pub fn layout_align(&self) -> u64 {
        self.layout_align
    }

    pub fn layout_hash(&self) -> u64 {
        self.layout_hash
    }

    pub fn snzi_leaf_count(&self) -> usize {
        self.snzi_leaf_count
    }

    pub fn artifact_digest(&self) -> [u8; 32] {
        self.artifact_digest
    }

    pub fn code_address(&self) -> usize {
        self.code.data_address()
    }

    pub fn code_fd(&self) -> RawFd {
        self.code_fd.as_raw_fd()
    }

    pub fn duplicate_code_fd_for_exec(&self) -> Result<OwnedFd> {
        duplicate_inheritable(self.code_fd.as_raw_fd())
    }

    pub fn validate(&self, state: &PodState) -> Result<()> {
        self.ensure_pair(state)?;
        let function: StateLenFn = unsafe { mem::transmute(self.entry(METHOD_VALIDATE)) };
        let status = unsafe { function(state.payload_ptr(), state.payload_len) };
        status_result("validate", status)
    }

    pub fn upsert(&self, state: &PodState, key: u64, delta: u64) -> Result<()> {
        self.ensure_pair(state)?;
        let function: UpsertFn = unsafe { mem::transmute(self.entry(METHOD_UPSERT)) };
        let status = unsafe { function(state.payload_ptr(), key, delta) };
        status_result("upsert", status)
    }

    pub fn get(&self, state: &PodState, key: u64) -> Result<Option<u64>> {
        self.ensure_pair(state)?;
        let function: GetFn = unsafe { mem::transmute(self.entry(METHOD_GET)) };
        let mut output = 0_u64;
        let status = unsafe { function(state.payload_ptr(), key, &mut output) };
        match status {
            0 => Ok(Some(output)),
            -4 => Ok(None),
            _ => Err(status_error("get", status)),
        }
    }

    pub fn len(&self, state: &PodState) -> Result<u64> {
        self.read_stat(state, METHOD_LEN, "len")
    }

    pub fn allocated(&self, state: &PodState) -> Result<u64> {
        self.read_stat(state, METHOD_ALLOCATED, "allocated")
    }

    pub fn capacity(&self, state: &PodState) -> Result<u64> {
        self.read_stat(state, METHOD_CAPACITY, "capacity")
    }

    pub fn snzi_arrive<'image, 'state>(
        &'image self,
        state: &'state PodState,
        leaf: usize,
    ) -> Result<SnziToken<'image, 'state>> {
        self.ensure_pair(state)?;
        if leaf >= self.snzi_leaf_count {
            return Err(Error::message(format!(
                "SNZI leaf {leaf} is outside 0..{}",
                self.snzi_leaf_count
            )));
        }
        let function: SnziArriveFn = unsafe { mem::transmute(self.entry(METHOD_SNZI_ARRIVE)) };
        let mut raw = INVALID_U64;
        let status = unsafe { function(state.payload_ptr(), leaf as u64, &mut raw) };
        snzi_status_result("arrive", status)?;
        validate_snzi_token(raw, leaf)?;
        Ok(SnziToken {
            raw,
            image: self,
            state,
        })
    }

    pub fn snzi_query(&self, state: &PodState) -> Result<bool> {
        self.read_snzi_bool(state, METHOD_SNZI_QUERY, "query")
    }

    pub fn snzi_is_quiescent(&self, state: &PodState) -> Result<bool> {
        self.read_snzi_bool(state, METHOD_SNZI_QUIESCENT, "quiescent")
    }

    pub fn verify_runtime_permissions(&self, state: &PodState) -> Result<()> {
        self.ensure_pair(state)?;
        verify_permissions(self.code.data_address(), true, false, true, true)?;
        verify_permissions(self.code.guard_before(), false, false, false, false)?;
        verify_permissions(self.code.guard_after(), false, false, false, false)?;
        verify_permissions(state.map.data_address(), true, true, false, true)?;
        verify_permissions(state.map.guard_before(), false, false, false, false)?;
        verify_permissions(state.map.guard_after(), false, false, false, false)?;
        Ok(())
    }

    fn map_state(&self, fd: OwnedFd, requested_address: Option<usize>) -> Result<PodState> {
        let address = if self.header.flags & FLAG_REQUIRES_SAME_VA != 0 {
            let required = self.header.required_state_address as usize;
            if let Some(requested) = requested_address {
                if requested != required {
                    return Err(Error::message(format!(
                        "fixed-address pod requires state at 0x{required:x}, not 0x{requested:x}"
                    )));
                }
            }
            Some(required)
        } else {
            requested_address
        };
        let map = GuardedMapping::map(
            fd.as_raw_fd(),
            self.header.state_file_len as usize,
            libc::PROT_READ | libc::PROT_WRITE,
            address,
        )?;
        let payload = map
            .data_address()
            .checked_add(self.header.payload_offset as usize)
            .ok_or_else(|| Error::message("payload address overflow"))?;
        if payload % self.layout_align as usize != 0 {
            return Err(Error::message(
                "mapped payload does not satisfy pod alignment",
            ));
        }
        Ok(PodState {
            fd,
            map,
            artifact_digest: self.artifact_digest,
            layout_hash: self.layout_hash,
            payload_len: self.header.payload_len,
            payload_offset: self.header.payload_offset as usize,
        })
    }

    fn initialize_state(&self, state: &mut PodState) -> Result<()> {
        let status = state.atomic(ENVELOPE_STATUS_OFFSET);
        status
            .compare_exchange(
                STATE_STATUS_EMPTY,
                STATE_STATUS_INITIALIZING,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .map_err(|found| {
                Error::message(format!(
                    "state initialization requires EMPTY, found lifecycle state {found}"
                ))
            })?;

        let result = (|| {
            state.write_bytes(ENVELOPE_MAGIC_OFFSET, &STATE_MAGIC);
            state.write_u32(ENVELOPE_VERSION_OFFSET, STATE_VERSION);
            state.write_u32(ENVELOPE_FAILURE_OFFSET, 0);
            state.write_bytes(ENVELOPE_CODE_HASH_OFFSET, &self.header.code_sha256);
            state.write_u64(ENVELOPE_LAYOUT_HASH_OFFSET, self.layout_hash);
            state.write_u64(ENVELOPE_LAYOUT_SIZE_OFFSET, self.layout_size);
            state.write_u64(ENVELOPE_LAYOUT_ALIGN_OFFSET, self.layout_align);
            state.write_u64(ENVELOPE_PAYLOAD_LEN_OFFSET, self.header.payload_len);
            state.write_u64(ENVELOPE_GENERATION_OFFSET, 1);
            state.write_u64(ENVELOPE_OWNER_PID_OFFSET, unsafe { libc::getpid() as u64 });
            state.write_bytes(ENVELOPE_ARTIFACT_HASH_OFFSET, &self.artifact_digest);
            state.write_u64(ENVELOPE_FLAGS_OFFSET, self.header.flags);
            state.write_u64(
                ENVELOPE_REQUIRED_ADDRESS_OFFSET,
                self.header.required_state_address,
            );

            let function: StateLenFn = unsafe { mem::transmute(self.entry(METHOD_INIT)) };
            let init_status = unsafe { function(state.payload_ptr(), state.payload_len) };
            status_result("init", init_status)?;
            let validate: StateLenFn = unsafe { mem::transmute(self.entry(METHOD_VALIDATE)) };
            let validate_status = unsafe { validate(state.payload_ptr(), state.payload_len) };
            status_result("post-init validation", validate_status)
        })();

        match result {
            Ok(()) => {
                status.store(STATE_STATUS_READY, Ordering::Release);
                Ok(())
            }
            Err(error) => {
                state.write_u32(ENVELOPE_FAILURE_OFFSET, 1);
                status.store(STATE_STATUS_POISONED, Ordering::Release);
                Err(error)
            }
        }
    }

    fn validate_envelope(&self, state: &PodState) -> Result<()> {
        match state.atomic(ENVELOPE_STATUS_OFFSET).load(Ordering::Acquire) {
            STATE_STATUS_READY => {}
            STATE_STATUS_EMPTY => return Err(Error::message("state is not initialized")),
            STATE_STATUS_INITIALIZING => {
                return Err(Error::message(
                    "state initializer has not published READY; refusing recovery",
                ));
            }
            STATE_STATUS_POISONED => {
                return Err(Error::message("state initialization is poisoned"));
            }
            status => {
                return Err(Error::message(format!(
                    "state has unknown lifecycle value {status}"
                )));
            }
        }
        if state.read_bytes::<8>(ENVELOPE_MAGIC_OFFSET) != STATE_MAGIC
            || state.read_u32(ENVELOPE_VERSION_OFFSET) != STATE_VERSION
            || state.read_u32(ENVELOPE_FAILURE_OFFSET) != 0
            || state.read_bytes::<32>(ENVELOPE_CODE_HASH_OFFSET) != self.header.code_sha256
            || state.read_u64(ENVELOPE_LAYOUT_HASH_OFFSET) != self.layout_hash
            || state.read_u64(ENVELOPE_LAYOUT_SIZE_OFFSET) != self.layout_size
            || state.read_u64(ENVELOPE_LAYOUT_ALIGN_OFFSET) != self.layout_align
            || state.read_u64(ENVELOPE_PAYLOAD_LEN_OFFSET) != self.header.payload_len
            || state.read_u64(ENVELOPE_GENERATION_OFFSET) == 0
            || state.read_u64(ENVELOPE_OWNER_PID_OFFSET) == 0
            || state.read_bytes::<32>(ENVELOPE_ARTIFACT_HASH_OFFSET) != self.artifact_digest
            || state.read_u64(ENVELOPE_FLAGS_OFFSET) != self.header.flags
            || state.read_u64(ENVELOPE_REQUIRED_ADDRESS_OFFSET)
                != self.header.required_state_address
        {
            return Err(Error::message(
                "state envelope does not match the authenticated pod",
            ));
        }
        if self.header.flags & FLAG_REQUIRES_SAME_VA != 0
            && state.map.data_address() != self.header.required_state_address as usize
        {
            return Err(Error::message(
                "state is not mapped at its required address",
            ));
        }
        Ok(())
    }

    fn ensure_pair(&self, state: &PodState) -> Result<()> {
        if state.artifact_digest != self.artifact_digest
            || state.layout_hash != self.layout_hash
            || state.payload_len != self.header.payload_len
        {
            return Err(Error::message("pod code and state do not belong together"));
        }
        if state.atomic(ENVELOPE_STATUS_OFFSET).load(Ordering::Acquire) != STATE_STATUS_READY {
            return Err(Error::message("pod state is not READY"));
        }
        Ok(())
    }

    fn entry(&self, method: usize) -> *const u8 {
        let entry = &self.header.methods[method];
        let relative = (entry.offset - self.header.code_offset) as usize;
        unsafe { self.code.data.as_ptr().add(relative).cast_const() }
    }

    fn read_stat(&self, state: &PodState, method: usize, name: &str) -> Result<u64> {
        self.ensure_pair(state)?;
        let function: StateU64Fn = unsafe { mem::transmute(self.entry(method)) };
        let value = unsafe { function(state.payload_ptr()) };
        if value == INVALID_U64 {
            Err(Error::message(format!("pod {name} method rejected state")))
        } else {
            Ok(value)
        }
    }

    fn read_snzi_bool(&self, state: &PodState, method: usize, name: &str) -> Result<bool> {
        self.ensure_pair(state)?;
        let function: StateU64Fn = unsafe { mem::transmute(self.entry(method)) };
        match unsafe { function(state.payload_ptr()) } {
            0 => Ok(false),
            1 => Ok(true),
            value => Err(Error::message(format!(
                "pod SNZI {name} returned invalid boolean value {value}"
            ))),
        }
    }
}

/// Linear evidence for one successful SNZI arrival.
///
/// The token is bound to the exact code image and state mapping that issued it,
/// and [`SnziToken::depart`] consumes it. Dropping a token without departing is
/// an explicit arrival leak; departure is not attempted from `Drop` because a
/// native call can fail and `Drop` cannot report that failure.
///
/// A caller cannot substitute another image or state at departure:
///
/// ```compile_fail
/// use shmem_pod_runtime::{PodImage, PodState, SnziToken};
///
/// fn wrong_issuer(
///     other_image: &PodImage,
///     other_state: &PodState,
///     token: SnziToken<'_, '_>,
/// ) {
///     other_image.snzi_depart(other_state, token);
/// }
/// ```
#[must_use = "a successful SNZI arrival must eventually be departed"]
pub struct SnziToken<'image, 'state> {
    raw: u64,
    image: &'image PodImage,
    state: &'state PodState,
}

impl SnziToken<'_, '_> {
    pub fn depart(self) -> Result<()> {
        let function: SnziDepartFn =
            unsafe { mem::transmute(self.image.entry(METHOD_SNZI_DEPART)) };
        let status = unsafe { function(self.state.payload_ptr(), self.raw) };
        snzi_status_result("depart", status)
    }
}

pub struct PodState {
    fd: OwnedFd,
    map: GuardedMapping,
    artifact_digest: [u8; 32],
    layout_hash: u64,
    payload_len: u64,
    payload_offset: usize,
}

// Concurrent access is permitted only through authenticated pod methods and envelope atomics.
unsafe impl Send for PodState {}
unsafe impl Sync for PodState {}

impl PodState {
    pub fn state_address(&self) -> usize {
        self.map.data_address()
    }

    pub fn payload_address(&self) -> usize {
        self.payload_ptr() as usize
    }

    pub fn state_fd(&self) -> RawFd {
        self.fd.as_raw_fd()
    }

    pub fn duplicate_fd_for_exec(&self) -> Result<OwnedFd> {
        duplicate_inheritable(self.fd.as_raw_fd())
    }

    pub fn ready_count(&self) -> u32 {
        self.atomic(ENVELOPE_READY_COUNT_OFFSET)
            .load(Ordering::Acquire)
    }

    pub fn announce_ready(&self) -> u32 {
        self.atomic(ENVELOPE_READY_COUNT_OFFSET)
            .fetch_add(1, Ordering::AcqRel)
            + 1
    }

    pub fn start(&self) {
        self.atomic(ENVELOPE_START_FLAG_OFFSET)
            .store(1, Ordering::Release);
    }

    pub fn wait_for_start(&self, timeout: Duration) -> Result<()> {
        let start = Instant::now();
        while self
            .atomic(ENVELOPE_START_FLAG_OFFSET)
            .load(Ordering::Acquire)
            == 0
        {
            if start.elapsed() >= timeout {
                return Err(Error::message("timed out waiting for shared start flag"));
            }
            std::hint::spin_loop();
            if start.elapsed().as_millis() % 8 == 0 {
                std::thread::yield_now();
            }
        }
        Ok(())
    }

    pub fn lifecycle_status(&self) -> u32 {
        self.atomic(ENVELOPE_STATUS_OFFSET).load(Ordering::Acquire)
    }

    fn payload_ptr(&self) -> *mut u8 {
        unsafe { self.map.data.as_ptr().add(self.payload_offset) }
    }

    fn atomic(&self, offset: usize) -> &AtomicU32 {
        assert_eq!(offset % mem::align_of::<AtomicU32>(), 0);
        unsafe { &*self.map.data.as_ptr().add(offset).cast::<AtomicU32>() }
    }

    fn write_bytes(&self, offset: usize, value: &[u8]) {
        assert!(offset + value.len() <= STATE_ENVELOPE_SIZE);
        unsafe {
            ptr::copy_nonoverlapping(
                value.as_ptr(),
                self.map.data.as_ptr().add(offset),
                value.len(),
            );
        }
    }

    fn read_bytes<const N: usize>(&self, offset: usize) -> [u8; N] {
        assert!(offset + N <= STATE_ENVELOPE_SIZE);
        let mut value = [0_u8; N];
        unsafe {
            ptr::copy_nonoverlapping(self.map.data.as_ptr().add(offset), value.as_mut_ptr(), N);
        }
        value
    }

    fn write_u32(&self, offset: usize, value: u32) {
        self.write_bytes(offset, &value.to_le_bytes());
    }

    fn read_u32(&self, offset: usize) -> u32 {
        u32::from_le_bytes(self.read_bytes(offset))
    }

    fn write_u64(&self, offset: usize, value: u64) {
        self.write_bytes(offset, &value.to_le_bytes());
    }

    fn read_u64(&self, offset: usize) -> u64 {
        u64::from_le_bytes(self.read_bytes(offset))
    }
}

struct GuardedMapping {
    reservation: NonNull<u8>,
    reservation_len: usize,
    data: NonNull<u8>,
    len: usize,
}

unsafe impl Send for GuardedMapping {}
unsafe impl Sync for GuardedMapping {}

impl GuardedMapping {
    fn map(fd: RawFd, len: usize, protection: i32, address: Option<usize>) -> Result<Self> {
        if len == 0 || len % PAGE_SIZE != 0 {
            return Err(Error::message("mapping length is not page aligned"));
        }
        let reservation_len = len
            .checked_add(PAGE_SIZE * 2)
            .ok_or_else(|| Error::message("guarded mapping length overflow"))?;
        let (hint, flags) = if let Some(data_address) = address {
            if data_address % PAGE_SIZE != 0 || data_address < PAGE_SIZE {
                return Err(Error::message(
                    "requested mapping address is not page aligned",
                ));
            }
            (
                (data_address - PAGE_SIZE) as *mut libc::c_void,
                libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_FIXED_NOREPLACE,
            )
        } else {
            (ptr::null_mut(), libc::MAP_PRIVATE | libc::MAP_ANONYMOUS)
        };
        let reservation =
            unsafe { libc::mmap(hint, reservation_len, libc::PROT_NONE, flags, -1, 0) };
        if reservation == libc::MAP_FAILED {
            return Err(last_os("reserve guarded address range"));
        }
        if !hint.is_null() && reservation != hint {
            unsafe {
                libc::munmap(reservation, reservation_len);
            }
            return Err(Error::message("kernel did not honor fixed mapping address"));
        }
        let data = unsafe { reservation.cast::<u8>().add(PAGE_SIZE) };
        let mapped = unsafe {
            libc::mmap(
                data.cast(),
                len,
                protection,
                libc::MAP_SHARED | libc::MAP_FIXED,
                fd,
                0,
            )
        };
        if mapped == libc::MAP_FAILED {
            let error = last_os("map guarded memfd");
            unsafe {
                libc::munmap(reservation, reservation_len);
            }
            return Err(error);
        }
        if mapped != data.cast() {
            unsafe {
                libc::munmap(reservation, reservation_len);
            }
            return Err(Error::message(
                "kernel mapped memfd at an unexpected address",
            ));
        }
        Ok(Self {
            reservation: NonNull::new(reservation.cast()).expect("mmap never returns null"),
            reservation_len,
            data: NonNull::new(data).expect("guarded data address is nonnull"),
            len,
        })
    }

    fn data_address(&self) -> usize {
        self.data.as_ptr() as usize
    }

    fn guard_before(&self) -> usize {
        self.reservation.as_ptr() as usize
    }

    fn guard_after(&self) -> usize {
        self.data_address() + self.len
    }
}

impl Drop for GuardedMapping {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.reservation.as_ptr().cast(), self.reservation_len);
        }
    }
}

unsafe fn call_layout(header: &ImageHeader, code: &GuardedMapping, method: usize) -> u64 {
    let relative = (header.methods[method].offset - header.code_offset) as usize;
    let entry = unsafe { code.data.as_ptr().add(relative).cast_const() };
    let function: LayoutFn = unsafe { mem::transmute(entry) };
    unsafe { function() }
}

fn create_code_fd(code: &[u8]) -> Result<OwnedFd> {
    let mapped_len = page_round(code.len())?;
    let fd = memfd(
        "shmem-pod-code",
        libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_EXEC,
    )?;
    resize(fd.as_raw_fd(), mapped_len)?;
    write_all_at(fd.as_raw_fd(), code, 0)?;
    add_seals(
        fd.as_raw_fd(),
        libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
    )?;
    verify_code_fd(fd.as_raw_fd(), code, mapped_len)?;
    Ok(fd)
}

fn verify_code_fd(fd: RawFd, code: &[u8], mapped_len: usize) -> Result<()> {
    verify_fd_len(fd, mapped_len)?;
    require_seals(
        fd,
        libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
        "code",
    )?;
    let mut bytes = vec![0_u8; mapped_len];
    read_exact_at(fd, &mut bytes, 0)?;
    if &bytes[..code.len()] != code {
        return Err(Error::message(
            "sealed code memfd differs from authenticated artifact",
        ));
    }
    if bytes[code.len()..].iter().any(|byte| *byte != 0) {
        return Err(Error::message("executable page padding is not zero"));
    }
    Ok(())
}

fn create_state_fd(len: usize) -> Result<OwnedFd> {
    if len != DEFAULT_STATE_FILE_LEN || len % PAGE_SIZE != 0 {
        return Err(Error::message("invalid state memfd length"));
    }
    let fd = memfd(
        "shmem-pod-state",
        libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_NOEXEC_SEAL,
    )?;
    resize(fd.as_raw_fd(), len)?;
    add_seals(
        fd.as_raw_fd(),
        libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
    )?;
    verify_state_fd(fd.as_raw_fd(), len)?;
    Ok(fd)
}

fn verify_state_fd(fd: RawFd, len: usize) -> Result<()> {
    verify_fd_len(fd, len)?;
    require_seals(
        fd,
        libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL | F_SEAL_EXEC,
        "state",
    )
}

fn memfd(name: &str, flags: libc::c_uint) -> Result<OwnedFd> {
    let name = CString::new(name).expect("static memfd name has no NUL");
    let fd = unsafe { libc::memfd_create(name.as_ptr(), flags) };
    if fd < 0 {
        return Err(last_os("memfd_create"));
    }
    Ok(unsafe { OwnedFd::from_raw_fd(fd) })
}

fn resize(fd: RawFd, len: usize) -> Result<()> {
    if unsafe { libc::ftruncate(fd, len as libc::off_t) } != 0 {
        return Err(last_os("ftruncate memfd"));
    }
    Ok(())
}

fn add_seals(fd: RawFd, seals: libc::c_int) -> Result<()> {
    if unsafe { libc::fcntl(fd, libc::F_ADD_SEALS, seals) } != 0 {
        return Err(last_os("F_ADD_SEALS"));
    }
    Ok(())
}

fn require_seals(fd: RawFd, required: libc::c_int, label: &str) -> Result<()> {
    let actual = unsafe { libc::fcntl(fd, libc::F_GET_SEALS) };
    if actual < 0 {
        return Err(last_os("F_GET_SEALS"));
    }
    if actual & required != required {
        return Err(Error::message(format!(
            "{label} memfd lacks required seals: required=0x{required:x}, actual=0x{actual:x}"
        )));
    }
    Ok(())
}

fn verify_fd_len(fd: RawFd, expected: usize) -> Result<()> {
    let mut stat = mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, stat.as_mut_ptr()) } != 0 {
        return Err(last_os("fstat memfd"));
    }
    let stat = unsafe { stat.assume_init() };
    if stat.st_size != expected as libc::off_t {
        return Err(Error::message(format!(
            "memfd length mismatch: expected {expected}, got {}",
            stat.st_size
        )));
    }
    Ok(())
}

fn write_all_at(fd: RawFd, mut bytes: &[u8], mut offset: usize) -> Result<()> {
    while !bytes.is_empty() {
        let written = unsafe {
            libc::pwrite(
                fd,
                bytes.as_ptr().cast(),
                bytes.len(),
                offset as libc::off_t,
            )
        };
        if written < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(Error::from(error));
        }
        if written == 0 {
            return Err(Error::message("pwrite made no progress"));
        }
        bytes = &bytes[written as usize..];
        offset += written as usize;
    }
    Ok(())
}

fn read_exact_at(fd: RawFd, mut bytes: &mut [u8], mut offset: usize) -> Result<()> {
    while !bytes.is_empty() {
        let read = unsafe {
            libc::pread(
                fd,
                bytes.as_mut_ptr().cast(),
                bytes.len(),
                offset as libc::off_t,
            )
        };
        if read < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(Error::from(error));
        }
        if read == 0 {
            return Err(Error::message("unexpected EOF reading memfd"));
        }
        let read = read as usize;
        bytes = &mut bytes[read..];
        offset += read;
    }
    Ok(())
}

fn duplicate_inheritable(fd: RawFd) -> Result<OwnedFd> {
    let duplicate = unsafe { libc::fcntl(fd, libc::F_DUPFD, 3) };
    if duplicate < 0 {
        return Err(last_os("F_DUPFD"));
    }
    Ok(unsafe { OwnedFd::from_raw_fd(duplicate) })
}

fn page_round(len: usize) -> Result<usize> {
    len.checked_add(PAGE_SIZE - 1)
        .map(|value| value & !(PAGE_SIZE - 1))
        .ok_or_else(|| Error::message("page rounding overflow"))
}

fn verify_permissions(
    address: usize,
    readable: bool,
    writable: bool,
    executable: bool,
    shared: bool,
) -> Result<()> {
    let maps = fs::read_to_string("/proc/self/maps").map_err(Error::from)?;
    let permissions = permission_at(&maps, address)
        .ok_or_else(|| Error::message(format!("address 0x{address:x} is absent from maps")))?;
    let bytes = permissions.as_bytes();
    if bytes.len() < 4
        || (bytes[0] == b'r') != readable
        || (bytes[1] == b'w') != writable
        || (bytes[2] == b'x') != executable
        || (bytes[3] == b's') != shared
    {
        return Err(Error::message(format!(
            "mapping at 0x{address:x} has permissions {permissions}, expected read={readable} write={writable} exec={executable} shared={shared}"
        )));
    }
    Ok(())
}

fn permission_at(maps: &str, address: usize) -> Option<&str> {
    for line in maps.lines() {
        let mut fields = line.split_whitespace();
        let range = fields.next()?;
        let permissions = fields.next()?;
        let (start, end) = range.split_once('-')?;
        let start = usize::from_str_radix(start, 16).ok()?;
        let end = usize::from_str_radix(end, 16).ok()?;
        if start <= address && address < end {
            return Some(permissions);
        }
    }
    None
}

fn status_result(operation: &str, status: i32) -> Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err(status_error(operation, status))
    }
}

fn status_error(operation: &str, status: i32) -> Error {
    Error::message(format!("pod {operation} returned status {status}"))
}

fn snzi_status_result(operation: &str, status: i32) -> Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err(snzi_status_error(operation, status))
    }
}

fn snzi_status_error(operation: &str, status: i32) -> Error {
    let meaning = match status {
        -1 => "null or misaligned state",
        -2 => "state layout mismatch",
        -5 => "invalid output argument",
        STATUS_SNZI_INVALID_LEAF => "invalid leaf",
        STATUS_SNZI_MALFORMED_TOKEN => "malformed raw token",
        STATUS_SNZI_GENERATION_MISMATCH => "stale token generation",
        STATUS_SNZI_INACTIVE_TOKEN => "inactive or already departed token",
        STATUS_SNZI_POISONED => "poisoned indicator",
        _ => "unknown raw status",
    };
    Error::message(format!(
        "pod SNZI {operation} returned status {status} ({meaning})"
    ))
}

fn validate_snzi_token(raw: u64, expected_leaf: usize) -> Result<()> {
    let malformed = raw == INVALID_U64
        || raw & SNZI_TOKEN_RESERVED_BIT != 0
        || raw >> SNZI_TOKEN_GENERATION_SHIFT == 0
        || raw & SNZI_TOKEN_LEAF_MASK != expected_leaf as u64;
    if malformed {
        Err(Error::message(format!(
            "pod SNZI arrive returned malformed raw token 0x{raw:016x}"
        )))
    } else {
        Ok(())
    }
}

fn last_os(operation: &str) -> Error {
    Error::message(format!("{operation}: {}", io::Error::last_os_error()))
}

pub fn parse_sha256(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64 {
        return Err(Error::message("SHA-256 must contain exactly 64 hex digits"));
    }
    let mut digest = [0_u8; 32];
    for (index, output) in digest.iter_mut().enumerate() {
        let offset = index * 2;
        *output = u8::from_str_radix(&value[offset..offset + 2], 16)
            .map_err(|_| Error::message("SHA-256 contains a non-hex digit"))?;
    }
    Ok(digest)
}

pub fn hex(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use fmt::Write;
        write!(output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use shmem_pod_image_api::{FLAG_OFFSET_ARENA, METHOD_SPECS, MethodEntry};

    fn test_artifact() -> (Vec<u8>, [u8; 32]) {
        let code = vec![0xcc_u8; 1024];
        let code_hash: [u8; 32] = Sha256::digest(&code).into();
        let mut header = ImageHeader::new(code.len(), code_hash).unwrap();
        header.flags = FLAG_OFFSET_ARENA;
        for (index, method) in header.methods.iter_mut().enumerate() {
            *method = MethodEntry {
                offset: (HEADER_SIZE + index * 32) as u64,
                size: 16,
                signature: METHOD_SPECS[index].1 as u16,
                reserved: 0,
            };
        }
        let mut image = header.encode().unwrap().to_vec();
        image.extend_from_slice(&code);
        let digest = Sha256::digest(&image).into();
        (image, digest)
    }

    #[test]
    fn parses_sha256_strictly() {
        let value = "0123456789abcdef".repeat(4);
        assert_eq!(hex(&parse_sha256(&value).unwrap()), value);
        assert!(parse_sha256("abc").is_err());
        assert!(parse_sha256(&"z".repeat(64)).is_err());
    }

    #[test]
    fn validates_snzi_raw_tokens_and_statuses() {
        let token = (7_u64 << SNZI_TOKEN_GENERATION_SHIFT) | 3;
        validate_snzi_token(token, 3).unwrap();
        assert!(validate_snzi_token(token, 2).is_err());
        assert!(validate_snzi_token(3, 3).is_err());
        assert!(validate_snzi_token(token | SNZI_TOKEN_RESERVED_BIT, 3).is_err());
        assert!(validate_snzi_token(INVALID_U64, 3).is_err());

        for (status, text) in [
            (STATUS_SNZI_INVALID_LEAF, "invalid leaf"),
            (STATUS_SNZI_MALFORMED_TOKEN, "malformed raw token"),
            (STATUS_SNZI_GENERATION_MISMATCH, "stale token generation"),
            (STATUS_SNZI_INACTIVE_TOKEN, "already departed token"),
            (STATUS_SNZI_POISONED, "poisoned indicator"),
            (-99, "unknown raw status"),
        ] {
            assert!(snzi_status_error("test", status).to_string().contains(text));
        }
    }

    #[test]
    fn snzi_token_departure_is_consuming_and_drop_is_not_raii() {
        let _: fn(SnziToken<'static, 'static>) -> Result<()> = SnziToken::depart;
        assert!(!mem::needs_drop::<SnziToken<'static, 'static>>());
    }

    #[test]
    fn finds_mapping_permissions() {
        let maps = "1000-2000 r-xs 00000000 00:01 1 /memfd:code\n3000-4000 ---p 00000000 00:00 0\n";
        assert_eq!(permission_at(maps, 0x1234), Some("r-xs"));
        assert_eq!(permission_at(maps, 0x3000), Some("---p"));
        assert_eq!(permission_at(maps, 0x4000), None);
    }

    #[test]
    fn authenticates_the_complete_artifact_before_parsing() {
        let (image, digest) = test_artifact();
        assert!(PodArtifact::from_bytes(image.clone(), digest).is_ok());

        let mut wrong_digest = digest;
        wrong_digest[0] ^= 1;
        assert!(PodArtifact::from_bytes(image.clone(), wrong_digest).is_err());

        let mut trailing = image.clone();
        trailing.push(0);
        let trailing_digest = Sha256::digest(&trailing).into();
        assert!(PodArtifact::from_bytes(trailing, trailing_digest).is_err());

        let mut reserved = image.clone();
        reserved[HEADER_SIZE - 1] = 1;
        let reserved_digest = Sha256::digest(&reserved).into();
        assert!(PodArtifact::from_bytes(reserved, reserved_digest).is_err());

        let mut changed_code = image;
        changed_code[HEADER_SIZE + 1] ^= 1;
        let changed_digest = Sha256::digest(&changed_code).into();
        assert!(PodArtifact::from_bytes(changed_code, changed_digest).is_err());
    }

    #[test]
    fn rejects_an_unsealed_code_fd() {
        let fd = memfd(
            "shmem-pod-unsealed-test",
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING | MFD_EXEC,
        )
        .unwrap();
        resize(fd.as_raw_fd(), PAGE_SIZE).unwrap();
        assert!(
            require_seals(
                fd.as_raw_fd(),
                libc::F_SEAL_WRITE | libc::F_SEAL_GROW | libc::F_SEAL_SHRINK | libc::F_SEAL_SEAL,
                "test code",
            )
            .is_err()
        );
    }

    #[test]
    fn sealed_code_cannot_be_mapped_writable() {
        let fd = create_code_fd(&[0xc3]).unwrap();
        let mapping = unsafe {
            libc::mmap(
                ptr::null_mut(),
                PAGE_SIZE,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                fd.as_raw_fd(),
                0,
            )
        };
        assert_eq!(mapping, libc::MAP_FAILED);
    }

    #[test]
    fn state_fd_cannot_gain_executable_mode_bits() {
        let fd = create_state_fd(DEFAULT_STATE_FILE_LEN).unwrap();
        let mut stat = mem::MaybeUninit::<libc::stat>::uninit();
        assert_eq!(unsafe { libc::fstat(fd.as_raw_fd(), stat.as_mut_ptr()) }, 0);
        let stat = unsafe { stat.assume_init() };
        assert_eq!(stat.st_mode & 0o111, 0);
        assert_eq!(
            unsafe { libc::fchmod(fd.as_raw_fd(), stat.st_mode | 0o100) },
            -1
        );
    }

    #[test]
    fn guarded_state_mapping_has_nx_data_and_inaccessible_guards() {
        let fd = create_state_fd(DEFAULT_STATE_FILE_LEN).unwrap();
        let mapping = GuardedMapping::map(
            fd.as_raw_fd(),
            DEFAULT_STATE_FILE_LEN,
            libc::PROT_READ | libc::PROT_WRITE,
            None,
        )
        .unwrap();
        verify_permissions(mapping.data_address(), true, true, false, true).unwrap();
        verify_permissions(mapping.guard_before(), false, false, false, false).unwrap();
        verify_permissions(mapping.guard_after(), false, false, false, false).unwrap();
    }
}
