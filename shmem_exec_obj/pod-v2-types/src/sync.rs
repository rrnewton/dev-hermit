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
