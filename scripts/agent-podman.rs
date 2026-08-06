#!/usr/bin/env rust-script
//! Run and reconcile Podman containers with durable agent ownership.
//!
//! Containers created through this tool are labelled with the creating agent,
//! exact agent invocation, task, and lifetime.  The operational reconciler may
//! remove only an `agent`-lifetime container whose exact invocation is gone.
//! Task-retained and legacy unlabelled containers are report-only.
//!
//! ```cargo
//! [dependencies]
//! fs2 = "0.4"
//! serde = { version = "1", features = ["derive"] }
//! serde_json = "1"
//! ```

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{exit, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MANAGED_LABEL: &str = "io.dev-hermit.agent-podman";
const MANAGED_VERSION: &str = "v1";
const AGENT_LABEL: &str = "io.dev-hermit.owner-agent";
const INVOCATION_LABEL: &str = "io.dev-hermit.owner-invocation";
const PANE_LABEL: &str = "io.dev-hermit.owner-pane";
const TASK_LABEL: &str = "io.dev-hermit.owner-task";
const LIFETIME_LABEL: &str = "io.dev-hermit.lifetime";
const SNAPSHOT_MAX_AGE_SECS: u64 = 10 * 60;
const QUERY_TIMEOUT_SECS: &str = "3s";
const EVIDENCE_TIMEOUT_SECS: &str = "1s";
const STOP_TIMEOUT_SECS: &str = "13s";
const REMOVE_TIMEOUT_SECS: &str = "3s";
const MAX_PROCESS_EVIDENCE: usize = 10;

const USAGE: &str = r#"Usage: agent-podman.rs COMMAND [OPTIONS]

Commands:
  quickstart
      Print the short agent workflow. Pure: no Podman or filesystem access.

  run --task TASK [--lifetime agent|task] -- PODMAN-RUN-ARGS...
  create --task TASK [--lifetime agent|task] -- PODMAN-CREATE-ARGS...
      Run `podman run` or `podman create` with durable ownership labels.
      Lifetime defaults to `agent`; use `task` only for an environment that
      must survive creator recycling and will be explicitly transferred.

  transfer CONTAINER --task TASK [--lifetime agent|task]
      Claim a managed container for the current agent invocation. This is the
      only supported handoff for a task-retained container.

  audit --agents-json PATH [--live-invocations PATH] [--json]
      Classify managed, task-retained, and legacy containers. Never mutates.

  reconcile --agents-json PATH [--live-invocations PATH] [--json] [--apply]
      With --apply, gracefully stop and remove only managed agent-lifetime
      containers whose exact owner invocation is gone. Never force-removes,
      and never removes task-retained or unlabelled containers.

Environment:
  DG_AGENT_NAME              Required for run/create/transfer.
  META_3PAI_INVOCATION_ID    Exact agent incarnation (CODING_AGENT_METADATA is
                             accepted as a fallback).
  TMUX_PANE                  Owning pane recorded for diagnosis.
  DEV_HERMIT_CONTAINER_STATE Override the ignored ownership registry path.
  AGENT_PODMAN_BIN           Override podman (tests only).
"#;

const QUICKSTART: &str = r#"agent-podman quickstart

1. Create every agent container through this wrapper, not raw `podman run`:
   ./scripts/agent-podman.rs run --task TASK -- --rm IMAGE COMMAND
2. Detached environments default to the agent's lifetime and are reclaimed
   after that exact agent invocation disappears:
   ./scripts/agent-podman.rs run --task TASK -- -d --name NAME IMAGE COMMAND
3. Use `--lifetime task` only when the environment must outlive the creator.
   Its next owner must run `transfer NAME --task TASK`; it is never auto-reaped.
4. Inspect without mutation:
   ./scripts/agent-podman.rs audit --agents-json ignored/ci-hub/agent-snapshot.json
5. Legacy unlabelled containers are warnings, never automatic deletion targets.
"#;

#[derive(Debug)]
enum AppError {
    Usage(String),
    Message(String),
    Io {
        context: String,
        source: io::Error,
    },
    Json {
        context: String,
        source: serde_json::Error,
    },
}

impl std::fmt::Display for AppError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Usage(message) | Self::Message(message) => write!(formatter, "{message}"),
            Self::Io { context, source } => write!(formatter, "{context}: {source}"),
            Self::Json { context, source } => write!(formatter, "{context}: {source}"),
        }
    }
}

type Result<T> = std::result::Result<T, AppError>;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
enum Lifetime {
    Agent,
    Task,
}

impl Lifetime {
    fn parse(value: &str) -> Result<Self> {
        match value {
            "agent" => Ok(Self::Agent),
            "task" => Ok(Self::Task),
            _ => Err(AppError::Usage(format!(
                "invalid lifetime {value:?}; expected agent or task"
            ))),
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Agent => "agent",
            Self::Task => "task",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
struct Ownership {
    container_id: String,
    owner_agent: String,
    owner_invocation: String,
    owner_pane: String,
    task: String,
    lifetime: Lifetime,
    updated_at: u64,
}

#[derive(Debug, Deserialize, Serialize)]
struct Registry {
    schema_version: u8,
    containers: BTreeMap<String, Ownership>,
}

impl Default for Registry {
    fn default() -> Self {
        Self {
            schema_version: 1,
            containers: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug)]
struct AgentIdentity {
    name: String,
    invocation: String,
    pane: String,
}

#[derive(Clone, Debug)]
struct Container {
    id: String,
    name: String,
    state: String,
    labels: BTreeMap<String, String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
enum Disposition {
    Live,
    Reclaimable,
    TransferRequired,
    OwnerUnknown,
    Unmanaged,
}

#[derive(Debug, Serialize)]
struct ContainerReport {
    id: String,
    name: String,
    state: String,
    disposition: Disposition,
    owner_agent: Option<String>,
    owner_invocation: Option<String>,
    task: Option<String>,
    lifetime: Option<Lifetime>,
    namespace: Option<String>,
    zombies: usize,
    action: String,
}

#[derive(Debug, Serialize)]
struct ReconcileReport {
    state: String,
    managed: usize,
    live: usize,
    reclaimed: usize,
    reclaimable: usize,
    transfer_required: usize,
    owner_unknown: usize,
    unmanaged: usize,
    zombie_containers: usize,
    zombies: usize,
    details: Vec<ContainerReport>,
    errors: Vec<String>,
}

#[derive(Debug)]
enum Cli {
    Quickstart,
    Launch {
        mode: String,
        task: String,
        lifetime: Lifetime,
        arguments: Vec<String>,
    },
    Transfer {
        container: String,
        task: String,
        lifetime: Lifetime,
    },
    Audit {
        agents_json: PathBuf,
        live_invocations: Option<PathBuf>,
        json: bool,
        apply: bool,
    },
}

fn main() {
    match run(env::args().skip(1).collect()) {
        Ok(code) => exit(code),
        Err(AppError::Usage(message)) => {
            eprintln!("agent-podman: {message}\n\n{USAGE}");
            exit(2);
        }
        Err(error) => {
            eprintln!("agent-podman: {error}");
            exit(1);
        }
    }
}

fn run(arguments: Vec<String>) -> Result<i32> {
    let command = parse_cli(arguments)?;
    if env::var_os("CI_HUB_DOCS_PARSE_ONLY").is_some() && !matches!(command, Cli::Quickstart) {
        let name = match &command {
            Cli::Quickstart => unreachable!(),
            Cli::Launch { mode, .. } => mode.as_str(),
            Cli::Transfer { .. } => "transfer",
            Cli::Audit { apply: true, .. } => "reconcile",
            Cli::Audit { apply: false, .. } => "audit",
        };
        println!("state=parse-ok command={name} summary=arguments-valid-no-action-taken");
        return Ok(0);
    }
    match command {
        Cli::Quickstart => {
            print!("{QUICKSTART}");
            Ok(0)
        }
        Cli::Launch {
            mode,
            task,
            lifetime,
            arguments,
        } => launch(&mode, &task, lifetime, &arguments),
        Cli::Transfer {
            container,
            task,
            lifetime,
        } => transfer(&container, &task, lifetime),
        Cli::Audit {
            agents_json,
            live_invocations,
            json,
            apply,
        } => reconcile(&agents_json, live_invocations.as_deref(), json, apply),
    }
}

fn parse_cli(arguments: Vec<String>) -> Result<Cli> {
    let Some(command) = arguments.first().map(String::as_str) else {
        return Err(AppError::Usage("missing command".to_string()));
    };
    if matches!(command, "-h" | "--help" | "help") {
        print!("{USAGE}");
        exit(0);
    }
    if command == "quickstart" {
        if arguments.len() != 1 {
            return Err(AppError::Usage(
                "quickstart accepts no arguments".to_string(),
            ));
        }
        return Ok(Cli::Quickstart);
    }
    if matches!(command, "run" | "create") {
        return parse_launch(command, &arguments[1..]);
    }
    if command == "transfer" {
        return parse_transfer(&arguments[1..]);
    }
    if matches!(command, "audit" | "reconcile") {
        return parse_audit(command, &arguments[1..]);
    }
    Err(AppError::Usage(format!("unknown command {command:?}")))
}

fn parse_launch(mode: &str, arguments: &[String]) -> Result<Cli> {
    let mut task = None;
    let mut lifetime = Lifetime::Agent;
    let mut index = 0;
    while index < arguments.len() {
        match arguments[index].as_str() {
            "--task" => {
                index += 1;
                task = arguments.get(index).cloned();
            }
            "--lifetime" => {
                index += 1;
                lifetime = Lifetime::parse(
                    arguments
                        .get(index)
                        .ok_or_else(|| AppError::Usage("--lifetime needs a value".to_string()))?,
                )?;
            }
            "--" => {
                let podman_arguments = arguments[index + 1..].to_vec();
                if podman_arguments.is_empty() {
                    return Err(AppError::Usage("podman arguments are empty".to_string()));
                }
                reject_reserved_arguments(&podman_arguments)?;
                return Ok(Cli::Launch {
                    mode: mode.to_string(),
                    task: required_nonempty("--task", task)?,
                    lifetime,
                    arguments: podman_arguments,
                });
            }
            value => return Err(AppError::Usage(format!("unexpected option {value:?}"))),
        }
        index += 1;
    }
    Err(AppError::Usage(
        "run/create needs `-- PODMAN-ARGS...`".to_string(),
    ))
}

fn parse_transfer(arguments: &[String]) -> Result<Cli> {
    let container = arguments
        .first()
        .filter(|value| !value.starts_with('-'))
        .cloned()
        .ok_or_else(|| AppError::Usage("transfer needs CONTAINER".to_string()))?;
    let mut task = None;
    let mut lifetime = Lifetime::Task;
    let mut index = 1;
    while index < arguments.len() {
        match arguments[index].as_str() {
            "--task" => {
                index += 1;
                task = arguments.get(index).cloned();
            }
            "--lifetime" => {
                index += 1;
                lifetime = Lifetime::parse(
                    arguments
                        .get(index)
                        .ok_or_else(|| AppError::Usage("--lifetime needs a value".to_string()))?,
                )?;
            }
            value => return Err(AppError::Usage(format!("unexpected option {value:?}"))),
        }
        index += 1;
    }
    Ok(Cli::Transfer {
        container,
        task: required_nonempty("--task", task)?,
        lifetime,
    })
}

fn parse_audit(command: &str, arguments: &[String]) -> Result<Cli> {
    let mut agents_json = None;
    let mut live_invocations = None;
    let mut json = false;
    let mut apply = false;
    let mut index = 0;
    while index < arguments.len() {
        match arguments[index].as_str() {
            "--agents-json" => {
                index += 1;
                agents_json = arguments.get(index).map(PathBuf::from);
            }
            "--live-invocations" => {
                index += 1;
                live_invocations = arguments.get(index).map(PathBuf::from);
            }
            "--json" => json = true,
            "--apply" if command == "reconcile" => apply = true,
            "--apply" => {
                return Err(AppError::Usage(
                    "audit is read-only; use reconcile --apply".to_string(),
                ))
            }
            value => return Err(AppError::Usage(format!("unexpected option {value:?}"))),
        }
        index += 1;
    }
    Ok(Cli::Audit {
        agents_json: agents_json
            .ok_or_else(|| AppError::Usage("--agents-json is required".to_string()))?,
        live_invocations,
        json,
        apply,
    })
}

fn required_nonempty(name: &str, value: Option<String>) -> Result<String> {
    value
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| AppError::Usage(format!("{name} is required")))
}

fn reject_reserved_arguments(arguments: &[String]) -> Result<()> {
    let reserved_labels = [
        MANAGED_LABEL,
        AGENT_LABEL,
        INVOCATION_LABEL,
        PANE_LABEL,
        TASK_LABEL,
        LIFETIME_LABEL,
    ];
    for (index, argument) in arguments.iter().enumerate() {
        if argument == "--cidfile" || argument.starts_with("--cidfile=") {
            return Err(AppError::Usage(
                "--cidfile is reserved for lifecycle registration".to_string(),
            ));
        }
        if reserved_labels.iter().any(|key| argument.contains(key))
            && (argument == "--label"
                || argument.starts_with("--label=")
                || index > 0 && arguments[index - 1] == "--label")
        {
            return Err(AppError::Usage(
                "io.dev-hermit lifecycle labels are reserved".to_string(),
            ));
        }
    }
    Ok(())
}

fn current_identity() -> Result<AgentIdentity> {
    let name = env::var("DG_AGENT_NAME")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| AppError::Message("DG_AGENT_NAME is required".to_string()))?;
    let invocation = env::var("META_3PAI_INVOCATION_ID")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(invocation_from_agent_metadata)
        .ok_or_else(|| {
            AppError::Message(
                "META_3PAI_INVOCATION_ID or CODING_AGENT_METADATA invocation_id is required"
                    .to_string(),
            )
        })?;
    let pane = env::var("TMUX_PANE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "outside-tmux".to_string());
    Ok(AgentIdentity {
        name,
        invocation,
        pane,
    })
}

fn invocation_from_agent_metadata() -> Option<String> {
    let metadata = env::var("CODING_AGENT_METADATA").ok()?;
    metadata.split(',').find_map(|field| {
        field
            .trim()
            .strip_prefix("invocation_id=")
            .map(str::to_string)
    })
}

fn launch(mode: &str, task: &str, lifetime: Lifetime, arguments: &[String]) -> Result<i32> {
    let identity = current_identity()?;
    let state_path = state_path()?;
    let cid_directory = state_path
        .parent()
        .ok_or_else(|| AppError::Message("ownership state has no parent".to_string()))?;
    fs::create_dir_all(cid_directory).map_err(|source| AppError::Io {
        context: format!("create {}", cid_directory.display()),
        source,
    })?;
    let cidfile = cid_directory.join(format!(
        ".agent-podman.{}.{}.cid",
        std::process::id(),
        now_secs()
    ));
    let labels = ownership_labels(&identity, task, lifetime);
    let mut command = Command::new(podman_bin());
    command.arg(mode);
    for (key, value) in labels {
        command.arg("--label").arg(format!("{key}={value}"));
    }
    command.arg("--cidfile").arg(&cidfile).args(arguments);
    command.stdin(Stdio::inherit());
    command.stdout(Stdio::inherit());
    command.stderr(Stdio::inherit());
    let mut child = command.spawn().map_err(|source| AppError::Io {
        context: format!("start podman {mode}"),
        source,
    })?;

    let mut registered_id = None;
    let status = loop {
        if registered_id.is_none() {
            if let Ok(raw) = fs::read_to_string(&cidfile) {
                let id = raw.trim();
                if !id.is_empty() {
                    let ownership = Ownership {
                        container_id: id.to_string(),
                        owner_agent: identity.name.clone(),
                        owner_invocation: identity.invocation.clone(),
                        owner_pane: identity.pane.clone(),
                        task: task.to_string(),
                        lifetime,
                        updated_at: now_secs(),
                    };
                    update_registry(|registry| {
                        registry.containers.insert(id.to_string(), ownership);
                    })?;
                    registered_id = Some(id.to_string());
                }
            }
        }
        if let Some(status) = child.try_wait().map_err(|source| AppError::Io {
            context: format!("wait for podman {mode}"),
            source,
        })? {
            break status;
        }
        thread::sleep(Duration::from_millis(50));
    };

    if registered_id.is_none() {
        if let Ok(raw) = fs::read_to_string(&cidfile) {
            let id = raw.trim();
            if !id.is_empty() {
                let ownership = Ownership {
                    container_id: id.to_string(),
                    owner_agent: identity.name,
                    owner_invocation: identity.invocation,
                    owner_pane: identity.pane,
                    task: task.to_string(),
                    lifetime,
                    updated_at: now_secs(),
                };
                update_registry(|registry| {
                    registry.containers.insert(id.to_string(), ownership);
                })?;
                registered_id = Some(id.to_string());
            }
        }
    }
    let _ = fs::remove_file(&cidfile);
    if let Some(id) = registered_id {
        if !container_exists(&id) {
            update_registry(|registry| {
                registry.containers.remove(&id);
            })?;
        }
    }
    Ok(exit_code(status))
}

fn transfer(container: &str, task: &str, lifetime: Lifetime) -> Result<i32> {
    let identity = current_identity()?;
    let labels = inspect_labels(container)?;
    if labels.get(MANAGED_LABEL).map(String::as_str) != Some(MANAGED_VERSION) {
        return Err(AppError::Message(format!(
            "refusing to claim unlabelled legacy container {container}; recreate it through agent-podman"
        )));
    }
    let id = inspect_id(container)?;
    let ownership = Ownership {
        container_id: id.clone(),
        owner_agent: identity.name,
        owner_invocation: identity.invocation,
        owner_pane: identity.pane,
        task: task.to_string(),
        lifetime,
        updated_at: now_secs(),
    };
    update_registry(|registry| {
        registry.containers.insert(id.clone(), ownership);
    })?;
    println!(
        "state=transferred container={} task={} lifetime={} summary=ownership-transferred-to-current-agent-invocation",
        id,
        task,
        lifetime.as_str()
    );
    Ok(0)
}

fn ownership_labels(
    identity: &AgentIdentity,
    task: &str,
    lifetime: Lifetime,
) -> BTreeMap<&'static str, String> {
    BTreeMap::from([
        (MANAGED_LABEL, MANAGED_VERSION.to_string()),
        (AGENT_LABEL, identity.name.clone()),
        (INVOCATION_LABEL, identity.invocation.clone()),
        (PANE_LABEL, identity.pane.clone()),
        (TASK_LABEL, task.to_string()),
        (LIFETIME_LABEL, lifetime.as_str().to_string()),
    ])
}

fn reconcile(
    agents_path: &Path,
    live_invocations_path: Option<&Path>,
    json_output: bool,
    apply: bool,
) -> Result<i32> {
    let agents = load_live_agents(agents_path)?;
    let invocations = match live_invocations_path {
        Some(path) => load_live_invocations(path)?,
        None => discover_live_invocations(&agents),
    };
    let registry = read_registry()?;
    let containers = list_containers()?;
    let mut reclaimed_ids = BTreeSet::new();

    let mut report = ReconcileReport {
        state: "ok".to_string(),
        managed: 0,
        live: 0,
        reclaimed: 0,
        reclaimable: 0,
        transfer_required: 0,
        owner_unknown: 0,
        unmanaged: 0,
        zombie_containers: 0,
        zombies: 0,
        details: Vec::new(),
        errors: Vec::new(),
    };

    let mut reclaim_candidates: Vec<(usize, Container)> = Vec::new();
    for (container_index, container) in containers.into_iter().enumerate() {
        let (zombies, namespace) = if container_index < MAX_PROCESS_EVIDENCE {
            container_process_evidence(&container)
        } else {
            (0, None)
        };
        if zombies > 0 {
            report.zombie_containers += 1;
            report.zombies += zombies;
        }
        let ownership = effective_ownership(&container, &registry);
        let Some(ownership) = ownership else {
            report.unmanaged += 1;
            report.details.push(ContainerReport {
                id: container.id,
                name: container.name,
                state: container.state,
                disposition: Disposition::Unmanaged,
                owner_agent: None,
                owner_invocation: None,
                task: None,
                lifetime: None,
                namespace,
                zombies,
                action: "report-only: legacy/unlabelled container is never auto-removed"
                    .to_string(),
            });
            continue;
        };
        report.managed += 1;
        let disposition = classify(&ownership, &agents, &invocations);
        let action = match disposition {
            Disposition::Live => {
                report.live += 1;
                "none: exact owner invocation is live".to_string()
            }
            Disposition::Reclaimable => {
                report.reclaimable += 1;
                "eligible: exact owner invocation is gone".to_string()
            }
            Disposition::TransferRequired => {
                report.transfer_required += 1;
                "transfer required: task-retained container is never auto-removed".to_string()
            }
            Disposition::OwnerUnknown => {
                report.owner_unknown += 1;
                "report-only: current owner invocation could not be established".to_string()
            }
            Disposition::Unmanaged => unreachable!(),
        };
        let detail_index = report.details.len();
        if apply && disposition == Disposition::Reclaimable {
            reclaim_candidates.push((detail_index, container.clone()));
        }
        report.details.push(ContainerReport {
            id: container.id,
            name: container.name,
            state: container.state,
            disposition,
            owner_agent: Some(ownership.owner_agent),
            owner_invocation: Some(ownership.owner_invocation),
            task: Some(ownership.task),
            lifetime: Some(ownership.lifetime),
            namespace,
            zombies,
            action,
        });
    }
    if apply && !reclaim_candidates.is_empty() {
        let outcomes = thread::scope(|scope| {
            reclaim_candidates
                .into_iter()
                .map(|(index, container)| {
                    scope.spawn(move || {
                        let outcome = gracefully_remove(&container);
                        (index, container, outcome)
                    })
                })
                .collect::<Vec<_>>()
                .into_iter()
                .map(|handle| handle.join())
                .collect::<Vec<_>>()
        });
        for outcome in outcomes {
            let (index, container, result) = outcome
                .map_err(|_| AppError::Message("container cleanup worker panicked".to_string()))?;
            match result {
                Ok(()) => {
                    report.reclaimable -= 1;
                    report.reclaimed += 1;
                    reclaimed_ids.insert(container.id);
                    report.details[index].action =
                        "reclaimed: graceful stop and remove completed".to_string();
                }
                Err(error) => {
                    report.errors.push(format!("{}:{error}", container.name));
                    report.details[index].action = format!("reclaim-failed: {error}");
                }
            }
        }
    }
    if apply && !reclaimed_ids.is_empty() {
        update_registry(|current| {
            for id in &reclaimed_ids {
                current.containers.remove(id);
            }
        })?;
    }
    let attention = report.reclaimable > 0
        || report.transfer_required > 0
        || report.owner_unknown > 0
        || report.unmanaged > 0
        || !report.errors.is_empty();
    report.state = if attention {
        "action-required".to_string()
    } else {
        "ok".to_string()
    };
    emit_report(&report, json_output)?;
    Ok(i32::from(attention))
}

fn classify(
    ownership: &Ownership,
    live_agents: &BTreeMap<String, String>,
    live_invocations: &BTreeMap<String, BTreeSet<String>>,
) -> Disposition {
    // ORC may be between agent-list updates while the process is still alive.
    // Exact process evidence always wins over an absent/recycled name.
    if live_invocations
        .values()
        .any(|invocations| invocations.contains(&ownership.owner_invocation))
    {
        return Disposition::Live;
    }
    if !live_agents.contains_key(&ownership.owner_agent) {
        return if ownership.lifetime == Lifetime::Agent {
            Disposition::Reclaimable
        } else {
            Disposition::TransferRequired
        };
    }
    let Some(invocations) = live_invocations.get(&ownership.owner_agent) else {
        return Disposition::OwnerUnknown;
    };
    if invocations.is_empty() {
        return Disposition::OwnerUnknown;
    }
    if invocations.contains(&ownership.owner_invocation) {
        Disposition::Live
    } else if ownership.lifetime == Lifetime::Agent {
        Disposition::Reclaimable
    } else {
        Disposition::TransferRequired
    }
}

fn effective_ownership(container: &Container, registry: &Registry) -> Option<Ownership> {
    if container.labels.get(MANAGED_LABEL).map(String::as_str) != Some(MANAGED_VERSION) {
        return None;
    }
    if let Some(ownership) = registry.containers.get(&container.id) {
        return Some(ownership.clone());
    }
    Some(Ownership {
        container_id: container.id.clone(),
        owner_agent: container.labels.get(AGENT_LABEL)?.clone(),
        owner_invocation: container.labels.get(INVOCATION_LABEL)?.clone(),
        owner_pane: container
            .labels
            .get(PANE_LABEL)
            .cloned()
            .unwrap_or_default(),
        task: container.labels.get(TASK_LABEL)?.clone(),
        lifetime: Lifetime::parse(container.labels.get(LIFETIME_LABEL)?).ok()?,
        updated_at: 0,
    })
}

fn load_live_agents(path: &Path) -> Result<BTreeMap<String, String>> {
    let raw = fs::read_to_string(path).map_err(|source| AppError::Io {
        context: format!("read agent snapshot {}", path.display()),
        source,
    })?;
    let value: Value = serde_json::from_str(&raw).map_err(|source| AppError::Json {
        context: format!("parse agent snapshot {}", path.display()),
        source,
    })?;
    let (agents, captured_at) = if let Some(object) = value.as_object() {
        let captured_at = object
            .get("captured_at")
            .and_then(Value::as_f64)
            .ok_or_else(|| AppError::Message("agent snapshot has no captured_at".to_string()))?;
        (
            object.get("agents").cloned().unwrap_or(Value::Null),
            Some(captured_at),
        )
    } else {
        (value, None)
    };
    if let Some(captured_at) = captured_at {
        let age = now_secs().saturating_sub(captured_at.max(0.0) as u64);
        if age > SNAPSHOT_MAX_AGE_SECS {
            return Err(AppError::Message(format!(
                "agent snapshot is stale: age={age}s max={SNAPSHOT_MAX_AGE_SECS}s"
            )));
        }
    }
    let array = agents
        .as_array()
        .ok_or_else(|| AppError::Message("agent snapshot is not an array".to_string()))?;
    let terminal: BTreeSet<&str> = [
        "closed",
        "crashed",
        "dead",
        "disconnected",
        "error",
        "exited",
        "failed",
        "retired",
        "terminated",
        "unreachable",
        "unresponsive",
    ]
    .into_iter()
    .collect();
    let mut live = BTreeMap::new();
    for agent in array {
        let Some(object) = agent.as_object() else {
            continue;
        };
        let name = object.get("name").and_then(Value::as_str).unwrap_or("");
        let status = object
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_ascii_lowercase();
        if !name.is_empty() && !terminal.contains(status.as_str()) {
            let pane = object
                .get("tmux_pane_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            live.insert(name.to_string(), pane);
        }
    }
    Ok(live)
}

fn load_live_invocations(path: &Path) -> Result<BTreeMap<String, BTreeSet<String>>> {
    let raw = fs::read_to_string(path).map_err(|source| AppError::Io {
        context: format!("read live invocations {}", path.display()),
        source,
    })?;
    serde_json::from_str(&raw).map_err(|source| AppError::Json {
        context: format!("parse live invocations {}", path.display()),
        source,
    })
}

fn discover_live_invocations(
    agents: &BTreeMap<String, String>,
) -> BTreeMap<String, BTreeSet<String>> {
    let pane_pids = tmux_pane_pids();
    let process_tree = process_tree();
    let mut discovered: BTreeMap<String, BTreeSet<String>> = agents
        .iter()
        .map(|(name, pane)| {
            let invocations = pane_pids
                .get(pane)
                .map(|pid| descendant_invocations(*pid, &process_tree))
                .unwrap_or_default();
            (name.clone(), invocations)
        })
        .collect();
    // Also scan panes not present in the ORC snapshot. During recycling there
    // can be a short interval where listAgents omits an invocation whose process
    // still exists; that interval must never authorize deletion.
    for (pane, pid) in pane_pids {
        discovered
            .entry(format!("@pane:{pane}"))
            .or_insert_with(|| descendant_invocations(pid, &process_tree));
    }
    discovered
}

fn tmux_pane_pids() -> BTreeMap<String, u32> {
    let output = Command::new("tmux")
        .args(["list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}"])
        .output();
    let Ok(output) = output else {
        return BTreeMap::new();
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let (pane, pid) = line.split_once('\t')?;
            Some((pane.to_string(), pid.parse().ok()?))
        })
        .collect()
}

fn process_tree() -> BTreeMap<u32, Vec<u32>> {
    let mut children: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    let Ok(entries) = fs::read_dir("/proc") else {
        return children;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<u32>().ok())
        else {
            continue;
        };
        let Ok(stat) = fs::read_to_string(entry.path().join("stat")) else {
            continue;
        };
        let Some(after_name) = stat.rsplit_once(") ").map(|(_, rest)| rest) else {
            continue;
        };
        let Some(ppid) = after_name
            .split_whitespace()
            .nth(1)
            .and_then(|value| value.parse::<u32>().ok())
        else {
            continue;
        };
        children.entry(ppid).or_default().push(pid);
    }
    children
}

fn descendant_invocations(root: u32, tree: &BTreeMap<u32, Vec<u32>>) -> BTreeSet<String> {
    let mut pending = vec![root];
    let mut seen = BTreeSet::new();
    let mut invocations = BTreeSet::new();
    while let Some(pid) = pending.pop() {
        if !seen.insert(pid) {
            continue;
        }
        if let Ok(raw) = fs::read(format!("/proc/{pid}/environ")) {
            for field in raw.split(|byte| *byte == 0) {
                let text = String::from_utf8_lossy(field);
                if let Some(value) = text.strip_prefix("META_3PAI_INVOCATION_ID=") {
                    if !value.is_empty() {
                        invocations.insert(value.to_string());
                    }
                } else if let Some(metadata) = text.strip_prefix("CODING_AGENT_METADATA=") {
                    for item in metadata.split(',') {
                        if let Some(value) = item.trim().strip_prefix("invocation_id=") {
                            if !value.is_empty() {
                                invocations.insert(value.to_string());
                            }
                        }
                    }
                }
            }
        }
        pending.extend(tree.get(&pid).into_iter().flatten().copied());
    }
    invocations
}

fn list_containers() -> Result<Vec<Container>> {
    let output = bounded_podman(&["ps", "-a", "--format", "json"], QUERY_TIMEOUT_SECS)?;
    if !output.status.success() {
        return Err(AppError::Message(format!(
            "podman ps failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    let value: Value = serde_json::from_slice(&output.stdout).map_err(|source| AppError::Json {
        context: "parse podman ps JSON".to_string(),
        source,
    })?;
    let entries = value
        .as_array()
        .ok_or_else(|| AppError::Message("podman ps JSON is not an array".to_string()))?;
    entries.iter().map(parse_container).collect()
}

fn parse_container(value: &Value) -> Result<Container> {
    let object = value
        .as_object()
        .ok_or_else(|| AppError::Message("podman container row is not an object".to_string()))?;
    let id = string_field(object, &["Id", "ID"])
        .ok_or_else(|| AppError::Message("podman container row has no ID".to_string()))?;
    let name = match object.get("Names") {
        Some(Value::Array(names)) => names
            .first()
            .and_then(Value::as_str)
            .unwrap_or(&id)
            .to_string(),
        Some(Value::String(name)) => name.clone(),
        _ => id.clone(),
    };
    let state = string_field(object, &["State", "Status"]).unwrap_or_else(|| "unknown".to_string());
    let labels = object
        .get("Labels")
        .and_then(Value::as_object)
        .map(|labels| {
            labels
                .iter()
                .filter_map(|(key, value)| Some((key.clone(), value.as_str()?.to_string())))
                .collect()
        })
        .unwrap_or_default();
    Ok(Container {
        id,
        name,
        state: state.to_ascii_lowercase(),
        labels,
    })
}

fn string_field(object: &Map<String, Value>, names: &[&str]) -> Option<String> {
    names.iter().find_map(|name| {
        object
            .get(*name)
            .and_then(Value::as_str)
            .map(str::to_string)
    })
}

fn container_process_evidence(container: &Container) -> (usize, Option<String>) {
    if container.state != "running" {
        return (0, None);
    }
    let zombies = bounded_podman(&["top", &container.id, "hpid,state"], EVIDENCE_TIMEOUT_SECS)
        .ok()
        .filter(|output| output.status.success())
        .map(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .skip(1)
                .filter(|line| {
                    line.split_whitespace()
                        .nth(1)
                        .is_some_and(|state| state.starts_with('Z'))
                })
                .count()
        })
        .unwrap_or(0);
    let namespace = bounded_podman(
        &["inspect", "--format", "{{.State.Pid}}", &container.id],
        EVIDENCE_TIMEOUT_SECS,
    )
    .ok()
    .filter(|output| output.status.success())
    .and_then(|output| String::from_utf8(output.stdout).ok())
    .and_then(|pid| fs::read_link(format!("/proc/{}/ns/pid", pid.trim())).ok())
    .map(|path| path.to_string_lossy().to_string());
    (zombies, namespace)
}

fn gracefully_remove(container: &Container) -> Result<()> {
    let mut stop_error = None;
    if container.state == "running" {
        let stop = bounded_podman(&["stop", "--time", "10", &container.id], STOP_TIMEOUT_SECS)?;
        if !stop.status.success() {
            stop_error = Some(format!(
                "podman stop failed: {}",
                String::from_utf8_lossy(&stop.stderr).trim()
            ));
        }
    }
    let remove = bounded_podman(&["rm", &container.id], REMOVE_TIMEOUT_SECS)?;
    if !remove.status.success() {
        let stop_context = stop_error
            .map(|error| format!("{error}; "))
            .unwrap_or_default();
        return Err(AppError::Message(format!(
            "{stop_context}podman rm failed: {}",
            String::from_utf8_lossy(&remove.stderr).trim()
        )));
    }
    Ok(())
}

fn inspect_labels(container: &str) -> Result<BTreeMap<String, String>> {
    let output = bounded_podman(
        &["inspect", "--format", "{{json .Config.Labels}}", container],
        QUERY_TIMEOUT_SECS,
    )?;
    if !output.status.success() {
        return Err(AppError::Message(format!(
            "podman inspect failed for {container}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    serde_json::from_slice(&output.stdout).map_err(|source| AppError::Json {
        context: format!("parse labels for {container}"),
        source,
    })
}

fn inspect_id(container: &str) -> Result<String> {
    let output = bounded_podman(
        &["inspect", "--format", "{{.Id}}", container],
        QUERY_TIMEOUT_SECS,
    )?;
    if !output.status.success() {
        return Err(AppError::Message(format!(
            "podman inspect failed for {container}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn container_exists(container: &str) -> bool {
    bounded_podman(&["container", "exists", container], QUERY_TIMEOUT_SECS)
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn bounded_podman(arguments: &[&str], timeout_secs: &str) -> Result<std::process::Output> {
    Command::new("timeout")
        .args([
            "--signal=TERM",
            "--kill-after=2s",
            timeout_secs,
            &podman_bin(),
        ])
        .args(arguments)
        .output()
        .map_err(|source| AppError::Io {
            context: format!("run bounded podman {}", arguments.join(" ")),
            source,
        })
}

fn podman_bin() -> String {
    env::var("AGENT_PODMAN_BIN").unwrap_or_else(|_| "podman".to_string())
}

fn state_path() -> Result<PathBuf> {
    if let Some(path) = env::var_os("DEV_HERMIT_CONTAINER_STATE") {
        return Ok(PathBuf::from(path));
    }
    if let Some(root) = env::var_os("DEV_HERMIT_PARENT") {
        return Ok(PathBuf::from(root).join("ignored/ci-hub/agent-containers.json"));
    }
    let mut candidate = env::current_dir().map_err(|source| AppError::Io {
        context: "read current directory".to_string(),
        source,
    })?;
    for _ in 0..=4 {
        if candidate.join("ci-hub").is_dir() && candidate.join("scripts/agent-podman.rs").is_file()
        {
            return Ok(candidate.join("ignored/ci-hub/agent-containers.json"));
        }
        if !candidate.pop() {
            break;
        }
    }
    let source = PathBuf::from(file!());
    if let Some(root) = source.parent().and_then(Path::parent) {
        if root.join("ci-hub").is_dir() {
            return Ok(root.join("ignored/ci-hub/agent-containers.json"));
        }
    }
    Err(AppError::Message(
        "cannot locate dev-hermit within four parents; set DEV_HERMIT_PARENT".to_string(),
    ))
}

fn lock_path() -> Result<PathBuf> {
    let state = state_path()?;
    Ok(state.with_extension("lock"))
}

fn open_lock() -> Result<File> {
    let path = lock_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|source| AppError::Io {
            context: format!("create {}", parent.display()),
            source,
        })?;
    }
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&path)
        .map_err(|source| AppError::Io {
            context: format!("open {}", path.display()),
            source,
        })?;
    file.lock_exclusive().map_err(|source| AppError::Io {
        context: format!("lock {}", path.display()),
        source,
    })?;
    Ok(file)
}

fn read_registry_unlocked(path: &Path) -> Result<Registry> {
    match fs::read_to_string(path) {
        Ok(raw) => serde_json::from_str(&raw).map_err(|source| AppError::Json {
            context: format!("parse {}", path.display()),
            source,
        }),
        Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(Registry::default()),
        Err(source) => Err(AppError::Io {
            context: format!("read {}", path.display()),
            source,
        }),
    }
}

fn read_registry() -> Result<Registry> {
    read_registry_unlocked(&state_path()?)
}

fn update_registry(action: impl FnOnce(&mut Registry)) -> Result<()> {
    let _lock = open_lock()?;
    let path = state_path()?;
    let mut registry = read_registry_unlocked(&path)?;
    action(&mut registry);
    write_registry_unlocked(&path, &registry)
}

fn write_registry_unlocked(path: &Path, registry: &Registry) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|source| AppError::Io {
            context: format!("create {}", parent.display()),
            source,
        })?;
    }
    let temporary = path.with_extension(format!("tmp.{}", std::process::id()));
    let text = serde_json::to_string_pretty(registry).map_err(|source| AppError::Json {
        context: "serialize ownership registry".to_string(),
        source,
    })?;
    fs::write(&temporary, format!("{text}\n")).map_err(|source| AppError::Io {
        context: format!("write {}", temporary.display()),
        source,
    })?;
    fs::rename(&temporary, path).map_err(|source| AppError::Io {
        context: format!("replace {}", path.display()),
        source,
    })
}

fn emit_report(report: &ReconcileReport, json_output: bool) -> Result<()> {
    if json_output {
        let text = serde_json::to_string_pretty(report).map_err(|source| AppError::Json {
            context: "serialize reconciliation report".to_string(),
            source,
        })?;
        println!("{text}");
        return Ok(());
    }
    println!(
        "state={} managed={} live={} reclaimed={} reclaimable={} transfer_required={} owner_unknown={} unmanaged={} zombie_containers={} zombies={} summary={}",
        report.state,
        report.managed,
        report.live,
        report.reclaimed,
        report.reclaimable,
        report.transfer_required,
        report.owner_unknown,
        report.unmanaged,
        report.zombie_containers,
        report.zombies,
        if report.state == "ok" {
            "all-managed-containers-have-live-owners"
        } else {
            "container-lifecycle-needs-action"
        }
    );
    for detail in &report.details {
        println!(
            "DETAIL: name={} id={} disposition={:?} owner={} invocation={} task={} lifetime={} namespace={} zombies={} action={}",
            detail.name,
            short_id(&detail.id),
            detail.disposition,
            detail.owner_agent.as_deref().unwrap_or("unknown"),
            detail.owner_invocation.as_deref().unwrap_or("unknown"),
            detail.task.as_deref().unwrap_or("unknown"),
            detail.lifetime.map(Lifetime::as_str).unwrap_or("unknown"),
            detail.namespace.as_deref().unwrap_or("none"),
            detail.zombies,
            detail.action
        );
    }
    for error in &report.errors {
        println!("ERROR: container-lifecycle {error}");
    }
    Ok(())
}

fn short_id(id: &str) -> &str {
    id.get(..12).unwrap_or(id)
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(unix)]
fn exit_code(status: ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    status
        .code()
        .unwrap_or_else(|| 128 + status.signal().unwrap_or(1))
}

#[cfg(not(unix))]
fn exit_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ownership(lifetime: Lifetime) -> Ownership {
        Ownership {
            container_id: "abc".to_string(),
            owner_agent: "hermit-1".to_string(),
            owner_invocation: "inv-old".to_string(),
            owner_pane: "%1".to_string(),
            task: "task-1".to_string(),
            lifetime,
            updated_at: 1,
        }
    }

    #[test]
    fn retired_agent_container_is_reclaimable() {
        assert_eq!(
            classify(
                &ownership(Lifetime::Agent),
                &BTreeMap::new(),
                &BTreeMap::new()
            ),
            Disposition::Reclaimable
        );
    }

    #[test]
    fn task_container_requires_transfer_when_creator_is_gone() {
        assert_eq!(
            classify(
                &ownership(Lifetime::Task),
                &BTreeMap::new(),
                &BTreeMap::new()
            ),
            Disposition::TransferRequired
        );
    }

    #[test]
    fn exact_invocation_not_reused_agent_name_defines_liveness() {
        let agents = BTreeMap::from([("hermit-1".to_string(), "%1".to_string())]);
        let matching = BTreeMap::from([(
            "hermit-1".to_string(),
            BTreeSet::from(["inv-old".to_string()]),
        )]);
        let recycled = BTreeMap::from([(
            "hermit-1".to_string(),
            BTreeSet::from(["inv-new".to_string()]),
        )]);
        assert_eq!(
            classify(&ownership(Lifetime::Agent), &agents, &matching),
            Disposition::Live
        );
        assert_eq!(
            classify(&ownership(Lifetime::Agent), &agents, &recycled),
            Disposition::Reclaimable
        );
    }

    #[test]
    fn missing_invocation_evidence_never_authorizes_removal() {
        let agents = BTreeMap::from([("hermit-1".to_string(), "%1".to_string())]);
        assert_eq!(
            classify(&ownership(Lifetime::Agent), &agents, &BTreeMap::new()),
            Disposition::OwnerUnknown
        );
    }

    #[test]
    fn live_process_evidence_overrides_transient_orc_absence() {
        let process_evidence = BTreeMap::from([(
            "@pane:%1".to_string(),
            BTreeSet::from(["inv-old".to_string()]),
        )]);
        assert_eq!(
            classify(
                &ownership(Lifetime::Agent),
                &BTreeMap::new(),
                &process_evidence
            ),
            Disposition::Live
        );
    }

    #[test]
    fn wrapper_rejects_cidfile_override() {
        assert!(reject_reserved_arguments(&["--cidfile=/tmp/x".to_string()]).is_err());
    }

    #[test]
    fn quickstart_is_nonempty_and_consequence_led() {
        assert!(QUICKSTART.contains("never automatic deletion targets"));
        assert!(QUICKSTART.lines().count() < 20);
    }
}
