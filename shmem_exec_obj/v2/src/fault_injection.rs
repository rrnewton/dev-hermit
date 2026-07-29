//! Test-only deterministic process stop points.

use core::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub(crate) enum FaultPoint {
    CloseableEntryReserved = 1,
    CloseableArrivalPublished = 2,
    CloseableDepartureReserved = 3,
    CloseableCheckingPublished = 4,
    CloseableDrainScanned = 5,
    CloseableDrainSealed = 6,
    SnziHalfPublished = 7,
    SnziNodePublished = 8,
    SnziNodeIncremented = 9,
    SnziNodeDecremented = 10,
    SnziRootArrived = 11,
    SnziRootDeparted = 12,
    SnziBeforeCompensation = 13,
    CsnziNodeArrived = 14,
    CsnziBeforeCompensation = 15,
    CsnziNodeDeparted = 16,
    CsnziRootArrived = 17,
    CsnziRootDeparted = 18,
    CsnziCloseClaimedEmpty = 19,
    CsnziCloseMarkedNonempty = 20,
    CsnziCloseConvertedTail = 21,
    CsnziCloseSealed = 22,
    CsnziDepartureTailSealed = 23,
    FutexAcquired = 24,
    FutexContended = 25,
    FutexReleased = 26,
}

const ANY_DETAIL: usize = usize::MAX;
static ARMED_POINT: AtomicU32 = AtomicU32::new(0);
static ARMED_DETAIL: AtomicUsize = AtomicUsize::new(ANY_DETAIL);
static ARMED_OCCURRENCE: AtomicUsize = AtomicUsize::new(1);
static MATCHES: AtomicUsize = AtomicUsize::new(0);
static NEXT_POINT: AtomicU32 = AtomicU32::new(0);
static NEXT_DETAIL: AtomicUsize = AtomicUsize::new(ANY_DETAIL);

pub(crate) fn arm(point: FaultPoint, detail: usize, occurrence: usize) {
    assert!(occurrence != 0);
    MATCHES.store(0, Ordering::Relaxed);
    ARMED_DETAIL.store(detail, Ordering::Relaxed);
    ARMED_OCCURRENCE.store(occurrence, Ordering::Relaxed);
    NEXT_POINT.store(0, Ordering::Relaxed);
    ARMED_POINT.store(point as u32, Ordering::Release);
}

pub(crate) fn arm_sequence(
    first: FaultPoint,
    first_detail: usize,
    second: FaultPoint,
    second_detail: usize,
) {
    arm(first, first_detail, 1);
    NEXT_DETAIL.store(second_detail, Ordering::Relaxed);
    NEXT_POINT.store(second as u32, Ordering::Release);
}

pub(crate) fn any_detail() -> usize {
    ANY_DETAIL
}

pub(crate) fn hit(point: FaultPoint, detail: usize) {
    if ARMED_POINT.load(Ordering::Acquire) != point as u32 {
        return;
    }
    let expected_detail = ARMED_DETAIL.load(Ordering::Relaxed);
    if expected_detail != ANY_DETAIL && expected_detail != detail {
        return;
    }
    if MATCHES.fetch_add(1, Ordering::Relaxed) + 1 != ARMED_OCCURRENCE.load(Ordering::Relaxed) {
        return;
    }

    let next = NEXT_POINT.swap(0, Ordering::AcqRel);
    // SAFETY: SIGSTOP has no handler and stops only this forked test process.
    // The parent observes the stop with waitpid(WUNTRACED). A sequenced cut can
    // be resumed once to arm its second point; ordinary cuts are killed.
    unsafe { libc::raise(libc::SIGSTOP) };
    if next != 0 {
        MATCHES.store(0, Ordering::Relaxed);
        ARMED_DETAIL.store(NEXT_DETAIL.load(Ordering::Relaxed), Ordering::Relaxed);
        ARMED_OCCURRENCE.store(1, Ordering::Relaxed);
        ARMED_POINT.store(next, Ordering::Release);
        return;
    }
    // SAFETY: reaching this path means a test resumed a one-shot cut instead of
    // killing it. Exit without running inherited destructors.
    unsafe { libc::_exit(125) }
}
