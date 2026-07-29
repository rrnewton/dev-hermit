#![no_main]

use libfuzzer_sys::fuzz_target;
use shmem_pod::injection::BootstrapContext;

fuzz_target!(|data: &[u8]| {
    if let Ok(context) = BootstrapContext::decode(data) {
        context
            .validate()
            .expect("decode returned an invalid context");
        assert_eq!(
            BootstrapContext::decode(&context.encode()).unwrap(),
            context
        );
    }
});
