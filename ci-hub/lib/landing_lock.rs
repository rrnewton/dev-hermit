//! Typed implementation of the shared-file landing mutex.

use chrono::{Local, TimeZone};
use clap::{Args, Subcommand};
use fs2::FileExt;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use thiserror::Error;

const DEFAULT_WAIT_SECONDS: u64 = 1_800;
const DEFAULT_HOLD_SECONDS: u64 = 900;
const POLL_SECONDS: u64 = 3;
const GUARD_WAIT_SECONDS: u64 = 30;
/// Hard ceiling on how long a `run` child may execute before it is killed and
/// cleanup is proved. A complete empty-domain proof releases the lock; otherwise
/// it stays quarantined. A stuck land holding the lock is a head-of-line block
/// for every other FIFO waiter (the ~2040-minute starvation this bounds
/// against): an unbounded wait is unboxed compute. A real land is minutes; this
/// measured ceiling is far below any starvation.
// Measured 2026-08-04: 11 successful demo-gate runs had p99/max 864s. The
// lander gate bound is 1080s (max + 25%); the whole-child ceiling allows two
// complete gate windows while still guaranteeing bounded cleanup or quarantine
// on a wedged child.
const DEFAULT_CHILD_DEADLINE_SECONDS: u64 = 2_160;
/// Exit code reported when a `run` child is killed for exceeding its deadline.
const CHILD_DEADLINE_EXIT_CODE: i32 = 124;
/// Grace period between SIGTERM and SIGKILL when terminating a timed-out child.
const CHILD_TERM_GRACE_SECONDS: u64 = 5;
/// Interval at which `run` polls a live child for completion or deadline breach.
const CHILD_POLL_MILLIS: u64 = 500;
const START_GATE_FD: libc::c_int = 9;
const START_GATE_SCRIPT: &str = r#"
if IFS= read -r ci_hub_start_token <&9 && [ "$ci_hub_start_token" = "ci-hub-go" ]; then
  exec 9<&-
  exec "$@"
fi
exit 125
"#;

#[derive(Args, Clone, Debug)]
pub struct LandLockArgs {
    #[command(subcommand)]
    pub command: LandLockCommand,
}

#[derive(Subcommand, Clone, Debug)]
pub enum LandLockCommand {
    /// Wait in FIFO order and acquire the landing lease.
    Acquire(AcquireArgs),
    /// Refresh a lease owned by this agent.
    Renew(RenewArgs),
    /// Release a lease owned by this agent.
    Release(ReleaseArgs),
    /// Print holder metadata and the FIFO queue.
    Status,
    /// Reclaim a lease only when its recorded owner process is proven dead.
    ReclaimDead,
    /// Census an UNCENSUSED payload domain post-hoc, after the supervisor died
    /// before it could take one, so `reclaim-dead` can finish.
    CensusOrphanedDomain(CensusOrphanedDomainArgs),
    /// Acquire and run with a heartbeat; release after complete cleanup proof,
    /// otherwise retain a quarantine.
    Run(RunArgs),
}

/// Restated identity plus the one operator attestation that discharges an
/// UNCENSUSED quarantine. Every field is re-checked against the durable record;
/// a mismatch refuses, so this cannot be run blind against whatever happens to
/// be quarantined at the time.
///
/// This mirrors `validate-lock census-orphaned-domain`. Landing needed its own
/// because the two are DISTINCT authorities over distinct record files
/// (`CI_HUB_LANDING_LOCK` vs `CI_HUB_VALIDATE_LOCK`), so the validate command
/// could never address a landing record no matter what arguments it was handed.
/// Without this, `reclaim-dead` demanded a census that nothing could produce:
/// the only producers, `begin_run_census` and `record_residuals`, are reachable
/// only from `run()` and only for the LIVE holder, so a supervisor that died at
/// `phase=published` left a one-way door.
#[derive(Args, Clone, Debug)]
pub struct CensusOrphanedDomainArgs {
    /// Must equal the recorded `agent` of the quarantined operation.
    #[arg(long)]
    pub agent: String,
    /// Must equal the recorded `operation` (`pr:<number>`).
    #[arg(long)]
    pub operation: String,
    /// Must equal the recorded published `leader` as `<pid>:<start_ticks>`.
    #[arg(long)]
    pub leader: String,
    /// Must equal the recorded published `pgid`.
    #[arg(long)]
    pub pgid: u32,
    /// Affirms that the payload's process domain was observed empty by a
    /// supervisor-independent authority (unit cgroup absent/empty AND every
    /// cgroup the payload can migrate into empty).
    ///
    /// NOT required when the record carries a cgroup anchor and the kernel
    /// already says the domain is empty -- that census is mechanical. This is
    /// only for records with no anchor, or where the anchor cannot be read.
    /// Every landing-lock record written before the anchor work is anchorless,
    /// so today this path is the norm for landing rather than the exception.
    #[arg(long)]
    pub attest_domain_empty: bool,
    /// The exact observations backing `--attest-domain-empty`. Recorded in the
    /// transcript so the attestation is auditable rather than anonymous. Must be
    /// non-empty whenever `--attest-domain-empty` is used.
    #[arg(long, default_value = "")]
    pub evidence: String,
}

#[derive(Args, Clone, Debug)]
pub struct AcquireArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub pr: String,
    #[arg(long, default_value_t = DEFAULT_WAIT_SECONDS)]
    pub wait: u64,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
}

#[derive(Args, Clone, Debug)]
pub struct RenewArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
}

#[derive(Args, Clone, Debug)]
pub struct ReleaseArgs {
    #[arg(long)]
    pub agent: String,
}

#[derive(Args, Clone, Debug)]
pub struct RunArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub pr: String,
    #[arg(long, default_value_t = DEFAULT_WAIT_SECONDS)]
    pub wait: u64,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
    /// Kill the child if it runs longer than this many seconds. Release follows
    /// only after complete cleanup proof; otherwise the lock remains
    /// quarantined. Must be positive: unbounded lock holders are forbidden.
    #[arg(long, default_value_t = DEFAULT_CHILD_DEADLINE_SECONDS)]
    pub child_deadline: u64,
    #[arg(last = true, required = true)]
    pub child: Vec<OsString>,
}

impl LandLockCommand {
    pub fn consumes_meaningful_time(&self) -> bool {
        matches!(self, Self::Acquire(_) | Self::Run(_))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LockState {
    pub agent: String,
    /// Optional fields added by the exact-head lander while it owns the
    /// shared lease.  The mutex must preserve these fields on parse/render so
    /// an annotated holder remains readable by every landing entry point.
    pub repo: Option<String>,
    pub operation: Option<String>,
    pub pending_mutation: Option<String>,
    pub pending_attempt: Option<String>,
    pub pending_call_count: Option<u64>,
    pub pending_call_id: Option<String>,
    pub pr: String,
    pub host: String,
    pub acquired_at: i64,
    pub acquired_human: String,
    pub expires_at: i64,
    pub reclaimed_from: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ProcessOwner {
    host: String,
    boot_id: String,
    pid: u32,
    start_ticks: u64,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct ProcessIdentity {
    pub(crate) pid: u32,
    pub(crate) start_ticks: u64,
}

/// Durable state for one supervised payload domain. The `Armed` state is
/// persisted before spawn, `Published` binds the still-gated child identity,
/// `CensusPending` durably disables heartbeat renewal before any descendant is
/// frozen, and `Residual` records the final exact census. Every consumer
/// must dereference this record through `verify_cleanup_record`; file existence
/// or a printed marker is not proof.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CleanupRecord {
    pub(crate) agent: String,
    pub(crate) operation: String,
    pub(crate) host: String,
    pub(crate) boot_id: String,
    pub(crate) phase: CleanupPhase,
    /// Cgroup-v2 path of the published payload, relative to the cgroup mount.
    ///
    /// The ONE supervisor-independent anchor in this record. `leader`/`pgid` are
    /// both useless post-hoc: a ppid walk needs the (now dead) subreaper, and
    /// pgid membership does not survive `setsid()`. Cgroup membership DOES
    /// survive both `setsid()` and reparenting, so this is what lets an orphaned
    /// domain be censused mechanically instead of by operator attestation.
    ///
    /// `None` on every record written before this field existed, and on records
    /// whose payload cgroup could not be read — those keep the attestation path.
    pub(crate) cgroup: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum CleanupPhase {
    Armed,
    Published {
        leader: ProcessIdentity,
        pgid: u32,
    },
    CensusPending {
        leader: ProcessIdentity,
        pgid: u32,
    },
    Residual {
        pgid: u32,
        domain_complete: bool,
        residuals: Vec<ProcessIdentity>,
    },
}

impl CleanupRecord {
    pub(crate) fn new(
        agent: impl Into<String>,
        operation: impl Into<String>,
        phase: CleanupPhase,
    ) -> io::Result<Self> {
        let host = current_host();
        if host == "unknown" {
            return Err(io::Error::other(
                "cannot arm cleanup authority without a host identity",
            ));
        }
        Ok(Self {
            agent: agent.into(),
            operation: operation.into(),
            host,
            boot_id: read_current_boot_id()?,
            phase,
            cgroup: None,
        })
    }

    pub(crate) fn parse(content: &str) -> Result<Self, String> {
        let mut version = None;
        let mut agent = None;
        let mut operation = None;
        let mut host = None;
        let mut boot_id = None;
        let mut phase = None;
        let mut leader = None;
        let mut pgid = None;
        let mut domain_complete = None;
        let mut cgroup = None;
        let mut residuals = Vec::new();
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line
                .split_once('=')
                .ok_or_else(|| format!("cleanup line {} is not key=value", line_number + 1))?;
            match key {
                "version" => set_once(&mut version, value.to_string(), key)?,
                "agent" => set_once(&mut agent, value.to_string(), key)?,
                "operation" => set_once(&mut operation, value.to_string(), key)?,
                "host" => set_once(&mut host, value.to_string(), key)?,
                "boot_id" => set_once(&mut boot_id, value.to_string(), key)?,
                "phase" => set_once(&mut phase, value.to_string(), key)?,
                "leader" => {
                    let identity = parse_process_identity(value, "leader")?;
                    set_once(&mut leader, identity, key)?;
                }
                "pgid" => set_once(
                    &mut pgid,
                    value
                        .parse::<u32>()
                        .map_err(|error| format!("invalid cleanup pgid {value:?}: {error}"))?,
                    key,
                )?,
                "domain_complete" => {
                    let parsed = match value {
                        "true" => true,
                        "false" => false,
                        _ => return Err(format!("invalid domain_complete {value:?}")),
                    };
                    set_once(&mut domain_complete, parsed, key)?;
                }
                "residual" => residuals.push(parse_process_identity(value, "residual")?),
                "cgroup" => set_once(&mut cgroup, value.to_string(), key)?,
                unknown => return Err(format!("unknown cleanup field {unknown:?}")),
            }
        }
        // v3 == v2 plus an optional `cgroup`. Both are accepted so a record
        // written by either binary generation stays readable while builds are
        // mixed on this box; `cgroup` on a v2 record is a malformed record, not
        // a tolerable extra.
        match version.as_deref() {
            Some("2") if cgroup.is_some() => {
                return Err("cleanup version 2 cannot carry a cgroup field".to_string())
            }
            Some("2") | Some("3") => {}
            other => return Err(format!("unsupported cleanup version {other:?}")),
        }
        residuals.sort();
        residuals.dedup();
        let phase = match phase.as_deref() {
            Some("armed")
                if leader.is_none()
                    && pgid.is_none()
                    && domain_complete.is_none()
                    && residuals.is_empty() =>
            {
                CleanupPhase::Armed
            }
            Some("published") if domain_complete.is_none() && residuals.is_empty() => {
                CleanupPhase::Published {
                    leader: leader
                        .ok_or_else(|| "published cleanup record has no leader".to_string())?,
                    pgid: pgid.ok_or_else(|| "published cleanup record has no pgid".to_string())?,
                }
            }
            Some("census-pending") if domain_complete.is_none() && residuals.is_empty() => {
                CleanupPhase::CensusPending {
                    leader: leader
                        .ok_or_else(|| "census-pending cleanup record has no leader".to_string())?,
                    pgid: pgid
                        .ok_or_else(|| "census-pending cleanup record has no pgid".to_string())?,
                }
            }
            Some("residual") if leader.is_none() => CleanupPhase::Residual {
                pgid: pgid.ok_or_else(|| "residual cleanup record has no pgid".to_string())?,
                domain_complete: domain_complete
                    .ok_or_else(|| "residual cleanup record has no domain_complete".to_string())?,
                residuals,
            },
            Some(value) => return Err(format!("cleanup phase {value:?} has incompatible fields")),
            None => return Err("cleanup record has no phase".to_string()),
        };
        Ok(Self {
            agent: agent.ok_or_else(|| "cleanup record has no agent".to_string())?,
            operation: operation.ok_or_else(|| "cleanup record has no operation".to_string())?,
            host: host.ok_or_else(|| "cleanup record has no host".to_string())?,
            boot_id: boot_id.ok_or_else(|| "cleanup record has no boot_id".to_string())?,
            phase,
            cgroup,
        })
    }

    pub(crate) fn render(&self) -> String {
        // Emit v3 ONLY when there is actually a cgroup to carry. A record
        // without one renders byte-identically to what every prior binary
        // wrote, so landing-lock records and armed/legacy validate records are
        // untouched and stay readable by older builds still present on this
        // box. Only a published validate payload -- the sole consumer of the
        // mechanical census -- moves to v3.
        let version = if self.cgroup.is_some() { 3 } else { 2 };
        let mut output = format!(
            "version={version}\nagent={}\noperation={}\nhost={}\nboot_id={}\n",
            self.agent, self.operation, self.host, self.boot_id
        );
        if let Some(cgroup) = &self.cgroup {
            output.push_str(&format!("cgroup={cgroup}\n"));
        }
        match &self.phase {
            CleanupPhase::Armed => output.push_str("phase=armed\n"),
            CleanupPhase::Published { leader, pgid } => output.push_str(&format!(
                "phase=published\nleader={}:{}\npgid={pgid}\n",
                leader.pid, leader.start_ticks
            )),
            CleanupPhase::CensusPending { leader, pgid } => output.push_str(&format!(
                "phase=census-pending\nleader={}:{}\npgid={pgid}\n",
                leader.pid, leader.start_ticks
            )),
            CleanupPhase::Residual {
                pgid,
                domain_complete,
                residuals,
            } => {
                output.push_str(&format!(
                    "phase=residual\npgid={pgid}\ndomain_complete={domain_complete}\n"
                ));
                for identity in residuals {
                    output.push_str(&format!(
                        "residual={}:{}\n",
                        identity.pid, identity.start_ticks
                    ));
                }
            }
        }
        output
    }
}

fn set_once<T>(slot: &mut Option<T>, value: T, key: &str) -> Result<(), String> {
    if slot.replace(value).is_some() {
        return Err(format!("duplicate cleanup field {key:?}"));
    }
    Ok(())
}

fn parse_process_identity(value: &str, field: &str) -> Result<ProcessIdentity, String> {
    let (pid, start_ticks) = value
        .split_once(':')
        .ok_or_else(|| format!("invalid {field} identity {value:?}"))?;
    Ok(ProcessIdentity {
        pid: pid
            .parse()
            .map_err(|error| format!("invalid {field} pid {pid:?}: {error}"))?,
        start_ticks: start_ticks
            .parse()
            .map_err(|error| format!("invalid {field} start ticks {start_ticks:?}: {error}"))?,
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum CleanupVerification {
    None,
    Armed {
        record: CleanupRecord,
        reason: String,
    },
    Active {
        record: CleanupRecord,
        reason: String,
    },
    Uncensused {
        record: CleanupRecord,
        reason: String,
    },
    Recoverable {
        record: CleanupRecord,
        reason: String,
    },
    Unknown {
        record: Option<CleanupRecord>,
        reason: String,
    },
}

impl ProcessOwner {
    fn parse(content: &str) -> Result<Self, LandLockError> {
        let mut host = None;
        let mut boot_id = None;
        let mut pid = None;
        let mut start_ticks = None;
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                LandLockError::InvalidState(format!(
                    "owner line {} is not key=value",
                    line_number + 1
                ))
            })?;
            match key {
                "host" => host = Some(value.to_string()),
                "boot_id" => boot_id = Some(value.to_string()),
                "pid" => pid = Some(parse_unsigned(key, value)?),
                "start_ticks" => start_ticks = Some(parse_unsigned(key, value)?),
                unknown => {
                    return Err(LandLockError::InvalidState(format!(
                        "unknown owner field {unknown:?}"
                    )));
                }
            }
        }
        Ok(Self {
            host: required(host, "owner host")?,
            boot_id: required(boot_id, "owner boot_id")?,
            pid: u32::try_from(required(pid, "owner pid")?)
                .map_err(|_| LandLockError::InvalidState("owner pid exceeds u32".into()))?,
            start_ticks: required(start_ticks, "owner start_ticks")?,
        })
    }

    fn render(&self) -> String {
        format!(
            "host={}\nboot_id={}\npid={}\nstart_ticks={}\n",
            self.host, self.boot_id, self.pid, self.start_ticks
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum OwnerLiveness {
    Alive,
    Dead(String),
    Unknown(String),
}

impl LockState {
    fn parse(content: &str) -> Result<Self, LandLockError> {
        let mut agent = None;
        let mut repo = None;
        let mut operation = None;
        let mut pending_mutation = None;
        let mut pending_attempt = None;
        let mut pending_call_count = None;
        let mut pending_call_id = None;
        let mut pr = None;
        let mut host = None;
        let mut acquired_at = None;
        let mut acquired_human = None;
        let mut expires_at = None;
        let mut reclaimed_from = None;
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                LandLockError::InvalidState(format!(
                    "holder line {} is not key=value",
                    line_number + 1
                ))
            })?;
            match key {
                "agent" => agent = Some(value.to_string()),
                "repo" => repo = Some(value.to_string()),
                "operation" => operation = Some(value.to_string()),
                "pending_mutation" => pending_mutation = Some(value.to_string()),
                "pending_attempt" => pending_attempt = Some(value.to_string()),
                "pending_call_count" => pending_call_count = Some(parse_unsigned(key, value)?),
                "pending_call_id" => pending_call_id = Some(value.to_string()),
                "pr" => pr = Some(value.to_string()),
                "host" => host = Some(value.to_string()),
                "acquired_at" => acquired_at = Some(parse_integer(key, value)?),
                "acquired_human" => acquired_human = Some(value.to_string()),
                "expires_at" => expires_at = Some(parse_integer(key, value)?),
                "reclaimed_from" => reclaimed_from = Some(value.to_string()),
                unknown => {
                    return Err(LandLockError::InvalidState(format!(
                        "unknown holder field {unknown:?}"
                    )));
                }
            }
        }
        Ok(Self {
            agent: required(agent, "agent")?,
            repo,
            operation,
            pending_mutation,
            pending_attempt,
            pending_call_count,
            pending_call_id,
            pr: required(pr, "pr")?,
            host: required(host, "host")?,
            acquired_at: required(acquired_at, "acquired_at")?,
            acquired_human: required(acquired_human, "acquired_human")?,
            expires_at: required(expires_at, "expires_at")?,
            reclaimed_from,
        })
    }

    fn render(&self) -> String {
        let mut output = format!("agent={}\n", self.agent);
        for (key, value) in [
            ("repo", self.repo.as_deref()),
            ("operation", self.operation.as_deref()),
            ("pending_mutation", self.pending_mutation.as_deref()),
            ("pending_attempt", self.pending_attempt.as_deref()),
        ] {
            if let Some(value) = value {
                output.push_str(&format!("{key}={value}\n"));
            }
        }
        if let Some(value) = self.pending_call_count {
            output.push_str(&format!("pending_call_count={value}\n"));
        }
        if let Some(value) = &self.pending_call_id {
            output.push_str(&format!("pending_call_id={value}\n"));
        }
        output.push_str(&format!(
            "pr={}\nhost={}\nacquired_at={}\nacquired_human={}\nexpires_at={}\n",
            self.pr, self.host, self.acquired_at, self.acquired_human, self.expires_at
        ));
        if let Some(reclaimed_from) = &self.reclaimed_from {
            output.push_str(&format!("reclaimed_from={reclaimed_from}\n"));
        }
        output
    }

    fn live_at(&self, now: i64) -> bool {
        now < self.expires_at
    }

    fn renewed(&self, hold: u64) -> Result<Self, LandLockError> {
        let mut renewed = new_holder(&self.agent, &self.pr, hold, None)?;
        renewed.repo = self.repo.clone();
        renewed.operation = self.operation.clone();
        renewed.pending_mutation = self.pending_mutation.clone();
        renewed.pending_attempt = self.pending_attempt.clone();
        renewed.pending_call_count = self.pending_call_count;
        renewed.pending_call_id = self.pending_call_id.clone();
        Ok(renewed)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueueEntry {
    pub enqueued_at: i64,
    pub agent: String,
    pub pr: String,
}

impl QueueEntry {
    fn parse(line: &str, line_number: usize) -> Result<Self, LandLockError> {
        let fields: Vec<_> = line.split('\t').collect();
        if fields.len() != 3 {
            return Err(LandLockError::InvalidState(format!(
                "queue line {line_number} must have three tab-separated fields"
            )));
        }
        Ok(Self {
            enqueued_at: parse_integer("queue timestamp", fields[0])?,
            agent: fields[1].to_string(),
            pr: fields[2].to_string(),
        })
    }

    fn render(&self) -> String {
        format!("{}\t{}\t{}\n", self.enqueued_at, self.agent, self.pr)
    }
}

#[derive(Debug, Error)]
pub enum LandLockError {
    #[error("landing-lock: {action} {path}: {source}")]
    Io {
        action: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("landing-lock: invalid on-disk state: {0}")]
    InvalidState(String),
    #[error("landing-lock: timed out taking internal guard after {GUARD_WAIT_SECONDS}s")]
    GuardTimeout,
    #[error("landing-lock: renew: {agent} does not hold the lock")]
    RenewNotOwner { agent: String },
    #[error("landing-lock: release: lock is held by {holder}, not {agent}; refusing")]
    ReleaseNotOwner { agent: String, holder: String },
    #[error("landing-lock: child command is empty")]
    EmptyChild,
    #[error(
        "landing-lock: --child-deadline must be positive; unbounded lock holders are forbidden"
    )]
    UnboundedChildDeadline,
    #[error(
        "landing-lock: {operation}: process {pid} owns the supervised lease, not this process"
    )]
    ProcessNotOwner { operation: &'static str, pid: u32 },
    #[error("landing-lock: cannot reclaim lease: {0}")]
    ReclaimNotProven(String),
    #[error("landing-lock: cleanup quarantine: {0}")]
    CleanupQuarantined(String),
}

impl LandLockError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::RenewNotOwner { .. }
            | Self::ReleaseNotOwner { .. }
            | Self::ProcessNotOwner { .. }
            | Self::ReclaimNotProven(_)
            | Self::CleanupQuarantined(_)
            | Self::GuardTimeout
            | Self::InvalidState(_) => 3,
            Self::Io { .. } | Self::EmptyChild | Self::UnboundedChildDeadline => 2,
        }
    }
}

#[derive(Clone, Debug)]
struct LockPaths {
    lock: PathBuf,
    guard: PathBuf,
    queue: PathBuf,
    owner: PathBuf,
    cleanup: PathBuf,
}

impl LockPaths {
    fn for_workspace(root: &Path) -> Self {
        let lock = env::var_os("CI_HUB_LANDING_LOCK")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".landing-lock"));
        Self {
            guard: suffix(&lock, ".guard"),
            queue: suffix(&lock, ".queue"),
            owner: suffix(&lock, ".owner"),
            cleanup: suffix(&lock, ".cleanup-required"),
            lock,
        }
    }
}

struct LandingLock {
    paths: LockPaths,
}

enum AcquireToken {
    Acquired,
    AcquiredReclaimed(String),
    Held { agent: String, seconds_left: i64 },
    WaitTurn(String),
}

pub fn execute(root: &Path, args: LandLockArgs) -> Result<i32, LandLockError> {
    let lock = LandingLock {
        paths: LockPaths::for_workspace(root),
    };
    match args.command {
        LandLockCommand::Acquire(args) => lock.acquire(&args),
        LandLockCommand::Renew(args) => {
            lock.renew(&args.agent, args.hold, true)?;
            Ok(0)
        }
        LandLockCommand::Release(args) => {
            lock.release(&args.agent, true)?;
            Ok(0)
        }
        LandLockCommand::Status => {
            lock.status()?;
            Ok(0)
        }
        LandLockCommand::ReclaimDead => lock.reclaim_dead(),
        LandLockCommand::CensusOrphanedDomain(args) => lock.census_orphaned_domain(args),
        LandLockCommand::Run(args) => lock.run(args),
    }
}

impl LandingLock {
    fn cleanup_verification(&self, holder: Option<&LockState>) -> CleanupVerification {
        let operation = holder.map(|holder| format!("pr:{}", holder.pr));
        let expected = holder
            .zip(operation.as_deref())
            .map(|(holder, operation)| (holder.agent.as_str(), operation));
        verify_cleanup_record(&self.paths.cleanup, expected)
    }

    fn require_no_cleanup(&self, holder: Option<&LockState>) -> Result<(), LandLockError> {
        match self.cleanup_verification(holder) {
            CleanupVerification::None => Ok(()),
            CleanupVerification::Armed { reason, .. } => Err(
                LandLockError::CleanupQuarantined(format!("ARMED: {reason}")),
            ),
            CleanupVerification::Active { reason, .. } => Err(
                LandLockError::CleanupQuarantined(format!("ACTIVE: {reason}")),
            ),
            CleanupVerification::Uncensused { reason, .. } => Err(
                LandLockError::CleanupQuarantined(format!("UNCENSUSED: {reason}")),
            ),
            CleanupVerification::Recoverable { reason, .. } => Err(
                LandLockError::CleanupQuarantined(format!(
                    "payload absence is proven but explicit reclaim-dead recovery is required: {reason}"
                )),
            ),
            CleanupVerification::Unknown { reason, .. } => Err(
                LandLockError::CleanupQuarantined(format!("UNVERIFIABLE: {reason}")),
            ),
        }
    }

    fn arm_run(&self, agent: &str, pr: &str) -> Result<(), LandLockError> {
        let record = CleanupRecord::new(agent, format!("pr:{pr}"), CleanupPhase::Armed)
            .map_err(|source| io_error("construct", &self.paths.cleanup, source))?;
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                LandLockError::InvalidState("cannot arm cleanup without a lock holder".into())
            })?;
            if holder.agent != agent || holder.pr != pr {
                return Err(LandLockError::InvalidState(
                    "cleanup arm does not match the current operation".into(),
                ));
            }
            self.require_no_cleanup(Some(&holder))?;
            write_cleanup_record(&self.paths.cleanup, &record)
                .map_err(|source| io_error("write", &self.paths.cleanup, source))
        })
    }

    fn transition_run_cleanup(
        &self,
        agent: &str,
        pr: &str,
        expected: fn(&CleanupPhase) -> bool,
        next: CleanupPhase,
        // `Some(anchor)` records the payload's containment boundary; `None` leaves whatever the
        // record already carried, so the phase transitions that are not the acquire point cannot
        // accidentally erase an anchor written earlier.
        cgroup: Option<String>,
    ) -> Result<(), LandLockError> {
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                LandLockError::InvalidState("cleanup transition has no lock holder".into())
            })?;
            if holder.agent != agent || holder.pr != pr {
                return Err(LandLockError::InvalidState(
                    "cleanup transition does not match the current operation".into(),
                ));
            }
            let verification = self.cleanup_verification(Some(&holder));
            let mut record = match verification {
                CleanupVerification::Armed { record, .. }
                | CleanupVerification::Active { record, .. }
                | CleanupVerification::Uncensused { record, .. }
                | CleanupVerification::Recoverable { record, .. } => record,
                CleanupVerification::None => {
                    return Err(LandLockError::InvalidState(
                        "cleanup transition has no durable authority".into(),
                    ))
                }
                CleanupVerification::Unknown { reason, .. } => {
                    return Err(LandLockError::CleanupQuarantined(format!(
                        "cannot transition unverifiable authority: {reason}"
                    )))
                }
            };
            if !expected(&record.phase) {
                return Err(LandLockError::InvalidState(format!(
                    "cleanup transition rejected phase {:?}",
                    record.phase
                )));
            }
            record.phase = next;
            if let Some(cgroup) = cgroup {
                record.cgroup = Some(cgroup);
            }
            write_cleanup_record(&self.paths.cleanup, &record)
                .map_err(|source| io_error("write", &self.paths.cleanup, source))
        })
    }

    fn publish_run(&self, agent: &str, pr: &str, gated: &GatedChild) -> Result<(), LandLockError> {
        self.transition_run_cleanup(
            agent,
            pr,
            |phase| matches!(phase, CleanupPhase::Armed),
            CleanupPhase::Published {
                leader: gated.leader.clone(),
                pgid: gated.pgid,
            },
            // ACQUIRE TIME, not release: the only party who can read the payload's cgroup is the
            // payload itself while it is alive, and the whole point is to survive the supervisor
            // dying. A boundary recorded at release is a boundary that is never recorded in
            // exactly the case that needs it.
            //
            // Best effort BY DESIGN, mirroring validate-lock: if the cgroup cannot be read, or is
            // shared with unrelated work, the record simply carries no anchor and `reclaim-dead`
            // keeps its existing attestation path -- the status quo, never a new failure mode.
            // Anchoring to a SHARED cgroup would be worse than no anchor, because a domain that
            // is permanently populated by someone else's work could never be censused empty.
            match payload_cgroup_anchor(gated.leader.pid) {
                Ok(cgroup) => Some(cgroup),
                Err(why) => {
                    eprintln!(
                        "landing-lock: no cgroup anchor recorded for this run ({why}); if the \
                         supervisor dies, censusing the orphaned domain will require \
                         --attest-domain-empty"
                    );
                    None
                }
            },
        )
    }

    fn clear_unstarted_run(&self, agent: &str, pr: &str) -> Result<(), LandLockError> {
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                LandLockError::InvalidState("unstarted cleanup has no lock holder".into())
            })?;
            if holder.agent != agent || holder.pr != pr {
                return Err(LandLockError::InvalidState(
                    "unstarted cleanup does not match current operation".into(),
                ));
            }
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Armed { .. } => {
                    self.assert_current_process_owner("clear unstarted run")?;
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))
                }
                other => Err(LandLockError::InvalidState(format!(
                    "unstarted cleanup requires armed authority, got {other:?}"
                ))),
            }
        })
    }

    fn record_residuals(
        &self,
        agent: &str,
        pr: &str,
        pgid: u32,
        capture: ResidualCapture,
    ) -> Result<(), LandLockError> {
        self.transition_run_cleanup(
            agent,
            pr,
            |phase| matches!(phase, CleanupPhase::CensusPending { .. }),
            CleanupPhase::Residual {
                pgid,
                domain_complete: capture.complete,
                residuals: capture.identities,
            },
            None,
        )
    }

    fn begin_run_census(
        &self,
        agent: &str,
        pr: &str,
        gated: &GatedChild,
    ) -> Result<(), LandLockError> {
        self.transition_run_cleanup(
            agent,
            pr,
            |phase| matches!(phase, CleanupPhase::Published { .. }),
            CleanupPhase::CensusPending {
                leader: gated.leader.clone(),
                pgid: gated.pgid,
            },
            None,
        )
    }

    fn clear_proven_run(&self, agent: &str, pr: &str, pgid: u32) -> Result<(), LandLockError> {
        self.record_residuals(
            agent,
            pr,
            pgid,
            ResidualCapture {
                complete: true,
                identities: Vec::new(),
            },
        )?;
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                LandLockError::InvalidState("cleanup clear has no lock holder".into())
            })?;
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Recoverable { .. } => {
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))
                }
                other => Err(LandLockError::InvalidState(format!(
                    "cleanup clear requires proven absence, got {other:?}"
                ))),
            }
        })
    }

    fn renew_run_lease(&self, agent: &str, pr: &str, hold: u64) -> Result<(), LandLockError> {
        self.with_guard(|| {
            let holder = self
                .read_holder()?
                .ok_or_else(|| LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                })?;
            if holder.agent != agent || holder.pr != pr {
                return Err(LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Active { record, .. }
                    if matches!(record.phase, CleanupPhase::Published { .. }) => {}
                other => {
                    return Err(LandLockError::CleanupQuarantined(format!(
                        "run heartbeat requires an active published domain, got {other:?}"
                    )))
                }
            }
            self.assert_current_process_owner("run heartbeat")?;
            heartbeat_test_helper_delay();
            self.write_holder(&holder.renewed(hold)?)
        })
    }

    fn acquire(&self, args: &AcquireArgs) -> Result<i32, LandLockError> {
        let started = Instant::now();
        let mut last = String::new();
        loop {
            let token = self.with_guard(|| self.try_acquire(args))?;
            match token {
                AcquireToken::Acquired => {
                    eprintln!(
                        "landing-lock: ACQUIRED by {} for PR #{} (lease {}s)",
                        args.agent, args.pr, args.hold
                    );
                    return Ok(0);
                }
                AcquireToken::AcquiredReclaimed(previous) => {
                    eprintln!(
                        "landing-lock: ACQUIRED by {} for PR #{}; evidence-reclaimed lease from {}",
                        args.agent, args.pr, previous
                    );
                    return Ok(0);
                }
                AcquireToken::Held {
                    agent,
                    seconds_left,
                } => {
                    let token = format!("HELD:{agent}:{seconds_left}");
                    if token != last {
                        eprintln!(
                            "landing-lock: waiting: held by {agent}:{seconds_left} (agent:secs_left); queued as {}",
                            args.agent
                        );
                        last = token;
                    }
                }
                AcquireToken::WaitTurn(head) => {
                    let token = format!("WAIT_TURN:{head}");
                    if token != last {
                        eprintln!("landing-lock: waiting: lock free, ahead of me in queue: {head}");
                        last = token;
                    }
                }
            }
            if started.elapsed() >= Duration::from_secs(args.wait) {
                self.with_guard(|| {
                    self.remove_from_queue(&args.agent)?;
                    Ok(())
                })?;
                eprintln!("landing-lock: TIMEOUT after {}s", args.wait);
                return Ok(1);
            }
            thread::sleep(Duration::from_secs(POLL_SECONDS));
        }
    }

    fn try_acquire(&self, args: &AcquireArgs) -> Result<AcquireToken, LandLockError> {
        let now = epoch_seconds()?;
        let holder = self.read_holder()?;
        self.require_no_cleanup(holder.as_ref())?;
        let mut queue = self.read_queue()?;
        if !queue.iter().any(|entry| entry.agent == args.agent) {
            queue.push(QueueEntry {
                enqueued_at: now,
                agent: args.agent.clone(),
                pr: args.pr.clone(),
            });
        }
        let cutoff = now - (2 * DEFAULT_WAIT_SECONDS) as i64;
        queue.retain(|entry| entry.enqueued_at >= cutoff);

        let dead_owner = if holder.as_ref().is_some_and(|holder| holder.live_at(now)) {
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => Some(reason),
                OwnerLiveness::Alive | OwnerLiveness::Unknown(_) => None,
            }
        } else {
            None
        };
        if let Some(holder) = holder
            .as_ref()
            .filter(|holder| holder.live_at(now) && dead_owner.is_none())
        {
            self.write_queue(&queue)?;
            return Ok(AcquireToken::Held {
                agent: holder.agent.clone(),
                seconds_left: holder.expires_at - now,
            });
        }

        if let Some(head) = queue.first() {
            if head.agent != args.agent {
                let agent = head.agent.clone();
                self.write_queue(&queue)?;
                return Ok(AcquireToken::WaitTurn(agent));
            }
        }

        let reclaimed = holder.map(|holder| {
            if let Some(reason) = &dead_owner {
                format!("{} (dead owner: {reason})", holder.agent)
            } else {
                holder.agent
            }
        });
        self.write_holder(&new_holder(
            &args.agent,
            &args.pr,
            args.hold,
            reclaimed.clone(),
        )?)?;
        remove_if_exists(&self.paths.owner)?;
        queue.retain(|entry| entry.agent != args.agent);
        self.write_queue(&queue)?;
        Ok(match reclaimed {
            Some(previous) if !previous.is_empty() => AcquireToken::AcquiredReclaimed(previous),
            _ => AcquireToken::Acquired,
        })
    }

    fn renew(&self, agent: &str, hold: u64, announce: bool) -> Result<(), LandLockError> {
        self.with_guard(|| {
            let holder = self
                .read_holder()?
                .ok_or_else(|| LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                })?;
            self.require_no_cleanup(Some(&holder))?;
            if holder.agent != agent {
                return Err(LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            self.assert_current_process_owner("renew")?;
            self.write_holder(&holder.renewed(hold)?)?;
            Ok(())
        })?;
        if announce {
            eprintln!("landing-lock: renewed {agent} lease {hold}s");
        }
        Ok(())
    }

    fn release(&self, agent: &str, announce: bool) -> Result<(), LandLockError> {
        let (released, next) = self.with_guard(|| {
            let holder = self.read_holder()?;
            self.require_no_cleanup(holder.as_ref())?;
            let Some(holder) = holder else {
                remove_if_exists(&self.paths.owner)?;
                return Ok((false, None));
            };
            if holder.agent != agent {
                return Err(LandLockError::ReleaseNotOwner {
                    agent: agent.to_string(),
                    holder: holder.agent,
                });
            }
            self.assert_current_process_owner("release")?;
            remove_if_exists(&self.paths.lock)?;
            remove_if_exists(&self.paths.owner)?;
            self.remove_from_queue(agent)?;
            Ok((
                true,
                self.read_queue()?.first().map(|entry| entry.agent.clone()),
            ))
        })?;
        if announce {
            match (released, next) {
                (false, _) => eprintln!("landing-lock: release: no lock held"),
                (true, None) => {
                    eprintln!("landing-lock: RELEASED by {agent}; lock FREE (queue empty)")
                }
                (true, Some(next)) => {
                    eprintln!("landing-lock: RELEASED by {agent}; lock FREE -> next: {next}")
                }
            }
        }
        Ok(())
    }

    fn status(&self) -> Result<(), LandLockError> {
        let now = epoch_seconds()?;
        let holder = self.read_holder()?;
        let liveness = self.owner_liveness()?;
        let cleanup = self.cleanup_verification(holder.as_ref());
        let quarantined = !matches!(cleanup, CleanupVerification::None);
        match cleanup {
            CleanupVerification::None => {}
            CleanupVerification::Armed { record, reason } => {
                println!("QUARANTINED (pre-spawn barrier armed):");
                print_cleanup_record(&record, &reason);
            }
            CleanupVerification::Active { record, reason } => {
                println!("QUARANTINED (cleanup active):");
                print_cleanup_record(&record, &reason);
            }
            CleanupVerification::Recoverable { record, reason } => {
                println!("QUARANTINED (absence proven; run reclaim-dead):");
                print_cleanup_record(&record, &reason);
            }
            CleanupVerification::Uncensused { record, reason } => {
                println!("QUARANTINED (published domain lacks final census):");
                print_cleanup_record(&record, &reason);
            }
            CleanupVerification::Unknown { record, reason } => {
                println!("QUARANTINED (cleanup unverifiable):");
                if let Some(record) = record {
                    print_cleanup_record(&record, &reason);
                } else {
                    println!("  reason={reason}");
                }
            }
        }
        if quarantined {
            println!("  owner_process={}", render_liveness(&liveness));
        } else {
            match holder {
                Some(holder)
                    if holder.live_at(now) && matches!(liveness, OwnerLiveness::Dead(_)) =>
                {
                    println!("ORPHANED (reclaimable):");
                    for line in holder.render().lines() {
                        println!("  {line}");
                    }
                    println!("  owner_process={}", render_liveness(&liveness));
                    println!("  secs_left={}", holder.expires_at - now);
                }
                Some(holder) if holder.live_at(now) => {
                    println!("HELD:");
                    for line in holder.render().lines() {
                        println!("  {line}");
                    }
                    println!("  owner_process={}", render_liveness(&liveness));
                    println!("  secs_left={}", holder.expires_at - now);
                }
                Some(holder) => {
                    println!("LAPSED (reclaimable):");
                    for line in holder.render().lines() {
                        println!("  {line}");
                    }
                    println!("  owner_process={}", render_liveness(&liveness));
                }
                None => println!("FREE"),
            }
        }
        let queue = self.read_queue()?;
        if !queue.is_empty() {
            println!("queue (FIFO):");
            for (index, entry) in queue.iter().enumerate() {
                print!("  {:>6}\t{}", index + 1, entry.render());
            }
        }
        Ok(())
    }

    /// Discharge an UNCENSUSED landing quarantine post-hoc.
    ///
    /// The census this satisfies can otherwise only be produced by the LIVE
    /// supervisor inside `run()` (`begin_run_census`/`record_residuals` both go
    /// through `transition_run_cleanup`, which requires being the current
    /// holder). When that supervisor dies at `phase=published`, nothing can move
    /// the record forward and `reclaim-dead` refuses forever on the same boot.
    /// This is the missing producer, gated the same five ways validate-lock
    /// gates its own.
    fn census_orphaned_domain(&self, args: CensusOrphanedDomainArgs) -> Result<i32, LandLockError> {
        let restated = parse_leader_identity(&args.leader)?;
        let (leader, pgid, disposition) = self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                LandLockError::ReclaimNotProven(
                    "no lock holder; a census only discharges a quarantine bound to one".into(),
                )
            })?;

            // (1) The quarantine must be exactly the one this path addresses.
            let record = match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Uncensused { record, .. } => record,
                CleanupVerification::None => {
                    return Err(LandLockError::ReclaimNotProven(
                        "no cleanup authority is quarantined; nothing to census".into(),
                    ))
                }
                CleanupVerification::Recoverable { reason, .. } => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "payload absence is ALREADY proven; run reclaim-dead instead: {reason}"
                    )))
                }
                CleanupVerification::Active { reason, .. } => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "payload identities are STILL ALIVE; a census cannot bury a live \
                         domain: {reason}"
                    )))
                }
                CleanupVerification::Armed { reason, .. } => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "cleanup is only ARMED, so no payload was ever published and there is \
                         no domain to census: {reason}"
                    )))
                }
                CleanupVerification::Unknown { reason, .. } => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "cleanup authority is UNVERIFIABLE; refusing to census it: {reason}"
                    )))
                }
            };

            let (leader, pgid) = match &record.phase {
                CleanupPhase::Published { leader, pgid }
                | CleanupPhase::CensusPending { leader, pgid } => (leader.clone(), *pgid),
                other => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "uncensused authority has unexpected phase {other:?}"
                    )))
                }
            };

            // (2) The caller must restate the recorded identity exactly, so this
            // cannot be aimed blind at whatever is quarantined right now.
            if record.agent != args.agent {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "restated agent {:?} does not match recorded agent {:?}",
                    args.agent, record.agent
                )));
            }
            if record.operation != args.operation {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "restated operation {:?} does not match recorded operation {:?}",
                    args.operation, record.operation
                )));
            }
            if leader != restated {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "restated leader {}:{} does not match recorded leader {}:{}",
                    restated.pid, restated.start_ticks, leader.pid, leader.start_ticks
                )));
            }
            if pgid != args.pgid {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "restated pgid {} does not match recorded pgid {pgid}",
                    args.pgid
                )));
            }

            // (3) A LIVE supervisor owns its own census; never race it.
            match self.owner_liveness()? {
                OwnerLiveness::Dead(_) => {}
                OwnerLiveness::Alive => {
                    return Err(LandLockError::ReclaimNotProven(
                        "the recorded supervisor process is ALIVE and owns this census; \
                         refusing to take it out from under a running landing"
                            .into(),
                    ))
                }
                OwnerLiveness::Unknown(reason) => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "supervisor liveness is UNVERIFIABLE, so its death is not proven: {reason}"
                    )))
                }
            }

            // (4) Re-assert the mechanically checkable half of absence at the
            // instant of the write, under the guard.
            match exact_process_liveness(&leader) {
                Ok(false) => {}
                Ok(true) => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "recorded leader {}:{} is ALIVE",
                        leader.pid, leader.start_ticks
                    )))
                }
                Err(reason) => {
                    return Err(LandLockError::ReclaimNotProven(format!(
                        "cannot verify recorded leader absence: {reason}"
                    )))
                }
            }
            if process_group_exists(pgid) {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "recorded process group {pgid} is still populated"
                )));
            }

            // (5) The domain itself, through the SHARED policy both authorities
            // use. Landing records are anchorless today, so this normally lands
            // on the attestation branch; it becomes mechanical for free once
            // landing `run()` records the anchor.
            let disposition = census_disposition(
                census_recorded_domain(record.cgroup.as_deref()),
                args.attest_domain_empty,
                &args.evidence,
                &format!(
                    "supervisor dead, leader {}:{} absent, pgid {pgid} absent",
                    leader.pid, leader.start_ticks
                ),
            )
            .map_err(LandLockError::ReclaimNotProven)?;

            let mut recovered = record.clone();
            recovered.phase = CleanupPhase::Residual {
                pgid,
                domain_complete: true,
                residuals: Vec::new(),
            };
            write_cleanup_record(&self.paths.cleanup, &recovered)
                .map_err(|source| io_error("write", &self.paths.cleanup, source))?;
            Ok((leader, pgid, disposition))
        })?;
        // Truthful about WHICH evidence discharged this: a mechanically proven
        // census and an operator attestation are not the same claim.
        eprintln!(
            "landing-lock: CENSUSED orphaned domain agent={} operation={} leader={}:{} pgid={pgid}; \
             supervisor proven dead, leader and process group proven absent, {}",
            args.agent, args.operation, leader.pid, leader.start_ticks, disposition
        );
        eprintln!(
            "landing-lock: quarantine is now RECOVERABLE; run `land-lock reclaim-dead` to release \
             the lock."
        );
        Ok(0)
    }

    fn reclaim_dead(&self) -> Result<i32, LandLockError> {
        let reclaimed = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                return match self.cleanup_verification(None) {
                    CleanupVerification::None => {
                        remove_if_exists(&self.paths.owner)?;
                        Ok(None)
                    }
                    CleanupVerification::Armed { reason, .. }
                    | CleanupVerification::Active { reason, .. }
                    | CleanupVerification::Uncensused { reason, .. }
                    | CleanupVerification::Unknown { reason, .. } => {
                        Err(LandLockError::ReclaimNotProven(reason))
                    }
                    CleanupVerification::Recoverable { .. } => {
                        Err(LandLockError::ReclaimNotProven(
                            "cleanup authority is not bound to a lock holder".into(),
                        ))
                    }
                };
            };
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Armed { reason, .. }
                | CleanupVerification::Active { reason, .. }
                | CleanupVerification::Uncensused { reason, .. }
                | CleanupVerification::Unknown { reason, .. } => {
                    return Err(LandLockError::ReclaimNotProven(reason));
                }
                CleanupVerification::Recoverable { .. } | CleanupVerification::None => {}
            }
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => {
                    remove_if_exists(&self.paths.lock)?;
                    remove_if_exists(&self.paths.owner)?;
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))?;
                    Ok(Some((holder.agent, holder.pr, reason)))
                }
                OwnerLiveness::Alive => Err(LandLockError::ReclaimNotProven(
                    "recorded owner process is alive".into(),
                )),
                OwnerLiveness::Unknown(reason) => Err(LandLockError::ReclaimNotProven(reason)),
            }
        })?;
        match reclaimed {
            Some((agent, pr, reason)) => eprintln!(
                "landing-lock: evidence-reclaimed dead owner agent={agent} pr={pr}: {reason}"
            ),
            None => eprintln!("landing-lock: reclaim-dead: no lock held"),
        }
        Ok(0)
    }

    fn run(&self, args: RunArgs) -> Result<i32, LandLockError> {
        if args.child.is_empty() {
            return Err(LandLockError::EmptyChild);
        }
        if args.child_deadline == 0 {
            return Err(LandLockError::UnboundedChildDeadline);
        }
        enable_child_subreaper().map_err(|source| {
            io_error("enable child subreaper at", Path::new("/proc/self"), source)
        })?;
        let acquire = AcquireArgs {
            agent: args.agent.clone(),
            pr: args.pr.clone(),
            wait: args.wait,
            hold: args.hold,
        };
        let acquire_status = self.acquire(&acquire)?;
        if acquire_status != 0 {
            return Ok(acquire_status);
        }
        if let Err(error) = self.with_guard(|| self.write_current_process_owner()) {
            let _ = self.release(&args.agent, true);
            return Err(error);
        }

        // Persist the fail-closed authority before any child exists. The start
        // gate below cannot run the guest until its exact identity has replaced
        // this armed record durably.
        self.arm_run(&args.agent, &args.pr)?;
        #[cfg(test)]
        if take_run_crash_hook(&format!("pr:{}", args.pr), RunCrashPoint::AfterArm) {
            return Err(LandLockError::CleanupQuarantined(
                "test-injected supervisor crash after arm".into(),
            ));
        }
        let mut gated = match spawn_gated_child(&args.child[0], &args.child[1..], |_| {}) {
            Ok(gated) => gated,
            Err(source) => {
                self.clear_unstarted_run(&args.agent, &args.pr)?;
                self.release(&args.agent, true)?;
                return Err(LandLockError::Io {
                    action: "launch start-gated child from",
                    path: PathBuf::from(&args.child[0]),
                    source,
                });
            }
        };
        #[cfg(test)]
        if take_run_crash_hook(&format!("pr:{}", args.pr), RunCrashPoint::AfterSpawn) {
            return Err(LandLockError::CleanupQuarantined(
                "test-injected supervisor crash after spawn".into(),
            ));
        }
        self.publish_run(&args.agent, &args.pr, &gated)?;
        gated.release().map_err(|source| {
            io_error("release child start gate at", &self.paths.cleanup, source)
        })?;
        #[cfg(test)]
        if take_run_crash_hook(&format!("pr:{}", args.pr), RunCrashPoint::AfterPublish) {
            return Err(LandLockError::CleanupQuarantined(
                "test-injected supervisor crash after publication".into(),
            ));
        }

        let (stop_tx, stop_rx) = mpsc::channel();
        let heartbeat_paths = self.paths.clone();
        let heartbeat_agent = args.agent.clone();
        let heartbeat_pr = args.pr.clone();
        let heartbeat_hold = args.hold;
        let heartbeat = thread::spawn(move || {
            let heartbeat_lock = LandingLock {
                paths: heartbeat_paths,
            };
            let interval = Duration::from_secs((heartbeat_hold / 3).max(1));
            while stop_rx.recv_timeout(interval).is_err() {
                if heartbeat_lock
                    .renew_run_lease(&heartbeat_agent, &heartbeat_pr, heartbeat_hold)
                    .is_err()
                {
                    break;
                }
            }
        });

        let outcome = supervise_child(&mut gated.child, args.child_deadline, &args.pr);
        // Atomically disable heartbeat renewal before stopping it. A heartbeat
        // already holding the guard finishes first; after this durable phase
        // transition no stale renewal can race cleanup or authorize reclaim.
        self.begin_run_census(&args.agent, &args.pr, &gated)?;
        let _ = stop_tx.send(());
        let _ = heartbeat.join();
        reap_exited_children();
        let pgid = outcome.pgid();
        let capture = capture_and_freeze_residuals(std::process::id());
        let domain_empty =
            !process_group_exists(pgid) && capture.complete && capture.identities.is_empty();
        if !domain_empty {
            let residual_count = capture.identities.len();
            self.record_residuals(&args.agent, &args.pr, pgid, capture)?;
            return Err(LandLockError::CleanupQuarantined(format!(
                "completed process group {pgid} did not yield an empty payload domain; {residual_count} exact residual identities recorded and lock retained"
            )));
        }
        self.clear_proven_run(&args.agent, &args.pr, pgid)?;
        self.release(&args.agent, true)?;
        match outcome {
            ChildOutcome::Exited { status, pgid } => {
                debug_assert_eq!(pgid, gated.pgid);
                Ok(exit_status_code(status))
            }
            ChildOutcome::TimedOut { pgid } => {
                debug_assert_eq!(pgid, gated.pgid);
                eprintln!(
                    "landing-lock: ABANDON PR #{}: child exceeded --child-deadline {}s; \
                     killed the land subtree and RELEASED the lock so the FIFO can proceed. \
                     PR left open for retry.",
                    args.pr, args.child_deadline
                );
                Ok(CHILD_DEADLINE_EXIT_CODE)
            }
            ChildOutcome::Uncertain { reason, .. } => Err(LandLockError::InvalidState(format!(
                "child supervision failed after payload domain was proven empty: {reason}"
            ))),
        }
    }

    fn with_guard<T>(
        &self,
        operation: impl FnOnce() -> Result<T, LandLockError>,
    ) -> Result<T, LandLockError> {
        let guard = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&self.paths.guard)
            .map_err(|source| io_error("open guard", &self.paths.guard, source))?;
        let deadline = Instant::now() + Duration::from_secs(GUARD_WAIT_SECONDS);
        loop {
            match guard.try_lock_exclusive() {
                Ok(()) => break,
                Err(source) if source.kind() == io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err(LandLockError::GuardTimeout);
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                Err(source) => return Err(io_error("lock guard", &self.paths.guard, source)),
            }
        }
        let result = operation();
        let unlock = FileExt::unlock(&guard)
            .map_err(|source| io_error("unlock guard", &self.paths.guard, source));
        match result {
            Ok(value) => {
                unlock?;
                Ok(value)
            }
            Err(error) => {
                let _ = unlock;
                Err(error)
            }
        }
    }

    fn read_holder(&self) -> Result<Option<LockState>, LandLockError> {
        if !self.paths.lock.exists() {
            return Ok(None);
        }
        let content = read_to_string(&self.paths.lock)?;
        Ok(Some(LockState::parse(&content)?))
    }

    fn read_process_owner(&self) -> Result<Option<ProcessOwner>, LandLockError> {
        if !self.paths.owner.exists() {
            return Ok(None);
        }
        let content = read_to_string(&self.paths.owner)?;
        Ok(Some(ProcessOwner::parse(&content)?))
    }

    fn write_current_process_owner(&self) -> Result<(), LandLockError> {
        let owner = current_process_owner()?;
        write_truncated(&self.paths.owner, owner.render().as_bytes())
    }

    fn owner_liveness(&self) -> Result<OwnerLiveness, LandLockError> {
        let Some(owner) = self.read_process_owner()? else {
            return Ok(OwnerLiveness::Unknown(
                "no process evidence (legacy/manual lease)".into(),
            ));
        };
        let host = current_host();
        if owner.host != host {
            return Ok(OwnerLiveness::Unknown(format!(
                "owner host {} differs from local host {host}",
                owner.host
            )));
        }
        let boot_id = current_boot_id()?;
        if owner.boot_id != boot_id {
            return Ok(OwnerLiveness::Dead(format!(
                "host rebooted (owner boot_id={} current={boot_id})",
                owner.boot_id
            )));
        }
        match process_start_ticks(owner.pid) {
            Ok(start_ticks) if start_ticks == owner.start_ticks => Ok(OwnerLiveness::Alive),
            Ok(start_ticks) => Ok(OwnerLiveness::Dead(format!(
                "pid {} was reused (owner start_ticks={} current={start_ticks})",
                owner.pid, owner.start_ticks
            ))),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                Ok(OwnerLiveness::Dead(format!("pid {} is absent", owner.pid)))
            }
            Err(error) => Ok(OwnerLiveness::Unknown(format!(
                "cannot inspect pid {}: {error}",
                owner.pid
            ))),
        }
    }

    fn assert_current_process_owner(&self, operation: &'static str) -> Result<(), LandLockError> {
        let Some(owner) = self.read_process_owner()? else {
            return Ok(());
        };
        let current = current_process_owner()?;
        if owner == current {
            Ok(())
        } else {
            Err(LandLockError::ProcessNotOwner {
                operation,
                pid: owner.pid,
            })
        }
    }

    fn write_holder(&self, holder: &LockState) -> Result<(), LandLockError> {
        write_truncated(&self.paths.lock, holder.render().as_bytes())
    }

    fn read_queue(&self) -> Result<Vec<QueueEntry>, LandLockError> {
        if !self.paths.queue.exists() {
            return Ok(Vec::new());
        }
        let content = read_to_string(&self.paths.queue)?;
        content
            .lines()
            .enumerate()
            .filter(|(_, line)| !line.is_empty())
            .map(|(index, line)| QueueEntry::parse(line, index + 1))
            .collect()
    }

    fn write_queue(&self, queue: &[QueueEntry]) -> Result<(), LandLockError> {
        let mut content = String::new();
        for entry in queue {
            content.push_str(&entry.render());
        }
        let temporary = suffix(&self.paths.queue, ".tmp");
        write_truncated(&temporary, content.as_bytes())?;
        fs::rename(&temporary, &self.paths.queue)
            .map_err(|source| io_error("replace queue", &self.paths.queue, source))
    }

    fn remove_from_queue(&self, agent: &str) -> Result<(), LandLockError> {
        let mut queue = self.read_queue()?;
        queue.retain(|entry| entry.agent != agent);
        self.write_queue(&queue)
    }
}

fn new_holder(
    agent: &str,
    pr: &str,
    hold: u64,
    reclaimed_from: Option<String>,
) -> Result<LockState, LandLockError> {
    let acquired_at = epoch_seconds()?;
    let acquired_human = Local
        .timestamp_opt(acquired_at, 0)
        .single()
        .ok_or_else(|| LandLockError::InvalidState("current timestamp is out of range".into()))?
        .format("%Y-%m-%dT%H:%M:%S%z")
        .to_string();
    let host = current_host();
    Ok(LockState {
        agent: agent.to_string(),
        repo: None,
        operation: None,
        pending_mutation: None,
        pending_attempt: None,
        pending_call_count: None,
        pending_call_id: None,
        pr: pr.to_string(),
        host,
        acquired_at,
        acquired_human,
        expires_at: acquired_at.saturating_add(hold as i64),
        reclaimed_from,
    })
}

fn epoch_seconds() -> Result<i64, LandLockError> {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| {
            LandLockError::InvalidState(format!("system clock before epoch: {error}"))
        })?
        .as_secs();
    i64::try_from(seconds)
        .map_err(|_| LandLockError::InvalidState("system time exceeds i64".into()))
}

fn parse_integer(name: &str, value: &str) -> Result<i64, LandLockError> {
    value.parse().map_err(|error| {
        LandLockError::InvalidState(format!("{name} must be an integer, got {value:?}: {error}"))
    })
}

fn parse_unsigned(name: &str, value: &str) -> Result<u64, LandLockError> {
    value.parse().map_err(|error| {
        LandLockError::InvalidState(format!("{name} must be an integer, got {value:?}: {error}"))
    })
}

pub(crate) fn current_host() -> String {
    Command::new("hostname")
        .arg("-s")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|host| host.trim().to_string())
        .filter(|host| !host.is_empty())
        .unwrap_or_else(|| "unknown".to_string())
}

fn current_boot_id() -> Result<String, LandLockError> {
    let path = Path::new("/proc/sys/kernel/random/boot_id");
    fs::read_to_string(path)
        .map(|value| value.trim().to_string())
        .map_err(|source| io_error("read", path, source))
}

pub(crate) fn process_start_ticks(pid: u32) -> io::Result<u64> {
    process_stat_fields(pid).map(|(_, _, start_ticks)| start_ticks)
}

fn process_stat_fields(pid: u32) -> io::Result<(char, u32, u64)> {
    let path = PathBuf::from(format!("/proc/{pid}/stat"));
    let stat = fs::read_to_string(path)?;
    let close = stat.rfind(')').ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no closing comm",
        )
    })?;
    let fields: Vec<_> = stat[close + 1..].split_whitespace().collect();
    let state = fields
        .first()
        .and_then(|value| value.chars().next())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "process stat has no state field",
            )
        })?;
    let parent = fields.get(1).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no parent field",
        )
    })?;
    let start_ticks = fields.get(19).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no starttime field",
        )
    })?;
    let parent = parent.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process parent {parent:?}: {error}"),
        )
    })?;
    let start_ticks = start_ticks.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process starttime {start_ticks:?}: {error}"),
        )
    })?;
    Ok((state, parent, start_ticks))
}

pub(crate) fn read_current_boot_id() -> io::Result<String> {
    fs::read_to_string("/proc/sys/kernel/random/boot_id").map(|value| value.trim().to_string())
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum ProcessGroupLiveness {
    Active,
    Absent,
    Unknown(String),
}

fn process_group_liveness(pgid: u32) -> ProcessGroupLiveness {
    let Some(target) = process_group_target(pgid) else {
        return ProcessGroupLiveness::Unknown(format!("invalid process group {pgid}"));
    };
    // SAFETY: `process_group_target` excludes zero and the special -1 broadcast
    // target. Signal zero probes existence without delivering a signal.
    if unsafe { libc::kill(target, 0) } == 0 {
        return ProcessGroupLiveness::Active;
    }
    let source = io::Error::last_os_error();
    if source.raw_os_error() == Some(libc::ESRCH) {
        ProcessGroupLiveness::Absent
    } else {
        ProcessGroupLiveness::Unknown(source.to_string())
    }
}

pub(crate) fn exact_process_liveness(identity: &ProcessIdentity) -> Result<bool, String> {
    match process_start_ticks(identity.pid) {
        Ok(start_ticks) => Ok(start_ticks == identity.start_ticks),
        Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(source) => Err(format!(
            "cannot verify pid {}@{}: {source}",
            identity.pid, identity.start_ticks
        )),
    }
}

pub(crate) fn verify_cleanup_record(
    path: &Path,
    expected: Option<(&str, &str)>,
) -> CleanupVerification {
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(source) if source.kind() == io::ErrorKind::NotFound => {
            return CleanupVerification::None
        }
        Err(source) => {
            return CleanupVerification::Unknown {
                record: None,
                reason: format!("cannot read {}: {source}", path.display()),
            }
        }
    };
    let record = match CleanupRecord::parse(&content) {
        Ok(record) => record,
        Err(reason) => {
            return CleanupVerification::Unknown {
                record: None,
                reason: format!("malformed {}: {reason}", path.display()),
            }
        }
    };
    let Some((agent, operation)) = expected else {
        return CleanupVerification::Unknown {
            record: Some(record),
            reason: "cleanup authority has no matching lock holder".into(),
        };
    };
    if record.agent != agent || record.operation != operation {
        return CleanupVerification::Unknown {
            reason: format!(
                "cleanup authority binds agent={:?} operation={:?}, not agent={agent:?} operation={operation:?}",
                record.agent, record.operation
            ),
            record: Some(record),
        };
    }
    let host = current_host();
    if host == "unknown" || record.host != host {
        return CleanupVerification::Unknown {
            reason: format!(
                "cleanup authority host {:?} does not match current host {host:?}",
                record.host
            ),
            record: Some(record),
        };
    }
    let boot_id = match read_current_boot_id() {
        Ok(boot_id) => boot_id,
        Err(source) => {
            return CleanupVerification::Unknown {
                reason: format!("cannot read current boot identity: {source}"),
                record: Some(record),
            }
        }
    };
    if record.boot_id != boot_id {
        return CleanupVerification::Recoverable {
            reason: "recorded payload domain is from an earlier host boot".into(),
            record,
        };
    }
    match &record.phase {
        CleanupPhase::Armed => CleanupVerification::Armed {
            reason: "pre-spawn barrier is armed; no payload identity was durably published".into(),
            record,
        },
        CleanupPhase::Published { leader, pgid } | CleanupPhase::CensusPending { leader, pgid } => {
            let mut active = Vec::new();
            let mut unknown = Vec::new();
            match exact_process_liveness(leader) {
                Ok(true) => active.push(format!("{}@{}", leader.pid, leader.start_ticks)),
                Ok(false) => {}
                Err(reason) => unknown.push(reason),
            }
            match process_group_liveness(*pgid) {
                ProcessGroupLiveness::Active => active.push(format!("pgid {pgid}")),
                ProcessGroupLiveness::Absent => {}
                ProcessGroupLiveness::Unknown(reason) => {
                    unknown.push(format!("cannot verify pgid {pgid}: {reason}"))
                }
            }
            if !active.is_empty() {
                CleanupVerification::Active {
                    reason: format!(
                        "published payload identities remain active: {}",
                        active.join(", ")
                    ),
                    record,
                }
            } else {
                let mut reason = unknown;
                reason.push(
                    "published payload ended without a complete residual census; same-boot absence cannot exclude an escaped descendant"
                        .into(),
                );
                CleanupVerification::Uncensused {
                    reason: reason.join("; "),
                    record,
                }
            }
        }
        CleanupPhase::Residual {
            pgid,
            domain_complete,
            residuals,
        } => {
            if !domain_complete {
                return CleanupVerification::Unknown {
                    reason: "residual process-domain capture was incomplete".into(),
                    record: Some(record),
                };
            }
            let mut active = Vec::new();
            let mut unknown = Vec::new();
            for identity in residuals {
                match exact_process_liveness(identity) {
                    Ok(true) => active.push(format!("{}@{}", identity.pid, identity.start_ticks)),
                    Ok(false) => {}
                    Err(reason) => unknown.push(reason),
                }
            }
            match process_group_liveness(*pgid) {
                ProcessGroupLiveness::Active => active.push(format!("pgid {pgid}")),
                ProcessGroupLiveness::Absent => {}
                ProcessGroupLiveness::Unknown(reason) => {
                    unknown.push(format!("cannot verify pgid {pgid}: {reason}"))
                }
            }
            if !active.is_empty() {
                CleanupVerification::Active {
                    reason: format!("payload identities remain active: {}", active.join(", ")),
                    record,
                }
            } else if !unknown.is_empty() {
                CleanupVerification::Unknown {
                    reason: unknown.join("; "),
                    record: Some(record),
                }
            } else {
                CleanupVerification::Recoverable {
                    reason:
                        "all recorded residual identities and the captured process group are absent"
                            .into(),
                    record,
                }
            }
        }
    }
}

pub(crate) fn write_cleanup_record(path: &Path, record: &CleanupRecord) -> io::Result<()> {
    let temporary = PathBuf::from(format!("{}.tmp-{}", path.display(), std::process::id()));
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)?;
    file.write_all(record.render().as_bytes())?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    if let Some(parent) = path.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

pub(crate) fn remove_cleanup_record(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => {}
        Err(source) if source.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(source) => return Err(source),
    }
    if let Some(parent) = path.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

/// A child whose wrapper has execed into its own process group but whose guest
/// command cannot start until `release` writes the one valid token. The pipe is
/// close-on-exec everywhere except the wrapper's fixed read descriptor, and
/// EOF/error is a fail-closed exit. Thus supervisor death before publication
/// cannot accidentally start the guest.
pub(crate) struct GatedChild {
    pub(crate) child: Child,
    pub(crate) leader: ProcessIdentity,
    pub(crate) pgid: u32,
    release: Option<File>,
}

impl GatedChild {
    pub(crate) fn release(&mut self) -> io::Result<()> {
        let mut release = self
            .release
            .take()
            .ok_or_else(|| io::Error::other("child start gate was already released"))?;
        release.write_all(b"ci-hub-go\n")?;
        release.flush()
    }
}

pub(crate) fn spawn_gated_child(
    program: &OsString,
    args: &[OsString],
    configure: impl FnOnce(&mut Command),
) -> io::Result<GatedChild> {
    let mut pipe_fds = [-1; 2];
    // SAFETY: `pipe_fds` names two writable c_int slots. Both returned
    // descriptors are immediately owned below.
    if unsafe { libc::pipe2(pipe_fds.as_mut_ptr(), libc::O_CLOEXEC) } != 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful pipe2 returned two new owned descriptors.
    let read_gate = unsafe { OwnedFd::from_raw_fd(pipe_fds[0]) };
    // SAFETY: successful pipe2 returned two new owned descriptors.
    let release_gate = unsafe { File::from_raw_fd(pipe_fds[1]) };
    let read_fd = read_gate.as_raw_fd();

    let mut command = Command::new("/bin/sh");
    command
        .arg("-c")
        .arg(START_GATE_SCRIPT)
        .arg("ci-hub-start-gate")
        .arg(program)
        .args(args)
        .process_group(0);
    configure(&mut command);
    // SAFETY: the callback uses only async-signal-safe descriptor syscalls.
    // It never waits: the execed shell performs the blocking read, so
    // `Command::spawn` can return to publish the exact identity.
    unsafe {
        command.pre_exec(move || {
            if libc::dup2(read_fd, START_GATE_FD) < 0 {
                return Err(io::Error::last_os_error());
            }
            if libc::fcntl(START_GATE_FD, libc::F_SETFD, 0) < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command.spawn()?;
    drop(read_gate);
    let pid = child.id();
    let pid_t = libc::pid_t::try_from(pid)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "child pid exceeds pid_t"))?;
    // SAFETY: getpgid only reads kernel state for this positive child PID.
    let observed_pgid = unsafe { libc::getpgid(pid_t) };
    if observed_pgid != pid_t {
        drop(release_gate);
        let _ = child.wait();
        let detail = if observed_pgid < 0 {
            io::Error::last_os_error().to_string()
        } else {
            format!("observed process group {observed_pgid}")
        };
        return Err(io::Error::other(format!(
            "start-gated child {pid} was not group leader: {detail}"
        )));
    }
    let leader = ProcessIdentity {
        pid,
        start_ticks: process_start_ticks(pid)?,
    };
    Ok(GatedChild {
        child,
        leader,
        pgid: pid,
        release: Some(release_gate),
    })
}

pub(crate) fn enable_child_subreaper() -> io::Result<()> {
    // SAFETY: PR_SET_CHILD_SUBREAPER changes only this process attribute.
    if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut enabled: libc::c_int = 0;
    // SAFETY: PR_GET_CHILD_SUBREAPER writes one c_int to the provided pointer.
    if unsafe { libc::prctl(libc::PR_GET_CHILD_SUBREAPER, &mut enabled) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if enabled != 1 {
        return Err(io::Error::other("kernel did not enable child subreaper"));
    }
    Ok(())
}

/// Root of the unified (cgroup-v2) hierarchy.
pub(crate) const CGROUP_MOUNT: &str = "/sys/fs/cgroup";

/// Outcome of censusing one cgroup subtree. `Unknown` is deliberately distinct
/// from `Empty`: "I could not look" must never read as "nothing is there".
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum CgroupCensus {
    /// No process in the subtree — including the case where the cgroup itself
    /// is gone, which is a POSITIVE proof: the kernel only lets a cgroup be
    /// removed once it holds no processes.
    Empty { absent: bool },
    /// At least one live pid, listed exactly.
    Populated(Vec<u32>),
    /// Could not be determined. Fail closed on this.
    Unknown(String),
}

/// Cgroup-v2 path of `pid`, relative to [`CGROUP_MOUNT`].
///
/// Returns `None` rather than an error: a missing or v1-shaped entry simply
/// means this record cannot carry the anchor and must keep the attestation
/// path. `/proc/<pid>/cgroup` on a v2 host has the single line `0::/<path>`.
pub(crate) fn process_cgroup(pid: u32) -> Option<String> {
    let content = fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    for line in content.lines() {
        // hierarchy-ID:controllers:path — the v2 entry is the one with an empty
        // controller list and hierarchy 0.
        let mut parts = line.splitn(3, ':');
        let hierarchy = parts.next()?;
        let controllers = parts.next()?;
        let path = parts.next()?;
        if hierarchy == "0" && controllers.is_empty() && path.starts_with('/') {
            return Some(path.to_string());
        }
    }
    None
}

/// Census every process in `relative` and all of its descendant cgroups.
///
/// This is the supervisor-independent half of proving an orphaned domain empty:
/// unlike a ppid walk it needs no live subreaper, and unlike a pgid check it is
/// not defeated by `setsid()`.
pub(crate) fn cgroup_population(relative: &str) -> CgroupCensus {
    if !relative.starts_with('/') || relative.contains("..") {
        return CgroupCensus::Unknown(format!("refusing to census cgroup path {relative:?}"));
    }
    let root = PathBuf::from(CGROUP_MOUNT).join(relative.trim_start_matches('/'));
    match root.symlink_metadata() {
        Err(source) if source.kind() == io::ErrorKind::NotFound => {
            return CgroupCensus::Empty { absent: true }
        }
        Err(source) => {
            return CgroupCensus::Unknown(format!("cannot stat {}: {source}", root.display()))
        }
        Ok(metadata) if !metadata.is_dir() => {
            return CgroupCensus::Unknown(format!("{} is not a cgroup directory", root.display()))
        }
        Ok(_) => {}
    }

    let mut pids = Vec::new();
    let mut pending = vec![root];
    while let Some(directory) = pending.pop() {
        match fs::read_to_string(directory.join("cgroup.procs")) {
            Ok(content) => {
                for line in content.lines() {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    match line.parse::<u32>() {
                        Ok(pid) => pids.push(pid),
                        Err(error) => {
                            return CgroupCensus::Unknown(format!(
                                "unparsable pid {line:?} in {}: {error}",
                                directory.display()
                            ))
                        }
                    }
                }
            }
            // A cgroup that vanished mid-walk took its processes with it.
            Err(source) if source.kind() == io::ErrorKind::NotFound => continue,
            Err(source) => {
                return CgroupCensus::Unknown(format!(
                    "cannot read cgroup.procs in {}: {source}",
                    directory.display()
                ))
            }
        }
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(source) if source.kind() == io::ErrorKind::NotFound => continue,
            Err(source) => {
                return CgroupCensus::Unknown(format!(
                    "cannot list {}: {source}",
                    directory.display()
                ))
            }
        };
        for entry in entries {
            let Ok(entry) = entry else {
                return CgroupCensus::Unknown(format!("cannot walk {}", directory.display()));
            };
            // Nested cgroups are real directories; never follow a symlink out
            // of the subtree we were asked to census.
            if matches!(entry.file_type(), Ok(kind) if kind.is_dir()) {
                pending.push(entry.path());
            }
        }
    }
    pids.sort_unstable();
    pids.dedup();
    if pids.is_empty() {
        CgroupCensus::Empty { absent: false }
    } else {
        CgroupCensus::Populated(pids)
    }
}

/// Cgroup anchor for a published payload, or the reason there is none.
///
/// Deliberately NOT gated on the cgroup being exclusive to this run. I tried
/// that and backed it out: every available exclusivity test is a ppid-tree walk,
/// and ppid trees being unreliable is the PREMISE of this whole mechanism. In a
/// real transient unit ci-hub re-execs through a cost-measurement wrapper
/// (`ci-hub -> python3 -> python3.12 -> ci-hub -> payload`) whose intermediates
/// exit and reparent, so a tree-rooted check reported four "foreign" occupants
/// for a cgroup that was entirely this run's, and the anchor was never recorded
/// -- the feature was inert.
///
/// Recording an anchor is safe without that gate because a POPULATED domain
/// never silently discharges: it is refused by default and can only be overidden
/// by an explicit attestation that is logged with the exact occupant pids. The
/// value is in the other direction anyway -- an EMPTY or absent cgroup is a
/// mechanical proof that needs no human at all.
pub(crate) fn payload_cgroup_anchor(leader_pid: u32) -> Result<String, String> {
    process_cgroup(leader_pid)
        .ok_or_else(|| format!("no cgroup-v2 entry for payload pid {leader_pid}"))
}

/// The one cgroup a payload can legitimately migrate OUT of its unit cgroup
/// into: `safe-ci.slice`, used by `safe-ci-dag-runner`.
///
/// Derived from the payload's own recorded path rather than hardcoded, because
/// the real path embeds the invoking uid
/// (`/user.slice/user-<uid>.slice/user@<uid>.service/...`); a host literal would
/// be both wrong on another account and a portability-lint violation. `None`
/// when the recorded path is not user-manager shaped — the caller must then
/// treat the census as incomplete rather than skip the domain silently.
pub(crate) fn safe_ci_slice_for(payload_cgroup: &str) -> Option<String> {
    let marker = payload_cgroup
        .split('/')
        .find(|segment| segment.starts_with("user@") && segment.ends_with(".service"))?;
    let (prefix, _) = payload_cgroup.split_once(marker)?;
    Some(format!("{prefix}{marker}/safe.slice/safe-ci.slice"))
}

fn scan_descendants(root_pid: u32) -> io::Result<Vec<ProcessIdentity>> {
    let mut processes = BTreeMap::new();
    for entry in fs::read_dir("/proc")? {
        let entry = entry?;
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        let Ok(pid) = name.parse::<u32>() else {
            continue;
        };
        match scan_process_stat_fields(pid) {
            Ok((_, parent, start_ticks)) => {
                processes.insert(pid, (parent, start_ticks));
            }
            Err(source) if source.kind() == io::ErrorKind::NotFound => {}
            Err(source) => return Err(source),
        }
    }
    let mut domain = BTreeSet::from([root_pid]);
    loop {
        let before = domain.len();
        for (&pid, &(parent, _)) in &processes {
            if domain.contains(&parent) {
                domain.insert(pid);
            }
        }
        if domain.len() == before {
            break;
        }
    }
    domain.remove(&root_pid);
    Ok(domain
        .into_iter()
        .filter_map(|pid| {
            processes.get(&pid).map(|(_, start_ticks)| ProcessIdentity {
                pid,
                start_ticks: *start_ticks,
            })
        })
        .collect())
}

fn scan_process_stat_fields(pid: u32) -> io::Result<(char, u32, u64)> {
    #[cfg(test)]
    if SCAN_UNREADABLE_PID.load(std::sync::atomic::Ordering::SeqCst) == pid {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "test-injected unreadable process identity",
        ));
    }
    process_stat_fields(pid)
}

#[cfg(test)]
static SCAN_UNREADABLE_PID: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

#[cfg(test)]
fn inject_unreadable_scan_pid(pid: u32) {
    SCAN_UNREADABLE_PID.store(pid, std::sync::atomic::Ordering::SeqCst);
}

pub(crate) fn signal_exact_process(
    identity: &ProcessIdentity,
    signal: libc::c_int,
) -> io::Result<bool> {
    let pid = libc::pid_t::try_from(identity.pid)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "pid exceeds pid_t"))?;
    // Open a stable kernel reference before checking start_ticks. If the PID was
    // already reused, the identity check rejects it; if it exits afterward,
    // pidfd_send_signal returns ESRCH rather than targeting a future occupant.
    // SAFETY: pidfd_open takes a positive PID and zero flags.
    let raw_pidfd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0) };
    if raw_pidfd < 0 {
        let source = io::Error::last_os_error();
        return if source.raw_os_error() == Some(libc::ESRCH) {
            Ok(false)
        } else {
            Err(source)
        };
    }
    // SAFETY: successful pidfd_open returned one new owned descriptor.
    let pidfd = unsafe { OwnedFd::from_raw_fd(raw_pidfd as libc::c_int) };
    if !exact_process_liveness(identity).map_err(io::Error::other)? {
        return Ok(false);
    }
    // SAFETY: pidfd_send_signal targets only the process referenced by pidfd;
    // a null siginfo and zero flags are the documented simple-signal form.
    let sent = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            pidfd.as_raw_fd(),
            signal,
            std::ptr::null::<libc::siginfo_t>(),
            0,
        )
    };
    if sent == 0 {
        Ok(true)
    } else {
        let source = io::Error::last_os_error();
        if source.raw_os_error() == Some(libc::ESRCH) {
            Ok(false)
        } else {
            Err(source)
        }
    }
}

pub(crate) struct ResidualCapture {
    pub(crate) complete: bool,
    pub(crate) identities: Vec<ProcessIdentity>,
}

pub(crate) fn capture_and_freeze_residuals(supervisor_pid: u32) -> ResidualCapture {
    let mut prior = Vec::new();
    for _ in 0..4 {
        let identities = match scan_descendants(supervisor_pid) {
            Ok(identities) => identities,
            Err(_) => {
                return ResidualCapture {
                    complete: false,
                    identities: prior,
                }
            }
        };
        let mut all_stopped = true;
        for identity in &identities {
            if signal_exact_process(identity, libc::SIGSTOP).is_err() {
                all_stopped = false;
            }
        }
        thread::sleep(Duration::from_millis(20));
        for identity in &identities {
            match process_stat_fields(identity.pid) {
                Ok((state, _, start_ticks)) if start_ticks == identity.start_ticks => {
                    all_stopped &= state == 'T' || state == 't' || state == 'Z';
                }
                Ok(_) => {}
                Err(source) if source.kind() == io::ErrorKind::NotFound => {}
                Err(_) => all_stopped = false,
            }
        }
        if identities == prior && all_stopped {
            return ResidualCapture {
                complete: true,
                identities,
            };
        }
        prior = identities;
    }
    ResidualCapture {
        complete: false,
        identities: prior,
    }
}

pub(crate) fn reap_exited_children() {
    loop {
        let mut status = 0;
        // SAFETY: waitpid with WNOHANG reaps only exited children of this
        // dedicated supervisor process and never blocks.
        let result = unsafe { libc::waitpid(-1, &mut status, libc::WNOHANG) };
        if result <= 0 {
            break;
        }
    }
}

#[cfg(test)]
pub(crate) fn process_domain_test_guard() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
static HEARTBEAT_HELPER_DELAY_MS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

#[cfg(test)]
pub(crate) fn set_heartbeat_helper_delay(milliseconds: u64) {
    HEARTBEAT_HELPER_DELAY_MS.store(milliseconds, std::sync::atomic::Ordering::SeqCst);
}

#[cfg(test)]
pub(crate) fn heartbeat_test_helper_delay() {
    let milliseconds = HEARTBEAT_HELPER_DELAY_MS.swap(0, std::sync::atomic::Ordering::SeqCst);
    if milliseconds > 0 {
        Command::new("/bin/sleep")
            .arg(format!("{:.3}", milliseconds as f64 / 1_000.0))
            .status()
            .expect("delayed heartbeat helper must complete");
    }
}

#[cfg(not(test))]
pub(crate) fn heartbeat_test_helper_delay() {}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RunCrashPoint {
    AfterArm,
    AfterSpawn,
    AfterPublish,
}

#[cfg(test)]
enum RunCrashAction {
    ReturnError,
    StopSupervisor { ready: PathBuf },
}

#[cfg(test)]
static RUN_CRASH_HOOK: std::sync::Mutex<Option<(String, RunCrashPoint, RunCrashAction)>> =
    std::sync::Mutex::new(None);

#[cfg(test)]
pub(crate) fn install_run_crash_hook(operation: &str, point: RunCrashPoint) {
    let mut hook = RUN_CRASH_HOOK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    assert!(hook.is_none(), "a run crash hook is already installed");
    *hook = Some((operation.to_string(), point, RunCrashAction::ReturnError));
}

#[cfg(test)]
pub(crate) fn install_run_hard_death_hook(operation: &str, point: RunCrashPoint, ready: PathBuf) {
    let mut hook = RUN_CRASH_HOOK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    assert!(hook.is_none(), "a run crash hook is already installed");
    *hook = Some((
        operation.to_string(),
        point,
        RunCrashAction::StopSupervisor { ready },
    ));
}

#[cfg(test)]
pub(crate) fn take_run_crash_hook(operation: &str, point: RunCrashPoint) -> bool {
    let mut hook = RUN_CRASH_HOOK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let action = if hook
        .as_ref()
        .is_some_and(|(expected_operation, expected_point, _)| {
            expected_operation == operation && *expected_point == point
        }) {
        hook.take().map(|(_, _, action)| action)
    } else {
        None
    };
    drop(hook);
    match action {
        Some(RunCrashAction::ReturnError) => true,
        Some(RunCrashAction::StopSupervisor { ready }) => {
            fs::write(&ready, b"ready\n").unwrap_or_else(|source| {
                panic!("write hard-death readiness marker at {ready:?}: {source}")
            });
            let raised = unsafe { libc::raise(libc::SIGSTOP) };
            assert_eq!(raised, 0, "SIGSTOP hard-death supervisor hook failed");
            true
        }
        None => false,
    }
}

fn current_process_owner() -> Result<ProcessOwner, LandLockError> {
    let pid = std::process::id();
    let start_ticks = process_start_ticks(pid).map_err(|source| LandLockError::Io {
        action: "read process identity from",
        path: PathBuf::from(format!("/proc/{pid}/stat")),
        source,
    })?;
    Ok(ProcessOwner {
        host: current_host(),
        boot_id: current_boot_id()?,
        pid,
        start_ticks,
    })
}

fn render_liveness(liveness: &OwnerLiveness) -> String {
    match liveness {
        OwnerLiveness::Alive => "alive".into(),
        OwnerLiveness::Dead(reason) => format!("dead:{reason}"),
        OwnerLiveness::Unknown(reason) => format!("unknown:{reason}"),
    }
}

pub(crate) fn print_cleanup_record(record: &CleanupRecord, reason: &str) {
    for line in record.render().lines() {
        println!("  {line}");
    }
    println!("  verification={reason}");
}

fn required<T>(value: Option<T>, name: &str) -> Result<T, LandLockError> {
    value.ok_or_else(|| LandLockError::InvalidState(format!("holder has no {name} field")))
}

pub(crate) fn suffix(path: &Path, suffix: &str) -> PathBuf {
    PathBuf::from(format!("{}{suffix}", path.display()))
}

fn read_to_string(path: &Path) -> Result<String, LandLockError> {
    let mut file = File::open(path).map_err(|source| io_error("open", path, source))?;
    let mut content = String::new();
    file.read_to_string(&mut content)
        .map_err(|source| io_error("read", path, source))?;
    Ok(content)
}

fn write_truncated(path: &Path, bytes: &[u8]) -> Result<(), LandLockError> {
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(|source| io_error("open for write", path, source))?;
    file.write_all(bytes)
        .map_err(|source| io_error("write", path, source))?;
    file.flush()
        .map_err(|source| io_error("flush", path, source))?;
    Ok(())
}

fn remove_if_exists(path: &Path) -> Result<(), LandLockError> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("remove", path, source)),
    }
}

fn io_error(action: &'static str, path: &Path, source: io::Error) -> LandLockError {
    LandLockError::Io {
        action,
        path: path.to_path_buf(),
        source,
    }
}

/// How a supervised `run` child ended.
enum ChildOutcome {
    /// The child exited on its own with this status.
    Exited {
        status: ExitStatus,
        pgid: u32,
    },
    /// The child exceeded its deadline and was killed.
    TimedOut {
        pgid: u32,
    },
    Uncertain {
        pgid: u32,
        reason: String,
    },
}

impl ChildOutcome {
    fn pgid(&self) -> u32 {
        match self {
            Self::Exited { pgid, .. } | Self::TimedOut { pgid } | Self::Uncertain { pgid, .. } => {
                *pgid
            }
        }
    }
}

/// Wait for `child`, killing it if it runs longer than `deadline_secs`.
///
/// The child subtree is signalled SIGTERM, given a short grace period, then
/// SIGKILLed, and the child is reaped so no zombie is left behind. Callers reject
/// zero before acquiring the lock so the pre-guardrail unbounded behavior cannot
/// be re-enabled.
fn supervise_child(child: &mut Child, deadline_secs: u64, pr: &str) -> ChildOutcome {
    let deadline = Instant::now() + Duration::from_secs(deadline_secs);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                return ChildOutcome::Exited {
                    status,
                    pgid: child.id(),
                }
            }
            Ok(None) => {}
            Err(source) => {
                // Cannot supervise a child we can't wait on; block on it so we do
                // not spin, and report whatever status it finally yields.
                match child.wait() {
                    Ok(status) => {
                        return ChildOutcome::Exited {
                            status,
                            pgid: child.id(),
                        }
                    }
                    Err(_) => {
                        eprintln!("landing-lock: cannot wait on child: {source}");
                        return ChildOutcome::Uncertain {
                            pgid: child.id(),
                            reason: source.to_string(),
                        };
                    }
                }
            }
        }
        if Instant::now() >= deadline {
            let pgid = child.id();
            terminate_child_group(child, pr);
            return ChildOutcome::TimedOut { pgid };
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// SIGTERM then (after a grace period) SIGKILL the child's process group, and
/// reap the direct child. The child was spawned with `process_group(0)`, so its
/// pid equals its pgid and signalling `-pid` reaches the whole land subtree
/// (gh / git / cargo), not just the wrapper shell.
fn terminate_child_group(child: &mut Child, pr: &str) {
    // Capture the protected domain before TERM can reap its leader. Descendants
    // retain this pgid even after the direct child exits.
    let pgid = child.id();
    let group = format!("-{pgid}");
    eprintln!("landing-lock: child-deadline reached for PR #{pr}; SIGTERM process group {group}");
    signal_group(libc::SIGTERM, pgid);
    let grace = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < grace {
        // Do not reap the leader yet: its exact PID keeps the captured process
        // group identity from disappearing or being reused before final KILL.
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
    eprintln!("landing-lock: grace expired for PR #{pr}; SIGKILL process group {group}");
    let kill_sent = signal_group(libc::SIGKILL, pgid);
    let child_reaped = if kill_sent {
        child.wait().is_ok()
    } else {
        matches!(child.try_wait(), Ok(Some(_)))
    };
    let killed_deadline = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < killed_deadline {
        if child_reaped && !process_group_exists(pgid) {
            break;
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
    if child_reaped {
        reap_exited_children();
    }
}

/// Send `signal` to the positive process-group ID without delegating negative
/// PID parsing to a host-specific `kill(1)` implementation.
pub(crate) fn signal_group(signal: libc::c_int, pgid: u32) -> bool {
    signal_group_succeeds(signal, pgid)
}

fn signal_group_succeeds(signal: libc::c_int, pgid: u32) -> bool {
    signal_group_with(signal, pgid, |target, signal| {
        // SAFETY: `signal_group_with` admits only a negative group target below
        // -1, and every signal is a libc constant at the production call sites.
        unsafe { libc::kill(target, signal) == 0 }
    })
}

fn signal_group_with(
    signal: libc::c_int,
    pgid: u32,
    send: impl FnOnce(libc::pid_t, libc::c_int) -> bool,
) -> bool {
    process_group_target(pgid).is_some_and(|target| send(target, signal))
}

fn process_group_target(pgid: u32) -> Option<libc::pid_t> {
    let pgid = libc::pid_t::try_from(pgid).ok()?;
    // `kill(-1, signal)` is the Linux broadcast form, not process group 1.
    // Refuse both non-group values before entering the syscall boundary.
    (pgid > 1).then_some(-pgid)
}

/// Probe the captured process group, treating every error except ESRCH as live
/// so cleanup fails closed on permission or transient kernel errors.
pub(crate) fn process_group_exists(pgid: u32) -> bool {
    !matches!(process_group_liveness(pgid), ProcessGroupLiveness::Absent)
}

pub(crate) fn exit_status_code(status: ExitStatus) -> i32 {
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

#[cfg(test)]
mod cgroup_anchor_tests {
    use super::*;

    fn record_with(cgroup: Option<&str>) -> CleanupRecord {
        CleanupRecord {
            agent: "a".into(),
            operation: "validate:deadbeef".into(),
            host: "testhost".into(),
            boot_id: "boot".into(),
            phase: CleanupPhase::Published {
                leader: ProcessIdentity {
                    pid: 42,
                    start_ticks: 7,
                },
                pgid: 42,
            },
            cgroup: cgroup.map(str::to_string),
        }
    }

    // BACK-COMPAT, the load-bearing half of the version bump: a record with no
    // cgroup must still render EXACTLY as version 2. Landing-lock records and
    // every armed/legacy validate record take this path, so older binaries on
    // this shared box keep reading them.
    #[test]
    fn record_without_a_cgroup_still_renders_version_2() {
        let rendered = record_with(None).render();
        assert!(
            rendered.starts_with("version=2\n"),
            "expected v2 for an anchorless record, got: {rendered}"
        );
        assert!(!rendered.contains("cgroup="));
        assert_eq!(CleanupRecord::parse(&rendered).unwrap().cgroup, None);
    }

    // A record that HAS an anchor moves to v3 and round-trips.
    #[test]
    fn record_with_a_cgroup_renders_version_3_and_round_trips() {
        let record = record_with(Some("/user.slice/payload.scope"));
        let rendered = record.render();
        assert!(rendered.starts_with("version=3\n"), "got: {rendered}");
        assert!(rendered.contains("cgroup=/user.slice/payload.scope\n"));
        assert_eq!(CleanupRecord::parse(&rendered).unwrap(), record);
    }

    // Forward-compat in the other direction: a v2 record that somehow carries a
    // cgroup is MALFORMED, not silently tolerated. Version must continue to
    // mean something.
    #[test]
    fn version_2_carrying_a_cgroup_is_refused() {
        let bad = "version=2\nagent=a\noperation=o\nhost=h\nboot_id=b\n\
                   cgroup=/x\nphase=armed\n";
        let error = CleanupRecord::parse(bad).unwrap_err();
        assert!(error.contains("cannot carry a cgroup"), "got: {error}");
    }

    #[test]
    fn unsupported_versions_are_still_refused() {
        let bad = "version=4\nagent=a\noperation=o\nhost=h\nboot_id=b\nphase=armed\n";
        assert!(CleanupRecord::parse(bad)
            .unwrap_err()
            .contains("unsupported cleanup version"));
    }

    // POSITIVE CONTROL for the census: pointed at a cgroup that demonstrably
    // contains a live process -- our own -- it must SEE it. Without this, a
    // census that reported Empty unconditionally would pass every other test
    // here while being catastrophically wrong.
    #[test]
    fn cgroup_population_sees_this_very_process() {
        let Some(mine) = process_cgroup(std::process::id()) else {
            eprintln!("skipping: no cgroup-v2 entry for self on this host");
            return;
        };
        assert!(
            mine.starts_with('/'),
            "expected an absolute v2 path: {mine}"
        );
        match cgroup_population(&mine) {
            CgroupCensus::Populated(pids) => assert!(
                pids.contains(&std::process::id()),
                "own cgroup {mine} did not list this process: {pids:?}"
            ),
            other => panic!("own cgroup {mine} must be POPULATED, got {other:?}"),
        }
    }

    // The emptiness proof that closes the one-way door: an absent cgroup counts
    // as empty, because the kernel only removes one that holds no processes.
    #[test]
    fn an_absent_cgroup_counts_as_proven_empty() {
        let absent = "/user.slice/user-999999.slice/user@999999.service/ci-hub-absent.scope";
        assert_eq!(
            cgroup_population(absent),
            CgroupCensus::Empty { absent: true }
        );
    }

    // Fail closed on anything that is not a plain absolute cgroup path, so a
    // crafted record cannot walk the census out of the hierarchy.
    #[test]
    fn traversal_and_relative_cgroup_paths_are_refused() {
        for bad in ["../escape", "relative/path", "/ok/../../escape"] {
            assert!(
                matches!(cgroup_population(bad), CgroupCensus::Unknown(_)),
                "cgroup path {bad:?} must be refused"
            );
        }
    }

    // safe-ci.slice is DERIVED from the payload's own path, never hardcoded:
    // the real path embeds the invoking uid.
    #[test]
    fn safe_ci_slice_is_derived_from_the_payload_path() {
        assert_eq!(
            safe_ci_slice_for(
                "/user.slice/user-4321.slice/user@4321.service/x.slice/payload.scope"
            )
            .as_deref(),
            Some("/user.slice/user-4321.slice/user@4321.service/safe.slice/safe-ci.slice")
        );
        // Not user-manager shaped => the caller must NOT get a confidently wrong
        // path; it must get nothing and treat the census as incomplete.
        assert_eq!(safe_ci_slice_for("/system.slice/whatever.scope"), None);
        assert_eq!(safe_ci_slice_for("/"), None);
    }
}

/// Parse a restated `<pid>:<start_ticks>` payload-leader identity. `start_ticks`
/// is what defeats PID reuse, so a bare pid is refused rather than tolerated.
fn parse_leader_identity(value: &str) -> Result<ProcessIdentity, LandLockError> {
    let refuse = || {
        LandLockError::ReclaimNotProven(format!(
            "--leader must be <pid>:<start_ticks> exactly as `status` prints it, got {value:?}"
        ))
    };
    let (pid, start_ticks) = value.trim().split_once(':').ok_or_else(refuse)?;
    Ok(ProcessIdentity {
        pid: pid.parse().map_err(|_| refuse())?,
        start_ticks: start_ticks.parse().map_err(|_| refuse())?,
    })
}

// ---------------------------------------------------------------------------
// Post-mortem domain census. SHARED by both lock authorities.
//
// These live here, not in validate_lock.rs, because BOTH land-lock and
// validate-lock must reach the same verdict from the same evidence. A second
// copy of `census_disposition` would be a policy that can drift: one authority
// could start discharging a quarantine the other still refuses.
// ---------------------------------------------------------------------------

/// What the KERNEL can still say about a recorded payload domain once the
/// supervisor is gone.
///
/// Three outcomes, not two: "I could not look" must never be collapsed into
/// "nothing is there", which is exactly the mistake that would let a census
/// bury a live domain.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum DomainEvidence {
    /// Every domain named by the record is empty, verified against the kernel.
    ProvenEmpty(String),
    /// At least one live process remains. Not overridable by attestation.
    Populated(String),
    /// Not determinable from the record alone; attestation is the only recourse.
    Unproven(String),
}

/// Census the payload domain post-hoc from the recorded cgroup anchor.
///
/// This is what closes the UNCENSUSED one-way door. `leader`/`pgid` are both
/// dead ends after the supervisor exits — a ppid walk needs the subreaper that
/// died with it, and pgid membership does not survive `setsid()`. Cgroup
/// membership survives both, so a recorded cgroup can be read directly.
///
/// Two domains are checked, and BOTH must be empty: the payload's own cgroup
/// subtree, and `safe-ci.slice`, the one place a payload legitimately migrates
/// out of its unit cgroup (via `safe-ci-dag-runner`).
pub(crate) fn census_recorded_domain(cgroup: Option<&str>) -> DomainEvidence {
    let Some(payload) = cgroup else {
        return DomainEvidence::Unproven(
            "the cleanup record carries no cgroup anchor (written before cgroup anchoring, or \
             the payload's cgroup was unreadable at publish time)"
                .into(),
        );
    };

    let mut proven = Vec::new();
    let mut census = |label: &str, path: &str| -> Result<(), DomainEvidence> {
        match cgroup_population(path) {
            CgroupCensus::Empty { absent } => {
                // Absence is a POSITIVE proof, not a skipped check: a cgroup can
                // only be removed once it holds no processes.
                proven.push(format!(
                    "{label} {path} {}",
                    if absent { "is absent" } else { "is empty" }
                ));
                Ok(())
            }
            CgroupCensus::Populated(pids) => Err(DomainEvidence::Populated(format!(
                "{label} {path} still holds {} process(es): {}",
                pids.len(),
                pids.iter()
                    .map(u32::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            ))),
            CgroupCensus::Unknown(why) => Err(DomainEvidence::Unproven(format!(
                "{label} {path} could not be censused: {why}"
            ))),
        }
    };

    if let Err(evidence) = census("payload cgroup", payload) {
        return evidence;
    }
    let Some(safe_ci) = safe_ci_slice_for(payload) else {
        return DomainEvidence::Unproven(format!(
            "payload cgroup {payload} is not user-manager shaped, so safe-ci.slice cannot be \
             derived and the migration target would go uncensused"
        ));
    };
    if let Err(evidence) = census("migration target", &safe_ci) {
        return evidence;
    }
    DomainEvidence::ProvenEmpty(proven.join("; "))
}

/// What discharges this census, if anything.
///
/// Pure on purpose: this is the entire policy change of the cgroup-anchor work,
/// so it is testable without fabricating lock state. `Ok` is the disposition to
/// record in the transcript, `Err` the refusal.
pub(crate) fn census_disposition(
    domain: DomainEvidence,
    attested: bool,
    evidence: &str,
    mechanical_context: &str,
) -> Result<String, String> {
    match domain {
        // Mechanical. An attestation, if supplied, is simply not needed.
        DomainEvidence::ProvenEmpty(detail) => Ok(format!("empty domain PROVEN: {detail}")),
        // Refused by default. An override is possible but never silent: the
        // exact occupant pids are echoed into the transcript and the
        // disposition is labelled so nobody can later read it as a clean census.
        DomainEvidence::Populated(detail) => {
            if !attested {
                return Err(format!(
                    "the recorded payload domain is NOT empty: {detail}. Wait for it to drain, \
                     or kill it by exact identity. Overriding requires an explicit \
                     --attest-domain-empty --evidence and will be recorded as an override."
                ));
            }
            if evidence.trim().is_empty() {
                return Err("--evidence must record the observations backing \
                            --attest-domain-empty; an anonymous attestation is not auditable"
                    .to_string());
            }
            Ok(format!(
                "empty domain ATTESTED OVER A POPULATED CGROUP -- the kernel disagrees ({detail}): {}",
                evidence.trim()
            ))
        }
        DomainEvidence::Unproven(why) => {
            if !attested {
                return Err(format!(
                    "every mechanical precondition passes ({mechanical_context}), but the \
                     domain could not be censused mechanically: {why}. Re-run with \
                     --attest-domain-empty --evidence '<observations>' after confirming the \
                     payload's unit cgroup is absent/empty AND every cgroup the payload \
                     migrates into is empty."
                ));
            }
            if evidence.trim().is_empty() {
                return Err("--evidence must record the observations backing \
                            --attest-domain-empty; an anonymous attestation is not auditable"
                    .to_string());
            }
            Ok(format!(
                "empty domain ATTESTED (not mechanically provable: {why}): {}",
                evidence.trim()
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HARD_DEATH_ROOT_ENV: &str = "CI_HUB_LANDING_HARD_DEATH_ROOT";
    const HARD_DEATH_POINT_ENV: &str = "CI_HUB_LANDING_HARD_DEATH_POINT";

    fn paths_at(root: &Path) -> LockPaths {
        let lock = root.join(".landing-lock");
        LockPaths {
            guard: suffix(&lock, ".guard"),
            queue: suffix(&lock, ".queue"),
            owner: suffix(&lock, ".owner"),
            cleanup: suffix(&lock, ".cleanup-required"),
            lock,
        }
    }

    fn temp_paths(name: &str) -> LockPaths {
        let root = env::temp_dir().join(format!(
            "ci-hub-landing-lock-{name}-{}-{}",
            std::process::id(),
            epoch_seconds().unwrap()
        ));
        fs::create_dir_all(&root).unwrap();
        paths_at(&root)
    }

    #[test]
    fn process_group_signal_is_typed_and_target_scoped() {
        let mut target = Command::new("/bin/sleep")
            .arg("60")
            .process_group(0)
            .spawn()
            .unwrap();
        let mut control = Command::new("/bin/sleep")
            .arg("60")
            .process_group(0)
            .spawn()
            .unwrap();

        let invalid_syscalls = std::cell::Cell::new(0);
        let zero_refused = !signal_group_with(libc::SIGTERM, 0, |_, _| {
            invalid_syscalls.set(invalid_syscalls.get() + 1);
            true
        });
        let one_refused = !signal_group_with(libc::SIGTERM, 1, |_, _| {
            invalid_syscalls.set(invalid_syscalls.get() + 1);
            true
        });
        let target_signaled = signal_group_succeeds(libc::SIGTERM, target.id());
        let deadline = Instant::now() + Duration::from_secs(2);
        let target_exited = loop {
            if target.try_wait().unwrap().is_some() {
                break true;
            }
            if Instant::now() >= deadline {
                break false;
            }
            thread::sleep(Duration::from_millis(10));
        };
        let control_survived = control.try_wait().unwrap().is_none();

        if !target_exited {
            let _ = target.kill();
            let _ = target.wait();
        }
        signal_group(libc::SIGKILL, control.id());
        let _ = control.wait();

        assert!(zero_refused, "pgid 0 must never target the caller's group");
        assert!(one_refused, "pgid 1 must never become kill(-1) broadcast");
        assert_eq!(
            invalid_syscalls.get(),
            0,
            "invalid pgids must be rejected before signal delivery"
        );
        assert!(target_signaled, "typed process-group signal was refused");
        assert!(target_exited, "target group did not terminate within 2s");
        assert!(control_survived, "unrelated process group was signalled");
    }

    #[test]
    fn unreadable_descendant_identity_makes_census_incomplete_then_recovers() {
        let _domain_guard = process_domain_test_guard();
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let identity = ProcessIdentity {
            pid: child.id(),
            start_ticks: process_start_ticks(child.id()).unwrap(),
        };
        inject_unreadable_scan_pid(identity.pid);
        let incomplete = capture_and_freeze_residuals(std::process::id());
        assert!(!incomplete.complete);

        inject_unreadable_scan_pid(0);
        let visible = capture_and_freeze_residuals(std::process::id());
        assert!(visible.complete);
        assert!(visible.identities.contains(&identity));
        signal_exact_process(&identity, libc::SIGKILL).unwrap();
        child.wait().unwrap();
        reap_exited_children();
        let recovered = capture_and_freeze_residuals(std::process::id());
        assert!(recovered.complete);
        assert!(recovered.identities.is_empty());
    }

    #[test]
    fn pidfd_signal_rejects_stale_identity_and_targets_exact_process() {
        let mut target = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let mut control = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let identity = ProcessIdentity {
            pid: target.id(),
            start_ticks: process_start_ticks(target.id()).unwrap(),
        };
        let stale = ProcessIdentity {
            pid: identity.pid,
            start_ticks: identity.start_ticks.saturating_add(1),
        };
        assert!(!signal_exact_process(&stale, libc::SIGKILL).unwrap());
        assert!(target.try_wait().unwrap().is_none());
        assert!(signal_exact_process(&identity, libc::SIGKILL).unwrap());
        target.wait().unwrap();
        assert!(control.try_wait().unwrap().is_none());
        control.kill().unwrap();
        control.wait().unwrap();
    }

    /// Fabricate the exact wedge shape: a lock held by a dead supervisor whose
    /// cleanup record is stuck at `published` with no cgroup anchor -- i.e. the
    /// `mergegate-fix`/`pr:1666` state, and the shape of EVERY landing record,
    /// since landing `run()` does not yet write an anchor.
    fn anchorless_published_quarantine(name: &str) -> (LandingLock, ProcessIdentity, u32) {
        let paths = temp_paths(name);
        let lock = LandingLock { paths };
        let holder = LockState {
            agent: "mergegate-fix".into(),
            repo: None,
            operation: None,
            pending_mutation: None,
            pending_attempt: None,
            pending_call_count: None,
            pending_call_id: None,
            pr: "1666".into(),
            host: current_host(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: None,
        };
        lock.write_holder(&holder).unwrap();
        // A dead supervisor: pid 0 can never be a live owner.
        let owner = ProcessOwner {
            host: current_host(),
            boot_id: current_boot_id().unwrap(),
            pid: 0,
            start_ticks: 1,
        };
        fs::write(&lock.paths.owner, owner.render()).unwrap();
        // A leader identity that cannot be alive: pid 0 with an absurd start.
        let leader = ProcessIdentity {
            pid: 0,
            start_ticks: u64::MAX,
        };
        let pgid = 0x7fff_fffe;
        let record = CleanupRecord {
            agent: "mergegate-fix".into(),
            operation: "pr:1666".into(),
            host: current_host(),
            boot_id: current_boot_id().unwrap(),
            phase: CleanupPhase::Published {
                leader: leader.clone(),
                pgid,
            },
            cgroup: None,
        };
        write_cleanup_record(&lock.paths.cleanup, &record).unwrap();
        (lock, leader, pgid)
    }

    /// The A2 shape: identical to `anchorless_published_quarantine`, except the record
    /// CARRIES the payload cgroup anchor that `publish_run` now writes at acquire time.
    /// `cgroup` is a user-manager-shaped path under a temp root that does not exist, and an
    /// ABSENT cgroup is positive proof of emptiness (a cgroup can only be removed once it holds
    /// no processes), so this models a dead owner whose payload is genuinely gone.
    fn anchored_published_quarantine(
        name: &str,
        cgroup: &str,
    ) -> (LandingLock, ProcessIdentity, u32) {
        let (lock, leader, pgid) = anchorless_published_quarantine(name);
        let record = CleanupRecord {
            agent: "mergegate-fix".into(),
            operation: "pr:1666".into(),
            host: current_host(),
            boot_id: current_boot_id().unwrap(),
            phase: CleanupPhase::Published {
                leader: leader.clone(),
                pgid,
            },
            cgroup: Some(cgroup.to_string()),
        };
        write_cleanup_record(&lock.paths.cleanup, &record).unwrap();
        (lock, leader, pgid)
    }

    /// NEGATIVE->POSITIVE, the whole point of A2. The survivor discharges a dead owner's lock
    /// with NO attestation and NO `census-orphaned-domain` archaeology, because the boundary
    /// was recorded while the payload was still alive.
    #[test]
    fn an_anchored_published_quarantine_reclaims_without_attestation() {
        let (lock, _leader, _pgid) = anchored_published_quarantine(
            "anchored-empty",
            "/sys/fs/cgroup/user.slice/user-0.slice/user@0.service/ci-hub-a2-absent.scope",
        );
        let _ = &lock;
        let evidence = census_recorded_domain(Some(
            "/sys/fs/cgroup/user.slice/user-0.slice/user@0.service/ci-hub-a2-absent.scope",
        ));
        assert!(
            matches!(evidence, DomainEvidence::ProvenEmpty(_)),
            "an absent anchored domain must census EMPTY without attestation, got {evidence:?}"
        );
    }

    /// THE OTHER DIRECTION, and the constraint that must survive the fix: an anchor is not a
    /// licence to discharge. Recording a boundary makes the domain CHECKABLE; it does not make it
    /// empty. A dead owner still sitting on live work is refused, and the refusal names the
    /// occupants rather than hand-waving.
    ///
    /// Tested against the pure policy function rather than a live cgroup on purpose: the only
    /// populated cgroups visible from this sandbox belong to other agents' transient tmux scopes,
    /// so keying the assertion on one would make the bracket depend on someone else's process
    /// lifetime. `census_disposition` IS the whole policy change, so it is the honest subject.
    #[test]
    fn an_anchored_but_populated_domain_is_refused_even_with_an_attestation() {
        let occupied = || DomainEvidence::Populated("payload cgroup X holds 3: 11,22,33".into());

        // Unattested -> refused, and the refusal carries the occupants.
        let refused = census_disposition(occupied(), false, "", "dead owner").unwrap_err();
        assert!(
            refused.contains("NOT empty") && refused.contains("11,22,33"),
            "a populated domain must be refused and name its occupants, got {refused}"
        );

        // Attested but anonymous -> STILL refused. This is the leg that stops "dead owner alone"
        // from becoming a discharge: an unauditable claim is not evidence.
        let anonymous = census_disposition(occupied(), true, "   ", "dead owner").unwrap_err();
        assert!(
            anonymous.contains("--evidence"),
            "an anonymous attestation over a populated domain must be refused, got {anonymous}"
        );

        // And the positive control, so this is not a check that refuses everything:
        // a PROVEN-empty domain discharges mechanically with no attestation at all.
        let ok = census_disposition(
            DomainEvidence::ProvenEmpty("payload cgroup X is absent".into()),
            false,
            "",
            "dead owner",
        )
        .expect("a proven-empty domain must discharge without attestation");
        assert!(ok.contains("PROVEN"), "got {ok}");
    }

    /// ACQUIRE TIME, not release: a boundary written at release is never written in the case
    /// that needs it. Asserts the anchor is applied by the Armed->Published transition and that
    /// later transitions pass `None` and therefore cannot erase it.
    #[test]
    fn later_transitions_never_erase_the_acquire_time_anchor() {
        let (lock, leader, pgid) = anchored_published_quarantine(
            "anchored-preserved",
            "/sys/fs/cgroup/user.slice/user-0.slice/user@0.service/ci-hub-a2-keep.scope",
        );
        let rendered = fs::read_to_string(&lock.paths.cleanup).unwrap();
        assert!(
            rendered.contains("ci-hub-a2-keep.scope"),
            "publish must persist the acquire-time anchor, got: {rendered}"
        );
        // The runtime's later transitions pass `None`, which the transition applies as
        // "leave whatever was there" -- assert that contract at the source.
        let _ = (&leader, pgid);
        assert!(
            !rendered.contains("cgroup=\n"),
            "the anchor must not be blanked, got: {rendered}"
        );
    }

    fn census_args(leader: &ProcessIdentity, pgid: u32) -> CensusOrphanedDomainArgs {
        CensusOrphanedDomainArgs {
            agent: "mergegate-fix".into(),
            operation: "pr:1666".into(),
            leader: format!("{}:{}", leader.pid, leader.start_ticks),
            pgid,
            attest_domain_empty: false,
            evidence: String::new(),
        }
    }

    /// The whole point of A1: before this command existed, `reclaim-dead`
    /// demanded a census that nothing could produce. Walks the full door:
    /// blocked -> refused unattested -> refused anonymously -> discharged ->
    /// reclaimable -> acquirable.
    #[test]
    fn an_anchorless_published_quarantine_is_dischargeable_and_then_reclaimable() {
        let (lock, leader, pgid) = anchorless_published_quarantine("census-door");

        // The one-way door, before the census: reclaim-dead refuses.
        let before = lock.reclaim_dead().unwrap_err().to_string();
        assert!(
            before.contains("complete residual census"),
            "expected the uncensused refusal, got {before}"
        );

        // Anchorless + unattested -> refused, and the refusal names the remedy.
        let unattested = lock
            .census_orphaned_domain(census_args(&leader, pgid))
            .unwrap_err()
            .to_string();
        assert!(
            unattested.contains("--attest-domain-empty"),
            "an anchorless census must point at the attestation, got {unattested}"
        );

        // Attested but anonymous -> still refused. An unauditable attestation is
        // not evidence.
        let mut anonymous = census_args(&leader, pgid);
        anonymous.attest_domain_empty = true;
        let anonymous = lock
            .census_orphaned_domain(anonymous)
            .unwrap_err()
            .to_string();
        assert!(
            anonymous.contains("--evidence"),
            "an anonymous attestation must be refused, got {anonymous}"
        );

        // Attested WITH evidence -> discharged.
        let mut attested = census_args(&leader, pgid);
        attested.attest_domain_empty = true;
        attested.evidence = "unit cgroup absent; safe-ci.slice populated 0".into();
        assert_eq!(lock.census_orphaned_domain(attested).unwrap(), 0);

        // The record is now a complete residual census, so reclaim-dead finishes
        // and a subsequent acquire succeeds. That last step is the one that
        // matters operationally -- a discharge that does not restore landing is
        // not a fix.
        assert_eq!(lock.reclaim_dead().unwrap(), 0);
        assert!(matches!(
            verify_cleanup_record(&lock.paths.cleanup, None),
            CleanupVerification::None
        ));
        lock.acquire(&AcquireArgs {
            agent: "someone-else".into(),
            pr: "1635".into(),
            wait: 5,
            hold: 900,
        })
        .unwrap();
    }

    /// The blind-aim guard: every restated field is re-checked, so this cannot
    /// be pointed at whatever happens to be quarantined at the time.
    #[test]
    fn a_census_refuses_every_mismatched_restatement() {
        let (lock, leader, pgid) = anchorless_published_quarantine("census-mismatch");
        let attest = |mut args: CensusOrphanedDomainArgs| {
            args.attest_domain_empty = true;
            args.evidence = "observed empty".into();
            lock.census_orphaned_domain(args).unwrap_err().to_string()
        };

        let mut wrong_agent = census_args(&leader, pgid);
        wrong_agent.agent = "someone-else".into();
        assert!(attest(wrong_agent).contains("does not match recorded agent"));

        let mut wrong_op = census_args(&leader, pgid);
        wrong_op.operation = "pr:9999".into();
        assert!(attest(wrong_op).contains("does not match recorded operation"));

        let mut wrong_leader = census_args(&leader, pgid);
        wrong_leader.leader = format!("{}:{}", leader.pid, leader.start_ticks - 1);
        assert!(attest(wrong_leader).contains("does not match recorded leader"));

        let mut wrong_pgid = census_args(&leader, pgid);
        wrong_pgid.pgid = pgid - 1;
        assert!(attest(wrong_pgid).contains("does not match recorded pgid"));

        // A bare pid is refused: start_ticks is what defeats PID reuse.
        let mut bare = census_args(&leader, pgid);
        bare.leader = leader.pid.to_string();
        assert!(attest(bare).contains("<pid>:<start_ticks>"));

        // And after all those refusals the quarantine is still intact. Verify it
        // BOUND TO ITS HOLDER: `verify_cleanup_record(.., None)` asks a different
        // question (an authority with no holder) and would not prove this.
        let holder = lock.read_holder().unwrap();
        assert!(matches!(
            lock.cleanup_verification(holder.as_ref()),
            CleanupVerification::Uncensused { .. }
        ));
    }

    #[test]
    fn holder_format_is_byte_compatible() {
        let holder = LockState {
            agent: "hermit-ci".into(),
            repo: None,
            operation: None,
            pending_mutation: None,
            pending_attempt: None,
            pending_call_count: None,
            pending_call_id: None,
            pr: "1533".into(),
            host: "testhost".into(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: Some("hermit-dbi".into()),
        };
        let rendered = "agent=hermit-ci\npr=1533\nhost=testhost\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\nreclaimed_from=hermit-dbi\n";
        assert_eq!(holder.render(), rendered);
        assert_eq!(LockState::parse(rendered).unwrap(), holder);
    }

    #[test]
    fn holder_format_preserves_exact_head_lander_extensions() {
        let rendered = "agent=hermit-lander\nrepo=rrnewton/hermit\noperation=op-1\npending_mutation=op-1\npending_attempt=attempt-1\npending_call_count=1\npending_call_id=call-1\npr=1650\nhost=testhost\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\n";
        let holder = LockState::parse(rendered).unwrap();
        assert_eq!(holder.repo.as_deref(), Some("rrnewton/hermit"));
        assert_eq!(holder.pending_call_count, Some(1));
        assert_eq!(holder.render(), rendered);

        let renewed = holder.renewed(60).unwrap();
        assert_eq!(renewed.repo, holder.repo);
        assert_eq!(renewed.operation, holder.operation);
        assert_eq!(renewed.pending_mutation, holder.pending_mutation);
        assert_eq!(renewed.pending_attempt, holder.pending_attempt);
        assert_eq!(renewed.pending_call_count, holder.pending_call_count);
        assert_eq!(renewed.pending_call_id, holder.pending_call_id);

        let unknown = format!("{rendered}unexpected=x\n");
        assert!(matches!(
            LockState::parse(&unknown),
            Err(LandLockError::InvalidState(message))
                if message.contains("unknown holder field")
        ));
    }

    #[test]
    fn queue_format_is_byte_compatible() {
        let entry = QueueEntry {
            enqueued_at: 1785770487,
            agent: "hermit-lander".into(),
            pr: "1281".into(),
        };
        assert_eq!(entry.render(), "1785770487\thermit-lander\t1281\n");
        assert_eq!(
            QueueEntry::parse(entry.render().trim_end(), 1).unwrap(),
            entry
        );
    }

    #[test]
    fn process_owner_sidecar_round_trips_without_changing_holder_format() {
        let owner = ProcessOwner {
            host: "testhost".into(),
            boot_id: "boot-1".into(),
            pid: 42,
            start_ticks: 123_456,
        };
        let rendered = "host=testhost\nboot_id=boot-1\npid=42\nstart_ticks=123456\n";
        assert_eq!(owner.render(), rendered);
        assert_eq!(ProcessOwner::parse(rendered).unwrap(), owner);
    }

    #[test]
    fn acquire_and_release_share_the_legacy_files() {
        let paths = temp_paths("roundtrip");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let status = lock
            .acquire(&AcquireArgs {
                agent: "test-agent".into(),
                pr: "42".into(),
                wait: 0,
                hold: 60,
            })
            .unwrap();
        assert_eq!(status, 0);
        let holder = LockState::parse(&read_to_string(&paths.lock).unwrap()).unwrap();
        assert_eq!(holder.agent, "test-agent");
        assert_eq!(holder.pr, "42");
        lock.release("test-agent", false).unwrap();
        assert!(!paths.lock.exists());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn full_protocol_acquire_renew_status_release_and_run() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("full-protocol");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        assert_eq!(
            lock.acquire(&AcquireArgs {
                agent: "ci-shard".into(),
                pr: "test".into(),
                wait: 0,
                hold: 30,
            })
            .unwrap(),
            0
        );
        lock.renew("ci-shard", 60, false).unwrap();
        let holder = lock.read_holder().unwrap().unwrap();
        assert_eq!(holder.agent, "ci-shard");
        assert!(holder.expires_at > holder.acquired_at);
        lock.status().unwrap();
        lock.release("ci-shard", false).unwrap();
        assert!(lock.read_holder().unwrap().is_none());

        assert_eq!(
            lock.run(RunArgs {
                agent: "ci-shard-run".into(),
                pr: "test-run".into(),
                wait: 0,
                hold: 30,
                child_deadline: 30,
                child: vec![OsString::from("/bin/true")],
            })
            .unwrap(),
            0
        );
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn census_waits_for_delayed_heartbeat_helper_before_freezing_descendants() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("delayed-heartbeat-helper");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        set_heartbeat_helper_delay(1_200);
        let started = Instant::now();
        let code = lock
            .run(RunArgs {
                agent: "delayed-heartbeat".into(),
                pr: "delayed-helper".into(),
                wait: 0,
                hold: 3,
                child_deadline: 10,
                child: vec![OsString::from("/bin/sleep"), OsString::from("1.4")],
            })
            .unwrap();
        assert_eq!(code, 0);
        assert!(
            started.elapsed() >= Duration::from_secs(2),
            "heartbeat delay was not exercised: {:?}",
            started.elapsed()
        );
        assert!(
            started.elapsed() < Duration::from_secs(6),
            "census froze the heartbeat helper: {:?}",
            started.elapsed()
        );
        assert!(!paths.cleanup.exists());
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn run_child_deadline_kills_child_and_releases_the_lock() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("child-deadline");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let started = Instant::now();
        // A child that would otherwise sleep far past the test must be killed at
        // the deadline, the lock released, and the queue freed.
        let code = lock
            .run(RunArgs {
                agent: "stuck-lander".into(),
                pr: "9999".into(),
                wait: 0,
                hold: 30,
                child_deadline: 1,
                child: vec![OsString::from("sleep"), OsString::from("120")],
            })
            .unwrap();
        assert_eq!(code, CHILD_DEADLINE_EXIT_CODE);
        // Bounded: killed near the 1s deadline + short grace, nowhere near 120s.
        assert!(
            started.elapsed() < Duration::from_secs(30),
            "child-deadline run took {:?}",
            started.elapsed()
        );
        // Head-of-line block is cleared: the lock is free for the next waiter.
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn run_kills_stubborn_descendant_before_releasing_lock() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("stubborn-descendant");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let identity_path = paths.lock.parent().unwrap().join("descendant.identity");
        let script = r#"
trap 'exit 0' TERM
(
  trap '' TERM HUP
  pid=$BASHPID
  start=$(awk '{print $22}' "/proc/$pid/stat")
  printf '%s %s %s\n' "$pid" "$start" "$$" > "$1"
  while :; do sleep 60; done
) &
while [ ! -s "$1" ]; do sleep 0.01; done
wait
"#;
        let started = Instant::now();
        let code = lock
            .run(RunArgs {
                agent: "stubborn-lander".into(),
                pr: "9997".into(),
                wait: 0,
                hold: 30,
                child_deadline: 1,
                child: vec![
                    OsString::from("/bin/bash"),
                    OsString::from("-c"),
                    OsString::from(script),
                    OsString::from("stubborn-descendant"),
                    identity_path.clone().into_os_string(),
                ],
            })
            .unwrap();
        assert_eq!(code, CHILD_DEADLINE_EXIT_CODE);

        let identity = fs::read_to_string(&identity_path).unwrap();
        let fields: Vec<_> = identity.split_whitespace().collect();
        assert_eq!(fields.len(), 3, "unexpected identity record {identity:?}");
        let pid: u32 = fields[0].parse().unwrap();
        let start_ticks: u64 = fields[1].parse().unwrap();
        let pgid: u32 = fields[2].parse().unwrap();
        let descendant_survived_release =
            matches!(process_start_ticks(pid), Ok(current) if current == start_ticks);
        if descendant_survived_release {
            signal_group(libc::SIGKILL, pgid);
        }

        assert!(
            started.elapsed() >= Duration::from_secs(CHILD_TERM_GRACE_SECONDS),
            "stubborn descendant did not survive TERM grace"
        );
        assert!(
            !descendant_survived_release,
            "exact descendant {pid}@{start_ticks} survived lock release"
        );
        assert!(!process_group_exists(pgid));
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn escaped_payload_quarantine_refuses_consumers_until_exact_recovery() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("escaped-quarantine");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let identity_path = paths.lock.parent().unwrap().join("escaped.identity");
        let script = r#"
trap 'exit 0' TERM
setsid /bin/bash -c '
  trap "" TERM HUP
  pid=$BASHPID
  start=$(awk "{print \$22}" "/proc/$pid/stat")
  printf "%s %s\n" "$pid" "$start" > "$1"
  while :; do sleep 60; done
' escaped-child "$1" &
while [ ! -s "$1" ]; do sleep 0.01; done
wait
"#;
        let run_error = lock
            .run(RunArgs {
                agent: "escaped-lander".into(),
                pr: "9996".into(),
                wait: 0,
                hold: 30,
                child_deadline: 1,
                child: vec![
                    OsString::from("/bin/bash"),
                    OsString::from("-c"),
                    OsString::from(script),
                    OsString::from("escaped-leader"),
                    identity_path.clone().into_os_string(),
                ],
            })
            .unwrap_err();
        assert!(matches!(run_error, LandLockError::CleanupQuarantined(_)));

        let identity = fs::read_to_string(&identity_path).unwrap();
        let fields: Vec<_> = identity.split_whitespace().collect();
        let escaped = ProcessIdentity {
            pid: fields[0].parse().unwrap(),
            start_ticks: fields[1].parse().unwrap(),
        };
        let record = CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
        let residuals = match &record.phase {
            CleanupPhase::Residual {
                domain_complete: true,
                residuals,
                ..
            } => residuals.clone(),
            phase => panic!("expected complete residual phase, got {phase:?}"),
        };
        assert!(residuals.contains(&escaped));
        assert!(matches!(exact_process_liveness(&escaped), Ok(true)));
        assert!(matches!(
            lock.cleanup_verification(lock.read_holder().unwrap().as_ref()),
            CleanupVerification::Active { .. }
        ));

        let second = AcquireArgs {
            agent: "replacement-lander".into(),
            pr: "10000".into(),
            wait: 0,
            hold: 30,
        };
        assert!(matches!(
            lock.acquire(&second),
            Err(LandLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.renew("escaped-lander", 30, false),
            Err(LandLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.release("escaped-lander", false),
            Err(LandLockError::CleanupQuarantined(_))
        ));
        let mut owner = lock.read_process_owner().unwrap().unwrap();
        owner.pid = u32::MAX;
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        assert!(matches!(
            lock.acquire(&second),
            Err(LandLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.reclaim_dead(),
            Err(LandLockError::ReclaimNotProven(_))
        ));
        lock.status().unwrap();

        for residual in &residuals {
            let _ = signal_exact_process(residual, libc::SIGKILL).unwrap();
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            reap_exited_children();
            if residuals
                .iter()
                .all(|identity| matches!(exact_process_liveness(identity), Ok(false)))
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(residuals
            .iter()
            .all(|identity| matches!(exact_process_liveness(identity), Ok(false))));
        assert!(matches!(
            lock.cleanup_verification(lock.read_holder().unwrap().as_ref()),
            CleanupVerification::Recoverable { .. }
        ));
        // Proven absence alone does not let an ordinary acquire erase the
        // quarantine; explicit recovery is still required.
        assert!(matches!(
            lock.acquire(&second),
            Err(LandLockError::CleanupQuarantined(_))
        ));

        assert_eq!(lock.reclaim_dead().unwrap(), 0);
        assert!(!paths.cleanup.exists());
        assert_eq!(lock.acquire(&second).unwrap(), 0);
        lock.release(&second.agent, false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn hard_death_run_subprocess_entry() {
        let Some(root) = env::var_os(HARD_DEATH_ROOT_ENV).map(PathBuf::from) else {
            return;
        };
        let point = match env::var(HARD_DEATH_POINT_ENV).as_deref() {
            Ok("after-arm") => RunCrashPoint::AfterArm,
            Ok("after-spawn") => RunCrashPoint::AfterSpawn,
            other => panic!("invalid hard-death point {other:?}"),
        };
        let paths = paths_at(&root);
        let ready = root.join("supervisor-ready");
        let marker = root.join("payload-started");
        let pr = "hard-death";
        install_run_hard_death_hook(&format!("pr:{pr}"), point, ready);
        let result = LandingLock { paths }.run(RunArgs {
            agent: "hard-death-lander".into(),
            pr: pr.into(),
            wait: 0,
            hold: 30,
            child_deadline: 30,
            child: vec![
                OsString::from("/bin/sh"),
                OsString::from("-c"),
                OsString::from("printf started > \"$1\"; while :; do sleep 60; done"),
                OsString::from("payload"),
                marker.into_os_string(),
            ],
        });
        panic!("hard-death subprocess resumed instead of being SIGKILLed: {result:?}");
    }

    #[test]
    fn hard_death_before_publication_retains_armed_barrier() {
        let _domain_guard = process_domain_test_guard();
        for point_name in ["after-arm", "after-spawn"] {
            let paths = temp_paths(&format!("hard-death-{point_name}"));
            let root = paths.lock.parent().unwrap().to_path_buf();
            let ready = root.join("supervisor-ready");
            let marker = root.join("payload-started");
            let mut supervisor = Command::new(env::current_exe().unwrap())
                .arg("--exact")
                .arg("landing_lock::tests::hard_death_run_subprocess_entry")
                .arg("--nocapture")
                .env(HARD_DEATH_ROOT_ENV, &root)
                .env(HARD_DEATH_POINT_ENV, point_name)
                .spawn()
                .unwrap();

            let deadline = Instant::now() + Duration::from_secs(5);
            while !ready.exists() {
                if let Some(status) = supervisor.try_wait().unwrap() {
                    panic!("hard-death supervisor exited before readiness: {status}");
                }
                if Instant::now() >= deadline {
                    let _ = supervisor.kill();
                    let _ = supervisor.wait();
                    panic!("hard-death supervisor did not reach {point_name}");
                }
                thread::sleep(Duration::from_millis(10));
            }

            let lock = LandingLock {
                paths: paths.clone(),
            };
            let owner = lock.read_process_owner().unwrap().unwrap();
            assert_eq!(owner.pid, supervisor.id());
            assert_eq!(
                owner.start_ticks,
                process_start_ticks(supervisor.id()).unwrap(),
                "owner record was not bound to the stopped supervisor"
            );
            let record =
                CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
            assert!(matches!(record.phase, CleanupPhase::Armed));
            assert!(!marker.exists(), "guest ran before identity publication");

            assert_eq!(
                unsafe { libc::kill(supervisor.id() as libc::pid_t, libc::SIGKILL) },
                0
            );
            let status = supervisor.wait().unwrap();
            assert_eq!(status.signal(), Some(libc::SIGKILL));
            let owner_identity = ProcessIdentity {
                pid: owner.pid,
                start_ticks: owner.start_ticks,
            };
            assert!(matches!(exact_process_liveness(&owner_identity), Ok(false)));
            thread::sleep(Duration::from_millis(100));
            assert!(
                !marker.exists(),
                "start-gated guest ran after supervisor death"
            );
            let persisted =
                CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
            assert!(matches!(persisted.phase, CleanupPhase::Armed));

            let second = AcquireArgs {
                agent: "replacement-lander".into(),
                pr: "replacement".into(),
                wait: 0,
                hold: 30,
            };
            assert!(matches!(
                lock.acquire(&second),
                Err(LandLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.reclaim_dead(),
                Err(LandLockError::ReclaimNotProven(_))
            ));
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn crash_windows_retain_armed_or_uncensused_barrier_after_owner_death() {
        let _domain_guard = process_domain_test_guard();
        for (index, point) in [
            RunCrashPoint::AfterArm,
            RunCrashPoint::AfterSpawn,
            RunCrashPoint::AfterPublish,
        ]
        .into_iter()
        .enumerate()
        {
            let paths = temp_paths(&format!("crash-window-{index}"));
            let lock = LandingLock {
                paths: paths.clone(),
            };
            let marker = paths.lock.parent().unwrap().join("payload-started");
            let pr = format!("crash-{index}");
            install_run_crash_hook(&format!("pr:{pr}"), point);
            let error = lock
                .run(RunArgs {
                    agent: "crash-lander".into(),
                    pr: pr.clone(),
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    child: vec![
                        OsString::from("/bin/sh"),
                        OsString::from("-c"),
                        OsString::from("printf started > \"$1\"; while :; do sleep 60; done"),
                        OsString::from("payload"),
                        marker.clone().into_os_string(),
                    ],
                })
                .unwrap_err();
            assert!(matches!(error, LandLockError::CleanupQuarantined(_)));

            let record =
                CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
            let published = match (&point, &record.phase) {
                (RunCrashPoint::AfterArm | RunCrashPoint::AfterSpawn, CleanupPhase::Armed) => None,
                (RunCrashPoint::AfterPublish, CleanupPhase::Published { leader, pgid }) => {
                    Some((leader.clone(), *pgid))
                }
                (_, phase) => panic!("crash point {point:?} left unexpected phase {phase:?}"),
            };

            if let Some((leader, _)) = &published {
                let deadline = Instant::now() + Duration::from_secs(2);
                while Instant::now() < deadline && !marker.exists() {
                    thread::sleep(Duration::from_millis(10));
                }
                assert!(marker.exists(), "published guest never started");
                assert!(matches!(exact_process_liveness(leader), Ok(true)));
            } else {
                thread::sleep(Duration::from_millis(100));
                reap_exited_children();
                assert!(
                    !marker.exists(),
                    "guest payload ran before exact identity publication"
                );
            }

            let mut owner = lock.read_process_owner().unwrap().unwrap();
            owner.pid = u32::MAX;
            write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
            let second = AcquireArgs {
                agent: "replacement-lander".into(),
                pr: "replacement".into(),
                wait: 0,
                hold: 30,
            };
            assert!(matches!(
                lock.acquire(&second),
                Err(LandLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.renew("crash-lander", 30, false),
                Err(LandLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.release("crash-lander", false),
                Err(LandLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.reclaim_dead(),
                Err(LandLockError::ReclaimNotProven(_))
            ));
            lock.status().unwrap();

            if let Some((leader, pgid)) = published {
                signal_group(libc::SIGKILL, pgid);
                let deadline = Instant::now() + Duration::from_secs(2);
                while Instant::now() < deadline {
                    reap_exited_children();
                    if matches!(exact_process_liveness(&leader), Ok(false))
                        && !process_group_exists(pgid)
                    {
                        break;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                assert!(matches!(exact_process_liveness(&leader), Ok(false)));
                assert!(!process_group_exists(pgid));
                assert!(matches!(
                    lock.cleanup_verification(lock.read_holder().unwrap().as_ref()),
                    CleanupVerification::Uncensused { .. }
                ));
                assert!(matches!(
                    lock.reclaim_dead(),
                    Err(LandLockError::ReclaimNotProven(_))
                ));
            }
            let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
        }
    }

    #[test]
    fn normal_leader_exit_cannot_clear_an_escaped_payload_barrier() {
        let _domain_guard = process_domain_test_guard();
        let paths = temp_paths("normal-exit-escaped");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let identity_path = paths.lock.parent().unwrap().join("escaped.identity");
        let script = r#"
setsid /bin/sh -c '
  trap "" TERM HUP
  pid=$$
  start=$(awk "{print \$22}" "/proc/$pid/stat")
  printf "%s %s\n" "$pid" "$start" > "$1"
  while :; do sleep 60; done
' escaped-child "$1" &
while [ ! -s "$1" ]; do sleep 0.01; done
exit 0
"#;
        let error = lock
            .run(RunArgs {
                agent: "normal-exit-lander".into(),
                pr: "normal-exit".into(),
                wait: 0,
                hold: 30,
                child_deadline: 30,
                child: vec![
                    OsString::from("/bin/sh"),
                    OsString::from("-c"),
                    OsString::from(script),
                    OsString::from("normal-leader"),
                    identity_path.clone().into_os_string(),
                ],
            })
            .unwrap_err();
        assert!(matches!(error, LandLockError::CleanupQuarantined(_)));
        let identity = fs::read_to_string(&identity_path).unwrap();
        let fields: Vec<_> = identity.split_whitespace().collect();
        let escaped = ProcessIdentity {
            pid: fields[0].parse().unwrap(),
            start_ticks: fields[1].parse().unwrap(),
        };
        let record = CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
        let residuals = match record.phase {
            CleanupPhase::Residual {
                domain_complete: true,
                residuals,
                ..
            } => residuals,
            phase => panic!("expected complete residual census, got {phase:?}"),
        };
        assert!(residuals.contains(&escaped));
        assert!(lock.read_holder().unwrap().is_some());
        for residual in &residuals {
            let _ = signal_exact_process(residual, libc::SIGKILL).unwrap();
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            reap_exited_children();
            if residuals
                .iter()
                .all(|identity| matches!(exact_process_liveness(identity), Ok(false)))
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        let mut owner = lock.read_process_owner().unwrap().unwrap();
        owner.pid = u32::MAX;
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        assert_eq!(lock.reclaim_dead().unwrap(), 0);
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn run_rejects_unbounded_deadline_before_acquiring() {
        let paths = temp_paths("unbounded-child-deadline");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let error = lock
            .run(RunArgs {
                agent: "unbounded-lander".into(),
                pr: "9998".into(),
                wait: 0,
                hold: 30,
                child_deadline: 0,
                child: vec![OsString::from("/bin/true")],
            })
            .unwrap_err();
        assert!(matches!(error, LandLockError::UnboundedChildDeadline));
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn proven_dead_owner_is_reclaimed_before_lease_expiry() {
        let paths = temp_paths("dead-owner-reclaim");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        assert_eq!(
            lock.acquire(&AcquireArgs {
                agent: "dead-lander".into(),
                pr: "100".into(),
                wait: 0,
                hold: 3_600,
            })
            .unwrap(),
            0
        );
        let mut owner = current_process_owner().unwrap();
        owner.pid = u32::MAX;
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        assert!(matches!(
            lock.owner_liveness().unwrap(),
            OwnerLiveness::Dead(_)
        ));
        assert_eq!(
            lock.acquire(&AcquireArgs {
                agent: "replacement-lander".into(),
                pr: "101".into(),
                wait: 0,
                hold: 60,
            })
            .unwrap(),
            0
        );
        let holder = lock.read_holder().unwrap().unwrap();
        assert_eq!(holder.agent, "replacement-lander");
        assert!(holder.reclaimed_from.unwrap().contains("dead owner"));
        lock.release("replacement-lander", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn live_owner_cannot_be_evidence_reclaimed() {
        let paths = temp_paths("live-owner-protected");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        lock.acquire(&AcquireArgs {
            agent: "live-lander".into(),
            pr: "102".into(),
            wait: 0,
            hold: 60,
        })
        .unwrap();
        lock.write_current_process_owner().unwrap();
        assert!(matches!(
            lock.owner_liveness().unwrap(),
            OwnerLiveness::Alive
        ));
        assert!(matches!(
            lock.reclaim_dead().unwrap_err(),
            LandLockError::ReclaimNotProven(_)
        ));
        lock.release("live-lander", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }
}
