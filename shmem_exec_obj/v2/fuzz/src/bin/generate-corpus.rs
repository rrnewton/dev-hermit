use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use shmem_pod::injection::{BOOTSTRAP_PAGE_SIZE, BootstrapContext, BootstrapFlags, ConnectorKind};
use shmem_pod::layout::LayoutDescriptor;
use shmem_pod_image_api::{
    CAP_OFFSET_ARENA, CPU_X86_64_BASELINE, DEMO_POD_API, HARDENING_NX_STATE, HARDENING_W_X,
    HEADER_SIZE, ImageHeader, ImageMetadata, MethodEntry,
};

fn write_seed(root: &Path, target: &str, name: &str, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    let directory = root.join(target);
    fs::create_dir_all(&directory)?;
    fs::write(directory.join(name), bytes)?;
    Ok(())
}

fn valid_artifact() -> Result<(Vec<u8>, [u8; 32]), Box<dyn Error>> {
    let code = vec![0xcc_u8; 1024];
    let code_sha256: [u8; 32] = Sha256::digest(&code).into();
    let methods = DEMO_POD_API
        .methods
        .iter()
        .enumerate()
        .map(|(index, specification)| MethodEntry {
            id: specification.id,
            signature: specification.signature as u16,
            offset: (HEADER_SIZE + index * 32) as u64,
            size: 16,
            alignment: 1,
            ..MethodEntry::default()
        })
        .collect();
    let header = ImageHeader::new(
        code.len(),
        16,
        code_sha256,
        ImageMetadata {
            api_fingerprint: DEMO_POD_API.fingerprint,
            state_fingerprint: 1,
            build_sha256: [2; 32],
            provenance_sha256: [3; 32],
            required_capabilities: CAP_OFFSET_ARENA,
            optional_capabilities: 0,
            required_hardening: HARDENING_W_X | HARDENING_NX_STATE,
            required_cpu_features: CPU_X86_64_BASELINE,
            required_state_address: 0,
        },
        methods,
    )?;
    let mut image = header.encode()?.to_vec();
    image.extend_from_slice(&code);
    let digest = Sha256::digest(&image).into();
    Ok((image, digest))
}

fn main() -> Result<(), Box<dyn Error>> {
    let root = std::env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("fuzz/corpus"));
    let (artifact, _) = valid_artifact()?;
    write_seed(
        &root,
        "image_header",
        "valid-header",
        &artifact[..HEADER_SIZE],
    )?;
    write_seed(&root, "image_header", "truncated", &artifact[..31])?;
    write_seed(&root, "pod_artifact", "valid-artifact", &artifact)?;
    write_seed(&root, "pod_artifact", "empty", &[])?;

    let context = BootstrapContext::new(
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
    )?;
    write_seed(
        &root,
        "bootstrap_context",
        "valid-context",
        &context.encode(),
    )?;
    write_seed(
        &root,
        "bootstrap_context",
        "short-context",
        &context.encode()[..17],
    )?;

    let layout = LayoutDescriptor::of::<u64>().encode();
    write_seed(&root, "layout_descriptor", "u64-layout", &layout)?;
    write_seed(&root, "layout_descriptor", "empty", &[])?;

    let mut valid_offset = Vec::new();
    valid_offset.extend_from_slice(&0_u64.to_le_bytes());
    valid_offset.extend_from_slice(&4_u64.to_le_bytes());
    valid_offset.extend_from_slice(&64_u64.to_le_bytes());
    write_seed(&root, "offset_resolution", "aligned-slice", &valid_offset)?;
    write_seed(&root, "offset_resolution", "max-fields", &[0xff; 24])?;
    Ok(())
}
