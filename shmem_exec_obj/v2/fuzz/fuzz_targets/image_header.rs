#![no_main]

use libfuzzer_sys::fuzz_target;
use shmem_pod_image_api::{DEMO_POD_API, ImageHeader};

fuzz_target!(|data: &[u8]| {
    if let Ok(header) = ImageHeader::decode(data) {
        header
            .validate()
            .expect("decode returned an invalid header");
        let encoded = header.encode().expect("decoded header did not encode");
        assert_eq!(ImageHeader::decode(&encoded).unwrap(), header);
        let _ = header.validate_for_host(&DEMO_POD_API);
    }
});
