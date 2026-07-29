#![no_main]

use libfuzzer_sys::fuzz_target;
use sha2::{Digest, Sha256};
use shmem_pod_runtime::PodArtifact;

const MAX_FUZZ_ARTIFACT: usize = 2 * 1024 * 1024;

fuzz_target!(|data: &[u8]| {
    if data.len() > MAX_FUZZ_ARTIFACT {
        return;
    }
    let digest: [u8; 32] = Sha256::digest(data).into();
    if let Ok(artifact) = PodArtifact::from_bytes(data.to_vec(), digest) {
        assert_eq!(artifact.digest(), digest);
        assert_eq!(artifact.len(), data.len());
        assert!(!artifact.is_empty());
    }
});
