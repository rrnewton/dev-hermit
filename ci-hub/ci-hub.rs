#!/usr/bin/env rust-script
//! Typed front door for dev-hermit CI state and operations.
//!
//! ```cargo
//! [dependencies]
//! chrono = "0.4"
//! clap = { version = "4", features = ["derive"] }
//! fs2 = "0.4"
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! thiserror = "2"
//! ```

#[path = "lib/landing_lock.rs"]
mod landing_lock;
#[path = "lib/records.rs"]
mod records;

use clap::error::ErrorKind;
use clap::{Args, Parser, Subcommand, ValueEnum};
use fs2::FileExt;
use records::{HistoryRow, ObligationRecord};
use serde::Serialize;
use std::collections::BTreeMap;
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, ExitStatus, Stdio};
use thiserror::Error;

const DEFAULT_MAIN_RUN_LIMIT: usize = 100;
const DEFAULT_WARN_THRESHOLD: usize = 10;
const DEFAULT_GITHUB_WAIT_SECONDS: u64 = 120;
const DEFAULT_POLL_SECONDS: u64 = 15;

#[derive(Parser, Debug)]
#[command(
    name = "ci-hub",
    about = "Typed front door for dev-hermit CI state and operations",
    version,
    propagate_version = true
)]
struct Cli {
    #[command(subcommand)]
    command: HubCommand,
}

#[derive(Subcommand, Debug)]
enum HubCommand {
    /// Summarize current-main, open-PR, and speculative-land health.
    Health(HealthArgs),
    /// Query current-main GitHub workflow health.
    MainHealth(MainHealthArgs),
    /// Pull fresh open-PR CI status via pinned agent-utils.
    #[command(visible_alias = "fresh")]
    PrStatus(PrStatusArgs),
    /// Run due operational-health gates.
    Tick(PassthroughArgs),
    /// Arm local and GitHub verification for a just-landed SHA.
    ArmLand(ArmLandArgs),
    /// Show speculative-land obligations from the typed JSONL store.
    Obligations(ObligationsArgs),
    /// Poll open obligations and record verifier transitions.
    WatchObligations(WatchObligationsArgs),
    /// Record a completed fix-forward or revert.
    ResolveObligation(ResolveObligationArgs),
    /// Incrementally ingest GitHub and local CI history.
    RefreshHistory(RefreshHistoryArgs),
    /// Query the local commit/CI history store.
    History(PassthroughArgs),
    /// Query legacy and machine-wide validate-run records.
    LocalHistory(LocalHistoryArgs),
    /// Summarize self-hosted runners and recent workflows.
    RunnerHealth(RunnerHealthArgs),
    /// Operate the shared-file landing mutex.
    LandLock(landing_lock::LandLockArgs),
}

#[derive(Args, Clone, Debug)]
struct HealthArgs {
    #[arg(long = "repo")]
    repos: Vec<String>,
    #[arg(long, default_value_t = DEFAULT_MAIN_RUN_LIMIT)]
    limit: usize,
    #[arg(long, default_value_t = DEFAULT_WARN_THRESHOLD)]
    warn_threshold: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct MainHealthArgs {
    #[arg(long = "repo")]
    repos: Vec<String>,
    #[arg(long, default_value_t = DEFAULT_MAIN_RUN_LIMIT)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct PrStatusArgs {
    #[arg(long = "repo")]
    repos: Vec<String>,
    #[arg(long, default_value_t = DEFAULT_WARN_THRESHOLD)]
    warn_threshold: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct PassthroughArgs {
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    args: Vec<OsString>,
}

#[derive(Clone, Debug, ValueEnum)]
enum LandMode {
    Admin,
    Speculative,
}

impl LandMode {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Admin => "admin",
            Self::Speculative => "speculative",
        }
    }
}

#[derive(Args, Clone, Debug)]
struct ArmLandArgs {
    sha: String,
    #[arg(long, default_value = "rrnewton/hermit")]
    repo: String,
    #[arg(long)]
    source: Option<PathBuf>,
    #[arg(long, value_enum, default_value_t = LandMode::Speculative)]
    land_mode: LandMode,
    #[arg(long)]
    actor: Option<String>,
    #[arg(long, default_value_t = DEFAULT_GITHUB_WAIT_SECONDS)]
    github_wait_seconds: u64,
    #[arg(long, default_value_t = DEFAULT_POLL_SECONDS)]
    poll_seconds: u64,
    #[arg(long)]
    no_dispatch: bool,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Args, Clone, Debug)]
struct ObligationsArgs {
    #[arg(long)]
    all: bool,
    #[arg(long)]
    json: bool,
    #[arg(long)]
    gate: bool,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Args, Clone, Debug)]
struct WatchObligationsArgs {
    #[arg(long)]
    id: Option<String>,
    #[arg(long)]
    once: bool,
    #[arg(long)]
    gate: bool,
    #[arg(long, default_value_t = DEFAULT_POLL_SECONDS)]
    poll_seconds: u64,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Clone, Debug, ValueEnum)]
enum RemediationKind {
    FixForward,
    Revert,
}

impl RemediationKind {
    fn as_str(&self) -> &'static str {
        match self {
            Self::FixForward => "fix-forward",
            Self::Revert => "revert",
        }
    }
}

#[derive(Args, Clone, Debug)]
struct ResolveObligationArgs {
    id: String,
    #[arg(long, value_enum)]
    kind: RemediationKind,
    #[arg(long = "ref")]
    reference: String,
    #[arg(long)]
    started_at: Option<String>,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Args, Clone, Debug)]
struct RefreshHistoryArgs {
    #[arg(long)]
    full: bool,
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    extra: Vec<OsString>,
}

#[derive(Args, Clone, Debug)]
struct LocalHistoryArgs {
    #[arg(long)]
    json: bool,
    #[arg(long)]
    csv: Option<PathBuf>,
    #[arg(long)]
    write_global: bool,
    #[arg(long)]
    since: Option<String>,
    #[arg(long)]
    slot: Option<String>,
    #[arg(long)]
    profiling: bool,
}

#[derive(Args, Clone, Debug)]
struct RunnerHealthArgs {
    #[arg(long, default_value = "rrnewton/hermit")]
    repo: String,
    #[arg(long)]
    all: bool,
    #[arg(long, default_value_t = DEFAULT_MAIN_RUN_LIMIT)]
    limit: usize,
    #[arg(long, default_value_t = 15)]
    sample: usize,
    #[arg(long)]
    gate: bool,
    #[arg(long)]
    gh: Option<String>,
}

#[derive(Clone, Debug)]
struct CostSpec {
    tool: &'static str,
    basis: String,
}

impl HubCommand {
    fn cost_spec(&self) -> Option<CostSpec> {
        let spec = match self {
            Self::Health(_) => CostSpec {
                tool: "ci-hub/health",
                basis: "not measured: composite repository/API query cost has no retained history"
                    .into(),
            },
            Self::MainHealth(args) => CostSpec {
                tool: "ci-hub/main-health",
                basis: format!(
                    "not measured: repo_count={}; no retained commit/workflow-query cost history",
                    if args.repos.is_empty() { 3 } else { args.repos.len() }
                ),
            },
            Self::PrStatus(args) => CostSpec {
                tool: "ci-hub/pr-status",
                basis: format!(
                    "not measured: repo_count={}; no retained planner/GitHub-query cost history",
                    if args.repos.is_empty() { 2 } else { args.repos.len() }
                ),
            },
            Self::Tick(_) => CostSpec {
                tool: "ci-hub/tick",
                basis: "not measured: due health gates vary and tick cost history is not retained"
                    .into(),
            },
            Self::ArmLand(_) => CostSpec {
                tool: "ci-hub/arm-land",
                basis: "not measured: verifier launch and GitHub dispatch cost history is not retained"
                    .into(),
            },
            Self::WatchObligations(args) => CostSpec {
                tool: "ci-hub/watch-obligations",
                basis: if args.once {
                    "not measured: one exact-SHA obligation poll has no retained cost history"
                        .into()
                } else {
                    "not measured: verifier completion time is unknown; polling bounds are not estimates"
                        .into()
                },
            },
            Self::RefreshHistory(args) => CostSpec {
                tool: "ci-hub/refresh-history",
                basis: if args.full {
                    "not measured: full GitHub Actions backfill size and cost are unknown before query"
                        .into()
                } else {
                    "not measured: incremental history scan cost is not retained".into()
                },
            },
            Self::History(_) => CostSpec {
                tool: "ci-hub/history",
                basis: "not measured: history-store scan cost is not retained".into(),
            },
            Self::LocalHistory(_) => CostSpec {
                tool: "ci-hub/local-history",
                basis: "not measured: ledger/store scan cost history is not retained".into(),
            },
            Self::RunnerHealth(_) => CostSpec {
                tool: "ci-hub/runner-health",
                basis: "not measured: runner/workflow query cost history is not retained".into(),
            },
            Self::LandLock(args) if args.command.consumes_meaningful_time() => CostSpec {
                tool: "ci-hub/land-lock",
                basis: "not measured: queue wait and optional child command vary; wait/lease values are bounds, not estimates"
                    .into(),
            },
            Self::Obligations(_)
            | Self::ResolveObligation(_)
            | Self::LandLock(_) => return None,
        };
        Some(spec)
    }
}

#[derive(Debug, Error)]
enum CiHubError {
    #[error("ci-hub: cannot locate workspace from {0}")]
    Workspace(PathBuf),
    #[error("ci-hub: failed to launch {tool}: {source}")]
    Launch {
        tool: String,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: cannot open obligation store {path}: {source}")]
    ObligationOpen {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: cannot read obligation store {path}: {source}")]
    ObligationRead {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: obligation store {path} line {line}: {source}")]
    ObligationJson {
        path: PathBuf,
        line: usize,
        #[source]
        source: serde_json::Error,
    },
    #[error("ci-hub: obligation store {path} line {line} uses schema {schema}, expected 1")]
    ObligationSchema {
        path: PathBuf,
        line: usize,
        schema: u32,
    },
    #[error("ci-hub: local-history --json returned an invalid typed history row: {0}")]
    HistoryJson(#[source] serde_json::Error),
    #[error(transparent)]
    LandingLock(#[from] landing_lock::LandLockError),
}

impl CiHubError {
    fn exit_code(&self) -> i32 {
        match self {
            Self::LandingLock(error) => error.exit_code(),
            _ => 2,
        }
    }
}

fn main() -> ExitCode {
    let mut raw: Vec<OsString> = env::args_os().collect();
    if raw.len() == 1 {
        raw.push("--help".into());
    }
    let cli = match Cli::try_parse_from(raw.clone()) {
        Ok(cli) => cli,
        Err(error) => {
            let code = match error.kind() {
                ErrorKind::DisplayHelp | ErrorKind::DisplayVersion => 0,
                _ => 2,
            };
            let _ = error.print();
            return to_exit_code(code);
        }
    };
    let root = match workspace_root() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("{error}");
            return to_exit_code(error.exit_code());
        }
    };

    if env::var_os("CI_HUB_TOOL_COST_ACTIVE").is_none() {
        if let Some(spec) = cli.command.cost_spec() {
            match run_costed(&root, &raw[1..], spec) {
                Ok(code) => return to_exit_code(code),
                Err(error) => {
                    eprintln!("{error}");
                    return to_exit_code(error.exit_code());
                }
            }
        }
    }

    match execute(&root, cli.command) {
        Ok(code) => to_exit_code(code),
        Err(error) => {
            eprintln!("{error}");
            to_exit_code(error.exit_code())
        }
    }
}

fn workspace_root() -> Result<PathBuf, CiHubError> {
    let source = env::var_os("RUST_SCRIPT_PATH")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| env::current_exe().ok())
        .ok_or_else(|| CiHubError::Workspace(PathBuf::from("ci-hub.rs")))?;
    let start = source
        .parent()
        .ok_or_else(|| CiHubError::Workspace(source.clone()))?;
    let output = Command::new("git")
        .arg("-C")
        .arg(start)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .map_err(|source_error| CiHubError::Launch {
            tool: "git rev-parse".into(),
            source: source_error,
        })?;
    if !output.status.success() {
        return Err(CiHubError::Workspace(start.to_path_buf()));
    }
    Ok(PathBuf::from(
        String::from_utf8_lossy(&output.stdout).trim(),
    ))
}

fn run_costed(root: &Path, original_args: &[OsString], spec: CostSpec) -> Result<i32, CiHubError> {
    let executable = env::current_exe().map_err(|source| CiHubError::Launch {
        tool: "current ci-hub executable".into(),
        source,
    })?;
    let mut command = Command::new(root.join("ci-hub/bin/tool-cost"));
    command
        .env("CI_HUB_TOOL_COST_ACTIVE", "1")
        .arg("--tool")
        .arg(spec.tool)
        .arg("--estimate-unknown")
        .arg("--basis")
        .arg(spec.basis)
        .arg("--")
        .arg(executable)
        .args(original_args);
    run_status(command, "tool-cost")
}

fn execute(root: &Path, command: HubCommand) -> Result<i32, CiHubError> {
    match command {
        HubCommand::Health(args) => {
            let obligation_code = print_obligations(
                root,
                ObligationsArgs {
                    all: false,
                    json: false,
                    gate: false,
                    store: None,
                },
            )?;
            let main_code = run_python(
                root,
                "ci-hub/health/github_main_health.py",
                main_health_arguments(&MainHealthArgs {
                    repos: args.repos.clone(),
                    limit: args.limit,
                    json: args.json,
                }),
            )?;
            let pr_code = run_python(
                root,
                "ci-hub/health/pr_status.py",
                pr_status_arguments(&PrStatusArgs {
                    repos: args.repos,
                    warn_threshold: args.warn_threshold,
                    json: args.json,
                }),
            )?;
            Ok(if obligation_code != 0 {
                obligation_code
            } else if main_code != 0 {
                main_code
            } else {
                pr_code
            })
        }
        HubCommand::MainHealth(args) => run_python(
            root,
            "ci-hub/health/github_main_health.py",
            main_health_arguments(&args),
        ),
        HubCommand::PrStatus(args) => run_python(
            root,
            "ci-hub/health/pr_status.py",
            pr_status_arguments(&args),
        ),
        HubCommand::Tick(args) => {
            let mut command = Command::new(root.join("ci-hub/bin/agent-tool"));
            command
                .current_dir(root)
                .args(["tick-hub", "tick", "--config"])
                .arg(root.join("ci-hub/health/tick-hub.yaml"))
                .arg("--state")
                .arg(root.join("ci-hub/health/tick-hub-state.yaml"))
                .arg("--fired-state")
                .arg(root.join(".tick-hub/fired-state"))
                .args(["--current-tick-min", "5"])
                .args(args.args);
            run_status(command, "tick-hub")
        }
        HubCommand::ArmLand(args) => {
            let mut protocol_args = vec![OsString::from("arm"), OsString::from(args.sha)];
            push_option(&mut protocol_args, "--repo", args.repo);
            push_option(
                &mut protocol_args,
                "--source",
                args.source.unwrap_or_else(|| root.join("hermit")),
            );
            push_option(&mut protocol_args, "--land-mode", args.land_mode.as_str());
            push_option(
                &mut protocol_args,
                "--actor",
                args.actor.unwrap_or_else(default_actor),
            );
            push_option(
                &mut protocol_args,
                "--github-wait-seconds",
                args.github_wait_seconds.to_string(),
            );
            push_option(
                &mut protocol_args,
                "--poll-seconds",
                args.poll_seconds.to_string(),
            );
            if args.no_dispatch {
                protocol_args.push("--no-dispatch".into());
            }
            if let Some(store) = args.store {
                push_option(&mut protocol_args, "--store", store);
            }
            run_python(root, "ci-hub/remediation/protocol.py", protocol_args)
        }
        HubCommand::Obligations(args) => print_obligations(root, args),
        HubCommand::WatchObligations(args) => {
            let mut protocol_args = vec![OsString::from("watch")];
            if let Some(id) = args.id {
                push_option(&mut protocol_args, "--id", id);
            }
            if args.once {
                protocol_args.push("--once".into());
            }
            if args.gate {
                protocol_args.push("--gate".into());
            }
            push_option(
                &mut protocol_args,
                "--poll-seconds",
                args.poll_seconds.to_string(),
            );
            if let Some(store) = args.store {
                push_option(&mut protocol_args, "--store", store);
            }
            run_python(root, "ci-hub/remediation/protocol.py", protocol_args)
        }
        HubCommand::ResolveObligation(args) => {
            let mut protocol_args = vec![OsString::from("resolve"), OsString::from(args.id)];
            push_option(&mut protocol_args, "--kind", args.kind.as_str());
            push_option(&mut protocol_args, "--ref", args.reference);
            if let Some(started_at) = args.started_at {
                push_option(&mut protocol_args, "--started-at", started_at);
            }
            if let Some(store) = args.store {
                push_option(&mut protocol_args, "--store", store);
            }
            run_python(root, "ci-hub/remediation/protocol.py", protocol_args)
        }
        HubCommand::RefreshHistory(args) => {
            let ingester = root.join("ci-hub/history/ingest.py");
            let mut forwarded = args.extra;
            if args.full {
                forwarded.insert(0, "--full".into());
            }
            if ingester.is_file() {
                run_python_path(&ingester, forwarded)
            } else {
                eprintln!("ci-hub: unified ingester pending; refreshing local validate history");
                forwarded.insert(0, "--write-global".into());
                run_python(root, "ci-hub/validate/aggregate.py", forwarded)
            }
        }
        HubCommand::History(args) => {
            let query = root.join("ci-hub/history/query.py");
            if query.is_file() {
                run_python_path(&query, args.args)
            } else {
                run_python(root, "ci-hub/validate/aggregate.py", args.args)
            }
        }
        HubCommand::LocalHistory(args) => run_local_history(root, args),
        HubCommand::RunnerHealth(args) => {
            let mut forwarded = Vec::new();
            push_option(&mut forwarded, "--repo", args.repo);
            if args.all {
                forwarded.push("--all".into());
            }
            push_option(&mut forwarded, "--limit", args.limit.to_string());
            push_option(&mut forwarded, "--sample", args.sample.to_string());
            if args.gate {
                forwarded.push("--gate".into());
            }
            if let Some(gh) = args.gh {
                push_option(&mut forwarded, "--gh", gh);
            }
            run_python(root, "ci-hub/runners/ci-status.py", forwarded)
        }
        HubCommand::LandLock(args) => landing_lock::execute(root, args).map_err(Into::into),
    }
}

fn main_health_arguments(args: &MainHealthArgs) -> Vec<OsString> {
    let mut forwarded = Vec::new();
    for repo in &args.repos {
        push_option(&mut forwarded, "--repo", repo);
    }
    push_option(&mut forwarded, "--limit", args.limit.to_string());
    if args.json {
        forwarded.push("--json".into());
    }
    forwarded
}

fn pr_status_arguments(args: &PrStatusArgs) -> Vec<OsString> {
    let mut forwarded = Vec::new();
    for repo in &args.repos {
        push_option(&mut forwarded, "--repo", repo);
    }
    push_option(
        &mut forwarded,
        "--warn-threshold",
        args.warn_threshold.to_string(),
    );
    if args.json {
        forwarded.push("--json".into());
    }
    forwarded
}

fn run_local_history(root: &Path, args: LocalHistoryArgs) -> Result<i32, CiHubError> {
    let mut forwarded = Vec::new();
    if args.json {
        forwarded.push("--json".into());
    }
    if let Some(csv) = args.csv {
        push_option(&mut forwarded, "--csv", csv);
    }
    if args.write_global {
        forwarded.push("--write-global".into());
    }
    if let Some(since) = args.since {
        push_option(&mut forwarded, "--since", since);
    }
    if let Some(slot) = args.slot {
        push_option(&mut forwarded, "--slot", slot);
    }
    if args.profiling {
        forwarded.push("--profiling".into());
    }

    if args.json && !args.write_global && !args.profiling {
        let output = Command::new("python3")
            .arg(root.join("ci-hub/validate/aggregate.py"))
            .args(&forwarded)
            .output()
            .map_err(|source| CiHubError::Launch {
                tool: "local-history".into(),
                source,
            })?;
        io::stderr().write_all(&output.stderr).ok();
        if !output.status.success() {
            io::stdout().write_all(&output.stdout).ok();
            return Ok(exit_status_code(output.status));
        }
        let _: Vec<HistoryRow> =
            serde_json::from_slice(&output.stdout).map_err(CiHubError::HistoryJson)?;
        io::stdout().write_all(&output.stdout).ok();
        return Ok(0);
    }
    run_python(root, "ci-hub/validate/aggregate.py", forwarded)
}

fn print_obligations(root: &Path, args: ObligationsArgs) -> Result<i32, CiHubError> {
    let store = args
        .store
        .or_else(|| env::var_os("CI_HUB_OBLIGATIONS_STORE").map(PathBuf::from))
        .unwrap_or_else(|| root.join("ignored/ci-hub/obligations.jsonl"));
    let mut records = latest_obligations(&store)?;
    if !args.all {
        records.retain(|record| !record.is_closed());
    }
    records.sort_by(|left, right| {
        (&left.opened_at, &left.obligation_id).cmp(&(&right.opened_at, &right.obligation_id))
    });
    let unresolved: Vec<_> = records
        .iter()
        .filter(|record| !record.is_closed())
        .collect();
    let remediation: Vec<_> = unresolved
        .iter()
        .filter(|record| record.remediation_required())
        .collect();

    if args.json {
        #[derive(Serialize)]
        struct Output<'a> {
            obligations: &'a [ObligationRecord],
        }
        println!(
            "{}",
            serde_json::to_string(&Output {
                obligations: &records
            })
            .expect("typed obligation output is serializable")
        );
    } else if args.gate {
        let state = if !remediation.is_empty() {
            "remediation-required"
        } else if !unresolved.is_empty() {
            "open"
        } else {
            "clear"
        };
        println!("state={state}");
        println!("count={}", unresolved.len());
        println!("remediation_count={}", remediation.len());
        println!(
            "ids={}",
            if unresolved.is_empty() {
                "none".to_string()
            } else {
                unresolved
                    .iter()
                    .map(|record| record.obligation_id.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            }
        );
        println!(
            "summary={}",
            if unresolved.is_empty() {
                "no-open-speculative-land-obligations".to_string()
            } else {
                unresolved
                    .iter()
                    .map(|record| obligation_summary(record))
                    .collect::<Vec<_>>()
                    .join(";")
            }
        );
    } else {
        let heading = if !remediation.is_empty() {
            "Speculative-land obligations: REMEDIATION REQUIRED"
        } else if !unresolved.is_empty() {
            "Speculative-land obligations: OPEN"
        } else {
            "Speculative-land obligations: CLEAR"
        };
        println!("{heading}");
        for record in &records {
            println!("  {}", obligation_summary(record));
            if let Some(failure) = &record.failure_summary {
                println!("    failure: {failure}");
            }
        }
    }
    Ok(if !remediation.is_empty() {
        2
    } else if !unresolved.is_empty() {
        1
    } else {
        0
    })
}

fn latest_obligations(path: &Path) -> Result<Vec<ObligationRecord>, CiHubError> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let file = File::open(path).map_err(|source| CiHubError::ObligationOpen {
        path: path.to_path_buf(),
        source,
    })?;
    file.lock_shared()
        .map_err(|source| CiHubError::ObligationOpen {
            path: path.to_path_buf(),
            source,
        })?;
    let mut latest = BTreeMap::new();
    for (index, line) in BufReader::new(&file).lines().enumerate() {
        let line_number = index + 1;
        let line = line.map_err(|source| CiHubError::ObligationRead {
            path: path.to_path_buf(),
            source,
        })?;
        if line.trim().is_empty() {
            continue;
        }
        let record: ObligationRecord =
            serde_json::from_str(&line).map_err(|source| CiHubError::ObligationJson {
                path: path.to_path_buf(),
                line: line_number,
                source,
            })?;
        if record.schema_version != 1 {
            return Err(CiHubError::ObligationSchema {
                path: path.to_path_buf(),
                line: line_number,
                schema: record.schema_version,
            });
        }
        latest.insert(record.obligation_id.clone(), record);
    }
    FileExt::unlock(&file).map_err(|source| CiHubError::ObligationRead {
        path: path.to_path_buf(),
        source,
    })?;
    Ok(latest.into_values().collect())
}

fn obligation_summary(record: &ObligationRecord) -> String {
    format!(
        "{} {}@{} overall={} local={} github={} recommendation={}",
        record.obligation_id,
        record.repo,
        &record.landed_sha[..record.landed_sha.len().min(12)],
        record.overall_state,
        record.local.state,
        record.github.state,
        record.recommendation_action()
    )
}

fn run_python(root: &Path, relative_script: &str, args: Vec<OsString>) -> Result<i32, CiHubError> {
    run_python_path(&root.join(relative_script), args)
}

fn run_python_path(script: &Path, args: Vec<OsString>) -> Result<i32, CiHubError> {
    let mut command = Command::new("python3");
    command.arg(script).args(args);
    run_status(command, &script.display().to_string())
}

fn run_status(mut command: Command, tool: &str) -> Result<i32, CiHubError> {
    command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    let status = command.status().map_err(|source| CiHubError::Launch {
        tool: tool.to_string(),
        source,
    })?;
    Ok(exit_status_code(status))
}

fn exit_status_code(status: ExitStatus) -> i32 {
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

fn push_option(arguments: &mut Vec<OsString>, name: &'static str, value: impl AsRef<OsStr>) {
    arguments.push(name.into());
    arguments.push(value.as_ref().to_os_string());
}

fn default_actor() -> String {
    env::var("AGENT")
        .or_else(|_| env::var("USER"))
        .unwrap_or_else(|_| "unknown".into())
}

fn to_exit_code(code: i32) -> ExitCode {
    ExitCode::from(u8::try_from(code.clamp(0, 255)).unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_typed_arm_command() {
        let cli = Cli::try_parse_from([
            "ci-hub",
            "arm-land",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "--land-mode",
            "admin",
            "--no-dispatch",
        ])
        .unwrap();
        let HubCommand::ArmLand(args) = cli.command else {
            panic!("wrong command variant")
        };
        assert!(matches!(args.land_mode, LandMode::Admin));
        assert!(args.no_dispatch);
    }

    #[test]
    fn passthrough_commands_accept_worker_flags() {
        let tick = Cli::try_parse_from(["ci-hub", "tick", "--flush", "--no-header"])
            .unwrap()
            .command;
        let HubCommand::Tick(args) = tick else {
            panic!("wrong command variant")
        };
        assert_eq!(args.args, ["--flush", "--no-header"]);

        let history = Cli::try_parse_from(["ci-hub", "history", "--since", "2026-08-03"])
            .unwrap()
            .command;
        assert!(matches!(history, HubCommand::History(_)));
    }

    #[test]
    fn trivial_reads_have_no_cost_wrapper() {
        let obligations = Cli::try_parse_from(["ci-hub", "obligations"])
            .unwrap()
            .command;
        assert!(obligations.cost_spec().is_none());
        let status = Cli::try_parse_from(["ci-hub", "land-lock", "status"])
            .unwrap()
            .command;
        assert!(status.cost_spec().is_none());
    }

    #[test]
    fn substantive_network_work_is_costed() {
        let command = Cli::try_parse_from(["ci-hub", "main-health"])
            .unwrap()
            .command;
        assert!(command.cost_spec().is_some());
    }
}
