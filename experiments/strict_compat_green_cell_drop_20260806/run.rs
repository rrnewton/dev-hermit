#!/usr/bin/env rust-script
//! ```cargo
//! [dependencies]
//! anyhow = "1"
//! nix = { version = "0.29", features = ["process", "signal"] }
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! sha2 = "0.10"
//! shell-words = "1"
//! wait-timeout = "0.2"
//! ```

use anyhow::{bail, Context, Result};
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use wait_timeout::ChildExt;

const SCHEMA: u32 = 2;
const FULL_TESTS: usize = 234;
const BACKENDS: [&str; 6] = ["ptrace", "kvm", "dbi", "sabre", "e9patch", "liteinst"];
const MAIN_MODE: &str = "strict_raw";
const CALIBRATION_TEST: &str = "backend-parity-c/pid-probe";
const SPOT_TESTS: [&str; 2] = ["backend-parity-c/pid-probe", "c-programs/print-memaddrs"];

#[derive(Debug, Clone)]
struct Paths {
    parent_repo: PathBuf,
    experiment: PathBuf,
    artifacts: PathBuf,
    hermit_repo: PathBuf,
    reverie_repo: PathBuf,
    liteinst_repo: PathBuf,
    binary: PathBuf,
    corpus_c: PathBuf,
    corpus_nonc: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct SourceFact {
    role: String,
    path: String,
    sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct ToolchainReceipt {
    cc_argv: Vec<String>,
    cc_output_sha256: String,
    rustc_argv: Vec<String>,
    rustc_output_sha256: String,
    cargo_argv: Vec<String>,
    cargo_output_sha256: String,
    receipt_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CompileReceipt {
    command: Vec<String>,
    command_sha256: String,
    inputs: Vec<SourceFact>,
    toolchain_receipt_sha256: String,
    exit_code: i32,
    log: ArtifactFact,
    output: ArtifactFact,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HermitBuildReceipt {
    source_sha: String,
    source_tree: String,
    cargo_lock_sha256: String,
    clean_before: bool,
    clean_after: bool,
    binary_absent_before: bool,
    command: Vec<String>,
    command_sha256: String,
    toolchain_receipt_sha256: String,
    exit_code: i32,
    log: ArtifactFact,
    output: ArtifactFact,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CompileSpec {
    source: String,
    cflags: Vec<String>,
    extra_sources: Vec<String>,
    command: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct TestSpec {
    id: String,
    lane: String,
    kind: String,
    argv: Vec<String>,
    source_sha256: String,
    binary_sha256: String,
    workload_identity_sha256: String,
    compile: Option<CompileSpec>,
    compile_receipt: Option<CompileReceipt>,
}

#[derive(Debug, Serialize, Deserialize)]
struct FrozenManifest {
    schema: u32,
    record_type: String,
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
    corpus_c_sha256: String,
    corpus_nonc_sha256: String,
    parent_commit: String,
    run_rs_sha256: String,
    verify_rs_sha256: String,
    toolchain: ToolchainReceipt,
    hermit_build: HermitBuildReceipt,
    guest_set_sha256: String,
    tests: Vec<TestSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct Termination {
    kind: String,
    code: Option<i32>,
    signal: Option<i32>,
    timed_out: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ArtifactFact {
    path: String,
    sha256: String,
    bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ExecutionRecord {
    schema: u32,
    record_type: String,
    run_kind: String,
    run_instance: String,
    run_binding_sha256: String,
    cell_id: String,
    test_id: String,
    backend: String,
    observation_mode: String,
    ordinal: u8,
    attempted: bool,
    termination: Termination,
    duration_ms: u128,
    log_event_count: u64,
    stdout: ArtifactFact,
    stderr: ArtifactFact,
    ordered_log_stream: ArtifactFact,
    command: Vec<String>,
    command_sha256: String,
    guest_binary_sha256: String,
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CellRecord {
    schema: u32,
    record_type: String,
    run_kind: String,
    run_instance: String,
    run_binding_sha256: String,
    cell_id: String,
    test_id: String,
    backend: String,
    observation_mode: String,
    execution_ordinals: Vec<u8>,
    stdout_equal: bool,
    stderr_equal: bool,
    termination_equal: bool,
    ordered_log_stream_equal: bool,
    nonzero_log_events: bool,
    successful_exit_both: bool,
    raw_observations_equal: bool,
    strict_green: bool,
    legacy_log_match: bool,
    legacy_green: bool,
    legacy_comparator: String,
    legacy_diagnostic: ArtifactFact,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Denominator {
    schema: u32,
    record_type: String,
    run_kind: String,
    run_instance: String,
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
    parent_commit: String,
    run_rs_sha256: String,
    verify_rs_sha256: String,
    guest_set_sha256: String,
    input_audit_path: String,
    input_audit_sha256: String,
    requested_manifest_path: String,
    requested_manifest_sha256: String,
    manifest_path: String,
    manifest_sha256: String,
    corpus_c_sha256: String,
    corpus_nonc_sha256: String,
    tests: Vec<String>,
    backends: Vec<String>,
    observation_modes: Vec<String>,
    run_ordinals: Vec<u8>,
    expected_cells: usize,
    expected_executions: usize,
    required_spot_completion: Option<EvidenceFile>,
    comparison_contract: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct EvidenceFile {
    path: String,
    sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RunBinding {
    schema: u32,
    record_type: String,
    run_kind: String,
    run_instance: String,
    parent_commit: String,
    run_rs_sha256: String,
    verify_rs_sha256: String,
    requested_manifest_sha256: String,
    input_audit_sha256: String,
    manifest_sha256: String,
    denominator_sha256: String,
    hermit_binary_sha256: String,
    guest_set_sha256: String,
    required_spot_completion: Option<EvidenceFile>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedSource {
    role: String,
    path: String,
    exists: bool,
    sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedInput {
    id: String,
    lane: String,
    kind: String,
    corpus_file: String,
    corpus_line: usize,
    corpus_row: String,
    argv: Vec<String>,
    compile_command: Vec<String>,
    workload_identity_sha256: String,
    sources: Vec<RequestedSource>,
    available: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedManifest {
    schema: u32,
    record_type: String,
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
    corpus_c_sha256: String,
    corpus_nonc_sha256: String,
    denominator_expected_tests: usize,
    inputs: Vec<RequestedInput>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct MissingInputSource {
    test_id: String,
    corpus_file: String,
    corpus_line: usize,
    role: String,
    path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct InputAudit {
    schema: u32,
    record_type: String,
    complete: bool,
    full_sweep_allowed: bool,
    denominator_expected_tests: usize,
    denominator_observed_rows: usize,
    denominator_unique_ids: usize,
    denominator_executable_tests: usize,
    c_rows: usize,
    nonc_rows: usize,
    missing_test_count: usize,
    missing_source_count: usize,
    missing_test_ids: Vec<String>,
    missing_sources: Vec<MissingInputSource>,
    duplicate_ids: Vec<String>,
    duplicate_workload_identities: Vec<String>,
    requested_manifest_path: String,
    requested_manifest_sha256: String,
    executable_manifest_path: Option<String>,
    executable_manifest_sha256: Option<String>,
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
    corpus_c_sha256: String,
    corpus_nonc_sha256: String,
}

#[derive(Debug, Clone)]
struct Job {
    run_kind: String,
    test: TestSpec,
    backend: String,
    observation_mode: String,
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let command = args.next().unwrap_or_else(|| "help".to_owned());
    let rest: Vec<String> = args.collect();
    let paths = Paths::from_args(&rest)?;
    match command.as_str() {
        "audit-inputs" => audit_inputs_command(&paths),
        "prepare" => prepare(&paths, value_usize(&rest, "--jobs", 32)),
        "run" => run_jobs(
            &paths,
            value_string(&rest, "--kind", "full"),
            required_slug_value(&rest, "--run-instance")?,
            optional_slug_value(&rest, "--spot-run-instance")?,
            value_usize(&rest, "--jobs", 16),
            value_u64(&rest, "--timeout-seconds", 120),
        ),
        "assemble" => assemble(
            &paths,
            &value_string(&rest, "--kind", "full"),
            &required_slug_value(&rest, "--run-instance")?,
        ),
        "help" | "--help" | "-h" => {
            print_help();
            Ok(())
        }
        other => bail!("unknown command {other:?}; use help"),
    }
}

impl Paths {
    fn from_args(args: &[String]) -> Result<Self> {
        let experiment = path_value(
            args,
            "--experiment",
            "/home/newton/work/dev-hermit/experiments/strict_compat_green_cell_drop_20260806",
        );
        let hermit_repo = path_value(
            args,
            "--hermit",
            "/home/newton/work/dev-hermit/worktrees/strict-metric/hermit",
        );
        Ok(Self {
            parent_repo: path_value(args, "--parent", "/home/newton/work/dev-hermit"),
            artifacts: path_value(
                args,
                "--artifacts",
                "/home/newton/work/dev-hermit/worktrees/strict-metric/hermit/ignored/strict-metric/raw",
            ),
            reverie_repo: path_value(
                args,
                "--reverie",
                "/home/newton/work/dev-hermit/worktrees/strict-metric/reverie",
            ),
            liteinst_repo: path_value(
                args,
                "--liteinst2",
                "/home/newton/work/dev-hermit/worktrees/strict-metric/liteinst2",
            ),
            binary: path_value(
                args,
                "--binary",
                &hermit_repo.join("target/release/hermit").display().to_string(),
            ),
            corpus_c: path_value(
                args,
                "--corpus-c",
                "/home/newton/work/dev-hermit/compat-envelope/corpus/corpus-c.tsv",
            ),
            corpus_nonc: path_value(
                args,
                "--corpus-nonc",
                "/home/newton/work/dev-hermit/compat-envelope/corpus/corpus-nonc.tsv",
            ),
            experiment,
            hermit_repo,
        })
    }
}

fn print_help() {
    println!(
        "strict raw compatibility metric\n\
         audit-inputs                 freeze requested rows and fail closed if any source is absent\n\
         prepare [--jobs 32]        freeze inputs and compile 234 guests\n\
         run --kind calibration     run 1 test x 6 paths x 2 ordinals\n\
         run --kind full            run 234 x 6 x 2 = 2,808 executions\n\
         run --kind spot            run 2 short tests x 6 x 3 L3 modes x 2\n\
         assemble --kind KIND       deterministically write typed JSONL\n\
         run/assemble require --run-instance; full also requires --spot-run-instance\n\
         common overrides: --parent --experiment --artifacts --hermit --reverie \
         --liteinst2 --binary --corpus-c --corpus-nonc"
    );
}

fn audit_inputs_command(paths: &Paths) -> Result<()> {
    require_repo_at_head(&paths.hermit_repo)?;
    require_repo_at_head(&paths.reverie_repo)?;
    require_repo_at_head(&paths.liteinst_repo)?;
    fs::create_dir_all(&paths.experiment)?;
    let audit = freeze_requested_inputs(paths)?;
    println!(
        "input audit complete={} expected={} observed={} unique={} executable={} missing_tests={} missing_sources={} requested_manifest_sha256={}",
        audit.complete,
        audit.denominator_expected_tests,
        audit.denominator_observed_rows,
        audit.denominator_unique_ids,
        audit.denominator_executable_tests,
        audit.missing_test_count,
        audit.missing_source_count,
        audit.requested_manifest_sha256
    );
    if !audit.complete {
        bail!(
            "full sweep refused: typed preflight evidence is {}",
            paths.experiment.join("input-audit.json").display()
        );
    }
    Ok(())
}

fn freeze_requested_inputs(paths: &Paths) -> Result<InputAudit> {
    fs::create_dir_all(&paths.experiment)?;
    fs::copy(
        &paths.corpus_c,
        paths.experiment.join("frozen-corpus-c.tsv"),
    )?;
    fs::copy(
        &paths.corpus_nonc,
        paths.experiment.join("frozen-corpus-nonc.tsv"),
    )?;

    let manifest = build_requested_manifest(paths)?;
    let requested_manifest_path = paths.experiment.join("requested-manifest.json");
    write_json_atomic(&requested_manifest_path, &manifest)?;
    let requested_manifest_sha256 = sha256_file(&requested_manifest_path)?;

    let mut id_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut workload_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut missing_sources = Vec::new();
    let mut missing_test_ids = BTreeSet::new();
    for input in &manifest.inputs {
        *id_counts.entry(input.id.clone()).or_default() += 1;
        workload_ids
            .entry(input.workload_identity_sha256.clone())
            .or_default()
            .push(input.id.clone());
        for source in input.sources.iter().filter(|source| !source.exists) {
            missing_test_ids.insert(input.id.clone());
            missing_sources.push(MissingInputSource {
                test_id: input.id.clone(),
                corpus_file: input.corpus_file.clone(),
                corpus_line: input.corpus_line,
                role: source.role.clone(),
                path: source.path.clone(),
            });
        }
    }
    missing_sources
        .sort_by(|a, b| (&a.test_id, &a.role, &a.path).cmp(&(&b.test_id, &b.role, &b.path)));
    let duplicate_ids: Vec<_> = id_counts
        .iter()
        .filter(|(_, count)| **count > 1)
        .map(|(id, _)| id.clone())
        .collect();
    let unique_ids = id_counts.len();
    let duplicate_workload_identities: Vec<_> = workload_ids
        .into_iter()
        .filter(|(_, ids)| ids.len() > 1)
        .map(|(identity, mut ids)| {
            ids.sort();
            format!("{identity}:{}", ids.join(","))
        })
        .collect();
    let executable_tests = manifest
        .inputs
        .iter()
        .filter(|input| input.available)
        .count();
    let c_rows = manifest
        .inputs
        .iter()
        .filter(|input| input.kind == "compiled_c")
        .count();
    let nonc_rows = manifest.inputs.len() - c_rows;
    let complete = manifest.inputs.len() == manifest.denominator_expected_tests
        && unique_ids == manifest.denominator_expected_tests
        && missing_sources.is_empty()
        && duplicate_ids.is_empty()
        && duplicate_workload_identities.is_empty();
    let audit = InputAudit {
        schema: SCHEMA,
        record_type: "strict_metric_input_audit".to_owned(),
        complete,
        full_sweep_allowed: complete,
        denominator_expected_tests: manifest.denominator_expected_tests,
        denominator_observed_rows: manifest.inputs.len(),
        denominator_unique_ids: unique_ids,
        denominator_executable_tests: executable_tests,
        c_rows,
        nonc_rows,
        missing_test_count: missing_test_ids.len(),
        missing_source_count: missing_sources.len(),
        missing_test_ids: missing_test_ids.into_iter().collect(),
        missing_sources,
        duplicate_ids,
        duplicate_workload_identities,
        requested_manifest_path: "requested-manifest.json".to_owned(),
        requested_manifest_sha256,
        executable_manifest_path: None,
        executable_manifest_sha256: None,
        hermit_sha: manifest.hermit_sha,
        reverie_sha: manifest.reverie_sha,
        reverie_dependency_sha: manifest.reverie_dependency_sha,
        liteinst2_sha: manifest.liteinst2_sha,
        liteinst2_dependency_sha: manifest.liteinst2_dependency_sha,
        hermit_binary_sha256: manifest.hermit_binary_sha256,
        corpus_c_sha256: manifest.corpus_c_sha256,
        corpus_nonc_sha256: manifest.corpus_nonc_sha256,
    };
    write_json_atomic(&paths.experiment.join("input-audit.json"), &audit)?;
    Ok(audit)
}

fn build_requested_manifest(paths: &Paths) -> Result<RequestedManifest> {
    let mut inputs = Vec::new();
    let c_file = File::open(&paths.corpus_c)?;
    for (line_no, line) in BufReader::new(c_file).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.split('|').collect();
        if fields.len() != 6 {
            bail!(
                "{}:{} expected 6 fields",
                paths.corpus_c.display(),
                line_no + 1
            );
        }
        let cflags = shell_words::split(fields[2])?;
        let extras = shell_words::split(fields[3])?;
        let mut declared_sources = vec![("primary".to_owned(), fields[1].to_owned())];
        declared_sources.extend(
            extras
                .iter()
                .cloned()
                .into_iter()
                .enumerate()
                .map(|(index, path)| (format!("extra_source_{}", index + 1), path)),
        );
        let mut sources = Vec::new();
        for (role, declared_path) in declared_sources {
            let path = paths.hermit_repo.join(&declared_path);
            let exists = path.is_file();
            sources.push(RequestedSource {
                role,
                path: declared_path,
                exists,
                sha256: if exists {
                    Some(sha256_file(&path)?)
                } else {
                    None
                },
            });
        }
        let output = paths
            .artifacts
            .join("guests")
            .join(slug(fields[0]))
            .join("guest");
        let compile_command = c_compile_command(
            &paths.hermit_repo.join(fields[1]),
            &cflags,
            &extras
                .iter()
                .map(|path| paths.hermit_repo.join(path).display().to_string())
                .collect::<Vec<_>>(),
            &output,
        );
        let semantic_compile = compile_command[..compile_command.len() - 2].to_vec();
        let workload_identity_sha256 = workload_identity(
            "compiled_c",
            &[],
            &sources,
            &semantic_compile,
            &paths.hermit_repo,
        )?;
        inputs.push(RequestedInput {
            id: fields[0].to_owned(),
            lane: fields[4].to_owned(),
            kind: "compiled_c".to_owned(),
            corpus_file: "compat-envelope/corpus/corpus-c.tsv".to_owned(),
            corpus_line: line_no + 1,
            corpus_row: line,
            argv: Vec::new(),
            compile_command,
            workload_identity_sha256,
            available: sources.iter().all(|source| source.exists),
            sources,
        });
    }

    let nonc_file = File::open(&paths.corpus_nonc)?;
    for (line_no, line) in BufReader::new(nonc_file).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.splitn(3, '|').collect();
        if fields.len() != 3 {
            bail!(
                "{}:{} expected 3 fields",
                paths.corpus_nonc.display(),
                line_no + 1
            );
        }
        let expanded = fields[2].replace("HERMITROOT", &paths.hermit_repo.display().to_string());
        let argv = shell_words::split(&expanded)?;
        if argv.is_empty() {
            bail!(
                "{}:{} empty command",
                paths.corpus_nonc.display(),
                line_no + 1
            );
        }
        let executable = PathBuf::from(&argv[0]);
        let exists = executable.is_file();
        let sources = vec![RequestedSource {
            role: "executable".to_owned(),
            path: executable.display().to_string(),
            exists,
            sha256: if exists {
                Some(sha256_file(&executable)?)
            } else {
                None
            },
        }];
        let workload_identity_sha256 = workload_identity(
            "script_or_interpreter",
            &argv,
            &sources,
            &[],
            &paths.hermit_repo,
        )?;
        inputs.push(RequestedInput {
            id: fields[0].to_owned(),
            lane: fields[1].to_owned(),
            kind: "script_or_interpreter".to_owned(),
            corpus_file: "compat-envelope/corpus/corpus-nonc.tsv".to_owned(),
            corpus_line: line_no + 1,
            corpus_row: line,
            argv,
            compile_command: Vec::new(),
            workload_identity_sha256,
            available: sources.iter().all(|source| source.exists),
            sources,
        });
    }
    inputs.sort_by(|a, b| a.id.cmp(&b.id));

    Ok(RequestedManifest {
        schema: SCHEMA,
        record_type: "strict_metric_requested_manifest".to_owned(),
        hermit_sha: git_head(&paths.hermit_repo)?,
        reverie_sha: git_head(&paths.reverie_repo)?,
        reverie_dependency_sha: cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/reverie")?,
        liteinst2_sha: git_head(&paths.liteinst_repo)?,
        liteinst2_dependency_sha: cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/liteinst2")?,
        hermit_binary_sha256: sha256_file(&paths.binary)?,
        corpus_c_sha256: sha256_file(&paths.corpus_c)?,
        corpus_nonc_sha256: sha256_file(&paths.corpus_nonc)?,
        denominator_expected_tests: FULL_TESTS,
        inputs,
    })
}

fn prepare(paths: &Paths, jobs: usize) -> Result<()> {
    let (parent_commit, run_rs_sha256, verify_rs_sha256) = harness_binding(paths)?;
    require_repo_at_head(&paths.hermit_repo)?;
    require_repo_at_head(&paths.reverie_repo)?;
    require_repo_at_head(&paths.liteinst_repo)?;
    fs::create_dir_all(&paths.experiment)?;
    fs::create_dir_all(paths.artifacts.join("guests"))?;
    fs::create_dir_all(paths.artifacts.join("compile-logs"))?;
    fs::create_dir_all(paths.artifacts.join("preparation"))?;

    let toolchain = capture_toolchain()?;
    let hermit_build = build_hermit(paths, jobs, &toolchain)?;

    let mut input_audit = freeze_requested_inputs(paths)?;
    if !input_audit.complete {
        let missing = input_audit
            .missing_sources
            .iter()
            .map(|source| format!("{}:{}", source.test_id, source.path))
            .collect::<Vec<_>>()
            .join("\n  ");
        bail!(
            "234-test input preflight is incomplete: observed_rows={} unique_ids={} executable_tests={} missing_tests={} missing_sources={} duplicate_ids={:?} duplicate_workloads={:?}; requested_manifest_sha256={}; missing:\n  {}",
            input_audit.denominator_observed_rows,
            input_audit.denominator_unique_ids,
            input_audit.denominator_executable_tests,
            input_audit.missing_test_count,
            input_audit.missing_source_count,
            input_audit.duplicate_ids,
            input_audit.duplicate_workload_identities,
            input_audit.requested_manifest_sha256,
            missing
        );
    }

    let corpus_c_sha256 = input_audit.corpus_c_sha256.clone();
    let corpus_nonc_sha256 = input_audit.corpus_nonc_sha256.clone();

    let mut tests = parse_c_tests(paths)?;
    tests.extend(parse_nonc_tests(paths)?);
    tests.sort_by(|a, b| a.id.cmp(&b.id));
    let ids: BTreeSet<_> = tests.iter().map(|t| t.id.as_str()).collect();
    if tests.len() != FULL_TESTS || ids.len() != FULL_TESTS {
        bail!(
            "frozen corpus must contain exactly {FULL_TESTS} unique semantic workloads; rows={} unique={}",
            tests.len(),
            ids.len()
        );
    }

    compile_c_guests(paths, &mut tests, jobs, &toolchain)?;
    for test in &mut tests {
        let guest = resolved_guest_path(paths, test);
        if !guest.is_file() {
            bail!(
                "prepared guest missing for {}: {}",
                test.id,
                guest.display()
            );
        }
        test.binary_sha256 = sha256_file(&guest)?;
        test.argv[0] = guest.display().to_string();
    }

    let reverie_sha = git_head(&paths.reverie_repo)?;
    let reverie_dependency_sha = cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/reverie")?;
    if reverie_sha != reverie_dependency_sha {
        bail!(
            "Reverie checkout {} does not equal Hermit's resolved dependency {}",
            reverie_sha,
            reverie_dependency_sha
        );
    }
    let manifest = FrozenManifest {
        schema: SCHEMA,
        record_type: "strict_metric_frozen_manifest".to_owned(),
        hermit_sha: git_head(&paths.hermit_repo)?,
        reverie_sha,
        reverie_dependency_sha,
        liteinst2_sha: git_head(&paths.liteinst_repo)?,
        liteinst2_dependency_sha: cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/liteinst2")?,
        hermit_binary_sha256: sha256_file(&paths.binary)?,
        corpus_c_sha256: corpus_c_sha256.clone(),
        corpus_nonc_sha256: corpus_nonc_sha256.clone(),
        parent_commit,
        run_rs_sha256,
        verify_rs_sha256,
        toolchain,
        hermit_build,
        guest_set_sha256: guest_set_sha256(&tests)?,
        tests,
    };
    write_json_atomic(&paths.experiment.join("manifest.json"), &manifest)?;
    input_audit.executable_manifest_path = Some("manifest.json".to_owned());
    input_audit.executable_manifest_sha256 =
        Some(sha256_file(&paths.experiment.join("manifest.json"))?);
    write_json_atomic(&paths.experiment.join("input-audit.json"), &input_audit)?;

    let metadata = serde_json::json!({
        "schema": SCHEMA,
        "record_type": "strict_metric_metadata",
        "created_epoch_ms": epoch_ms(),
        "hermit_sha": manifest.hermit_sha,
        "reverie_sha": manifest.reverie_sha,
        "reverie_dependency_sha": manifest.reverie_dependency_sha,
        "liteinst2_sha": manifest.liteinst2_sha,
        "liteinst2_dependency_sha": manifest.liteinst2_dependency_sha,
        "hermit_binary": paths.binary,
        "hermit_binary_sha256": manifest.hermit_binary_sha256,
        "manifest_sha256": sha256_file(&paths.experiment.join("manifest.json"))?,
        "artifact_root": paths.artifacts,
        "logical_cpus": std::thread::available_parallelism().map(|n| n.get()).unwrap_or(0),
        "comparison": {
            "stdout": "raw bytes",
            "stderr": "raw bytes",
            "termination": "exact exit code or terminating signal",
            "ordered_log_stream": "complete raw --log=info --log-file bytes",
            "stripping": [],
            "canonicalizations": [],
            "filters": [],
        }
    });
    write_json_atomic(&paths.experiment.join("metadata.json"), &metadata)?;
    println!(
        "prepared {FULL_TESTS}-test manifest at {} (sha256 {})",
        paths.experiment.join("manifest.json").display(),
        sha256_file(&paths.experiment.join("manifest.json"))?
    );
    Ok(())
}

fn run_jobs(
    paths: &Paths,
    kind: String,
    run_instance: String,
    spot_run_instance: Option<String>,
    jobs: usize,
    timeout_seconds: u64,
) -> Result<()> {
    if jobs == 0 {
        bail!("--jobs must be positive");
    }
    let manifest: FrozenManifest = read_json(&paths.experiment.join("manifest.json"))?;
    validate_frozen_inputs(paths, &manifest)?;
    let (binding, binding_sha256) = write_denominator(
        paths,
        &kind,
        &run_instance,
        spot_run_instance.as_deref(),
        &manifest,
    )?;
    let queue = Arc::new(Mutex::new(VecDeque::from(build_jobs(&manifest, &kind)?)));
    let total = queue.lock().unwrap().len();
    let completed = Arc::new(AtomicUsize::new(0));
    let errors: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let started = Instant::now();
    let mut workers = Vec::new();
    for worker_id in 0..jobs.min(total.max(1)) {
        let queue = Arc::clone(&queue);
        let completed = Arc::clone(&completed);
        let errors = Arc::clone(&errors);
        let paths = paths.clone();
        let binding = binding.clone();
        let binding_sha256 = binding_sha256.clone();
        workers.push(thread::spawn(move || loop {
            let next = queue.lock().unwrap().pop_front();
            let Some(job) = next else { break };
            if let Err(error) = run_pair(&paths, &job, &binding, &binding_sha256, timeout_seconds) {
                errors
                    .lock()
                    .unwrap()
                    .push(format!("{}: {error:#}", job_cell_id(&job)));
            }
            let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
            if done % 25 == 0 || done == total {
                eprintln!(
                    "progress kind={} worker={} pairs={}/{} elapsed_ms={}",
                    job.run_kind,
                    worker_id,
                    done,
                    total,
                    started.elapsed().as_millis()
                );
            }
        }));
    }
    for worker in workers {
        worker
            .join()
            .map_err(|_| anyhow::anyhow!("worker panicked"))?;
    }
    let errors = errors.lock().unwrap();
    if !errors.is_empty() {
        for error in errors.iter() {
            eprintln!("runner-error: {error}");
        }
        bail!("{} pair runner errors", errors.len());
    }
    assemble(paths, &kind, &run_instance)?;
    println!(
        "completed kind={} pairs={} executions={} wall_ms={}",
        kind,
        total,
        total * 2,
        started.elapsed().as_millis()
    );
    Ok(())
}

fn build_jobs(manifest: &FrozenManifest, kind: &str) -> Result<Vec<Job>> {
    let mut selected: Vec<TestSpec> = match kind {
        "full" => manifest.tests.clone(),
        "calibration" => manifest
            .tests
            .iter()
            .filter(|test| test.id == CALIBRATION_TEST)
            .cloned()
            .collect(),
        "spot" => manifest
            .tests
            .iter()
            .filter(|test| SPOT_TESTS.contains(&test.id.as_str()))
            .cloned()
            .collect(),
        other => bail!("invalid --kind {other:?}; expected full, calibration, or spot"),
    };
    selected.sort_by(|a, b| a.id.cmp(&b.id));
    let expected_tests = match kind {
        "full" => FULL_TESTS,
        "calibration" => 1,
        "spot" => 2,
        _ => unreachable!(),
    };
    if selected.len() != expected_tests {
        bail!(
            "kind {kind} selected {} tests, expected {expected_tests}",
            selected.len()
        );
    }
    let modes: &[&str] = if kind == "spot" {
        &["heap", "stack", "heap_stack"]
    } else {
        &[MAIN_MODE]
    };
    let mut result = Vec::new();
    for test in selected {
        for backend in BACKENDS {
            for mode in modes {
                result.push(Job {
                    run_kind: kind.to_owned(),
                    test: test.clone(),
                    backend: backend.to_owned(),
                    observation_mode: (*mode).to_owned(),
                });
            }
        }
    }
    Ok(result)
}

fn run_pair(
    paths: &Paths,
    job: &Job,
    binding: &RunBinding,
    binding_sha256: &str,
    timeout_seconds: u64,
) -> Result<()> {
    let cell_id = job_cell_id(job);
    let state = state_root(paths, binding, binding_sha256);
    let cell_record_path = state.join("cells").join(format!("{cell_id}.json"));
    let one_record_path = state.join("executions").join(format!("{cell_id}--1.json"));
    let two_record_path = state.join("executions").join(format!("{cell_id}--2.json"));
    let any_cached =
        cell_record_path.exists() || one_record_path.exists() || two_record_path.exists();
    if any_cached {
        if !(cell_record_path.is_file() && one_record_path.is_file() && two_record_path.is_file()) {
            bail!("stale cache is partial for {cell_id}; refusing resume");
        }
        let one: ExecutionRecord = read_json(&one_record_path)?;
        let two: ExecutionRecord = read_json(&two_record_path)?;
        let cell: CellRecord = read_json(&cell_record_path)?;
        validate_cached_pair(paths, job, binding, binding_sha256, &one, &two, &cell)
            .with_context(|| format!("stale cache refused for {cell_id}"))?;
        return Ok(());
    }
    fs::create_dir_all(state.join("executions"))?;
    fs::create_dir_all(state.join("cells"))?;
    let artifact_dir = paths
        .artifacts
        .join("artifacts")
        .join(&job.run_kind)
        .join(&binding.run_instance)
        .join(binding_sha256)
        .join(&cell_id);
    fs::create_dir_all(&artifact_dir)?;

    let one = run_one(
        paths,
        job,
        binding,
        binding_sha256,
        1,
        timeout_seconds,
        &artifact_dir,
    )?;
    let two = run_one(
        paths,
        job,
        binding,
        binding_sha256,
        2,
        timeout_seconds,
        &artifact_dir,
    )?;
    write_json_atomic(
        &state.join("executions").join(format!("{cell_id}--1.json")),
        &one,
    )?;
    write_json_atomic(
        &state.join("executions").join(format!("{cell_id}--2.json")),
        &two,
    )?;

    let stdout_equal = files_equal(
        &paths.artifacts.join(&one.stdout.path),
        &paths.artifacts.join(&two.stdout.path),
    )?;
    let stderr_equal = files_equal(
        &paths.artifacts.join(&one.stderr.path),
        &paths.artifacts.join(&two.stderr.path),
    )?;
    let log_equal = files_equal(
        &paths.artifacts.join(&one.ordered_log_stream.path),
        &paths.artifacts.join(&two.ordered_log_stream.path),
    )?;
    let termination_equal = one.termination == two.termination;
    let nonzero_log_events = one.log_event_count > 0 && two.log_event_count > 0;
    let successful_exit_both = successful(&one.termination) && successful(&two.termination);
    let raw_observations_equal = stdout_equal && stderr_equal && termination_equal && log_equal;

    let legacy_diagnostic_path = artifact_dir.join("legacy-log-diff.stderr");
    let legacy_log_match = run_legacy_logdiff(
        paths,
        &paths.artifacts.join(&one.ordered_log_stream.path),
        &paths.artifacts.join(&two.ordered_log_stream.path),
        &legacy_diagnostic_path,
    )?;
    let cell = CellRecord {
        schema: SCHEMA,
        record_type: "strict_metric_cell".to_owned(),
        run_kind: job.run_kind.clone(),
        run_instance: binding.run_instance.clone(),
        run_binding_sha256: binding_sha256.to_owned(),
        cell_id: cell_id.clone(),
        test_id: job.test.id.clone(),
        backend: job.backend.clone(),
        observation_mode: job.observation_mode.clone(),
        execution_ordinals: vec![1, 2],
        stdout_equal,
        stderr_equal,
        termination_equal,
        ordered_log_stream_equal: log_equal,
        nonzero_log_events,
        successful_exit_both,
        raw_observations_equal,
        strict_green: raw_observations_equal && nonzero_log_events && successful_exit_both,
        legacy_log_match,
        legacy_green: stdout_equal
            && stderr_equal
            && termination_equal
            && legacy_log_match
            && successful_exit_both,
        legacy_comparator: "hermit log-diff --unsafe-strip-lines (deterministic subset)".to_owned(),
        legacy_diagnostic: artifact_fact(paths, &legacy_diagnostic_path)?,
    };
    write_json_atomic(&cell_record_path, &cell)?;
    Ok(())
}

fn run_one(
    paths: &Paths,
    job: &Job,
    binding: &RunBinding,
    binding_sha256: &str,
    ordinal: u8,
    timeout_seconds: u64,
    artifact_dir: &Path,
) -> Result<ExecutionRecord> {
    let stdout_path = artifact_dir.join(format!("run{ordinal}.stdout"));
    let stderr_path = artifact_dir.join(format!("run{ordinal}.stderr"));
    let log_path = artifact_dir.join(format!("run{ordinal}.info.log"));
    File::create(&log_path)?;
    let command = command_argv(paths, job, &log_path);
    let command_sha256 = sha256_bytes(&serde_json::to_vec(&command)?);
    let stdout = File::create(&stdout_path)?;
    let stderr = File::create(&stderr_path)?;
    let started = Instant::now();
    let mut cmd = Command::new(&command[0]);
    cmd.args(&command[1..])
        .current_dir(&paths.hermit_repo)
        .env("LC_ALL", "C")
        .env("TZ", "UTC")
        .env_remove("RUST_LOG")
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    unsafe {
        cmd.pre_exec(|| {
            if libc_setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut attempted = false;
    let mut error = None;
    let termination = match cmd.spawn() {
        Ok(mut child) => {
            attempted = true;
            match child.wait_timeout(Duration::from_secs(timeout_seconds))? {
                Some(status) => termination_from_status(status, false),
                None => {
                    let pgid = Pid::from_raw(child.id() as i32);
                    let _ = killpg(pgid, Signal::SIGTERM);
                    thread::sleep(Duration::from_secs(1));
                    let _ = killpg(pgid, Signal::SIGKILL);
                    let _ = child.wait();
                    Termination {
                        kind: "timeout".to_owned(),
                        code: None,
                        signal: None,
                        timed_out: true,
                    }
                }
            }
        }
        Err(spawn_error) => {
            error = Some(spawn_error.to_string());
            Termination {
                kind: "spawn_error".to_owned(),
                code: None,
                signal: None,
                timed_out: false,
            }
        }
    };
    if !log_path.exists() {
        File::create(&log_path)?;
    }
    let duration_ms = started.elapsed().as_millis();
    let log_event_count = count_raw_log_events(&log_path)?;
    Ok(ExecutionRecord {
        schema: SCHEMA,
        record_type: "strict_metric_execution".to_owned(),
        run_kind: job.run_kind.clone(),
        run_instance: binding.run_instance.clone(),
        run_binding_sha256: binding_sha256.to_owned(),
        cell_id: job_cell_id(job),
        test_id: job.test.id.clone(),
        backend: job.backend.clone(),
        observation_mode: job.observation_mode.clone(),
        ordinal,
        attempted,
        termination,
        duration_ms,
        log_event_count,
        stdout: artifact_fact(paths, &stdout_path)?,
        stderr: artifact_fact(paths, &stderr_path)?,
        ordered_log_stream: artifact_fact(paths, &log_path)?,
        command,
        command_sha256,
        guest_binary_sha256: job.test.binary_sha256.clone(),
        error,
    })
}

fn command_argv(paths: &Paths, job: &Job, log_path: &Path) -> Vec<String> {
    let mut command = vec![
        paths.binary.display().to_string(),
        "--log=info".to_owned(),
        format!("--log-file={}", log_path.display()),
        "--backend".to_owned(),
        job.backend.clone(),
        "run".to_owned(),
        "--strict".to_owned(),
    ];
    if job.test.lane == "portable" {
        command.push("--no-virtualize-cpuid".to_owned());
        command.push("--max-timeslice=disabled".to_owned());
    }
    match job.observation_mode.as_str() {
        MAIN_MODE => {}
        "heap" => command.push("--detlog-heap".to_owned()),
        "stack" => command.push("--detlog-stack".to_owned()),
        "heap_stack" => {
            command.push("--detlog-heap".to_owned());
            command.push("--detlog-stack".to_owned());
        }
        other => panic!("unsupported observation mode {other}"),
    }
    command.push("--".to_owned());
    command.extend(job.test.argv.clone());
    command
}

fn run_legacy_logdiff(paths: &Paths, left: &Path, right: &Path, diagnostic: &Path) -> Result<bool> {
    let stderr = File::create(diagnostic)?;
    let status = Command::new(&paths.binary)
        .arg("log-diff")
        .arg(left)
        .arg(right)
        .arg("--unsafe-strip-lines")
        .arg("--no-color")
        .arg("--limit=1")
        .current_dir(&paths.hermit_repo)
        .stdout(Stdio::null())
        .stderr(Stdio::from(stderr))
        .status()
        .context("running legacy log-diff")?;
    Ok(status.success())
}

fn assemble(paths: &Paths, kind: &str, run_instance: &str) -> Result<()> {
    let run_dir = run_dir(paths, kind, run_instance);
    let binding_path = run_dir.join("run-binding.json");
    let binding: RunBinding = read_json(&binding_path)?;
    if binding.run_kind != kind || binding.run_instance != run_instance {
        bail!("run binding identity does not match assemble request");
    }
    let binding_sha256 = sha256_file(&binding_path)?;
    let state = state_root(paths, &binding, &binding_sha256);
    let executions = collect_json_records(&state.join("executions"))?;
    let cells = collect_json_records(&state.join("cells"))?;
    write_jsonl_atomic(&run_dir.join("executions.jsonl"), &executions)?;
    write_jsonl_atomic(&run_dir.join("cells.jsonl"), &cells)?;
    Ok(())
}

fn write_denominator(
    paths: &Paths,
    kind: &str,
    run_instance: &str,
    spot_run_instance: Option<&str>,
    manifest: &FrozenManifest,
) -> Result<(RunBinding, String)> {
    let input_audit_path = paths.experiment.join("input-audit.json");
    let input_audit: InputAudit = read_json(&input_audit_path)?;
    let manifest_sha256 = sha256_file(&paths.experiment.join("manifest.json"))?;
    if !input_audit.complete
        || !input_audit.full_sweep_allowed
        || input_audit.denominator_expected_tests != FULL_TESTS
        || input_audit.denominator_observed_rows != FULL_TESTS
        || input_audit.denominator_unique_ids != FULL_TESTS
        || input_audit.denominator_executable_tests != FULL_TESTS
        || input_audit.missing_test_count != 0
        || input_audit.missing_source_count != 0
        || !input_audit.missing_test_ids.is_empty()
        || !input_audit.missing_sources.is_empty()
        || !input_audit.duplicate_ids.is_empty()
        || !input_audit.duplicate_workload_identities.is_empty()
        || input_audit.executable_manifest_path.as_deref() != Some("manifest.json")
        || input_audit.executable_manifest_sha256.as_deref() != Some(&manifest_sha256)
    {
        bail!("input audit does not authorize the complete 234-workload denominator");
    }
    let jobs = build_jobs(manifest, kind)?;
    let tests: BTreeSet<_> = jobs.iter().map(|j| j.test.id.clone()).collect();
    let modes: BTreeSet<_> = jobs.iter().map(|j| j.observation_mode.clone()).collect();
    let mut comparison_contract = BTreeMap::new();
    comparison_contract.insert("stdout".to_owned(), serde_json::json!("raw_bytes"));
    comparison_contract.insert("stderr".to_owned(), serde_json::json!("raw_bytes"));
    comparison_contract.insert(
        "termination".to_owned(),
        serde_json::json!("exact_exit_code_or_signal"),
    );
    comparison_contract.insert(
        "ordered_log_stream".to_owned(),
        serde_json::json!("complete_raw_log_file_bytes"),
    );
    comparison_contract.insert("stripped_prefixes".to_owned(), serde_json::json!([]));
    comparison_contract.insert("canonicalizations".to_owned(), serde_json::json!([]));
    comparison_contract.insert("filters".to_owned(), serde_json::json!([]));
    comparison_contract.insert(
        "minimum_log_events_per_execution".to_owned(),
        serde_json::json!(1),
    );
    let required_spot_completion = match (kind, spot_run_instance) {
        ("full", Some(instance)) => {
            let path = run_dir(paths, "spot", instance).join("spot-completion.json");
            if !path.is_file() {
                bail!(
                    "full run requires an already verified spot completion: {}",
                    path.display()
                );
            }
            Some(EvidenceFile {
                path: path.strip_prefix(&paths.experiment)?.display().to_string(),
                sha256: sha256_file(&path)?,
            })
        }
        ("full", None) => bail!("full run requires --spot-run-instance"),
        (_, Some(_)) => bail!("--spot-run-instance is only valid for a full run"),
        (_, None) => None,
    };
    let denominator = Denominator {
        schema: SCHEMA,
        record_type: "strict_metric_denominator".to_owned(),
        run_kind: kind.to_owned(),
        run_instance: run_instance.to_owned(),
        hermit_sha: manifest.hermit_sha.clone(),
        reverie_sha: manifest.reverie_sha.clone(),
        reverie_dependency_sha: manifest.reverie_dependency_sha.clone(),
        liteinst2_sha: manifest.liteinst2_sha.clone(),
        liteinst2_dependency_sha: manifest.liteinst2_dependency_sha.clone(),
        hermit_binary_sha256: manifest.hermit_binary_sha256.clone(),
        parent_commit: manifest.parent_commit.clone(),
        run_rs_sha256: manifest.run_rs_sha256.clone(),
        verify_rs_sha256: manifest.verify_rs_sha256.clone(),
        guest_set_sha256: manifest.guest_set_sha256.clone(),
        input_audit_path: "input-audit.json".to_owned(),
        input_audit_sha256: sha256_file(&input_audit_path)?,
        requested_manifest_path: input_audit.requested_manifest_path.clone(),
        requested_manifest_sha256: input_audit.requested_manifest_sha256.clone(),
        manifest_path: "manifest.json".to_owned(),
        manifest_sha256,
        corpus_c_sha256: manifest.corpus_c_sha256.clone(),
        corpus_nonc_sha256: manifest.corpus_nonc_sha256.clone(),
        tests: tests.into_iter().collect(),
        backends: BACKENDS.iter().map(|s| (*s).to_owned()).collect(),
        observation_modes: modes.into_iter().collect(),
        run_ordinals: vec![1, 2],
        expected_cells: jobs.len(),
        expected_executions: jobs.len() * 2,
        required_spot_completion: required_spot_completion.clone(),
        comparison_contract,
    };
    let run_dir = run_dir(paths, kind, run_instance);
    fs::create_dir_all(&run_dir)?;
    let denominator_path = run_dir.join("denominator.json");
    if denominator_path.exists() {
        let existing: Denominator = read_json(&denominator_path)?;
        if serde_json::to_vec(&existing)? != serde_json::to_vec(&denominator)? {
            bail!("run instance {run_instance:?} already has a different denominator");
        }
    } else {
        write_json_atomic(&denominator_path, &denominator)?;
    }
    let denominator_sha256 = sha256_file(&denominator_path)?;
    let binding = RunBinding {
        schema: SCHEMA,
        record_type: "strict_metric_run_binding".to_owned(),
        run_kind: kind.to_owned(),
        run_instance: run_instance.to_owned(),
        parent_commit: manifest.parent_commit.clone(),
        run_rs_sha256: manifest.run_rs_sha256.clone(),
        verify_rs_sha256: manifest.verify_rs_sha256.clone(),
        requested_manifest_sha256: input_audit.requested_manifest_sha256.clone(),
        input_audit_sha256: sha256_file(&input_audit_path)?,
        manifest_sha256: sha256_file(&paths.experiment.join("manifest.json"))?,
        denominator_sha256,
        hermit_binary_sha256: manifest.hermit_binary_sha256.clone(),
        guest_set_sha256: manifest.guest_set_sha256.clone(),
        required_spot_completion,
    };
    let binding_path = run_dir.join("run-binding.json");
    if binding_path.exists() {
        let existing: RunBinding = read_json(&binding_path)?;
        if serde_json::to_vec(&existing)? != serde_json::to_vec(&binding)? {
            bail!("run instance {run_instance:?} already has a different binding");
        }
    } else {
        write_json_atomic(&binding_path, &binding)?;
    }
    let binding_sha256 = sha256_file(&binding_path)?;
    Ok((binding, binding_sha256))
}

fn validate_frozen_inputs(paths: &Paths, manifest: &FrozenManifest) -> Result<()> {
    verify_harness_commit(
        paths,
        &manifest.parent_commit,
        &manifest.run_rs_sha256,
        &manifest.verify_rs_sha256,
    )?;
    let checks = [
        (
            "Hermit",
            git_head(&paths.hermit_repo)?,
            manifest.hermit_sha.clone(),
        ),
        (
            "Reverie",
            git_head(&paths.reverie_repo)?,
            manifest.reverie_sha.clone(),
        ),
        (
            "LiteInst2",
            git_head(&paths.liteinst_repo)?,
            manifest.liteinst2_sha.clone(),
        ),
    ];
    for (name, actual, expected) in checks {
        if actual != expected {
            bail!("{name} SHA moved: expected {expected}, found {actual}");
        }
    }
    if sha256_file(&paths.corpus_c)? != manifest.corpus_c_sha256
        || sha256_file(&paths.corpus_nonc)? != manifest.corpus_nonc_sha256
    {
        bail!("source corpus changed after manifest freeze");
    }
    validate_input_authority(paths, manifest)?;
    if cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/reverie")?
        != manifest.reverie_dependency_sha
        || cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/liteinst2")?
            != manifest.liteinst2_dependency_sha
    {
        bail!("Hermit Cargo.lock dependency pin changed after manifest freeze");
    }
    if !paths.binary.is_file() || sha256_file(&paths.binary)? != manifest.hermit_binary_sha256 {
        bail!("Hermit binary missing or changed after manifest freeze");
    }
    if capture_toolchain()? != manifest.toolchain {
        bail!("toolchain changed after manifest freeze");
    }
    let expected_build_command = hermit_build_command();
    if manifest.hermit_build.source_sha != manifest.hermit_sha
        || manifest.hermit_build.source_tree != git_tree(&paths.hermit_repo)?
        || manifest.hermit_build.cargo_lock_sha256
            != sha256_file(&paths.hermit_repo.join("Cargo.lock"))?
        || !manifest.hermit_build.clean_before
        || !manifest.hermit_build.clean_after
        || !manifest.hermit_build.binary_absent_before
        || manifest.hermit_build.exit_code != 0
        || manifest.hermit_build.command != expected_build_command
        || manifest.hermit_build.command_sha256
            != sha256_bytes(&serde_json::to_vec(&manifest.hermit_build.command)?)
        || manifest.hermit_build.toolchain_receipt_sha256 != manifest.toolchain.receipt_sha256
        || manifest.hermit_build.output.sha256 != manifest.hermit_binary_sha256
    {
        bail!("Hermit causal build receipt no longer binds the source tuple and binary");
    }
    verify_artifact_fact(paths, &manifest.hermit_build.log)?;
    verify_artifact_fact(paths, &manifest.hermit_build.output)?;
    let mut test_ids = BTreeSet::new();
    let mut workload_ids = BTreeSet::new();
    for test in &manifest.tests {
        if !test_ids.insert(&test.id) || !workload_ids.insert(&test.workload_identity_sha256) {
            bail!("frozen manifest has duplicate test or workload identity");
        }
        let guest = Path::new(&test.argv[0]);
        if !guest.is_file() || sha256_file(guest)? != test.binary_sha256 {
            bail!("guest binary missing or changed for {}", test.id);
        }
        match (&test.compile, &test.compile_receipt) {
            (Some(compile), Some(receipt)) => {
                let mut expected_inputs = vec![SourceFact {
                    role: "primary".to_owned(),
                    path: compile.source.clone(),
                    sha256: sha256_file(Path::new(&compile.source))?,
                }];
                for (index, source) in compile.extra_sources.iter().enumerate() {
                    expected_inputs.push(SourceFact {
                        role: format!("extra_source_{}", index + 1),
                        path: source.clone(),
                        sha256: sha256_file(Path::new(source))?,
                    });
                }
                if receipt.command != compile.command
                    || receipt.command_sha256
                        != sha256_bytes(&serde_json::to_vec(&compile.command)?)
                    || receipt.inputs != expected_inputs
                    || receipt.output.sha256 != test.binary_sha256
                    || receipt.exit_code != 0
                    || receipt.toolchain_receipt_sha256 != manifest.toolchain.receipt_sha256
                {
                    bail!("compile receipt no longer binds guest {}", test.id);
                }
                verify_artifact_fact(paths, &receipt.log)?;
                verify_artifact_fact(paths, &receipt.output)?;
            }
            (None, None) => {}
            _ => bail!(
                "compile specification/receipt pairing is invalid: {}",
                test.id
            ),
        }
    }
    if manifest.tests.len() != FULL_TESTS {
        bail!("frozen manifest must contain exactly {FULL_TESTS} unique semantic workloads");
    }
    if guest_set_sha256(&manifest.tests)? != manifest.guest_set_sha256 {
        bail!("guest-set digest changed after manifest freeze");
    }
    Ok(())
}

fn validate_input_authority(paths: &Paths, manifest: &FrozenManifest) -> Result<()> {
    let requested_path = paths.experiment.join("requested-manifest.json");
    let recorded: RequestedManifest = read_json(&requested_path)?;
    let derived = build_requested_manifest(paths)?;
    if serde_json::to_vec(&recorded)? != serde_json::to_vec(&derived)? {
        bail!("requested manifest differs from independent corpus/source rederivation");
    }
    let audit: InputAudit = read_json(&paths.experiment.join("input-audit.json"))?;
    let mut id_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut workload_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut missing_test_ids = BTreeSet::new();
    let mut missing_source_count = 0usize;
    for input in &derived.inputs {
        *id_counts.entry(input.id.clone()).or_default() += 1;
        workload_ids
            .entry(input.workload_identity_sha256.clone())
            .or_default()
            .push(input.id.clone());
        for _source in input.sources.iter().filter(|source| !source.exists) {
            missing_source_count += 1;
            missing_test_ids.insert(input.id.clone());
        }
    }
    let duplicate_ids: Vec<_> = id_counts
        .iter()
        .filter(|(_, count)| **count > 1)
        .map(|(id, _)| id.clone())
        .collect();
    let duplicate_workloads: Vec<_> = workload_ids
        .into_iter()
        .filter(|(_, ids)| ids.len() > 1)
        .map(|(identity, mut ids)| {
            ids.sort();
            format!("{identity}:{}", ids.join(","))
        })
        .collect();
    let executable_tests = derived
        .inputs
        .iter()
        .filter(|input| input.available)
        .count();
    let c_rows = derived
        .inputs
        .iter()
        .filter(|input| input.kind == "compiled_c")
        .count();
    let missing_test_ids: Vec<_> = missing_test_ids.into_iter().collect();
    let manifest_hash = sha256_file(&paths.experiment.join("manifest.json"))?;
    let audit_binding = (
        &audit.hermit_sha,
        &audit.reverie_sha,
        &audit.reverie_dependency_sha,
        &audit.liteinst2_sha,
        &audit.liteinst2_dependency_sha,
        &audit.hermit_binary_sha256,
        &audit.corpus_c_sha256,
        &audit.corpus_nonc_sha256,
    );
    let manifest_binding = (
        &manifest.hermit_sha,
        &manifest.reverie_sha,
        &manifest.reverie_dependency_sha,
        &manifest.liteinst2_sha,
        &manifest.liteinst2_dependency_sha,
        &manifest.hermit_binary_sha256,
        &manifest.corpus_c_sha256,
        &manifest.corpus_nonc_sha256,
    );
    if audit.schema != SCHEMA
        || audit.record_type != "strict_metric_input_audit"
        || !audit.complete
        || !audit.full_sweep_allowed
        || audit.denominator_expected_tests != FULL_TESTS
        || audit.denominator_observed_rows != derived.inputs.len()
        || audit.denominator_unique_ids != id_counts.len()
        || audit.denominator_executable_tests != executable_tests
        || audit.c_rows != c_rows
        || audit.nonc_rows != derived.inputs.len() - c_rows
        || audit.missing_test_count != missing_test_ids.len()
        || audit.missing_source_count != missing_source_count
        || audit.missing_test_ids != missing_test_ids
        || audit.missing_sources.len() != missing_source_count
        || audit.duplicate_ids != duplicate_ids
        || audit.duplicate_workload_identities != duplicate_workloads
        || audit.requested_manifest_path != "requested-manifest.json"
        || audit.requested_manifest_sha256 != sha256_file(&requested_path)?
        || audit.executable_manifest_path.as_deref() != Some("manifest.json")
        || audit.executable_manifest_sha256.as_deref() != Some(manifest_hash.as_str())
        || audit_binding != manifest_binding
        || derived.inputs.len() != FULL_TESTS
        || id_counts.len() != FULL_TESTS
        || executable_tests != FULL_TESTS
        || missing_source_count != 0
        || !duplicate_ids.is_empty()
        || !duplicate_workloads.is_empty()
    {
        bail!("input audit does not equal independently rederived 234-workload authority");
    }
    Ok(())
}

fn parse_c_tests(paths: &Paths) -> Result<Vec<TestSpec>> {
    let input = File::open(&paths.corpus_c)?;
    let mut result = Vec::new();
    for (line_no, line) in BufReader::new(input).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.split('|').collect();
        if fields.len() != 6 {
            bail!(
                "{}:{} expected 6 fields",
                paths.corpus_c.display(),
                line_no + 1
            );
        }
        let source = paths.hermit_repo.join(fields[1]);
        let cflags = shell_words::split(fields[2])?;
        let extra_sources: Vec<_> = shell_words::split(fields[3])?
            .into_iter()
            .map(|p| paths.hermit_repo.join(p).display().to_string())
            .collect();
        let output = paths
            .artifacts
            .join("guests")
            .join(slug(fields[0]))
            .join("guest");
        let command = c_compile_command(&source, &cflags, &extra_sources, &output);
        let requested_sources = std::iter::once(RequestedSource {
            role: "primary".to_owned(),
            path: fields[1].to_owned(),
            exists: true,
            sha256: Some(sha256_file(&source)?),
        })
        .chain(extra_sources.iter().enumerate().map(|(index, path)| {
            let absolute = PathBuf::from(path);
            RequestedSource {
                role: format!("extra_source_{}", index + 1),
                path: absolute
                    .strip_prefix(&paths.hermit_repo)
                    .unwrap_or(&absolute)
                    .display()
                    .to_string(),
                exists: true,
                sha256: Some(sha256_file(&absolute).expect("validated source")),
            }
        }))
        .collect::<Vec<_>>();
        let semantic_compile = command[..command.len() - 2].to_vec();
        result.push(TestSpec {
            id: fields[0].to_owned(),
            lane: fields[4].to_owned(),
            kind: "compiled_c".to_owned(),
            argv: vec![output.display().to_string()],
            source_sha256: sha256_file(&source)?,
            binary_sha256: String::new(),
            workload_identity_sha256: workload_identity(
                "compiled_c",
                &[],
                &requested_sources,
                &semantic_compile,
                &paths.hermit_repo,
            )?,
            compile: Some(CompileSpec {
                source: source.display().to_string(),
                cflags,
                extra_sources,
                command,
            }),
            compile_receipt: None,
        });
    }
    Ok(result)
}

fn parse_nonc_tests(paths: &Paths) -> Result<Vec<TestSpec>> {
    let input = File::open(&paths.corpus_nonc)?;
    let mut result = Vec::new();
    for (line_no, line) in BufReader::new(input).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.splitn(3, '|').collect();
        if fields.len() != 3 {
            bail!(
                "{}:{} expected 3 fields",
                paths.corpus_nonc.display(),
                line_no + 1
            );
        }
        let expanded = fields[2].replace("HERMITROOT", &paths.hermit_repo.display().to_string());
        let argv = shell_words::split(&expanded)?;
        if argv.is_empty() {
            bail!(
                "{}:{} empty command",
                paths.corpus_nonc.display(),
                line_no + 1
            );
        }
        let source = RequestedSource {
            role: "executable".to_owned(),
            path: argv[0].clone(),
            exists: true,
            sha256: Some(sha256_file(Path::new(&argv[0]))?),
        };
        result.push(TestSpec {
            id: fields[0].to_owned(),
            lane: fields[1].to_owned(),
            kind: "script_or_interpreter".to_owned(),
            source_sha256: sha256_file(Path::new(&argv[0]))?,
            binary_sha256: sha256_file(Path::new(&argv[0]))?,
            workload_identity_sha256: workload_identity(
                "script_or_interpreter",
                &argv,
                std::slice::from_ref(&source),
                &[],
                &paths.hermit_repo,
            )?,
            argv,
            compile: None,
            compile_receipt: None,
        });
    }
    Ok(result)
}

fn compile_c_guests(
    paths: &Paths,
    tests: &mut [TestSpec],
    jobs: usize,
    toolchain: &ToolchainReceipt,
) -> Result<()> {
    let queue: VecDeque<_> = tests
        .iter()
        .filter(|test| test.compile.is_some())
        .cloned()
        .collect();
    let queue = Arc::new(Mutex::new(queue));
    let errors: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let receipts: Arc<Mutex<BTreeMap<String, CompileReceipt>>> =
        Arc::new(Mutex::new(BTreeMap::new()));
    let mut workers = Vec::new();
    for _ in 0..jobs.max(1) {
        let queue = Arc::clone(&queue);
        let errors = Arc::clone(&errors);
        let receipts = Arc::clone(&receipts);
        let paths = paths.clone();
        let toolchain_hash = toolchain.receipt_sha256.clone();
        workers.push(thread::spawn(move || loop {
            let Some(test) = queue.lock().unwrap().pop_front() else {
                break;
            };
            let compile = test.compile.as_ref().unwrap();
            let output = PathBuf::from(compile.command.last().unwrap());
            if let Some(parent) = output.parent() {
                if let Err(error) = fs::create_dir_all(parent) {
                    errors.lock().unwrap().push(format!("{}: {error}", test.id));
                    continue;
                }
            }
            let log = paths
                .artifacts
                .join("compile-logs")
                .join(format!("{}.log", slug(&test.id)));
            let result = (|| -> Result<CompileReceipt> {
                let log_file = File::create(&log)?;
                let status = Command::new(&compile.command[0])
                    .args(&compile.command[1..])
                    .current_dir(&paths.hermit_repo)
                    .stdout(Stdio::from(log_file.try_clone()?))
                    .stderr(Stdio::from(log_file))
                    .status()?;
                if !status.success() {
                    bail!("compiler exited {status}");
                }
                let exit_code = status
                    .code()
                    .context("compiler terminated without an exit code")?;
                let mut inputs = vec![SourceFact {
                    role: "primary".to_owned(),
                    path: compile.source.clone(),
                    sha256: sha256_file(Path::new(&compile.source))?,
                }];
                for (index, source) in compile.extra_sources.iter().enumerate() {
                    inputs.push(SourceFact {
                        role: format!("extra_source_{}", index + 1),
                        path: source.clone(),
                        sha256: sha256_file(Path::new(source))?,
                    });
                }
                Ok(CompileReceipt {
                    command: compile.command.clone(),
                    command_sha256: sha256_bytes(&serde_json::to_vec(&compile.command)?),
                    inputs,
                    toolchain_receipt_sha256: toolchain_hash.clone(),
                    exit_code,
                    log: artifact_fact(&paths, &log)?,
                    output: artifact_fact(&paths, &output)?,
                })
            })();
            match result {
                Ok(receipt) => {
                    receipts.lock().unwrap().insert(test.id.clone(), receipt);
                }
                Err(error) => {
                    errors.lock().unwrap().push(format!(
                        "{}: {error:#}; log={}",
                        test.id,
                        log.display()
                    ));
                }
            }
        }));
    }
    for worker in workers {
        worker
            .join()
            .map_err(|_| anyhow::anyhow!("compiler worker panicked"))?;
    }
    let errors = errors.lock().unwrap();
    if !errors.is_empty() {
        for error in errors.iter() {
            eprintln!("compile-error: {error}");
        }
        bail!("{} guest compilation failures", errors.len());
    }
    let mut receipts = receipts.lock().unwrap();
    for test in tests.iter_mut().filter(|test| test.compile.is_some()) {
        test.compile_receipt = Some(
            receipts
                .remove(&test.id)
                .with_context(|| format!("missing compile receipt for {}", test.id))?,
        );
    }
    if !receipts.is_empty() {
        bail!(
            "unexpected compile receipts remained: {:?}",
            receipts.keys()
        );
    }
    Ok(())
}

fn c_compile_command(
    source: &Path,
    cflags: &[String],
    extra_sources: &[String],
    output: &Path,
) -> Vec<String> {
    let mut command = vec![
        "cc".to_owned(),
        "-std=c11".to_owned(),
        "-O2".to_owned(),
        "-g".to_owned(),
        "-Wall".to_owned(),
        "-Wextra".to_owned(),
        "-Werror".to_owned(),
    ];
    command.extend(cflags.iter().cloned());
    command.push(source.display().to_string());
    command.extend(extra_sources.iter().cloned());
    command.push("-o".to_owned());
    command.push(output.display().to_string());
    command
}

fn hermit_build_command() -> Vec<String> {
    vec![
        "/usr/bin/with-proxy".to_owned(),
        "cargo".to_owned(),
        "build".to_owned(),
        "--release".to_owned(),
        "-p".to_owned(),
        "hermit".to_owned(),
        "--features".to_owned(),
        "third-party-backends".to_owned(),
        "--bin".to_owned(),
        "hermit".to_owned(),
    ]
}

fn workload_identity(
    kind: &str,
    argv: &[String],
    sources: &[RequestedSource],
    semantic_compile: &[String],
    hermit_repo: &Path,
) -> Result<String> {
    let normalize =
        |value: &str| value.replace(&hermit_repo.display().to_string(), "${HERMIT_REPO}");
    let source_values: Vec<_> = sources
        .iter()
        .map(|source| {
            serde_json::json!({
                "role": source.role,
                "path": normalize(&source.path),
                "sha256": source.sha256,
            })
        })
        .collect();
    let value = serde_json::json!({
        "kind": kind,
        "argv": argv.iter().map(|value| normalize(value)).collect::<Vec<_>>(),
        "sources": source_values,
        "semantic_compile": semantic_compile.iter().map(|value| normalize(value)).collect::<Vec<_>>(),
    });
    Ok(sha256_bytes(&serde_json::to_vec(&value)?))
}

fn capture_toolchain() -> Result<ToolchainReceipt> {
    let cc_argv = vec!["cc".to_owned(), "--version".to_owned()];
    let rustc_argv = vec!["rustc".to_owned(), "-vV".to_owned()];
    let cargo_argv = vec!["cargo".to_owned(), "-V".to_owned()];
    let cc_output_sha256 = command_output_sha256(&cc_argv)?;
    let rustc_output_sha256 = command_output_sha256(&rustc_argv)?;
    let cargo_output_sha256 = command_output_sha256(&cargo_argv)?;
    let receipt_sha256 = sha256_bytes(&serde_json::to_vec(&serde_json::json!({
        "cc_argv": cc_argv,
        "cc_output_sha256": cc_output_sha256,
        "rustc_argv": rustc_argv,
        "rustc_output_sha256": rustc_output_sha256,
        "cargo_argv": cargo_argv,
        "cargo_output_sha256": cargo_output_sha256,
    }))?);
    Ok(ToolchainReceipt {
        cc_argv,
        cc_output_sha256,
        rustc_argv,
        rustc_output_sha256,
        cargo_argv,
        cargo_output_sha256,
        receipt_sha256,
    })
}

fn command_output_sha256(argv: &[String]) -> Result<String> {
    let output = Command::new(&argv[0]).args(&argv[1..]).output()?;
    if !output.status.success() {
        bail!("toolchain probe failed: {:?}: {}", argv, output.status);
    }
    let bytes = serde_json::to_vec(&(output.stdout, output.stderr))?;
    Ok(sha256_bytes(&bytes))
}

fn build_hermit(
    paths: &Paths,
    jobs: usize,
    toolchain: &ToolchainReceipt,
) -> Result<HermitBuildReceipt> {
    let source_sha = git_head(&paths.hermit_repo)?;
    let source_tree = git_tree(&paths.hermit_repo)?;
    let cargo_lock_sha256 = sha256_file(&paths.hermit_repo.join("Cargo.lock"))?;
    let clean_before = repo_clean(&paths.hermit_repo)?;
    if !clean_before {
        bail!("Hermit source must be clean before the causal build");
    }
    if paths.binary.is_file() {
        fs::remove_file(&paths.binary)
            .with_context(|| format!("removing prior Hermit binary {}", paths.binary.display()))?;
    }
    let binary_absent_before = !paths.binary.exists();
    if !binary_absent_before {
        bail!("Hermit binary remained present before causal build");
    }
    let command = hermit_build_command();
    let log_path = paths.artifacts.join("preparation/hermit-build.log");
    let log = File::create(&log_path)?;
    let status = Command::new(&command[0])
        .args(&command[1..])
        .env("CARGO_BUILD_JOBS", jobs.max(1).to_string())
        .current_dir(&paths.hermit_repo)
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .status()?;
    let exit_code = status
        .code()
        .context("Hermit build terminated without an exit code")?;
    if !status.success() || !paths.binary.is_file() {
        bail!(
            "Hermit causal build failed: status={status}; log={}",
            log_path.display()
        );
    }
    let clean_after = repo_clean(&paths.hermit_repo)?;
    if !clean_after
        || git_head(&paths.hermit_repo)? != source_sha
        || git_tree(&paths.hermit_repo)? != source_tree
    {
        bail!("Hermit source tuple changed during the causal build");
    }
    let binary_snapshot = paths.artifacts.join("preparation/hermit.snapshot");
    fs::copy(&paths.binary, &binary_snapshot)?;
    Ok(HermitBuildReceipt {
        source_sha,
        source_tree,
        cargo_lock_sha256,
        clean_before,
        clean_after,
        binary_absent_before,
        command_sha256: sha256_bytes(&serde_json::to_vec(&command)?),
        command,
        toolchain_receipt_sha256: toolchain.receipt_sha256.clone(),
        exit_code,
        log: artifact_fact(paths, &log_path)?,
        output: artifact_fact(paths, &binary_snapshot)?,
    })
}

fn harness_binding(paths: &Paths) -> Result<(String, String, String)> {
    let run_path = paths.experiment.join("run.rs");
    let verify_path = paths.experiment.join("verify.rs");
    let parent_commit = git_head(&paths.parent_repo)?;
    let run_rs_sha256 = sha256_file(&run_path)?;
    let verify_rs_sha256 = sha256_file(&verify_path)?;
    verify_harness_commit(paths, &parent_commit, &run_rs_sha256, &verify_rs_sha256)?;
    Ok((parent_commit, run_rs_sha256, verify_rs_sha256))
}

fn verify_harness_commit(
    paths: &Paths,
    parent_commit: &str,
    run_rs_sha256: &str,
    verify_rs_sha256: &str,
) -> Result<()> {
    if parent_commit.len() != 40 || !parent_commit.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        bail!("parent harness commit is not a 40-hex object name");
    }
    let run_path = paths.experiment.join("run.rs");
    let verify_path = paths.experiment.join("verify.rs");
    for (path, expected_hash) in [(&run_path, run_rs_sha256), (&verify_path, verify_rs_sha256)] {
        let relative = path
            .strip_prefix(&paths.parent_repo)
            .with_context(|| format!("harness path escaped parent: {}", path.display()))?;
        if sha256_file(path)? != expected_hash {
            bail!(
                "working harness bytes differ from the bound commit: {}",
                path.display()
            );
        }
        let status = Command::new("git")
            .args(["diff", "--quiet", "HEAD", "--"])
            .arg(relative)
            .current_dir(&paths.parent_repo)
            .status()?;
        if !status.success() {
            bail!(
                "harness file is not committed at current parent HEAD: {}",
                path.display()
            );
        }
        let object = format!("{parent_commit}:{}", relative.display());
        let output = Command::new("git")
            .args(["show", &object])
            .current_dir(&paths.parent_repo)
            .output()?;
        if !output.status.success() || sha256_bytes(&output.stdout) != expected_hash {
            bail!("bound parent commit does not contain the recorded harness bytes: {object}");
        }
    }
    Ok(())
}

fn guest_set_sha256(tests: &[TestSpec]) -> Result<String> {
    let values: Vec<_> = tests
        .iter()
        .map(|test| (&test.id, &test.binary_sha256))
        .collect();
    Ok(sha256_bytes(&serde_json::to_vec(&values)?))
}

fn resolved_guest_path(paths: &Paths, test: &TestSpec) -> PathBuf {
    if test.compile.is_some() {
        paths
            .artifacts
            .join("guests")
            .join(slug(&test.id))
            .join("guest")
    } else {
        PathBuf::from(&test.argv[0])
    }
}

fn require_repo_at_head(path: &Path) -> Result<()> {
    if !repo_clean(path)? {
        bail!("source repo is dirty: {}", path.display());
    }
    Ok(())
}

fn repo_clean(path: &Path) -> Result<bool> {
    let status = Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(path)
        .output()?;
    if !status.status.success() {
        bail!("git status failed for {}", path.display());
    }
    Ok(status.stdout.is_empty())
}

fn git_head(path: &Path) -> Result<String> {
    let output = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(path)
        .output()?;
    if !output.status.success() {
        bail!("git rev-parse failed for {}", path.display());
    }
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn git_tree(path: &Path) -> Result<String> {
    let output = Command::new("git")
        .args(["rev-parse", "HEAD^{tree}"])
        .current_dir(path)
        .output()?;
    if !output.status.success() {
        bail!("git tree resolution failed for {}", path.display());
    }
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn cargo_lock_git_rev(repo: &Path, repository: &str) -> Result<String> {
    let lock = fs::read_to_string(repo.join("Cargo.lock"))?;
    let markers = [
        format!("github.com/{repository}?rev="),
        format!("github.com/{repository}.git?rev="),
    ];
    let mut revisions = BTreeSet::new();
    for line in lock.lines() {
        for marker in &markers {
            let Some((_, remainder)) = line.split_once(marker) else {
                continue;
            };
            let revision: String = remainder
                .chars()
                .take_while(|character| character.is_ascii_hexdigit())
                .collect();
            if revision.len() == 40 {
                revisions.insert(revision);
            }
        }
    }
    if revisions.len() != 1 {
        bail!(
            "Cargo.lock must resolve exactly one 40-hex revision for {repository}; found {:?}",
            revisions
        );
    }
    Ok(revisions.into_iter().next().unwrap())
}

fn termination_from_status(status: std::process::ExitStatus, timed_out: bool) -> Termination {
    if let Some(code) = status.code() {
        Termination {
            kind: "exit".to_owned(),
            code: Some(code),
            signal: None,
            timed_out,
        }
    } else {
        Termination {
            kind: "signal".to_owned(),
            code: None,
            signal: status.signal(),
            timed_out,
        }
    }
}

fn successful(termination: &Termination) -> bool {
    termination.kind == "exit" && termination.code == Some(0) && !termination.timed_out
}

fn job_cell_id(job: &Job) -> String {
    format!(
        "{}--{}--{}",
        slug(&job.test.id),
        slug(&job.backend),
        slug(&job.observation_mode)
    )
}

fn run_dir(paths: &Paths, kind: &str, run_instance: &str) -> PathBuf {
    paths.experiment.join("runs").join(kind).join(run_instance)
}

fn state_root(paths: &Paths, binding: &RunBinding, binding_sha256: &str) -> PathBuf {
    paths
        .artifacts
        .join("state")
        .join(&binding.run_kind)
        .join(&binding.run_instance)
        .join(binding_sha256)
}

fn canonical_artifact_relative(
    binding: &RunBinding,
    binding_sha256: &str,
    cell_id: &str,
    filename: &str,
) -> String {
    PathBuf::from("artifacts")
        .join(&binding.run_kind)
        .join(&binding.run_instance)
        .join(binding_sha256)
        .join(cell_id)
        .join(filename)
        .display()
        .to_string()
}

fn validate_cached_pair(
    paths: &Paths,
    job: &Job,
    binding: &RunBinding,
    binding_sha256: &str,
    one: &ExecutionRecord,
    two: &ExecutionRecord,
    cell: &CellRecord,
) -> Result<()> {
    let cell_id = job_cell_id(job);
    for (ordinal, execution) in [(1u8, one), (2u8, two)] {
        if execution.schema != SCHEMA
            || execution.record_type != "strict_metric_execution"
            || execution.run_kind != binding.run_kind
            || execution.run_instance != binding.run_instance
            || execution.run_binding_sha256 != binding_sha256
            || execution.cell_id != cell_id
            || execution.test_id != job.test.id
            || execution.backend != job.backend
            || execution.observation_mode != job.observation_mode
            || execution.ordinal != ordinal
            || execution.guest_binary_sha256 != job.test.binary_sha256
            || !execution.attempted
            || execution.error.is_some()
        {
            bail!("cached execution identity/binding mismatch for {cell_id}::{ordinal}");
        }
        let expected_paths = [
            canonical_artifact_relative(
                binding,
                binding_sha256,
                &cell_id,
                &format!("run{ordinal}.stdout"),
            ),
            canonical_artifact_relative(
                binding,
                binding_sha256,
                &cell_id,
                &format!("run{ordinal}.stderr"),
            ),
            canonical_artifact_relative(
                binding,
                binding_sha256,
                &cell_id,
                &format!("run{ordinal}.info.log"),
            ),
        ];
        let facts = [
            &execution.stdout,
            &execution.stderr,
            &execution.ordered_log_stream,
        ];
        for (fact, expected) in facts.into_iter().zip(expected_paths) {
            if fact.path != expected {
                bail!("cached artifact path is not canonical: {}", fact.path);
            }
            verify_artifact_fact(paths, fact)?;
        }
        let log_path = paths.artifacts.join(&execution.ordered_log_stream.path);
        let expected_command = command_argv(paths, job, &log_path);
        if execution.command != expected_command
            || execution.command_sha256 != sha256_bytes(&serde_json::to_vec(&expected_command)?)
            || execution.log_event_count != count_raw_log_events(&log_path)?
        {
            bail!("cached command/log binding mismatch for {cell_id}::{ordinal}");
        }
    }
    let all_paths = BTreeSet::from([
        one.stdout.path.as_str(),
        one.stderr.path.as_str(),
        one.ordered_log_stream.path.as_str(),
        two.stdout.path.as_str(),
        two.stderr.path.as_str(),
        two.ordered_log_stream.path.as_str(),
    ]);
    if all_paths.len() != 6 {
        bail!("cached execution artifacts alias ordinals or streams for {cell_id}");
    }
    let stdout_equal = files_equal(
        &paths.artifacts.join(&one.stdout.path),
        &paths.artifacts.join(&two.stdout.path),
    )?;
    let stderr_equal = files_equal(
        &paths.artifacts.join(&one.stderr.path),
        &paths.artifacts.join(&two.stderr.path),
    )?;
    let log_equal = files_equal(
        &paths.artifacts.join(&one.ordered_log_stream.path),
        &paths.artifacts.join(&two.ordered_log_stream.path),
    )?;
    let termination_equal = one.termination == two.termination;
    let nonzero_log_events = one.log_event_count > 0 && two.log_event_count > 0;
    let successful_exit_both = successful(&one.termination) && successful(&two.termination);
    let raw_equal = stdout_equal && stderr_equal && termination_equal && log_equal;
    let expected_diag =
        canonical_artifact_relative(binding, binding_sha256, &cell_id, "legacy-log-diff.stderr");
    if cell.schema != SCHEMA
        || cell.record_type != "strict_metric_cell"
        || cell.run_kind != binding.run_kind
        || cell.run_instance != binding.run_instance
        || cell.run_binding_sha256 != binding_sha256
        || cell.cell_id != cell_id
        || cell.test_id != job.test.id
        || cell.backend != job.backend
        || cell.observation_mode != job.observation_mode
        || cell.execution_ordinals != vec![1, 2]
        || cell.stdout_equal != stdout_equal
        || cell.stderr_equal != stderr_equal
        || cell.termination_equal != termination_equal
        || cell.ordered_log_stream_equal != log_equal
        || cell.nonzero_log_events != nonzero_log_events
        || cell.successful_exit_both != successful_exit_both
        || cell.raw_observations_equal != raw_equal
        || cell.strict_green != (raw_equal && nonzero_log_events && successful_exit_both)
        || cell.legacy_diagnostic.path != expected_diag
    {
        bail!("cached cell verdict/binding mismatch for {cell_id}");
    }
    verify_artifact_fact(paths, &cell.legacy_diagnostic)?;
    Ok(())
}

fn slug(value: &str) -> String {
    value
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect()
}

fn artifact_fact(paths: &Paths, path: &Path) -> Result<ArtifactFact> {
    let relative = path
        .strip_prefix(&paths.artifacts)
        .with_context(|| format!("artifact escaped root: {}", path.display()))?;
    Ok(ArtifactFact {
        path: relative.display().to_string(),
        sha256: sha256_file(path)?,
        bytes: fs::metadata(path)?.len(),
    })
}

fn verify_artifact_fact(paths: &Paths, fact: &ArtifactFact) -> Result<()> {
    let path = paths.artifacts.join(&fact.path);
    let metadata =
        fs::metadata(&path).with_context(|| format!("missing artifact {}", path.display()))?;
    if !metadata.is_file() || metadata.len() != fact.bytes || sha256_file(&path)? != fact.sha256 {
        bail!("artifact fact mismatch: {}", path.display());
    }
    Ok(())
}

fn count_raw_log_events(path: &Path) -> Result<u64> {
    let file = File::open(path)?;
    let mut count = 0;
    for line in BufReader::new(file).split(b'\n') {
        let line = line?;
        let timestamp_shape = line.len() >= 20
            && line[0..4].iter().all(u8::is_ascii_digit)
            && line.get(4) == Some(&b'-')
            && line.get(7) == Some(&b'-')
            && line.get(10) == Some(&b'T');
        if timestamp_shape {
            count += 1;
        }
    }
    Ok(count)
}

fn files_equal(left: &Path, right: &Path) -> Result<bool> {
    if fs::metadata(left)?.len() != fs::metadata(right)?.len() {
        return Ok(false);
    }
    let mut a = BufReader::new(File::open(left)?);
    let mut b = BufReader::new(File::open(right)?);
    let mut abuf = [0u8; 64 * 1024];
    let mut bbuf = [0u8; 64 * 1024];
    loop {
        let an = a.read(&mut abuf)?;
        let bn = b.read(&mut bbuf)?;
        if an != bn || abuf[..an] != bbuf[..bn] {
            return Ok(false);
        }
        if an == 0 {
            return Ok(true);
        }
    }
}

fn collect_json_records(dir: &Path) -> Result<Vec<serde_json::Value>> {
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut paths: Vec<_> = fs::read_dir(dir)?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().and_then(|s| s.to_str()) == Some("json"))
        .collect();
    paths.sort();
    paths
        .into_iter()
        .map(|path| read_json::<serde_json::Value>(&path))
        .collect()
}

fn write_jsonl_atomic(path: &Path, records: &[serde_json::Value]) -> Result<()> {
    let temp = path.with_extension("jsonl.tmp");
    let mut file = File::create(&temp)?;
    for record in records {
        serde_json::to_writer(&mut file, record)?;
        file.write_all(b"\n")?;
    }
    file.sync_all()?;
    fs::rename(temp, path)?;
    Ok(())
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp = path.with_extension("json.tmp");
    let mut file = File::create(&temp)?;
    serde_json::to_writer_pretty(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(temp, path)?;
    Ok(())
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
    serde_json::from_reader(file).with_context(|| format!("parsing {}", path.display()))
}

fn sha256_file(path: &Path) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("hashing {}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn sha256_bytes(bytes: &[u8]) -> String {
    let mut hash = Sha256::new();
    hash.update(bytes);
    format!("{:x}", hash.finalize())
}

fn epoch_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn path_value(args: &[String], flag: &str, default: &str) -> PathBuf {
    PathBuf::from(value_string(args, flag, default))
}

fn value_string(args: &[String], flag: &str, default: &str) -> String {
    args.windows(2)
        .find(|pair| pair[0] == flag)
        .map(|pair| pair[1].clone())
        .unwrap_or_else(|| default.to_owned())
}

fn required_slug_value(args: &[String], flag: &str) -> Result<String> {
    optional_slug_value(args, flag)?
        .with_context(|| format!("{flag} is required and must be a stable lowercase slug"))
}

fn optional_slug_value(args: &[String], flag: &str) -> Result<Option<String>> {
    let Some(value) = args
        .windows(2)
        .find(|pair| pair[0] == flag)
        .map(|pair| pair[1].clone())
    else {
        return Ok(None);
    };
    let valid = !value.is_empty()
        && value.len() <= 80
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        && !value.starts_with('-')
        && !value.ends_with('-');
    if !valid {
        bail!("{flag} must be a lowercase [a-z0-9-] slug, at most 80 bytes");
    }
    Ok(Some(value))
}

fn value_usize(args: &[String], flag: &str, default: usize) -> usize {
    value_string(args, flag, &default.to_string())
        .parse()
        .unwrap_or(default)
}

fn value_u64(args: &[String], flag: &str, default: u64) -> u64 {
    value_string(args, flag, &default.to_string())
        .parse()
        .unwrap_or(default)
}

unsafe fn libc_setsid() -> i32 {
    unsafe extern "C" {
        fn setsid() -> i32;
    }
    unsafe { setsid() }
}
