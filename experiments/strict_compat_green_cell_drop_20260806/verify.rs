#!/usr/bin/env rust-script
//! ```cargo
//! [dependencies]
//! anyhow = "1"
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! sha2 = "0.10"
//! shell-words = "1"
//! ```

#[path = "log_authority.rs"]
mod log_authority;

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

const SCHEMA: u32 = 3;
const FULL_TESTS: usize = 231;
const BACKENDS: [&str; 6] = ["ptrace", "kvm", "dbi", "sabre", "e9patch", "liteinst"];
const CALIBRATION_TEST: &str = "backend-parity-c/pid-probe";
const SPOT_TESTS: [&str; 2] = ["backend-parity-c/pid-probe", "c-programs/print-memaddrs"];
const SELF_TESTS: [&str; 2] = ["fixture/one", "fixture/two"];

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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct EvidenceFile {
    path: String,
    sha256: String,
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
struct CompileSpecEvidence {
    source: String,
    cflags: Vec<String>,
    extra_sources: Vec<String>,
    command: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CompileReceiptEvidence {
    command: Vec<String>,
    command_sha256: String,
    inputs: Vec<SourceFact>,
    toolchain_receipt_sha256: String,
    exit_code: i32,
    log: ArtifactFact,
    output: ArtifactFact,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct PreparedArtifact {
    path: String,
    sha256: String,
    bytes: u64,
    mode: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct NoncPreparationReceiptEvidence {
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
    canonical_argv: Vec<String>,
    canonical_argv_sha256: String,
    receipt_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HermitBuildReceiptEvidence {
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
    preparation_receipt_sha256: Option<String>,
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
    tests: Vec<String>,
    backends: Vec<String>,
    observation_modes: Vec<String>,
    run_ordinals: Vec<u8>,
    expected_cells: usize,
    expected_executions: usize,
    required_spot_completion: Option<EvidenceFile>,
    comparison_contract: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FrozenManifestEvidence {
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
    log_authority_rs_sha256: String,
    toolchain: ToolchainReceipt,
    hermit_build: HermitBuildReceiptEvidence,
    guest_set_sha256: String,
    tests: Vec<FrozenTestEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FrozenTestEvidence {
    id: String,
    lane: String,
    kind: String,
    argv: Vec<String>,
    source_sha256: String,
    binary_sha256: String,
    workload_identity_sha256: String,
    compile: Option<CompileSpecEvidence>,
    compile_receipt: Option<CompileReceiptEvidence>,
    nonc_preparation: Option<NoncPreparationReceiptEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct InputAuditEvidence {
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
    missing_sources: Vec<serde_json::Value>,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedSourceEvidence {
    role: String,
    path: String,
    exists: bool,
    sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedInputEvidence {
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
    sources: Vec<RequestedSourceEvidence>,
    nonc_preparation: Option<NoncPreparationReceiptEvidence>,
    available: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RequestedManifestEvidence {
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
    inputs: Vec<RequestedInputEvidence>,
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
    input_audit_sha256: String,
    manifest_sha256: String,
    denominator_sha256: String,
    hermit_binary_sha256: String,
    guest_set_sha256: String,
    required_spot_completion: Option<EvidenceFile>,
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

#[derive(Debug, Serialize)]
struct BackendSummary {
    cells: usize,
    legacy_green: usize,
    strict_green: usize,
    drop_cells: i64,
}

#[derive(Debug, Serialize)]
struct VerificationSummary {
    schema: u32,
    record_type: String,
    verified_epoch_ms: u128,
    run_kind: String,
    run_instance: String,
    run_binding_sha256: String,
    denominator_cells: usize,
    expected_executions: usize,
    observed_executions: usize,
    missing_execution_keys: Vec<String>,
    duplicate_execution_keys: Vec<String>,
    unexpected_execution_keys: Vec<String>,
    missing_cell_keys: Vec<String>,
    duplicate_cell_keys: Vec<String>,
    unexpected_cell_keys: Vec<String>,
    artifact_hashes_verified: usize,
    aggregate_execution_duration_ms: u128,
    attempted_executions: usize,
    nonzero_log_executions: usize,
    successful_exit_executions: usize,
    legacy_green_cells: usize,
    raw_equal_cells: usize,
    strict_green_cells: usize,
    legacy_green_percent: f64,
    strict_green_percent: f64,
    absolute_drop_cells: i64,
    drop_percentage_points: f64,
    mismatch_components: BTreeMap<String, usize>,
    by_backend: BTreeMap<String, BackendSummary>,
    denominator_sha256: String,
    manifest_sha256: String,
    executions_jsonl_sha256: String,
    cells_jsonl_sha256: String,
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
    strict_contract: BTreeMap<String, serde_json::Value>,
    required_spot_completion_sha256: Option<String>,
    verification_complete: bool,
}

#[derive(Debug, Clone)]
struct VerifyPaths {
    parent_repo: PathBuf,
    experiment: PathBuf,
    artifacts: PathBuf,
    binary: PathBuf,
    hermit_repo: PathBuf,
    reverie_repo: PathBuf,
    liteinst_repo: PathBuf,
    corpus_c: PathBuf,
    corpus_nonc: PathBuf,
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("help");
    match command {
        "verify" => {
            let paths = VerifyPaths::from_args(&args[1..]);
            let kind = value_string(&args[1..], "--kind", "full");
            let run_instance = required_slug_value(&args[1..], "--run-instance")?;
            let summary = verify(&paths, &kind, &run_instance, true)?;
            write_outputs(&paths, &kind, &run_instance, &summary)?;
            println!(
                "VERIFIED kind={} executions={}/{} cells={} legacy_green={} strict_green={} drop={} ({:.6} pp)",
                kind,
                summary.observed_executions,
                summary.expected_executions,
                summary.denominator_cells,
                summary.legacy_green_cells,
                summary.strict_green_cells,
                summary.absolute_drop_cells,
                summary.drop_percentage_points
            );
            Ok(())
        }
        "self-test" => self_test(&VerifyPaths::from_args(&args[1..])),
        "help" | "--help" | "-h" => {
            println!("verify --kind full|calibration|spot --run-instance SLUG; self-test");
            Ok(())
        }
        other => bail!("unknown command {other:?}"),
    }
}

impl VerifyPaths {
    fn from_args(args: &[String]) -> Self {
        let hermit_repo = path_value(
            args,
            "--hermit",
            "/home/newton/work/dev-hermit/worktrees/strict-metric/hermit",
        );
        Self {
            parent_repo: path_value(args, "--parent", "/home/newton/work/dev-hermit"),
            experiment: path_value(
                args,
                "--experiment",
                "/home/newton/work/dev-hermit/experiments/strict_compat_green_cell_drop_20260806",
            ),
            artifacts: path_value(
                args,
                "--artifacts",
                "/home/newton/work/dev-hermit/worktrees/strict-metric/hermit/ignored/strict-metric/raw",
            ),
            binary: path_value(
                args,
                "--binary",
                &hermit_repo.join("target/release/hermit").display().to_string(),
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
            hermit_repo,
        }
    }
}

fn verify(
    paths: &VerifyPaths,
    kind: &str,
    run_instance: &str,
    verify_environment: bool,
) -> Result<VerificationSummary> {
    let run_dir = run_dir(paths, kind, run_instance);
    let denominator_path = run_dir.join("denominator.json");
    let executions_path = run_dir.join("executions.jsonl");
    let cells_path = run_dir.join("cells.jsonl");
    let binding_path = run_dir.join("run-binding.json");
    let binding: RunBinding = read_json(&binding_path)?;
    let binding_sha256 = sha256_file(&binding_path)?;
    let denominator: Denominator = read_json(&denominator_path)?;
    if denominator.schema != SCHEMA
        || denominator.record_type != "strict_metric_denominator"
        || denominator.run_kind != kind
        || denominator.run_instance != run_instance
    {
        bail!("invalid denominator type/schema/kind");
    }
    verify_contract(&denominator)?;
    verify_axes(&denominator, kind)?;
    verify_run_binding(
        paths,
        &denominator,
        &binding,
        &binding_sha256,
        &denominator_path,
        kind,
        run_instance,
    )?;
    if sha256_file(&paths.experiment.join(&denominator.manifest_path))?
        != denominator.manifest_sha256
    {
        bail!("manifest hash does not match denominator");
    }
    if sha256_file(&paths.binary)? != denominator.hermit_binary_sha256 {
        bail!("Hermit binary hash does not match denominator");
    }
    verify_input_audit(paths, &denominator, kind)?;
    let manifest = verify_manifest(paths, &denominator, kind)?;
    rederive_requested_inputs(paths, &denominator, &manifest, kind)?;
    let required_spot_completion_sha256 = if kind == "full" {
        Some(verify_required_spot_completion(
            paths,
            &denominator,
            verify_environment,
        )?)
    } else {
        if denominator.required_spot_completion.is_some() {
            bail!("only a full run may bind a required spot completion");
        }
        None
    };
    if verify_environment {
        verify_parent_harness(paths, &denominator)?;
        verify_repo_sha(&paths.hermit_repo, &denominator.hermit_sha, "Hermit")?;
        verify_repo_sha(&paths.reverie_repo, &denominator.reverie_sha, "Reverie")?;
        verify_repo_sha(
            &paths.liteinst_repo,
            &denominator.liteinst2_sha,
            "LiteInst2",
        )?;
        if cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/reverie")?
            != denominator.reverie_dependency_sha
            || cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/liteinst2")?
                != denominator.liteinst2_dependency_sha
        {
            bail!("Hermit Cargo.lock dependency pin does not match denominator");
        }
    }

    let (executions, duplicate_execution_keys) = read_execution_jsonl(&executions_path)?;
    let (cells, duplicate_cell_keys) = read_cell_jsonl(&cells_path)?;
    let expected_cells = expected_cell_keys(&denominator);
    let expected_executions = expected_execution_keys(&expected_cells, &denominator.run_ordinals);
    let observed_execution_keys: BTreeSet<_> = executions.keys().cloned().collect();
    let observed_cell_keys: BTreeSet<_> = cells.keys().cloned().collect();
    let missing_execution_keys = set_difference(&expected_executions, &observed_execution_keys);
    let unexpected_execution_keys = set_difference(&observed_execution_keys, &expected_executions);
    let missing_cell_keys = set_difference(&expected_cells, &observed_cell_keys);
    let unexpected_cell_keys = set_difference(&observed_cell_keys, &expected_cells);
    if denominator.expected_cells != expected_cells.len()
        || denominator.expected_executions != expected_executions.len()
    {
        bail!("denominator count fields disagree with its typed axes");
    }
    if !missing_execution_keys.is_empty()
        || !duplicate_execution_keys.is_empty()
        || !unexpected_execution_keys.is_empty()
        || !missing_cell_keys.is_empty()
        || !duplicate_cell_keys.is_empty()
        || !unexpected_cell_keys.is_empty()
    {
        bail!(
            "coverage refusal: missing_exec={} duplicate_exec={} extra_exec={} missing_cell={} duplicate_cell={} extra_cell={}",
            missing_execution_keys.len(),
            duplicate_execution_keys.len(),
            unexpected_execution_keys.len(),
            missing_cell_keys.len(),
            duplicate_cell_keys.len(),
            unexpected_cell_keys.len()
        );
    }

    let mut artifact_hashes_verified = 0;
    let mut aggregate_execution_duration_ms = 0;
    let mut attempted_executions = 0;
    let mut nonzero_log_executions = 0;
    let mut successful_exit_executions = 0;
    let mut artifact_paths = BTreeSet::new();
    for (key, execution) in &executions {
        if execution.run_binding_sha256 != binding_sha256 {
            bail!("execution run binding mismatch at {key}");
        }
        if execution.schema != SCHEMA
            || execution.record_type != "strict_metric_execution"
            || execution.run_kind != kind
            || execution.run_instance != run_instance
        {
            bail!("invalid execution record at {key}");
        }
        if !execution.attempted {
            bail!("execution was recorded but never attempted: {key}");
        }
        verify_execution_identity(
            paths,
            &denominator,
            &manifest,
            &binding_sha256,
            key,
            execution,
            kind,
        )?;
        verify_termination(&execution.termination, key)?;
        if execution.error.is_some() {
            bail!("attempted execution unexpectedly carries a spawn error: {key}");
        }
        attempted_executions += 1;
        aggregate_execution_duration_ms += execution.duration_ms;
        if execution.log_event_count > 0 {
            nonzero_log_executions += 1;
        }
        if successful(&execution.termination) {
            successful_exit_executions += 1;
        }
        for fact in [
            &execution.stdout,
            &execution.stderr,
            &execution.ordered_log_stream,
            &execution.log_parser_diagnostic,
        ] {
            if !artifact_paths.insert(fact.path.clone()) {
                bail!("ordinal/stream artifact alias detected: {}", fact.path);
            }
            verify_artifact(paths, fact)?;
            artifact_hashes_verified += 1;
        }
        let log_path = resolve_artifact(paths, &execution.ordered_log_stream.path)?;
        let inspection = log_authority::inspect_file(&paths.binary, &log_path)?;
        let diagnostic_path = resolve_artifact(paths, &execution.log_parser_diagnostic.path)?;
        if execution.log_parser != log_authority::PARSER_ID
            || execution.log_level != "INFO"
            || execution.log_counts != inspection.counts
            || execution.log_event_count != inspection.counts.info_messages
            || execution.log_parser_command != inspection.command
            || execution.log_parser_command_sha256
                != sha256_bytes(&serde_json::to_vec(&inspection.command)?)
            || fs::read(&diagnostic_path)? != inspection.diagnostic_stderr
        {
            bail!(
                "canonical INFO parser evidence mismatch for {key}: record={} actual={}",
                execution.log_event_count,
                inspection.counts.info_messages
            );
        }
    }

    let mut legacy_green_cells = 0;
    let mut raw_equal_cells = 0;
    let mut strict_green_cells = 0;
    let mut mismatch_components: BTreeMap<String, usize> = BTreeMap::new();
    let mut by_backend_counts: BTreeMap<String, (usize, usize, usize)> = BTreeMap::new();
    for (cell_key, cell) in &cells {
        if cell.schema != SCHEMA
            || cell.record_type != "strict_metric_cell"
            || cell.run_kind != kind
            || cell.run_instance != run_instance
            || cell.run_binding_sha256 != binding_sha256
            || cell.execution_ordinals != vec![1, 2]
        {
            bail!("invalid cell record at {cell_key}");
        }
        let bound_cell_id = format!(
            "{}--{}--{}",
            slug(&cell.test_id),
            slug(&cell.backend),
            slug(&cell.observation_mode)
        );
        if cell.cell_id != bound_cell_id || cell_key != &bound_cell_id {
            bail!("cell identity is not bound to its typed axes: {cell_key}");
        }
        let expected_diagnostic = canonical_artifact_relative(
            kind,
            run_instance,
            &binding_sha256,
            cell_key,
            "legacy-log-diff.stderr",
        );
        if cell.legacy_diagnostic.path != expected_diagnostic {
            bail!("legacy diagnostic path is not canonical: {cell_key}");
        }
        if !artifact_paths.insert(cell.legacy_diagnostic.path.clone()) {
            bail!("legacy diagnostic aliases another artifact: {cell_key}");
        }
        verify_artifact(paths, &cell.legacy_diagnostic)?;
        artifact_hashes_verified += 1;
        let one = executions
            .get(&format!("{cell_key}::1"))
            .with_context(|| format!("missing first execution for {cell_key}"))?;
        let two = executions
            .get(&format!("{cell_key}::2"))
            .with_context(|| format!("missing second execution for {cell_key}"))?;
        if one.test_id != cell.test_id
            || two.test_id != cell.test_id
            || one.backend != cell.backend
            || two.backend != cell.backend
            || one.observation_mode != cell.observation_mode
            || two.observation_mode != cell.observation_mode
        {
            bail!("execution identity disagrees with cell {cell_key}");
        }
        let stdout_equal = artifact_files_equal(paths, &one.stdout, &two.stdout)?;
        let stderr_equal = artifact_files_equal(paths, &one.stderr, &two.stderr)?;
        let log_equal =
            artifact_files_equal(paths, &one.ordered_log_stream, &two.ordered_log_stream)?;
        let termination_equal = one.termination == two.termination;
        let nonzero_log_events = one.log_event_count > 0 && two.log_event_count > 0;
        let successful_exit_both = successful(&one.termination) && successful(&two.termination);
        let raw_observations_equal = stdout_equal && stderr_equal && termination_equal && log_equal;
        let strict_green = raw_observations_equal && nonzero_log_events && successful_exit_both;
        let legacy_log_match = legacy_log_match(
            paths,
            &resolve_artifact(paths, &one.ordered_log_stream.path)?,
            &resolve_artifact(paths, &two.ordered_log_stream.path)?,
        )?;
        let legacy_green = stdout_equal
            && stderr_equal
            && termination_equal
            && legacy_log_match
            && successful_exit_both;
        let producer_tuple = (
            cell.stdout_equal,
            cell.stderr_equal,
            cell.termination_equal,
            cell.ordered_log_stream_equal,
            cell.nonzero_log_events,
            cell.successful_exit_both,
            cell.raw_observations_equal,
            cell.strict_green,
            cell.legacy_log_match,
            cell.legacy_green,
        );
        let verified_tuple = (
            stdout_equal,
            stderr_equal,
            termination_equal,
            log_equal,
            nonzero_log_events,
            successful_exit_both,
            raw_observations_equal,
            strict_green,
            legacy_log_match,
            legacy_green,
        );
        if cell.strict_green && !nonzero_log_events {
            bail!("zero-message cell claimed strict green: {cell_key}");
        }
        if producer_tuple != verified_tuple {
            bail!("producer cell verdict disagrees with dereferenced evidence: {cell_key}");
        }
        if !stdout_equal {
            *mismatch_components.entry("stdout".to_owned()).or_default() += 1;
        }
        if !stderr_equal {
            *mismatch_components.entry("stderr".to_owned()).or_default() += 1;
        }
        if !termination_equal {
            *mismatch_components
                .entry("termination".to_owned())
                .or_default() += 1;
        }
        if !log_equal {
            *mismatch_components
                .entry("ordered_log_stream".to_owned())
                .or_default() += 1;
        }
        if !nonzero_log_events {
            *mismatch_components
                .entry("zero_log_events".to_owned())
                .or_default() += 1;
        }
        if !successful_exit_both {
            *mismatch_components
                .entry("nonzero_or_nonexit".to_owned())
                .or_default() += 1;
        }
        legacy_green_cells += usize::from(legacy_green);
        raw_equal_cells += usize::from(raw_observations_equal);
        strict_green_cells += usize::from(strict_green);
        let entry = by_backend_counts.entry(cell.backend.clone()).or_default();
        entry.0 += 1;
        entry.1 += usize::from(legacy_green);
        entry.2 += usize::from(strict_green);
    }

    let denominator_cells = expected_cells.len();
    let legacy_percent = percent(legacy_green_cells, denominator_cells);
    let strict_percent = percent(strict_green_cells, denominator_cells);
    let by_backend = by_backend_counts
        .into_iter()
        .map(|(backend, (count, legacy, strict))| {
            (
                backend,
                BackendSummary {
                    cells: count,
                    legacy_green: legacy,
                    strict_green: strict,
                    drop_cells: legacy as i64 - strict as i64,
                },
            )
        })
        .collect();
    Ok(VerificationSummary {
        schema: SCHEMA,
        record_type: "strict_metric_verified_summary".to_owned(),
        verified_epoch_ms: epoch_ms(),
        run_kind: kind.to_owned(),
        run_instance: run_instance.to_owned(),
        run_binding_sha256: binding_sha256,
        denominator_cells,
        expected_executions: expected_executions.len(),
        observed_executions: executions.len(),
        missing_execution_keys,
        duplicate_execution_keys,
        unexpected_execution_keys,
        missing_cell_keys,
        duplicate_cell_keys,
        unexpected_cell_keys,
        artifact_hashes_verified,
        aggregate_execution_duration_ms,
        attempted_executions,
        nonzero_log_executions,
        successful_exit_executions,
        legacy_green_cells,
        raw_equal_cells,
        strict_green_cells,
        legacy_green_percent: legacy_percent,
        strict_green_percent: strict_percent,
        absolute_drop_cells: legacy_green_cells as i64 - strict_green_cells as i64,
        drop_percentage_points: legacy_percent - strict_percent,
        mismatch_components,
        by_backend,
        denominator_sha256: sha256_file(&denominator_path)?,
        manifest_sha256: denominator.manifest_sha256.clone(),
        executions_jsonl_sha256: sha256_file(&executions_path)?,
        cells_jsonl_sha256: sha256_file(&cells_path)?,
        hermit_sha: denominator.hermit_sha,
        reverie_sha: denominator.reverie_sha,
        reverie_dependency_sha: denominator.reverie_dependency_sha,
        liteinst2_sha: denominator.liteinst2_sha,
        liteinst2_dependency_sha: denominator.liteinst2_dependency_sha,
        hermit_binary_sha256: denominator.hermit_binary_sha256,
        strict_contract: denominator.comparison_contract,
        required_spot_completion_sha256,
        verification_complete: true,
    })
}

fn verify_axes(denominator: &Denominator, kind: &str) -> Result<()> {
    let unique_tests: BTreeSet<_> = denominator.tests.iter().cloned().collect();
    let unique_backends: BTreeSet<_> = denominator.backends.iter().cloned().collect();
    let unique_modes: BTreeSet<_> = denominator.observation_modes.iter().cloned().collect();
    let unique_ordinals: BTreeSet<_> = denominator.run_ordinals.iter().copied().collect();
    if unique_tests.len() != denominator.tests.len()
        || unique_backends.len() != denominator.backends.len()
        || unique_modes.len() != denominator.observation_modes.len()
        || unique_ordinals.len() != denominator.run_ordinals.len()
    {
        bail!("denominator axes contain duplicates");
    }
    if denominator.run_ordinals != vec![1, 2] {
        bail!("run ordinals must be exactly [1, 2]");
    }
    let expected_backends: Vec<_> = if kind == "selftest" {
        vec!["ptrace".to_owned()]
    } else {
        BACKENDS.iter().map(|value| (*value).to_owned()).collect()
    };
    if denominator.backends != expected_backends {
        bail!("backend axis is not the exact six-path contract");
    }
    let (expected_tests, expected_modes, expected_cells, expected_executions) = match kind {
        "full" => (
            None,
            vec!["strict_raw".to_owned()],
            FULL_TESTS * 6,
            FULL_TESTS * 6 * 2,
        ),
        "calibration" => (
            Some(BTreeSet::from([CALIBRATION_TEST.to_owned()])),
            vec!["strict_raw".to_owned()],
            6,
            12,
        ),
        "spot" => (
            Some(SPOT_TESTS.iter().map(|value| (*value).to_owned()).collect()),
            vec![
                "heap".to_owned(),
                "heap_stack".to_owned(),
                "stack".to_owned(),
            ],
            36,
            72,
        ),
        "selftest" => (
            Some(SELF_TESTS.iter().map(|value| (*value).to_owned()).collect()),
            vec!["strict_raw".to_owned()],
            2,
            4,
        ),
        other => bail!("unknown run kind {other:?}"),
    };
    if kind == "full" && unique_tests.len() != FULL_TESTS {
        bail!("full denominator must contain exactly 231 unique semantic workloads");
    }
    if let Some(expected_tests) = expected_tests {
        if unique_tests != expected_tests {
            bail!("{kind} denominator has the wrong test identities");
        }
    }
    if denominator.observation_modes != expected_modes
        || denominator.expected_cells != expected_cells
        || denominator.expected_executions != expected_executions
    {
        bail!("{kind} denominator axes/counts do not match the frozen contract");
    }
    Ok(())
}

fn verify_run_binding(
    paths: &VerifyPaths,
    denominator: &Denominator,
    binding: &RunBinding,
    _binding_sha256: &str,
    denominator_path: &Path,
    kind: &str,
    run_instance: &str,
) -> Result<()> {
    if binding.schema != SCHEMA
        || binding.record_type != "strict_metric_run_binding"
        || binding.run_kind != kind
        || binding.run_instance != run_instance
        || binding.parent_commit != denominator.parent_commit
        || binding.run_rs_sha256 != denominator.run_rs_sha256
        || binding.verify_rs_sha256 != denominator.verify_rs_sha256
        || binding.log_authority_rs_sha256 != denominator.log_authority_rs_sha256
        || binding.requested_manifest_sha256 != denominator.requested_manifest_sha256
        || binding.input_audit_sha256 != denominator.input_audit_sha256
        || binding.manifest_sha256 != denominator.manifest_sha256
        || binding.denominator_sha256 != sha256_file(denominator_path)?
        || binding.hermit_binary_sha256 != denominator.hermit_binary_sha256
        || binding.guest_set_sha256 != denominator.guest_set_sha256
        || binding.required_spot_completion != denominator.required_spot_completion
    {
        bail!("run binding is detached from the denominator or run identity");
    }
    let manifest_path = paths.experiment.join(&denominator.manifest_path);
    let audit_path = paths.experiment.join(&denominator.input_audit_path);
    let requested_path = paths.experiment.join(&denominator.requested_manifest_path);
    if sha256_file(&manifest_path)? != binding.manifest_sha256
        || sha256_file(&audit_path)? != binding.input_audit_sha256
        || sha256_file(&requested_path)? != binding.requested_manifest_sha256
    {
        bail!("run binding hashes do not dereference to the frozen evidence");
    }
    Ok(())
}

fn verify_parent_harness(paths: &VerifyPaths, denominator: &Denominator) -> Result<()> {
    if denominator.parent_commit.len() != 40
        || !denominator
            .parent_commit
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        bail!("parent harness commit is not a 40-hex object name");
    }
    let run_path = paths.experiment.join("run.rs");
    let verify_path = paths.experiment.join("verify.rs");
    let log_authority_path = paths.experiment.join("log_authority.rs");
    for (path, expected_hash) in [
        (&run_path, &denominator.run_rs_sha256),
        (&verify_path, &denominator.verify_rs_sha256),
        (&log_authority_path, &denominator.log_authority_rs_sha256),
    ] {
        if sha256_file(path)? != *expected_hash {
            bail!("harness bytes moved after run binding: {}", path.display());
        }
        let relative = path.strip_prefix(&paths.parent_repo)?;
        let status = Command::new("git")
            .args(["diff", "--quiet", "HEAD", "--"])
            .arg(relative)
            .current_dir(&paths.parent_repo)
            .status()?;
        if !status.success() {
            bail!("harness file is not the version committed at parent HEAD");
        }
        let object = format!("{}:{}", denominator.parent_commit, relative.display());
        let output = Command::new("git")
            .args(["show", &object])
            .current_dir(&paths.parent_repo)
            .output()?;
        if !output.status.success() || sha256_bytes(&output.stdout) != *expected_hash {
            bail!("bound parent commit lacks the recorded harness bytes: {object}");
        }
    }
    Ok(())
}

fn verify_required_spot_completion(
    paths: &VerifyPaths,
    denominator: &Denominator,
    verify_environment: bool,
) -> Result<String> {
    let evidence = denominator
        .required_spot_completion
        .as_ref()
        .context("full denominator lacks a required spot completion")?;
    let relative = Path::new(&evidence.path);
    if relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        bail!("spot completion path escapes experiment root");
    }
    let path = paths.experiment.join(relative);
    if sha256_file(&path)? != evidence.sha256 {
        bail!("spot completion hash does not match full denominator");
    }
    let recorded: SpotCompletion = read_json(&path)?;
    let expected_path = run_dir(paths, "spot", &recorded.run_instance).join("spot-completion.json");
    if path != expected_path {
        bail!("spot completion is not in its canonical run-instance path");
    }
    let summary = verify(paths, "spot", &recorded.run_instance, verify_environment)?;
    let computed = make_spot_completion(paths, &summary)?;
    if recorded != computed {
        bail!("spot completion does not match independently reverified 72-execution evidence");
    }
    Ok(evidence.sha256.clone())
}

fn make_spot_completion(
    paths: &VerifyPaths,
    summary: &VerificationSummary,
) -> Result<SpotCompletion> {
    if summary.run_kind != "spot"
        || !summary.verification_complete
        || summary.denominator_cells != 36
        || summary.expected_executions != 72
        || summary.observed_executions != 72
        || summary.attempted_executions != 72
        || summary.nonzero_log_executions != 72
        || summary.successful_exit_executions != 72
        || summary.raw_equal_cells != 36
        || summary.strict_green_cells != 36
    {
        bail!(
            "spot summary is not the healthy exact 36-cell/72-execution profile: attempted={} nonzero_info={} successful={} raw_equal={} strict_green={}",
            summary.attempted_executions,
            summary.nonzero_log_executions,
            summary.successful_exit_executions,
            summary.raw_equal_cells,
            summary.strict_green_cells
        );
    }
    let denominator: Denominator =
        read_json(&run_dir(paths, "spot", &summary.run_instance).join("denominator.json"))?;
    let mut completion = SpotCompletion {
        schema: SCHEMA,
        record_type: "strict_metric_spot_completion".to_owned(),
        complete: true,
        run_instance: summary.run_instance.clone(),
        run_binding_sha256: summary.run_binding_sha256.clone(),
        denominator_sha256: summary.denominator_sha256.clone(),
        executions_jsonl_sha256: summary.executions_jsonl_sha256.clone(),
        cells_jsonl_sha256: summary.cells_jsonl_sha256.clone(),
        tests: denominator.tests,
        backends: denominator.backends,
        observation_modes: denominator.observation_modes,
        run_ordinals: denominator.run_ordinals,
        expected_cells: summary.denominator_cells,
        expected_executions: summary.expected_executions,
        attempted_executions: summary.attempted_executions,
        nonzero_info_executions: summary.nonzero_log_executions,
        successful_exit_executions: summary.successful_exit_executions,
        strict_green_cells: summary.strict_green_cells,
        raw_equal_cells: summary.raw_equal_cells,
        completion_digest_sha256: String::new(),
    };
    completion.completion_digest_sha256 = spot_completion_digest(&completion)?;
    Ok(completion)
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

fn verify_input_audit(paths: &VerifyPaths, denominator: &Denominator, kind: &str) -> Result<()> {
    let audit_path = paths.experiment.join(&denominator.input_audit_path);
    if sha256_file(&audit_path)? != denominator.input_audit_sha256 {
        bail!("input audit hash does not match denominator");
    }
    let audit: InputAuditEvidence = read_json(&audit_path)?;
    let expected_tests = if kind == "selftest" { 2 } else { FULL_TESTS };
    let requested_path = paths.experiment.join(&denominator.requested_manifest_path);
    let requested: RequestedManifestEvidence = read_json(&requested_path)?;
    let derived = derive_requested_inputs(
        paths,
        &paths.experiment.join("frozen-corpus-c.tsv"),
        &paths.experiment.join("frozen-corpus-nonc.tsv"),
        Some(&requested),
    )?;
    let mut id_counts: BTreeMap<&str, usize> = BTreeMap::new();
    let mut workload_counts: BTreeMap<&str, usize> = BTreeMap::new();
    let mut derived_missing_tests = BTreeSet::new();
    let mut derived_missing_sources = 0usize;
    for input in &derived {
        *id_counts.entry(&input.id).or_default() += 1;
        *workload_counts
            .entry(&input.workload_identity_sha256)
            .or_default() += 1;
        for source in input.sources.iter().filter(|source| !source.exists) {
            derived_missing_sources += 1;
            let _ = source;
            derived_missing_tests.insert(input.id.clone());
        }
        if !input.available {
            derived_missing_tests.insert(input.id.clone());
        }
    }
    let derived_duplicate_ids = id_counts.values().filter(|count| **count > 1).count();
    let derived_duplicate_workloads = workload_counts.values().filter(|count| **count > 1).count();
    let derived_executable = derived.iter().filter(|input| input.available).count();
    let derived_c_rows = derived
        .iter()
        .filter(|input| input.kind == "compiled_c")
        .count();
    let derived_nonc_rows = derived.len() - derived_c_rows;
    if derived_duplicate_ids != 0 {
        bail!("duplicate test identity independently rederived from corpus");
    }
    if derived_duplicate_workloads != 0 {
        bail!("duplicate workload identity independently rederived from corpus");
    }
    if audit.schema != SCHEMA
        || audit.record_type != "strict_metric_input_audit"
        || !audit.complete
        || !audit.full_sweep_allowed
        || audit.denominator_expected_tests != expected_tests
        || audit.denominator_observed_rows != derived.len()
        || audit.denominator_unique_ids != id_counts.len()
        || audit.denominator_executable_tests != derived_executable
        || audit.c_rows != derived_c_rows
        || audit.nonc_rows != derived_nonc_rows
        || audit.missing_test_count != derived_missing_tests.len()
        || audit.missing_source_count != derived_missing_sources
        || audit.missing_test_ids != derived_missing_tests.into_iter().collect::<Vec<_>>()
        || audit.missing_sources.len() != derived_missing_sources
        || audit.duplicate_ids.len() != derived_duplicate_ids
        || audit.duplicate_workload_identities.len() != derived_duplicate_workloads
        || derived.len() != expected_tests
        || id_counts.len() != expected_tests
        || derived_executable != expected_tests
        || derived_missing_sources != 0
        || derived_duplicate_ids != 0
        || derived_duplicate_workloads != 0
        || audit.requested_manifest_path != denominator.requested_manifest_path
        || audit.requested_manifest_sha256 != denominator.requested_manifest_sha256
        || audit.executable_manifest_path.as_deref() != Some(denominator.manifest_path.as_str())
        || audit.executable_manifest_sha256.as_deref() != Some(denominator.manifest_sha256.as_str())
    {
        bail!("input audit does not prove a complete typed denominator");
    }
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
    let denominator_binding = (
        &denominator.hermit_sha,
        &denominator.reverie_sha,
        &denominator.reverie_dependency_sha,
        &denominator.liteinst2_sha,
        &denominator.liteinst2_dependency_sha,
        &denominator.hermit_binary_sha256,
        &denominator.corpus_c_sha256,
        &denominator.corpus_nonc_sha256,
    );
    if audit_binding != denominator_binding {
        bail!("input audit facts disagree with denominator");
    }

    if sha256_file(&requested_path)? != denominator.requested_manifest_sha256 {
        bail!("requested manifest hash does not match denominator");
    }
    if requested.schema != SCHEMA
        || requested.record_type != "strict_metric_requested_manifest"
        || requested.denominator_expected_tests != expected_tests
        || requested.inputs.len() != expected_tests
    {
        bail!("requested manifest type/count is invalid");
    }
    Ok(())
}

fn rederive_requested_inputs(
    paths: &VerifyPaths,
    denominator: &Denominator,
    manifest: &BTreeMap<String, FrozenTestEvidence>,
    kind: &str,
) -> Result<()> {
    let frozen_c = paths.experiment.join("frozen-corpus-c.tsv");
    let frozen_nonc = paths.experiment.join("frozen-corpus-nonc.tsv");
    if sha256_file(&frozen_c)? != denominator.corpus_c_sha256
        || sha256_file(&frozen_nonc)? != denominator.corpus_nonc_sha256
        || sha256_file(&paths.corpus_c)? != denominator.corpus_c_sha256
        || sha256_file(&paths.corpus_nonc)? != denominator.corpus_nonc_sha256
    {
        bail!("frozen/source corpus bytes are not the bytes bound by the denominator");
    }
    let requested_path = paths.experiment.join(&denominator.requested_manifest_path);
    let requested: RequestedManifestEvidence = read_json(&requested_path)?;
    let expected_count = if kind == "selftest" { 2 } else { FULL_TESTS };
    let binding = (
        &requested.hermit_sha,
        &requested.reverie_sha,
        &requested.reverie_dependency_sha,
        &requested.liteinst2_sha,
        &requested.liteinst2_dependency_sha,
        &requested.hermit_binary_sha256,
        &requested.corpus_c_sha256,
        &requested.corpus_nonc_sha256,
    );
    let expected_binding = (
        &denominator.hermit_sha,
        &denominator.reverie_sha,
        &denominator.reverie_dependency_sha,
        &denominator.liteinst2_sha,
        &denominator.liteinst2_dependency_sha,
        &denominator.hermit_binary_sha256,
        &denominator.corpus_c_sha256,
        &denominator.corpus_nonc_sha256,
    );
    if requested.schema != SCHEMA
        || requested.record_type != "strict_metric_requested_manifest"
        || requested.denominator_expected_tests != expected_count
        || binding != expected_binding
    {
        bail!("requested manifest header is detached from the denominator");
    }
    let derived = derive_requested_inputs(paths, &frozen_c, &frozen_nonc, Some(&requested))?;
    if derived.len() != expected_count {
        bail!(
            "rederived corpus count is {}, expected {expected_count}",
            derived.len()
        );
    }
    if serde_json::to_vec(&derived)? != serde_json::to_vec(&requested.inputs)? {
        bail!("requested manifest rows do not equal independent corpus/repository rederivation");
    }
    let mut ids = BTreeSet::new();
    let mut workload_to_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for input in &derived {
        if !input.available || !ids.insert(input.id.clone()) {
            bail!(
                "rederived input is unavailable or duplicates an ID: {}",
                input.id
            );
        }
        workload_to_ids
            .entry(input.workload_identity_sha256.clone())
            .or_default()
            .push(input.id.clone());
        let test = manifest.get(&input.id).with_context(|| {
            format!("rederived input absent from frozen manifest: {}", input.id)
        })?;
        if test.lane != input.lane
            || test.kind != input.kind
            || test.workload_identity_sha256 != input.workload_identity_sha256
        {
            bail!(
                "frozen test type/lane/workload differs from corpus: {}",
                input.id
            );
        }
        if input.kind == "compiled_c" {
            let compile = test
                .compile
                .as_ref()
                .with_context(|| format!("compiled row lacks compile spec: {}", input.id))?;
            let primary = &input.sources[0];
            let expected_source = paths.hermit_repo.join(&primary.path).display().to_string();
            let expected_extras: Vec<_> = input.sources[1..]
                .iter()
                .map(|source| paths.hermit_repo.join(&source.path).display().to_string())
                .collect();
            if compile.source != expected_source
                || compile.extra_sources != expected_extras
                || compile.command != input.compile_command
                || test.source_sha256 != primary.sha256.as_deref().unwrap_or("")
            {
                bail!(
                    "compile command/input binding differs from corpus: {}",
                    input.id
                );
            }
        } else {
            let receipt = input
                .nonc_preparation
                .as_ref()
                .with_context(|| format!("non-C requested receipt is missing: {}", input.id))?;
            let mut expected_argv = input.argv.clone();
            expected_argv[0] = resolve_program(paths, &expected_argv[0])?
                .display()
                .to_string();
            if test.compile.is_some()
                || test.compile_receipt.is_some()
                || test.argv != expected_argv
                || test.source_sha256 != sha256_bytes(&serde_json::to_vec(&receipt.source_chain)?)
                || test.binary_sha256 != sha256_file(Path::new(&expected_argv[0]))?
                || serde_json::to_vec(&test.nonc_preparation)?
                    != serde_json::to_vec(&input.nonc_preparation)?
            {
                bail!(
                    "non-C argv/source binding differs from corpus: {}",
                    input.id
                );
            }
        }
    }
    let duplicates: Vec<_> = workload_to_ids
        .into_iter()
        .filter(|(_, ids)| ids.len() > 1)
        .collect();
    if !duplicates.is_empty() {
        bail!("duplicate workload identity after independent rederivation: {duplicates:?}");
    }
    Ok(())
}

fn derive_requested_inputs(
    paths: &VerifyPaths,
    corpus_c: &Path,
    corpus_nonc: &Path,
    recorded: Option<&RequestedManifestEvidence>,
) -> Result<Vec<RequestedInputEvidence>> {
    let mut inputs = Vec::new();
    let recorded_by_id: BTreeMap<_, _> = recorded
        .map(|manifest| {
            manifest
                .inputs
                .iter()
                .map(|input| (input.id.as_str(), input))
                .collect()
        })
        .unwrap_or_default();
    for (line_no, line) in BufReader::new(File::open(corpus_c)?).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.split('|').collect();
        if fields.len() != 6 {
            bail!("{}:{} expected 6 fields", corpus_c.display(), line_no + 1);
        }
        let cflags = shell_words::split(fields[2])?;
        let extras = shell_words::split(fields[3])?;
        let mut declared = vec![("primary".to_owned(), fields[1].to_owned())];
        declared.extend(
            extras
                .iter()
                .enumerate()
                .map(|(index, path)| (format!("extra_source_{}", index + 1), path.clone())),
        );
        let mut sources = Vec::new();
        for (role, relative) in declared {
            let absolute = paths.hermit_repo.join(&relative);
            let exists = absolute.is_file();
            sources.push(RequestedSourceEvidence {
                role,
                path: relative,
                exists,
                sha256: exists.then(|| sha256_file(&absolute)).transpose()?,
            });
        }
        let output = paths
            .artifacts
            .join("guests")
            .join(slug(fields[0]))
            .join("guest");
        let source = paths.hermit_repo.join(fields[1]);
        let absolute_extras: Vec<_> = extras
            .iter()
            .map(|path| paths.hermit_repo.join(path).display().to_string())
            .collect();
        let compile_command = c_compile_command(&source, &cflags, &absolute_extras, &output);
        let semantic_compile = compile_command[..compile_command.len() - 2].to_vec();
        inputs.push(RequestedInputEvidence {
            id: fields[0].to_owned(),
            lane: fields[4].to_owned(),
            kind: "compiled_c".to_owned(),
            corpus_file: "compat-envelope/corpus/corpus-c.tsv".to_owned(),
            corpus_line: line_no + 1,
            corpus_row: line,
            argv: Vec::new(),
            canonical_argv: Vec::new(),
            compile_command,
            workload_identity_sha256: workload_identity(
                "compiled_c",
                &[],
                &sources,
                &semantic_compile,
                &paths.hermit_repo,
            )?,
            available: sources.iter().all(|source| source.exists),
            sources,
            nonc_preparation: None,
        });
    }
    for (line_no, line) in BufReader::new(File::open(corpus_nonc)?).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<_> = line.splitn(3, '|').collect();
        if fields.len() != 3 {
            bail!(
                "{}:{} expected 3 fields",
                corpus_nonc.display(),
                line_no + 1
            );
        }
        let expanded = fields[2].replace("HERMITROOT", &paths.hermit_repo.display().to_string());
        let argv = shell_words::split(&expanded)?;
        if argv.is_empty() {
            bail!("{}:{} empty command", corpus_nonc.display(), line_no + 1);
        }
        let sources = discover_nonc_sources(paths, &argv)?;
        let canonical_argv = canonical_nonc_argv(paths, &argv)?;
        let semantic_sources = discover_nonc_sources(paths, &canonical_argv)?;
        let receipt = recorded_by_id
            .get(fields[0])
            .and_then(|input| input.nonc_preparation.clone());
        if let Some(receipt) = &receipt {
            validate_expected_nonc_preparation(
                paths,
                fields[0],
                &argv,
                &canonical_argv,
                &sources,
                receipt,
            )?;
        }
        let available = sources.iter().all(|source| source.exists)
            && receipt
                .as_ref()
                .is_none_or(|receipt| receipt.exit_code == 0);
        inputs.push(RequestedInputEvidence {
            id: fields[0].to_owned(),
            lane: fields[1].to_owned(),
            kind: "script_or_interpreter".to_owned(),
            corpus_file: "compat-envelope/corpus/corpus-nonc.tsv".to_owned(),
            corpus_line: line_no + 1,
            corpus_row: line,
            argv: argv.clone(),
            canonical_argv: canonical_argv.clone(),
            compile_command: Vec::new(),
            workload_identity_sha256: workload_identity(
                "script_or_interpreter",
                &canonical_argv,
                &semantic_sources,
                &[],
                &paths.hermit_repo,
            )?,
            available,
            sources,
            nonc_preparation: receipt,
        });
    }
    inputs.sort_by(|left, right| left.id.cmp(&right.id));
    Ok(inputs)
}

fn validate_expected_nonc_preparation(
    paths: &VerifyPaths,
    id: &str,
    argv: &[String],
    canonical_argv: &[String],
    sources: &[RequestedSourceEvidence],
    receipt: &NoncPreparationReceiptEvidence,
) -> Result<()> {
    validate_nonc_preparation(paths, receipt)?;
    let cell = paths
        .artifacts
        .join("preparation")
        .join("nonc")
        .join(slug(id));
    let home = cell.join("home");
    let xdg = cell.join("xdg-config");
    let tmp = cell.join("tmp");
    let fixtures = cell.join("fixtures");
    let prepare_environment = BTreeMap::from([
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("TZ".to_owned(), "UTC".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), xdg.display().to_string()),
        ("E2E_TMPDIR".to_owned(), tmp.display().to_string()),
        ("E2E_FIXTURE_DIR".to_owned(), fixtures.display().to_string()),
    ]);
    let run_environment_template = BTreeMap::from([
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("TZ".to_owned(), "UTC".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), xdg.display().to_string()),
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
    let log_path = cell.join("prepare.log");
    let prepared_artifacts = collect_prepared_artifacts(paths, &cell, &log_path)?;
    let expected_log_path = log_path
        .strip_prefix(&paths.artifacts)?
        .display()
        .to_string();
    if receipt.protocol
        != if wrapper_protocol {
            "e2e-shell-prepare-run-v1"
        } else {
            "direct-executable-probe-v1"
        }
        || receipt.prepare_command != prepare_command
        || receipt.prepare_environment != prepare_environment
        || receipt.run_environment_template != run_environment_template
        || receipt.source_chain != source_chain
        || receipt.canonical_argv != canonical_argv
        || receipt.prepared_artifacts != prepared_artifacts
        || receipt.log.path != expected_log_path
    {
        bail!("non-C preparation receipt does not equal independent rederivation: {id}");
    }
    Ok(())
}

fn discover_nonc_sources(
    paths: &VerifyPaths,
    argv: &[String],
) -> Result<Vec<RequestedSourceEvidence>> {
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
    discovered
        .into_iter()
        .enumerate()
        .map(|(index, path)| {
            let exists = path.is_file();
            Ok(RequestedSourceEvidence {
                role: format!("source_{:03}", index + 1),
                path: path.display().to_string(),
                exists,
                sha256: exists.then(|| sha256_file(&path)).transpose()?,
            })
        })
        .collect()
}

fn direct_script_path(paths: &VerifyPaths, argv: &[String]) -> Result<Option<PathBuf>> {
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

fn direct_probe_path(paths: &VerifyPaths, argv: &[String]) -> Result<PathBuf> {
    direct_script_path(paths, argv)?.map_or_else(|| resolve_program(paths, &argv[0]), Ok)
}

fn resolve_program(paths: &VerifyPaths, value: &str) -> Result<PathBuf> {
    let declared = PathBuf::from(value);
    if declared.is_absolute() {
        return Ok(declared.canonicalize().unwrap_or(declared));
    }
    if value.contains('/') {
        return Ok(paths.hermit_repo.join(declared));
    }
    let search = std::env::var_os("PATH").context("PATH is unset")?;
    for directory in std::env::split_paths(&search) {
        let candidate = directory.join(value);
        if candidate.is_file() && fs::metadata(&candidate)?.permissions().mode() & 0o111 != 0 {
            return Ok(candidate.canonicalize().unwrap_or(candidate));
        }
    }
    Ok(PathBuf::from(value))
}

fn shebang_interpreter(paths: &VerifyPaths, script: &Path) -> Result<Option<PathBuf>> {
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

fn referenced_repo_files(paths: &VerifyPaths, script: &Path, contents: &str) -> Vec<PathBuf> {
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

fn canonical_nonc_argv(paths: &VerifyPaths, argv: &[String]) -> Result<Vec<String>> {
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

fn collect_prepared_artifacts(
    paths: &VerifyPaths,
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

fn verify_manifest(
    paths: &VerifyPaths,
    denominator: &Denominator,
    kind: &str,
) -> Result<BTreeMap<String, FrozenTestEvidence>> {
    let manifest_path = paths.experiment.join(&denominator.manifest_path);
    let manifest: FrozenManifestEvidence = read_json(&manifest_path)?;
    if manifest.schema != SCHEMA || manifest.record_type != "strict_metric_frozen_manifest" {
        bail!("invalid frozen manifest type/schema");
    }
    let denominator_binding = (
        &denominator.hermit_sha,
        &denominator.reverie_sha,
        &denominator.reverie_dependency_sha,
        &denominator.liteinst2_sha,
        &denominator.liteinst2_dependency_sha,
        &denominator.hermit_binary_sha256,
        &denominator.corpus_c_sha256,
        &denominator.corpus_nonc_sha256,
        &denominator.parent_commit,
        &denominator.run_rs_sha256,
        &denominator.verify_rs_sha256,
        &denominator.guest_set_sha256,
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
        &manifest.parent_commit,
        &manifest.run_rs_sha256,
        &manifest.verify_rs_sha256,
        &manifest.guest_set_sha256,
    );
    if denominator_binding != manifest_binding
        || denominator.log_authority_rs_sha256 != manifest.log_authority_rs_sha256
    {
        bail!("denominator facts disagree with dereferenced frozen manifest");
    }
    if sha256_file(&paths.experiment.join("frozen-corpus-c.tsv"))? != manifest.corpus_c_sha256
        || sha256_file(&paths.experiment.join("frozen-corpus-nonc.tsv"))?
            != manifest.corpus_nonc_sha256
    {
        bail!("frozen corpus bytes disagree with manifest hashes");
    }
    if capture_toolchain()? != manifest.toolchain {
        bail!("toolchain receipt does not match the executing toolchain");
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
        || manifest.hermit_build.toolchain_receipt_sha256 != manifest.toolchain.receipt_sha256
        || manifest.hermit_build.command != expected_build_command
        || manifest.hermit_build.command_sha256
            != sha256_bytes(&serde_json::to_vec(&manifest.hermit_build.command)?)
        || manifest.hermit_build.output.sha256 != manifest.hermit_binary_sha256
    {
        bail!("Hermit causal build receipt is malformed or detached from its source tuple");
    }
    verify_artifact(paths, &manifest.hermit_build.log)?;
    verify_artifact(paths, &manifest.hermit_build.output)?;

    let mut tests = BTreeMap::new();
    let mut workload_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for test in manifest.tests {
        if test.argv.is_empty() {
            bail!("manifest test has empty argv: {}", test.id);
        }
        let binary = Path::new(&test.argv[0]);
        if !binary.is_file() || sha256_file(binary)? != test.binary_sha256 {
            bail!("manifest guest binary missing or changed: {}", test.id);
        }
        workload_ids
            .entry(test.workload_identity_sha256.clone())
            .or_default()
            .push(test.id.clone());
        match (&test.compile, &test.compile_receipt, &test.nonc_preparation) {
            (Some(compile), Some(receipt), None) => {
                if receipt.command != compile.command
                    || receipt.command_sha256
                        != sha256_bytes(&serde_json::to_vec(&compile.command)?)
                    || receipt.toolchain_receipt_sha256 != manifest.toolchain.receipt_sha256
                    || receipt.exit_code != 0
                    || receipt.output.sha256 != test.binary_sha256
                {
                    bail!("compile receipt is detached from test {}", test.id);
                }
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
                if receipt.inputs != expected_inputs {
                    bail!("compile input receipt is detached from test {}", test.id);
                }
                verify_artifact(paths, &receipt.log)?;
                verify_artifact(paths, &receipt.output)?;
            }
            (None, None, Some(receipt)) => validate_nonc_preparation(paths, receipt)?,
            _ => bail!(
                "compile/non-C preparation receipt pairing is invalid: {}",
                test.id
            ),
        }
        if tests.insert(test.id.clone(), test).is_some() {
            bail!("duplicate test identity in frozen manifest");
        }
    }
    let duplicate_workloads: Vec<_> = workload_ids
        .into_iter()
        .filter(|(_, ids)| ids.len() > 1)
        .collect();
    if !duplicate_workloads.is_empty() {
        bail!("duplicate workload identity in frozen manifest: {duplicate_workloads:?}");
    }
    if kind != "selftest" && tests.len() != FULL_TESTS {
        bail!("frozen manifest must contain exactly 231 unique semantic workloads");
    }
    let denominator_tests: BTreeSet<_> = denominator.tests.iter().cloned().collect();
    let manifest_tests: BTreeSet<_> = tests.keys().cloned().collect();
    if !denominator_tests.is_subset(&manifest_tests)
        || (kind == "full" && denominator_tests != manifest_tests)
    {
        bail!("denominator test axis is not bound to the frozen manifest");
    }
    let guest_values: Vec<_> = tests
        .values()
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
    if sha256_bytes(&serde_json::to_vec(&guest_values)?) != manifest.guest_set_sha256 {
        bail!("guest-set digest does not match frozen manifest");
    }
    Ok(tests)
}

fn nonc_receipt_sha256(receipt: &NoncPreparationReceiptEvidence) -> Result<String> {
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
        "canonical_argv": receipt.canonical_argv,
        "canonical_argv_sha256": receipt.canonical_argv_sha256,
    }))?))
}

fn validate_nonc_preparation(
    paths: &VerifyPaths,
    receipt: &NoncPreparationReceiptEvidence,
) -> Result<()> {
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
        || receipt.receipt_sha256 != nonc_receipt_sha256(receipt)?
        || receipt
            .run_environment_template
            .get("E2E_TMPDIR")
            .map(String::as_str)
            != Some("${EXECUTION_TMPDIR}")
        || receipt
            .run_environment_template
            .get("E2E_FIXTURE_DIR")
            .map(String::as_str)
            != Some("${EXECUTION_FIXTURE_DIR}")
    {
        bail!("non-C preparation receipt is internally inconsistent");
    }
    verify_artifact(paths, &receipt.log)?;
    let mut seen = BTreeSet::new();
    for artifact in &receipt.prepared_artifacts {
        if !seen.insert(&artifact.path) {
            bail!("non-C preparation aliases artifact {}", artifact.path);
        }
        let path = resolve_artifact(paths, &artifact.path)?;
        let metadata = fs::metadata(&path)?;
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
    Ok(())
}

fn verify_execution_identity(
    paths: &VerifyPaths,
    denominator: &Denominator,
    manifest: &BTreeMap<String, FrozenTestEvidence>,
    binding_sha256: &str,
    key: &str,
    execution: &ExecutionRecord,
    kind: &str,
) -> Result<()> {
    let bound_cell_id = format!(
        "{}--{}--{}",
        slug(&execution.test_id),
        slug(&execution.backend),
        slug(&execution.observation_mode)
    );
    if execution.cell_id != bound_cell_id
        || key != format!("{}::{}", bound_cell_id, execution.ordinal)
        || !denominator.tests.contains(&execution.test_id)
        || !denominator.backends.contains(&execution.backend)
        || !denominator
            .observation_modes
            .contains(&execution.observation_mode)
        || !denominator.run_ordinals.contains(&execution.ordinal)
    {
        bail!("execution identity is not bound to denominator axes: {key}");
    }
    if sha256_bytes(&serde_json::to_vec(&execution.command)?) != execution.command_sha256
        || sha256_bytes(&serde_json::to_vec(&execution.environment)?)
            != execution.environment_sha256
    {
        bail!("execution command/environment hash mismatch: {key}");
    }
    let test = manifest
        .get(&execution.test_id)
        .with_context(|| format!("execution test absent from manifest: {key}"))?;
    let log_path = resolve_artifact(paths, &execution.ordered_log_stream.path)?;
    let expected_paths = [
        canonical_artifact_relative(
            kind,
            &denominator.run_instance,
            binding_sha256,
            &bound_cell_id,
            &format!("run{}.stdout", execution.ordinal),
        ),
        canonical_artifact_relative(
            kind,
            &denominator.run_instance,
            binding_sha256,
            &bound_cell_id,
            &format!("run{}.stderr", execution.ordinal),
        ),
        canonical_artifact_relative(
            kind,
            &denominator.run_instance,
            binding_sha256,
            &bound_cell_id,
            &format!("run{}.info.log", execution.ordinal),
        ),
        canonical_artifact_relative(
            kind,
            &denominator.run_instance,
            binding_sha256,
            &bound_cell_id,
            &format!("run{}.log-inspection.stderr", execution.ordinal),
        ),
    ];
    if [
        &execution.stdout.path,
        &execution.stderr.path,
        &execution.ordered_log_stream.path,
        &execution.log_parser_diagnostic.path,
    ]
    .into_iter()
    .zip(expected_paths.iter())
    .any(|(actual, expected)| actual != expected)
    {
        bail!("execution artifacts are not in canonical ordinal/stream paths: {key}");
    }
    if execution.guest_binary_sha256 != test.binary_sha256 {
        bail!("execution guest binary hash does not match frozen manifest: {key}");
    }
    let expected_environment = expected_execution_environment(
        paths,
        denominator,
        binding_sha256,
        &bound_cell_id,
        execution.ordinal,
        test,
    )?;
    if execution.environment != expected_environment
        || execution.preparation_receipt_sha256
            != test
                .nonc_preparation
                .as_ref()
                .map(|receipt| receipt.receipt_sha256.clone())
    {
        bail!("execution environment/preparation binding mismatch: {key}");
    }
    let mut expected = vec![
        paths.binary.display().to_string(),
        "--log=info".to_owned(),
        format!("--log-file={}", log_path.display()),
        "--backend".to_owned(),
        execution.backend.clone(),
        "run".to_owned(),
        "--strict".to_owned(),
    ];
    if test.lane == "portable" {
        expected.push("--no-virtualize-cpuid".to_owned());
        expected.push("--max-timeslice=disabled".to_owned());
    }
    match execution.observation_mode.as_str() {
        "strict_raw" => {}
        "heap" => expected.push("--detlog-heap".to_owned()),
        "stack" => expected.push("--detlog-stack".to_owned()),
        "heap_stack" => {
            expected.push("--detlog-heap".to_owned());
            expected.push("--detlog-stack".to_owned());
        }
        other => bail!("unsupported observation mode in execution {key}: {other}"),
    }
    expected.push("--".to_owned());
    expected.extend(test.argv.clone());
    if execution.command != expected {
        bail!("execution command is not bound to typed cell/manifest: {key}");
    }
    Ok(())
}

fn expected_execution_environment(
    paths: &VerifyPaths,
    denominator: &Denominator,
    binding_sha256: &str,
    cell_id: &str,
    ordinal: u8,
    test: &FrozenTestEvidence,
) -> Result<BTreeMap<String, String>> {
    let Some(receipt) = &test.nonc_preparation else {
        return Ok(BTreeMap::from([
            ("LC_ALL".to_owned(), "C".to_owned()),
            ("TZ".to_owned(), "UTC".to_owned()),
        ]));
    };
    validate_nonc_preparation(paths, receipt)?;
    let runtime = paths
        .artifacts
        .join("runtime")
        .join(&denominator.run_kind)
        .join(&denominator.run_instance)
        .join(binding_sha256)
        .join(cell_id)
        .join(format!("run{ordinal}"));
    let tmp = runtime.join("tmp");
    let fixtures = runtime.join("fixtures");
    if !tmp.is_dir() || !fixtures.is_dir() {
        bail!(
            "bound execution tmp/fixture directory is missing: {}",
            runtime.display()
        );
    }
    let mut environment = receipt.run_environment_template.clone();
    if environment.insert("E2E_TMPDIR".to_owned(), tmp.display().to_string())
        != Some("${EXECUTION_TMPDIR}".to_owned())
    {
        bail!("non-C run environment lacks the execution tmpdir placeholder");
    }
    if environment.insert("E2E_FIXTURE_DIR".to_owned(), fixtures.display().to_string())
        != Some("${EXECUTION_FIXTURE_DIR}".to_owned())
    {
        bail!("non-C run environment lacks the execution fixture placeholder");
    }
    Ok(environment)
}

fn verify_termination(termination: &Termination, key: &str) -> Result<()> {
    let valid = match termination.kind.as_str() {
        "exit" => {
            termination.code.is_some() && termination.signal.is_none() && !termination.timed_out
        }
        "signal" => {
            termination.code.is_none() && termination.signal.is_some() && !termination.timed_out
        }
        "timeout" => {
            termination.code.is_none() && termination.signal.is_none() && termination.timed_out
        }
        _ => false,
    };
    if !valid {
        bail!("malformed termination record: {key}");
    }
    Ok(())
}

fn verify_contract(denominator: &Denominator) -> Result<()> {
    let contract = &denominator.comparison_contract;
    let require = |key: &str, expected: serde_json::Value| -> Result<()> {
        if contract.get(key) != Some(&expected) {
            bail!("comparison contract {key:?} is not {expected}");
        }
        Ok(())
    };
    require("stdout", serde_json::json!("raw_bytes"))?;
    require("stderr", serde_json::json!("raw_bytes"))?;
    require(
        "termination",
        serde_json::json!("exact_exit_code_or_signal"),
    )?;
    require(
        "ordered_log_stream",
        serde_json::json!("complete_raw_log_file_bytes"),
    )?;
    require("stripped_prefixes", serde_json::json!([]))?;
    require("canonicalizations", serde_json::json!([]))?;
    require("filters", serde_json::json!([]))?;
    require("minimum_log_events_per_execution", serde_json::json!(1))?;
    require(
        "log_event_parser",
        serde_json::json!(log_authority::PARSER_ID),
    )?;
    require("log_event_level", serde_json::json!("INFO"))
}

fn expected_cell_keys(denominator: &Denominator) -> BTreeSet<String> {
    let mut result = BTreeSet::new();
    for test in &denominator.tests {
        for backend in &denominator.backends {
            for mode in &denominator.observation_modes {
                result.insert(format!("{}--{}--{}", slug(test), slug(backend), slug(mode)));
            }
        }
    }
    result
}

fn expected_execution_keys(cells: &BTreeSet<String>, ordinals: &[u8]) -> BTreeSet<String> {
    let mut result = BTreeSet::new();
    for cell in cells {
        for ordinal in ordinals {
            result.insert(format!("{cell}::{ordinal}"));
        }
    }
    result
}

fn read_execution_jsonl(path: &Path) -> Result<(BTreeMap<String, ExecutionRecord>, Vec<String>)> {
    let mut records = BTreeMap::new();
    let mut duplicates = Vec::new();
    for (line_no, line) in BufReader::new(File::open(path)?).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() {
            bail!("blank JSONL line at {}:{}", path.display(), line_no + 1);
        }
        let record: ExecutionRecord = serde_json::from_str(&line)
            .with_context(|| format!("{}:{}", path.display(), line_no + 1))?;
        let key = format!("{}::{}", record.cell_id, record.ordinal);
        if records.insert(key.clone(), record).is_some() {
            duplicates.push(key);
        }
    }
    Ok((records, duplicates))
}

fn read_cell_jsonl(path: &Path) -> Result<(BTreeMap<String, CellRecord>, Vec<String>)> {
    let mut records = BTreeMap::new();
    let mut duplicates = Vec::new();
    for (line_no, line) in BufReader::new(File::open(path)?).lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() {
            bail!("blank JSONL line at {}:{}", path.display(), line_no + 1);
        }
        let record: CellRecord = serde_json::from_str(&line)
            .with_context(|| format!("{}:{}", path.display(), line_no + 1))?;
        let key = record.cell_id.clone();
        if records.insert(key.clone(), record).is_some() {
            duplicates.push(key);
        }
    }
    Ok((records, duplicates))
}

fn verify_artifact(paths: &VerifyPaths, fact: &ArtifactFact) -> Result<()> {
    let path = resolve_artifact(paths, &fact.path)?;
    let metadata =
        fs::metadata(&path).with_context(|| format!("missing artifact {}", path.display()))?;
    if !metadata.is_file() || metadata.len() != fact.bytes || sha256_file(&path)? != fact.sha256 {
        bail!("artifact size/hash mismatch: {}", path.display());
    }
    Ok(())
}

fn resolve_artifact(paths: &VerifyPaths, relative: &str) -> Result<PathBuf> {
    let relative = Path::new(relative);
    if relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        bail!("artifact path escapes root: {}", relative.display());
    }
    Ok(paths.artifacts.join(relative))
}

fn artifact_files_equal(
    paths: &VerifyPaths,
    left: &ArtifactFact,
    right: &ArtifactFact,
) -> Result<bool> {
    files_equal(
        &resolve_artifact(paths, &left.path)?,
        &resolve_artifact(paths, &right.path)?,
    )
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

fn legacy_log_match(paths: &VerifyPaths, left: &Path, right: &Path) -> Result<bool> {
    let status = Command::new(&paths.binary)
        .arg("log-diff")
        .arg(left)
        .arg(right)
        .arg("--unsafe-strip-lines")
        .arg("--no-color")
        .arg("--limit=1")
        .current_dir(&paths.hermit_repo)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    match status.code() {
        Some(0) => Ok(true),
        Some(1) => Ok(false),
        other => bail!("legacy log-diff infrastructure error: status {other:?}"),
    }
}

fn successful(termination: &Termination) -> bool {
    termination.kind == "exit" && termination.code == Some(0) && !termination.timed_out
}

fn write_outputs(
    paths: &VerifyPaths,
    kind: &str,
    run_instance: &str,
    summary: &VerificationSummary,
) -> Result<()> {
    let run_dir = run_dir(paths, kind, run_instance);
    write_json_atomic(&run_dir.join("summary.json"), summary)?;
    if kind == "spot" {
        let completion = make_spot_completion(paths, summary)?;
        write_json_atomic(&run_dir.join("spot-completion.json"), &completion)?;
    }
    if kind == "full" {
        write_report(paths, summary)?;
    }
    Ok(())
}

fn write_report(paths: &VerifyPaths, summary: &VerificationSummary) -> Result<()> {
    let mut report = String::new();
    report.push_str("# Strict compat green-cell drop — verified result\n\n");
    report.push_str(&format!(
        "Exact frozen inputs: Hermit `{}`, Reverie checkout/dependency `{}`/`{}`, LiteInst2 checkout/dependency `{}`/`{}`. The main denominator contains {} cells and {}/{} expected executions were present exactly once.\n\n",
        summary.hermit_sha,
        summary.reverie_sha,
        summary.reverie_dependency_sha,
        summary.liteinst2_sha,
        summary.liteinst2_dependency_sha,
        summary.denominator_cells,
        summary.observed_executions,
        summary.expected_executions
    ));
    report.push_str(&format!(
        "- Legacy lossy comparator: **{}/{} ({:.6}%)** green.\n- Strict raw comparator: **{}/{} ({:.6}%)** green.\n- Drop: **{} cells, {:.6} percentage points**.\n\n",
        summary.legacy_green_cells,
        summary.denominator_cells,
        summary.legacy_green_percent,
        summary.strict_green_cells,
        summary.denominator_cells,
        summary.strict_green_percent,
        summary.absolute_drop_cells,
        summary.drop_percentage_points
    ));
    report.push_str("A strict green requires successful exits, raw stdout/stderr equality, exact exit-or-signal equality, a nonzero event count on both sides, and byte-exact equality of the complete ordered INFO log files. No prefix, number, path, address, or event is stripped, canonicalized, or filtered.\n\n");
    report.push_str("## Per execution path\n\n| path | cells | legacy green | strict green | drop |\n|---|---:|---:|---:|---:|\n");
    for (backend, row) in &summary.by_backend {
        report.push_str(&format!(
            "| {} | {} | {} | {} | {} |\n",
            backend, row.cells, row.legacy_green, row.strict_green, row.drop_cells
        ));
    }
    report.push_str("\n## Integrity evidence\n\n");
    report.push_str(&format!(
        "Missing/duplicate/unexpected execution and cell sets are all empty. The verifier dereferenced {} artifact hashes. `denominator.json` SHA-256 is `{}`; `manifest.json` is `{}`; `executions.jsonl` is `{}`; `cells.jsonl` is `{}`; the Hermit binary is `{}`.\n",
        summary.artifact_hashes_verified,
        summary.denominator_sha256,
        summary.manifest_sha256,
        summary.executions_jsonl_sha256,
        summary.cells_jsonl_sha256,
        summary.hermit_binary_sha256
    ));
    fs::write(
        run_dir(paths, "full", &summary.run_instance).join("REPORT.md"),
        report,
    )?;
    Ok(())
}

fn run_dir(paths: &VerifyPaths, kind: &str, run_instance: &str) -> PathBuf {
    paths.experiment.join("runs").join(kind).join(run_instance)
}

fn canonical_artifact_relative(
    kind: &str,
    run_instance: &str,
    binding_sha256: &str,
    cell_id: &str,
    filename: &str,
) -> String {
    PathBuf::from("artifacts")
        .join(kind)
        .join(run_instance)
        .join(binding_sha256)
        .join(cell_id)
        .join(filename)
        .display()
        .to_string()
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
    sources: &[RequestedSourceEvidence],
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
        "cc_argv": &cc_argv,
        "cc_output_sha256": &cc_output_sha256,
        "rustc_argv": &rustc_argv,
        "rustc_output_sha256": &rustc_output_sha256,
        "cargo_argv": &cargo_argv,
        "cargo_output_sha256": &cargo_output_sha256,
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
    Ok(sha256_bytes(&serde_json::to_vec(&(
        output.stdout,
        output.stderr,
    ))?))
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

#[derive(Clone)]
struct SelfFixture {
    paths: VerifyPaths,
    kind: String,
    run_instance: String,
    executions: Vec<ExecutionRecord>,
    cells: Vec<CellRecord>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum FixtureFault {
    None,
    DuplicateWorkload,
    BuildReceipt,
    CompileReceipt,
}

fn self_test(default_paths: &VerifyPaths) -> Result<()> {
    let root = std::env::temp_dir().join(format!(
        "strict-metric-verifier-selftest-{}-{}",
        std::process::id(),
        epoch_ms()
    ));
    if root.exists() {
        bail!("self-test path unexpectedly exists: {}", root.display());
    }
    fs::create_dir_all(&root)?;
    log_authority::self_test(&default_paths.binary, &root.join("log-authority"))?;

    let fixture = build_self_fixture(
        default_paths,
        &root.join("main"),
        "selftest",
        FixtureFault::None,
    )?;
    run_verifier_cli(&fixture, true, "")?;
    let fixture_run_dir = run_dir(&fixture.paths, &fixture.kind, &fixture.run_instance);
    let executions_path = fixture_run_dir.join("executions.jsonl");
    let cells_path = fixture_run_dir.join("cells.jsonl");
    let original_executions = fixture.executions.clone();
    let original_cells = fixture.cells.clone();

    let write_records = |executions: &[ExecutionRecord], cells: &[CellRecord]| -> Result<()> {
        write_jsonl_owned(&executions_path, executions)?;
        write_jsonl_owned(&cells_path, cells)
    };

    let mut empty_log = original_executions.clone();
    let empty_log_path = resolve_artifact(&fixture.paths, &empty_log[0].ordered_log_stream.path)?;
    let empty_diag_path =
        resolve_artifact(&fixture.paths, &empty_log[0].log_parser_diagnostic.path)?;
    let original_log = fs::read(&empty_log_path)?;
    let original_diag = fs::read(&empty_diag_path)?;
    fs::write(&empty_log_path, b"")?;
    refresh_execution_log_evidence(&fixture.paths, &mut empty_log[0])?;
    write_records(&empty_log, &original_cells)?;
    run_verifier_cli(&fixture, false, "zero-message")?;
    fs::write(&empty_log_path, &original_log)?;
    fs::write(&empty_diag_path, &original_diag)?;

    let mut stale = original_executions.clone();
    stale[0].run_binding_sha256 = "f".repeat(64);
    write_records(&stale, &original_cells)?;
    run_verifier_cli(&fixture, false, "binding")?;

    let mut alias = original_executions.clone();
    alias[1].stdout = alias[0].stdout.clone();
    write_records(&alias, &original_cells)?;
    run_verifier_cli(&fixture, false, "canonical")?;

    let mut binary_substitution = original_executions.clone();
    binary_substitution[0].command[0] = "/bin/true".to_owned();
    binary_substitution[0].command_sha256 =
        sha256_bytes(&serde_json::to_vec(&binary_substitution[0].command)?);
    write_records(&binary_substitution, &original_cells)?;
    run_verifier_cli(&fixture, false, "command")?;

    let mut command_substitution = original_executions.clone();
    let backend_index = command_substitution[0]
        .command
        .iter()
        .position(|value| value == "ptrace")
        .context("self-test command has no ptrace argument")?;
    command_substitution[0].command[backend_index] = "kvm".to_owned();
    command_substitution[0].command_sha256 =
        sha256_bytes(&serde_json::to_vec(&command_substitution[0].command)?);
    write_records(&command_substitution, &original_cells)?;
    run_verifier_cli(&fixture, false, "command")?;

    let mut log_path_substitution = original_executions.clone();
    let other_log = resolve_artifact(
        &fixture.paths,
        &log_path_substitution[1].ordered_log_stream.path,
    )?;
    let log_arg = log_path_substitution[0]
        .command
        .iter()
        .position(|value| value.starts_with("--log-file="))
        .context("self-test command has no log-file argument")?;
    log_path_substitution[0].command[log_arg] = format!("--log-file={}", other_log.display());
    log_path_substitution[0].command_sha256 =
        sha256_bytes(&serde_json::to_vec(&log_path_substitution[0].command)?);
    write_records(&log_path_substitution, &original_cells)?;
    run_verifier_cli(&fixture, false, "command")?;

    write_records(
        &original_executions[..original_executions.len() - 1],
        &original_cells,
    )?;
    run_verifier_cli(&fixture, false, "missing_exec")?;

    let mut duplicate_executions = original_executions.clone();
    duplicate_executions.push(original_executions[0].clone());
    write_records(&duplicate_executions, &original_cells)?;
    run_verifier_cli(&fixture, false, "duplicate_exec")?;

    write_records(
        &original_executions,
        &original_cells[..original_cells.len() - 1],
    )?;
    run_verifier_cli(&fixture, false, "missing_cell")?;

    let mut duplicate_cells = original_cells.clone();
    duplicate_cells.push(original_cells[0].clone());
    write_records(&original_executions, &duplicate_cells)?;
    run_verifier_cli(&fixture, false, "duplicate_cell")?;

    let mut malformed = original_executions.clone();
    malformed[0].termination.signal = Some(9);
    write_records(&malformed, &original_cells)?;
    run_verifier_cli(&fixture, false, "malformed termination")?;

    let mut normalization = original_executions.clone();
    let second_log = resolve_artifact(&fixture.paths, &normalization[1].ordered_log_stream.path)?;
    let original_second_log = fs::read(&second_log)?;
    fs::write(
        &second_log,
        b"2026-08-06T10:00:00.000000Z  INFO detcore: DETLOG fixture value=999\n",
    )?;
    let second_diag =
        resolve_artifact(&fixture.paths, &normalization[1].log_parser_diagnostic.path)?;
    let original_second_diag = fs::read(&second_diag)?;
    refresh_execution_log_evidence(&fixture.paths, &mut normalization[1])?;
    write_records(&normalization, &original_cells)?;
    run_verifier_cli(&fixture, false, "producer cell verdict")?;
    fs::write(&second_log, &original_second_log)?;
    fs::write(&second_diag, &original_second_diag)?;
    write_records(&original_executions, &original_cells)?;

    let harness_path = fixture.paths.experiment.join("verify.rs");
    let original_harness = fs::read(&harness_path)?;
    let mut changed_harness = original_harness.clone();
    changed_harness.extend_from_slice(b"\n// self-test harness mutation\n");
    fs::write(&harness_path, changed_harness)?;
    run_verifier_cli(&fixture, false, "harness bytes moved")?;
    fs::write(&harness_path, original_harness)?;

    let duplicate = build_self_fixture(
        default_paths,
        &root.join("duplicate-workload"),
        "selftest",
        FixtureFault::DuplicateWorkload,
    )?;
    run_verifier_cli(&duplicate, false, "duplicate workload identity")?;

    let bad_build = build_self_fixture(
        default_paths,
        &root.join("bad-build-receipt"),
        "selftest",
        FixtureFault::BuildReceipt,
    )?;
    run_verifier_cli(&bad_build, false, "causal build receipt")?;

    let bad_compile = build_self_fixture(
        default_paths,
        &root.join("bad-compile-receipt"),
        "selftest",
        FixtureFault::CompileReceipt,
    )?;
    run_verifier_cli(&bad_compile, false, "compile receipt")?;

    let unhealthy_spot = build_self_fixture(
        default_paths,
        &root.join("unhealthy-spot"),
        "spot",
        FixtureFault::None,
    )?;
    let mut unhealthy_executions = unhealthy_spot.executions.clone();
    let mut unhealthy_cells = unhealthy_spot.cells.clone();
    for execution in &mut unhealthy_executions[0..2] {
        execution.termination.code = Some(1);
    }
    unhealthy_cells[0].successful_exit_both = false;
    unhealthy_cells[0].strict_green = false;
    unhealthy_cells[0].legacy_green = false;
    let unhealthy_run_dir = run_dir(
        &unhealthy_spot.paths,
        &unhealthy_spot.kind,
        &unhealthy_spot.run_instance,
    );
    write_jsonl_owned(
        &unhealthy_run_dir.join("executions.jsonl"),
        &unhealthy_executions,
    )?;
    write_jsonl_owned(&unhealthy_run_dir.join("cells.jsonl"), &unhealthy_cells)?;
    run_verifier_cli(&unhealthy_spot, false, "healthy exact")?;

    let spot = build_self_fixture(
        default_paths,
        &root.join("spot"),
        "spot",
        FixtureFault::None,
    )?;
    run_verifier_cli(&spot, true, "")?;
    let spot_run_dir = run_dir(&spot.paths, "spot", &spot.run_instance);
    let spot_completion_path = spot_run_dir.join("spot-completion.json");
    let spot_evidence = EvidenceFile {
        path: spot_completion_path
            .strip_prefix(&spot.paths.experiment)?
            .display()
            .to_string(),
        sha256: sha256_file(&spot_completion_path)?,
    };
    let full = write_self_run(spot.paths.clone(), "full", Some(spot_evidence))?;
    run_verifier_cli(&full, true, "")?;

    let mut malformed_completion: SpotCompletion = read_json(&spot_completion_path)?;
    malformed_completion.complete = false;
    malformed_completion.completion_digest_sha256 = spot_completion_digest(&malformed_completion)?;
    write_json_atomic(&spot_completion_path, &malformed_completion)?;
    let malformed_evidence = EvidenceFile {
        path: spot_completion_path
            .strip_prefix(&spot.paths.experiment)?
            .display()
            .to_string(),
        sha256: sha256_file(&spot_completion_path)?,
    };
    let full_run_dir = run_dir(&full.paths, "full", &full.run_instance);
    let mut full_denominator: Denominator = read_json(&full_run_dir.join("denominator.json"))?;
    full_denominator.required_spot_completion = Some(malformed_evidence.clone());
    write_json_atomic(&full_run_dir.join("denominator.json"), &full_denominator)?;
    let mut full_binding: RunBinding = read_json(&full_run_dir.join("run-binding.json"))?;
    full_binding.denominator_sha256 = sha256_file(&full_run_dir.join("denominator.json"))?;
    full_binding.required_spot_completion = Some(malformed_evidence);
    write_json_atomic(&full_run_dir.join("run-binding.json"), &full_binding)?;
    run_verifier_cli(&full, false, "spot completion")?;

    let mut spot_denominator: Denominator = read_json(&spot_run_dir.join("denominator.json"))?;
    spot_denominator.observation_modes = vec!["heap".to_owned(), "stack".to_owned()];
    write_json_atomic(&spot_run_dir.join("denominator.json"), &spot_denominator)?;
    run_verifier_cli(&spot, false, "axes")?;

    fs::remove_dir_all(&root)?;
    println!(
        "SELF-TEST PASS: normal CLI positive profiles=3 (selftest, exact 72-execution spot, spot-gated 2,772-execution full); negatives=19 refused, including harness/build/compile/workload authority, empty logs, stale binding, ordinal aliases, substitutions, missing/duplicate coverage, malformed termination, normalization, unhealthy spot evidence, malformed spot axes, and malformed spot completion"
    );
    Ok(())
}

fn refresh_execution_log_evidence(
    paths: &VerifyPaths,
    execution: &mut ExecutionRecord,
) -> Result<()> {
    let log = resolve_artifact(paths, &execution.ordered_log_stream.path)?;
    let diagnostic = resolve_artifact(paths, &execution.log_parser_diagnostic.path)?;
    let inspection = log_authority::inspect_file(&paths.binary, &log)?;
    fs::write(&diagnostic, &inspection.diagnostic_stderr)?;
    execution.ordered_log_stream = artifact_fact_for(paths, &log)?;
    execution.log_event_count = inspection.counts.info_messages;
    execution.log_parser = inspection.parser_id;
    execution.log_level = "INFO".to_owned();
    execution.log_counts = inspection.counts;
    execution.log_parser_command_sha256 = sha256_bytes(&serde_json::to_vec(&inspection.command)?);
    execution.log_parser_command = inspection.command;
    execution.log_parser_diagnostic = artifact_fact_for(paths, &diagnostic)?;
    Ok(())
}

fn build_self_fixture(
    default_paths: &VerifyPaths,
    root: &Path,
    kind: &str,
    fault: FixtureFault,
) -> Result<SelfFixture> {
    let parent_repo = root.join("parent");
    let experiment = parent_repo.join("experiment");
    let artifacts = root.join("artifacts");
    fs::create_dir_all(&experiment)?;
    fs::create_dir_all(artifacts.join("preparation"))?;
    let fixture_binary = root.join("fixture-hermit");
    let real_binary = default_paths
        .binary
        .display()
        .to_string()
        .replace('\'', "'\\''");
    fs::write(
        &fixture_binary,
        format!("#!/bin/sh\nexec '{real_binary}' \"$@\"\n"),
    )?;
    fs::set_permissions(&fixture_binary, fs::Permissions::from_mode(0o755))?;
    fs::copy(
        default_paths.experiment.join("run.rs"),
        experiment.join("run.rs"),
    )?;
    fs::copy(
        default_paths.experiment.join("verify.rs"),
        experiment.join("verify.rs"),
    )?;
    fs::copy(
        default_paths.experiment.join("log_authority.rs"),
        experiment.join("log_authority.rs"),
    )?;
    run_checked(
        Command::new("git")
            .args(["init", "-q"])
            .current_dir(&parent_repo),
    )?;
    run_checked(
        Command::new("git")
            .args([
                "config",
                "user.email",
                "strict-metric-selftest@example.invalid",
            ])
            .current_dir(&parent_repo),
    )?;
    run_checked(
        Command::new("git")
            .args(["config", "user.name", "strict-metric-selftest"])
            .current_dir(&parent_repo),
    )?;
    run_checked(
        Command::new("git")
            .args([
                "add",
                "experiment/run.rs",
                "experiment/verify.rs",
                "experiment/log_authority.rs",
            ])
            .current_dir(&parent_repo),
    )?;
    run_checked(
        Command::new("git")
            .args(["commit", "-q", "-m", "Freeze self-test harness"])
            .current_dir(&parent_repo),
    )?;

    let corpus_c = experiment.join("frozen-corpus-c.tsv");
    let corpus_nonc = experiment.join("frozen-corpus-nonc.tsv");
    let mut test_ids: Vec<String> = if kind == "spot" {
        let mut ids = vec![SPOT_TESTS[0].to_owned(), SPOT_TESTS[1].to_owned()];
        ids.extend((0..229).map(|index| format!("fixture/spot-dummy-{index:03}")));
        ids
    } else {
        SELF_TESTS.iter().map(|value| (*value).to_owned()).collect()
    };
    test_ids.sort();
    let compiled_id = if fault == FixtureFault::DuplicateWorkload {
        None
    } else if kind == "spot" {
        Some(SPOT_TESTS[0])
    } else {
        Some(SELF_TESTS[0])
    };
    let c_corpus = compiled_id
        .map(|id| format!("{id}|tests/backend-parity/fixtures/pid_probe.c|||portable|disabled\n"))
        .unwrap_or_default();
    fs::write(&corpus_c, c_corpus)?;
    let binary = fixture_binary.display().to_string();
    let mut corpus = String::new();
    for (index, test_id) in test_ids.iter().enumerate() {
        if compiled_id == Some(test_id.as_str()) {
            continue;
        }
        let discriminator = if fault == FixtureFault::DuplicateWorkload {
            0
        } else {
            index
        };
        corpus.push_str(&format!(
            "{test_id}|portable|{binary} --strict-metric-fixture {discriminator}\n"
        ));
    }
    fs::write(&corpus_nonc, corpus)?;
    let paths = VerifyPaths {
        parent_repo: parent_repo.clone(),
        experiment: experiment.clone(),
        artifacts: artifacts.clone(),
        binary: fixture_binary,
        hermit_repo: default_paths.hermit_repo.clone(),
        reverie_repo: default_paths.reverie_repo.clone(),
        liteinst_repo: default_paths.liteinst_repo.clone(),
        corpus_c: corpus_c.clone(),
        corpus_nonc: corpus_nonc.clone(),
    };
    let mut inputs = derive_requested_inputs(&paths, &corpus_c, &corpus_nonc, None)?;
    for input in inputs
        .iter_mut()
        .filter(|input| input.kind == "script_or_interpreter")
    {
        let receipt = make_self_nonc_preparation(&paths, input)?;
        input.available =
            input.sources.iter().all(|source| source.exists) && receipt.exit_code == 0;
        input.nonc_preparation = Some(receipt);
    }
    let hermit_sha = git_head(&paths.hermit_repo)?;
    let reverie_sha = git_head(&paths.reverie_repo)?;
    let liteinst2_sha = git_head(&paths.liteinst_repo)?;
    let reverie_dependency_sha = cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/reverie")?;
    let liteinst2_dependency_sha = cargo_lock_git_rev(&paths.hermit_repo, "rrnewton/liteinst2")?;
    let binary_sha256 = sha256_file(&paths.binary)?;
    let requested = RequestedManifestEvidence {
        schema: SCHEMA,
        record_type: "strict_metric_requested_manifest".to_owned(),
        hermit_sha: hermit_sha.clone(),
        reverie_sha: reverie_sha.clone(),
        reverie_dependency_sha: reverie_dependency_sha.clone(),
        liteinst2_sha: liteinst2_sha.clone(),
        liteinst2_dependency_sha: liteinst2_dependency_sha.clone(),
        hermit_binary_sha256: binary_sha256.clone(),
        corpus_c_sha256: sha256_file(&corpus_c)?,
        corpus_nonc_sha256: sha256_file(&corpus_nonc)?,
        denominator_expected_tests: inputs.len(),
        inputs: inputs.clone(),
    };
    write_json_atomic(&experiment.join("requested-manifest.json"), &requested)?;

    let toolchain = capture_toolchain()?;
    let build_log = artifacts.join("preparation/hermit-build.log");
    let build_snapshot = artifacts.join("preparation/hermit.snapshot");
    fs::write(&build_log, b"self-test inert build receipt\n")?;
    fs::copy(&paths.binary, &build_snapshot)?;
    let parent_commit = git_head(&parent_repo)?;
    let mut tests = Vec::new();
    for input in &inputs {
        let source_sha256 = input.sources[0]
            .sha256
            .clone()
            .with_context(|| format!("self-test source lacks a hash: {}", input.id))?;
        if input.kind == "compiled_c" {
            let output = PathBuf::from(
                input
                    .compile_command
                    .last()
                    .context("self-test compile command lacks output")?,
            );
            fs::create_dir_all(output.parent().context("self-test guest lacks parent")?)?;
            let compile_log = artifacts
                .join("compile-logs")
                .join(format!("{}.log", slug(&input.id)));
            fs::create_dir_all(compile_log.parent().unwrap())?;
            let log = File::create(&compile_log)?;
            let status = Command::new(&input.compile_command[0])
                .args(&input.compile_command[1..])
                .stdout(Stdio::from(log.try_clone()?))
                .stderr(Stdio::from(log))
                .status()?;
            if !status.success() || !output.is_file() {
                bail!(
                    "self-test real compiler command failed for {}: {status}",
                    input.id
                );
            }
            let source = paths.hermit_repo.join(&input.sources[0].path);
            let extra_sources: Vec<_> = input.sources[1..]
                .iter()
                .map(|source| paths.hermit_repo.join(&source.path).display().to_string())
                .collect();
            let compile = CompileSpecEvidence {
                source: source.display().to_string(),
                cflags: Vec::new(),
                extra_sources: extra_sources.clone(),
                command: input.compile_command.clone(),
            };
            let mut receipt_command = compile.command.clone();
            if fault == FixtureFault::CompileReceipt {
                receipt_command[0] = "/bin/true".to_owned();
            }
            let mut receipt_inputs = vec![SourceFact {
                role: "primary".to_owned(),
                path: source.display().to_string(),
                sha256: source_sha256.clone(),
            }];
            for (index, source) in extra_sources.iter().enumerate() {
                receipt_inputs.push(SourceFact {
                    role: format!("extra_source_{}", index + 1),
                    path: source.clone(),
                    sha256: sha256_file(Path::new(source))?,
                });
            }
            let binary_sha256 = sha256_file(&output)?;
            tests.push(FrozenTestEvidence {
                id: input.id.clone(),
                lane: input.lane.clone(),
                kind: input.kind.clone(),
                argv: vec![output.display().to_string()],
                source_sha256,
                binary_sha256,
                workload_identity_sha256: input.workload_identity_sha256.clone(),
                compile: Some(compile),
                compile_receipt: Some(CompileReceiptEvidence {
                    command_sha256: sha256_bytes(&serde_json::to_vec(&receipt_command)?),
                    command: receipt_command,
                    inputs: receipt_inputs,
                    toolchain_receipt_sha256: toolchain.receipt_sha256.clone(),
                    exit_code: status
                        .code()
                        .context("self-test compiler exited without code")?,
                    log: artifact_fact_for(&paths, &compile_log)?,
                    output: artifact_fact_for(&paths, &output)?,
                }),
                nonc_preparation: None,
            });
        } else {
            let receipt = input
                .nonc_preparation
                .clone()
                .with_context(|| format!("self-test non-C receipt missing: {}", input.id))?;
            let mut argv = input.argv.clone();
            argv[0] = resolve_program(&paths, &argv[0])?.display().to_string();
            tests.push(FrozenTestEvidence {
                id: input.id.clone(),
                lane: input.lane.clone(),
                kind: input.kind.clone(),
                argv: argv.clone(),
                source_sha256: sha256_bytes(&serde_json::to_vec(&receipt.source_chain)?),
                binary_sha256: sha256_file(Path::new(&argv[0]))?,
                workload_identity_sha256: input.workload_identity_sha256.clone(),
                compile: None,
                compile_receipt: None,
                nonc_preparation: Some(receipt),
            });
        }
    }
    tests.sort_by(|left, right| left.id.cmp(&right.id));
    let guest_values: Vec<_> = tests
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
    let guest_set_sha256 = sha256_bytes(&serde_json::to_vec(&guest_values)?);
    let build_command = if fault == FixtureFault::BuildReceipt {
        vec!["/bin/true".to_owned()]
    } else {
        hermit_build_command()
    };
    let manifest = FrozenManifestEvidence {
        schema: SCHEMA,
        record_type: "strict_metric_frozen_manifest".to_owned(),
        hermit_sha: hermit_sha.clone(),
        reverie_sha: reverie_sha.clone(),
        reverie_dependency_sha: reverie_dependency_sha.clone(),
        liteinst2_sha: liteinst2_sha.clone(),
        liteinst2_dependency_sha: liteinst2_dependency_sha.clone(),
        hermit_binary_sha256: binary_sha256.clone(),
        corpus_c_sha256: requested.corpus_c_sha256.clone(),
        corpus_nonc_sha256: requested.corpus_nonc_sha256.clone(),
        parent_commit: parent_commit.clone(),
        run_rs_sha256: sha256_file(&experiment.join("run.rs"))?,
        verify_rs_sha256: sha256_file(&experiment.join("verify.rs"))?,
        log_authority_rs_sha256: sha256_file(&experiment.join("log_authority.rs"))?,
        toolchain: toolchain.clone(),
        hermit_build: HermitBuildReceiptEvidence {
            source_sha: hermit_sha.clone(),
            source_tree: git_tree(&paths.hermit_repo)?,
            cargo_lock_sha256: sha256_file(&paths.hermit_repo.join("Cargo.lock"))?,
            clean_before: true,
            clean_after: true,
            binary_absent_before: true,
            command_sha256: sha256_bytes(&serde_json::to_vec(&build_command)?),
            command: build_command,
            toolchain_receipt_sha256: toolchain.receipt_sha256.clone(),
            exit_code: 0,
            log: artifact_fact_for(&paths, &build_log)?,
            output: artifact_fact_for(&paths, &build_snapshot)?,
        },
        guest_set_sha256: guest_set_sha256.clone(),
        tests: tests.clone(),
    };
    write_json_atomic(&experiment.join("manifest.json"), &manifest)?;
    let manifest_sha256 = sha256_file(&experiment.join("manifest.json"))?;
    let requested_manifest_sha256 = sha256_file(&experiment.join("requested-manifest.json"))?;
    let audit = InputAuditEvidence {
        schema: SCHEMA,
        record_type: "strict_metric_input_audit".to_owned(),
        complete: true,
        full_sweep_allowed: true,
        denominator_expected_tests: inputs.len(),
        denominator_observed_rows: inputs.len(),
        denominator_unique_ids: inputs.len(),
        denominator_executable_tests: inputs.len(),
        c_rows: inputs
            .iter()
            .filter(|input| input.kind == "compiled_c")
            .count(),
        nonc_rows: inputs
            .iter()
            .filter(|input| input.kind == "script_or_interpreter")
            .count(),
        missing_test_count: 0,
        missing_source_count: 0,
        missing_test_ids: Vec::new(),
        missing_sources: Vec::new(),
        duplicate_ids: Vec::new(),
        duplicate_workload_identities: Vec::new(),
        requested_manifest_path: "requested-manifest.json".to_owned(),
        requested_manifest_sha256: requested_manifest_sha256.clone(),
        executable_manifest_path: Some("manifest.json".to_owned()),
        executable_manifest_sha256: Some(manifest_sha256.clone()),
        hermit_sha: hermit_sha.clone(),
        reverie_sha: reverie_sha.clone(),
        reverie_dependency_sha: reverie_dependency_sha.clone(),
        liteinst2_sha: liteinst2_sha.clone(),
        liteinst2_dependency_sha: liteinst2_dependency_sha.clone(),
        hermit_binary_sha256: binary_sha256.clone(),
        corpus_c_sha256: requested.corpus_c_sha256.clone(),
        corpus_nonc_sha256: requested.corpus_nonc_sha256.clone(),
    };
    write_json_atomic(&experiment.join("input-audit.json"), &audit)?;
    write_self_run(paths, kind, None)
}

fn make_self_nonc_preparation(
    paths: &VerifyPaths,
    input: &RequestedInputEvidence,
) -> Result<NoncPreparationReceiptEvidence> {
    let cell = paths
        .artifacts
        .join("preparation")
        .join("nonc")
        .join(slug(&input.id));
    let home = cell.join("home");
    let xdg = cell.join("xdg-config");
    let tmp = cell.join("tmp");
    let fixtures = cell.join("fixtures");
    let captures = cell.join("captures");
    for directory in [&home, &xdg, &tmp, &fixtures, &captures] {
        fs::create_dir_all(directory)?;
    }
    let source_xdg = paths.hermit_repo.join("tests/e2e/xdg-config");
    if source_xdg.is_dir() {
        copy_self_fixture_tree(&source_xdg, &xdg)?;
    }
    let prepare_environment = BTreeMap::from([
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("TZ".to_owned(), "UTC".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), xdg.display().to_string()),
        ("E2E_TMPDIR".to_owned(), tmp.display().to_string()),
        ("E2E_FIXTURE_DIR".to_owned(), fixtures.display().to_string()),
    ]);
    let run_environment_template = BTreeMap::from([
        ("LC_ALL".to_owned(), "C".to_owned()),
        ("TZ".to_owned(), "UTC".to_owned()),
        ("HOME".to_owned(), home.display().to_string()),
        ("XDG_CONFIG_HOME".to_owned(), xdg.display().to_string()),
        ("E2E_TMPDIR".to_owned(), "${EXECUTION_TMPDIR}".to_owned()),
        (
            "E2E_FIXTURE_DIR".to_owned(),
            "${EXECUTION_FIXTURE_DIR}".to_owned(),
        ),
    ]);
    let prepare_command = vec![
        "/usr/bin/test".to_owned(),
        "-x".to_owned(),
        direct_probe_path(paths, &input.argv)?.display().to_string(),
    ];
    let log_path = cell.join("prepare.log");
    let log = File::create(&log_path)?;
    let status = Command::new(&prepare_command[0])
        .args(&prepare_command[1..])
        .envs(&prepare_environment)
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .status()?;
    let source_chain: Vec<_> = input
        .sources
        .iter()
        .map(|source| {
            Ok(SourceFact {
                role: source.role.clone(),
                path: source.path.clone(),
                sha256: source
                    .sha256
                    .clone()
                    .with_context(|| format!("missing source hash for {}", input.id))?,
            })
        })
        .collect::<Result<_>>()?;
    let prepared_artifacts = collect_prepared_artifacts(paths, &cell, &log_path)?;
    let mut receipt = NoncPreparationReceiptEvidence {
        protocol: "direct-executable-probe-v1".to_owned(),
        prepare_command_sha256: sha256_bytes(&serde_json::to_vec(&prepare_command)?),
        prepare_command,
        prepare_environment_sha256: sha256_bytes(&serde_json::to_vec(&prepare_environment)?),
        prepare_environment,
        run_environment_template_sha256: sha256_bytes(&serde_json::to_vec(
            &run_environment_template,
        )?),
        run_environment_template,
        source_chain,
        exit_code: status
            .code()
            .context("self-test preparation exited without code")?,
        log: artifact_fact_for(paths, &log_path)?,
        prepared_artifacts_sha256: sha256_bytes(&serde_json::to_vec(&prepared_artifacts)?),
        prepared_artifacts,
        canonical_argv_sha256: sha256_bytes(&serde_json::to_vec(&input.canonical_argv)?),
        canonical_argv: input.canonical_argv.clone(),
        receipt_sha256: String::new(),
    };
    receipt.receipt_sha256 = nonc_receipt_sha256(&receipt)?;
    Ok(receipt)
}

fn copy_self_fixture_tree(source: &Path, destination: &Path) -> Result<()> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let target = destination.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            fs::create_dir_all(&target)?;
            copy_self_fixture_tree(&entry.path(), &target)?;
        } else {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

fn write_self_run(
    paths: VerifyPaths,
    kind: &str,
    required_spot_completion: Option<EvidenceFile>,
) -> Result<SelfFixture> {
    let experiment = paths.experiment.clone();
    let artifacts = paths.artifacts.clone();
    let manifest: FrozenManifestEvidence = read_json(&experiment.join("manifest.json"))?;
    let requested: RequestedManifestEvidence =
        read_json(&experiment.join("requested-manifest.json"))?;
    let tests = manifest.tests.clone();
    let hermit_sha = manifest.hermit_sha.clone();
    let reverie_sha = manifest.reverie_sha.clone();
    let reverie_dependency_sha = manifest.reverie_dependency_sha.clone();
    let liteinst2_sha = manifest.liteinst2_sha.clone();
    let liteinst2_dependency_sha = manifest.liteinst2_dependency_sha.clone();
    let binary_sha256 = manifest.hermit_binary_sha256.clone();
    let parent_commit = manifest.parent_commit.clone();
    let guest_set_sha256 = manifest.guest_set_sha256.clone();
    let manifest_sha256 = sha256_file(&experiment.join("manifest.json"))?;
    let requested_manifest_sha256 = sha256_file(&experiment.join("requested-manifest.json"))?;
    let selected_tests: Vec<String> = match kind {
        "selftest" => SELF_TESTS.iter().map(|value| (*value).to_owned()).collect(),
        "spot" => SPOT_TESTS.iter().map(|value| (*value).to_owned()).collect(),
        "full" => tests.iter().map(|test| test.id.clone()).collect(),
        other => bail!("unsupported self-test fixture kind {other}"),
    };
    let backends: Vec<String> = if kind == "selftest" {
        vec!["ptrace".to_owned()]
    } else {
        BACKENDS.iter().map(|value| (*value).to_owned()).collect()
    };
    let modes: Vec<String> = if kind == "spot" {
        vec![
            "heap".to_owned(),
            "heap_stack".to_owned(),
            "stack".to_owned(),
        ]
    } else {
        vec!["strict_raw".to_owned()]
    };
    let expected_cells = selected_tests.len() * backends.len() * modes.len();
    let run_instance = format!("{kind}-bracket");
    let denominator = Denominator {
        schema: SCHEMA,
        record_type: "strict_metric_denominator".to_owned(),
        run_kind: kind.to_owned(),
        run_instance: run_instance.clone(),
        hermit_sha,
        reverie_sha,
        reverie_dependency_sha,
        liteinst2_sha,
        liteinst2_dependency_sha,
        hermit_binary_sha256: binary_sha256.clone(),
        parent_commit: parent_commit.clone(),
        run_rs_sha256: manifest.run_rs_sha256.clone(),
        verify_rs_sha256: manifest.verify_rs_sha256.clone(),
        log_authority_rs_sha256: manifest.log_authority_rs_sha256.clone(),
        guest_set_sha256: guest_set_sha256.clone(),
        input_audit_path: "input-audit.json".to_owned(),
        input_audit_sha256: sha256_file(&experiment.join("input-audit.json"))?,
        requested_manifest_path: "requested-manifest.json".to_owned(),
        requested_manifest_sha256,
        manifest_path: "manifest.json".to_owned(),
        manifest_sha256: manifest_sha256.clone(),
        corpus_c_sha256: requested.corpus_c_sha256,
        corpus_nonc_sha256: requested.corpus_nonc_sha256,
        tests: selected_tests.clone(),
        backends: backends.clone(),
        observation_modes: modes.clone(),
        run_ordinals: vec![1, 2],
        expected_cells,
        expected_executions: expected_cells * 2,
        required_spot_completion: required_spot_completion.clone(),
        comparison_contract: strict_contract(),
    };
    let run_dir = run_dir(&paths, kind, &run_instance);
    fs::create_dir_all(&run_dir)?;
    write_json_atomic(&run_dir.join("denominator.json"), &denominator)?;
    let binding = RunBinding {
        schema: SCHEMA,
        record_type: "strict_metric_run_binding".to_owned(),
        run_kind: kind.to_owned(),
        run_instance: run_instance.clone(),
        parent_commit,
        run_rs_sha256: manifest.run_rs_sha256,
        verify_rs_sha256: manifest.verify_rs_sha256,
        log_authority_rs_sha256: manifest.log_authority_rs_sha256,
        requested_manifest_sha256: denominator.requested_manifest_sha256.clone(),
        input_audit_sha256: denominator.input_audit_sha256.clone(),
        manifest_sha256,
        denominator_sha256: sha256_file(&run_dir.join("denominator.json"))?,
        hermit_binary_sha256: binary_sha256,
        guest_set_sha256,
        required_spot_completion,
    };
    write_json_atomic(&run_dir.join("run-binding.json"), &binding)?;
    let binding_sha256 = sha256_file(&run_dir.join("run-binding.json"))?;
    let manifest_by_id: BTreeMap<_, _> = tests
        .into_iter()
        .map(|test| (test.id.clone(), test))
        .collect();
    let mut executions = Vec::new();
    let mut cells = Vec::new();
    for test_id in &selected_tests {
        let test = &manifest_by_id[test_id];
        for backend in &backends {
            for mode in &modes {
                let cell_id = format!("{}--{}--{}", slug(test_id), slug(backend), slug(mode));
                let mut pair = Vec::new();
                for ordinal in [1u8, 2u8] {
                    let artifact_dir = artifacts
                        .join("artifacts")
                        .join(kind)
                        .join(&run_instance)
                        .join(&binding_sha256)
                        .join(&cell_id);
                    fs::create_dir_all(&artifact_dir)?;
                    let stdout = artifact_dir.join(format!("run{ordinal}.stdout"));
                    let stderr = artifact_dir.join(format!("run{ordinal}.stderr"));
                    let log = artifact_dir.join(format!("run{ordinal}.info.log"));
                    let parser_diagnostic =
                        artifact_dir.join(format!("run{ordinal}.log-inspection.stderr"));
                    fs::write(&stdout, b"")?;
                    fs::write(&stderr, b"")?;
                    fs::write(
                        &log,
                        b"2026-08-06T10:00:00.000000Z  INFO detcore: DETLOG fixture value=7\n",
                    )?;
                    if test.nonc_preparation.is_some() {
                        let runtime = artifacts
                            .join("runtime")
                            .join(kind)
                            .join(&run_instance)
                            .join(&binding_sha256)
                            .join(&cell_id)
                            .join(format!("run{ordinal}"));
                        fs::create_dir_all(runtime.join("tmp"))?;
                        fs::create_dir_all(runtime.join("fixtures"))?;
                    }
                    let environment = expected_execution_environment(
                        &paths,
                        &denominator,
                        &binding_sha256,
                        &cell_id,
                        ordinal,
                        test,
                    )?;
                    let inspection = log_authority::inspect_file(&paths.binary, &log)?;
                    fs::write(&parser_diagnostic, &inspection.diagnostic_stderr)?;
                    let mut command = vec![
                        paths.binary.display().to_string(),
                        "--log=info".to_owned(),
                        format!("--log-file={}", log.display()),
                        "--backend".to_owned(),
                        backend.clone(),
                        "run".to_owned(),
                        "--strict".to_owned(),
                        "--no-virtualize-cpuid".to_owned(),
                        "--max-timeslice=disabled".to_owned(),
                    ];
                    match mode.as_str() {
                        "strict_raw" => {}
                        "heap" => command.push("--detlog-heap".to_owned()),
                        "stack" => command.push("--detlog-stack".to_owned()),
                        "heap_stack" => {
                            command.push("--detlog-heap".to_owned());
                            command.push("--detlog-stack".to_owned());
                        }
                        other => bail!("unsupported self-test mode {other}"),
                    }
                    command.push("--".to_owned());
                    command.extend(test.argv.clone());
                    pair.push(ExecutionRecord {
                        schema: SCHEMA,
                        record_type: "strict_metric_execution".to_owned(),
                        run_kind: kind.to_owned(),
                        run_instance: run_instance.clone(),
                        run_binding_sha256: binding_sha256.clone(),
                        cell_id: cell_id.clone(),
                        test_id: test_id.clone(),
                        backend: backend.clone(),
                        observation_mode: mode.clone(),
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
                        log_parser_command_sha256: sha256_bytes(&serde_json::to_vec(
                            &inspection.command,
                        )?),
                        log_parser_command: inspection.command,
                        log_parser_diagnostic: artifact_fact_for(&paths, &parser_diagnostic)?,
                        stdout: artifact_fact_for(&paths, &stdout)?,
                        stderr: artifact_fact_for(&paths, &stderr)?,
                        ordered_log_stream: artifact_fact_for(&paths, &log)?,
                        command_sha256: sha256_bytes(&serde_json::to_vec(&command)?),
                        command,
                        environment_sha256: sha256_bytes(&serde_json::to_vec(&environment)?),
                        environment,
                        preparation_receipt_sha256: test
                            .nonc_preparation
                            .as_ref()
                            .map(|receipt| receipt.receipt_sha256.clone()),
                        guest_binary_sha256: test.binary_sha256.clone(),
                        error: None,
                    });
                }
                let diagnostic = artifacts
                    .join("artifacts")
                    .join(kind)
                    .join(&run_instance)
                    .join(&binding_sha256)
                    .join(&cell_id)
                    .join("legacy-log-diff.stderr");
                fs::write(&diagnostic, b"")?;
                cells.push(CellRecord {
                    schema: SCHEMA,
                    record_type: "strict_metric_cell".to_owned(),
                    run_kind: kind.to_owned(),
                    run_instance: run_instance.clone(),
                    run_binding_sha256: binding_sha256.clone(),
                    cell_id,
                    test_id: test_id.clone(),
                    backend: backend.clone(),
                    observation_mode: mode.clone(),
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
                    legacy_comparator:
                        "hermit log-diff --unsafe-strip-lines (deterministic subset)".to_owned(),
                    legacy_diagnostic: artifact_fact_for(&paths, &diagnostic)?,
                });
                executions.extend(pair);
            }
        }
    }
    write_jsonl_owned(&run_dir.join("executions.jsonl"), &executions)?;
    write_jsonl_owned(&run_dir.join("cells.jsonl"), &cells)?;
    Ok(SelfFixture {
        paths,
        kind: kind.to_owned(),
        run_instance,
        executions,
        cells,
    })
}

fn strict_contract() -> BTreeMap<String, serde_json::Value> {
    BTreeMap::from([
        ("stdout".to_owned(), serde_json::json!("raw_bytes")),
        ("stderr".to_owned(), serde_json::json!("raw_bytes")),
        (
            "termination".to_owned(),
            serde_json::json!("exact_exit_code_or_signal"),
        ),
        (
            "ordered_log_stream".to_owned(),
            serde_json::json!("complete_raw_log_file_bytes"),
        ),
        ("stripped_prefixes".to_owned(), serde_json::json!([])),
        ("canonicalizations".to_owned(), serde_json::json!([])),
        ("filters".to_owned(), serde_json::json!([])),
        (
            "minimum_log_events_per_execution".to_owned(),
            serde_json::json!(1),
        ),
        (
            "log_event_parser".to_owned(),
            serde_json::json!(log_authority::PARSER_ID),
        ),
        ("log_event_level".to_owned(), serde_json::json!("INFO")),
    ])
}

fn artifact_fact_for(paths: &VerifyPaths, path: &Path) -> Result<ArtifactFact> {
    let relative = path.strip_prefix(&paths.artifacts)?;
    Ok(ArtifactFact {
        path: relative.display().to_string(),
        sha256: sha256_file(path)?,
        bytes: fs::metadata(path)?.len(),
    })
}

fn write_jsonl_owned<T: Serialize>(path: &Path, records: &[T]) -> Result<()> {
    let mut file = File::create(path)?;
    for record in records {
        serde_json::to_writer(&mut file, record)?;
        file.write_all(b"\n")?;
    }
    Ok(())
}

fn run_checked(command: &mut Command) -> Result<()> {
    let status = command.status()?;
    if !status.success() {
        bail!("self-test setup command failed: {status}");
    }
    Ok(())
}

fn run_verifier_cli(fixture: &SelfFixture, should_pass: bool, label: &str) -> Result<()> {
    let executable = std::env::current_exe()?;
    let output = Command::new(executable)
        .arg("verify")
        .arg("--kind")
        .arg(&fixture.kind)
        .arg("--run-instance")
        .arg(&fixture.run_instance)
        .arg("--parent")
        .arg(&fixture.paths.parent_repo)
        .arg("--experiment")
        .arg(&fixture.paths.experiment)
        .arg("--artifacts")
        .arg(&fixture.paths.artifacts)
        .arg("--binary")
        .arg(&fixture.paths.binary)
        .arg("--hermit")
        .arg(&fixture.paths.hermit_repo)
        .arg("--reverie")
        .arg(&fixture.paths.reverie_repo)
        .arg("--liteinst2")
        .arg(&fixture.paths.liteinst_repo)
        .arg("--corpus-c")
        .arg(&fixture.paths.corpus_c)
        .arg("--corpus-nonc")
        .arg(&fixture.paths.corpus_nonc)
        .output()?;
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    if should_pass {
        if !output.status.success() {
            bail!("positive normal-CLI fixture refused: {combined}");
        }
    } else if output.status.success() {
        bail!("negative normal-CLI fixture was accepted: {label}");
    } else if !label.is_empty() && !combined.contains(label) {
        bail!("negative fixture {label:?} refused for the wrong reason: {combined}");
    }
    Ok(())
}

fn verify_repo_sha(path: &Path, expected: &str, name: &str) -> Result<()> {
    let output = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(path)
        .output()?;
    let actual = String::from_utf8(output.stdout)?.trim().to_owned();
    if !output.status.success() || actual != expected {
        bail!("{name} checkout SHA mismatch: expected {expected}, found {actual}");
    }
    let status = Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(path)
        .output()?;
    if !status.status.success() || !status.stdout.is_empty() {
        bail!("{name} checkout is dirty during evidence verification");
    }
    Ok(())
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

fn set_difference(left: &BTreeSet<String>, right: &BTreeSet<String>) -> Vec<String> {
    left.difference(right).cloned().collect()
}

fn percent(numerator: usize, denominator: usize) -> f64 {
    if denominator == 0 {
        0.0
    } else {
        numerator as f64 * 100.0 / denominator as f64
    }
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

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    serde_json::from_reader(File::open(path)?)
        .with_context(|| format!("parsing {}", path.display()))
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
    let value = args
        .windows(2)
        .find(|pair| pair[0] == flag)
        .map(|pair| pair[1].clone())
        .with_context(|| format!("{flag} is required"))?;
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
    Ok(value)
}
