//! Fork-, thread-, FFI-, and mmap-free Miri coverage for byte parsers and offsets.

use core::mem::size_of;

use shmem_pod::injection::{
    BOOTSTRAP_PAGE_SIZE, BootstrapContext, BootstrapError, BootstrapFlags, ConnectorKind,
};
use shmem_pod::layout::{DecodeError, LayoutDescriptor};
use shmem_pod::offset::{Offset, OffsetSlice, PodRegion, ResolveError};

fn bootstrap_context() -> BootstrapContext {
    BootstrapContext::new(
        ConnectorKind::Preload,
        BootstrapFlags::REQUIRED.union(BootstrapFlags::INHERIT_ACROSS_EXEC),
        10,
        11,
        12,
        BOOTSTRAP_PAGE_SIZE * 2,
        BOOTSTRAP_PAGE_SIZE * 4,
        7,
        0x1234,
        [0x5a; 32],
        [0xa5; 16],
    )
    .unwrap()
}

#[test]
fn bootstrap_context_round_trip_is_pure() {
    let context = bootstrap_context();
    assert_eq!(BootstrapContext::decode(&context.encode()), Ok(context));
}

#[test]
fn bootstrap_context_rejects_corrupt_bytes() {
    let encoded = bootstrap_context().encode();
    assert_eq!(
        BootstrapContext::decode(&encoded[..encoded.len() - 1]),
        Err(BootstrapError::EncodedLength(encoded.len() - 1))
    );
    let mut bad_magic = encoded;
    bad_magic[0] ^= 0xff;
    assert_eq!(
        BootstrapContext::decode(&bad_magic),
        Err(BootstrapError::BadMagic)
    );
}

#[test]
fn layout_descriptor_round_trip_and_rejection_are_pure() {
    let descriptor = LayoutDescriptor::of::<u64>();
    let encoded = descriptor.encode();
    assert_eq!(LayoutDescriptor::decode(&encoded), Ok(descriptor));
    assert_eq!(
        LayoutDescriptor::decode(&encoded[..encoded.len() - 1]),
        Err(DecodeError::Truncated {
            expected: LayoutDescriptor::ENCODED_LEN,
            actual: LayoutDescriptor::ENCODED_LEN - 1,
        })
    );
}

#[test]
fn checked_offsets_preserve_strict_provenance() {
    let mut words = [11_u64, 22, 33, 44];
    let mut region = unsafe {
        PodRegion::from_raw_parts(words.as_mut_ptr().cast(), size_of_val(&words)).unwrap()
    };
    let second = Offset::<u64>::new(size_of::<u64>() as u64).unwrap();
    assert_eq!(*unsafe { region.get(second) }.unwrap().unwrap(), 22);
    let tail = OffsetSlice::new(second, 3).unwrap();
    assert_eq!(
        unsafe { region.get_slice(tail) }.unwrap().unwrap(),
        [22, 33, 44]
    );
    *unsafe { region.get_mut(second) }.unwrap().unwrap() = 29;
    assert_eq!(words[1], 29);
    assert_eq!(
        unsafe { region.get(Offset::<u64>::new((words.len() * size_of::<u64>()) as u64).unwrap()) },
        Err(ResolveError::OutOfBounds {
            offset: (words.len() * size_of::<u64>()) as u64,
            byte_len: size_of::<u64>(),
            region_len: size_of_val(&words),
        })
    );
}
