use core::mem::{align_of, size_of};
use shmem_pod::offset::{Offset, OffsetSlice, PodRegion, ResolveError};
use shmem_pod::{PodSync, PodValue};

fn require_pod<T: PodValue + PodSync>() {}

#[test]
fn resolves_objects_and_slices_at_different_mapping_bases() {
    require_pod::<Offset<u64>>();
    require_pod::<OffsetSlice<u64>>();

    let mut first = [0_u64; 8];
    let mut second = [0_u64; 8];
    first[3..6].copy_from_slice(&[11, 22, 33]);
    second[3..6].copy_from_slice(&[11, 22, 33]);
    let offset: Offset<u64> = Offset::new((3 * size_of::<u64>()) as u64).unwrap();
    let slice = OffsetSlice::new(offset, 3).unwrap();

    let first_region = unsafe {
        PodRegion::from_raw_parts(first.as_mut_ptr().cast(), size_of::<[u64; 8]>()).unwrap()
    };
    let second_region = unsafe {
        PodRegion::from_raw_parts(second.as_mut_ptr().cast(), size_of::<[u64; 8]>()).unwrap()
    };

    assert_ne!(first_region.base(), second_region.base());
    assert_eq!(*unsafe { first_region.get(offset) }.unwrap().unwrap(), 11);
    assert_eq!(
        unsafe { first_region.get_slice(slice) }.unwrap().unwrap(),
        [11, 22, 33]
    );
    assert_eq!(
        unsafe { second_region.get_slice(slice) }.unwrap().unwrap(),
        [11, 22, 33]
    );
}

#[test]
fn null_and_malformed_null_are_distinct() {
    let mut words = [0_u64; 2];
    let region = unsafe {
        PodRegion::from_raw_parts(words.as_mut_ptr().cast(), size_of_val(&words)).unwrap()
    };
    assert!(
        unsafe { region.get(Offset::<u64>::null()) }
            .unwrap()
            .is_none()
    );
    assert!(
        unsafe { region.get_slice(OffsetSlice::<u64>::null()) }
            .unwrap()
            .is_none()
    );
    assert_eq!(
        unsafe { region.get_slice(OffsetSlice::<u64>::from_raw(u64::MAX, 1)) },
        Err(ResolveError::NullWithNonzeroLength)
    );
}

#[test]
fn rejects_misalignment_out_of_bounds_and_length_overflow() {
    #[repr(align(8))]
    struct Aligned([u8; 32]);

    let mut bytes = Aligned([0; 32]);
    let region = unsafe { PodRegion::from_raw_parts(bytes.0.as_mut_ptr(), bytes.0.len()).unwrap() };
    assert!(matches!(
        unsafe { region.get(Offset::<u64>::new(1).unwrap()) },
        Err(ResolveError::Misaligned { required, .. }) if required == align_of::<u64>()
    ));
    assert!(matches!(
        unsafe { region.get(Offset::<u64>::new(32).unwrap()) },
        Err(ResolveError::OutOfBounds { .. })
    ));
    assert_eq!(
        unsafe { region.get_slice(OffsetSlice::<u64>::from_raw(0, u64::MAX)) },
        Err(ResolveError::LengthOverflow)
    );
}

#[test]
fn mutable_resolution_requires_an_explicit_unsafe_boundary() {
    let mut words = [1_u64, 2, 3];
    let mut region = unsafe {
        PodRegion::from_raw_parts(words.as_mut_ptr().cast(), size_of_val(&words)).unwrap()
    };
    let second: Offset<u64> = Offset::new(size_of::<u64>() as u64).unwrap();
    *unsafe { region.get_mut(second) }.unwrap().unwrap() = 20;
    let tail = OffsetSlice::new(second, 2).unwrap();
    unsafe { region.get_slice_mut(tail) }.unwrap().unwrap()[1] = 30;
    assert_eq!(words, [1, 20, 30]);
}
