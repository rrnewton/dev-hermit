//! Self-checking hierarchical SNZI example across forked processes.

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::mem::size_of;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use core::ptr;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
use shmem_pod::snzi::Snzi;

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
const WORKERS: usize = 8;
#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
const ITERATIONS: usize = 20_000;

#[cfg(all(target_os = "linux", target_has_atomic = "64"))]
fn main() {
    type SharedSnzi = Snzi<84>; // 4-ary tree with 64 leaves.

    let mapping = unsafe {
        libc::mmap(
            ptr::null_mut(),
            size_of::<SharedSnzi>(),
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    assert_ne!(mapping, libc::MAP_FAILED, "mmap failed");
    let shared = mapping.cast::<SharedSnzi>();
    unsafe { shared.write(SharedSnzi::new()) };
    let snzi = unsafe { &*shared };

    // The sentinel lets every worker and the parent assert that query never
    // reports a forbidden false result during the stress phase.
    let sentinel = snzi.arrive(0).expect("sentinel arrival");
    let mut children = [0; WORKERS];
    for (worker, child) in children.iter_mut().enumerate() {
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork failed");
        if pid == 0 {
            let snzi = unsafe { &*shared };
            let mut ok = true;
            for iteration in 0..ITERATIONS {
                // Deliberate collisions exercise helping and parent filtering.
                let leaf = (worker * 7 + iteration) % 8;
                match snzi.arrive(leaf) {
                    Ok(token) => {
                        ok &= snzi.query();
                        ok &= token.depart().is_ok();
                    }
                    Err(_) => {
                        ok = false;
                        break;
                    }
                }
            }
            unsafe { libc::_exit(if ok { 0 } else { 1 }) };
        }
        *child = pid;
    }

    for child in children {
        let mut status = 0;
        while unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) } == 0 {
            assert!(snzi.query(), "query missed the held sentinel");
            core::hint::spin_loop();
        }
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 0);
    }

    assert!(snzi.query());
    sentinel.depart().expect("sentinel departure");
    assert!(!snzi.query());
    assert!(snzi.is_quiescent());
    assert_eq!(snzi.poison_reason(), None);

    unsafe { ptr::drop_in_place(shared) };
    assert_eq!(unsafe { libc::munmap(mapping, size_of::<SharedSnzi>()) }, 0);
    println!(
        "PASS snzi workers={WORKERS} operations={} leaves=64",
        WORKERS * ITERATIONS
    );
}

#[cfg(not(all(target_os = "linux", target_has_atomic = "64")))]
fn main() {
    eprintln!("snzi example requires Linux and 64-bit atomics");
}
