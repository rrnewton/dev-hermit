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

#[path = "log_authority.rs"]
mod log_authority;

use anyhow::{bail, Context, Result};
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use wait_timeout::ChildExt;

const SCHEMA: u32 = 4;
const FULL_TESTS: usize = 231;
const BACKENDS: [&str; 6] = ["ptrace", "kvm", "dbi", "sabre", "e9patch", "liteinst"];
const MAIN_MODE: &str = "strict_raw";
const CALIBRATION_TEST: &str = "backend-parity-c/pid-probe";
const SPOT_TESTS: [&str; 2] = ["backend-parity-c/pid-probe", "c-programs/print-memaddrs"];
const HERMETIC_PATH: &str = "/home/newton/.cargo/bin:/usr/local/bin:/usr/bin:/bin";

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
    environment: BTreeMap<String, String>,
    environment_sha256: String,
    receipt_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CompileReceipt {
    command: Vec<String>,
    command_sha256: String,
    inputs: Vec<SourceFact>,
    toolchain_receipt_sha256: String,
    environment: BTreeMap<String, String>,
    environment_sha256: String,
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
    environment: BTreeMap<String, String>,
    environment_sha256: String,
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct PreparedArtifact {
    path: String,
    sha256: String,
    bytes: u64,
    mode: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct TreeEntry {
    relative_path: String,
    kind: String,
    bytes: u64,
    mode: u32,
    sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct TreeManifest {
    entries: Vec<TreeEntry>,
    digest_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct NoncPreparationReceipt {
    protocol: String,
    prepare_command: Vec<String>,
    prepare_command_sha256: String,
    prepare_environment: BTreeMap<String, String>,
    prepare_environment_sha256: String,
    run_environment_template: BTreeMap<String, String>,
    run_environment_template_sha256: String,
    source_chain: Vec<SourceFact>,
    exit_code: i32,
    log: ArtifactFact,
    prepared_artifacts: Vec<PreparedArtifact>,
    prepared_artifacts_sha256: String,
    prepared_input_manifests: BTreeMap<String, TreeManifest>,
    prepared_input_manifests_sha256: String,
    canonical_argv: Vec<String>,
    canonical_argv_sha256: String,
    receipt_sha256: String,
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
    nonc_preparation: Option<NoncPreparationReceipt>,
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
    denominator_decision_sha256: String,
    denominator_decision_semantic_sha256: String,
    parent_commit: String,
    run_rs_sha256: String,
    verify_rs_sha256: String,
    log_authority_rs_sha256: String,
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
    log_parser: String,
    log_level: String,
    log_counts: log_authority::CanonicalLogCounts,
    log_parser_command: Vec<String>,
    log_parser_command_sha256: String,
    log_parser_diagnostic: ArtifactFact,
    stdout: ArtifactFact,
    stderr: ArtifactFact,
    ordered_log_stream: ArtifactFact,
    command: Vec<String>,
    command_sha256: String,
    environment: BTreeMap<String, String>,
    environment_sha256: String,
    input_receipt: ExecutionInputReceipt,
    preparation_receipt_sha256: Option<String>,
    guest_binary_sha256: String,
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct ExecutionInputReceipt {
    schema: u32,
    record_type: String,
    run_kind: String,
    run_instance: String,
    run_binding_sha256: String,
    cell_id: String,
    ordinal: u8,
    preparation_receipt_sha256: Option<String>,
    root_paths: BTreeMap<String, String>,
    execution_root_paths: BTreeMap<String, String>,
    root_manifests: BTreeMap<String, TreeManifest>,
    receipt_sha256: String,
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
    log_authority_rs_sha256: String,
    guest_set_sha256: String,
    input_audit_path: String,
    input_audit_sha256: String,
    requested_manifest_path: String,
    requested_manifest_sha256: String,
    manifest_path: String,
    manifest_sha256: String,
    corpus_c_sha256: String,
    corpus_nonc_sha256: String,
    denominator_decision_path: String,
    denominator_decision_sha256: String,
    denominator_decision_semantic_sha256: String,
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct SpotCompletion {
    schema: u32,
    record_type: String,
    complete: bool,
    run_instance: String,
    run_binding_sha256: String,
    denominator_sha256: String,
    executions_jsonl_sha256: String,
    cells_jsonl_sha256: String,
    tests: Vec<String>,
    backends: Vec<String>,
    observation_modes: Vec<String>,
    run_ordinals: Vec<u8>,
    expected_cells: usize,
    expected_executions: usize,
    attempted_executions: usize,
    nonzero_info_executions: usize,
    successful_exit_executions: usize,
    strict_green_cells: usize,
    raw_equal_cells: usize,
    completion_digest_sha256: String,
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
    log_authority_rs_sha256: String,
    requested_manifest_sha256: String,
    denominator_decision_sha256: String,
    denominator_decision_semantic_sha256: String,
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
    canonical_argv: Vec<String>,
    compile_command: Vec<String>,
    workload_identity_sha256: String,
    sources: Vec<RequestedSource>,
    nonc_preparation: Option<NoncPreparationReceipt>,
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
    denominator_decision_path: String,
    denominator_decision_sha256: String,
    denominator_decision_semantic_sha256: String,
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
    denominator_decision_path: String,
    denominator_decision_sha256: String,
    denominator_decision_semantic_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AliasDecision {
    id: String,
    corpus_row: String,
    wrapper_path: String,
    wrapper_commit: String,
    wrapper_sha256: String,
    wrapper_prepare_action: String,
    wrapper_run_action: String,
    retained_id: String,
    retained_path: String,
    retained_commit: String,
    retained_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct DenominatorDecision {
    schema: u32,
    record_type: String,
    decision: String,
    prior_named_rows: usize,
    corrected_unique_workloads: usize,
    retired_aliases: Vec<AliasDecision>,
    retained_corpus_rows: Vec<String>,
    launcher_decision: String,
    rationale: String,
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
        "self-test" => self_test(&paths),
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
         self-test                    run bounded producer/cache/receipt brackets\n\
         audit-inputs                 prepare/freeze requested rows and fail closed on any gap\n\
         prepare [--jobs 32]        freeze inputs and prepare/compile 231 guests\n\
         run --kind calibration     run 1 test x 6 paths x 2 ordinals\n\
         run --kind full            run 231 x 6 x 2 = 2,772 executions\n\
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

    let manifest = build_requested_manifest(paths, true)?;
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
        if !input.available {
            missing_test_ids.insert(input.id.clone());
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
        && executable_tests == manifest.denominator_expected_tests
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
        denominator_decision_path: manifest.denominator_decision_path,
        denominator_decision_sha256: manifest.denominator_decision_sha256,
        denominator_decision_semantic_sha256: manifest.denominator_decision_semantic_sha256,
    };
    write_json_atomic(&paths.experiment.join("input-audit.json"), &audit)?;
    Ok(audit)
}

fn build_requested_manifest(paths: &Paths, execute_preparation: bool) -> Result<RequestedManifest> {
    let (denominator_decision_sha256, denominator_decision_semantic_sha256) =
        validate_denominator_decision(paths, None)?;
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
            canonical_argv: Vec::new(),
            compile_command,
            workload_identity_sha256,
            available: sources.iter().all(|source| source.exists),
            sources,
            nonc_preparation: None,
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
        let prepared = prepare_nonc_input(paths, fields[0], &argv, execute_preparation)?;
        let workload_identity_sha256 = workload_identity(
            "script_or_interpreter",
            &prepared.canonical_argv,
            &prepared.semantic_sources,
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
            canonical_argv: prepared.canonical_argv,
            compile_command: Vec::new(),
            workload_identity_sha256,
            available: prepared.available,
            sources: prepared.sources,
            nonc_preparation: Some(prepared.receipt),
        });
    }
    inputs.sort_by(|a, b| a.id.cmp(&b.id));
    validate_denominator_decision(paths, Some(&inputs))?;

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
        denominator_decision_path: "denominator-decision.json".to_owned(),
        denominator_decision_sha256,
        denominator_decision_semantic_sha256,
        denominator_expected_tests: FULL_TESTS,
        inputs,
    })
}

fn validate_denominator_decision(
    paths: &Paths,
    inputs: Option<&[RequestedInput]>,
) -> Result<(String, String)> {
    let path = paths.experiment.join("denominator-decision.json");
    let decision: DenominatorDecision = read_json(&path)?;
    let exact = [
        (
            "applications/timed-progress-bar",
            "applications/example-timed-progress-bar",
            "tests/e2e/applications/timed-progress-bar.sh",
            "bb56774c90ae13e63a0a75d6cc1f6c01c69d5734",
            "8475285e1c568e81d543c4ebb402fbc4c3d41dadc9c492263115197c80089d15",
            "test -x /usr/bin/python3",
            "exec \"${BASH_SOURCE[0]%/*}/../../../examples/timed-progress-bar.py\"",
            "examples/timed-progress-bar.py",
            "4c9a4ed94ed492021bd04ba236bb625cb60600c088cafcd84b3ec1bdf9f0e094",
        ),
        (
            "determinism-stress/thread-output",
            "determinism-stress/example-race",
            "tests/e2e/determinism-stress/thread-output.sh",
            "49321e67e6356d3f1f26d897ef8193a21c7e28ef",
            "27b8a443c7fcadebc09e9fe50dc6f6b21b3025690e2d5186ca0245d9b62fbf74",
            "test -x /bin/bash",
            "exec \"${BASH_SOURCE[0]%/*}/../../../examples/race.sh\"",
            "examples/race.sh",
            "00ba1d05db05f914c990da9dcd45c7b41c300cc2db9c5ad7bf5d1b9766a44fc1",
        ),
        (
            "language-runtimes/python-random",
            "language-runtimes/example-python-random",
            "tests/e2e/language-runtimes/python-random.sh",
            "49321e67e6356d3f1f26d897ef8193a21c7e28ef",
            "fb3fb07d73685d3fdfe37ff804ffa46dd7db413b0002fd2237b834ecc2f6c2af",
            "test -x /usr/bin/python3",
            "exec \"${BASH_SOURCE[0]%/*}/../../../examples/rand.py\"",
            "examples/rand.py",
            "e1a32fd938403f394f36555be180f2b17dd497859f28e9aa9ac0dadcd0372343",
        ),
        (
            "system-utils/random-device",
            "system-utils/example-devrand",
            "tests/e2e/system-utils/random-device.sh",
            "49321e67e6356d3f1f26d897ef8193a21c7e28ef",
            "61321a5e85b9ab248bd3b4d01a8678bff01b0674563c74bce763e51f4b1da4cf",
            "command -v hexdump >/dev/null",
            "exec \"${BASH_SOURCE[0]%/*}/../../../examples/devrand.sh\"",
            "examples/devrand.sh",
            "00b605c8021ff67eb7d1615275b828b970b31f7715316ed363a3bd6ce3500f6c",
        ),
    ];
    if decision.schema != SCHEMA
        || decision.record_type != "strict_metric_denominator_decision"
        || decision.decision != "retire_trivial_exec_aliases"
        || decision.prior_named_rows != 235
        || decision.corrected_unique_workloads != FULL_TESTS
        || decision.retired_aliases.len() != exact.len()
        || decision.prior_named_rows - decision.retired_aliases.len()
            != decision.corrected_unique_workloads
        || decision.retained_corpus_rows.len() != exact.len()
        || decision.launcher_decision.trim().is_empty()
        || decision.rationale.trim().is_empty()
    {
        bail!("denominator decision header/arithmetic is malformed");
    }
    let expected: BTreeMap<_, _> = exact.into_iter().map(|entry| (entry.0, entry)).collect();
    let mut alias_ids = BTreeSet::new();
    let mut retained_ids = BTreeSet::new();
    let mut wrapper_paths = BTreeSet::new();
    let mut corpus_rows = BTreeSet::new();
    let mut alias_graph = BTreeMap::new();
    for alias in &decision.retired_aliases {
        let expected = expected
            .get(alias.id.as_str())
            .with_context(|| format!("unexpected retired alias {}", alias.id))?;
        if !alias_ids.insert(alias.id.clone())
            || !retained_ids.insert(alias.retained_id.clone())
            || !wrapper_paths.insert(alias.wrapper_path.clone())
            || !corpus_rows.insert(alias.corpus_row.clone())
        {
            bail!("denominator decision contains duplicate aliases");
        }
        if alias.retained_id != expected.1
            || alias.wrapper_path != expected.2
            || alias.wrapper_commit != expected.3
            || alias.wrapper_sha256 != expected.4
            || alias.wrapper_prepare_action != expected.5
            || alias.wrapper_run_action != expected.6
            || alias.retained_path != expected.7
            || alias.retained_commit != expected.3
            || alias.retained_sha256 != expected.8
            || !alias
                .corpus_row
                .starts_with(&format!("{}|portable|", alias.id))
        {
            bail!(
                "denominator alias decision differs from the exact reviewed mapping: {}",
                alias.id
            );
        }
        let wrapper = git_blob(
            &paths.hermit_repo,
            &alias.wrapper_commit,
            &alias.wrapper_path,
        )?;
        if sha256_bytes(&wrapper) != alias.wrapper_sha256 {
            bail!("historical wrapper blob hash mismatch: {}", alias.id);
        }
        validate_side_effect_free_wrapper(
            &wrapper,
            &alias.wrapper_prepare_action,
            &alias.wrapper_run_action,
        )?;
        let retained = git_blob(
            &paths.hermit_repo,
            &alias.retained_commit,
            &alias.retained_path,
        )?;
        if sha256_bytes(&retained) != alias.retained_sha256 {
            bail!(
                "historical retained payload hash mismatch: {}",
                alias.retained_id
            );
        }
        alias_graph.insert(alias.id.clone(), alias.retained_id.clone());
    }
    for start in alias_graph.keys() {
        let mut seen = BTreeSet::new();
        let mut current = start;
        while let Some(next) = alias_graph.get(current) {
            if !seen.insert(current.clone()) {
                bail!("denominator alias graph contains a cycle at {start}");
            }
            current = next;
        }
    }
    if let Some(inputs) = inputs.filter(|inputs| inputs.len() == FULL_TESTS) {
        let by_id: BTreeMap<_, _> = inputs
            .iter()
            .map(|input| (input.id.as_str(), input))
            .collect();
        if by_id.len() != FULL_TESTS {
            bail!("denominator decision does not resolve to 231 unique requested identities");
        }
        for alias in &decision.retired_aliases {
            if by_id.contains_key(alias.id.as_str()) {
                bail!("retired alias remains in requested manifest: {}", alias.id);
            }
            let retained = by_id.get(alias.retained_id.as_str()).with_context(|| {
                format!(
                    "retained payload missing from corpus: {}",
                    alias.retained_id
                )
            })?;
            if !decision.retained_corpus_rows.contains(&retained.corpus_row) {
                bail!(
                    "retained corpus row is not bound by denominator decision: {}",
                    alias.retained_id
                );
            }
        }
    }
    let semantic_sha256 = sha256_bytes(&serde_json::to_vec(&decision)?);
    Ok((sha256_file(&path)?, semantic_sha256))
}

fn git_blob(repo: &Path, commit: &str, path: &str) -> Result<Vec<u8>> {
    if commit.len() != 40 || !commit.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        bail!("historical blob commit is not 40-hex: {commit}");
    }
    let object = format!("{commit}:{path}");
    let output = Command::new("/usr/bin/git")
        .args(["show", &object])
        .current_dir(repo)
        .env_clear()
        .envs(hermetic_environment())
        .output()?;
    if !output.status.success() {
        bail!("historical decision blob is missing: {object}");
    }
    Ok(output.stdout)
}

fn validate_side_effect_free_wrapper(bytes: &[u8], prepare: &str, run: &str) -> Result<()> {
    let text = std::str::from_utf8(bytes).context("historical wrapper is not UTF-8")?;
    let operational: Vec<_> = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect();
    let expected = vec![
        "set -euo pipefail".to_owned(),
        "case ${1:-} in".to_owned(),
        format!("--prepare) {prepare} ;;"),
        format!("--run) {run} ;;"),
        "*) echo \"usage: $0 --prepare|--run\" >&2; exit 2 ;;".to_owned(),
        "esac".to_owned(),
    ];
    if operational != expected {
        bail!("historical wrapper has side effects or unsupported control flow");
    }
    Ok(())
}

struct PreparedNoncInput {
    sources: Vec<RequestedSource>,
    semantic_sources: Vec<RequestedSource>,
    canonical_argv: Vec<String>,
    receipt: NoncPreparationReceipt,
    available: bool,
}

fn prepare_nonc_input(
    paths: &Paths,
    id: &str,
    argv: &[String],
    execute: bool,
) -> Result<PreparedNoncInput> {
    let canonical_argv = canonical_nonc_argv(paths, argv)?;
    let sources = discover_nonc_sources(paths, argv)?;
    let semantic_sources = discover_nonc_sources(paths, &canonical_argv)?;
    let source_chain: Vec<_> = sources
        .iter()
        .filter_map(|source| {
            source.sha256.as_ref().map(|sha256| SourceFact {
                role: source.role.clone(),
                path: source.path.clone(),
                sha256: sha256.clone(),
            })
        })
        .collect();

    let cell = paths
        .artifacts
        .join("preparation")
        .join("nonc")
        .join(slug(id));
    let home = cell.join("home");
    let xdg = cell.join("xdg-config");
    let tmp = cell.join("tmp");
    let fixtures = cell.join("fixtures");
    let captures = cell.join("captures");
    if execute {
        if cell.exists() {
            fs::remove_dir_all(&cell).with_context(|| {
                format!("replacing owned non-C preparation cell {}", cell.display())
            })?;
        }
        for directory in [&home, &xdg, &tmp, &fixtures, &captures] {
            fs::create_dir_all(directory)?;
        }
        let source_xdg = paths.hermit_repo.join("tests/e2e/xdg-config");
        if source_xdg.is_dir() {
            copy_tree(&source_xdg, &xdg)?;
        }
    }

    let mut prepare_environment = hermetic_environment();
    prepare_environment.extend([
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), xdg.display().to_string()),
        ("TMPDIR".to_owned(), tmp.display().to_string()),
        ("E2E_TMPDIR".to_owned(), tmp.display().to_string()),
        ("E2E_FIXTURE_DIR".to_owned(), fixtures.display().to_string()),
    ]);
    let mut run_environment_template = hermetic_environment();
    run_environment_template.extend([
        ("HOME".to_owned(), "${EXECUTION_HOME}".to_owned()),
        (
            "XDG_CONFIG_HOME".to_owned(),
            "${EXECUTION_XDG_CONFIG_HOME}".to_owned(),
        ),
        ("TMPDIR".to_owned(), "${EXECUTION_TMPDIR}".to_owned()),
        ("E2E_TMPDIR".to_owned(), "${EXECUTION_TMPDIR}".to_owned()),
        (
            "E2E_FIXTURE_DIR".to_owned(),
            "${EXECUTION_FIXTURE_DIR}".to_owned(),
        ),
    ]);

    let wrapper_protocol = argv.last().map(String::as_str) == Some("--run")
        && resolve_program(paths, &argv[0])?
            .extension()
            .and_then(|value| value.to_str())
            == Some("sh");
    let prepare_command = if wrapper_protocol {
        let mut command = argv.to_vec();
        command[0] = resolve_program(paths, &command[0])?.display().to_string();
        *command.last_mut().unwrap() = "--prepare".to_owned();
        command
    } else {
        vec![
            "/usr/bin/test".to_owned(),
            "-x".to_owned(),
            direct_probe_path(paths, argv)?.display().to_string(),
        ]
    };
    let log_path = cell.join("prepare.log");
    let exit_code = if execute {
        let log = File::create(&log_path)?;
        let mut command = Command::new(&prepare_command[0]);
        command
            .args(&prepare_command[1..])
            .current_dir(&paths.hermit_repo)
            .env_clear()
            .envs(&prepare_environment)
            .stdout(Stdio::from(log.try_clone()?))
            .stderr(Stdio::from(log));
        match command.status() {
            Ok(status) => status.code().unwrap_or(128),
            Err(error) => {
                fs::write(&log_path, format!("prepare spawn failed: {error}\n"))?;
                127
            }
        }
    } else {
        if !log_path.is_file() {
            bail!("non-C preparation receipt log is missing for {id}");
        }
        0
    };
    let prepared_artifacts = collect_prepared_artifacts(paths, &cell, &log_path)?;
    let prepared_input_manifests = prepared_input_roots(&cell)
        .into_iter()
        .map(|(name, root)| Ok((name, snapshot_tree(&root)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut receipt = NoncPreparationReceipt {
        protocol: if wrapper_protocol {
            "e2e-shell-prepare-run-v1".to_owned()
        } else {
            "direct-executable-probe-v1".to_owned()
        },
        prepare_command_sha256: sha256_bytes(&serde_json::to_vec(&prepare_command)?),
        prepare_command,
        prepare_environment_sha256: sha256_bytes(&serde_json::to_vec(&prepare_environment)?),
        prepare_environment,
        run_environment_template_sha256: sha256_bytes(&serde_json::to_vec(
            &run_environment_template,
        )?),
        run_environment_template,
        source_chain,
        exit_code,
        log: artifact_fact(paths, &log_path)?,
        prepared_artifacts_sha256: sha256_bytes(&serde_json::to_vec(&prepared_artifacts)?),
        prepared_artifacts,
        prepared_input_manifests_sha256: sha256_bytes(&serde_json::to_vec(
            &prepared_input_manifests,
        )?),
        prepared_input_manifests,
        canonical_argv_sha256: sha256_bytes(&serde_json::to_vec(&canonical_argv)?),
        canonical_argv: canonical_argv.clone(),
        receipt_sha256: String::new(),
    };
    receipt.receipt_sha256 = nonc_receipt_sha256(&receipt)?;
    let available = sources.iter().all(|source| source.exists) && exit_code == 0;
    Ok(PreparedNoncInput {
        sources,
        semantic_sources,
        canonical_argv,
        receipt,
        available,
    })
}

fn nonc_receipt_sha256(receipt: &NoncPreparationReceipt) -> Result<String> {
    Ok(sha256_bytes(&serde_json::to_vec(&serde_json::json!({
        "protocol": receipt.protocol,
        "prepare_command": receipt.prepare_command,
        "prepare_command_sha256": receipt.prepare_command_sha256,
        "prepare_environment": receipt.prepare_environment,
        "prepare_environment_sha256": receipt.prepare_environment_sha256,
        "run_environment_template": receipt.run_environment_template,
        "run_environment_template_sha256": receipt.run_environment_template_sha256,
        "source_chain": receipt.source_chain,
        "exit_code": receipt.exit_code,
        "log": receipt.log,
        "prepared_artifacts": receipt.prepared_artifacts,
        "prepared_artifacts_sha256": receipt.prepared_artifacts_sha256,
        "prepared_input_manifests": receipt.prepared_input_manifests,
        "prepared_input_manifests_sha256": receipt.prepared_input_manifests_sha256,
        "canonical_argv": receipt.canonical_argv,
        "canonical_argv_sha256": receipt.canonical_argv_sha256,
    }))?))
}

fn discover_nonc_sources(paths: &Paths, argv: &[String]) -> Result<Vec<RequestedSource>> {
    let mut pending = VecDeque::new();
    let mut discovered = BTreeSet::new();
    let mut scanned = BTreeSet::new();
    let launcher = resolve_program(paths, &argv[0])?;
    discovered.insert(launcher.clone());
    if let Some(script) = direct_script_path(paths, argv)? {
        pending.push_back(script);
    } else if launcher.starts_with(&paths.hermit_repo) {
        pending.push_back(launcher);
    }

    while let Some(path) = pending.pop_front() {
        let path = path.canonicalize().unwrap_or(path);
        discovered.insert(path.clone());
        if !scanned.insert(path.clone()) || !path.is_file() {
            continue;
        }
        if let Some(interpreter) = shebang_interpreter(paths, &path)? {
            discovered.insert(interpreter);
        }
        if path.starts_with(&paths.hermit_repo) {
            let contents = fs::read_to_string(&path).unwrap_or_default();
            for nested in referenced_repo_files(paths, &path, &contents) {
                if !discovered.contains(&nested) {
                    pending.push_back(nested);
                }
            }
        }
    }

    Ok(discovered
        .into_iter()
        .enumerate()
        .map(|(index, path)| {
            let exists = path.is_file();
            Ok(RequestedSource {
                role: format!("source_{:03}", index + 1),
                path: path.display().to_string(),
                exists,
                sha256: exists.then(|| sha256_file(&path)).transpose()?,
            })
        })
        .collect::<Result<Vec<_>>>()?)
}

fn direct_script_path(paths: &Paths, argv: &[String]) -> Result<Option<PathBuf>> {
    if argv.len() == 3
        && argv[1] == "-c"
        && matches!(
            Path::new(&argv[0])
                .file_name()
                .and_then(|value| value.to_str()),
            Some("bash" | "sh")
        )
        && !argv[2]
            .bytes()
            .any(|byte| b";&|`$()<>*?[]{}".contains(&byte))
    {
        return Ok(Some(resolve_program(paths, &argv[2])?));
    }
    let program = resolve_program(paths, &argv[0])?;
    let has_shebang = fs::read(&program)
        .ok()
        .is_some_and(|bytes| bytes.starts_with(b"#!"));
    Ok(has_shebang.then_some(program))
}

fn direct_probe_path(paths: &Paths, argv: &[String]) -> Result<PathBuf> {
    direct_script_path(paths, argv)?.map_or_else(|| resolve_program(paths, &argv[0]), Ok)
}

fn resolve_program(paths: &Paths, value: &str) -> Result<PathBuf> {
    let declared = PathBuf::from(value);
    if declared.is_absolute() {
        return Ok(declared.canonicalize().unwrap_or(declared));
    }
    if value.contains('/') {
        return Ok(paths.hermit_repo.join(declared));
    }
    for directory in std::env::split_paths(HERMETIC_PATH) {
        let candidate = directory.join(value);
        if candidate.is_file() && fs::metadata(&candidate)?.permissions().mode() & 0o111 != 0 {
            return Ok(candidate.canonicalize().unwrap_or(candidate));
        }
    }
    bail!("program {value:?} is absent from the exact hermetic PATH {HERMETIC_PATH}")
}

fn shebang_interpreter(paths: &Paths, script: &Path) -> Result<Option<PathBuf>> {
    let bytes = fs::read(script)?;
    let Some(first) = bytes.split(|byte| *byte == b'\n').next() else {
        return Ok(None);
    };
    let Ok(first) = std::str::from_utf8(first) else {
        return Ok(None);
    };
    let Some(spec) = first.strip_prefix("#!") else {
        return Ok(None);
    };
    let words = shell_words::split(spec.trim())?;
    if words.is_empty() {
        return Ok(None);
    }
    if words[0] == "/usr/bin/env" {
        let program = words
            .iter()
            .skip(1)
            .find(|value| !value.starts_with('-'))
            .context("/usr/bin/env shebang lacks a program")?;
        Ok(Some(resolve_program(paths, program)?))
    } else {
        Ok(Some(resolve_program(paths, &words[0])?))
    }
}

fn referenced_repo_files(paths: &Paths, script: &Path, contents: &str) -> Vec<PathBuf> {
    let mut result = BTreeSet::new();
    for (marker, base) in [
        ("$ROOT_DIR/", paths.hermit_repo.clone()),
        (
            "${BASH_SOURCE[0]%/*}/",
            script.parent().unwrap_or(&paths.hermit_repo).to_path_buf(),
        ),
    ] {
        let mut rest = contents;
        while let Some(index) = rest.find(marker) {
            let tail = &rest[index + marker.len()..];
            let value: String = tail
                .chars()
                .take_while(|character| {
                    !character.is_whitespace()
                        && !matches!(*character, '\'' | '"' | ';' | ')' | '(')
                })
                .collect();
            if !value.is_empty() {
                let candidate = base.join(&value);
                if candidate.is_file() {
                    result.insert(candidate.canonicalize().unwrap_or(candidate));
                }
            }
            rest = &tail[value.len()..];
        }
    }
    result.into_iter().collect()
}

fn self_test(default_paths: &Paths) -> Result<()> {
    let root = std::env::temp_dir().join(format!(
        "strict-metric-producer-selftest-{}-{}",
        std::process::id(),
        epoch_ms()
    ));
    if root.exists() {
        bail!("producer self-test root already exists: {}", root.display());
    }
    fs::create_dir_all(&root)?;
    log_authority::self_test(&default_paths.binary, &root.join("log-authority"))?;

    let mut paths = default_paths.clone();
    paths.artifacts = root.join("artifacts");
    paths.experiment = root.join("experiment");
    fs::create_dir_all(paths.artifacts.join("compile-logs"))?;
    fs::create_dir_all(paths.artifacts.join("guests/tiny"))?;
    fs::create_dir_all(&paths.experiment)?;
    fs::copy(
        default_paths.experiment.join("denominator-decision.json"),
        paths.experiment.join("denominator-decision.json"),
    )?;
    validate_denominator_decision(&paths, None)?;
    let decision_path = paths.experiment.join("denominator-decision.json");
    let original_decision = fs::read(&decision_path)?;
    let mut malformed_decision: DenominatorDecision = read_json(&decision_path)?;
    malformed_decision.prior_named_rows += 1;
    write_json_atomic(&decision_path, &malformed_decision)?;
    require_refusal(
        "rehashed semantically malformed denominator decision",
        validate_denominator_decision(&paths, None).map(|_| ()),
    )?;
    fs::write(&decision_path, original_decision)?;

    let mut healthy_spot = SpotCompletion {
        schema: SCHEMA,
        record_type: "strict_metric_spot_completion".to_owned(),
        complete: true,
        run_instance: "producer-selftest-spot".to_owned(),
        run_binding_sha256: "1".repeat(64),
        denominator_sha256: "2".repeat(64),
        executions_jsonl_sha256: "3".repeat(64),
        cells_jsonl_sha256: "4".repeat(64),
        tests: SPOT_TESTS.iter().map(|value| (*value).to_owned()).collect(),
        backends: BACKENDS.iter().map(|value| (*value).to_owned()).collect(),
        observation_modes: vec![
            "heap".to_owned(),
            "heap_stack".to_owned(),
            "stack".to_owned(),
        ],
        run_ordinals: vec![1, 2],
        expected_cells: 36,
        expected_executions: 72,
        attempted_executions: 72,
        nonzero_info_executions: 72,
        successful_exit_executions: 72,
        strict_green_cells: 36,
        raw_equal_cells: 36,
        completion_digest_sha256: String::new(),
    };
    healthy_spot.completion_digest_sha256 = spot_completion_digest(&healthy_spot)?;
    validate_spot_completion(&healthy_spot, "producer-selftest-spot")?;
    let mut unhealthy_spot = healthy_spot.clone();
    unhealthy_spot.successful_exit_executions = 71;
    unhealthy_spot.completion_digest_sha256 = spot_completion_digest(&unhealthy_spot)?;
    require_refusal(
        "digest-valid unhealthy spot completion",
        validate_spot_completion(&unhealthy_spot, "producer-selftest-spot"),
    )?;

    let source = root.join("tiny.c");
    fs::write(&source, b"int main(void) { return 0; }\n")?;
    let output = paths.artifacts.join("guests/tiny/guest");
    let compile_log = paths.artifacts.join("compile-logs/tiny.log");
    let compile_command = c_compile_command(&source, &[], &[], &output);
    let log = File::create(&compile_log)?;
    let compile_environment = hermetic_environment();
    let status = Command::new(&compile_command[0])
        .args(&compile_command[1..])
        .env_clear()
        .envs(&compile_environment)
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .status()?;
    if !status.success() || !output.is_file() {
        bail!("bounded real tiny compiler command failed: {status}");
    }
    let toolchain = capture_toolchain()?;
    let compile = CompileSpec {
        source: source.display().to_string(),
        cflags: Vec::new(),
        extra_sources: Vec::new(),
        command: compile_command.clone(),
    };
    let compile_receipt = CompileReceipt {
        command: compile_command.clone(),
        command_sha256: sha256_bytes(&serde_json::to_vec(&compile_command)?),
        inputs: vec![SourceFact {
            role: "primary".to_owned(),
            path: source.display().to_string(),
            sha256: sha256_file(&source)?,
        }],
        toolchain_receipt_sha256: toolchain.receipt_sha256.clone(),
        environment_sha256: sha256_bytes(&serde_json::to_vec(&compile_environment)?),
        environment: compile_environment,
        exit_code: status.code().context("tiny compiler exited without code")?,
        log: artifact_fact(&paths, &compile_log)?,
        output: artifact_fact(&paths, &output)?,
    };
    let test = TestSpec {
        id: "fixture/tiny-compile".to_owned(),
        lane: "portable".to_owned(),
        kind: "compiled_c".to_owned(),
        argv: vec![output.display().to_string()],
        source_sha256: sha256_file(&source)?,
        binary_sha256: sha256_file(&output)?,
        workload_identity_sha256: sha256_bytes(b"fixture/tiny-compile"),
        compile: Some(compile),
        compile_receipt: Some(compile_receipt),
        nonc_preparation: None,
    };
    validate_test_preparation(&paths, &test, &toolchain.receipt_sha256)?;

    let mut missing = test.clone();
    missing.compile_receipt = None;
    require_refusal(
        "missing compile receipt",
        validate_test_preparation(&paths, &missing, &toolchain.receipt_sha256),
    )?;
    let mut tampered = test.clone();
    tampered.compile_receipt.as_mut().unwrap().output.sha256 = "f".repeat(64);
    require_refusal(
        "tampered compile receipt",
        validate_test_preparation(&paths, &tampered, &toolchain.receipt_sha256),
    )?;
    let mut relabelled = test.clone();
    relabelled.compile_receipt.as_mut().unwrap().inputs[0].role = "extra_source_1".to_owned();
    require_refusal(
        "relabelled compile receipt",
        validate_test_preparation(&paths, &relabelled, &toolchain.receipt_sha256),
    )?;
    let mut partial = test.clone();
    partial.compile = None;
    require_refusal(
        "partial compile receipt",
        validate_test_preparation(&paths, &partial, &toolchain.receipt_sha256),
    )?;

    let nonc_id = "fixture/nonc-inputs";
    let nonc_argv = vec!["/usr/bin/true".to_owned()];
    let mut prepared = prepare_nonc_input(&paths, nonc_id, &nonc_argv, true)?;
    let preparation_cell = paths
        .artifacts
        .join("preparation")
        .join("nonc")
        .join(slug(nonc_id));
    for (name, value) in [
        ("home", b"home-sentinel\n".as_slice()),
        ("xdg_config", b"xdg-sentinel\n".as_slice()),
        ("tmp", b"tmp-sentinel\n".as_slice()),
        ("fixtures", b"fixture-sentinel\n".as_slice()),
    ] {
        let root = prepared_input_roots(&preparation_cell)[name].clone();
        let sentinel = root.join("authority-sentinel");
        fs::write(&sentinel, value)?;
        fs::set_permissions(&sentinel, fs::Permissions::from_mode(0o640))?;
    }
    let preparation_log = preparation_cell.join("prepare.log");
    prepared.receipt.prepared_artifacts =
        collect_prepared_artifacts(&paths, &preparation_cell, &preparation_log)?;
    prepared.receipt.prepared_artifacts_sha256 =
        sha256_bytes(&serde_json::to_vec(&prepared.receipt.prepared_artifacts)?);
    prepared.receipt.prepared_input_manifests = prepared_input_roots(&preparation_cell)
        .into_iter()
        .map(|(name, root)| Ok((name, snapshot_tree(&root)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    prepared.receipt.prepared_input_manifests_sha256 = sha256_bytes(&serde_json::to_vec(
        &prepared.receipt.prepared_input_manifests,
    )?);
    prepared.receipt.receipt_sha256 = nonc_receipt_sha256(&prepared.receipt)?;
    let nonc_test = TestSpec {
        id: nonc_id.to_owned(),
        lane: "portable".to_owned(),
        kind: "script_or_interpreter".to_owned(),
        argv: nonc_argv,
        source_sha256: sha256_bytes(&serde_json::to_vec(&prepared.receipt.source_chain)?),
        binary_sha256: sha256_file(Path::new("/usr/bin/true"))?,
        workload_identity_sha256: sha256_bytes(b"fixture/nonc-inputs"),
        compile: None,
        compile_receipt: None,
        nonc_preparation: Some(prepared.receipt),
    };
    validate_test_preparation(&paths, &nonc_test, &toolchain.receipt_sha256)?;

    let binding = RunBinding {
        schema: SCHEMA,
        record_type: "strict_metric_run_binding".to_owned(),
        run_kind: "calibration".to_owned(),
        run_instance: "producer-selftest".to_owned(),
        parent_commit: "a".repeat(40),
        run_rs_sha256: "b".repeat(64),
        verify_rs_sha256: "c".repeat(64),
        log_authority_rs_sha256: "d".repeat(64),
        requested_manifest_sha256: "e".repeat(64),
        denominator_decision_sha256: "5".repeat(64),
        denominator_decision_semantic_sha256: "6".repeat(64),
        input_audit_sha256: "1".repeat(64),
        manifest_sha256: "2".repeat(64),
        denominator_sha256: "3".repeat(64),
        hermit_binary_sha256: sha256_file(&paths.binary)?,
        guest_set_sha256: "4".repeat(64),
        required_spot_completion: None,
    };
    let binding_sha256 = sha256_bytes(b"producer-selftest-binding");
    let job = Job {
        run_kind: binding.run_kind.clone(),
        test: nonc_test.clone(),
        backend: "ptrace".to_owned(),
        observation_mode: MAIN_MODE.to_owned(),
    };
    let cell_id = job_cell_id(&job);
    let artifact_dir = paths
        .artifacts
        .join("artifacts")
        .join(&binding.run_kind)
        .join(&binding.run_instance)
        .join(&binding_sha256)
        .join(&cell_id);
    fs::create_dir_all(&artifact_dir)?;
    let one = make_self_test_execution(&paths, &job, &binding, &binding_sha256, 1, &artifact_dir)?;
    let two = make_self_test_execution(&paths, &job, &binding, &binding_sha256, 2, &artifact_dir)?;
    let diagnostic = artifact_dir.join("legacy-log-diff.stderr");
    fs::write(&diagnostic, b"")?;
    let cell = CellRecord {
        schema: SCHEMA,
        record_type: "strict_metric_cell".to_owned(),
        run_kind: binding.run_kind.clone(),
        run_instance: binding.run_instance.clone(),
        run_binding_sha256: binding_sha256.clone(),
        cell_id: cell_id.clone(),
        test_id: job.test.id.clone(),
        backend: job.backend.clone(),
        observation_mode: job.observation_mode.clone(),
        execution_ordinals: vec![1, 2],
        stdout_equal: true,
        stderr_equal: true,
        termination_equal: true,
        ordered_log_stream_equal: true,
        nonzero_log_events: true,
        successful_exit_both: true,
        raw_observations_equal: true,
        strict_green: true,
        legacy_log_match: true,
        legacy_green: true,
        legacy_comparator: "self-test".to_owned(),
        legacy_diagnostic: artifact_fact(&paths, &diagnostic)?,
    };
    validate_cached_pair(&paths, &job, &binding, &binding_sha256, &one, &two, &cell)?;

    let environment_output = Command::new("/usr/bin/env")
        .env("PYTHONPATH", "hostile-ambient-value")
        .env("RUST_LOG", "hostile-ambient-value")
        .env_clear()
        .envs(&one.environment)
        .output()?;
    let observed_environment = String::from_utf8(environment_output.stdout)?;
    if !environment_output.status.success()
        || observed_environment.contains("PYTHONPATH=")
        || observed_environment.contains("RUST_LOG=")
        || !observed_environment.contains(&format!("PATH={HERMETIC_PATH}\n"))
    {
        bail!("hermetic process positive sentinel leaked hostile ambient environment");
    }

    let home = paths.artifacts.join(&one.input_receipt.root_paths["home"]);
    let sentinel = home.join("authority-sentinel");
    let sentinel_bytes = fs::read(&sentinel)?;
    let sentinel_mode = fs::metadata(&sentinel)?.permissions().mode() & 0o7777;
    let added = home.join("planted-addition");
    fs::write(&added, b"added")?;
    require_refusal(
        "execution input tree addition",
        validate_cached_pair(&paths, &job, &binding, &binding_sha256, &one, &two, &cell),
    )?;
    fs::remove_file(&added)?;
    fs::remove_file(&sentinel)?;
    require_refusal(
        "execution input tree removal",
        validate_cached_pair(&paths, &job, &binding, &binding_sha256, &one, &two, &cell),
    )?;
    fs::write(&sentinel, &sentinel_bytes)?;
    fs::set_permissions(&sentinel, fs::Permissions::from_mode(sentinel_mode))?;
    fs::write(&sentinel, b"wrong bytes")?;
    require_refusal(
        "execution input tree byte change",
        validate_cached_pair(&paths, &job, &binding, &binding_sha256, &one, &two, &cell),
    )?;
    fs::write(&sentinel, &sentinel_bytes)?;
    fs::set_permissions(&sentinel, fs::Permissions::from_mode(sentinel_mode ^ 0o100))?;
    require_refusal(
        "execution input tree mode change",
        validate_cached_pair(&paths, &job, &binding, &binding_sha256, &one, &two, &cell),
    )?;
    fs::set_permissions(&sentinel, fs::Permissions::from_mode(sentinel_mode))?;

    let mut wrong_seed = one.clone();
    wrong_seed.input_receipt.root_manifests.insert(
        "home".to_owned(),
        wrong_seed.input_receipt.root_manifests["fixtures"].clone(),
    );
    wrong_seed.input_receipt.receipt_sha256 =
        execution_input_receipt_sha256(&wrong_seed.input_receipt)?;
    require_refusal(
        "rehashed semantically wrong input seed",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &wrong_seed,
            &two,
            &cell,
        ),
    )?;
    let mut missing_manifest = one.clone();
    missing_manifest.input_receipt.root_manifests.remove("home");
    missing_manifest.input_receipt.receipt_sha256 =
        execution_input_receipt_sha256(&missing_manifest.input_receipt)?;
    require_refusal(
        "missing execution input manifest",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &missing_manifest,
            &two,
            &cell,
        ),
    )?;
    let mut swapped_home_xdg = one.clone();
    let home = swapped_home_xdg
        .input_receipt
        .root_paths
        .remove("home")
        .context("self-test HOME seed missing")?;
    let xdg = swapped_home_xdg
        .input_receipt
        .root_paths
        .remove("xdg_config")
        .context("self-test XDG seed missing")?;
    swapped_home_xdg
        .input_receipt
        .root_paths
        .insert("home".to_owned(), xdg);
    swapped_home_xdg
        .input_receipt
        .root_paths
        .insert("xdg_config".to_owned(), home);
    let home = swapped_home_xdg
        .input_receipt
        .execution_root_paths
        .remove("home")
        .context("self-test HOME execution root missing")?;
    let xdg = swapped_home_xdg
        .input_receipt
        .execution_root_paths
        .remove("xdg_config")
        .context("self-test XDG execution root missing")?;
    swapped_home_xdg
        .input_receipt
        .execution_root_paths
        .insert("home".to_owned(), xdg);
    swapped_home_xdg
        .input_receipt
        .execution_root_paths
        .insert("xdg_config".to_owned(), home);
    swapped_home_xdg.input_receipt.receipt_sha256 =
        execution_input_receipt_sha256(&swapped_home_xdg.input_receipt)?;
    require_refusal(
        "swapped HOME/XDG input roots",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &swapped_home_xdg,
            &two,
            &cell,
        ),
    )?;
    let mut cross_cell = one.clone();
    cross_cell.input_receipt.cell_id = "different-cell".to_owned();
    cross_cell.input_receipt.receipt_sha256 =
        execution_input_receipt_sha256(&cross_cell.input_receipt)?;
    require_refusal(
        "cross-cell input receipt swap",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &cross_cell,
            &two,
            &cell,
        ),
    )?;
    let mut shared_ordinal = two.clone();
    shared_ordinal.input_receipt.root_paths = one.input_receipt.root_paths.clone();
    shared_ordinal.input_receipt.execution_root_paths =
        one.input_receipt.execution_root_paths.clone();
    shared_ordinal.input_receipt.receipt_sha256 =
        execution_input_receipt_sha256(&shared_ordinal.input_receipt)?;
    require_refusal(
        "shared ordinal HOME/XDG/TMP/fixture roots",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &one,
            &shared_ordinal,
            &cell,
        ),
    )?;
    let mut hostile_environment = one.clone();
    hostile_environment
        .environment
        .insert("PYTHONPATH".to_owned(), "hostile".to_owned());
    hostile_environment.environment_sha256 =
        sha256_bytes(&serde_json::to_vec(&hostile_environment.environment)?);
    require_refusal(
        "rehashed hostile ambient execution environment",
        validate_cached_pair(
            &paths,
            &job,
            &binding,
            &binding_sha256,
            &hostile_environment,
            &two,
            &cell,
        ),
    )?;

    let state = state_root(&paths, &binding, &binding_sha256);
    let execution_dir = state.join("executions");
    let cell_dir = state.join("cells");
    fs::create_dir_all(&execution_dir)?;
    fs::create_dir_all(&cell_dir)?;
    let one_record_path = execution_dir.join(format!("{cell_id}--1.json"));
    let two_record_path = execution_dir.join(format!("{cell_id}--2.json"));
    let cell_record_path = cell_dir.join(format!("{cell_id}.json"));

    let mut relabelled_cache = one.clone();
    relabelled_cache.preparation_receipt_sha256 = Some("9".repeat(64));
    write_json_atomic(&one_record_path, &relabelled_cache)?;
    write_json_atomic(&two_record_path, &two)?;
    write_json_atomic(&cell_record_path, &cell)?;
    require_refusal(
        "relabelled cached preparation",
        run_pair(&paths, &job, &binding, &binding_sha256, 1),
    )?;
    let stdout_path = paths.artifacts.join(&one.stdout.path);
    write_json_atomic(&one_record_path, &one)?;
    fs::write(&stdout_path, b"tampered")?;
    require_refusal(
        "tampered cached artifact",
        run_pair(&paths, &job, &binding, &binding_sha256, 1),
    )?;
    fs::write(&stdout_path, b"")?;
    fs::remove_file(&stdout_path)?;
    require_refusal(
        "missing cached artifact",
        run_pair(&paths, &job, &binding, &binding_sha256, 1),
    )?;
    fs::write(&stdout_path, b"")?;

    fs::remove_dir_all(&state)?;
    fs::create_dir_all(state.join("executions"))?;
    write_json_atomic(
        &state.join("executions").join(format!("{cell_id}--1.json")),
        &one,
    )?;
    require_refusal(
        "partial cached receipt set",
        run_pair(&paths, &job, &binding, &binding_sha256, 1),
    )?;

    fs::remove_dir_all(&root)?;
    println!(
        "PRODUCER SELF-TEST PASS: positive real compiler, hermetic-environment, isolated-input-tree, canonical-parser, and healthy-spot sentinels; negatives=20 refused, including compile/cache gaps, hostile ambient env, input add/remove/byte/mode changes, wrong/missing/swapped/shared/cross-cell input receipts, malformed denominator decision, and unhealthy spot evidence"
    );
    Ok(())
}

fn make_self_test_execution(
    paths: &Paths,
    job: &Job,
    binding: &RunBinding,
    binding_sha256: &str,
    ordinal: u8,
    artifact_dir: &Path,
) -> Result<ExecutionRecord> {
    let stdout = artifact_dir.join(format!("run{ordinal}.stdout"));
    let stderr = artifact_dir.join(format!("run{ordinal}.stderr"));
    let log = artifact_dir.join(format!("run{ordinal}.info.log"));
    let parser_diagnostic = artifact_dir.join(format!("run{ordinal}.log-inspection.stderr"));
    fs::write(&stdout, b"")?;
    fs::write(&stderr, b"")?;
    fs::write(
        &log,
        b"2026-08-06T10:00:00.000000Z  INFO detcore: DETLOG fixture value=7\n",
    )?;
    let inspection = log_authority::inspect_file(&paths.binary, &log)?;
    fs::write(&parser_diagnostic, &inspection.diagnostic_stderr)?;
    let command = command_argv(paths, job, &log);
    let (environment, input_receipt) =
        execution_inputs(paths, job, binding, binding_sha256, ordinal, true)?;
    Ok(ExecutionRecord {
        schema: SCHEMA,
        record_type: "strict_metric_execution".to_owned(),
        run_kind: binding.run_kind.clone(),
        run_instance: binding.run_instance.clone(),
        run_binding_sha256: binding_sha256.to_owned(),
        cell_id: job_cell_id(job),
        test_id: job.test.id.clone(),
        backend: job.backend.clone(),
        observation_mode: job.observation_mode.clone(),
        ordinal,
        attempted: true,
        termination: Termination {
            kind: "exit".to_owned(),
            code: Some(0),
            signal: None,
            timed_out: false,
        },
        duration_ms: 1,
        log_event_count: inspection.counts.info_messages,
        log_parser: inspection.parser_id,
        log_level: "INFO".to_owned(),
        log_counts: inspection.counts,
        log_parser_command_sha256: sha256_bytes(&serde_json::to_vec(&inspection.command)?),
        log_parser_command: inspection.command,
        log_parser_diagnostic: artifact_fact(paths, &parser_diagnostic)?,
        stdout: artifact_fact(paths, &stdout)?,
        stderr: artifact_fact(paths, &stderr)?,
        ordered_log_stream: artifact_fact(paths, &log)?,
        command_sha256: sha256_bytes(&serde_json::to_vec(&command)?),
        command,
        environment_sha256: sha256_bytes(&serde_json::to_vec(&environment)?),
        environment,
        input_receipt,
        preparation_receipt_sha256: job
            .test
            .nonc_preparation
            .as_ref()
            .map(|receipt| receipt.receipt_sha256.clone()),
        guest_binary_sha256: job.test.binary_sha256.clone(),
        error: None,
    })
}

fn require_refusal(label: &str, result: Result<()>) -> Result<()> {
    if result.is_ok() {
        bail!("negative producer bracket was accepted: {label}");
    }
    Ok(())
}

fn canonical_nonc_argv(paths: &Paths, argv: &[String]) -> Result<Vec<String>> {
    let mut current = argv.to_vec();
    for _ in 0..16 {
        if current.len() == 3
            && current[1] == "-c"
            && matches!(
                Path::new(&current[0])
                    .file_name()
                    .and_then(|value| value.to_str()),
                Some("bash" | "sh")
            )
            && !current[2]
                .bytes()
                .any(|byte| b";&|`$()<>*?[]{}".contains(&byte))
        {
            current = vec![resolve_program(paths, &current[2])?.display().to_string()];
            continue;
        }
        current[0] = resolve_program(paths, &current[0])?.display().to_string();
        if current.last().map(String::as_str) != Some("--run") {
            return Ok(current);
        }
        let Some(next) = trivial_exec_run(Path::new(&current[0]))? else {
            return Ok(current);
        };
        current = next;
    }
    bail!("trivial exec wrapper recursion exceeded 16 levels: {argv:?}")
}

fn trivial_exec_run(script: &Path) -> Result<Option<Vec<String>>> {
    let contents = fs::read_to_string(script)?;
    let Some((_, after)) = contents.split_once("--run)") else {
        return Ok(None);
    };
    let Some((branch, _)) = after.split_once(";;") else {
        return Ok(None);
    };
    let branch = branch.trim();
    let Some(command) = branch.strip_prefix("exec ") else {
        return Ok(None);
    };
    if command.contains('\n') {
        return Ok(None);
    }
    let parent = script.parent().context("wrapper has no parent")?;
    let expanded = command.replace("${BASH_SOURCE[0]%/*}", &parent.display().to_string());
    let argv = shell_words::split(&expanded)?;
    if argv.is_empty() || argv.iter().any(|value| value.contains('$')) {
        return Ok(None);
    }
    Ok(Some(argv))
}

fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    let source_metadata = fs::symlink_metadata(source)?;
    if !source_metadata.is_dir() || source_metadata.file_type().is_symlink() {
        bail!("tree root is not a real directory: {}", source.display());
    }
    fs::create_dir_all(destination)?;
    fs::set_permissions(
        destination,
        fs::Permissions::from_mode(source_metadata.permissions().mode() & 0o7777),
    )?;
    let mut entries = fs::read_dir(source)?.collect::<std::io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let metadata = fs::symlink_metadata(entry.path())?;
        let file_type = metadata.file_type();
        let target = destination.join(entry.file_name());
        if file_type.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), &target)?;
            fs::set_permissions(
                &target,
                fs::Permissions::from_mode(metadata.permissions().mode() & 0o7777),
            )?;
        } else {
            bail!(
                "tree contains a symlink or special entry: {}",
                entry.path().display()
            );
        }
    }
    Ok(())
}

fn hermetic_environment() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("LANG".to_owned(), "C".to_owned()),
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("PATH".to_owned(), HERMETIC_PATH.to_owned()),
        ("TZ".to_owned(), "UTC".to_owned()),
    ])
}

fn tool_environment() -> BTreeMap<String, String> {
    let mut environment = hermetic_environment();
    environment.extend([
        ("CARGO_HOME".to_owned(), "/home/newton/.cargo".to_owned()),
        ("HOME".to_owned(), "/home/newton".to_owned()),
        ("RUSTUP_HOME".to_owned(), "/home/newton/.rustup".to_owned()),
    ]);
    environment
}

fn prepared_input_roots(cell: &Path) -> BTreeMap<String, PathBuf> {
    BTreeMap::from([
        ("fixtures".to_owned(), cell.join("fixtures")),
        ("home".to_owned(), cell.join("home")),
        ("tmp".to_owned(), cell.join("tmp")),
        ("xdg_config".to_owned(), cell.join("xdg-config")),
    ])
}

fn snapshot_tree(root: &Path) -> Result<TreeManifest> {
    let mut entries = Vec::new();
    snapshot_tree_entry(root, root, Path::new("."), &mut entries)?;
    entries.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    let digest_sha256 = sha256_bytes(&serde_json::to_vec(&entries)?);
    Ok(TreeManifest {
        entries,
        digest_sha256,
    })
}

fn snapshot_tree_entry(
    root: &Path,
    path: &Path,
    relative: &Path,
    entries: &mut Vec<TreeEntry>,
) -> Result<String> {
    let metadata = fs::symlink_metadata(path)
        .with_context(|| format!("walking input tree {}", path.display()))?;
    if metadata.file_type().is_symlink() {
        bail!("input tree contains a symlink: {}", path.display());
    }
    let relative_path = if relative == Path::new(".") {
        ".".to_owned()
    } else {
        relative
            .to_str()
            .with_context(|| format!("non-UTF8 input path under {}", root.display()))?
            .to_owned()
    };
    let mode = metadata.permissions().mode() & 0o7777;
    if metadata.is_file() {
        let sha256 = sha256_file(path)?;
        entries.push(TreeEntry {
            relative_path,
            kind: "file".to_owned(),
            bytes: metadata.len(),
            mode,
            sha256: sha256.clone(),
        });
        return Ok(sha256);
    }
    if !metadata.is_dir() {
        bail!("input tree contains a special entry: {}", path.display());
    }
    let mut children = fs::read_dir(path)?.collect::<std::io::Result<Vec<_>>>()?;
    children.sort_by_key(|entry| entry.file_name());
    let mut child_material = Vec::new();
    for child in children {
        let name = child
            .file_name()
            .into_string()
            .map_err(|_| anyhow::anyhow!("non-UTF8 input path under {}", root.display()))?;
        let child_relative = if relative == Path::new(".") {
            PathBuf::from(&name)
        } else {
            relative.join(&name)
        };
        let child_metadata = fs::symlink_metadata(child.path())?;
        let child_kind = if child_metadata.is_dir() {
            "directory"
        } else if child_metadata.is_file() {
            "file"
        } else {
            bail!(
                "input tree contains a symlink or special entry: {}",
                child.path().display()
            );
        };
        let child_hash = snapshot_tree_entry(root, &child.path(), &child_relative, entries)?;
        child_material.push((
            name,
            child_kind,
            if child_metadata.is_file() {
                child_metadata.len()
            } else {
                0
            },
            child_metadata.permissions().mode() & 0o7777,
            child_hash,
        ));
    }
    let sha256 = sha256_bytes(&serde_json::to_vec(&child_material)?);
    entries.push(TreeEntry {
        relative_path,
        kind: "directory".to_owned(),
        bytes: 0,
        mode,
        sha256: sha256.clone(),
    });
    Ok(sha256)
}

fn verify_tree_manifest(root: &Path, expected: &TreeManifest) -> Result<()> {
    let actual = snapshot_tree(root)?;
    if actual != *expected {
        bail!("input tree manifest mismatch: {}", root.display());
    }
    Ok(())
}

fn tree_inodes(root: &Path) -> Result<BTreeSet<(u64, u64)>> {
    let manifest = snapshot_tree(root)?;
    let mut result = BTreeSet::new();
    for entry in manifest.entries {
        let path = if entry.relative_path == "." {
            root.to_path_buf()
        } else {
            root.join(entry.relative_path)
        };
        let metadata = fs::symlink_metadata(path)?;
        result.insert((metadata.dev(), metadata.ino()));
    }
    Ok(result)
}

fn require_disjoint_trees(left: &Path, right: &Path) -> Result<()> {
    let left_path = left.canonicalize()?;
    let right_path = right.canonicalize()?;
    if left_path == right_path
        || left_path.starts_with(&right_path)
        || right_path.starts_with(&left_path)
        || !tree_inodes(left)?.is_disjoint(&tree_inodes(right)?)
    {
        bail!(
            "input trees alias by path or inode: {} and {}",
            left.display(),
            right.display()
        );
    }
    Ok(())
}

fn collect_prepared_artifacts(
    paths: &Paths,
    root: &Path,
    excluded: &Path,
) -> Result<Vec<PreparedArtifact>> {
    let mut pending = vec![root.to_path_buf()];
    let mut result = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)?;
            if metadata.is_dir() {
                pending.push(path);
            } else if metadata.is_file() && path != excluded {
                result.push(PreparedArtifact {
                    path: path.strip_prefix(&paths.artifacts)?.display().to_string(),
                    sha256: sha256_file(&path)?,
                    bytes: metadata.len(),
                    mode: metadata.permissions().mode() & 0o7777,
                });
            } else if !metadata.is_file() {
                bail!(
                    "prepared artifact is not a regular file: {}",
                    path.display()
                );
            }
        }
    }
    result.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(result)
}

fn validate_nonc_preparation(
    paths: &Paths,
    id: &str,
    receipt: &NoncPreparationReceipt,
) -> Result<()> {
    let mut expected_template = hermetic_environment();
    expected_template.extend([
        ("HOME".to_owned(), "${EXECUTION_HOME}".to_owned()),
        (
            "XDG_CONFIG_HOME".to_owned(),
            "${EXECUTION_XDG_CONFIG_HOME}".to_owned(),
        ),
        ("TMPDIR".to_owned(), "${EXECUTION_TMPDIR}".to_owned()),
        ("E2E_TMPDIR".to_owned(), "${EXECUTION_TMPDIR}".to_owned()),
        (
            "E2E_FIXTURE_DIR".to_owned(),
            "${EXECUTION_FIXTURE_DIR}".to_owned(),
        ),
    ]);
    if receipt.exit_code != 0
        || receipt.prepare_command.is_empty()
        || receipt.prepare_command_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.prepare_command)?)
        || receipt.prepare_environment_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.prepare_environment)?)
        || receipt.run_environment_template_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.run_environment_template)?)
        || receipt.canonical_argv.is_empty()
        || receipt.canonical_argv_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.canonical_argv)?)
        || receipt.prepared_artifacts_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.prepared_artifacts)?)
        || receipt.prepared_input_manifests_sha256
            != sha256_bytes(&serde_json::to_vec(&receipt.prepared_input_manifests)?)
        || receipt.receipt_sha256 != nonc_receipt_sha256(receipt)?
        || receipt.run_environment_template != expected_template
    {
        bail!("non-C preparation receipt is internally inconsistent");
    }
    verify_artifact_fact(paths, &receipt.log)?;
    let mut paths_seen = BTreeSet::new();
    for artifact in &receipt.prepared_artifacts {
        if !paths_seen.insert(&artifact.path) {
            bail!(
                "non-C preparation receipt aliases artifact {}",
                artifact.path
            );
        }
        let path = paths.artifacts.join(&artifact.path);
        let metadata = fs::metadata(&path)
            .with_context(|| format!("missing prepared artifact {}", path.display()))?;
        if !metadata.is_file()
            || metadata.len() != artifact.bytes
            || metadata.permissions().mode() & 0o7777 != artifact.mode
            || sha256_file(&path)? != artifact.sha256
        {
            bail!("prepared artifact fact mismatch: {}", path.display());
        }
    }
    for source in &receipt.source_chain {
        let path = Path::new(&source.path);
        if !path.is_file() || sha256_file(path)? != source.sha256 {
            bail!("non-C preparation source changed: {}", source.path);
        }
    }
    let cell = paths
        .artifacts
        .join("preparation")
        .join("nonc")
        .join(slug(id));
    let expected_roots = prepared_input_roots(&cell);
    if receipt.prepared_input_manifests.len() != expected_roots.len() {
        bail!("non-C preparation input manifest set is incomplete for {id}");
    }
    for (name, root) in expected_roots {
        let manifest = receipt
            .prepared_input_manifests
            .get(&name)
            .with_context(|| format!("non-C preparation lacks {name} input manifest for {id}"))?;
        verify_tree_manifest(&root, manifest)?;
    }
    let roots: Vec<_> = prepared_input_roots(&cell).into_values().collect();
    for left in 0..roots.len() {
        for right in left + 1..roots.len() {
            require_disjoint_trees(&roots[left], &roots[right])?;
        }
    }
    Ok(())
}

fn prepare(paths: &Paths, jobs: usize) -> Result<()> {
    let (parent_commit, run_rs_sha256, verify_rs_sha256, log_authority_rs_sha256) =
        harness_binding(paths)?;
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
            "231-test input preflight is incomplete: observed_rows={} unique_ids={} executable_tests={} missing_tests={} missing_sources={} duplicate_ids={:?} duplicate_workloads={:?}; requested_manifest_sha256={}; missing:\n  {}",
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
        let guest = resolved_guest_path(paths, test)?;
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
        denominator_decision_sha256: input_audit.denominator_decision_sha256.clone(),
        denominator_decision_semantic_sha256: input_audit
            .denominator_decision_semantic_sha256
            .clone(),
        parent_commit,
        run_rs_sha256,
        verify_rs_sha256,
        log_authority_rs_sha256,
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
    validate_pair_input_disjoint(paths, job, &one, &two)?;
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
    let log_parser_diagnostic_path =
        artifact_dir.join(format!("run{ordinal}.log-inspection.stderr"));
    File::create(&log_path)?;
    let command = command_argv(paths, job, &log_path);
    let command_sha256 = sha256_bytes(&serde_json::to_vec(&command)?);
    let (environment, input_receipt) =
        execution_inputs(paths, job, binding, binding_sha256, ordinal, true)?;
    let environment_sha256 = sha256_bytes(&serde_json::to_vec(&environment)?);
    let stdout = File::create(&stdout_path)?;
    let stderr = File::create(&stderr_path)?;
    let started = Instant::now();
    let mut cmd = Command::new(&command[0]);
    cmd.args(&command[1..])
        .current_dir(&paths.hermit_repo)
        .env_clear()
        .envs(&environment)
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
    let inspection = log_authority::inspect_file(&paths.binary, &log_path)?;
    fs::write(&log_parser_diagnostic_path, &inspection.diagnostic_stderr)?;
    let log_event_count = inspection.counts.info_messages;
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
        log_parser: inspection.parser_id,
        log_level: "INFO".to_owned(),
        log_counts: inspection.counts,
        log_parser_command_sha256: sha256_bytes(&serde_json::to_vec(&inspection.command)?),
        log_parser_command: inspection.command,
        log_parser_diagnostic: artifact_fact(paths, &log_parser_diagnostic_path)?,
        stdout: artifact_fact(paths, &stdout_path)?,
        stderr: artifact_fact(paths, &stderr_path)?,
        ordered_log_stream: artifact_fact(paths, &log_path)?,
        command,
        command_sha256,
        environment,
        environment_sha256,
        input_receipt,
        preparation_receipt_sha256: job
            .test
            .nonc_preparation
            .as_ref()
            .map(|receipt| receipt.receipt_sha256.clone()),
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

fn execution_input_roots(runtime: &Path) -> BTreeMap<String, PathBuf> {
    BTreeMap::from([
        ("fixtures".to_owned(), runtime.join("fixtures")),
        ("home".to_owned(), runtime.join("home")),
        ("tmp".to_owned(), runtime.join("tmp")),
        ("xdg_config".to_owned(), runtime.join("xdg-config")),
    ])
}

fn execution_inputs(
    paths: &Paths,
    job: &Job,
    binding: &RunBinding,
    binding_sha256: &str,
    ordinal: u8,
    reset_tmp: bool,
) -> Result<(BTreeMap<String, String>, ExecutionInputReceipt)> {
    if let Some(receipt) = &job.test.nonc_preparation {
        validate_nonc_preparation(paths, &job.test.id, receipt)?;
    }
    let runtime = paths
        .artifacts
        .join("runtime")
        .join(&binding.run_kind)
        .join(&binding.run_instance)
        .join(binding_sha256)
        .join(job_cell_id(job))
        .join(format!("run{ordinal}"));
    let roots = execution_input_roots(&runtime.join("inputs"));
    let execution_roots = execution_input_roots(&runtime.join("execution"));
    if reset_tmp && runtime.exists() {
        fs::remove_dir_all(&runtime).with_context(|| {
            format!(
                "resetting owned execution environment {}",
                runtime.display()
            )
        })?;
    }
    if reset_tmp {
        if let Some(receipt) = &job.test.nonc_preparation {
            let preparation_cell = paths
                .artifacts
                .join("preparation")
                .join("nonc")
                .join(slug(&job.test.id));
            let prepared_roots = prepared_input_roots(&preparation_cell);
            for (name, destination) in &roots {
                let source = prepared_roots
                    .get(name)
                    .with_context(|| format!("missing prepared input root {name}"))?;
                copy_tree(source, destination)?;
                require_disjoint_trees(source, destination)?;
                verify_tree_manifest(
                    destination,
                    receipt
                        .prepared_input_manifests
                        .get(name)
                        .with_context(|| format!("missing prepared input manifest {name}"))?,
                )?;
                let execution = &execution_roots[name];
                copy_tree(destination, execution)?;
                require_disjoint_trees(source, execution)?;
                require_disjoint_trees(destination, execution)?;
                verify_tree_manifest(execution, &receipt.prepared_input_manifests[name])?;
            }
        } else {
            for root in roots.values().chain(execution_roots.values()) {
                fs::create_dir_all(root)?;
                fs::set_permissions(root, fs::Permissions::from_mode(0o700))?;
            }
        }
    } else if roots
        .values()
        .chain(execution_roots.values())
        .any(|root| !root.is_dir())
    {
        bail!(
            "cached execution input root is missing: {}",
            runtime.display()
        );
    }
    let root_paths = roots
        .iter()
        .map(|(name, root)| {
            Ok((
                name.clone(),
                root.strip_prefix(&paths.artifacts)?
                    .to_str()
                    .context("execution input root path is not UTF-8")?
                    .to_owned(),
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let root_manifests = roots
        .iter()
        .map(|(name, root)| Ok((name.clone(), snapshot_tree(root)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    if let Some(receipt) = &job.test.nonc_preparation {
        for name in ["fixtures", "home", "tmp", "xdg_config"] {
            if root_manifests[name] != receipt.prepared_input_manifests[name] {
                bail!(
                    "execution input seed differs from preparation for {}::{ordinal}:{name}",
                    job_cell_id(job)
                );
            }
        }
    } else {
        let empty = empty_tree_manifest(0o700)?;
        if root_manifests.values().any(|manifest| manifest != &empty) {
            bail!("compiled guest execution input roots must be exact empty trees");
        }
    }
    let execution_root_paths = execution_roots
        .iter()
        .map(|(name, root)| {
            Ok((
                name.clone(),
                root.strip_prefix(&paths.artifacts)?
                    .to_str()
                    .context("execution root path is not UTF-8")?
                    .to_owned(),
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut input_receipt = ExecutionInputReceipt {
        schema: SCHEMA,
        record_type: "strict_metric_execution_inputs".to_owned(),
        run_kind: binding.run_kind.clone(),
        run_instance: binding.run_instance.clone(),
        run_binding_sha256: binding_sha256.to_owned(),
        cell_id: job_cell_id(job),
        ordinal,
        preparation_receipt_sha256: job
            .test
            .nonc_preparation
            .as_ref()
            .map(|receipt| receipt.receipt_sha256.clone()),
        root_paths,
        execution_root_paths,
        root_manifests,
        receipt_sha256: String::new(),
    };
    input_receipt.receipt_sha256 = execution_input_receipt_sha256(&input_receipt)?;

    let mut environment = hermetic_environment();
    environment.extend([
        (
            "HOME".to_owned(),
            execution_roots["home"].display().to_string(),
        ),
        (
            "XDG_CONFIG_HOME".to_owned(),
            execution_roots["xdg_config"].display().to_string(),
        ),
        (
            "TMPDIR".to_owned(),
            execution_roots["tmp"].display().to_string(),
        ),
        (
            "E2E_TMPDIR".to_owned(),
            execution_roots["tmp"].display().to_string(),
        ),
        (
            "E2E_FIXTURE_DIR".to_owned(),
            execution_roots["fixtures"].display().to_string(),
        ),
    ]);
    Ok((environment, input_receipt))
}

fn execution_input_receipt_sha256(receipt: &ExecutionInputReceipt) -> Result<String> {
    Ok(sha256_bytes(&serde_json::to_vec(&serde_json::json!({
        "schema": receipt.schema,
        "record_type": receipt.record_type,
        "run_kind": receipt.run_kind,
        "run_instance": receipt.run_instance,
        "run_binding_sha256": receipt.run_binding_sha256,
        "cell_id": receipt.cell_id,
        "ordinal": receipt.ordinal,
        "preparation_receipt_sha256": receipt.preparation_receipt_sha256,
        "root_paths": receipt.root_paths,
        "execution_root_paths": receipt.execution_root_paths,
        "root_manifests": receipt.root_manifests,
    }))?))
}

fn empty_tree_manifest(mode: u32) -> Result<TreeManifest> {
    let directory_sha256 = sha256_bytes(&serde_json::to_vec(&Vec::<serde_json::Value>::new())?);
    let entries = vec![TreeEntry {
        relative_path: ".".to_owned(),
        kind: "directory".to_owned(),
        bytes: 0,
        mode,
        sha256: directory_sha256,
    }];
    Ok(TreeManifest {
        digest_sha256: sha256_bytes(&serde_json::to_vec(&entries)?),
        entries,
    })
}

fn run_legacy_logdiff(paths: &Paths, left: &Path, right: &Path, diagnostic: &Path) -> Result<bool> {
    let stderr = File::create(diagnostic)?;
    let mut command = Command::new(&paths.binary);
    let status = command
        .arg("log-diff")
        .arg(left)
        .arg(right)
        .arg("--unsafe-strip-lines")
        .arg("--no-color")
        .arg("--limit=1")
        .current_dir(&paths.hermit_repo)
        .env_clear()
        .envs(hermetic_environment())
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
        || input_audit.denominator_decision_path != "denominator-decision.json"
        || input_audit.denominator_decision_sha256 != manifest.denominator_decision_sha256
        || input_audit.denominator_decision_semantic_sha256
            != manifest.denominator_decision_semantic_sha256
    {
        bail!("input audit does not authorize the complete 231-workload denominator");
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
    comparison_contract.insert(
        "log_event_parser".to_owned(),
        serde_json::json!(log_authority::PARSER_ID),
    );
    comparison_contract.insert("log_event_level".to_owned(), serde_json::json!("INFO"));
    let required_spot_completion = match (kind, spot_run_instance) {
        ("full", Some(instance)) => {
            let path = run_dir(paths, "spot", instance).join("spot-completion.json");
            if !path.is_file() {
                bail!(
                    "full run requires an already verified spot completion: {}",
                    path.display()
                );
            }
            reverify_spot_before_full(paths, instance)?;
            let completion: SpotCompletion = read_json(&path)?;
            validate_spot_completion(&completion, instance)?;
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
        log_authority_rs_sha256: manifest.log_authority_rs_sha256.clone(),
        guest_set_sha256: manifest.guest_set_sha256.clone(),
        input_audit_path: "input-audit.json".to_owned(),
        input_audit_sha256: sha256_file(&input_audit_path)?,
        requested_manifest_path: input_audit.requested_manifest_path.clone(),
        requested_manifest_sha256: input_audit.requested_manifest_sha256.clone(),
        manifest_path: "manifest.json".to_owned(),
        manifest_sha256,
        corpus_c_sha256: manifest.corpus_c_sha256.clone(),
        corpus_nonc_sha256: manifest.corpus_nonc_sha256.clone(),
        denominator_decision_path: input_audit.denominator_decision_path.clone(),
        denominator_decision_sha256: input_audit.denominator_decision_sha256.clone(),
        denominator_decision_semantic_sha256: input_audit
            .denominator_decision_semantic_sha256
            .clone(),
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
        log_authority_rs_sha256: manifest.log_authority_rs_sha256.clone(),
        requested_manifest_sha256: input_audit.requested_manifest_sha256.clone(),
        denominator_decision_sha256: input_audit.denominator_decision_sha256.clone(),
        denominator_decision_semantic_sha256: input_audit
            .denominator_decision_semantic_sha256
            .clone(),
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

fn reverify_spot_before_full(paths: &Paths, instance: &str) -> Result<()> {
    let verifier = paths.experiment.join("verify.rs");
    let arguments = vec![
        "verify".to_owned(),
        "--kind".to_owned(),
        "spot".to_owned(),
        "--run-instance".to_owned(),
        instance.to_owned(),
        "--parent".to_owned(),
        paths.parent_repo.display().to_string(),
        "--experiment".to_owned(),
        paths.experiment.display().to_string(),
        "--artifacts".to_owned(),
        paths.artifacts.display().to_string(),
        "--hermit".to_owned(),
        paths.hermit_repo.display().to_string(),
        "--reverie".to_owned(),
        paths.reverie_repo.display().to_string(),
        "--liteinst2".to_owned(),
        paths.liteinst_repo.display().to_string(),
        "--binary".to_owned(),
        paths.binary.display().to_string(),
        "--corpus-c".to_owned(),
        paths.corpus_c.display().to_string(),
        "--corpus-nonc".to_owned(),
        paths.corpus_nonc.display().to_string(),
    ];
    let output = Command::new(&verifier)
        .args(&arguments)
        .env_clear()
        .envs(tool_environment())
        .output()
        .with_context(|| format!("launching exact spot verifier {}", verifier.display()))?;
    if !output.status.success() {
        bail!(
            "full run refused because exact spot reverification failed: status={} stdout={} stderr={}",
            output.status,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
    }
    Ok(())
}

fn validate_spot_completion(completion: &SpotCompletion, expected_instance: &str) -> Result<()> {
    let expected_tests: Vec<_> = SPOT_TESTS.iter().map(|value| (*value).to_owned()).collect();
    let expected_backends: Vec<_> = BACKENDS.iter().map(|value| (*value).to_owned()).collect();
    let expected_modes = vec![
        "heap".to_owned(),
        "heap_stack".to_owned(),
        "stack".to_owned(),
    ];
    if completion.schema != SCHEMA
        || completion.record_type != "strict_metric_spot_completion"
        || !completion.complete
        || completion.run_instance != expected_instance
        || completion.tests != expected_tests
        || completion.backends != expected_backends
        || completion.observation_modes != expected_modes
        || completion.run_ordinals != vec![1, 2]
        || completion.expected_cells != 36
        || completion.expected_executions != 72
        || completion.attempted_executions != 72
        || completion.nonzero_info_executions != 72
        || completion.successful_exit_executions != 72
        || completion.strict_green_cells != 36
        || completion.raw_equal_cells != 36
        || completion.completion_digest_sha256 != spot_completion_digest(completion)?
    {
        bail!(
            "full run requires a digest-valid healthy exact 36-cell/72-execution spot completion"
        );
    }
    Ok(())
}

fn spot_completion_digest(completion: &SpotCompletion) -> Result<String> {
    let value = serde_json::json!({
        "schema": completion.schema,
        "record_type": completion.record_type,
        "complete": completion.complete,
        "run_instance": completion.run_instance,
        "run_binding_sha256": completion.run_binding_sha256,
        "denominator_sha256": completion.denominator_sha256,
        "executions_jsonl_sha256": completion.executions_jsonl_sha256,
        "cells_jsonl_sha256": completion.cells_jsonl_sha256,
        "tests": completion.tests,
        "backends": completion.backends,
        "observation_modes": completion.observation_modes,
        "run_ordinals": completion.run_ordinals,
        "expected_cells": completion.expected_cells,
        "expected_executions": completion.expected_executions,
        "attempted_executions": completion.attempted_executions,
        "nonzero_info_executions": completion.nonzero_info_executions,
        "successful_exit_executions": completion.successful_exit_executions,
        "strict_green_cells": completion.strict_green_cells,
        "raw_equal_cells": completion.raw_equal_cells,
    });
    Ok(sha256_bytes(&serde_json::to_vec(&value)?))
}

fn validate_frozen_inputs(paths: &Paths, manifest: &FrozenManifest) -> Result<()> {
    verify_harness_commit(
        paths,
        &manifest.parent_commit,
        &manifest.run_rs_sha256,
        &manifest.verify_rs_sha256,
        &manifest.log_authority_rs_sha256,
        &manifest.denominator_decision_sha256,
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
        || manifest.hermit_build.environment_sha256
            != sha256_bytes(&serde_json::to_vec(&manifest.hermit_build.environment)?)
        || manifest
            .hermit_build
            .environment
            .get("PATH")
            .map(String::as_str)
            != Some(HERMETIC_PATH)
        || manifest
            .hermit_build
            .environment
            .keys()
            .any(|key| key == "PYTHONPATH")
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
        validate_test_preparation(paths, test, &manifest.toolchain.receipt_sha256)?;
    }
    if manifest.tests.len() != FULL_TESTS {
        bail!("frozen manifest must contain exactly {FULL_TESTS} unique semantic workloads");
    }
    if guest_set_sha256(&manifest.tests)? != manifest.guest_set_sha256 {
        bail!("guest-set digest changed after manifest freeze");
    }
    Ok(())
}

fn validate_test_preparation(
    paths: &Paths,
    test: &TestSpec,
    toolchain_receipt_sha256: &str,
) -> Result<()> {
    match (&test.compile, &test.compile_receipt, &test.nonc_preparation) {
        (Some(compile), Some(receipt), None) => {
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
                || receipt.command_sha256 != sha256_bytes(&serde_json::to_vec(&compile.command)?)
                || receipt.inputs != expected_inputs
                || receipt.output.sha256 != test.binary_sha256
                || receipt.exit_code != 0
                || receipt.toolchain_receipt_sha256 != toolchain_receipt_sha256
                || receipt.environment != hermetic_environment()
                || receipt.environment_sha256
                    != sha256_bytes(&serde_json::to_vec(&receipt.environment)?)
            {
                bail!("compile receipt no longer binds guest {}", test.id);
            }
            verify_artifact_fact(paths, &receipt.log)?;
            verify_artifact_fact(paths, &receipt.output)?;
        }
        (None, None, Some(receipt)) => validate_nonc_preparation(paths, &test.id, receipt)?,
        _ => bail!(
            "compile/non-C preparation receipt pairing is invalid: {}",
            test.id
        ),
    }
    Ok(())
}

fn validate_input_authority(paths: &Paths, manifest: &FrozenManifest) -> Result<()> {
    let requested_path = paths.experiment.join("requested-manifest.json");
    let recorded: RequestedManifest = read_json(&requested_path)?;
    let derived = build_requested_manifest(paths, false)?;
    let (decision_file_sha256, decision_semantic_sha256) =
        validate_denominator_decision(paths, Some(&derived.inputs))?;
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
        if !input.available {
            missing_test_ids.insert(input.id.clone());
        }
        match (&input.kind[..], &input.nonc_preparation) {
            ("script_or_interpreter", Some(receipt)) => {
                validate_nonc_preparation(paths, &input.id, receipt)?
            }
            ("compiled_c", None) => {}
            _ => bail!("requested input preparation type mismatch: {}", input.id),
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
        || audit.denominator_decision_path != "denominator-decision.json"
        || audit.denominator_decision_sha256 != decision_file_sha256
        || audit.denominator_decision_semantic_sha256 != decision_semantic_sha256
        || recorded.denominator_decision_path != audit.denominator_decision_path
        || recorded.denominator_decision_sha256 != audit.denominator_decision_sha256
        || recorded.denominator_decision_semantic_sha256
            != audit.denominator_decision_semantic_sha256
        || manifest.denominator_decision_sha256 != audit.denominator_decision_sha256
        || manifest.denominator_decision_semantic_sha256
            != audit.denominator_decision_semantic_sha256
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
        bail!("input audit does not equal independently rederived 231-workload authority");
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
            nonc_preparation: None,
        });
    }
    Ok(result)
}

fn parse_nonc_tests(paths: &Paths) -> Result<Vec<TestSpec>> {
    let requested: RequestedManifest =
        read_json(&paths.experiment.join("requested-manifest.json"))?;
    let requested_by_id: BTreeMap<_, _> = requested
        .inputs
        .into_iter()
        .filter(|input| input.kind == "script_or_interpreter")
        .map(|input| (input.id.clone(), input))
        .collect();
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
        let requested = requested_by_id
            .get(fields[0])
            .with_context(|| format!("requested non-C receipt missing for {}", fields[0]))?;
        if requested.argv != argv || !requested.available {
            bail!(
                "requested non-C command is not prepared/runnable: {}",
                fields[0]
            );
        }
        let preparation = requested
            .nonc_preparation
            .clone()
            .with_context(|| format!("non-C preparation receipt missing for {}", fields[0]))?;
        validate_nonc_preparation(paths, fields[0], &preparation)?;
        let launcher = resolve_program(paths, &argv[0])?;
        result.push(TestSpec {
            id: fields[0].to_owned(),
            lane: fields[1].to_owned(),
            kind: "script_or_interpreter".to_owned(),
            source_sha256: sha256_bytes(&serde_json::to_vec(&preparation.source_chain)?),
            binary_sha256: sha256_file(&launcher)?,
            workload_identity_sha256: requested.workload_identity_sha256.clone(),
            argv,
            compile: None,
            compile_receipt: None,
            nonc_preparation: Some(preparation),
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
                let environment = hermetic_environment();
                let status = Command::new(&compile.command[0])
                    .args(&compile.command[1..])
                    .current_dir(&paths.hermit_repo)
                    .env_clear()
                    .envs(&environment)
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
                    environment_sha256: sha256_bytes(&serde_json::to_vec(&environment)?),
                    environment,
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
        "/usr/bin/cc".to_owned(),
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
        "/home/newton/.cargo/bin/cargo".to_owned(),
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
    let cc_argv = vec!["/usr/bin/cc".to_owned(), "--version".to_owned()];
    let rustc_argv = vec!["/home/newton/.cargo/bin/rustc".to_owned(), "-vV".to_owned()];
    let cargo_argv = vec!["/home/newton/.cargo/bin/cargo".to_owned(), "-V".to_owned()];
    let environment = tool_environment();
    let cc_output_sha256 = command_output_sha256(&cc_argv, &environment)?;
    let rustc_output_sha256 = command_output_sha256(&rustc_argv, &environment)?;
    let cargo_output_sha256 = command_output_sha256(&cargo_argv, &environment)?;
    let environment_sha256 = sha256_bytes(&serde_json::to_vec(&environment)?);
    let receipt_sha256 = sha256_bytes(&serde_json::to_vec(&serde_json::json!({
        "cc_argv": cc_argv,
        "cc_output_sha256": cc_output_sha256,
        "rustc_argv": rustc_argv,
        "rustc_output_sha256": rustc_output_sha256,
        "cargo_argv": cargo_argv,
        "cargo_output_sha256": cargo_output_sha256,
        "environment": environment,
        "environment_sha256": environment_sha256,
    }))?);
    Ok(ToolchainReceipt {
        cc_argv,
        cc_output_sha256,
        rustc_argv,
        rustc_output_sha256,
        cargo_argv,
        cargo_output_sha256,
        environment,
        environment_sha256,
        receipt_sha256,
    })
}

fn command_output_sha256(
    argv: &[String],
    environment: &BTreeMap<String, String>,
) -> Result<String> {
    let output = Command::new(&argv[0])
        .args(&argv[1..])
        .env_clear()
        .envs(environment)
        .output()?;
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
    let mut environment = tool_environment();
    environment.insert("CARGO_BUILD_JOBS".to_owned(), jobs.max(1).to_string());
    let status = Command::new(&command[0])
        .args(&command[1..])
        .env_clear()
        .envs(&environment)
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
        environment_sha256: sha256_bytes(&serde_json::to_vec(&environment)?),
        environment,
        exit_code,
        log: artifact_fact(paths, &log_path)?,
        output: artifact_fact(paths, &binary_snapshot)?,
    })
}

fn harness_binding(paths: &Paths) -> Result<(String, String, String, String)> {
    let run_path = paths.experiment.join("run.rs");
    let verify_path = paths.experiment.join("verify.rs");
    let log_authority_path = paths.experiment.join("log_authority.rs");
    let denominator_decision_path = paths.experiment.join("denominator-decision.json");
    let parent_commit = git_head(&paths.parent_repo)?;
    let run_rs_sha256 = sha256_file(&run_path)?;
    let verify_rs_sha256 = sha256_file(&verify_path)?;
    let log_authority_rs_sha256 = sha256_file(&log_authority_path)?;
    verify_harness_commit(
        paths,
        &parent_commit,
        &run_rs_sha256,
        &verify_rs_sha256,
        &log_authority_rs_sha256,
        &sha256_file(&denominator_decision_path)?,
    )?;
    Ok((
        parent_commit,
        run_rs_sha256,
        verify_rs_sha256,
        log_authority_rs_sha256,
    ))
}

fn verify_harness_commit(
    paths: &Paths,
    parent_commit: &str,
    run_rs_sha256: &str,
    verify_rs_sha256: &str,
    log_authority_rs_sha256: &str,
    denominator_decision_sha256: &str,
) -> Result<()> {
    if parent_commit.len() != 40 || !parent_commit.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        bail!("parent harness commit is not a 40-hex object name");
    }
    let run_path = paths.experiment.join("run.rs");
    let verify_path = paths.experiment.join("verify.rs");
    let log_authority_path = paths.experiment.join("log_authority.rs");
    let denominator_decision_path = paths.experiment.join("denominator-decision.json");
    for (path, expected_hash) in [
        (&run_path, run_rs_sha256),
        (&verify_path, verify_rs_sha256),
        (&log_authority_path, log_authority_rs_sha256),
        (&denominator_decision_path, denominator_decision_sha256),
    ] {
        let relative = path
            .strip_prefix(&paths.parent_repo)
            .with_context(|| format!("harness path escaped parent: {}", path.display()))?;
        if sha256_file(path)? != expected_hash {
            bail!(
                "working harness bytes differ from the bound commit: {}",
                path.display()
            );
        }
        let status = Command::new("/usr/bin/git")
            .args(["diff", "--quiet", "HEAD", "--"])
            .arg(relative)
            .current_dir(&paths.parent_repo)
            .env_clear()
            .envs(tool_environment())
            .status()?;
        if !status.success() {
            bail!(
                "harness file is not committed at current parent HEAD: {}",
                path.display()
            );
        }
        let object = format!("{parent_commit}:{}", relative.display());
        let output = Command::new("/usr/bin/git")
            .args(["show", &object])
            .current_dir(&paths.parent_repo)
            .env_clear()
            .envs(tool_environment())
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
        .map(|test| {
            (
                &test.id,
                &test.binary_sha256,
                test.nonc_preparation
                    .as_ref()
                    .map(|receipt| &receipt.receipt_sha256),
            )
        })
        .collect();
    Ok(sha256_bytes(&serde_json::to_vec(&values)?))
}

fn resolved_guest_path(paths: &Paths, test: &TestSpec) -> Result<PathBuf> {
    if test.compile.is_some() {
        Ok(paths
            .artifacts
            .join("guests")
            .join(slug(&test.id))
            .join("guest"))
    } else {
        resolve_program(paths, &test.argv[0])
    }
}

fn require_repo_at_head(path: &Path) -> Result<()> {
    if !repo_clean(path)? {
        bail!("source repo is dirty: {}", path.display());
    }
    Ok(())
}

fn repo_clean(path: &Path) -> Result<bool> {
    let status = Command::new("/usr/bin/git")
        .args(["status", "--porcelain"])
        .current_dir(path)
        .env_clear()
        .envs(tool_environment())
        .output()?;
    if !status.status.success() {
        bail!("git status failed for {}", path.display());
    }
    Ok(status.stdout.is_empty())
}

fn git_head(path: &Path) -> Result<String> {
    let output = Command::new("/usr/bin/git")
        .args(["rev-parse", "HEAD"])
        .current_dir(path)
        .env_clear()
        .envs(tool_environment())
        .output()?;
    if !output.status.success() {
        bail!("git rev-parse failed for {}", path.display());
    }
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

fn git_tree(path: &Path) -> Result<String> {
    let output = Command::new("/usr/bin/git")
        .args(["rev-parse", "HEAD^{tree}"])
        .current_dir(path)
        .env_clear()
        .envs(tool_environment())
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
            canonical_artifact_relative(
                binding,
                binding_sha256,
                &cell_id,
                &format!("run{ordinal}.log-inspection.stderr"),
            ),
        ];
        let facts = [
            &execution.stdout,
            &execution.stderr,
            &execution.ordered_log_stream,
            &execution.log_parser_diagnostic,
        ];
        for (fact, expected) in facts.into_iter().zip(expected_paths) {
            if fact.path != expected {
                bail!("cached artifact path is not canonical: {}", fact.path);
            }
            verify_artifact_fact(paths, fact)?;
        }
        let log_path = paths.artifacts.join(&execution.ordered_log_stream.path);
        let expected_command = command_argv(paths, job, &log_path);
        let (expected_environment, expected_input_receipt) =
            execution_inputs(paths, job, binding, binding_sha256, ordinal, false)?;
        let inspection = log_authority::inspect_file(&paths.binary, &log_path)?;
        let diagnostic_path = paths.artifacts.join(&execution.log_parser_diagnostic.path);
        if execution.command != expected_command
            || execution.command_sha256 != sha256_bytes(&serde_json::to_vec(&expected_command)?)
            || execution.environment != expected_environment
            || execution.environment_sha256
                != sha256_bytes(&serde_json::to_vec(&expected_environment)?)
            || execution.input_receipt != expected_input_receipt
            || execution.input_receipt.receipt_sha256
                != execution_input_receipt_sha256(&execution.input_receipt)?
            || execution.preparation_receipt_sha256
                != job
                    .test
                    .nonc_preparation
                    .as_ref()
                    .map(|receipt| receipt.receipt_sha256.clone())
            || execution.log_parser != log_authority::PARSER_ID
            || execution.log_level != "INFO"
            || execution.log_counts != inspection.counts
            || execution.log_event_count != inspection.counts.info_messages
            || execution.log_parser_command != inspection.command
            || execution.log_parser_command_sha256
                != sha256_bytes(&serde_json::to_vec(&inspection.command)?)
            || fs::read(&diagnostic_path)? != inspection.diagnostic_stderr
        {
            bail!("cached command/environment/log binding mismatch for {cell_id}::{ordinal}");
        }
    }
    validate_pair_input_disjoint(paths, job, one, two)?;
    let all_paths = BTreeSet::from([
        one.stdout.path.as_str(),
        one.stderr.path.as_str(),
        one.ordered_log_stream.path.as_str(),
        one.log_parser_diagnostic.path.as_str(),
        two.stdout.path.as_str(),
        two.stderr.path.as_str(),
        two.ordered_log_stream.path.as_str(),
        two.log_parser_diagnostic.path.as_str(),
    ]);
    if all_paths.len() != 8 {
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

fn validate_pair_input_disjoint(
    paths: &Paths,
    job: &Job,
    one: &ExecutionRecord,
    two: &ExecutionRecord,
) -> Result<()> {
    for name in ["fixtures", "home", "tmp", "xdg_config"] {
        let left = paths.artifacts.join(&one.input_receipt.root_paths[name]);
        let right = paths.artifacts.join(&two.input_receipt.root_paths[name]);
        let left_execution = paths
            .artifacts
            .join(&one.input_receipt.execution_root_paths[name]);
        let right_execution = paths
            .artifacts
            .join(&two.input_receipt.execution_root_paths[name]);
        require_disjoint_trees(&left, &right)?;
        require_disjoint_trees(&left_execution, &right_execution)?;
        require_disjoint_trees(&left, &left_execution)?;
        require_disjoint_trees(&right, &right_execution)?;
        if let Some(receipt) = &job.test.nonc_preparation {
            let prepared = prepared_input_roots(
                &paths
                    .artifacts
                    .join("preparation")
                    .join("nonc")
                    .join(slug(&job.test.id)),
            );
            require_disjoint_trees(&left, &prepared[name])?;
            require_disjoint_trees(&right, &prepared[name])?;
            verify_tree_manifest(&prepared[name], &receipt.prepared_input_manifests[name])?;
        }
    }
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
