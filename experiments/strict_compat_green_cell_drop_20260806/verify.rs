#!/usr/bin/env rust-script
//! ```cargo
//! [dependencies]
//! anyhow = "1"
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! sha2 = "0.10"
//! ```

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

const SCHEMA: u32 = 1;
const BACKENDS: [&str; 6] = ["ptrace", "kvm", "dbi", "sabre", "e9patch", "liteinst"];
const CALIBRATION_TEST: &str = "backend-parity-c/pid-probe";
const SPOT_TESTS: [&str; 2] = ["backend-parity-c/pid-probe", "c-programs/print-memaddrs"];

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
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CellRecord {
    schema: u32,
    record_type: String,
    run_kind: String,
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
    hermit_sha: String,
    reverie_sha: String,
    reverie_dependency_sha: String,
    liteinst2_sha: String,
    liteinst2_dependency_sha: String,
    hermit_binary_sha256: String,
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
    tests: Vec<FrozenTestEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FrozenTestEvidence {
    id: String,
    lane: String,
    argv: Vec<String>,
    binary_sha256: String,
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
    missing_test_count: usize,
    missing_source_count: usize,
    missing_test_ids: Vec<String>,
    missing_sources: Vec<serde_json::Value>,
    duplicate_ids: Vec<String>,
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
}

#[derive(Debug, Clone)]
struct VerifyPaths {
    experiment: PathBuf,
    artifacts: PathBuf,
    binary: PathBuf,
    hermit_repo: PathBuf,
    reverie_repo: PathBuf,
    liteinst_repo: PathBuf,
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("help");
    match command {
        "verify" => {
            let paths = VerifyPaths::from_args(&args[1..]);
            let kind = value_string(&args[1..], "--kind", "full");
            let summary = verify(&paths, &kind, true)?;
            write_outputs(&paths, &kind, &summary)?;
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
            println!("verify --kind full|calibration|spot; self-test");
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
            hermit_repo,
        }
    }
}

fn verify(
    paths: &VerifyPaths,
    kind: &str,
    verify_environment: bool,
) -> Result<VerificationSummary> {
    let denominator_path = paths.experiment.join(if kind == "full" {
        "denominator.json".to_owned()
    } else {
        format!("denominator-{kind}.json")
    });
    let executions_path = paths.experiment.join(if kind == "full" {
        "executions.jsonl".to_owned()
    } else {
        format!("executions-{kind}.jsonl")
    });
    let cells_path = paths.experiment.join(if kind == "full" {
        "cells.jsonl".to_owned()
    } else {
        format!("cells-{kind}.jsonl")
    });
    let denominator: Denominator = read_json(&denominator_path)?;
    if denominator.schema != SCHEMA
        || denominator.record_type != "strict_metric_denominator"
        || denominator.run_kind != kind
    {
        bail!("invalid denominator type/schema/kind");
    }
    verify_contract(&denominator)?;
    verify_axes(&denominator, kind)?;
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
    if verify_environment {
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
    for (key, execution) in &executions {
        if execution.schema != SCHEMA
            || execution.record_type != "strict_metric_execution"
            || execution.run_kind != kind
        {
            bail!("invalid execution record at {key}");
        }
        if !execution.attempted {
            bail!("execution was recorded but never attempted: {key}");
        }
        verify_execution_identity(paths, &denominator, &manifest, key, execution, kind)?;
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
        ] {
            verify_artifact(paths, fact)?;
            artifact_hashes_verified += 1;
        }
        let log_path = resolve_artifact(paths, &execution.ordered_log_stream.path)?;
        let actual_events = count_raw_log_events(&log_path)?;
        if actual_events != execution.log_event_count {
            bail!(
                "log event count mismatch for {key}: record={} actual={actual_events}",
                execution.log_event_count
            );
        }
    }

    let mut legacy_green_cells = 0;
    let mut strict_green_cells = 0;
    let mut mismatch_components: BTreeMap<String, usize> = BTreeMap::new();
    let mut by_backend_counts: BTreeMap<String, (usize, usize, usize)> = BTreeMap::new();
    for (cell_key, cell) in &cells {
        if cell.schema != SCHEMA
            || cell.record_type != "strict_metric_cell"
            || cell.run_kind != kind
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
        if producer_tuple != verified_tuple {
            bail!("producer cell verdict disagrees with dereferenced evidence: {cell_key}");
        }
        if cell.strict_green && !nonzero_log_events {
            bail!("zero-message cell claimed strict green: {cell_key}");
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
    if kind == "selftest" {
        return Ok(());
    }

    let expected_backends: Vec<_> = BACKENDS.iter().map(|value| (*value).to_owned()).collect();
    if denominator.backends != expected_backends {
        bail!("backend axis is not the exact six-path contract");
    }
    let (expected_tests, expected_modes, expected_cells, expected_executions) = match kind {
        "full" => (
            None,
            vec!["strict_raw".to_owned()],
            235usize * 6,
            235usize * 6 * 2,
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
        other => bail!("unknown run kind {other:?}"),
    };
    if kind == "full" && unique_tests.len() != 235 {
        bail!("full denominator must contain exactly 235 unique tests");
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

fn verify_input_audit(paths: &VerifyPaths, denominator: &Denominator, kind: &str) -> Result<()> {
    let audit_path = paths.experiment.join(&denominator.input_audit_path);
    if sha256_file(&audit_path)? != denominator.input_audit_sha256 {
        bail!("input audit hash does not match denominator");
    }
    let audit: InputAuditEvidence = read_json(&audit_path)?;
    let expected_tests = if kind == "selftest" { 1 } else { 235 };
    if audit.schema != SCHEMA
        || audit.record_type != "strict_metric_input_audit"
        || !audit.complete
        || !audit.full_sweep_allowed
        || audit.denominator_expected_tests != expected_tests
        || audit.denominator_observed_rows != expected_tests
        || audit.denominator_unique_ids != expected_tests
        || audit.denominator_executable_tests != expected_tests
        || audit.missing_test_count != 0
        || audit.missing_source_count != 0
        || !audit.missing_test_ids.is_empty()
        || !audit.missing_sources.is_empty()
        || !audit.duplicate_ids.is_empty()
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

    let requested_path = paths.experiment.join(&denominator.requested_manifest_path);
    if sha256_file(&requested_path)? != denominator.requested_manifest_sha256 {
        bail!("requested manifest hash does not match denominator");
    }
    let requested: serde_json::Value = read_json(&requested_path)?;
    let inputs = requested
        .get("inputs")
        .and_then(serde_json::Value::as_array)
        .context("requested manifest has no typed inputs array")?;
    if requested.get("schema").and_then(serde_json::Value::as_u64) != Some(SCHEMA as u64)
        || requested
            .get("record_type")
            .and_then(serde_json::Value::as_str)
            != Some("strict_metric_requested_manifest")
        || requested
            .get("denominator_expected_tests")
            .and_then(serde_json::Value::as_u64)
            != Some(expected_tests as u64)
        || inputs.len() != expected_tests
    {
        bail!("requested manifest type/count is invalid");
    }
    Ok(())
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
    if denominator_binding != manifest_binding {
        bail!("denominator facts disagree with dereferenced frozen manifest");
    }
    if sha256_file(&paths.experiment.join("frozen-corpus-c.tsv"))? != manifest.corpus_c_sha256
        || sha256_file(&paths.experiment.join("frozen-corpus-nonc.tsv"))?
            != manifest.corpus_nonc_sha256
    {
        bail!("frozen corpus bytes disagree with manifest hashes");
    }

    let mut tests = BTreeMap::new();
    for test in manifest.tests {
        if test.argv.is_empty() {
            bail!("manifest test has empty argv: {}", test.id);
        }
        let binary = Path::new(&test.argv[0]);
        if !binary.is_file() || sha256_file(binary)? != test.binary_sha256 {
            bail!("manifest guest binary missing or changed: {}", test.id);
        }
        if tests.insert(test.id.clone(), test).is_some() {
            bail!("duplicate test identity in frozen manifest");
        }
    }
    if kind != "selftest" && tests.len() != 235 {
        bail!("frozen manifest must contain exactly 235 unique tests");
    }
    let denominator_tests: BTreeSet<_> = denominator.tests.iter().cloned().collect();
    let manifest_tests: BTreeSet<_> = tests.keys().cloned().collect();
    if !denominator_tests.is_subset(&manifest_tests)
        || (kind == "full" && denominator_tests != manifest_tests)
    {
        bail!("denominator test axis is not bound to the frozen manifest");
    }
    Ok(tests)
}

fn verify_execution_identity(
    paths: &VerifyPaths,
    denominator: &Denominator,
    manifest: &BTreeMap<String, FrozenTestEvidence>,
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
    if sha256_bytes(&serde_json::to_vec(&execution.command)?) != execution.command_sha256 {
        bail!("execution command hash mismatch: {key}");
    }
    if kind == "selftest" {
        return Ok(());
    }
    let test = manifest
        .get(&execution.test_id)
        .with_context(|| format!("execution test absent from manifest: {key}"))?;
    let log_path = resolve_artifact(paths, &execution.ordered_log_stream.path)?;
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
    require("minimum_log_events_per_execution", serde_json::json!(1))
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

fn count_raw_log_events(path: &Path) -> Result<u64> {
    let mut count = 0;
    for line in BufReader::new(File::open(path)?).split(b'\n') {
        let line = line?;
        if line.len() >= 20
            && line[0..4].iter().all(u8::is_ascii_digit)
            && line.get(4) == Some(&b'-')
            && line.get(7) == Some(&b'-')
            && line.get(10) == Some(&b'T')
        {
            count += 1;
        }
    }
    Ok(count)
}

fn successful(termination: &Termination) -> bool {
    termination.kind == "exit" && termination.code == Some(0) && !termination.timed_out
}

fn write_outputs(paths: &VerifyPaths, kind: &str, summary: &VerificationSummary) -> Result<()> {
    let summary_name = if kind == "full" {
        "summary.json".to_owned()
    } else {
        format!("summary-{kind}.json")
    };
    write_json_atomic(&paths.experiment.join(summary_name), summary)?;
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
    fs::write(paths.experiment.join("REPORT.md"), report)?;
    Ok(())
}

fn self_test(default_paths: &VerifyPaths) -> Result<()> {
    let root = std::env::temp_dir().join(format!(
        "strict-metric-verifier-selftest-{}",
        std::process::id()
    ));
    if root.exists() {
        bail!("self-test path unexpectedly exists: {}", root.display());
    }
    let experiment = root.join("experiment");
    let artifacts = root.join("artifacts");
    fs::create_dir_all(&experiment)?;
    fs::create_dir_all(&artifacts)?;
    let stdout1 = artifacts.join("run1.stdout");
    let stdout2 = artifacts.join("run2.stdout");
    let stderr1 = artifacts.join("run1.stderr");
    let stderr2 = artifacts.join("run2.stderr");
    let log1 = artifacts.join("run1.info.log");
    let log2 = artifacts.join("run2.info.log");
    let diagnostic = artifacts.join("legacy.stderr");
    for path in [&stdout1, &stdout2, &stderr1, &stderr2, &diagnostic] {
        fs::write(path, b"")?;
    }
    let log = b"2026-08-06T10:00:00.000000Z  INFO detcore: DETLOG fixture value=7\n";
    fs::write(&log1, log)?;
    fs::write(&log2, log)?;
    let binary_hash = sha256_file(&default_paths.binary)?;
    fs::write(experiment.join("frozen-corpus-c.tsv"), b"fixture-c\n")?;
    fs::write(experiment.join("frozen-corpus-nonc.tsv"), b"fixture-nonc\n")?;
    let manifest = FrozenManifestEvidence {
        schema: SCHEMA,
        record_type: "strict_metric_frozen_manifest".to_owned(),
        hermit_sha: "0".repeat(40),
        reverie_sha: "1".repeat(40),
        reverie_dependency_sha: "1".repeat(40),
        liteinst2_sha: "2".repeat(40),
        liteinst2_dependency_sha: "6".repeat(40),
        hermit_binary_sha256: binary_hash.clone(),
        corpus_c_sha256: sha256_file(&experiment.join("frozen-corpus-c.tsv"))?,
        corpus_nonc_sha256: sha256_file(&experiment.join("frozen-corpus-nonc.tsv"))?,
        tests: vec![FrozenTestEvidence {
            id: "fixture/test".to_owned(),
            lane: "portable".to_owned(),
            argv: vec![default_paths.binary.display().to_string()],
            binary_sha256: binary_hash.clone(),
        }],
    };
    write_json_atomic(&experiment.join("manifest.json"), &manifest)?;
    let requested = serde_json::json!({
        "schema": SCHEMA,
        "record_type": "strict_metric_requested_manifest",
        "denominator_expected_tests": 1,
        "inputs": [{"id": "fixture/test"}],
    });
    write_json_atomic(&experiment.join("requested-manifest.json"), &requested)?;
    let manifest_sha256 = sha256_file(&experiment.join("manifest.json"))?;
    let requested_manifest_sha256 = sha256_file(&experiment.join("requested-manifest.json"))?;
    let input_audit = InputAuditEvidence {
        schema: SCHEMA,
        record_type: "strict_metric_input_audit".to_owned(),
        complete: true,
        full_sweep_allowed: true,
        denominator_expected_tests: 1,
        denominator_observed_rows: 1,
        denominator_unique_ids: 1,
        denominator_executable_tests: 1,
        missing_test_count: 0,
        missing_source_count: 0,
        missing_test_ids: Vec::new(),
        missing_sources: Vec::new(),
        duplicate_ids: Vec::new(),
        requested_manifest_path: "requested-manifest.json".to_owned(),
        requested_manifest_sha256: requested_manifest_sha256.clone(),
        executable_manifest_path: Some("manifest.json".to_owned()),
        executable_manifest_sha256: Some(manifest_sha256.clone()),
        hermit_sha: manifest.hermit_sha.clone(),
        reverie_sha: manifest.reverie_sha.clone(),
        reverie_dependency_sha: manifest.reverie_dependency_sha.clone(),
        liteinst2_sha: manifest.liteinst2_sha.clone(),
        liteinst2_dependency_sha: manifest.liteinst2_dependency_sha.clone(),
        hermit_binary_sha256: manifest.hermit_binary_sha256.clone(),
        corpus_c_sha256: manifest.corpus_c_sha256.clone(),
        corpus_nonc_sha256: manifest.corpus_nonc_sha256.clone(),
    };
    write_json_atomic(&experiment.join("input-audit.json"), &input_audit)?;
    let mut contract = BTreeMap::new();
    contract.insert("stdout".to_owned(), serde_json::json!("raw_bytes"));
    contract.insert("stderr".to_owned(), serde_json::json!("raw_bytes"));
    contract.insert(
        "termination".to_owned(),
        serde_json::json!("exact_exit_code_or_signal"),
    );
    contract.insert(
        "ordered_log_stream".to_owned(),
        serde_json::json!("complete_raw_log_file_bytes"),
    );
    contract.insert("stripped_prefixes".to_owned(), serde_json::json!([]));
    contract.insert("canonicalizations".to_owned(), serde_json::json!([]));
    contract.insert("filters".to_owned(), serde_json::json!([]));
    contract.insert(
        "minimum_log_events_per_execution".to_owned(),
        serde_json::json!(1),
    );
    let denominator = Denominator {
        schema: SCHEMA,
        record_type: "strict_metric_denominator".to_owned(),
        run_kind: "selftest".to_owned(),
        hermit_sha: "0".repeat(40),
        reverie_sha: "1".repeat(40),
        reverie_dependency_sha: "1".repeat(40),
        liteinst2_sha: "2".repeat(40),
        liteinst2_dependency_sha: "6".repeat(40),
        hermit_binary_sha256: binary_hash,
        input_audit_path: "input-audit.json".to_owned(),
        input_audit_sha256: sha256_file(&experiment.join("input-audit.json"))?,
        requested_manifest_path: "requested-manifest.json".to_owned(),
        requested_manifest_sha256,
        manifest_path: "manifest.json".to_owned(),
        manifest_sha256,
        corpus_c_sha256: manifest.corpus_c_sha256.clone(),
        corpus_nonc_sha256: manifest.corpus_nonc_sha256.clone(),
        tests: vec!["fixture/test".to_owned()],
        backends: vec!["ptrace".to_owned()],
        observation_modes: vec!["strict_raw".to_owned()],
        run_ordinals: vec![1, 2],
        expected_cells: 1,
        expected_executions: 2,
        comparison_contract: contract,
    };
    write_json_atomic(&experiment.join("denominator-selftest.json"), &denominator)?;
    let cell_id = "fixture-test--ptrace--strict-raw";
    let make_fact = |path: &Path| -> Result<ArtifactFact> {
        Ok(ArtifactFact {
            path: path.strip_prefix(&artifacts)?.display().to_string(),
            sha256: sha256_file(path)?,
            bytes: fs::metadata(path)?.len(),
        })
    };
    let termination = Termination {
        kind: "exit".to_owned(),
        code: Some(0),
        signal: None,
        timed_out: false,
    };
    let make_execution =
        |ordinal, stdout: &Path, stderr: &Path, log: &Path| -> Result<ExecutionRecord> {
            let command = vec!["selftest-command".to_owned()];
            Ok(ExecutionRecord {
                schema: SCHEMA,
                record_type: "strict_metric_execution".to_owned(),
                run_kind: "selftest".to_owned(),
                cell_id: cell_id.to_owned(),
                test_id: "fixture/test".to_owned(),
                backend: "ptrace".to_owned(),
                observation_mode: "strict_raw".to_owned(),
                ordinal,
                attempted: true,
                termination: termination.clone(),
                duration_ms: 1,
                log_event_count: 1,
                stdout: make_fact(stdout)?,
                stderr: make_fact(stderr)?,
                ordered_log_stream: make_fact(log)?,
                command_sha256: sha256_bytes(&serde_json::to_vec(&command)?),
                command,
                error: None,
            })
        };
    let one = make_execution(1, &stdout1, &stderr1, &log1)?;
    let two = make_execution(2, &stdout2, &stderr2, &log2)?;
    let cell = CellRecord {
        schema: SCHEMA,
        record_type: "strict_metric_cell".to_owned(),
        run_kind: "selftest".to_owned(),
        cell_id: cell_id.to_owned(),
        test_id: "fixture/test".to_owned(),
        backend: "ptrace".to_owned(),
        observation_mode: "strict_raw".to_owned(),
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
        legacy_comparator: "hermit log-diff --unsafe-strip-lines (deterministic subset)".to_owned(),
        legacy_diagnostic: make_fact(&diagnostic)?,
    };
    write_jsonl(&experiment.join("executions-selftest.jsonl"), &[&one, &two])?;
    write_jsonl(&experiment.join("cells-selftest.jsonl"), &[&cell])?;
    let fixture_paths = VerifyPaths {
        experiment: experiment.clone(),
        artifacts: artifacts.clone(),
        binary: default_paths.binary.clone(),
        hermit_repo: default_paths.hermit_repo.clone(),
        reverie_repo: default_paths.reverie_repo.clone(),
        liteinst_repo: default_paths.liteinst_repo.clone(),
    };
    verify(&fixture_paths, "selftest", false).context("positive fixture refused")?;
    let original_exec = fs::read(experiment.join("executions-selftest.jsonl"))?;

    let first_line = original_exec.split(|b| *b == b'\n').next().unwrap();
    fs::write(
        experiment.join("executions-selftest.jsonl"),
        [first_line, b"\n"].concat(),
    )?;
    expect_refusal(
        || verify(&fixture_paths, "selftest", false),
        "missing execution",
    )?;

    fs::write(
        experiment.join("executions-selftest.jsonl"),
        [original_exec.as_slice(), first_line, b"\n"].concat(),
    )?;
    expect_refusal(
        || verify(&fixture_paths, "selftest", false),
        "duplicate execution",
    )?;

    fs::write(experiment.join("executions-selftest.jsonl"), &original_exec)?;
    fs::write(&stdout1, b"tampered")?;
    expect_refusal(
        || verify(&fixture_paths, "selftest", false),
        "tampered artifact",
    )?;
    fs::write(&stdout1, b"")?;

    let mut zero = one.clone();
    zero.log_event_count = 0;
    write_jsonl(
        &experiment.join("executions-selftest.jsonl"),
        &[&zero, &two],
    )?;
    expect_refusal(
        || verify(&fixture_paths, "selftest", false),
        "false zero-message fact",
    )?;

    fs::remove_dir_all(&root)?;
    println!("SELF-TEST PASS: positive=1 accepted, negatives=4 refused (missing, duplicate, tampered, zero-message)");
    Ok(())
}

fn expect_refusal<F>(operation: F, label: &str) -> Result<()>
where
    F: FnOnce() -> Result<VerificationSummary>,
{
    if operation().is_ok() {
        bail!("negative fixture was accepted: {label}");
    }
    Ok(())
}

fn write_jsonl<T: Serialize>(path: &Path, records: &[&T]) -> Result<()> {
    let mut file = File::create(path)?;
    for record in records {
        serde_json::to_writer(&mut file, record)?;
        file.write_all(b"\n")?;
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
