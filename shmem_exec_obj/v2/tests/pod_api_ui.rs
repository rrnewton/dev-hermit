#![cfg(feature = "derive")]

#[test]
fn rejects_ambiguous_or_unsupported_native_apis() {
    use std::fmt::Write as _;
    use std::fs;
    use std::process::Command;

    const CASES: &[(&str, &str)] = &[
        ("pod_api_async", "pod methods cannot be async"),
        ("pod_api_duplicate_id", "duplicate pod method ID 1"),
        ("pod_api_duplicate_symbol", "duplicate pod export symbol"),
        ("pod_api_generic", "pod methods cannot be generic"),
        ("pod_api_missing_method", "pod method requires"),
        ("pod_api_non_c", "supports extern \"C\" only"),
        ("pod_api_unsupported_type", "unsupported pod ABI type"),
        ("pod_api_variadic", "pod methods cannot be variadic"),
    ];

    let workspace_root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let project = workspace_root
        .join("target")
        .join(format!("pod-api-ui-check-{}", std::process::id()));
    fs::create_dir_all(project.join("src")).unwrap();
    let manifest = format!(
        "[workspace]\n\
         [package]\n\
         name = \"shmem-pod-api-ui-check\"\n\
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
        writeln!(source, "#[path = {:?}] mod {case};", path).unwrap();
    }
    fs::write(project.join("src/lib.rs"), source).unwrap();

    let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
        .args(["check", "--quiet", "--offline"])
        .arg("--manifest-path")
        .arg(project.join("Cargo.toml"))
        .env("CARGO_TARGET_DIR", project.join("target"))
        .env("CARGO_TERM_COLOR", "never")
        .output()
        .expect("run pod API compile-fail fixtures");
    assert!(
        !output.status.success(),
        "invalid pod APIs unexpectedly compiled"
    );

    let stderr = String::from_utf8(output.stderr).expect("cargo diagnostics are UTF-8");
    for (case, expected) in CASES {
        assert!(
            stderr.contains(expected),
            "fixture {case} did not emit {expected:?}:\n{stderr}"
        );
    }
}
