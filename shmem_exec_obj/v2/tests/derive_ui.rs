#![cfg(feature = "derive")]

#[test]
fn rejects_unsupported_or_unsound_shapes() {
    use std::fmt::Write as _;
    use std::fs;
    use std::process::Command;

    const CASES: &[(&str, &str)] = &[
        ("box", "Box<u64>: PodValue"),
        (
            "drop_type",
            "NeedsDrop cannot implement a pod value capability because it needs drop",
        ),
        ("enum", "pod capability derives support structs only"),
        ("fixed_not_strong", "FixedOnly: PodValue"),
        (
            "fixed_pointer",
            "*const case_fixed_pointer::Target: FixedAddressPodValue",
        ),
        (
            "generic_drop",
            "generic pod capability derives are not supported",
        ),
        ("mutex", "Mutex<u64>: PodValue"),
        ("pointer", "*const case_pointer::Target: PodValue"),
        ("reference", "&'static case_reference::Target: PodValue"),
        ("standard_vec", "Vec<u64>: PodValue"),
        (
            "sync_without_storage",
            "MissingStorageTier: FixedAddressPodValue",
        ),
        ("union", "pod capability derives do not support unions"),
    ];

    let workspace_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let project = workspace_root
        .join("target")
        .join(format!("macro-ui-check-{}", std::process::id()));
    fs::create_dir_all(project.join("src")).unwrap();

    let manifest = format!(
        "[workspace]\n\
         [package]\n\
         name = \"shmem-pod-macro-ui-check\"\n\
         version = \"0.0.0\"\n\
         edition = \"2024\"\n\
         [dependencies]\n\
         shmem-pod = {{ path = {:?} }}\n",
        workspace_root,
    );
    fs::write(project.join("Cargo.toml"), manifest).unwrap();

    let mut source = String::new();
    for (case, _) in CASES {
        let path = workspace_root.join("tests/ui").join(format!("{case}.rs"));
        writeln!(source, "#[path = {:?}] mod case_{case};", path).unwrap();
    }
    fs::write(project.join("src/lib.rs"), source).unwrap();

    let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
        .args(["check", "--quiet", "--offline"])
        .arg("--manifest-path")
        .arg(project.join("Cargo.toml"))
        .env("CARGO_TARGET_DIR", project.join("target"))
        .env("CARGO_TERM_COLOR", "never")
        .output()
        .expect("run compile-fail fixture crate");
    assert!(
        !output.status.success(),
        "unsupported pod shapes unexpectedly compiled"
    );

    let stderr = String::from_utf8(output.stderr).expect("cargo diagnostics are UTF-8");
    for (case, expected) in CASES {
        assert!(
            stderr.contains(expected),
            "{case} did not emit expected diagnostic {expected:?}:\n{stderr}"
        );
    }
}
