use core::mem::{align_of, size_of};

use shmem_pod::FixedAddressPodValue;
use shmem_pod::layout::{DecodeError, LayoutDescriptor, LayoutField};

const FINGERPRINT_START: usize = 16;
const SIZE_START: usize = 32;
const ALIGNMENT_START: usize = 40;

#[cfg(feature = "derive")]
#[derive(shmem_pod::PodValue)]
struct RustLayout {
    narrow: u8,
    wide: u64,
}

struct MaterializedFingerprint;

// SAFETY: This zero-sized test type has no destructor or transitive fields.
unsafe impl FixedAddressPodValue for MaterializedFingerprint {
    const FINGERPRINT: u128 = {
        assert!(!core::mem::needs_drop::<Self>());
        0x0123_4567_89ab_cdef_fedc_ba98_7654_3210
    };
}

#[test]
fn exact_wire_encoding_round_trips() {
    let descriptor = LayoutDescriptor::of::<u64>();
    let encoded = descriptor.encode();
    let mut expected = [0; LayoutDescriptor::ENCODED_LEN];
    expected[0..8].copy_from_slice(b"SHMPODL\0");
    expected[8..10].copy_from_slice(&[1, 0]);
    expected[FINGERPRINT_START..SIZE_START].copy_from_slice(&u64::FINGERPRINT.to_le_bytes());
    expected[SIZE_START..ALIGNMENT_START].copy_from_slice(&(size_of::<u64>() as u64).to_le_bytes());
    expected[ALIGNMENT_START..LayoutDescriptor::ENCODED_LEN]
        .copy_from_slice(&(align_of::<u64>() as u64).to_le_bytes());

    assert_eq!(LayoutDescriptor::ENCODED_LEN, 48);
    assert_eq!(LayoutDescriptor::MAGIC, *b"SHMPODL\0");
    assert_eq!(LayoutDescriptor::VERSION, 1);
    assert_eq!(encoded, expected);
    assert_eq!(LayoutDescriptor::decode(&encoded), Ok(descriptor));
}

#[test]
fn validates_matches_and_reports_the_differing_field_and_layouts() {
    let descriptor = LayoutDescriptor::of::<u64>();
    assert!(descriptor.matches::<u64>());
    assert!(!descriptor.matches::<u32>());
    assert_eq!(descriptor.validate::<u64>(), Ok(()));

    let mismatch = descriptor.validate::<u32>().unwrap_err();
    assert_eq!(mismatch.field(), LayoutField::Fingerprint);
    assert_eq!(mismatch.expected(), LayoutDescriptor::of::<u32>());
    assert_eq!(mismatch.found(), descriptor);
    assert!(!mismatch.to_string().is_empty());

    let mut wrong_size = descriptor.encode();
    wrong_size[SIZE_START..ALIGNMENT_START].copy_from_slice(&(descriptor.size() + 1).to_le_bytes());
    let wrong_size = LayoutDescriptor::decode(&wrong_size).unwrap();
    assert_eq!(
        wrong_size.validate::<u64>().unwrap_err().field(),
        LayoutField::Size
    );

    let mut wrong_alignment = descriptor.encode();
    let different_alignment: u64 = if descriptor.alignment() == 1 { 2 } else { 1 };
    wrong_alignment[ALIGNMENT_START..LayoutDescriptor::ENCODED_LEN]
        .copy_from_slice(&different_alignment.to_le_bytes());
    let wrong_alignment = LayoutDescriptor::decode(&wrong_alignment).unwrap();
    assert_eq!(
        wrong_alignment.validate::<u64>().unwrap_err().field(),
        LayoutField::Alignment
    );
}

#[test]
fn rejects_each_corrupt_header_class() {
    let encoded = LayoutDescriptor::of::<u64>().encode();
    assert_eq!(
        LayoutDescriptor::decode(&encoded[..encoded.len() - 1]),
        Err(DecodeError::Truncated {
            expected: LayoutDescriptor::ENCODED_LEN,
            actual: LayoutDescriptor::ENCODED_LEN - 1,
        })
    );

    let mut with_trailing_byte = encoded.to_vec();
    with_trailing_byte.push(0);
    assert_eq!(
        LayoutDescriptor::decode(&with_trailing_byte),
        Err(DecodeError::TrailingBytes {
            expected: LayoutDescriptor::ENCODED_LEN,
            actual: LayoutDescriptor::ENCODED_LEN + 1,
        })
    );

    let mut bad_magic = encoded;
    bad_magic[0] ^= 0xff;
    assert!(matches!(
        LayoutDescriptor::decode(&bad_magic),
        Err(DecodeError::BadMagic { .. })
    ));

    let mut bad_version = encoded;
    bad_version[8..10].copy_from_slice(&(LayoutDescriptor::VERSION + 1).to_le_bytes());
    assert_eq!(
        LayoutDescriptor::decode(&bad_version),
        Err(DecodeError::UnsupportedVersion {
            found: LayoutDescriptor::VERSION + 1,
        })
    );

    let mut bad_reserved = encoded;
    bad_reserved[13] = 7;
    assert_eq!(
        LayoutDescriptor::decode(&bad_reserved),
        Err(DecodeError::NonzeroReserved {
            offset: 13,
            value: 7,
        })
    );

    for invalid in [0_u64, 3] {
        let mut bad_alignment = encoded;
        bad_alignment[ALIGNMENT_START..LayoutDescriptor::ENCODED_LEN]
            .copy_from_slice(&invalid.to_le_bytes());
        assert_eq!(
            LayoutDescriptor::decode(&bad_alignment),
            Err(DecodeError::InvalidAlignment { alignment: invalid })
        );
    }
}

#[cfg(target_pointer_width = "32")]
#[test]
fn rejects_sizes_not_representable_by_the_target() {
    let mut encoded = LayoutDescriptor::of::<u64>().encode();
    let invalid = u64::from(u32::MAX) + 1;
    encoded[SIZE_START..ALIGNMENT_START].copy_from_slice(&invalid.to_le_bytes());
    assert_eq!(
        LayoutDescriptor::decode(&encoded),
        Err(DecodeError::SizeOutOfRange { size: invalid })
    );
}

#[cfg(target_pointer_width = "64")]
#[test]
fn every_wire_size_is_representable_by_the_target() {
    assert_eq!(usize::MAX as u128, u64::MAX as u128);
}

#[test]
#[cfg(feature = "derive")]
fn describes_a_compiler_selected_repr_rust_layout() {
    let descriptor = LayoutDescriptor::of::<RustLayout>();
    assert_eq!(descriptor.fingerprint(), RustLayout::FINGERPRINT);
    assert_eq!(descriptor.size(), size_of::<RustLayout>() as u64);
    assert_eq!(descriptor.alignment(), align_of::<RustLayout>() as u64);
    assert_eq!(
        LayoutDescriptor::decode(&descriptor.encode())
            .unwrap()
            .validate::<RustLayout>(),
        Ok(())
    );
}

#[test]
fn constructor_materializes_the_no_drop_checked_fingerprint() {
    let descriptor = LayoutDescriptor::of::<MaterializedFingerprint>();
    assert_eq!(
        descriptor.fingerprint(),
        MaterializedFingerprint::FINGERPRINT
    );
}
