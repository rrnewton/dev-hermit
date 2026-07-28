//! Atomic backend selected for bounded model checking.

#[cfg(not(shmem_pod_loom))]
pub(crate) use core::sync::atomic::{AtomicU64, Ordering};
#[cfg(shmem_pod_loom)]
pub(crate) use loom::sync::atomic::{AtomicU64, Ordering};
