#![no_main]

use libfuzzer_sys::fuzz_target;
use shmem_pod::layout::LayoutDescriptor;

fuzz_target!(|data: &[u8]| {
    if let Ok(descriptor) = LayoutDescriptor::decode(data) {
        assert_eq!(
            LayoutDescriptor::decode(&descriptor.encode()).unwrap(),
            descriptor
        );
        let _ = descriptor.validate::<u64>();
    }
});
