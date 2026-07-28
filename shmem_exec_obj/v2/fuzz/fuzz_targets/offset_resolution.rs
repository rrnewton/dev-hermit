#![no_main]

use libfuzzer_sys::fuzz_target;
use shmem_pod::offset::{Offset, OffsetSlice, PodRegion};

fuzz_target!(|data: &[u8]| {
    let mut fields = [0_u64; 3];
    for (index, chunk) in data.chunks(8).take(fields.len()).enumerate() {
        let mut bytes = [0_u8; 8];
        bytes[..chunk.len()].copy_from_slice(chunk);
        fields[index] = u64::from_le_bytes(bytes);
    }

    let mut storage = [0_u64; 64];
    let region_len = usize::try_from(fields[2] % (storage.len() as u64 * 8 + 1)).unwrap();
    // SAFETY: storage is a live aligned allocation, every u64 is initialized,
    // and region_len is bounded by its complete byte extent.
    let region = unsafe {
        PodRegion::from_raw_parts(storage.as_mut_ptr().cast::<u8>(), region_len).unwrap()
    };
    // SAFETY: successful bounds/alignment checks can only select initialized
    // bytes within storage. Failed checks form no reference.
    let _ = unsafe { region.get(Offset::<u64>::from_raw(fields[0])) };
    // SAFETY: the same initialized storage and checked extent contract applies.
    let _ = unsafe { region.get_slice(OffsetSlice::<u64>::from_raw(fields[0], fields[1])) };
});
