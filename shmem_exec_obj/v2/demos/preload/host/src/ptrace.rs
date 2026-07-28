//! Narrow x86-64 ptrace bootstrap used only by the executable demonstration.
//!
//! The fixture is single-threaded and stopped at an explicit safe point. A
//! production injector must seize every thread and handle clone/exec races.

use shmem_pod::injection::{BootstrapContext, BootstrapStatus};
use std::error::Error;
use std::ffi::{CStr, CString, c_void};
use std::fs;
use std::mem::MaybeUninit;
use std::path::{Path, PathBuf};
use std::ptr;

const SCRATCH_LEN: usize = 4096;

pub fn wait_for_fixture_stop(pid: libc::pid_t) -> Result<(), Box<dyn Error>> {
    let mut status = 0;
    let waited = unsafe { libc::waitpid(pid, &mut status, libc::WUNTRACED) };
    if waited != pid {
        return Err(format!(
            "waitpid for ptrace fixture: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    if !libc::WIFSTOPPED(status) || libc::WSTOPSIG(status) != libc::SIGSTOP {
        return Err(
            format!("ptrace fixture did not stop with SIGSTOP: status=0x{status:x}").into(),
        );
    }
    Ok(())
}

pub fn inject(
    pid: libc::pid_t,
    shim: &Path,
    context: &BootstrapContext,
) -> Result<(), Box<dyn Error>> {
    if !cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        return Err("ptrace demo supports Linux x86-64 only".into());
    }
    ptrace_call(
        libc::PTRACE_SEIZE,
        pid,
        0,
        libc::PTRACE_O_EXITKILL as usize,
        "PTRACE_SEIZE",
    )?;
    let mut tracee = AttachedTracee { pid, live: true };
    ptrace_call(libc::PTRACE_INTERRUPT, pid, 0, 0, "PTRACE_INTERRUPT")?;
    wait_stopped(pid, None)?;

    let scratch = remote_syscall(
        pid,
        libc::SYS_mmap as u64,
        [
            0,
            SCRATCH_LEN as u64,
            (libc::PROT_READ | libc::PROT_WRITE) as u64,
            (libc::MAP_PRIVATE | libc::MAP_ANONYMOUS) as u64,
            u64::MAX,
            0,
        ],
    )?;
    let scratch = syscall_pointer(scratch, "remote mmap")?;

    let shim = shim.canonicalize()?;
    let shim_c = CString::new(shim.as_os_str().as_encoded_bytes())?;
    if shim_c.as_bytes_with_nul().len() + BootstrapContext::ENCODED_LEN > SCRATCH_LEN {
        return Err("ptrace scratch payload is too large".into());
    }
    process_write(pid, scratch, shim_c.as_bytes_with_nul())?;

    let remote_dlopen = remote_symbol_from_local(pid, local_symbol(c"dlopen")?)?;
    let handle = remote_call(
        pid,
        remote_dlopen,
        [
            scratch,
            (libc::RTLD_NOW | libc::RTLD_LOCAL) as u64,
            0,
            0,
            0,
            0,
        ],
    )?;
    if handle == 0 {
        return Err("remote dlopen returned null".into());
    }

    let local_handle = unsafe { libc::dlopen(shim_c.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
    if local_handle.is_null() {
        return Err(format!("local dlopen for symbol offset failed: {}", dlerror()).into());
    }
    let local_bootstrap = unsafe { libc::dlsym(local_handle, c"shmem_pod_bootstrap_v1".as_ptr()) };
    if local_bootstrap.is_null() {
        unsafe { libc::dlclose(local_handle) };
        return Err(format!("local dlsym bootstrap failed: {}", dlerror()).into());
    }
    let remote_bootstrap = remote_symbol_from_local(pid, local_bootstrap)?;

    let context_address = align_up(scratch + shim_c.as_bytes_with_nul().len() as u64, 16)?;
    process_write(pid, context_address, &context.encode())?;
    let status = remote_call(pid, remote_bootstrap, [context_address, 0, 0, 0, 0, 0])? as i64;
    unsafe { libc::dlclose(local_handle) };
    if status != BootstrapStatus::Ok as i64 {
        return Err(format!("remote bootstrap returned status {status}").into());
    }

    let unmapped = remote_syscall(
        pid,
        libc::SYS_munmap as u64,
        [scratch, SCRATCH_LEN as u64, 0, 0, 0, 0],
    )?;
    if unmapped != 0 {
        return Err(format!("remote munmap returned {unmapped}").into());
    }
    tracee.detach()?;
    Ok(())
}

struct AttachedTracee {
    pid: libc::pid_t,
    live: bool,
}

impl AttachedTracee {
    fn detach(&mut self) -> Result<(), Box<dyn Error>> {
        ptrace_call(
            libc::PTRACE_DETACH,
            self.pid,
            0,
            libc::SIGCONT as usize,
            "PTRACE_DETACH",
        )?;
        self.live = false;
        Ok(())
    }
}

impl Drop for AttachedTracee {
    fn drop(&mut self) {
        if self.live {
            let _ = unsafe {
                libc::ptrace(
                    libc::PTRACE_DETACH,
                    self.pid,
                    ptr::null_mut::<c_void>(),
                    libc::SIGKILL as usize as *mut c_void,
                )
            };
            let _ = unsafe { libc::kill(self.pid, libc::SIGKILL) };
        }
    }
}

fn remote_syscall(
    pid: libc::pid_t,
    number: u64,
    arguments: [u64; 6],
) -> Result<u64, Box<dyn Error>> {
    let original = get_registers(pid)?;
    let original_word = peek(pid, original.rip, libc::PTRACE_PEEKTEXT, "PTRACE_PEEKTEXT")?;
    let mut patched = original_word.to_ne_bytes();
    patched[..3].copy_from_slice(&[0x0f, 0x05, 0xcc]); // syscall; int3
    poke(
        pid,
        original.rip,
        u64::from_ne_bytes(patched),
        libc::PTRACE_POKETEXT,
        "PTRACE_POKETEXT",
    )?;

    let mut registers = original;
    registers.rax = number;
    registers.rdi = arguments[0];
    registers.rsi = arguments[1];
    registers.rdx = arguments[2];
    registers.r10 = arguments[3];
    registers.r8 = arguments[4];
    registers.r9 = arguments[5];
    set_registers(pid, &registers)?;
    ptrace_call(libc::PTRACE_CONT, pid, 0, 0, "PTRACE_CONT syscall")?;
    let stopped = wait_stopped(pid, Some(libc::SIGTRAP));
    let result = stopped.and_then(|()| get_registers(pid).map(|registers| registers.rax));

    // Restore code before registers so any failure leaves the tracee stopped at
    // the original instruction rather than at a temporary syscall gadget.
    let restore_code = poke(
        pid,
        original.rip,
        original_word,
        libc::PTRACE_POKETEXT,
        "restore tracee text",
    );
    let restore_registers = set_registers(pid, &original);
    restore_code?;
    restore_registers?;
    result
}

fn remote_call(
    pid: libc::pid_t,
    function: u64,
    arguments: [u64; 6],
) -> Result<u64, Box<dyn Error>> {
    let original = get_registers(pid)?;
    // Stay below the SysV red zone and enter with rsp % 16 == 8, exactly as if
    // a `call` instruction had pushed the null sentinel return address.
    let call_stack = ((original
        .rsp
        .checked_sub(256)
        .ok_or("tracee stack underflow")?)
        & !15)
        .checked_sub(8)
        .ok_or("tracee stack underflow")?;
    let original_stack = peek(pid, call_stack, libc::PTRACE_PEEKDATA, "save tracee stack")?;
    poke(
        pid,
        call_stack,
        0,
        libc::PTRACE_POKEDATA,
        "install null return sentinel",
    )?;

    let mut registers = original;
    registers.rip = function;
    registers.rsp = call_stack;
    registers.rdi = arguments[0];
    registers.rsi = arguments[1];
    registers.rdx = arguments[2];
    registers.rcx = arguments[3];
    registers.r8 = arguments[4];
    registers.r9 = arguments[5];
    registers.rax = 0;
    set_registers(pid, &registers)?;
    ptrace_call(libc::PTRACE_CONT, pid, 0, 0, "PTRACE_CONT function")?;
    let stopped = wait_stopped(pid, Some(libc::SIGSEGV));
    let result = stopped.and_then(|()| {
        let registers = get_registers(pid)?;
        if registers.rip != 0 {
            return Err(format!(
                "remote function faulted at 0x{:x}, not its null return sentinel",
                registers.rip
            )
            .into());
        }
        Ok(registers.rax)
    });
    let restore_stack = poke(
        pid,
        call_stack,
        original_stack,
        libc::PTRACE_POKEDATA,
        "restore tracee stack",
    );
    let restore_registers = set_registers(pid, &original);
    restore_stack?;
    restore_registers?;
    result
}

fn local_symbol(name: &CStr) -> Result<*mut c_void, Box<dyn Error>> {
    let symbol = unsafe { libc::dlsym(libc::RTLD_DEFAULT, name.as_ptr()) };
    if symbol.is_null() {
        Err(format!(
            "local dlsym {} failed: {}",
            name.to_string_lossy(),
            dlerror()
        )
        .into())
    } else {
        Ok(symbol)
    }
}

fn remote_symbol_from_local(
    pid: libc::pid_t,
    local_symbol: *mut c_void,
) -> Result<u64, Box<dyn Error>> {
    let mut info = MaybeUninit::<libc::Dl_info>::uninit();
    if unsafe { libc::dladdr(local_symbol, info.as_mut_ptr()) } == 0 {
        return Err("dladdr could not identify local symbol".into());
    }
    let info = unsafe { info.assume_init() };
    if info.dli_fbase.is_null() || info.dli_fname.is_null() {
        return Err("dladdr returned incomplete module identity".into());
    }
    let path = PathBuf::from(
        unsafe { CStr::from_ptr(info.dli_fname) }
            .to_string_lossy()
            .as_ref(),
    );
    let offset = (local_symbol as usize)
        .checked_sub(info.dli_fbase as usize)
        .ok_or("local symbol precedes module base")? as u64;
    let remote_base = remote_module_base(pid, &path)?;
    remote_base
        .checked_add(offset)
        .ok_or_else(|| "remote symbol address overflow".into())
}

fn remote_module_base(pid: libc::pid_t, local_path: &Path) -> Result<u64, Box<dyn Error>> {
    let wanted = local_path.canonicalize()?;
    let maps = fs::read_to_string(format!("/proc/{pid}/maps"))?;
    for line in maps.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() < 6 || !fields[5].starts_with('/') {
            continue;
        }
        let candidate = Path::new(fields[5]);
        if candidate.canonicalize().ok().as_ref() != Some(&wanted) {
            continue;
        }
        let start = fields[0]
            .split_once('-')
            .and_then(|(start, _)| u64::from_str_radix(start, 16).ok())
            .ok_or("malformed remote mapping range")?;
        let offset = u64::from_str_radix(fields[2], 16)?;
        return start
            .checked_sub(offset)
            .ok_or_else(|| "remote module mapping offset underflow".into());
    }
    Err(format!("{} is absent from tracee maps", wanted.display()).into())
}

fn process_write(pid: libc::pid_t, address: u64, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    let local = libc::iovec {
        iov_base: bytes.as_ptr() as *mut c_void,
        iov_len: bytes.len(),
    };
    let remote = libc::iovec {
        iov_base: address as usize as *mut c_void,
        iov_len: bytes.len(),
    };
    let written = unsafe { libc::process_vm_writev(pid, &local, 1, &remote, 1, 0) };
    if written != bytes.len() as isize {
        return Err(format!(
            "process_vm_writev wrote {written}/{} bytes: {}",
            bytes.len(),
            std::io::Error::last_os_error()
        )
        .into());
    }
    Ok(())
}

fn get_registers(pid: libc::pid_t) -> Result<libc::user_regs_struct, Box<dyn Error>> {
    let mut registers = MaybeUninit::<libc::user_regs_struct>::uninit();
    ptrace_call(
        libc::PTRACE_GETREGS,
        pid,
        0,
        registers.as_mut_ptr() as usize,
        "PTRACE_GETREGS",
    )?;
    Ok(unsafe { registers.assume_init() })
}

fn set_registers(
    pid: libc::pid_t,
    registers: &libc::user_regs_struct,
) -> Result<(), Box<dyn Error>> {
    ptrace_call(
        libc::PTRACE_SETREGS,
        pid,
        0,
        registers as *const _ as usize,
        "PTRACE_SETREGS",
    )
}

fn peek(
    pid: libc::pid_t,
    address: u64,
    request: libc::c_uint,
    operation: &str,
) -> Result<u64, Box<dyn Error>> {
    unsafe { *libc::__errno_location() = 0 };
    let result = unsafe {
        libc::ptrace(
            request,
            pid,
            address as usize as *mut c_void,
            ptr::null_mut::<c_void>(),
        )
    };
    let errno = unsafe { *libc::__errno_location() };
    if result == -1 && errno != 0 {
        Err(format!("{operation}: {}", std::io::Error::from_raw_os_error(errno)).into())
    } else {
        Ok(result as u64)
    }
}

fn poke(
    pid: libc::pid_t,
    address: u64,
    word: u64,
    request: libc::c_uint,
    operation: &str,
) -> Result<(), Box<dyn Error>> {
    ptrace_call(request, pid, address as usize, word as usize, operation)
}

fn ptrace_call(
    request: libc::c_uint,
    pid: libc::pid_t,
    address: usize,
    data: usize,
    operation: &str,
) -> Result<(), Box<dyn Error>> {
    let result = unsafe { libc::ptrace(request, pid, address as *mut c_void, data as *mut c_void) };
    if result == -1 {
        Err(format!("{operation}: {}", std::io::Error::last_os_error()).into())
    } else {
        Ok(())
    }
}

fn wait_stopped(pid: libc::pid_t, signal: Option<libc::c_int>) -> Result<(), Box<dyn Error>> {
    let mut status = 0;
    let waited = unsafe { libc::waitpid(pid, &mut status, libc::__WALL) };
    if waited != pid {
        return Err(format!("ptrace waitpid: {}", std::io::Error::last_os_error()).into());
    }
    if !libc::WIFSTOPPED(status) {
        return Err(format!("tracee was not stopped: status=0x{status:x}").into());
    }
    if let Some(expected) = signal
        && libc::WSTOPSIG(status) != expected
    {
        return Err(format!(
            "tracee stopped with signal {}, expected {expected}",
            libc::WSTOPSIG(status)
        )
        .into());
    }
    Ok(())
}

fn syscall_pointer(value: u64, operation: &str) -> Result<u64, Box<dyn Error>> {
    let signed = value as i64;
    if (-4095..=-1).contains(&signed) {
        Err(format!(
            "{operation}: {}",
            std::io::Error::from_raw_os_error(-signed as i32)
        )
        .into())
    } else if value == 0 {
        Err(format!("{operation} returned null").into())
    } else {
        Ok(value)
    }
}

fn align_up(value: u64, alignment: u64) -> Result<u64, Box<dyn Error>> {
    value
        .checked_add(alignment - 1)
        .map(|value| value & !(alignment - 1))
        .ok_or_else(|| "address alignment overflow".into())
}

fn dlerror() -> String {
    let error = unsafe { libc::dlerror() };
    if error.is_null() {
        "unknown dynamic-loader error".into()
    } else {
        unsafe { CStr::from_ptr(error) }
            .to_string_lossy()
            .into_owned()
    }
}
