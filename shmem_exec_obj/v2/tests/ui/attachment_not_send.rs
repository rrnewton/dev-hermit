use core::marker::PhantomData;
use std::sync::MutexGuard;

use shmem_pod::mapping::Attachment;
use shmem_pod::{FixedAddressPodValue, PodSync, PodValue};

struct SyncNotSend(PhantomData<MutexGuard<'static, ()>>);

// SAFETY: this compile-only ZST stores no address or value and needs no drop.
unsafe impl FixedAddressPodValue for SyncNotSend {
    const FINGERPRINT: u128 = 1;
}

// SAFETY: the ZST representation stores no address.
unsafe impl PodValue for SyncNotSend {}

// SAFETY: MutexGuard's marker is Sync but deliberately not Send.
unsafe impl PodSync for SyncNotSend {}

fn require_send<T: Send>() {}

fn main() {
    require_send::<Attachment<'static, SyncNotSend>>();
}
