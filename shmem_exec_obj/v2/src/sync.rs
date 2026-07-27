//! Process-shared synchronization primitives.
//!
//! These primitives use only atomics stored inside the shared object. They do
//! not depend on process-local pthread state or a process-local allocator.

use core::cell::UnsafeCell;
use core::hint::spin_loop;
use core::marker::PhantomData;
use core::mem::{align_of, needs_drop, offset_of, size_of};
use core::ops::{Deref, DerefMut};
use core::sync::atomic::{AtomicU32, Ordering};

use crate::{__private, FixedAddressPodValue, PodSync, PodValue};

const UNLOCKED: u32 = 0;
const LOCKED: u32 = 1;

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const CONTENDED: u32 = 2;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const FUTEX_SPIN_LIMIT: usize = 64;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const FUTEX_WAIT: u32 = 0;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const FUTEX_WAKE: u32 = 1;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const EINTR: isize = 4;
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
const EAGAIN: isize = 11;

/// A small, non-fair mutex whose lock word lives in process-shared memory.
///
/// Unlike [`std::sync::Mutex`](https://doc.rust-lang.org/std/sync/struct.Mutex.html),
/// this type has no process-local runtime state. The same initialized object may
/// therefore be used by threads in multiple processes which map its pages.
///
/// This is a spin lock. It is appropriate only for short critical sections and
/// does not sleep while contended. It is not reentrant, fair, robust, or
/// async-signal-safe. If a thread or process exits while holding the lock, the
/// mutex remains locked permanently. Use the lock-free [`crate::snzi::Snzi`]
/// presence indicator or a separately audited robust owner protocol when owner
/// death must not wedge progress.
pub struct ProcessSpinMutex<T: ?Sized> {
    state: AtomicU32,
    value: UnsafeCell<T>,
}

impl<T> ProcessSpinMutex<T> {
    /// Creates an unlocked mutex containing `value`.
    ///
    /// Construct the object once before making the containing shared mapping
    /// visible to other processes.
    pub const fn new(value: T) -> Self {
        Self {
            state: AtomicU32::new(UNLOCKED),
            value: UnsafeCell::new(value),
        }
    }

    /// Consumes the mutex and returns its value without locking.
    pub fn into_inner(self) -> T {
        self.value.into_inner()
    }
}

impl<T: ?Sized> ProcessSpinMutex<T> {
    /// Spins until the mutex is acquired.
    #[inline]
    pub fn lock(&self) -> ProcessSpinMutexGuard<'_, T> {
        loop {
            if let Some(guard) = self.try_lock() {
                return guard;
            }

            while self.state.load(Ordering::Relaxed) == LOCKED {
                spin_loop();
            }
        }
    }

    /// Attempts to acquire the mutex without waiting.
    #[inline]
    pub fn try_lock(&self) -> Option<ProcessSpinMutexGuard<'_, T>> {
        self.state
            .compare_exchange(UNLOCKED, LOCKED, Ordering::Acquire, Ordering::Relaxed)
            .ok()
            .map(|_| ProcessSpinMutexGuard {
                mutex: self,
                _not_send: PhantomData,
            })
    }

    /// Returns whether the lock word currently appears locked.
    ///
    /// The result is only a snapshot and may be stale immediately.
    #[inline]
    pub fn is_locked(&self) -> bool {
        self.state.load(Ordering::Relaxed) == LOCKED
    }

    /// Returns mutable access without locking.
    ///
    /// The exclusive borrow proves that no Rust reference can concurrently use
    /// this instance. The caller remains responsible for excluding raw access
    /// from another process.
    pub fn get_mut(&mut self) -> &mut T {
        self.value.get_mut()
    }

    #[inline]
    fn unlock(&self) {
        self.state.store(UNLOCKED, Ordering::Release);
    }
}

/// An RAII guard returned by [`ProcessSpinMutex::lock`].
///
/// The guard is intentionally not `Send`; it must unlock in the acquiring
/// thread's control flow. Do not call `fork` while a guard is live: the child
/// receives a duplicate guard whose drop would release the same shared lock
/// while the parent still assumes exclusive access. A child created in that
/// state must not access or drop the guard or protected value; normally it
/// should immediately `exec` or `_exit` without unwinding Rust values.
#[must_use = "dropping the guard immediately unlocks the mutex"]
pub struct ProcessSpinMutexGuard<'a, T: ?Sized> {
    mutex: &'a ProcessSpinMutex<T>,
    _not_send: PhantomData<*mut ()>,
}

impl<T: ?Sized> Deref for ProcessSpinMutexGuard<'_, T> {
    type Target = T;

    fn deref(&self) -> &Self::Target {
        // SAFETY: a guard exists only after exclusive lock acquisition. The
        // mutex's Sync implementation requires the protected value to be Send.
        unsafe { &*self.mutex.value.get() }
    }
}

impl<T: ?Sized> DerefMut for ProcessSpinMutexGuard<'_, T> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        // SAFETY: this guard uniquely owns the acquired lock.
        unsafe { &mut *self.mutex.value.get() }
    }
}

impl<T: ?Sized> Drop for ProcessSpinMutexGuard<'_, T> {
    fn drop(&mut self) {
        self.mutex.unlock();
    }
}

// SAFETY: ownership of T may move with the mutex.
unsafe impl<T: ?Sized + Send> Send for ProcessSpinMutex<T> {}

// SAFETY: all shared access to T exposed by this type is serialized by an
// acquire/release atomic lock word. T need only be Send, as with std::sync::Mutex.
unsafe impl<T: ?Sized + Send> Sync for ProcessSpinMutex<T> {}

// SAFETY: the lock word has no destructor or process-local state, and the
// recursive bound establishes the same property for the protected value.
unsafe impl<T: FixedAddressPodValue + Send> FixedAddressPodValue for ProcessSpinMutex<T> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod mutexes must not need drop");
        let state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-process-spin-mutex-v1",
        );
        let state = __private::mix_usize(state, size_of::<Self>());
        let mut state = __private::mix_usize(state, align_of::<Self>());

        state = __private::mix_bytes(state, b"state");
        state = __private::mix_usize(state, offset_of!(Self, state));
        state = __private::mix_usize(state, size_of::<AtomicU32>());
        state = __private::mix_usize(state, align_of::<AtomicU32>());
        state = __private::mix_u128(state, AtomicU32::FINGERPRINT);

        state = __private::mix_bytes(state, b"value");
        state = __private::mix_usize(state, offset_of!(Self, value));
        state = __private::mix_usize(state, size_of::<T>());
        state = __private::mix_usize(state, align_of::<T>());
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

// SAFETY: both the lock word and T are address-independent.
unsafe impl<T: PodValue + Send> PodValue for ProcessSpinMutex<T> {}

// SAFETY: safe shared mutation is serialized by the process-shared lock word.
unsafe impl<T: FixedAddressPodValue + Send> PodSync for ProcessSpinMutex<T> {}

/// A Linux process-shared mutex which spins briefly and then sleeps on a futex.
///
/// The mutex word must reside in a `MAP_SHARED` mapping backed by the same
/// shared-memory object in every participating process. Mapping addresses may
/// differ. The implementation uses the non-private `FUTEX_WAIT` and
/// `FUTEX_WAKE` operations; using the `_PRIVATE` variants would incorrectly key
/// waiters by process address space.
///
/// Uncontended acquisition performs one compare-exchange. Contended acquisition
/// spins for a bounded interval before sleeping in the kernel. On 64-bit
/// `x86_64` and `aarch64`, futex operations use inline system calls and add no
/// libc function relocation to the lock path. Linux x32 and other
/// architectures use `libc::syscall` as a portability fallback.
///
/// This mutex is not reentrant, fair, robust, or async-signal-safe. In
/// particular, it does not register a Linux robust-futex list: if a thread or
/// process exits while holding the lock, the mutex remains locked permanently.
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
pub struct ProcessFutexMutex<T: ?Sized> {
    state: AtomicU32,
    value: UnsafeCell<T>,
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl<T> ProcessFutexMutex<T> {
    /// Creates an unlocked mutex containing `value`.
    ///
    /// Construct the object once before publishing its shared mapping.
    pub const fn new(value: T) -> Self {
        Self {
            state: AtomicU32::new(UNLOCKED),
            value: UnsafeCell::new(value),
        }
    }

    /// Consumes the mutex and returns its value without locking.
    pub fn into_inner(self) -> T {
        self.value.into_inner()
    }
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl<T: ?Sized> ProcessFutexMutex<T> {
    /// Acquires the mutex, sleeping in the kernel after bounded spinning.
    #[inline]
    pub fn lock(&self) -> ProcessFutexMutexGuard<'_, T> {
        if let Some(guard) = self.try_lock() {
            return guard;
        }

        for _ in 0..FUTEX_SPIN_LIMIT {
            spin_loop();
            if let Some(guard) = self.try_lock() {
                return guard;
            }
        }

        self.lock_contended()
    }

    /// Attempts to acquire the mutex without waiting.
    #[inline]
    pub fn try_lock(&self) -> Option<ProcessFutexMutexGuard<'_, T>> {
        self.state
            .compare_exchange(UNLOCKED, LOCKED, Ordering::Acquire, Ordering::Relaxed)
            .ok()
            .map(|_| ProcessFutexMutexGuard {
                mutex: self,
                _not_send: PhantomData,
            })
    }

    /// Returns whether the lock word currently appears locked.
    ///
    /// The result is only a snapshot and may be stale immediately.
    #[inline]
    pub fn is_locked(&self) -> bool {
        self.state.load(Ordering::Relaxed) != UNLOCKED
    }

    /// Returns whether the lock word currently carries the contended marker.
    ///
    /// This is a diagnostic snapshot, not an exact waiter count. The marker can
    /// remain set while a woken waiter owns the mutex.
    #[inline]
    pub fn is_contended(&self) -> bool {
        self.state.load(Ordering::Relaxed) == CONTENDED
    }

    /// Returns mutable access without locking.
    ///
    /// The exclusive borrow excludes ordinary Rust access through this
    /// instance. The caller remains responsible for excluding raw access from
    /// another process.
    pub fn get_mut(&mut self) -> &mut T {
        self.value.get_mut()
    }

    #[cold]
    fn lock_contended(&self) -> ProcessFutexMutexGuard<'_, T> {
        // State 2 both records that a wake may be required and serves as the
        // futex comparison value. swap(2) acquires directly if an unlock raced
        // with entry into the slow path.
        if self.state.swap(CONTENDED, Ordering::Acquire) != UNLOCKED {
            loop {
                futex_wait(&self.state, CONTENDED);
                if self.state.swap(CONTENDED, Ordering::Acquire) == UNLOCKED {
                    break;
                }
            }
        }

        ProcessFutexMutexGuard {
            mutex: self,
            _not_send: PhantomData,
        }
    }

    #[inline]
    fn unlock(&self) {
        // A waiter changes state 1 to 2 before FUTEX_WAIT. Consequently either
        // this release observes 2 and wakes it, or the waiter observes 0 and
        // acquires without sleeping; there is no lost-wakeup interval.
        if self.state.swap(UNLOCKED, Ordering::Release) == CONTENDED {
            futex_wake_one(&self.state);
        }
    }
}

/// An RAII guard returned by [`ProcessFutexMutex::lock`].
///
/// The guard is intentionally not `Send`. Do not call `fork` while a guard is
/// live unless the child immediately forgets its duplicate guard and then
/// `exec`s or `_exit`s without accessing the protected value. Dropping the
/// duplicate would incorrectly unlock the parent's shared mutex.
#[cfg(all(feature = "linux-futex", target_os = "linux"))]
#[must_use = "dropping the guard immediately unlocks the mutex"]
pub struct ProcessFutexMutexGuard<'a, T: ?Sized> {
    mutex: &'a ProcessFutexMutex<T>,
    _not_send: PhantomData<*mut ()>,
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl<T: ?Sized> Deref for ProcessFutexMutexGuard<'_, T> {
    type Target = T;

    fn deref(&self) -> &Self::Target {
        // SAFETY: a guard exists only after exclusive lock acquisition. The
        // mutex's Sync implementation requires the protected value to be Send.
        unsafe { &*self.mutex.value.get() }
    }
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl<T: ?Sized> DerefMut for ProcessFutexMutexGuard<'_, T> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        // SAFETY: this guard uniquely owns the acquired lock.
        unsafe { &mut *self.mutex.value.get() }
    }
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
impl<T: ?Sized> Drop for ProcessFutexMutexGuard<'_, T> {
    fn drop(&mut self) {
        self.mutex.unlock();
    }
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
// SAFETY: ownership of T may move with the mutex.
unsafe impl<T: ?Sized + Send> Send for ProcessFutexMutex<T> {}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
// SAFETY: all shared access to T is serialized by the atomic lock word and
// Linux's process-shared futex wait queues. T need only be Send.
unsafe impl<T: ?Sized + Send> Sync for ProcessFutexMutex<T> {}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
// SAFETY: the lock word has no destructor or process-local state, and the
// recursive bound establishes the same property for the protected value.
unsafe impl<T: FixedAddressPodValue + Send> FixedAddressPodValue for ProcessFutexMutex<T> {
    const FINGERPRINT: u128 = {
        assert!(!needs_drop::<Self>(), "pod mutexes must not need drop");
        let state = __private::mix_bytes(
            __private::FINGERPRINT_SEED,
            b"shmem-pod-process-futex-mutex-v1",
        );
        let state = __private::mix_usize(state, size_of::<Self>());
        let mut state = __private::mix_usize(state, align_of::<Self>());

        state = __private::mix_bytes(state, b"state");
        state = __private::mix_usize(state, offset_of!(Self, state));
        state = __private::mix_usize(state, size_of::<AtomicU32>());
        state = __private::mix_usize(state, align_of::<AtomicU32>());
        state = __private::mix_u128(state, AtomicU32::FINGERPRINT);

        state = __private::mix_bytes(state, b"value");
        state = __private::mix_usize(state, offset_of!(Self, value));
        state = __private::mix_usize(state, size_of::<T>());
        state = __private::mix_usize(state, align_of::<T>());
        __private::finish(__private::mix_u128(state, T::FINGERPRINT))
    };
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
// SAFETY: both the lock word and T are address-independent.
unsafe impl<T: PodValue + Send> PodValue for ProcessFutexMutex<T> {}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
// SAFETY: safe shared mutation is serialized by the process-shared mutex.
unsafe impl<T: FixedAddressPodValue + Send> PodSync for ProcessFutexMutex<T> {}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
#[inline]
fn futex_wait(word: &AtomicU32, expected: u32) {
    loop {
        // SAFETY: AtomicU32 has the 32-bit size and alignment required by the
        // futex ABI, and the reference remains valid throughout the syscall.
        let result = unsafe { raw_futex(word, FUTEX_WAIT, expected) };
        if result == -EINTR {
            continue;
        }
        // EAGAIN means the value changed before the kernel enqueued us.
        // Returning makes lock_contended retry acquisition before deciding
        // whether to sleep again. Safe construction rules out EFAULT/EINVAL.
        debug_assert!(
            result >= 0 || result == -EAGAIN,
            "unexpected futex wait error: {}",
            -result
        );
        break;
    }
}

#[cfg(all(feature = "linux-futex", target_os = "linux"))]
#[inline]
fn futex_wake_one(word: &AtomicU32) {
    // SAFETY: the atomic word is valid and aligned for the futex ABI. Wake has
    // no timeout or secondary pointer argument.
    let _ = unsafe { raw_futex(word, FUTEX_WAKE, 1) };
}

#[cfg(all(
    feature = "linux-futex",
    target_os = "linux",
    target_arch = "x86_64",
    target_pointer_width = "64"
))]
#[inline]
unsafe fn raw_futex(word: &AtomicU32, operation: u32, value: u32) -> isize {
    let mut result = 202_isize;
    // SAFETY: this follows the Linux x86_64 syscall ABI. FUTEX_WAIT reads the
    // pointed-to atomic and FUTEX_WAKE uses it as a wait-queue key.
    unsafe {
        core::arch::asm!(
            "syscall",
            inlateout("rax") result,
            in("rdi") word as *const AtomicU32 as usize,
            in("rsi") operation as usize,
            in("rdx") value as usize,
            in("r10") 0_usize,
            in("r8") 0_usize,
            in("r9") 0_usize,
            lateout("rcx") _,
            lateout("r11") _,
            options(nostack),
        );
    }
    result
}

#[cfg(all(feature = "linux-futex", target_os = "linux", target_arch = "aarch64"))]
#[inline]
unsafe fn raw_futex(word: &AtomicU32, operation: u32, value: u32) -> isize {
    let mut result = word as *const AtomicU32 as usize;
    // SAFETY: this follows the Linux aarch64 syscall ABI. Syscall 98 is futex.
    unsafe {
        core::arch::asm!(
            "svc 0",
            inlateout("x0") result,
            in("x1") operation as usize,
            in("x2") value as usize,
            in("x3") 0_usize,
            in("x4") 0_usize,
            in("x5") 0_usize,
            in("x8") 98_usize,
            options(nostack),
        );
    }
    result as isize
}

#[cfg(all(
    feature = "linux-futex",
    target_os = "linux",
    not(any(
        all(target_arch = "x86_64", target_pointer_width = "64"),
        target_arch = "aarch64"
    ))
))]
#[inline]
unsafe fn raw_futex(word: &AtomicU32, operation: u32, value: u32) -> isize {
    // SAFETY: this passes the same valid atomic address and scalar arguments to
    // libc's variadic syscall shim on Linux architectures without an inline
    // syscall implementation above.
    let result = unsafe {
        libc::syscall(
            libc::SYS_futex,
            word as *const AtomicU32,
            operation,
            value,
            core::ptr::null::<libc::timespec>(),
            0_usize,
            0_usize,
        ) as isize
    };
    if result == -1 {
        // libc's syscall shim converts the kernel's negative result to -1 and
        // records the actual error, unlike the inline syscall paths above.
        // SAFETY: __errno_location returns this thread's valid errno slot.
        -(unsafe { *libc::__errno_location() } as isize)
    } else {
        result
    }
}

/// Raw process-shared spin lock used to integrate with `lock_api` consumers.
///
/// This type is available with the `fixed-allocator` feature. Most users should
/// use [`ProcessSpinMutex`] directly.
#[cfg(feature = "fixed-allocator")]
pub struct ProcessSpinRawMutex {
    state: AtomicU32,
}

#[cfg(feature = "fixed-allocator")]
impl ProcessSpinRawMutex {
    /// Creates an unlocked raw mutex.
    pub const fn new() -> Self {
        Self {
            state: AtomicU32::new(UNLOCKED),
        }
    }
}

#[cfg(feature = "fixed-allocator")]
impl Default for ProcessSpinRawMutex {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(feature = "fixed-allocator")]
// SAFETY: successful compare-exchange grants exclusive ownership until the
// matching release store in unlock. This mutex is not reentrant or robust.
unsafe impl lock_api::RawMutex for ProcessSpinRawMutex {
    #[allow(clippy::declare_interior_mutable_const)]
    const INIT: Self = Self::new();
    type GuardMarker = lock_api::GuardNoSend;

    fn lock(&self) {
        while !self.try_lock() {
            while self.state.load(Ordering::Relaxed) == LOCKED {
                spin_loop();
            }
        }
    }

    fn try_lock(&self) -> bool {
        self.state
            .compare_exchange(UNLOCKED, LOCKED, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
    }

    unsafe fn unlock(&self) {
        self.state.store(UNLOCKED, Ordering::Release);
    }
}
