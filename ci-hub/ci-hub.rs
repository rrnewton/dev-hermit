#!/usr/bin/env -S rust-script --force
//! Typed front door for dev-hermit CI state and operations.
//!
//! `--force` makes rust-script ask Cargo to check the `#[path]` modules below.
//! Without it, rust-script keys only on this top-level file and can execute a
//! stale cached binary after `lib/*.rs` changes. Unchanged modules take Cargo's
//! no-op dependency-check path rather than being recompiled.
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
#[path = "lib/history_queries.rs"]
mod history_queries;
#[path = "lib/records.rs"]
mod records;
#[path = "lib/validate_status.rs"]
mod validate_status;

use clap::error::ErrorKind;
use clap::{Args, Parser, Subcommand, ValueEnum};
use fs2::FileExt;
use history_queries::{FirstBadOutcome, HistoryQueryEngine, NewestGreenCache, NewestGreenOutcome};
use records::{HistoryRow, ObligationRecord};
use serde::{Deserialize, Serialize};
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
const DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECONDS: u64 = 10 * 60;
const AGENT_QUICKSTART: &str = r#"ci-hub agent quickstart

FOR: dev-hermit operational CI truth, local validation receipts, landing locks,
and mandatory post-land remediation. NOT FOR: product test implementation or a
portable CI engine; those stay in Hermit and pinned agent-utils.

1. Start with the composite health signal; never translate pending into green:
     ./ci-hub/ci-hub health
   Nonzero means open remediation, red CI, or unavailable evidence. Read the
   named component before acting.

2. Inspect the narrower source of truth when triaging:
     ./ci-hub/ci-hub fresh
     ./ci-hub/ci-hub main-health
     ./ci-hub/ci-hub runner-health --all
     ./ci-hub/bin/load-probe

3. Recover local validation evidence after an agent or pane disappears:
     ./ci-hub/ci-hub local-history --since YYYY-MM-DD
     ./ci-hub/ci-hub validate-worktrees --runs 10
     ./ci-hub/ci-hub newest-green-main
     ./ci-hub/ci-hub first-bad CELL_OR_GATE
   Receipts live under ignored/ci-hub and identify slot, SHA, profile, dirty
   state, result, wall seconds, and CPU seconds. Newest-green reports whether
   its guarantee is full or selected; first-bad reports unobserved commit gaps.

4. Treat post-land obligations as durable work, not notification delivery:
     ./ci-hub/ci-hub obligations --actionable
     ./ci-hub/ci-hub inherit-obligations --agent AGENT --session "$HOSTNAME:$$"
   Do not raw-admin-merge; use the documented land-and-arm path so local and
   GitHub exact-SHA verification are armed together.

5. Serialize fleet landings through the evidence-based mutex:
     ./ci-hub/ci-hub land-lock status
     ./ci-hub/ci-hub land-lock run --agent AGENT --pr PR -- COMMAND...
   Never force-release another owner. Dead-owner reclamation requires process
   evidence and is built into the lock.

Run from the dev-hermit root. Networked subcommands apply with-proxy internally.
Meaningful work prints estimated and actual wall+CPU cost; quickstart itself is
pure and performs no workspace discovery, filesystem writes, or network calls.
"#;

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
    /// Print the opinionated agent workflow (pure: no files or network).
    Quickstart,
    /// Summarize current-main, open-PR, and speculative-land health.
    Health(HealthArgs),
    /// Reconcile TaskGraph state, task ownership, and live ORC agents.
    ActiveWork(ActiveWorkArgs),
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
    /// Discover and acknowledge remediation inherited by this lander instance.
    InheritObligations(InheritObligationsArgs),
    /// Record that ORC sent a wake which has not yet been acknowledged.
    RecordObligationWake(RecordObligationWakeArgs),
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
    /// Show validate runs currently registered by each worktree.
    ValidateWorktrees(ValidateWorktreesArgs),
    /// Summarize self-hosted runners and recent workflows.
    RunnerHealth(RunnerHealthArgs),
    /// Gate timing-sensitive work on measured CPU and memory utilization.
    LoadProbe(LoadProbeArgs),
    /// Query the local validate ledger for a commit and print the landing/cache verdict.
    ValidateStatus(ValidateStatusArgs),
    /// Find the newest main commit whose latest local validation passed.
    NewestGreenMain(NewestGreenMainArgs),
    /// Find the newest recorded PASS -> FAIL transition for a local cell or gate.
    FirstBad(FirstBadArgs),
    /// Apply `locally-validated` to PRs whose head has a clean full-validate record.
    ApplyLocalLabel(ApplyLocalLabelArgs),
    /// Operate the shared-file landing mutex.
    LandLock(landing_lock::LandLockArgs),
    /// Inspect or switch the committed CI-constrained mode and its GitHub projection.
    CiMode(CiModeArgs),
    /// Inspect or edit the named current CI batch and its ci-batch PR labels.
    Batch(BatchArgs),
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
struct ActiveWorkArgs {
    /// Read a fresh orc.listAgents() JSON array from this file.
    #[arg(long)]
    agent_snapshot: Option<PathBuf>,
    /// Reject the cached ORC snapshot after this many seconds.
    #[arg(long, default_value_t = DEFAULT_AGENT_SNAPSHOT_MAX_AGE_SECONDS)]
    max_snapshot_age: u64,
    /// Emit the versioned machine-readable report.
    #[arg(long)]
    json: bool,
    /// Emit tick-hub key/value fields.
    #[arg(long)]
    gate: bool,
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
    actionable: bool,
    #[arg(long)]
    json: bool,
    #[arg(long)]
    gate: bool,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Args, Clone, Debug)]
struct InheritObligationsArgs {
    #[arg(long)]
    agent: String,
    #[arg(long)]
    session: Option<String>,
    #[arg(long)]
    store: Option<PathBuf>,
}

#[derive(Args, Clone, Debug)]
struct RecordObligationWakeArgs {
    #[arg(long)]
    target: String,
    #[arg(long, default_value = "orc")]
    source: String,
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
struct ValidateWorktreesArgs {
    /// Include this many most-recent registered runs.
    #[arg(long, default_value_t = 0)]
    runs: usize,
    /// Mark a registered worktree stale after this many hours.
    #[arg(long, default_value_t = 24.0)]
    stale_hours: f64,
    /// Emit the machine-readable worktree and run report.
    #[arg(long)]
    json: bool,
    /// Override the canonical parent ignored/ci-hub store.
    #[arg(long)]
    data_dir: Option<PathBuf>,
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

#[derive(Args, Clone, Debug)]
struct LoadProbeArgs {
    #[arg(long, default_value_t = 1.0)]
    sample_seconds: f64,
    #[arg(long, default_value_t = 50.0)]
    max_executing_percent: f64,
    #[arg(long, default_value_t = 10.0)]
    min_memory_available_percent: f64,
    #[arg(long, default_value_t = 5)]
    top: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct ValidateStatusArgs {
    /// The commit SHA (full 40-hex or an unambiguous ledger prefix) to assess.
    #[arg(long, conflicts_with = "pr")]
    sha: Option<String>,
    /// A PR number; its head SHA is resolved via gh and assessed.
    #[arg(long, conflicts_with = "sha")]
    pr: Option<u64>,
    /// Repository used to resolve --pr head SHAs.
    #[arg(long, default_value = "rrnewton/hermit")]
    repo: String,
    /// Override the validate ledger path. Default: the parent-repo ledger named
    /// by validate_status::LEDGER_REL (resolved against the repo root by
    /// ledger_path); the literal lives only in that const, never restated here.
    #[arg(long)]
    ledger: Option<PathBuf>,
    /// Emit the machine-readable verdict report.
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct HistoryQueryArgs {
    /// Hermit checkout whose first-parent main history is queried.
    #[arg(long, default_value = "hermit")]
    repo_dir: PathBuf,
    /// Main ref to walk, newest first.
    #[arg(long, default_value = "origin/main")]
    main_ref: String,
    /// Override the canonical local validate ledger.
    #[arg(long)]
    ledger: Option<PathBuf>,
    /// Do not refresh origin/main before querying (offline/reproducible use).
    #[arg(long)]
    no_fetch: bool,
    /// Emit a versioned machine-readable report.
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct NewestGreenMainArgs {
    #[command(flatten)]
    query: HistoryQueryArgs,
    /// Override the cache path.
    #[arg(long)]
    cache: Option<PathBuf>,
    /// Ignore a valid cache and recompute from the same ledger and main tip.
    #[arg(long)]
    no_cache: bool,
}

#[derive(Args, Clone, Debug)]
struct FirstBadArgs {
    /// Exact local validate gate, DAG node, or Rust test-function name.
    cell_or_gate: String,
    #[command(flatten)]
    query: HistoryQueryArgs,
}

#[derive(Args, Clone, Debug)]
struct ApplyLocalLabelArgs {
    /// A single PR number to consider.
    #[arg(long, conflicts_with = "all_open")]
    pr: Option<u64>,
    /// Sweep every open PR in the repo, labeling each validated head.
    #[arg(long, conflicts_with = "pr")]
    all_open: bool,
    /// Repository whose PRs are labeled.
    #[arg(long, default_value = "rrnewton/hermit")]
    repo: String,
    /// Override the validate ledger path. Default: the parent-repo ledger named
    /// by validate_status::LEDGER_REL (resolved against the repo root by
    /// ledger_path); the literal lives only in that const, never restated here.
    #[arg(long)]
    ledger: Option<PathBuf>,
    /// Report intended label actions without editing any label.
    #[arg(long)]
    dry_run: bool,
    /// Emit the machine-readable per-PR action report.
    #[arg(long)]
    json: bool,
}

/// Name of the committed source-of-truth mode file, relative to the workspace root.
const CI_MODE_STATE_PATH: &str = "ci-hub/health/ci-mode.json";
/// GitHub repository variable that projects the mode where workflows can read it.
const CI_MODE_VARIABLE: &str = "CI_MODE";
/// Repositories whose auto-fan-out is gated by the mode; both carry the projection.
const CI_MODE_REPOS: [&str; 2] = ["rrnewton/hermit", "rrnewton/reverie"];

/// Name of the committed source-of-truth batch file, relative to the workspace root.
const CI_BATCH_STATE_PATH: &str = "ci-hub/health/ci-batch.json";
/// PR label that projects batch membership where workflows can read it; a PR
/// carrying this label is exempt from the constrained-mode gate and gets CI.
const CI_BATCH_LABEL: &str = "ci-batch";
/// Repository assumed for `--pr N` when no `--repo` is given.
const CI_BATCH_DEFAULT_REPO: &str = "rrnewton/hermit";

#[derive(Args, Clone, Debug)]
struct CiModeArgs {
    #[command(subcommand)]
    command: CiModeCommand,
}

#[derive(Subcommand, Clone, Debug)]
enum CiModeCommand {
    /// Print the committed mode and report drift against the GitHub projection.
    Status(CiModeStatusArgs),
    /// Write the mode, project it to GitHub, and commit the state file to main.
    Set(CiModeSetArgs),
    /// Dispatch targeted CI for one PR head without auto-arming any fan-out.
    Fire(CiModeFireArgs),
}

#[derive(Args, Clone, Debug)]
struct CiModeStatusArgs {
    /// Emit the machine-readable mode-and-drift report.
    #[arg(long)]
    json: bool,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum CiModeValue {
    /// Every PR push auto-fans-out the hosted CI workflows.
    Auto,
    /// Auto-fan-out is suppressed; hosted CI runs only when explicitly fired.
    Constrained,
}

impl CiModeValue {
    fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Constrained => "constrained",
        }
    }
}

#[derive(Args, Clone, Debug)]
struct CiModeSetArgs {
    /// New mode to record and project.
    #[arg(value_enum)]
    mode: CiModeValue,
    /// Operator-supplied justification recorded in the state file.
    #[arg(long)]
    reason: String,
    /// Optional evidence string, e.g. "queued=6 max-age=2h03m".
    #[arg(long)]
    evidence: Option<String>,
    /// Compute and print the intended write without touching files, GitHub, or git.
    #[arg(long)]
    dry_run: bool,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum CiModeLane {
    Portable,
    Privileged,
}

impl CiModeLane {
    fn as_str(self) -> &'static str {
        match self {
            Self::Portable => "portable",
            Self::Privileged => "privileged",
        }
    }
}

#[derive(Args, Clone, Debug)]
struct CiModeFireArgs {
    /// PR number whose head branch receives the targeted dispatch.
    #[arg(long)]
    pr: u64,
    /// Which validation lane to dispatch.
    #[arg(long, value_enum, default_value_t = CiModeLane::Portable)]
    lane: CiModeLane,
    /// Repository owning the PR and the dispatch-only DAG workflow.
    #[arg(long, default_value = "rrnewton/hermit")]
    repo: String,
}

/// Committed source of truth for the CI-constrained mode. Absent file == auto.
#[derive(Serialize, Deserialize, Clone, Debug)]
struct CiModeState {
    mode: String,
    reason: String,
    since: String,
    actor: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    evidence: Option<String>,
}

impl CiModeState {
    fn default_auto() -> Self {
        Self {
            mode: "auto".into(),
            reason: "default: no committed CI-mode state on disk; treated as auto".into(),
            since: String::new(),
            actor: String::new(),
            evidence: None,
        }
    }
}

#[derive(Args, Clone, Debug)]
struct BatchArgs {
    #[command(subcommand)]
    command: BatchCommand,
}

#[derive(Subcommand, Clone, Debug)]
enum BatchCommand {
    /// Print the named current batch and its member PRs (reads the file only).
    Show(BatchShowArgs),
    /// Replace the current batch with a new named batch and label its PRs.
    Set(BatchSetArgs),
    /// Add PR(s) to the current batch and apply the ci-batch label.
    Add(BatchMemberArgs),
    /// Remove PR(s) from the current batch and drop the ci-batch label.
    Remove(BatchMemberArgs),
    /// Clear the current batch, dropping the ci-batch label from every member.
    Clear(BatchClearArgs),
}

#[derive(Args, Clone, Debug)]
struct BatchShowArgs {
    /// Emit the machine-readable batch report.
    #[arg(long)]
    json: bool,
}

#[derive(Args, Clone, Debug)]
struct BatchSetArgs {
    /// Stable descriptive slug naming the batch, e.g. "cpu-timeout-landing".
    name: String,
    /// Operator-supplied justification recorded in the state file.
    #[arg(long)]
    reason: String,
    /// Repository owning every `--pr` in this invocation.
    #[arg(long, default_value = CI_BATCH_DEFAULT_REPO)]
    repo: String,
    /// Initial member PR number(s); each is labelled ci-batch. Repeatable.
    #[arg(long = "pr")]
    prs: Vec<u64>,
    /// Compute and print the intended change without touching files, GitHub, or git.
    #[arg(long)]
    dry_run: bool,
}

#[derive(Args, Clone, Debug)]
struct BatchMemberArgs {
    /// Repository owning every `--pr` in this invocation.
    #[arg(long, default_value = CI_BATCH_DEFAULT_REPO)]
    repo: String,
    /// PR number(s) to add or remove. Repeatable; at least one required.
    #[arg(long = "pr", required = true)]
    prs: Vec<u64>,
    /// Compute and print the intended change without touching files, GitHub, or git.
    #[arg(long)]
    dry_run: bool,
}

#[derive(Args, Clone, Debug)]
struct BatchClearArgs {
    /// Compute and print the intended change without touching files, GitHub, or git.
    #[arg(long)]
    dry_run: bool,
}

/// One member of a batch: a PR is identified by its owning repo and number, so a
/// batch may span both gated repositories.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
struct BatchPr {
    repo: String,
    number: u64,
}

/// Committed source of truth for the named current CI batch. Absent file == no
/// batch. Membership is projected to GitHub as the ci-batch label on each PR.
#[derive(Serialize, Deserialize, Clone, Debug)]
struct CiBatchState {
    name: String,
    reason: String,
    since: String,
    actor: String,
    #[serde(default)]
    prs: Vec<BatchPr>,
}

impl CiBatchState {
    fn empty() -> Self {
        Self {
            name: String::new(),
            reason: String::new(),
            since: String::new(),
            actor: String::new(),
            prs: Vec::new(),
        }
    }

    fn is_active(&self) -> bool {
        !self.name.is_empty()
    }
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
            Self::ActiveWork(_) => CostSpec {
                tool: "ci-hub/active-work",
                basis: "not measured: one local TaskGraph scan plus ORC snapshot reconciliation"
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
            Self::NewestGreenMain(_) => CostSpec {
                tool: "ci-hub/newest-green-main",
                basis: "not measured: one bounded main fetch plus local first-parent/ledger query; cache may avoid the query but not freshness check".into(),
            },
            Self::FirstBad(_) => CostSpec {
                tool: "ci-hub/first-bad",
                basis: "not measured: one bounded main fetch plus local ledger/log/diff query; no tests are executed".into(),
            },
            Self::RunnerHealth(_) => CostSpec {
                tool: "ci-hub/runner-health",
                basis: "not measured: runner/workflow query cost history is not retained".into(),
            },
            Self::LoadProbe(args) => CostSpec {
                tool: "ci-hub/load-probe",
                basis: format!(
                    "not measured: requested sample={:.3}s plus /proc+cgroup scan; retained runtime history not established",
                    args.sample_seconds
                ),
            },
            Self::LandLock(args) if args.command.consumes_meaningful_time() => CostSpec {
                tool: "ci-hub/land-lock",
                basis: "not measured: queue wait and optional child command vary; wait/lease values are bounds, not estimates"
                    .into(),
            },
            Self::Obligations(_)
            | Self::InheritObligations(_)
            | Self::RecordObligationWake(_)
            | Self::ResolveObligation(_)
            | Self::ValidateWorktrees(_)
            | Self::Quickstart
            | Self::CiMode(_)
            | Self::Batch(_)
            | Self::ValidateStatus(_)
            | Self::ApplyLocalLabel(_)
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
    #[error("ci-hub: cannot read CI-mode state {path}: {source}")]
    CiModeRead {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: cannot write CI-mode state {path}: {source}")]
    CiModeWrite {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: CI-mode state {path} is not valid JSON: {source}")]
    CiModeJson {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("ci-hub: cannot read batch state {path}: {source}")]
    CiBatchRead {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: cannot write batch state {path}: {source}")]
    CiBatchWrite {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: batch state {path} is not valid JSON: {source}")]
    CiBatchJson {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("ci-hub: cannot read validate ledger {path}: {source}")]
    LedgerRead {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("ci-hub: validate-status: {0}")]
    ValidateStatus(String),
    #[error("ci-hub: history query: {0}")]
    HistoryQuery(String),
    #[error("ci-hub: gh {context}: {message}")]
    Gh { context: String, message: String },
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
    // Agent primers are exploratory documentation. Keep this before workspace
    // discovery, cost wrapping, and every subprocess so it is safe anywhere.
    if matches!(&cli.command, HubCommand::Quickstart) {
        print!("{AGENT_QUICKSTART}");
        return ExitCode::SUCCESS;
    }
    if env::var_os("CI_HUB_DOCS_PARSE_ONLY").is_some() {
        println!(
            "DOCS PARSE OK: {}",
            raw[1..]
                .iter()
                .map(|argument| argument.to_string_lossy())
                .collect::<Vec<_>>()
                .join(" ")
        );
        return ExitCode::SUCCESS;
    }
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
        HubCommand::Quickstart => unreachable!("quickstart returns before workspace discovery"),
        HubCommand::Health(args) => {
            let obligation_code = print_obligations(
                root,
                ObligationsArgs {
                    all: false,
                    actionable: false,
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
        HubCommand::ActiveWork(args) => {
            let mut forwarded = vec![OsString::from("active-work")];
            if let Some(snapshot) = args.agent_snapshot {
                push_option(&mut forwarded, "--agent-snapshot", snapshot);
            }
            push_option(
                &mut forwarded,
                "--max-snapshot-age",
                args.max_snapshot_age.to_string(),
            );
            if args.json {
                forwarded.push("--json".into());
            }
            if args.gate {
                forwarded.push("--gate".into());
            }
            run_python(root, "ci-hub/health/operational_health.py", forwarded)
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
            let mut command = Command::new(agent_tool(root));
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
        HubCommand::InheritObligations(args) => {
            let mut protocol_args = vec![OsString::from("inherit")];
            push_option(&mut protocol_args, "--agent", args.agent);
            if let Some(session) = args.session {
                push_option(&mut protocol_args, "--session", session);
            }
            if let Some(store) = args.store {
                push_option(&mut protocol_args, "--store", store);
            }
            run_python(root, "ci-hub/remediation/protocol.py", protocol_args)
        }
        HubCommand::RecordObligationWake(args) => {
            let mut protocol_args = vec![OsString::from("wake-sent")];
            push_option(&mut protocol_args, "--target", args.target);
            push_option(&mut protocol_args, "--source", args.source);
            if let Some(store) = args.store {
                push_option(&mut protocol_args, "--store", store);
            }
            run_python(root, "ci-hub/remediation/protocol.py", protocol_args)
        }
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
        HubCommand::ValidateWorktrees(args) => {
            let mut forwarded = Vec::new();
            push_option(&mut forwarded, "--runs", args.runs.to_string());
            push_option(
                &mut forwarded,
                "--stale-hours",
                args.stale_hours.to_string(),
            );
            if args.json {
                forwarded.push("--json".into());
            }
            if let Some(data_dir) = args.data_dir {
                push_option(&mut forwarded, "--data-dir", data_dir);
            }
            run_python(root, "ci-hub/validate/worktrees.py", forwarded)
        }
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
        HubCommand::LoadProbe(args) => {
            let mut forwarded = Vec::new();
            push_option(
                &mut forwarded,
                "--sample-seconds",
                args.sample_seconds.to_string(),
            );
            push_option(
                &mut forwarded,
                "--max-executing-percent",
                args.max_executing_percent.to_string(),
            );
            push_option(
                &mut forwarded,
                "--min-memory-available-percent",
                args.min_memory_available_percent.to_string(),
            );
            push_option(&mut forwarded, "--top", args.top.to_string());
            if args.json {
                forwarded.push("--json".into());
            }
            run_python(root, "ci-hub/health/load_probe.py", forwarded)
        }
        HubCommand::ValidateStatus(args) => run_validate_status(root, args),
        HubCommand::NewestGreenMain(args) => run_newest_green_main(root, args),
        HubCommand::FirstBad(args) => run_first_bad(root, args),
        HubCommand::ApplyLocalLabel(args) => run_apply_local_label(root, args),
        HubCommand::LandLock(args) => landing_lock::execute(root, args).map_err(Into::into),
        HubCommand::CiMode(args) => match args.command {
            CiModeCommand::Status(status_args) => ci_mode_status(root, status_args),
            CiModeCommand::Set(set_args) => ci_mode_set(root, set_args),
            CiModeCommand::Fire(fire_args) => ci_mode_fire(root, fire_args),
        },
        HubCommand::Batch(args) => match args.command {
            BatchCommand::Show(show_args) => batch_show(root, show_args),
            BatchCommand::Set(set_args) => batch_set(root, set_args),
            BatchCommand::Add(member_args) => batch_add(root, member_args),
            BatchCommand::Remove(member_args) => batch_remove(root, member_args),
            BatchCommand::Clear(clear_args) => batch_clear(root, clear_args),
        },
    }
}

fn ci_mode_path(root: &Path) -> PathBuf {
    root.join(CI_MODE_STATE_PATH)
}

/// Load the committed mode. Returns `(state, present)`; an absent file is auto.
fn load_ci_mode(root: &Path) -> Result<(CiModeState, bool), CiHubError> {
    let path = ci_mode_path(root);
    if !path.exists() {
        return Ok((CiModeState::default_auto(), false));
    }
    let raw = std::fs::read_to_string(&path).map_err(|source| CiHubError::CiModeRead {
        path: path.clone(),
        source,
    })?;
    let state = serde_json::from_str(&raw).map_err(|source| CiHubError::CiModeJson { path, source })?;
    Ok((state, true))
}

fn ci_mode_actor() -> String {
    if let Ok(session) = env::var("ORC_AGENT_SESSION_ID") {
        if !session.is_empty() {
            return session;
        }
    }
    let host = env::var("HOSTNAME")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            Command::new("hostname")
                .arg("-s")
                .output()
                .ok()
                .filter(|out| out.status.success())
                .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
                .filter(|value| !value.is_empty())
        });
    match host {
        Some(host) => format!("{host}:{}", std::process::id()),
        None => "unknown".into(),
    }
}

fn on_path(binary: &str) -> bool {
    env::var_os("PATH")
        .map(|paths| env::split_paths(&paths).any(|dir| dir.join(binary).is_file()))
        .unwrap_or(false)
}

/// Build a `gh` invocation, prefixing `with-proxy` for external egress when
/// available (mirrors the Python health probes' `with-proxy gh` default).
fn gh_command(root: &Path, args: &[&str]) -> Command {
    let mut command = if on_path("with-proxy") {
        let mut command = Command::new("with-proxy");
        command.arg("gh");
        command
    } else {
        Command::new("gh")
    };
    command.args(args).current_dir(root);
    command
}

#[derive(Deserialize)]
struct GhVariableRow {
    name: String,
    value: String,
}

/// Read the projected mode variable. `Ok(None)` means the variable is not set
/// (treated as auto); `Err` means the query itself failed (network/auth).
fn read_ci_mode_variable(root: &Path, repo: &str) -> Result<Option<String>, String> {
    let output = gh_command(
        root,
        &["variable", "list", "--repo", repo, "--json", "name,value"],
    )
    .output()
    .map_err(|source| format!("launch gh: {source}"))?;
    if !output.status.success() {
        return Err(format!(
            "gh variable list exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let rows: Vec<GhVariableRow> =
        serde_json::from_slice(&output.stdout).map_err(|source| format!("parse gh json: {source}"))?;
    Ok(rows
        .into_iter()
        .find(|row| row.name == CI_MODE_VARIABLE)
        .map(|row| row.value))
}

fn set_ci_mode_variable(root: &Path, repo: &str, value: &str) -> Result<(), String> {
    let output = gh_command(
        root,
        &[
            "variable",
            "set",
            CI_MODE_VARIABLE,
            "--repo",
            repo,
            "--body",
            value,
        ],
    )
    .output()
    .map_err(|source| format!("launch gh: {source}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "gh variable set exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

fn ci_mode_status(root: &Path, args: CiModeStatusArgs) -> Result<i32, CiHubError> {
    let (state, present) = load_ci_mode(root)?;

    #[derive(Serialize)]
    struct Projection {
        repo: String,
        value: Option<String>,
        projected_mode: String,
        drift: bool,
        error: Option<String>,
    }

    let mut projections = Vec::new();
    let mut any_drift = false;
    let mut any_error = false;
    for repo in CI_MODE_REPOS {
        match read_ci_mode_variable(root, repo) {
            Ok(value) => {
                let projected = value.clone().unwrap_or_else(|| "auto".into());
                let drift = projected != state.mode;
                any_drift |= drift;
                projections.push(Projection {
                    repo: repo.to_string(),
                    value,
                    projected_mode: projected,
                    drift,
                    error: None,
                });
            }
            Err(error) => {
                any_error = true;
                projections.push(Projection {
                    repo: repo.to_string(),
                    value: None,
                    projected_mode: "unknown".into(),
                    drift: false,
                    error: Some(error),
                });
            }
        }
    }

    if args.json {
        #[derive(Serialize)]
        struct Report<'a> {
            file_present: bool,
            mode: &'a str,
            reason: &'a str,
            since: &'a str,
            actor: &'a str,
            #[serde(skip_serializing_if = "Option::is_none")]
            evidence: &'a Option<String>,
            drift: bool,
            projection_error: bool,
            projections: &'a [Projection],
        }
        println!(
            "{}",
            serde_json::to_string(&Report {
                file_present: present,
                mode: &state.mode,
                reason: &state.reason,
                since: &state.since,
                actor: &state.actor,
                evidence: &state.evidence,
                drift: any_drift,
                projection_error: any_error,
                projections: &projections,
            })
            .expect("ci-mode status report is serializable")
        );
    } else {
        println!("CI mode: {}", state.mode.to_uppercase());
        println!(
            "  source: {}",
            if present {
                ci_mode_path(root).display().to_string()
            } else {
                format!("(no {CI_MODE_STATE_PATH}; default auto)")
            }
        );
        if present {
            println!("  reason: {}", state.reason);
            if !state.since.is_empty() {
                println!("  since:  {}", state.since);
            }
            if !state.actor.is_empty() {
                println!("  actor:  {}", state.actor);
            }
            if let Some(evidence) = &state.evidence {
                println!("  evidence: {evidence}");
            }
        }
        println!("GitHub projection ({CI_MODE_VARIABLE}):");
        for projection in &projections {
            match &projection.error {
                Some(error) => println!("  {}: QUERY FAILED: {error}", projection.repo),
                None => {
                    let displayed = projection
                        .value
                        .clone()
                        .unwrap_or_else(|| "<not set> (auto)".into());
                    let marker = if projection.drift { "  <-- DRIFT" } else { "" };
                    println!("  {}: {displayed}{marker}", projection.repo);
                }
            }
        }
        if any_drift {
            println!(
                "DRIFT: committed mode is {} but the GitHub projection disagrees; re-run `ci-hub ci-mode set {}` to reconcile.",
                state.mode, state.mode
            );
        }
        if any_error {
            println!("WARNING: at least one projection query failed; drift is undetermined for those repos.");
        }
    }

    Ok(if any_error {
        2
    } else if any_drift {
        1
    } else {
        0
    })
}

fn ci_mode_set(root: &Path, args: CiModeSetArgs) -> Result<i32, CiHubError> {
    let value = args.mode.as_str();
    let state = CiModeState {
        mode: value.to_string(),
        reason: args.reason,
        since: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
        actor: ci_mode_actor(),
        evidence: args.evidence,
    };
    let json = serde_json::to_string_pretty(&state).expect("ci-mode state is serializable") + "\n";
    let path = ci_mode_path(root);

    if args.dry_run {
        println!("DRY RUN: no files, GitHub variables, or commits changed.");
        println!("Would write {}:", path.display());
        println!("{json}");
        println!("Would project {CI_MODE_VARIABLE}={value} to: {}", CI_MODE_REPOS.join(", "));
        println!("Would commit {CI_MODE_STATE_PATH} to parent main and push origin HEAD:main.");
        return Ok(0);
    }

    // 1. Write the source of truth first so a projection or git failure never
    //    loses the recorded decision.
    std::fs::write(&path, &json).map_err(|source| CiHubError::CiModeWrite {
        path: path.clone(),
        source,
    })?;
    println!("Wrote {} (mode={value}).", path.display());

    let mut failures: Vec<String> = Vec::new();

    // 2. Project to the GitHub variable on every gated repo.
    for repo in CI_MODE_REPOS {
        match set_ci_mode_variable(root, repo, value) {
            Ok(()) => println!("Projected {CI_MODE_VARIABLE}={value} to {repo}."),
            Err(error) => {
                eprintln!("PROJECTION FAILED for {repo}: {error}");
                failures.push(format!("{repo}: {error}"));
            }
        }
    }

    // 3. Commit the state file to parent main (path-scoped; never disturbs
    //    another agent's staged work). Push follows; a rejected push is
    //    reported, not force-resolved, on the shared checkout.
    match commit_ci_mode_state(root, value) {
        Ok(true) => {
            println!("Committed {CI_MODE_STATE_PATH} to parent main.");
            match push_parent_main(root) {
                Ok(()) => println!("Pushed origin HEAD:main."),
                Err(error) => {
                    eprintln!("PUSH FAILED: {error}");
                    failures.push(format!("push: {error}"));
                }
            }
        }
        Ok(false) => println!("No state change to commit (file already matches)."),
        Err(error) => {
            eprintln!("COMMIT FAILED: {error}");
            failures.push(format!("commit: {error}"));
        }
    }

    if failures.is_empty() {
        println!("CI mode set to {value}.");
        Ok(0)
    } else {
        eprintln!(
            "ci-hub ci-mode set: committed decision is {value}, but {} projection/publish step(s) failed: {}",
            failures.len(),
            failures.join("; ")
        );
        Ok(2)
    }
}

/// Returns Ok(true) if a commit was created, Ok(false) if the file was unchanged.
fn commit_ci_mode_state(root: &Path, value: &str) -> Result<bool, String> {
    let dirty = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["status", "--porcelain", "--", CI_MODE_STATE_PATH])
        .output()
        .map_err(|source| format!("launch git status: {source}"))?;
    if !dirty.status.success() {
        return Err(format!(
            "git status exited {}: {}",
            exit_status_code(dirty.status),
            String::from_utf8_lossy(&dirty.stderr).trim()
        ));
    }
    if dirty.stdout.is_empty() {
        return Ok(false);
    }
    let message = format!("ci-hub: set CI mode to {value}");
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["commit", "-m", &message, "-o", "--", CI_MODE_STATE_PATH])
        .output()
        .map_err(|source| format!("launch git commit: {source}"))?;
    if output.status.success() {
        Ok(true)
    } else {
        Err(format!(
            "git commit exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

fn push_parent_main(root: &Path) -> Result<(), String> {
    let mut command = if on_path("with-proxy") {
        let mut command = Command::new("with-proxy");
        command.arg("git");
        command
    } else {
        Command::new("git")
    };
    let output = command
        .arg("-C")
        .arg(root)
        .args(["push", "origin", "HEAD:main"])
        .output()
        .map_err(|source| format!("launch git push: {source}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "git push exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

fn ci_mode_fire(root: &Path, args: CiModeFireArgs) -> Result<i32, CiHubError> {
    let pr = args.pr.to_string();
    // 1. Resolve the PR head branch; the dispatch-only DAG runs at that ref.
    let view = gh_command(
        root,
        &[
            "pr",
            "view",
            &pr,
            "--repo",
            &args.repo,
            "--json",
            "headRefName",
            "-q",
            ".headRefName",
        ],
    )
    .output()
    .map_err(|source| CiHubError::Launch {
        tool: "gh pr view".into(),
        source,
    })?;
    if !view.status.success() {
        eprintln!(
            "ci-hub ci-mode fire: gh pr view #{pr} on {} failed (exit {}): {}",
            args.repo,
            exit_status_code(view.status),
            String::from_utf8_lossy(&view.stderr).trim()
        );
        return Ok(2);
    }
    let head_ref = String::from_utf8_lossy(&view.stdout).trim().to_string();
    if head_ref.is_empty() {
        eprintln!("ci-hub ci-mode fire: could not resolve head branch for PR #{pr} on {}.", args.repo);
        return Ok(2);
    }

    // 2. Dispatch the workflow_dispatch-only DAG at that head; arm nothing else.
    let lane_arg = format!("lane={}", args.lane.as_str());
    let dispatch = gh_command(
        root,
        &[
            "workflow",
            "run",
            "ci-dag.yml",
            "--repo",
            &args.repo,
            "--ref",
            &head_ref,
            "-f",
            &lane_arg,
        ],
    )
    .status()
    .map_err(|source| CiHubError::Launch {
        tool: "gh workflow run".into(),
        source,
    })?;
    if dispatch.success() {
        println!(
            "Dispatched ci-dag.yml (lane={}) on {} at {} (PR #{pr}).",
            args.lane.as_str(),
            args.repo,
            head_ref
        );
        Ok(0)
    } else {
        eprintln!(
            "ci-hub ci-mode fire: gh workflow run failed (exit {}).",
            exit_status_code(dispatch)
        );
        Ok(2)
    }
}

fn batch_path(root: &Path) -> PathBuf {
    root.join(CI_BATCH_STATE_PATH)
}

/// Load the committed batch. Returns `(state, present)`; an absent file is an
/// empty (inactive) batch.
fn load_batch(root: &Path) -> Result<(CiBatchState, bool), CiHubError> {
    let path = batch_path(root);
    if !path.exists() {
        return Ok((CiBatchState::empty(), false));
    }
    let raw = std::fs::read_to_string(&path).map_err(|source| CiHubError::CiBatchRead {
        path: path.clone(),
        source,
    })?;
    let state =
        serde_json::from_str(&raw).map_err(|source| CiHubError::CiBatchJson { path, source })?;
    Ok((state, true))
}

/// Serialize the batch to its committed on-disk form (pretty + trailing newline,
/// matching ci-mode so a hand-authored seed and a tool write are byte-identical).
fn batch_json(state: &CiBatchState) -> String {
    serde_json::to_string_pretty(state).expect("batch state is serializable") + "\n"
}

/// Create the ci-batch label if it is missing; an "already exists" result is
/// success, so this is idempotent and safe to call before every label edit.
fn ensure_batch_label(root: &Path, repo: &str) -> Result<(), String> {
    let output = gh_command(
        root,
        &[
            "label",
            "create",
            CI_BATCH_LABEL,
            "--repo",
            repo,
            "--color",
            "1D76DB",
            "--description",
            "Current CI batch: exempt from constrained-mode gate; gets CI now.",
        ],
    )
    .output()
    .map_err(|source| format!("launch gh label create: {source}"))?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    if stderr.contains("already exists") {
        return Ok(());
    }
    Err(format!(
        "gh label create exited {}: {}",
        exit_status_code(output.status),
        stderr.trim()
    ))
}

/// Apply (`--add-label`) or drop (`--remove-label`) the ci-batch label on one PR.
fn edit_batch_label(root: &Path, pr: &BatchPr, add: bool) -> Result<(), String> {
    let flag = if add { "--add-label" } else { "--remove-label" };
    let number = pr.number.to_string();
    let output = gh_command(
        root,
        &[
            "pr",
            "edit",
            &number,
            "--repo",
            &pr.repo,
            flag,
            CI_BATCH_LABEL,
        ],
    )
    .output()
    .map_err(|source| format!("launch gh pr edit: {source}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "gh pr edit #{} on {} exited {}: {}",
            pr.number,
            pr.repo,
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

/// Returns Ok(true) if a commit was created, Ok(false) if the file was unchanged.
fn commit_batch_state(root: &Path, name: &str) -> Result<bool, String> {
    let dirty = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["status", "--porcelain", "--", CI_BATCH_STATE_PATH])
        .output()
        .map_err(|source| format!("launch git status: {source}"))?;
    if !dirty.status.success() {
        return Err(format!(
            "git status exited {}: {}",
            exit_status_code(dirty.status),
            String::from_utf8_lossy(&dirty.stderr).trim()
        ));
    }
    if dirty.stdout.is_empty() {
        return Ok(false);
    }
    let message = if name.is_empty() {
        "ci-hub: clear CI batch".to_string()
    } else {
        format!("ci-hub: set CI batch to {name}")
    };
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["commit", "-m", &message, "-o", "--", CI_BATCH_STATE_PATH])
        .output()
        .map_err(|source| format!("launch git commit: {source}"))?;
    if output.status.success() {
        Ok(true)
    } else {
        Err(format!(
            "git commit exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

/// Shared publish path for every mutating batch command: write the source of
/// truth first (a label/git failure never loses the decision), project the
/// ci-batch label additions and removals, then commit and push the state file.
/// `to_add`/`to_drop` are the label deltas; `state` is the already-updated batch.
fn publish_batch(
    root: &Path,
    state: &CiBatchState,
    to_add: &[BatchPr],
    to_drop: &[BatchPr],
) -> Result<i32, CiHubError> {
    let path = batch_path(root);
    std::fs::write(&path, batch_json(state)).map_err(|source| CiHubError::CiBatchWrite {
        path: path.clone(),
        source,
    })?;
    println!("Wrote {}.", path.display());

    let mut failures: Vec<String> = Vec::new();

    // Ensure the label exists once per repo we add it to (removals reference an
    // already-existing label, so only additions need the create).
    let mut repos: Vec<&str> = to_add.iter().map(|pr| pr.repo.as_str()).collect();
    repos.sort_unstable();
    repos.dedup();
    for repo in repos {
        if let Err(error) = ensure_batch_label(root, repo) {
            eprintln!("LABEL ENSURE FAILED for {repo}: {error}");
            failures.push(format!("ensure-label {repo}: {error}"));
        }
    }

    for pr in to_add {
        match edit_batch_label(root, pr, true) {
            Ok(()) => println!("Labelled {} #{} {CI_BATCH_LABEL}.", pr.repo, pr.number),
            Err(error) => {
                eprintln!("LABEL ADD FAILED for {} #{}: {error}", pr.repo, pr.number);
                failures.push(format!("add-label {} #{}: {error}", pr.repo, pr.number));
            }
        }
    }
    for pr in to_drop {
        match edit_batch_label(root, pr, false) {
            Ok(()) => println!("Unlabelled {} #{} {CI_BATCH_LABEL}.", pr.repo, pr.number),
            Err(error) => {
                eprintln!("LABEL REMOVE FAILED for {} #{}: {error}", pr.repo, pr.number);
                failures.push(format!("remove-label {} #{}: {error}", pr.repo, pr.number));
            }
        }
    }

    match commit_batch_state(root, &state.name) {
        Ok(true) => {
            println!("Committed {CI_BATCH_STATE_PATH} to parent main.");
            match push_parent_main(root) {
                Ok(()) => println!("Pushed origin HEAD:main."),
                Err(error) => {
                    eprintln!("PUSH FAILED: {error}");
                    failures.push(format!("push: {error}"));
                }
            }
        }
        Ok(false) => println!("No state change to commit (file already matches)."),
        Err(error) => {
            eprintln!("COMMIT FAILED: {error}");
            failures.push(format!("commit: {error}"));
        }
    }

    if failures.is_empty() {
        Ok(0)
    } else {
        eprintln!(
            "ci-hub batch: state written, but {} projection/publish step(s) failed: {}",
            failures.len(),
            failures.join("; ")
        );
        Ok(2)
    }
}

fn batch_show(root: &Path, args: BatchShowArgs) -> Result<i32, CiHubError> {
    let (state, present) = load_batch(root)?;
    if args.json {
        #[derive(Serialize)]
        struct Report<'a> {
            file_present: bool,
            active: bool,
            name: &'a str,
            reason: &'a str,
            since: &'a str,
            actor: &'a str,
            prs: &'a [BatchPr],
        }
        println!(
            "{}",
            serde_json::to_string(&Report {
                file_present: present,
                active: state.is_active(),
                name: &state.name,
                reason: &state.reason,
                since: &state.since,
                actor: &state.actor,
                prs: &state.prs,
            })
            .expect("batch report is serializable")
        );
        return Ok(0);
    }
    if !state.is_active() {
        println!("Current batch: NONE");
        println!(
            "  source: {}",
            if present {
                batch_path(root).display().to_string()
            } else {
                format!("(no {CI_BATCH_STATE_PATH}; no batch)")
            }
        );
        return Ok(0);
    }
    println!("Current batch: {}", state.name);
    println!("  source: {}", batch_path(root).display());
    println!("  reason: {}", state.reason);
    if !state.since.is_empty() {
        println!("  since:  {}", state.since);
    }
    if !state.actor.is_empty() {
        println!("  actor:  {}", state.actor);
    }
    if state.prs.is_empty() {
        println!("  PRs:    (none)");
    } else {
        println!("  PRs:");
        for pr in &state.prs {
            println!("    {} #{}", pr.repo, pr.number);
        }
    }
    Ok(0)
}

/// Deduplicate a `--repo` + repeated `--pr` invocation into distinct members.
fn members_from(repo: &str, prs: &[u64]) -> Vec<BatchPr> {
    let mut out: Vec<BatchPr> = Vec::new();
    for &number in prs {
        let pr = BatchPr {
            repo: repo.to_string(),
            number,
        };
        if !out.contains(&pr) {
            out.push(pr);
        }
    }
    out
}

fn batch_set(root: &Path, args: BatchSetArgs) -> Result<i32, CiHubError> {
    let (old, _present) = load_batch(root)?;
    let new_members = members_from(&args.repo, &args.prs);
    let state = CiBatchState {
        name: args.name,
        reason: args.reason,
        since: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
        actor: ci_mode_actor(),
        prs: new_members.clone(),
    };
    // Old members no longer present lose the label; new members gain it.
    let to_drop: Vec<BatchPr> = old
        .prs
        .iter()
        .filter(|pr| !new_members.contains(pr))
        .cloned()
        .collect();
    let to_add: Vec<BatchPr> = new_members
        .iter()
        .filter(|pr| !old.prs.contains(pr))
        .cloned()
        .collect();

    if args.dry_run {
        batch_dry_run(root, &state, &to_add, &to_drop);
        return Ok(0);
    }
    publish_batch(root, &state, &to_add, &to_drop)
}

fn batch_add(root: &Path, args: BatchMemberArgs) -> Result<i32, CiHubError> {
    let (mut state, _present) = load_batch(root)?;
    if !state.is_active() {
        eprintln!(
            "ci-hub batch add: no current batch; run `ci-hub batch set <name> --reason ...` first."
        );
        return Ok(2);
    }
    let requested = members_from(&args.repo, &args.prs);
    let to_add: Vec<BatchPr> = requested
        .iter()
        .filter(|pr| !state.prs.contains(pr))
        .cloned()
        .collect();
    if to_add.is_empty() {
        println!("All requested PRs are already in batch {}.", state.name);
        return Ok(0);
    }
    state.prs.extend(to_add.iter().cloned());

    if args.dry_run {
        batch_dry_run(root, &state, &to_add, &[]);
        return Ok(0);
    }
    publish_batch(root, &state, &to_add, &[])
}

fn batch_remove(root: &Path, args: BatchMemberArgs) -> Result<i32, CiHubError> {
    let (mut state, present) = load_batch(root)?;
    if !present || !state.is_active() {
        eprintln!("ci-hub batch remove: no current batch to remove PRs from.");
        return Ok(2);
    }
    let requested = members_from(&args.repo, &args.prs);
    let to_drop: Vec<BatchPr> = requested
        .iter()
        .filter(|pr| state.prs.contains(pr))
        .cloned()
        .collect();
    if to_drop.is_empty() {
        println!("None of the requested PRs are in batch {}.", state.name);
        return Ok(0);
    }
    state.prs.retain(|pr| !to_drop.contains(pr));

    if args.dry_run {
        batch_dry_run(root, &state, &[], &to_drop);
        return Ok(0);
    }
    publish_batch(root, &state, &[], &to_drop)
}

fn batch_clear(root: &Path, args: BatchClearArgs) -> Result<i32, CiHubError> {
    let (old, present) = load_batch(root)?;
    if !present && !old.is_active() {
        println!("No current batch to clear.");
        return Ok(0);
    }
    let to_drop = old.prs.clone();
    let state = CiBatchState::empty();

    if args.dry_run {
        batch_dry_run(root, &state, &[], &to_drop);
        return Ok(0);
    }
    publish_batch(root, &state, &[], &to_drop)
}

fn batch_dry_run(root: &Path, state: &CiBatchState, to_add: &[BatchPr], to_drop: &[BatchPr]) {
    println!("DRY RUN: no files, GitHub labels, or commits changed.");
    println!("Would write {}:", batch_path(root).display());
    println!("{}", batch_json(state));
    for pr in to_add {
        println!("Would add label {CI_BATCH_LABEL} to {} #{}.", pr.repo, pr.number);
    }
    for pr in to_drop {
        println!(
            "Would remove label {CI_BATCH_LABEL} from {} #{}.",
            pr.repo, pr.number
        );
    }
    println!("Would commit {CI_BATCH_STATE_PATH} to parent main and push origin HEAD:main.");
}

fn agent_tool(root: &Path) -> PathBuf {
    env::var_os("CI_HUB_AGENT_TOOL")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("ci-hub/bin/agent-tool"))
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

/// Resolve the validate ledger path, honoring an explicit override.
fn ledger_path(root: &Path, override_path: &Option<PathBuf>) -> PathBuf {
    override_path
        .clone()
        .unwrap_or_else(|| root.join(validate_status::LEDGER_REL))
}

/// Load and parse the validate ledger. A missing ledger is an empty history (a
/// commit is simply NOT validated), never an error. Unparseable lines are
/// skipped with a warning so one bad append never blinds the whole query.
fn load_ledger_rows(path: &Path) -> Result<Vec<HistoryRow>, CiHubError> {
    let buf = match std::fs::read_to_string(path) {
        Ok(buf) => buf,
        Err(error) if error.kind() == io::ErrorKind::NotFound => String::new(),
        Err(source) => {
            return Err(CiHubError::LedgerRead {
                path: path.to_path_buf(),
                source,
            })
        }
    };
    let (rows, skipped) = validate_status::parse_ledger(&buf);
    if skipped > 0 {
        eprintln!(
            "ci-hub: validate-status: skipped {skipped} unparseable ledger line(s) in {}",
            path.display()
        );
    }
    Ok(rows)
}

/// Read a PR's current head commit via gh (`with-proxy` when available).
fn gh_pr_head(root: &Path, repo: &str, pr: u64) -> Result<String, CiHubError> {
    let pr_arg = pr.to_string();
    let output = gh_command(
        root,
        &[
            "pr", "view", &pr_arg, "--repo", repo, "--json", "headRefOid", "-q", ".headRefOid",
        ],
    )
    .output()
    .map_err(|source| CiHubError::Launch {
        tool: "gh pr view".into(),
        source,
    })?;
    if !output.status.success() {
        return Err(CiHubError::Gh {
            context: format!("pr view #{pr}"),
            message: String::from_utf8_lossy(&output.stderr).trim().to_string(),
        });
    }
    let sha = String::from_utf8_lossy(&output.stdout).trim().to_ascii_lowercase();
    if sha.is_empty() {
        return Err(CiHubError::Gh {
            context: format!("pr view #{pr}"),
            message: "empty headRefOid".into(),
        });
    }
    Ok(sha)
}

/// List open PR numbers in the repo.
fn gh_open_prs(root: &Path, repo: &str) -> Result<Vec<u64>, CiHubError> {
    let output = gh_command(
        root,
        &[
            "pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json", "number",
            "-q", ".[].number",
        ],
    )
    .output()
    .map_err(|source| CiHubError::Launch {
        tool: "gh pr list".into(),
        source,
    })?;
    if !output.status.success() {
        return Err(CiHubError::Gh {
            context: "pr list".into(),
            message: String::from_utf8_lossy(&output.stderr).trim().to_string(),
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u64>().ok())
        .collect())
}

/// Read a PR's current label names.
fn gh_pr_labels(root: &Path, repo: &str, pr: u64) -> Result<Vec<String>, CiHubError> {
    let pr_arg = pr.to_string();
    let output = gh_command(
        root,
        &[
            "pr", "view", &pr_arg, "--repo", repo, "--json", "labels", "-q",
            ".labels[].name",
        ],
    )
    .output()
    .map_err(|source| CiHubError::Launch {
        tool: "gh pr view labels".into(),
        source,
    })?;
    if !output.status.success() {
        return Err(CiHubError::Gh {
            context: format!("pr view #{pr} labels"),
            message: String::from_utf8_lossy(&output.stderr).trim().to_string(),
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty())
        .collect())
}

const LOCALLY_VALIDATED_LABEL: &str = "locally-validated";

/// One qualifying record rendered for human/JSON output.
fn describe_record(row: &HistoryRow) -> serde_json::Value {
    serde_json::json!({
        "finished_at": row.finished_at,
        "host": row.host,
        "profile": row.profile,
        "selection_mode": row.selection_mode,
        "result": row.result,
        "real_seconds": row.real_seconds,
        "user_seconds": row.user_seconds,
        "sys_seconds": row.sys_seconds,
        "slot": row.slot,
    })
}

fn history_repo_path(root: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    }
}

fn fetch_history_ref(repo: &Path, main_ref: &str) -> Result<(), CiHubError> {
    let Some((remote, branch)) = main_ref.split_once('/') else {
        return Err(CiHubError::HistoryQuery(format!(
            "cannot refresh main ref '{main_ref}'; use REMOTE/BRANCH or pass --no-fetch"
        )));
    };
    let refspec = format!("refs/heads/{branch}:refs/remotes/{remote}/{branch}");
    let mut command = if on_path("with-proxy") {
        let mut command = Command::new("with-proxy");
        command.arg("git");
        command
    } else {
        Command::new("git")
    };
    let output = command
        .arg("-C")
        .arg(repo)
        .args(["fetch", "--quiet", remote, &refspec])
        .output()
        .map_err(|source| CiHubError::Launch {
            tool: "git fetch main for history query".into(),
            source,
        })?;
    if !output.status.success() {
        return Err(CiHubError::HistoryQuery(format!(
            "git fetch {remote}/{branch} exited {}: {}",
            exit_status_code(output.status),
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(())
}

fn main_history(repo: &Path, main_ref: &str) -> Result<Vec<String>, CiHubError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["rev-list", "--first-parent", main_ref])
        .output()
        .map_err(|source| CiHubError::Launch {
            tool: "git rev-list main for history query".into(),
            source,
        })?;
    if !output.status.success() {
        return Err(CiHubError::HistoryQuery(format!(
            "cannot walk {main_ref}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    let commits: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect();
    if commits.is_empty() {
        return Err(CiHubError::HistoryQuery(format!(
            "{main_ref} has no commits"
        )));
    }
    Ok(commits)
}

fn ledger_stamp(path: &Path) -> Result<(u64, u128), CiHubError> {
    let metadata = match std::fs::metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok((0, 0)),
        Err(source) => {
            return Err(CiHubError::LedgerRead {
                path: path.to_path_buf(),
                source,
            })
        }
    };
    let modified_ns = metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    Ok((metadata.len(), modified_ns))
}

fn read_newest_green_cache(path: &Path) -> Option<NewestGreenCache> {
    let raw = std::fs::read(path).ok()?;
    match serde_json::from_slice(&raw) {
        Ok(cache) => Some(cache),
        Err(error) => {
            eprintln!(
                "ci-hub: ignoring invalid newest-green cache {}: {error}",
                path.display()
            );
            None
        }
    }
}

fn write_newest_green_cache(path: &Path, cache: &NewestGreenCache) -> Result<(), CiHubError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|source| {
            CiHubError::HistoryQuery(format!(
                "cannot create cache directory {}: {source}",
                parent.display()
            ))
        })?;
    }
    let temporary = path.with_extension(format!("tmp.{}", std::process::id()));
    let mut bytes = serde_json::to_vec_pretty(cache)
        .map_err(|error| CiHubError::HistoryQuery(format!("serialize cache: {error}")))?;
    bytes.push(b'\n');
    std::fs::write(&temporary, bytes).map_err(|source| {
        CiHubError::HistoryQuery(format!("write cache {}: {source}", temporary.display()))
    })?;
    std::fs::rename(&temporary, path).map_err(|source| {
        CiHubError::HistoryQuery(format!(
            "replace cache {} with {}: {source}",
            path.display(),
            temporary.display()
        ))
    })
}

fn print_newest_green(report: &history_queries::NewestGreenReport, cache_hit: bool, json: bool) {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "cache_hit": cache_hit,
                "report": report,
            }))
            .expect("serialize newest-green report")
        );
        return;
    }
    println!(
        "NEWEST-GREEN {} validated={} profile={} selection={} guarantee={}",
        report.green.sha,
        report.green.finished_at.as_deref().unwrap_or("unknown"),
        report.green.profile,
        report.green.selection_mode,
        report.green.coverage.as_str(),
    );
    println!(
        "MAIN-TIP {} commits-after-green={} recorded={} no-record={} cache={}",
        report.main_tip,
        report.commits_after_green,
        report.commits_with_records,
        report.commits_without_any_record,
        if cache_hit { "hit" } else { "miss" },
    );
    if report.green.coverage != history_queries::CoverageStrength::Full {
        println!(
            "WEAKER-GUARANTEE: this was not a full-profile/full-selection validation; rebase decisions inherit only the stated {} evidence",
            report.green.coverage.as_str()
        );
    }
}

fn run_newest_green_main(root: &Path, args: NewestGreenMainArgs) -> Result<i32, CiHubError> {
    let repo = history_repo_path(root, &args.query.repo_dir);
    if !args.query.no_fetch {
        fetch_history_ref(&repo, &args.query.main_ref)?;
    }
    let commits = main_history(&repo, &args.query.main_ref)?;
    let main_tip = commits.first().expect("nonempty history");
    let ledger = ledger_path(root, &args.query.ledger);
    let (ledger_len, ledger_modified_ns) = ledger_stamp(&ledger)?;
    let cache_path = history_queries::cache_path(root, &args.cache);
    if !args.no_cache {
        if let Some(cache) = read_newest_green_cache(&cache_path) {
            if history_queries::cache_matches(
                &cache,
                main_tip,
                &args.query.main_ref,
                &ledger,
                ledger_len,
                ledger_modified_ns,
            ) {
                print_newest_green(&cache.report, true, args.query.json);
                return Ok(0);
            }
        }
    }

    let rows = load_ledger_rows(&ledger)?;
    match HistoryQueryEngine::new(commits, rows).newest_green(&args.query.main_ref) {
        NewestGreenOutcome::Found(report) => {
            let cache = NewestGreenCache {
                schema_version: 1,
                main_tip: report.main_tip.clone(),
                main_ref: args.query.main_ref,
                ledger_path: ledger.display().to_string(),
                ledger_len,
                ledger_modified_ns,
                report: report.clone(),
            };
            write_newest_green_cache(&cache_path, &cache)?;
            print_newest_green(&report, false, args.query.json);
            Ok(0)
        }
        NewestGreenOutcome::FailedOnly { main_tip, recorded } => {
            if args.query.json {
                println!("{}", serde_json::json!({"schema_version": 1, "verdict": "FAILED", "exit_code": 3, "main_tip": main_tip, "trustworthy_recorded_commits": recorded}));
            } else {
                println!("NEWEST-GREEN FAILED main-tip={main_tip} -- {recorded} main commit(s) have clean anchored records, but none has a latest PASS");
            }
            Ok(3)
        }
        NewestGreenOutcome::NoEvidence { main_tip } => {
            if args.query.json {
                println!("{}", serde_json::json!({"schema_version": 1, "verdict": "NOT-VALIDATED", "exit_code": 4, "main_tip": main_tip}));
            } else {
                println!("NEWEST-GREEN NOT-VALIDATED main-tip={main_tip} -- no clean commit-anchored main validation record exists");
            }
            Ok(4)
        }
    }
}

fn files_touched(repo: &Path, sha: &str) -> Result<Vec<String>, CiHubError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha])
        .output()
        .map_err(|source| CiHubError::Launch {
            tool: "git diff-tree first-bad".into(),
            source,
        })?;
    if !output.status.success() {
        return Err(CiHubError::HistoryQuery(format!(
            "cannot inspect files at {sha}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect())
}

fn assess_diff_plausibility(files: &[String], source_node: Option<&str>) -> String {
    if files.is_empty() {
        return "unknown: candidate commit has no retained file list".into();
    }
    if files.iter().all(|path| path.starts_with(".github/")) {
        return format!(
            "implausible: the commit changes only GitHub workflow files, which local validate{} does not execute",
            source_node.map(|node| format!(" cell {node}")) .unwrap_or_default()
        );
    }
    if files.iter().all(|path| {
        path.ends_with(".md")
            || path.starts_with("docs/")
            || path.starts_with("ai_docs/")
            || path.starts_with("experiments/")
    }) {
        return "implausible: the commit changes only documentation/evidence paths excluded by the test-footprint policy".into();
    }
    "not exonerated by the file-only check; inspect the listed paths against the cell's test footprint".into()
}

fn print_first_bad(report: &history_queries::FirstBadReport, json: bool) {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(report).expect("serialize first-bad report")
        );
        return;
    }
    println!(
        "FIRST-BAD {} cell={} observed={} profile={} selection={}",
        report.first_bad.sha,
        report.matched_name,
        report.first_bad.finished_at.as_deref().unwrap_or("unknown"),
        report.first_bad.profile,
        report.first_bad.selection_mode,
    );
    println!(
        "LAST-GOOD {} observed={} commits-between={} no-cell-record={}",
        report.last_good.sha,
        report.last_good.finished_at.as_deref().unwrap_or("unknown"),
        report.commits_between,
        report.commits_without_cell_record,
    );
    println!("FILES-TOUCHED {}", if report.files_touched.is_empty() { "(none)".into() } else { report.files_touched.join(" ") });
    println!("PLAUSIBILITY {}", report.plausibility);
    println!(
        "LOAD-CONTEXT {}",
        report.load_context.as_deref().unwrap_or("not retained")
    );
    if report.error_excerpt.is_empty() {
        println!("ERROR-DETAIL not retained (ledger has the outcome but its referenced log is absent or has no canonical error line)");
    } else {
        println!("ERROR-DETAIL");
        for line in &report.error_excerpt {
            println!("  {line}");
        }
    }
}

fn run_first_bad(root: &Path, args: FirstBadArgs) -> Result<i32, CiHubError> {
    let repo = history_repo_path(root, &args.query.repo_dir);
    if !args.query.no_fetch {
        fetch_history_ref(&repo, &args.query.main_ref)?;
    }
    let commits = main_history(&repo, &args.query.main_ref)?;
    let main_set: std::collections::BTreeSet<&str> =
        commits.iter().map(String::as_str).collect();
    let ledger = ledger_path(root, &args.query.ledger);
    let mut rows = load_ledger_rows(&ledger)?;
    rows.retain(|row| {
        row.commit
            .as_deref()
            .map(|commit| main_set.contains(commit))
            .unwrap_or(false)
    });
    history_queries::enrich_rows_from_logs(&mut rows);
    match HistoryQueryEngine::new(commits, rows).first_bad(&args.cell_or_gate) {
        FirstBadOutcome::Found(mut report) => {
            report.files_touched = files_touched(&repo, &report.first_bad.sha)?;
            report.plausibility = assess_diff_plausibility(
                &report.files_touched,
                report.source_node.as_deref(),
            );
            print_first_bad(&report, args.query.json);
            Ok(0)
        }
        FirstBadOutcome::FailureWithoutKnownGood {
            query,
            matched_name,
            failure,
        } => {
            if args.query.json {
                println!("{}", serde_json::json!({"schema_version": 1, "verdict": "FAILED", "exit_code": 3, "query": query, "matched_name": matched_name, "failure": failure, "reason": "failure exists but no earlier PASS is retained"}));
            } else {
                println!("FIRST-BAD FAILED cell={matched_name} sha={} -- failure exists but no earlier PASS is retained", failure.sha);
            }
            Ok(3)
        }
        FirstBadOutcome::NoEvidence { query, available_names } => {
            if args.query.json {
                println!("{}", serde_json::json!({"schema_version": 1, "verdict": "NOT-VALIDATED", "exit_code": 4, "query": query, "suggestions": available_names, "reason": "no retained cell/gate record"}));
            } else {
                println!("FIRST-BAD NOT-VALIDATED cell={query} -- no retained cell/gate record; absence is not PASS");
                if !available_names.is_empty() {
                    println!("SUGGESTIONS {}", available_names.join(" | "));
                }
            }
            Ok(4)
        }
        FirstBadOutcome::NoTransition { query, matched_name, observations } => {
            if args.query.json {
                println!("{}", serde_json::json!({"schema_version": 1, "verdict": "NOT-VALIDATED", "exit_code": 4, "query": query, "matched_name": matched_name, "observations": observations, "reason": "no retained PASS-to-FAIL transition"}));
            } else {
                println!("FIRST-BAD NOT-VALIDATED cell={matched_name} observations={observations} -- no retained PASS-to-FAIL transition");
            }
            Ok(4)
        }
    }
}

/// `ci-hub validate-status --sha <SHA> | --pr <N>` — the SHA-queryable landing /
/// cache predicate. Exit 0 VALIDATED, 3 FAILED (known-bad), 4 NOT-VALIDATED.
fn run_validate_status(root: &Path, args: ValidateStatusArgs) -> Result<i32, CiHubError> {
    let path = ledger_path(root, &args.ledger);
    let rows = load_ledger_rows(&path)?;
    let sha = match (&args.sha, args.pr) {
        (Some(input), None) => {
            validate_status::resolve_sha(&rows, input).map_err(CiHubError::ValidateStatus)?
        }
        (None, Some(pr)) => gh_pr_head(root, &args.repo, pr)?,
        _ => {
            return Err(CiHubError::ValidateStatus(
                "exactly one of --sha or --pr is required".into(),
            ))
        }
    };
    let assessment = validate_status::assess(&rows, &sha);
    let newest = validate_status::newest(&assessment.qualifying);

    if args.json {
        let report = serde_json::json!({
            "schema_version": 1,
            "sha": assessment.sha,
            "verdict": assessment.verdict.as_str(),
            "exit_code": assessment.verdict.exit_code(),
            "qualifying_count": assessment.qualifying.len(),
            "disqualified_count": assessment.disqualified.len(),
            "newest_qualifying": newest.map(describe_record),
            "ledger": path.display().to_string(),
        });
        println!("{}", serde_json::to_string_pretty(&report).expect("serialize report"));
    } else {
        match assessment.verdict {
            validate_status::Verdict::Validated => {
                let row = newest.expect("validated implies a qualifying record");
                println!(
                    "# validate VALIDATED {} (passed {}, wall {}s, host {}, profile full/full) -- clean-tree commit-anchored full run",
                    assessment.sha,
                    row.finished_at.as_deref().unwrap_or("?"),
                    row.real_seconds.map(|s| s.round() as i64).unwrap_or(-1),
                    row.host.as_deref().unwrap_or("?"),
                );
            }
            validate_status::Verdict::FailedOnRecord => {
                println!(
                    "# validate FAILED {} -- a clean full-coverage run exists but did NOT pass ({} record(s)); this commit is known-failing",
                    assessment.sha,
                    assessment.disqualified.len(),
                );
            }
            validate_status::Verdict::NotValidated => {
                println!(
                    "# validate NOT-VALIDATED {} -- no clean full-coverage PASS record ({} non-qualifying record(s) for this commit)",
                    assessment.sha,
                    assessment.disqualified.len(),
                );
            }
        }
    }
    Ok(assessment.verdict.exit_code())
}

/// `ci-hub apply-local-label --pr <N> | --all-open` — close the retroactive gap:
/// when a commit was validated BEFORE its PR existed, validate.sh could not stamp
/// `locally-validated`. This applies it to any PR whose head has a clean
/// full-validate record and lacks the label. Never fabricates the label: a PR
/// whose head is not VALIDATED is left untouched.
fn run_apply_local_label(root: &Path, args: ApplyLocalLabelArgs) -> Result<i32, CiHubError> {
    let path = ledger_path(root, &args.ledger);
    let rows = load_ledger_rows(&path)?;
    let prs = match (args.pr, args.all_open) {
        (Some(pr), false) => vec![pr],
        (None, true) => gh_open_prs(root, &args.repo)?,
        _ => {
            return Err(CiHubError::ValidateStatus(
                "exactly one of --pr or --all-open is required".into(),
            ))
        }
    };

    let mut actions: Vec<serde_json::Value> = Vec::new();
    let mut applied = 0i32;
    for pr in prs {
        // In sweep mode a single unreadable PR must not abort the whole run.
        let head = match gh_pr_head(root, &args.repo, pr) {
            Ok(head) => head,
            Err(error) => {
                eprintln!("ci-hub: apply-local-label: PR #{pr}: {error}");
                actions.push(serde_json::json!({"pr": pr, "action": "error", "detail": error.to_string()}));
                continue;
            }
        };
        let verdict = validate_status::assess(&rows, &head).verdict;
        if verdict != validate_status::Verdict::Validated {
            println!("PR #{pr}: skip -- head {} is {}", &head[..12.min(head.len())], verdict.as_str());
            actions.push(serde_json::json!({"pr": pr, "head": head, "action": "skip", "verdict": verdict.as_str()}));
            continue;
        }
        let labels = gh_pr_labels(root, &args.repo, pr).unwrap_or_default();
        if labels.iter().any(|l| l == LOCALLY_VALIDATED_LABEL) {
            println!("PR #{pr}: already labeled (head validated)");
            actions.push(serde_json::json!({"pr": pr, "head": head, "action": "already-labeled"}));
            continue;
        }
        if args.dry_run {
            println!("PR #{pr}: WOULD add {LOCALLY_VALIDATED_LABEL} (head validated)");
            actions.push(serde_json::json!({"pr": pr, "head": head, "action": "would-add"}));
            continue;
        }
        let pr_arg = pr.to_string();
        let status = gh_command(
            root,
            &[
                "pr", "edit", &pr_arg, "--repo", &args.repo, "--add-label",
                LOCALLY_VALIDATED_LABEL,
            ],
        )
        .status()
        .map_err(|source| CiHubError::Launch {
            tool: "gh pr edit".into(),
            source,
        })?;
        if status.success() {
            println!("PR #{pr}: applied {LOCALLY_VALIDATED_LABEL} (head validated)");
            actions.push(serde_json::json!({"pr": pr, "head": head, "action": "added"}));
            applied += 1;
        } else {
            eprintln!("ci-hub: apply-local-label: PR #{pr}: gh pr edit exited nonzero");
            actions.push(serde_json::json!({"pr": pr, "head": head, "action": "edit-failed"}));
        }
    }

    if args.json {
        let report = serde_json::json!({"schema_version": 1, "applied": applied, "actions": actions});
        println!("{}", serde_json::to_string_pretty(&report).expect("serialize report"));
    }
    Ok(0)
}

fn print_obligations(root: &Path, args: ObligationsArgs) -> Result<i32, CiHubError> {
    let store = args
        .store
        .or_else(|| env::var_os("CI_HUB_OBLIGATIONS_STORE").map(PathBuf::from))
        .unwrap_or_else(|| root.join("ignored/ci-hub/obligations.jsonl"));
    let mut records = latest_obligations(&store)?;
    if args.actionable {
        records.retain(|record| record.remediation_required());
    }
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
    let sent_unacknowledged = remediation
        .iter()
        .filter(|record| record.dispatch_state() == "sent_unacknowledged")
        .count();
    let acknowledged = remediation
        .iter()
        .filter(|record| record.dispatch_state() == "acknowledged")
        .count();

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
        println!("sent_unacknowledged_count={sent_unacknowledged}");
        println!("acknowledged_count={acknowledged}");
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
        "{} {}@{} overall={} local={} github={} recommendation={} dispatch={}",
        record.obligation_id,
        record.repo,
        &record.landed_sha[..record.landed_sha.len().min(12)],
        record.overall_state,
        record.local.state,
        record.github.state,
        record.recommendation_action(),
        record.dispatch_state()
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
    fn parses_typed_active_work_command() {
        let command = Cli::try_parse_from([
            "ci-hub",
            "active-work",
            "--agent-snapshot",
            "/tmp/agents.json",
            "--max-snapshot-age",
            "300",
            "--json",
        ])
        .unwrap()
        .command;
        let HubCommand::ActiveWork(args) = command else {
            panic!("wrong command variant")
        };
        assert_eq!(args.agent_snapshot, Some(PathBuf::from("/tmp/agents.json")));
        assert_eq!(args.max_snapshot_age, 300);
        assert!(args.json);
    }

    #[test]
    fn parses_typed_validate_worktrees_command() {
        let command = Cli::try_parse_from([
            "ci-hub",
            "validate-worktrees",
            "--runs",
            "10",
            "--stale-hours",
            "6",
            "--data-dir",
            "/tmp/ci-hub",
            "--json",
        ])
        .unwrap()
        .command;
        let HubCommand::ValidateWorktrees(args) = command else {
            panic!("wrong command variant")
        };
        assert_eq!(args.runs, 10);
        assert_eq!(args.stale_hours, 6.0);
        assert_eq!(args.data_dir, Some(PathBuf::from("/tmp/ci-hub")));
        assert!(args.json);
    }

    #[test]
    fn parses_typed_load_probe_command() {
        let command = Cli::try_parse_from([
            "ci-hub",
            "load-probe",
            "--sample-seconds",
            "0.5",
            "--max-executing-percent",
            "40",
            "--top",
            "3",
            "--json",
        ])
        .unwrap()
        .command;
        let HubCommand::LoadProbe(args) = command else {
            panic!("wrong command variant")
        };
        assert_eq!(args.sample_seconds, 0.5);
        assert_eq!(args.max_executing_percent, 40.0);
        assert_eq!(args.top, 3);
        assert!(args.json);
        assert!(HubCommand::LoadProbe(args).cost_spec().is_some());
    }

    #[test]
    fn parses_shared_history_query_commands() {
        let newest = Cli::try_parse_from([
            "ci-hub",
            "newest-green-main",
            "--no-fetch",
            "--no-cache",
            "--json",
        ])
        .unwrap()
        .command;
        let HubCommand::NewestGreenMain(args) = newest else {
            panic!("wrong command variant")
        };
        assert!(args.query.no_fetch);
        assert!(args.no_cache);
        assert!(args.query.json);
        assert!(HubCommand::NewestGreenMain(args).cost_spec().is_some());

        let first_bad = Cli::try_parse_from([
            "ci-hub",
            "first-bad",
            "test.detcore_misc",
            "--no-fetch",
        ])
        .unwrap()
        .command;
        let HubCommand::FirstBad(args) = first_bad else {
            panic!("wrong command variant")
        };
        assert_eq!(args.cell_or_gate, "test.detcore_misc");
        assert!(args.query.no_fetch);
        assert!(HubCommand::FirstBad(args).cost_spec().is_some());
    }

    #[test]
    fn quickstart_is_short_pure_agent_workflow() {
        let command = Cli::try_parse_from(["ci-hub", "quickstart"])
            .unwrap()
            .command;
        assert!(matches!(command, HubCommand::Quickstart));
        assert!(AGENT_QUICKSTART.starts_with("ci-hub agent quickstart\n"));
        assert!(AGENT_QUICKSTART.contains("ci-hub/ci-hub health"));
        assert!(AGENT_QUICKSTART.contains("validate-worktrees"));
        assert!(AGENT_QUICKSTART.contains("newest-green-main"));
        assert!(AGENT_QUICKSTART.contains("first-bad CELL_OR_GATE"));
        assert!(AGENT_QUICKSTART.contains("obligations --actionable"));
        assert!(AGENT_QUICKSTART.contains("land-lock run"));
        assert!(AGENT_QUICKSTART.lines().count() < 45);
    }

    #[test]
    fn trivial_reads_have_no_cost_wrapper() {
        let obligations = Cli::try_parse_from(["ci-hub", "obligations"])
            .unwrap()
            .command;
        assert!(obligations.cost_spec().is_none());
        let inherit = Cli::try_parse_from([
            "ci-hub",
            "inherit-obligations",
            "--agent",
            "hermit-lander",
            "--session",
            "session-1",
        ])
        .unwrap()
        .command;
        assert!(inherit.cost_spec().is_none());
        let wake = Cli::try_parse_from([
            "ci-hub",
            "record-obligation-wake",
            "--target",
            "hermit-lander",
        ])
        .unwrap()
        .command;
        assert!(wake.cost_spec().is_none());
        let status = Cli::try_parse_from(["ci-hub", "land-lock", "status"])
            .unwrap()
            .command;
        assert!(status.cost_spec().is_none());
        let worktrees = Cli::try_parse_from(["ci-hub", "validate-worktrees"])
            .unwrap()
            .command;
        assert!(worktrees.cost_spec().is_none());
    }

    #[test]
    fn parses_typed_ci_mode_commands() {
        let set = Cli::try_parse_from([
            "ci-hub",
            "ci-mode",
            "set",
            "constrained",
            "--reason",
            "queued=6 max-age=2h03m",
            "--dry-run",
        ])
        .unwrap()
        .command;
        let HubCommand::CiMode(args) = set else {
            panic!("wrong command variant")
        };
        let CiModeCommand::Set(set_args) = args.command else {
            panic!("wrong subcommand variant")
        };
        assert!(matches!(set_args.mode, CiModeValue::Constrained));
        assert_eq!(set_args.reason, "queued=6 max-age=2h03m");
        assert!(set_args.dry_run);

        let fire = Cli::try_parse_from([
            "ci-hub",
            "ci-mode",
            "fire",
            "--pr",
            "1563",
            "--lane",
            "privileged",
        ])
        .unwrap()
        .command;
        let HubCommand::CiMode(args) = fire else {
            panic!("wrong command variant")
        };
        let CiModeCommand::Fire(fire_args) = args.command.clone() else {
            panic!("wrong subcommand variant")
        };
        assert_eq!(fire_args.pr, 1563);
        assert!(matches!(fire_args.lane, CiModeLane::Privileged));
        assert_eq!(fire_args.repo, "rrnewton/hermit");

        // ci-mode is a trivial local read/write dispatcher: no cost wrapper.
        assert!(HubCommand::CiMode(args).cost_spec().is_none());
    }

    #[test]
    fn parses_typed_batch_commands() {
        let set = Cli::try_parse_from([
            "ci-hub",
            "batch",
            "set",
            "cpu-timeout-landing",
            "--reason",
            "priority: land cpu_timeout chain",
            "--pr",
            "1566",
            "--pr",
            "1568",
        ])
        .unwrap()
        .command;
        let HubCommand::Batch(args) = set else {
            panic!("wrong command variant")
        };
        let BatchCommand::Set(set_args) = args.command else {
            panic!("wrong subcommand variant")
        };
        assert_eq!(set_args.name, "cpu-timeout-landing");
        assert_eq!(set_args.repo, CI_BATCH_DEFAULT_REPO);
        assert_eq!(set_args.prs, vec![1566, 1568]);
        assert!(!set_args.dry_run);

        let add = Cli::try_parse_from([
            "ci-hub",
            "batch",
            "add",
            "--repo",
            "rrnewton/reverie",
            "--pr",
            "42",
        ])
        .unwrap()
        .command;
        let HubCommand::Batch(args) = add else {
            panic!("wrong command variant")
        };
        let BatchCommand::Add(member_args) = args.command.clone() else {
            panic!("wrong subcommand variant")
        };
        assert_eq!(member_args.repo, "rrnewton/reverie");
        assert_eq!(member_args.prs, vec![42]);

        // batch is a local read/write dispatcher: no cost wrapper, like ci-mode.
        assert!(HubCommand::Batch(args).cost_spec().is_none());

        // members_from deduplicates a repeated --pr within one repo.
        let members = members_from("rrnewton/hermit", &[7, 7, 9]);
        assert_eq!(members.len(), 2);
        assert_eq!(members[0].number, 7);
        assert_eq!(members[1].number, 9);
    }

    #[test]
    fn substantive_network_work_is_costed() {
        let command = Cli::try_parse_from(["ci-hub", "main-health"])
            .unwrap()
            .command;
        assert!(command.cost_spec().is_some());
    }
}
