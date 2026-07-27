use pod_api::{
    ABI_VERSION, IMAGE_MAGIC, METHOD_ATOMIC_ADD, METHOD_COARSE_ADD, METHOD_COUNT, METHOD_FINE_ADD,
    METHOD_REGISTER, PodAddFn, PodImageHeader, PodMode, PodRegisterFn, PodState, STATUS_OK,
    TARGET_ARCH_X86_64,
};
use std::error::Error;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileExt, OpenOptionsExt};
use std::path::Path;
use std::ptr::NonNull;

#[derive(Debug)]
pub enum PodError {
    Io(io::Error),
    InvalidImage(String),
    Call { method: &'static str, status: i32 },
}

impl PodError {
    pub fn status(&self) -> Option<i32> {
        match self {
            Self::Call { status, .. } => Some(*status),
            _ => None,
        }
    }
}

impl fmt::Display for PodError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "{error}"),
            Self::InvalidImage(message) => write!(formatter, "invalid pod image: {message}"),
            Self::Call { method, status } => {
                write!(formatter, "pod method {method} returned status {status}")
            }
        }
    }
}

impl Error for PodError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            _ => None,
        }
    }
}

impl From<io::Error> for PodError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub type Result<T> = std::result::Result<T, PodError>;

pub fn create_instance(image_path: &Path, instance_path: &Path) -> Result<PodImageHeader> {
    let image = fs::read(image_path)?;
    let header = parse_header_bytes(&image)?;
    if header.image_len as usize != image.len() {
        return Err(PodError::InvalidImage(format!(
            "compiler image length {} does not match file length {}",
            header.image_len,
            image.len()
        )));
    }

    if let Some(parent) = instance_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let instance = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(instance_path)?;
    let file_len = header
        .state_offset
        .checked_add(header.state_len)
        .ok_or_else(|| PodError::InvalidImage("file length overflow".into()))?;
    instance.set_len(file_len)?;
    instance.write_all_at(&image, 0)?;

    let state_mapping = map_region(
        &instance,
        header.state_len as usize,
        libc::PROT_READ | libc::PROT_WRITE,
        header.state_offset,
        None,
        Region::State,
    )?;
    unsafe {
        state_mapping
            .cast::<PodState>()
            .as_ptr()
            .write(PodState::new());
        if libc::msync(
            state_mapping.as_ptr(),
            header.state_len as usize,
            libc::MS_SYNC,
        ) != 0
        {
            let error = io::Error::last_os_error();
            libc::munmap(state_mapping.as_ptr(), header.state_len as usize);
            return Err(error.into());
        }
        if libc::munmap(state_mapping.as_ptr(), header.state_len as usize) != 0 {
            return Err(io::Error::last_os_error().into());
        }
    }
    instance.sync_all()?;
    Ok(header)
}

pub struct MappedPod {
    header: PodImageHeader,
    code: NonNull<libc::c_void>,
    code_len: usize,
    state: NonNull<PodState>,
    state_len: usize,
}

// Moving or sharing the mappings is valid. Executing their untrusted bytes remains unsafe.
unsafe impl Send for MappedPod {}
unsafe impl Sync for MappedPod {}

impl MappedPod {
    pub fn open(path: &Path) -> Result<Self> {
        Self::open_with_address_tag(path, None)
    }

    pub fn open_with_address_tag(path: &Path, address_tag: Option<u64>) -> Result<Self> {
        let code_file = OpenOptions::new().read(true).open(path)?;
        let data_file = OpenOptions::new().read(true).write(true).open(path)?;
        let header = read_header(&code_file)?;
        validate_header(&header, data_file.metadata()?.len())?;

        let code_len = header.state_offset as usize;
        let code = map_region(
            &code_file,
            code_len,
            libc::PROT_READ | libc::PROT_EXEC,
            0,
            address_tag,
            Region::Code,
        )?;
        let state_mapping = match map_region(
            &data_file,
            header.state_len as usize,
            libc::PROT_READ | libc::PROT_WRITE,
            header.state_offset,
            address_tag,
            Region::State,
        ) {
            Ok(mapping) => mapping,
            Err(error) => {
                unsafe {
                    libc::munmap(code.as_ptr(), code_len);
                }
                return Err(error);
            }
        };
        let state = state_mapping.cast::<PodState>();
        if !unsafe { state.as_ref() }.is_compatible() {
            unsafe {
                libc::munmap(code.as_ptr(), code_len);
                libc::munmap(state_mapping.as_ptr(), header.state_len as usize);
            }
            return Err(PodError::InvalidImage(
                "shared state header is incompatible".into(),
            ));
        }

        Ok(Self {
            header,
            code,
            code_len,
            state,
            state_len: header.state_len as usize,
        })
    }

    pub fn header(&self) -> &PodImageHeader {
        &self.header
    }

    pub fn state(&self) -> &PodState {
        unsafe { self.state.as_ref() }
    }

    pub fn code_base(&self) -> usize {
        self.code.as_ptr() as usize
    }

    pub fn state_base(&self) -> usize {
        self.state.as_ptr() as usize
    }

    /// Invokes the process-registration entry in the mapped image.
    ///
    /// # Safety
    ///
    /// The mapped bytes must be a trusted artifact implementing the exact
    /// `PodRegisterFn` ABI and respecting all `PodState` memory invariants.
    pub unsafe fn register(&self, pid: u64, mode: PodMode) -> Result<()> {
        let function: PodRegisterFn = unsafe { std::mem::transmute(self.entry(METHOD_REGISTER)) };
        let status = unsafe {
            function(
                self.state.as_ptr(),
                pid,
                self.code_base() as u64,
                self.state_base() as u64,
                mode as u32,
            )
        };
        check_status("register", status)
    }

    /// Invokes one counter entry in the mapped image.
    ///
    /// # Safety
    ///
    /// The mapped bytes must be a trusted artifact implementing the exact
    /// `PodAddFn` ABI and respecting all `PodState` memory invariants.
    pub unsafe fn add(&self, mode: PodMode, index: u32, delta: u64) -> Result<()> {
        let (method, name) = match mode {
            PodMode::Coarse => (METHOD_COARSE_ADD, "coarse_add"),
            PodMode::Fine => (METHOD_FINE_ADD, "fine_add"),
            PodMode::Atomic => (METHOD_ATOMIC_ADD, "atomic_add"),
        };
        let function: PodAddFn = unsafe { std::mem::transmute(self.entry(method)) };
        let status = unsafe { function(self.state.as_ptr(), index, delta) };
        check_status(name, status)
    }

    pub fn mapping_permissions(&self) -> io::Result<(String, String)> {
        Ok((
            permissions_for_address(self.code_base())?,
            permissions_for_address(self.state_base())?,
        ))
    }

    fn entry(&self, method: usize) -> *const u8 {
        let offset = self.header.methods[method].offset as usize;
        unsafe { self.code.as_ptr().cast::<u8>().add(offset) }
    }
}

impl Drop for MappedPod {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.code.as_ptr(), self.code_len);
            libc::munmap(self.state.as_ptr().cast(), self.state_len);
        }
    }
}

fn check_status(method: &'static str, status: i32) -> Result<()> {
    if status == STATUS_OK {
        Ok(())
    } else {
        Err(PodError::Call { method, status })
    }
}

fn read_header(file: &File) -> Result<PodImageHeader> {
    let mut bytes = [0_u8; core::mem::size_of::<PodImageHeader>()];
    file.read_exact_at(&mut bytes, 0)?;
    parse_header_bytes(&bytes)
}

fn parse_header_bytes(bytes: &[u8]) -> Result<PodImageHeader> {
    if bytes.len() < core::mem::size_of::<PodImageHeader>() {
        return Err(PodError::InvalidImage("header is truncated".into()));
    }
    let header = unsafe { bytes.as_ptr().cast::<PodImageHeader>().read_unaligned() };
    validate_header_fields(&header)?;
    Ok(header)
}

fn validate_header(header: &PodImageHeader, file_len: u64) -> Result<()> {
    validate_header_fields(header)?;
    let required = header
        .state_offset
        .checked_add(header.state_len)
        .ok_or_else(|| PodError::InvalidImage("file extent overflow".into()))?;
    if file_len < required {
        return Err(PodError::InvalidImage(format!(
            "file is truncated: need {required} bytes, have {file_len}"
        )));
    }
    Ok(())
}

fn validate_header_fields(header: &PodImageHeader) -> Result<()> {
    if header.magic != IMAGE_MAGIC {
        return Err(PodError::InvalidImage("bad image magic".into()));
    }
    if header.abi_version != ABI_VERSION {
        return Err(PodError::InvalidImage(format!(
            "ABI version {} is unsupported",
            header.abi_version
        )));
    }
    if header.target_arch != TARGET_ARCH_X86_64 {
        return Err(PodError::InvalidImage(format!(
            "target architecture 0x{:x} is unsupported",
            header.target_arch
        )));
    }
    if header.method_count as usize != METHOD_COUNT
        || header.header_size as usize != core::mem::size_of::<PodImageHeader>()
    {
        return Err(PodError::InvalidImage(
            "method/header count mismatch".into(),
        ));
    }
    let host_page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if host_page_size <= 0 || header.page_size as libc::c_long != host_page_size {
        return Err(PodError::InvalidImage(format!(
            "image page size {} does not match host page size {host_page_size}",
            header.page_size
        )));
    }
    if header.image_len < header.header_size as u64 || header.image_len > header.state_offset {
        return Err(PodError::InvalidImage("invalid image/state extent".into()));
    }
    if header.state_offset % header.page_size as u64 != 0
        || header.state_len % header.page_size as u64 != 0
        || header.state_len < core::mem::size_of::<PodState>() as u64
    {
        return Err(PodError::InvalidImage(
            "state mapping is not page-aligned or is too small".into(),
        ));
    }

    for (index, method) in header.methods.iter().enumerate() {
        let end = method
            .offset
            .checked_add(method.size as u64)
            .ok_or_else(|| PodError::InvalidImage(format!("method {index} extent overflow")))?;
        if method.size == 0 || method.offset < header.header_size as u64 || end > header.image_len {
            return Err(PodError::InvalidImage(format!(
                "method {index} is outside the code image"
            )));
        }
    }
    for left in 0..header.methods.len() {
        let left_start = header.methods[left].offset;
        let left_end = left_start + header.methods[left].size as u64;
        for right in (left + 1)..header.methods.len() {
            let right_start = header.methods[right].offset;
            let right_end = right_start + header.methods[right].size as u64;
            if left_start < right_end && right_start < left_end {
                return Err(PodError::InvalidImage(format!(
                    "methods {left} and {right} overlap"
                )));
            }
        }
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum Region {
    Code,
    State,
}

fn map_region(
    file: &File,
    len: usize,
    protection: libc::c_int,
    offset: u64,
    address_tag: Option<u64>,
    region: Region,
) -> Result<NonNull<libc::c_void>> {
    if let Some(tag) = address_tag {
        let base = match region {
            Region::Code => 0x2000_0000_0000_u64,
            Region::State => 0x4000_0000_0000_u64,
        };
        let first_slot = tag.wrapping_mul(0x9e37_79b9) & 0xffff;
        for attempt in 0..64_u64 {
            let slot = (first_slot + attempt) & 0xffff;
            let address = (base + slot * 0x20_0000) as *mut libc::c_void;
            let mapping = unsafe {
                libc::mmap(
                    address,
                    len,
                    protection,
                    libc::MAP_SHARED | libc::MAP_FIXED_NOREPLACE,
                    file.as_raw_fd(),
                    offset as libc::off_t,
                )
            };
            if mapping != libc::MAP_FAILED {
                if mapping != address {
                    unsafe {
                        libc::munmap(mapping, len);
                    }
                    return Err(PodError::InvalidImage(
                        "kernel ignored the requested fixed mapping address".into(),
                    ));
                }
                return NonNull::new(mapping)
                    .ok_or_else(|| PodError::InvalidImage("mmap returned null".into()));
            }
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::EEXIST) {
                return Err(error.into());
            }
        }
        return Err(PodError::Io(io::Error::new(
            io::ErrorKind::AddrInUse,
            "could not reserve a distinct fixed pod mapping",
        )));
    }

    let mapping = unsafe {
        libc::mmap(
            std::ptr::null_mut(),
            len,
            protection,
            libc::MAP_SHARED,
            file.as_raw_fd(),
            offset as libc::off_t,
        )
    };
    if mapping == libc::MAP_FAILED {
        return Err(io::Error::last_os_error().into());
    }
    NonNull::new(mapping).ok_or_else(|| PodError::InvalidImage("mmap returned null".into()))
}

fn permissions_for_address(address: usize) -> io::Result<String> {
    let maps = fs::read_to_string("/proc/self/maps")?;
    for line in maps.lines() {
        let mut fields = line.split_whitespace();
        let Some(range) = fields.next() else {
            continue;
        };
        let Some(permissions) = fields.next() else {
            continue;
        };
        let Some((start, end)) = range.split_once('-') else {
            continue;
        };
        let Ok(start) = usize::from_str_radix(start, 16) else {
            continue;
        };
        let Ok(end) = usize::from_str_radix(end, 16) else {
            continue;
        };
        if (start..end).contains(&address) {
            return Ok(permissions.to_owned());
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!("address 0x{address:x} is absent from /proc/self/maps"),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_header() -> PodImageHeader {
        let mut header = PodImageHeader::empty();
        header.image_len = 256;
        header.state_offset = 4096;
        header.state_len = 36_864;
        for (index, method) in header.methods.iter_mut().enumerate() {
            method.offset = (128 + index * 16) as u64;
            method.size = 8;
        }
        header
    }

    #[test]
    fn rejects_wrong_abi_version() {
        let mut header = valid_header();
        header.abi_version += 1;
        let error = validate_header_fields(&header).unwrap_err();
        assert!(error.to_string().contains("ABI version"));
    }

    #[test]
    fn rejects_overlapping_methods() {
        let mut header = valid_header();
        header.methods[1].offset = header.methods[0].offset + 1;
        let error = validate_header_fields(&header).unwrap_err();
        assert!(error.to_string().contains("overlap"));
    }

    #[test]
    fn rejects_truncated_header() {
        let error = parse_header_bytes(&[0_u8; 16]).unwrap_err();
        assert!(error.to_string().contains("truncated"));
    }
}
