//! Attach a shared Talc arena at one exact address across independent execs.
//!
//! The parent creates a `memfd` and maps it at a chosen high address with
//! `MAP_FIXED_NOREPLACE`. The mapping contains a stable bootstrap header, the
//! `FixedRegionAllocator` control object, and the arena. After initialization,
//! the parent publishes readiness with release ordering and spawns fresh copies
//! of this executable while leaving the descriptor open across `exec`.
//!
//! Each child maps the descriptor at the recorded address, acquires readiness,
//! validates the bootstrap geometry and `LayoutDescriptor`, and only then calls
//! `FixedRegionAllocator::attach`. A collision at the required address is a hard
//! error; the example never replaces an existing mapping. Every allocator-aware
//! `Vec` header remains local to one process and is dropped before that process
//! detaches.

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux {
    use allocator_api2::vec::Vec;
    use core::mem::{MaybeUninit, align_of, size_of};
    use core::ptr::{self, NonNull};
    use core::sync::atomic::{AtomicU32, Ordering};
    use shmem_pod::fixed_allocator::{FixedRegionAllocator, FixedRegionAllocatorHandle};
    use shmem_pod::layout::LayoutDescriptor;
    use std::env;
    use std::error::Error;
    use std::io;
    use std::os::fd::RawFd;
    use std::process::{Child, Command};

    type Result<T> = std::result::Result<T, Box<dyn Error>>;

    const MAGIC: [u8; 8] = *b"SHPODA1\0";
    const FORMAT_VERSION: u32 = 1;
    const DESCRIPTOR_OFFSET: usize = 16;
    const REQUIRED_ADDRESS_OFFSET: usize = 64;
    const MAPPING_LEN_OFFSET: usize = 72;
    const CONTROL_OFFSET_OFFSET: usize = 80;
    const ARENA_OFFSET_OFFSET: usize = 88;
    const ARENA_LEN_OFFSET: usize = 96;
    const PAGE_SIZE_OFFSET: usize = 104;
    const READY_OFFSET: usize = 112;
    const BOOTSTRAP_LEN: usize = 128;
    const READY: u32 = 1;

    const FIXED_ADDRESS: usize = 0x5000_0000_0000;
    const ARENA_LEN: usize = 2 * 1024 * 1024;
    const WORKERS: usize = 4;
    const ROUNDS: usize = 150;
    const ELEMENTS: usize = 1_024;

    #[derive(Clone, Copy)]
    struct Geometry {
        mapping_len: usize,
        control_offset: usize,
        arena_offset: usize,
        arena_len: usize,
        page_size: usize,
    }

    struct Mapping {
        base: NonNull<u8>,
        len: usize,
    }

    impl Mapping {
        fn base(&self) -> *mut u8 {
            self.base.as_ptr()
        }
    }

    impl Drop for Mapping {
        fn drop(&mut self) {
            let result = unsafe { libc::munmap(self.base.as_ptr().cast(), self.len) };
            assert_eq!(result, 0, "munmap failed: {}", io::Error::last_os_error());
        }
    }

    struct OwnedFd(RawFd);

    impl OwnedFd {
        fn create(length: usize) -> Result<Self> {
            let fd = unsafe { libc::memfd_create(c"shmem-pod-allocator".as_ptr(), 0) };
            if fd < 0 {
                return Err(io::Error::last_os_error().into());
            }
            let owned = Self(fd);
            if unsafe { libc::ftruncate(fd, length.try_into()?) } != 0 {
                return Err(io::Error::last_os_error().into());
            }
            Ok(owned)
        }

        fn inherited(raw: RawFd, expected_len: usize) -> Result<Self> {
            if raw <= libc::STDERR_FILENO || unsafe { libc::fcntl(raw, libc::F_GETFD) } < 0 {
                return Err(
                    io::Error::new(io::ErrorKind::InvalidInput, "invalid inherited fd").into(),
                );
            }

            let mut status = MaybeUninit::<libc::stat>::uninit();
            if unsafe { libc::fstat(raw, status.as_mut_ptr()) } != 0 {
                return Err(io::Error::last_os_error().into());
            }
            let status = unsafe { status.assume_init() };
            if usize::try_from(status.st_size)? != expected_len {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "shared fd length does not match bootstrap argument",
                )
                .into());
            }
            Ok(Self(raw))
        }

        fn raw(&self) -> RawFd {
            self.0
        }
    }

    impl Drop for OwnedFd {
        fn drop(&mut self) {
            let result = unsafe { libc::close(self.0) };
            assert_eq!(result, 0, "close failed: {}", io::Error::last_os_error());
        }
    }

    pub fn main() -> Result<()> {
        let arguments: Vec<String> = env::args().collect();
        if arguments.get(1).map(String::as_str) == Some("--child") {
            return child_main(&arguments[2..]);
        }
        parent_main()
    }

    fn parent_main() -> Result<()> {
        let page_size = page_size()?;
        if BOOTSTRAP_LEN > page_size || FIXED_ADDRESS % page_size != 0 {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "invalid bootstrap geometry").into(),
            );
        }
        let geometry = Geometry {
            mapping_len: 2 * page_size + ARENA_LEN,
            control_offset: page_size,
            arena_offset: 2 * page_size,
            arena_len: ARENA_LEN,
            page_size,
        };
        validate_geometry(FIXED_ADDRESS, geometry)?;

        let fd = OwnedFd::create(geometry.mapping_len)?;
        let mapping = map_exact(fd.raw(), FIXED_ADDRESS, geometry.mapping_len)?;
        initialize_bootstrap(mapping.base(), geometry);

        let control = unsafe {
            mapping
                .base()
                .add(geometry.control_offset)
                .cast::<FixedRegionAllocator>()
        };
        let arena = unsafe { mapping.base().add(geometry.arena_offset) };
        unsafe { control.write(FixedRegionAllocator::new()) };

        {
            let allocator = unsafe { &*control };
            let parent_handle = unsafe { allocator.initialize(arena, geometry.arena_len) }?;
            ready_word(mapping.base()).store(READY, Ordering::Release);

            let executable = env::current_exe()?;
            let mut children = Vec::<Child>::new();
            for worker in 0..WORKERS {
                children.push(
                    Command::new(&executable)
                        .arg("--child")
                        .arg(fd.raw().to_string())
                        .arg(FIXED_ADDRESS.to_string())
                        .arg(geometry.mapping_len.to_string())
                        .arg(geometry.page_size.to_string())
                        .arg((worker + 1).to_string())
                        .spawn()?,
                );
            }

            if !exercise(parent_handle, 0) {
                return Err(io::Error::other("parent allocator exercise failed").into());
            }
            for mut child in children {
                let status = child.wait()?;
                if !status.success() {
                    return Err(
                        io::Error::other(format!("allocator child exited with {status}")).into(),
                    );
                }
            }
        }

        unsafe { ptr::drop_in_place(control) };
        println!(
            "PASS fixed_allocator_exec processes={} vector_rounds={} fixed_address=0x{FIXED_ADDRESS:x} arena_bytes={ARENA_LEN}",
            WORKERS + 1,
            (WORKERS + 1) * ROUNDS,
        );
        Ok(())
    }

    fn child_main(arguments: &[String]) -> Result<()> {
        if arguments.len() != 5 {
            return Err(
                io::Error::new(io::ErrorKind::InvalidInput, "invalid child arguments").into(),
            );
        }
        let raw_fd: RawFd = arguments[0].parse()?;
        let required_address: usize = arguments[1].parse()?;
        let mapping_len: usize = arguments[2].parse()?;
        let expected_page_size: usize = arguments[3].parse()?;
        let seed: u64 = arguments[4].parse()?;

        if page_size()? != expected_page_size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "page size changed across exec",
            )
            .into());
        }
        let fd = OwnedFd::inherited(raw_fd, mapping_len)?;
        let mapping = map_exact(fd.raw(), required_address, mapping_len)?;
        drop(fd);

        let geometry = validate_bootstrap(mapping.base(), required_address, mapping_len)?;
        let control = unsafe {
            mapping
                .base()
                .add(geometry.control_offset)
                .cast::<FixedRegionAllocator>()
        };
        let arena = unsafe { mapping.base().add(geometry.arena_offset) };
        let allocator = unsafe { &*control };
        let handle = unsafe { allocator.attach(arena, geometry.arena_len) }?;
        if !exercise(handle, seed) {
            return Err(io::Error::other("child allocator exercise failed").into());
        }
        Ok(())
    }

    fn exercise(handle: FixedRegionAllocatorHandle<'_>, seed: u64) -> bool {
        for round in 0..ROUNDS as u64 {
            let mut values = Vec::new_in(handle);
            if values.try_reserve(ELEMENTS).is_err() {
                return false;
            }
            values.extend((0..ELEMENTS as u64).map(|value| value ^ seed ^ round));
            if values[ELEMENTS / 2] != (ELEMENTS / 2) as u64 ^ seed ^ round
                || !handle
                    .region()
                    .contains(values.as_ptr().cast(), values.capacity() * size_of::<u64>())
            {
                return false;
            }
        }
        true
    }

    fn initialize_bootstrap(base: *mut u8, geometry: Geometry) {
        unsafe {
            base.write_bytes(0, BOOTSTRAP_LEN);
            ptr::copy_nonoverlapping(MAGIC.as_ptr(), base, MAGIC.len());
            write_u32(base, 8, FORMAT_VERSION);
            let descriptor = LayoutDescriptor::of::<FixedRegionAllocator>().encode();
            ptr::copy_nonoverlapping(
                descriptor.as_ptr(),
                base.add(DESCRIPTOR_OFFSET),
                descriptor.len(),
            );
            write_u64(base, REQUIRED_ADDRESS_OFFSET, FIXED_ADDRESS as u64);
            write_u64(base, MAPPING_LEN_OFFSET, geometry.mapping_len as u64);
            write_u64(base, CONTROL_OFFSET_OFFSET, geometry.control_offset as u64);
            write_u64(base, ARENA_OFFSET_OFFSET, geometry.arena_offset as u64);
            write_u64(base, ARENA_LEN_OFFSET, geometry.arena_len as u64);
            write_u64(base, PAGE_SIZE_OFFSET, geometry.page_size as u64);
            base.add(READY_OFFSET)
                .cast::<AtomicU32>()
                .write(AtomicU32::new(0));
        }
    }

    fn validate_bootstrap(
        base: *mut u8,
        required_address: usize,
        mapping_len: usize,
    ) -> Result<Geometry> {
        if ready_word(base).load(Ordering::Acquire) != READY {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "allocator is not ready").into(),
            );
        }
        let bootstrap = unsafe { core::slice::from_raw_parts(base, BOOTSTRAP_LEN) };
        if bootstrap[..8] != MAGIC
            || read_u32(bootstrap, 8) != FORMAT_VERSION
            || bootstrap[12..16].iter().any(|byte| *byte != 0)
        {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "invalid allocator bootstrap").into(),
            );
        }

        LayoutDescriptor::decode(
            &bootstrap[DESCRIPTOR_OFFSET..DESCRIPTOR_OFFSET + LayoutDescriptor::ENCODED_LEN],
        )?
        .validate::<FixedRegionAllocator>()?;

        let recorded_address = usize::try_from(read_u64(bootstrap, REQUIRED_ADDRESS_OFFSET))?;
        let geometry = Geometry {
            mapping_len: usize::try_from(read_u64(bootstrap, MAPPING_LEN_OFFSET))?,
            control_offset: usize::try_from(read_u64(bootstrap, CONTROL_OFFSET_OFFSET))?,
            arena_offset: usize::try_from(read_u64(bootstrap, ARENA_OFFSET_OFFSET))?,
            arena_len: usize::try_from(read_u64(bootstrap, ARENA_LEN_OFFSET))?,
            page_size: usize::try_from(read_u64(bootstrap, PAGE_SIZE_OFFSET))?,
        };
        if recorded_address != required_address || geometry.mapping_len != mapping_len {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "bootstrap mapping mismatch").into(),
            );
        }
        validate_geometry(recorded_address, geometry)?;
        Ok(geometry)
    }

    fn validate_geometry(required_address: usize, geometry: Geometry) -> Result<()> {
        if geometry.page_size == 0
            || !geometry.page_size.is_power_of_two()
            || required_address % geometry.page_size != 0
            || geometry.control_offset != geometry.page_size
            || geometry.arena_offset != 2 * geometry.page_size
            || geometry.arena_len != ARENA_LEN
            || geometry.mapping_len != geometry.arena_offset + geometry.arena_len
            || geometry.control_offset % align_of::<FixedRegionAllocator>() != 0
            || size_of::<FixedRegionAllocator>() > geometry.page_size
            || geometry.arena_offset % FixedRegionAllocator::REGION_ALIGNMENT != 0
            || geometry.arena_len % FixedRegionAllocator::REGION_ALIGNMENT != 0
        {
            return Err(
                io::Error::new(io::ErrorKind::InvalidData, "invalid shared geometry").into(),
            );
        }
        required_address
            .checked_add(geometry.mapping_len)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "mapping extent overflow"))?;
        Ok(())
    }

    fn map_exact(fd: RawFd, required_address: usize, length: usize) -> Result<Mapping> {
        let mapping = unsafe {
            libc::mmap(
                required_address as *mut libc::c_void,
                length,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED | libc::MAP_FIXED_NOREPLACE,
                fd,
                0,
            )
        };
        if mapping == libc::MAP_FAILED {
            return Err(io::Error::last_os_error().into());
        }
        if mapping as usize != required_address {
            unsafe { libc::munmap(mapping, length) };
            return Err(io::Error::new(
                io::ErrorKind::AddrNotAvailable,
                "mmap ignored fixed address",
            )
            .into());
        }
        Ok(Mapping {
            base: NonNull::new(mapping.cast()).expect("mmap returned a non-null fixed address"),
            len: length,
        })
    }

    fn page_size() -> Result<usize> {
        let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
        if page_size <= 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(usize::try_from(page_size)?)
    }

    fn ready_word(base: *mut u8) -> &'static AtomicU32 {
        unsafe { &*base.add(READY_OFFSET).cast::<AtomicU32>() }
    }

    unsafe fn write_u32(base: *mut u8, offset: usize, value: u32) {
        unsafe { ptr::copy_nonoverlapping(value.to_le_bytes().as_ptr(), base.add(offset), 4) };
    }

    unsafe fn write_u64(base: *mut u8, offset: usize, value: u64) {
        unsafe { ptr::copy_nonoverlapping(value.to_le_bytes().as_ptr(), base.add(offset), 8) };
    }

    fn read_u32(bytes: &[u8], offset: usize) -> u32 {
        u32::from_le_bytes(
            bytes[offset..offset + 4]
                .try_into()
                .expect("fixed field width"),
        )
    }

    fn read_u64(bytes: &[u8], offset: usize) -> u64 {
        u64::from_le_bytes(
            bytes[offset..offset + 8]
                .try_into()
                .expect("fixed field width"),
        )
    }
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    linux::main()
}

#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
fn main() {
    eprintln!("fixed_allocator_exec requires x86-64 Linux");
}
