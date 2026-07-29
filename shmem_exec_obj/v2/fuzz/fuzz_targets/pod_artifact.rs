#![no_main]

use libfuzzer_sys::fuzz_target;
use sha2::{Digest, Sha256};
use shmem_pod_runtime::PodArtifact;

const MAX_FUZZ_ARTIFACT: usize = 2 * 1024 * 1024;

fuzz_target!(|data: &[u8]| {
    if data.len() < 32 || data.len() - 32 > MAX_FUZZ_ARTIFACT {
        return;
    }
    let mut expected_digest = [0_u8; 32];
    expected_digest.copy_from_slice(&data[..32]);
    let bytes = &data[32..];

    if let Ok(artifact) = PodArtifact::from_bytes(bytes.to_vec(), expected_digest) {
        assert_eq!(artifact.digest(), expected_digest);
        assert_eq!(artifact.len(), bytes.len());
        assert!(!artifact.is_empty());
    }

    let computed_digest: [u8; 32] = Sha256::digest(bytes).into();
    if let Ok(artifact) = PodArtifact::from_bytes(bytes.to_vec(), computed_digest) {
        assert_eq!(artifact.digest(), computed_digest);
        assert_eq!(artifact.len(), bytes.len());
        assert!(!artifact.is_empty());
    }
});
