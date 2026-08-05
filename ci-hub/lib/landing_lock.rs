//! Typed implementation of the shared-file landing mutex.

use chrono::{Local, TimeZone};
use clap::{Args, Subcommand};
use fs2::FileExt;
use std::collections::BTreeSet;
use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use thiserror::Error;

const DEFAULT_WAIT_SECONDS: u64 = 1_800;
const DEFAULT_HOLD_SECONDS: u64 = 900;
const POLL_SECONDS: u64 = 3;
const GUARD_WAIT_SECONDS: u64 = 30;
/// Hard ceiling on how long a `run` child may execute before it is killed and the
/// lock is released. A stuck land holding the lock is a head-of-line block for
/// every other FIFO waiter (the ~2040-minute starvation this bounds against): an
/// unbounded wait is unboxed compute. A real land is minutes; this measured
/// ceiling is far below any starvation.
// Measured 2026-08-04: 11 successful demo-gate runs had p99/max 864s. The
// lander gate bound is 1080s (max + 25%); the whole-child ceiling allows two
// complete gate windows while still guaranteeing release on a wedged child.
const DEFAULT_CHILD_DEADLINE_SECONDS: u64 = 2_160;
/// Exit code reported when a `run` child is killed for exceeding its deadline.
const CHILD_DEADLINE_EXIT_CODE: i32 = 124;
/// Exit code reported when the lease heartbeat fails and the child domain is
/// terminated before it can outlive the serialized authority.
const HEARTBEAT_FAILURE_EXIT_CODE: i32 = 125;
/// Grace period between SIGTERM and SIGKILL when terminating a timed-out child.
const CHILD_TERM_GRACE_SECONDS: u64 = 5;
/// Interval at which `run` polls a live child for completion or deadline breach.
const CHILD_POLL_MILLIS: u64 = 500;
/// Maximum time to observe the exec wrapper self-stop before user code starts.
const CHILD_STARTUP_SECONDS: u64 = 30;
/// Maximum time to acknowledge the watchdog's clean shutdown. The watchdog
/// normally exits immediately after its supervisor writes `done`; a bounded
/// wait keeps a broken watchdog from becoming a new unboxed process.
const WATCHDOG_SHUTDOWN_SECONDS: u64 = 5;
/// Extra observation room around a phase whose nominal work is bounded by a
/// separate timeout. The exact-parent watchdog is a last-resort flock breaker,
/// not the primary timeout reporter.
const SUPERVISOR_PHASE_MARGIN_SECONDS: u64 = 2;
/// Maximum launcher layers accepted on either side of the selected Python
/// landing child. Normal measured topology is two edges or fewer.
const MAX_ASSERT_CHILD_ANCESTRY_DEPTH: usize = 8;

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
    /// Prove this process is the supervised child of the exact held lease.
    AssertChild(AssertChildArgs),
    /// Persist a barrier before the supervised child starts an external mutation.
    ArmMutation(MutationArgs),
    /// Advance the exact external-call high-water before invoking GitHub.
    BindMutationCall(MutationCallArgs),
    /// Clear a barrier after proving the external mutation cannot still occur.
    ClearMutation(MutationArgs),
    /// Acquire, run one command with a heartbeat, then release.
    Run(RunArgs),
    /// Internal exact process-domain deadline watchdog.
    #[command(hide = true)]
    Watchdog(WatchdogArgs),
    /// Internal process-group anchor and arbitrary-command launcher.
    #[command(hide = true)]
    Payload(PayloadArgs),
    /// Internal exact-parent deadline watchdog for the canonical run path.
    #[command(hide = true)]
    SupervisorWatchdog(SupervisorWatchdogArgs),
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
pub struct AssertChildArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub repo: String,
    #[arg(long)]
    pub pr: String,
    /// Exact operation identity (the authorized head for safe landings).
    #[arg(long)]
    pub operation: Option<String>,
    /// PID of the Python landing child. This is only a selector: the canonical
    /// verifier dereferences kernel ancestry to bind it to both the recorded
    /// supervisor and this verifier process.
    #[arg(long)]
    pub child_pid: u32,
}

#[derive(Args, Clone, Debug)]
pub struct MutationArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub repo: String,
    #[arg(long)]
    pub pr: String,
    #[arg(long)]
    pub operation: String,
    /// Durable landing-attempt identity bound to this exact mutation.
    #[arg(long)]
    pub attempt_id: String,
    #[arg(long)]
    pub child_pid: u32,
}

#[derive(Args, Clone, Debug)]
pub struct MutationCallArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub repo: String,
    #[arg(long)]
    pub pr: String,
    #[arg(long)]
    pub operation: String,
    #[arg(long)]
    pub attempt_id: String,
    #[arg(long)]
    pub call_id: String,
    #[arg(long)]
    pub call_count: u64,
    #[arg(long)]
    pub child_pid: u32,
}

#[derive(Args, Clone, Debug)]
pub struct RunArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long)]
    pub pr: String,
    /// Repository identity carried by supervised landing children. Legacy and
    /// diagnostic lock users may omit it, but such leases cannot authorize an
    /// exact-repository child assertion.
    #[arg(long)]
    pub repo: Option<String>,
    /// Exact operation identity used to recover a retained external mutation.
    #[arg(long)]
    pub operation: Option<String>,
    #[arg(long, default_value_t = DEFAULT_WAIT_SECONDS)]
    pub wait: u64,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
    /// Kill and empty the child domain if it runs longer than this many
    /// seconds. The lock then releases only if no external mutation barrier is
    /// armed. Must be positive: unbounded lock holders are forbidden.
    #[arg(long, default_value_t = DEFAULT_CHILD_DEADLINE_SECONDS)]
    pub child_deadline: u64,
    #[arg(last = true, required = true)]
    pub child: Vec<OsString>,
}

#[derive(Args, Clone, Debug)]
pub struct WatchdogArgs {
    #[arg(long)]
    pub pgid: u32,
    #[arg(long)]
    pub leader_pid: u32,
    #[arg(long)]
    pub leader_start_ticks: u64,
    #[arg(long)]
    pub deadline_at: i64,
}

#[derive(Args, Clone, Debug)]
pub struct PayloadArgs {
    #[arg(last = true, required = true)]
    pub child: Vec<OsString>,
}

#[derive(Args, Clone, Debug)]
pub struct SupervisorWatchdogArgs {
    #[arg(long)]
    pub supervisor_start_ticks: u64,
}

impl LandLockCommand {
    pub fn consumes_meaningful_time(&self) -> bool {
        matches!(self, Self::Acquire(_) | Self::Run(_))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LockState {
    pub agent: String,
    /// Added for supervised exact-repository landings. None preserves parsing
    /// and rendering compatibility with legacy/manual leases.
    pub repo: Option<String>,
    /// Exact operation identity (the authorized head for safe landings).
    pub operation: Option<String>,
    /// Operation whose external mutation may still complete. This barrier is
    /// retained across supervisor death and non-successful child exits.
    pub pending_mutation: Option<String>,
    /// Durable event-log attempt that owns `pending_mutation`.
    pub pending_attempt: Option<String>,
    /// Number of fsynced external-call intents bound to this barrier.
    pub pending_call_count: Option<u64>,
    /// Most recent fsynced call identity, or none when the count is zero.
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

#[derive(Clone, Debug, Eq, PartialEq)]
struct ProcessDomain {
    host: String,
    boot_id: String,
    leader_pid: u32,
    leader_start_ticks: u64,
    pgid: u32,
    deadline_at: i64,
    watchdog_pid: u32,
    watchdog_start_ticks: u64,
}

impl ProcessDomain {
    fn parse(content: &str) -> Result<Self, LandLockError> {
        let mut host = None;
        let mut boot_id = None;
        let mut leader_pid = None;
        let mut leader_start_ticks = None;
        let mut pgid = None;
        let mut deadline_at = None;
        let mut watchdog_pid = None;
        let mut watchdog_start_ticks = None;
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                LandLockError::InvalidState(format!(
                    "domain line {} is not key=value",
                    line_number + 1
                ))
            })?;
            match key {
                "host" => host = Some(value.to_string()),
                "boot_id" => boot_id = Some(value.to_string()),
                "leader_pid" => leader_pid = Some(parse_unsigned(key, value)?),
                "leader_start_ticks" => leader_start_ticks = Some(parse_unsigned(key, value)?),
                "pgid" => pgid = Some(parse_unsigned(key, value)?),
                "deadline_at" => deadline_at = Some(parse_integer(key, value)?),
                "watchdog_pid" => watchdog_pid = Some(parse_unsigned(key, value)?),
                "watchdog_start_ticks" => watchdog_start_ticks = Some(parse_unsigned(key, value)?),
                unknown => {
                    return Err(LandLockError::InvalidState(format!(
                        "unknown domain field {unknown:?}"
                    )));
                }
            }
        }
        Ok(Self {
            host: required(host, "domain host")?,
            boot_id: required(boot_id, "domain boot_id")?,
            leader_pid: u32::try_from(required(leader_pid, "domain leader_pid")?)
                .map_err(|_| LandLockError::InvalidState("domain leader pid exceeds u32".into()))?,
            leader_start_ticks: required(leader_start_ticks, "domain leader_start_ticks")?,
            pgid: u32::try_from(required(pgid, "domain pgid")?)
                .map_err(|_| LandLockError::InvalidState("domain pgid exceeds u32".into()))?,
            deadline_at: required(deadline_at, "domain deadline_at")?,
            watchdog_pid: u32::try_from(required(watchdog_pid, "domain watchdog_pid")?).map_err(
                |_| LandLockError::InvalidState("domain watchdog pid exceeds u32".into()),
            )?,
            watchdog_start_ticks: required(watchdog_start_ticks, "domain watchdog_start_ticks")?,
        })
    }

    fn render(&self) -> String {
        format!(
            "host={}\nboot_id={}\nleader_pid={}\nleader_start_ticks={}\npgid={}\ndeadline_at={}\nwatchdog_pid={}\nwatchdog_start_ticks={}\n",
            self.host,
            self.boot_id,
            self.leader_pid,
            self.leader_start_ticks,
            self.pgid,
            self.deadline_at,
            self.watchdog_pid,
            self.watchdog_start_ticks,
        )
    }
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

#[derive(Clone, Debug, Eq, PartialEq)]
enum DomainLiveness {
    Absent,
    Active,
    Empty,
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
        let state = Self {
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
        };
        match (
            state.pending_mutation.as_deref(),
            state.pending_attempt.as_deref(),
            state.pending_call_count,
            state.pending_call_id.as_deref(),
        ) {
            (None, None, None, None) => {}
            (Some(mutation), Some(attempt), Some(0), None)
                if state.repo.is_some()
                    && state.operation.as_deref() == Some(mutation)
                    && attempt.len() == 32
                    && attempt.bytes().all(|byte| {
                        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                    }) => {}
            (Some(mutation), Some(attempt), Some(count), Some(call_id))
                if count > 0
                    && state.repo.is_some()
                    && state.operation.as_deref() == Some(mutation)
                    && attempt.len() == 32
                    && attempt.bytes().all(|byte| {
                        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                    })
                    && call_id.len() == 32
                    && call_id.bytes().all(|byte| {
                        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                    }) => {}
            _ => {
                return Err(LandLockError::InvalidState(
                    "pending mutation must bind exact operation, repository, attempt, and call high-water"
                        .into(),
                ))
            }
        }
        Ok(state)
    }

    fn render(&self) -> String {
        let mut output = format!("agent={}\n", self.agent);
        if let Some(repo) = &self.repo {
            output.push_str(&format!("repo={repo}\n"));
        }
        if let Some(operation) = &self.operation {
            output.push_str(&format!("operation={operation}\n"));
        }
        if let Some(pending_mutation) = &self.pending_mutation {
            output.push_str(&format!("pending_mutation={pending_mutation}\n"));
        }
        if let Some(pending_attempt) = &self.pending_attempt {
            output.push_str(&format!("pending_attempt={pending_attempt}\n"));
        }
        if let Some(pending_call_count) = self.pending_call_count {
            output.push_str(&format!("pending_call_count={pending_call_count}\n"));
        }
        if let Some(pending_call_id) = &self.pending_call_id {
            output.push_str(&format!("pending_call_id={pending_call_id}\n"));
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
    #[error("landing-lock: assert-child refused: {0}")]
    ChildAssertion(String),
    #[error("landing-lock: supervised child domain is still active: {0}")]
    DomainActive(String),
    #[error("landing-lock: external mutation remains pending: {0}")]
    MutationPending(String),
    #[error("landing-lock: cannot reclaim lease: {0}")]
    ReclaimNotProven(String),
}

impl LandLockError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::RenewNotOwner { .. }
            | Self::ReleaseNotOwner { .. }
            | Self::ProcessNotOwner { .. }
            | Self::ChildAssertion(_)
            | Self::DomainActive(_)
            | Self::MutationPending(_)
            | Self::ReclaimNotProven(_)
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
    domain: PathBuf,
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
            domain: suffix(&lock, ".domain"),
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
        LandLockCommand::AssertChild(args) => lock.assert_child(&args),
        LandLockCommand::ArmMutation(args) => lock.set_mutation_barrier(&args, true),
        LandLockCommand::BindMutationCall(args) => lock.bind_mutation_call(&args),
        LandLockCommand::ClearMutation(args) => lock.set_mutation_barrier(&args, false),
        LandLockCommand::Run(args) => lock.run(args),
        LandLockCommand::Watchdog(args) => run_domain_watchdog(&lock, &args),
        LandLockCommand::Payload(args) => run_payload_launcher(&args),
        LandLockCommand::SupervisorWatchdog(args) => run_supervisor_watchdog(&args),
    }
}

impl LandingLock {
    fn acquire(&self, args: &AcquireArgs) -> Result<i32, LandLockError> {
        self.acquire_with_binding(args, None)
    }

    fn acquire_with_binding(
        &self,
        args: &AcquireArgs,
        binding: Option<(Option<&str>, Option<&str>)>,
    ) -> Result<i32, LandLockError> {
        self.acquire_with_binding_impl(args, binding, None)
    }

    fn acquire_with_binding_supervised(
        &self,
        args: &AcquireArgs,
        binding: Option<(Option<&str>, Option<&str>)>,
        supervisor_watchdog: &mut SupervisorWatchdog,
    ) -> Result<i32, LandLockError> {
        self.acquire_with_binding_impl(args, binding, Some(supervisor_watchdog))
    }

    fn acquire_with_binding_impl(
        &self,
        args: &AcquireArgs,
        binding: Option<(Option<&str>, Option<&str>)>,
        mut supervisor_watchdog: Option<&mut SupervisorWatchdog>,
    ) -> Result<i32, LandLockError> {
        let started = Instant::now();
        let mut last = String::new();
        loop {
            if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                watchdog.arm_for(GUARD_WAIT_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
            }
            let token = match self.with_guard(|| self.try_acquire(args, binding)) {
                Ok(token) => token,
                Err(error) => {
                    if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                        let _ = watchdog.idle();
                    }
                    return Err(error);
                }
            };
            match token {
                AcquireToken::Acquired => {
                    if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                        watchdog
                            .arm_for(CHILD_STARTUP_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
                    }
                    eprintln!(
                        "landing-lock: ACQUIRED by {} for PR #{} (lease {}s)",
                        args.agent, args.pr, args.hold
                    );
                    return Ok(0);
                }
                AcquireToken::AcquiredReclaimed(previous) => {
                    if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                        watchdog
                            .arm_for(CHILD_STARTUP_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
                    }
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
            if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                watchdog.arm_for(POLL_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
            }
            if started.elapsed() >= Duration::from_secs(args.wait) {
                if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                    watchdog.arm_for(GUARD_WAIT_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
                }
                let removal = self.with_guard(|| {
                    self.remove_from_queue(&args.agent)?;
                    Ok(())
                });
                if let Some(watchdog) = supervisor_watchdog.as_deref_mut() {
                    watchdog.idle()?;
                }
                removal?;
                eprintln!("landing-lock: TIMEOUT after {}s", args.wait);
                return Ok(1);
            }
            thread::sleep(Duration::from_secs(POLL_SECONDS));
        }
    }

    fn try_acquire(
        &self,
        args: &AcquireArgs,
        binding: Option<(Option<&str>, Option<&str>)>,
    ) -> Result<AcquireToken, LandLockError> {
        let now = epoch_seconds()?;
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

        let holder = self.read_holder()?;
        let owner_recorded = self.read_process_owner()?.is_some();
        let owner_liveness = self.owner_liveness()?;
        let domain_liveness = self.domain_liveness()?;
        let timed_out_empty_domain = self.timed_out_empty_domain(now)?;
        let timed_out_owner_superseded = timed_out_empty_domain
            && binding.is_some_and(|(repo, operation)| repo.is_some() && operation.is_some());
        let owner_superseded =
            matches!(owner_liveness, OwnerLiveness::Dead(_)) || timed_out_owner_superseded;
        let supervised_active = (matches!(owner_liveness, OwnerLiveness::Alive)
            && !timed_out_owner_superseded)
            || (owner_recorded
                && matches!(owner_liveness, OwnerLiveness::Unknown(_))
                && !timed_out_owner_superseded)
            || matches!(
                domain_liveness,
                DomainLiveness::Active | DomainLiveness::Unknown(_)
            );
        let retained_mutation = holder
            .as_ref()
            .is_some_and(|holder| holder.pending_mutation.is_some());
        if holder.is_none() && supervised_active {
            self.write_queue(&queue)?;
            return Ok(AcquireToken::Held {
                agent: "<supervised-authority-without-holder>".into(),
                seconds_left: 0,
            });
        }
        if let Some(holder) = holder.as_ref().filter(|holder| {
            (holder.live_at(now) && !owner_superseded) || supervised_active || retained_mutation
        }) {
            self.write_queue(&queue)?;
            return Ok(AcquireToken::Held {
                agent: holder.agent.clone(),
                seconds_left: (holder.expires_at - now).max(0),
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
            let reason = match &owner_liveness {
                OwnerLiveness::Dead(reason) => format!(" (dead owner: {reason})"),
                OwnerLiveness::Alive | OwnerLiveness::Unknown(_) if timed_out_owner_superseded => {
                    " (deadline elapsed; exact child domain empty)".into()
                }
                OwnerLiveness::Alive | OwnerLiveness::Unknown(_) => String::new(),
            };
            format!("{}{reason}", holder.agent)
        });
        let mut acquired = new_holder(&args.agent, &args.pr, args.hold, reclaimed.clone())?;
        if let Some((repo, operation)) = binding {
            acquired.repo = repo.map(str::to_string);
            acquired.operation = operation.map(str::to_string);
        }
        self.write_holder(&acquired)?;
        remove_if_exists(&self.paths.owner)?;
        remove_if_exists(&self.paths.domain)?;
        if binding.is_some() {
            self.write_current_process_owner()?;
        }
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
            if holder.agent != agent {
                return Err(LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            self.assert_current_process_owner("renew")?;
            let mut renewed = new_holder(agent, &holder.pr, hold, None)?;
            renewed.repo = holder.repo;
            renewed.operation = holder.operation;
            renewed.pending_mutation = holder.pending_mutation;
            renewed.pending_attempt = holder.pending_attempt;
            renewed.pending_call_count = holder.pending_call_count;
            renewed.pending_call_id = holder.pending_call_id;
            self.write_holder(&renewed)?;
            Ok(())
        })?;
        if announce {
            eprintln!("landing-lock: renewed {agent} lease {hold}s");
        }
        Ok(())
    }

    fn release(&self, agent: &str, announce: bool) -> Result<(), LandLockError> {
        let (released, next) = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                let owner_recorded = self.read_process_owner()?.is_some();
                if owner_recorded {
                    self.assert_current_process_owner("release")?;
                }
                match self.domain_liveness()? {
                    DomainLiveness::Active => {
                        return Err(LandLockError::DomainActive(
                            "holder is missing while its process group remains live".into(),
                        ));
                    }
                    DomainLiveness::Unknown(reason) => {
                        return Err(LandLockError::DomainActive(reason));
                    }
                    DomainLiveness::Absent | DomainLiveness::Empty => {}
                }
                remove_if_exists(&self.paths.owner)?;
                remove_if_exists(&self.paths.domain)?;
                return Ok((false, None));
            };
            if holder.agent != agent {
                return Err(LandLockError::ReleaseNotOwner {
                    agent: agent.to_string(),
                    holder: holder.agent,
                });
            }
            if let Some(operation) = &holder.pending_mutation {
                return Err(LandLockError::MutationPending(operation.clone()));
            }
            self.assert_current_process_owner("release")?;
            match self.domain_liveness()? {
                DomainLiveness::Active => {
                    return Err(LandLockError::DomainActive(
                        "process group still contains live members".into(),
                    ));
                }
                DomainLiveness::Unknown(reason) => {
                    return Err(LandLockError::DomainActive(reason));
                }
                DomainLiveness::Absent | DomainLiveness::Empty => {}
            }
            remove_if_exists(&self.paths.lock)?;
            remove_if_exists(&self.paths.owner)?;
            remove_if_exists(&self.paths.domain)?;
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
        let owner_recorded = self.read_process_owner()?.is_some();
        let liveness = self.owner_liveness()?;
        let domain = self.domain_liveness()?;
        match holder {
            Some(holder) if holder.pending_mutation.is_some() => {
                println!("RETAINED_MUTATION (recovery required):");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
            }
            Some(holder)
                if matches!(domain, DomainLiveness::Active | DomainLiveness::Unknown(_)) =>
            {
                println!("SUPERVISED_DOMAIN_ACTIVE (not reclaimable):");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
            }
            Some(holder)
                if matches!(liveness, OwnerLiveness::Alive)
                    || (owner_recorded && matches!(liveness, OwnerLiveness::Unknown(_))) =>
            {
                println!("HELD:");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
                println!("  secs_left={}", (holder.expires_at - now).max(0));
            }
            Some(holder) if holder.live_at(now) && matches!(liveness, OwnerLiveness::Dead(_)) => {
                println!("ORPHANED (reclaimable):");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
                println!("  secs_left={}", holder.expires_at - now);
            }
            Some(holder) if holder.live_at(now) => {
                println!("HELD:");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
                println!("  secs_left={}", holder.expires_at - now);
            }
            Some(holder) => {
                println!("LAPSED (reclaimable):");
                for line in holder.render().lines() {
                    println!("  {line}");
                }
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
            }
            None if owner_recorded || !matches!(domain, DomainLiveness::Absent) => {
                println!("INCONSISTENT_SUPERVISED_AUTHORITY (not free):");
                println!("  owner_process={}", render_liveness(&liveness));
                println!("  child_domain={}", render_domain_liveness(&domain));
            }
            None => println!("FREE"),
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

    fn reclaim_dead(&self) -> Result<i32, LandLockError> {
        let reclaimed = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                match self.domain_liveness()? {
                    DomainLiveness::Active => {
                        return Err(LandLockError::ReclaimNotProven(
                            "holder is missing while supervised child domain is active".into(),
                        ));
                    }
                    DomainLiveness::Unknown(reason) => {
                        return Err(LandLockError::ReclaimNotProven(reason));
                    }
                    DomainLiveness::Absent | DomainLiveness::Empty => {}
                }
                match self.owner_liveness()? {
                    OwnerLiveness::Alive => {
                        return Err(LandLockError::ReclaimNotProven(
                            "holder is missing while recorded owner process is alive".into(),
                        ));
                    }
                    OwnerLiveness::Unknown(reason) if self.read_process_owner()?.is_some() => {
                        return Err(LandLockError::ReclaimNotProven(reason));
                    }
                    OwnerLiveness::Dead(_) | OwnerLiveness::Unknown(_) => {
                        remove_if_exists(&self.paths.owner)?;
                        remove_if_exists(&self.paths.domain)?;
                        return Ok(None);
                    }
                }
            };
            if holder.pending_mutation.is_some() {
                return Err(LandLockError::ReclaimNotProven(format!(
                    "external mutation {:?} requires exact-operation recovery",
                    holder.pending_mutation
                )));
            }
            match self.domain_liveness()? {
                DomainLiveness::Active => {
                    return Err(LandLockError::ReclaimNotProven(
                        "supervised child domain is still active".into(),
                    ));
                }
                DomainLiveness::Unknown(reason) => {
                    return Err(LandLockError::ReclaimNotProven(reason));
                }
                DomainLiveness::Absent | DomainLiveness::Empty => {}
            }
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => {
                    remove_if_exists(&self.paths.lock)?;
                    remove_if_exists(&self.paths.owner)?;
                    remove_if_exists(&self.paths.domain)?;
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

    fn try_adopt_retained_mutation(&self, args: &RunArgs) -> Result<bool, LandLockError> {
        let Some(holder) = self.read_holder()? else {
            return Ok(false);
        };
        let Some(pending) = holder.pending_mutation.as_deref() else {
            return Ok(false);
        };
        let pending_attempt = holder.pending_attempt.clone();
        let pending_call_count = holder.pending_call_count;
        let pending_call_id = holder.pending_call_id.clone();
        if holder.agent != args.agent
            || holder.pr != args.pr
            || holder.repo != args.repo
            || holder.operation != args.operation
            || args.operation.as_deref() != Some(pending)
        {
            return Ok(false);
        }
        let timed_out_empty_domain = self.timed_out_empty_domain(epoch_seconds()?)?;
        if self.read_process_owner()?.is_some() {
            match self.owner_liveness()? {
                OwnerLiveness::Dead(_) => {}
                OwnerLiveness::Alive | OwnerLiveness::Unknown(_) if timed_out_empty_domain => {}
                OwnerLiveness::Alive | OwnerLiveness::Unknown(_) => return Ok(false),
            }
        }
        match self.domain_liveness()? {
            DomainLiveness::Absent | DomainLiveness::Empty => {}
            DomainLiveness::Active | DomainLiveness::Unknown(_) => return Ok(false),
        }

        let mut adopted = new_holder(&args.agent, &args.pr, args.hold, None)?;
        adopted.repo = args.repo.clone();
        adopted.operation = args.operation.clone();
        adopted.pending_mutation = Some(pending.to_string());
        adopted.pending_attempt = pending_attempt;
        adopted.pending_call_count = pending_call_count;
        adopted.pending_call_id = pending_call_id;
        self.write_holder(&adopted)?;
        remove_if_exists(&self.paths.owner)?;
        remove_if_exists(&self.paths.domain)?;
        self.write_current_process_owner()?;
        self.remove_from_queue(&args.agent)?;
        Ok(true)
    }

    fn run(&self, args: RunArgs) -> Result<i32, LandLockError> {
        if args.child.is_empty() {
            return Err(LandLockError::EmptyChild);
        }
        if args.child_deadline == 0 {
            return Err(LandLockError::UnboundedChildDeadline);
        }
        let payload_executable = internal_executable()?;
        // This exact-parent pidfd watchdog exists before the first guard
        // attempt. It is the canonical run path's escape hatch if SIGSTOP or a
        // deadlock freezes the supervisor while it owns the advisory flock.
        let mut supervisor_watchdog = SupervisorWatchdog::spawn()?;
        let acquire = AcquireArgs {
            agent: args.agent.clone(),
            pr: args.pr.clone(),
            wait: args.wait,
            hold: args.hold,
        };
        supervisor_watchdog.arm_for(GUARD_WAIT_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
        let adopted = match self.with_guard(|| self.try_adopt_retained_mutation(&args)) {
            Ok(adopted) => adopted,
            Err(error) => {
                let _ = supervisor_watchdog.idle();
                return Err(error);
            }
        };
        if adopted {
            supervisor_watchdog.arm_for(CHILD_STARTUP_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
            eprintln!(
                "landing-lock: ADOPTED retained mutation by {} for PR #{} operation={:?}",
                args.agent, args.pr, args.operation
            );
        } else {
            supervisor_watchdog.idle()?;
            let binding = Some((args.repo.as_deref(), args.operation.as_deref()));
            let acquire_status =
                self.acquire_with_binding_supervised(&acquire, binding, &mut supervisor_watchdog)?;
            if acquire_status != 0 {
                supervisor_watchdog.finish()?;
                return Ok(acquire_status);
            }
        }

        let (stop_tx, stop_rx) = mpsc::channel();
        let heartbeat_failed = Arc::new(AtomicBool::new(false));
        let heartbeat_paths = self.paths.clone();
        let heartbeat_agent = args.agent.clone();
        let heartbeat_hold = args.hold;
        let heartbeat_failed_worker = Arc::clone(&heartbeat_failed);
        let heartbeat = thread::spawn(move || {
            let heartbeat_lock = LandingLock {
                paths: heartbeat_paths,
            };
            let interval = Duration::from_secs((heartbeat_hold / 3).max(1));
            while stop_rx.recv_timeout(interval).is_err() {
                if heartbeat_lock
                    .renew(&heartbeat_agent, heartbeat_hold, false)
                    .is_err()
                {
                    heartbeat_failed_worker.store(true, Ordering::Release);
                    break;
                }
            }
        });

        // Run the child in its own process group so a deadline kill reaches the
        // whole land subtree (gh / git / cargo), not just the wrapper shell.
        // PDEATHSIG shortens supervisor-death cleanup; the persisted process
        // domain remains the authority for refusing reacquisition until every
        // group member is gone.
        let supervisor_pid = std::process::id() as libc::pid_t;
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "kill -STOP $$; exec \"$@\"", "ci-hub-land-child"])
            .arg(&payload_executable)
            .args(["land-lock", "payload", "--"])
            .args(&args.child)
            .process_group(0);
        // SAFETY: this closure runs after fork and before exec. It calls only
        // async-signal-safe libc operations and returns fixed errno values.
        unsafe {
            command.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(io::Error::last_os_error());
                }
                if libc::getppid() != supervisor_pid {
                    return Err(io::Error::from_raw_os_error(libc::ESRCH));
                }
                Ok(())
            });
        }
        let outcome: Result<i32, LandLockError> = match command.spawn() {
            Ok(mut child) => {
                let stopped = wait_for_process_state(
                    child.id(),
                    |state| state == "T" || state == "t",
                    Duration::from_secs(CHILD_STARTUP_SECONDS),
                );
                let domain_result = match stopped {
                    Ok(true) => self.start_process_domain(child.id(), args.child_deadline),
                    Ok(false) => Err(LandLockError::InvalidState(format!(
                        "supervised child {} did not stop before startup deadline",
                        child.id()
                    ))),
                    Err(source) => Err(LandLockError::Io {
                        action: "observe stopped child from",
                        path: PathBuf::from(format!("/proc/{}/stat", child.id())),
                        source,
                    }),
                };
                match domain_result {
                    Ok(mut watchdog) => {
                        let supervisor_deadline = args
                            .child_deadline
                            .saturating_add(2 * CHILD_TERM_GRACE_SECONDS)
                            .saturating_add(SUPERVISOR_PHASE_MARGIN_SECONDS);
                        if let Err(error) = supervisor_watchdog.arm_for(supervisor_deadline) {
                            watchdog.trigger_cleanup();
                            Err(error)
                        } else {
                            let group = format!("-{}", child.id());
                            if !signal_group_succeeds("CONT", &group) {
                                let emptied = terminate_child_group(&mut child, &args.pr);
                                if !emptied {
                                    watchdog.trigger_cleanup();
                                    Err(LandLockError::DomainActive(format!(
                                        "failed to resume or empty process group {}",
                                        child.id()
                                    )))
                                } else {
                                    let resume_error = LandLockError::InvalidState(format!(
                                        "failed to resume supervised process group {}",
                                        child.id()
                                    ));
                                    match watchdog.finish() {
                                        Ok(()) => Err(resume_error),
                                        Err(error) => Err(error),
                                    }
                                }
                            } else {
                                let supervised = supervise_child(
                                    &mut child,
                                    args.child_deadline,
                                    &args.pr,
                                    &heartbeat_failed,
                                    &mut watchdog,
                                    &mut supervisor_watchdog,
                                );
                                match supervised {
                                    Ok(outcome) => match watchdog.finish() {
                                        Ok(()) => Ok(match outcome {
                                            ChildOutcome::Exited(status) => {
                                                exit_status_code(status)
                                            }
                                            ChildOutcome::TimedOut => CHILD_DEADLINE_EXIT_CODE,
                                            ChildOutcome::HeartbeatFailed => {
                                                HEARTBEAT_FAILURE_EXIT_CODE
                                            }
                                        }),
                                        Err(error) => Err(error),
                                    },
                                    Err(error) => {
                                        watchdog.trigger_cleanup();
                                        Err(error)
                                    }
                                }
                            }
                        }
                    }
                    Err(error) => {
                        let emptied = terminate_child_group(&mut child, &args.pr);
                        if !emptied {
                            Err(LandLockError::DomainActive(format!(
                                "failed to bind and empty process group {}",
                                child.id()
                            )))
                        } else {
                            Err(error)
                        }
                    }
                }
            }
            Err(source) => Err(LandLockError::Io {
                action: "launch child from",
                path: PathBuf::from(&args.child[0]),
                source,
            }),
        };
        // Child supervision is over; bound heartbeat shutdown and every final
        // guard before waiting on a thread that may itself be wedged in flock
        // or filesystem I/O.
        supervisor_watchdog.arm_for(GUARD_WAIT_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
        let _ = stop_tx.send(());
        let _ = heartbeat.join();

        match self.domain_liveness()? {
            DomainLiveness::Active => {
                return Err(LandLockError::DomainActive(
                    "child process group remained live after supervision".into(),
                ));
            }
            DomainLiveness::Unknown(reason) => {
                return Err(LandLockError::DomainActive(reason));
            }
            DomainLiveness::Absent | DomainLiveness::Empty => {}
        }

        let result_code = outcome
            .as_ref()
            .copied()
            .unwrap_or_else(|error| error.exit_code());
        supervisor_watchdog.arm_for(GUARD_WAIT_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS)?;
        let retained = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                return Ok(false);
            };
            if holder.pending_mutation.is_none() {
                return Ok(false);
            }
            self.assert_current_process_owner("retain mutation")?;
            remove_if_exists(&self.paths.owner)?;
            remove_if_exists(&self.paths.domain)?;
            Ok(true)
        })?;
        if retained {
            supervisor_watchdog.finish()?;
            eprintln!(
                "landing-lock: RETAINED external mutation for PR #{} operation={:?}; exact-operation recovery required",
                args.pr, args.operation
            );
            if result_code == 0 {
                return Err(LandLockError::MutationPending(
                    args.operation.unwrap_or_else(|| "unknown operation".into()),
                ));
            }
            return outcome;
        }

        let release_result = self.release(&args.agent, true);
        supervisor_watchdog.finish()?;
        release_result?;
        if result_code == CHILD_DEADLINE_EXIT_CODE {
            eprintln!(
                "landing-lock: ABANDON PR #{}: child exceeded --child-deadline {}s; \
                 verified the land subtree empty and RELEASED the lock. PR left open for retry.",
                args.pr, args.child_deadline
            );
        } else if result_code == HEARTBEAT_FAILURE_EXIT_CODE {
            eprintln!(
                "landing-lock: ABANDON PR #{}: lease heartbeat failed; verified the land subtree empty and RELEASED the lock",
                args.pr
            );
        }
        outcome
    }

    fn assert_child(&self, args: &AssertChildArgs) -> Result<i32, LandLockError> {
        let verifier_pid = std::process::id();
        let (
            supervisor_pid,
            child_depth,
            verifier_depth,
            pending_mutation,
            pending_attempt,
            pending_call_count,
            pending_call_id,
        ) = self.with_guard(|| self.assert_child_process(args, verifier_pid))?;
        println!(
            "LOCK_CHILD_VERIFIED agent={} repo={} pr={} operation={} child_pid={} verifier_pid={} supervisor_pid={} child_depth={} verifier_depth={} pending_mutation={} pending_attempt={} pending_call_count={} pending_call_id={}",
            args.agent,
            args.repo,
            args.pr,
            args.operation.as_deref().unwrap_or("-"),
            args.child_pid,
            verifier_pid,
            supervisor_pid,
            child_depth,
            verifier_depth,
            pending_mutation.as_deref().unwrap_or("-"),
            pending_attempt.as_deref().unwrap_or("-"),
            pending_call_count.map_or_else(|| "-".into(), |count| count.to_string()),
            pending_call_id.as_deref().unwrap_or("-"),
        );
        Ok(0)
    }

    fn set_mutation_barrier(&self, args: &MutationArgs, armed: bool) -> Result<i32, LandLockError> {
        if args.attempt_id.len() != 32
            || !args
                .attempt_id
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(LandLockError::ChildAssertion(
                "mutation barrier attempt id must be lowercase 32-hex".into(),
            ));
        }
        let verifier_pid = std::process::id();
        let assertion = AssertChildArgs {
            agent: args.agent.clone(),
            repo: args.repo.clone(),
            pr: args.pr.clone(),
            operation: Some(args.operation.clone()),
            child_pid: args.child_pid,
        };
        self.with_guard(|| {
            self.assert_child_process(&assertion, verifier_pid)?;
            let mut holder = self
                .read_holder()?
                .ok_or_else(|| LandLockError::ChildAssertion("no landing lease is held".into()))?;
            let existing = (
                holder.pending_mutation.as_deref(),
                holder.pending_attempt.as_deref(),
            );
            let expected = (
                Some(args.operation.as_str()),
                Some(args.attempt_id.as_str()),
            );
            match (armed, existing) {
                (true, (None, None)) => {
                    holder.pending_mutation = Some(args.operation.clone());
                    holder.pending_attempt = Some(args.attempt_id.clone());
                    holder.pending_call_count = Some(0);
                    holder.pending_call_id = None;
                }
                (true, observed) if observed == expected => {}
                (true, observed) => {
                    return Err(LandLockError::ChildAssertion(format!(
                        "another mutation barrier is already armed: {observed:?}"
                    )))
                }
                (false, (None, None)) => {}
                (false, observed) if observed == expected => {
                    holder.pending_mutation = None;
                    holder.pending_attempt = None;
                    holder.pending_call_count = None;
                    holder.pending_call_id = None;
                }
                (false, observed) => {
                    return Err(LandLockError::ChildAssertion(format!(
                        "mutation barrier is {observed:?}, not {expected:?}"
                    )))
                }
            }
            self.write_holder(&holder)
        })?;
        println!(
            "MUTATION_BARRIER_{} agent={} repo={} pr={} operation={} attempt_id={}",
            if armed { "ARMED" } else { "CLEARED" },
            args.agent,
            args.repo,
            args.pr,
            args.operation,
            args.attempt_id,
        );
        Ok(0)
    }

    fn bind_mutation_call(&self, args: &MutationCallArgs) -> Result<i32, LandLockError> {
        let valid_hex = |value: &str| {
            value.len() == 32
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        };
        if !valid_hex(&args.attempt_id) || !valid_hex(&args.call_id) || args.call_count == 0 {
            return Err(LandLockError::ChildAssertion(
                "mutation call requires lowercase 32-hex attempt/call ids and positive count"
                    .into(),
            ));
        }
        let verifier_pid = std::process::id();
        let assertion = AssertChildArgs {
            agent: args.agent.clone(),
            repo: args.repo.clone(),
            pr: args.pr.clone(),
            operation: Some(args.operation.clone()),
            child_pid: args.child_pid,
        };
        self.with_guard(|| {
            self.assert_child_process(&assertion, verifier_pid)?;
            let mut holder = self
                .read_holder()?
                .ok_or_else(|| LandLockError::ChildAssertion("no landing lease is held".into()))?;
            if holder.pending_mutation.as_deref() != Some(args.operation.as_str())
                || holder.pending_attempt.as_deref() != Some(args.attempt_id.as_str())
            {
                return Err(LandLockError::ChildAssertion(
                    "mutation call does not match the armed operation and attempt".into(),
                ));
            }
            let prior_count = holder.pending_call_count.ok_or_else(|| {
                LandLockError::ChildAssertion("mutation barrier has no call high-water".into())
            })?;
            let expected_count = prior_count.checked_add(1).ok_or_else(|| {
                LandLockError::ChildAssertion("mutation call count overflow".into())
            })?;
            if args.call_count != expected_count {
                return Err(LandLockError::ChildAssertion(format!(
                    "mutation call count {} does not advance prior count {prior_count} by one",
                    args.call_count
                )));
            }
            if holder.pending_call_id.as_deref() == Some(args.call_id.as_str()) {
                return Err(LandLockError::ChildAssertion(
                    "mutation call id repeats the prior high-water".into(),
                ));
            }
            holder.pending_call_count = Some(args.call_count);
            holder.pending_call_id = Some(args.call_id.clone());
            self.write_holder(&holder)
        })?;
        println!(
            "MUTATION_CALL_BOUND agent={} repo={} pr={} operation={} attempt_id={} call_count={} call_id={}",
            args.agent,
            args.repo,
            args.pr,
            args.operation,
            args.attempt_id,
            args.call_count,
            args.call_id,
        );
        Ok(0)
    }

    /// Canonical child-ownership predicate. The selected Python landing child
    /// must descend from the recorded supervisor, and the short-lived verifier
    /// must descend from that child. Bounded walks on both sides permit the
    /// host's Python and rust-script launchers without accepting an unrelated
    /// process tree.
    fn assert_child_process(
        &self,
        args: &AssertChildArgs,
        verifier_pid: u32,
    ) -> Result<
        (
            u32,
            usize,
            usize,
            Option<String>,
            Option<String>,
            Option<u64>,
            Option<String>,
        ),
        LandLockError,
    > {
        let now = epoch_seconds()?;
        let holder = self
            .read_holder()?
            .ok_or_else(|| LandLockError::ChildAssertion("no landing lease is held".into()))?;
        if !holder.live_at(now) {
            return Err(LandLockError::ChildAssertion(
                "landing lease is expired".into(),
            ));
        }
        if holder.agent != args.agent {
            return Err(LandLockError::ChildAssertion(format!(
                "holder agent is {:?}, not {:?}",
                holder.agent, args.agent
            )));
        }
        if holder.repo.as_deref() != Some(args.repo.as_str()) {
            return Err(LandLockError::ChildAssertion(format!(
                "holder repository is {:?}, not {:?}",
                holder.repo, args.repo
            )));
        }
        if holder.operation != args.operation {
            return Err(LandLockError::ChildAssertion(format!(
                "holder operation is {:?}, not {:?}",
                holder.operation, args.operation
            )));
        }
        if holder.pr != args.pr {
            return Err(LandLockError::ChildAssertion(format!(
                "holder PR is {:?}, not {:?}",
                holder.pr, args.pr
            )));
        }
        match (&holder.pending_mutation, &holder.pending_attempt) {
            (None, None) | (Some(_), Some(_)) => {}
            _ => {
                return Err(LandLockError::ChildAssertion(
                    "mutation barrier lacks an exact durable attempt binding".into(),
                ))
            }
        }
        let pending_mutation = holder.pending_mutation.clone();
        let pending_attempt = holder.pending_attempt.clone();
        let pending_call_count = holder.pending_call_count;
        let pending_call_id = holder.pending_call_id.clone();
        let local_host = current_host();
        if local_host == "unknown" {
            return Err(LandLockError::ChildAssertion(
                "current host identity is unavailable".into(),
            ));
        }
        if holder.host != local_host {
            return Err(LandLockError::ChildAssertion(format!(
                "holder host is {:?}, not current host {:?}",
                holder.host, local_host
            )));
        }
        let owner = self.read_process_owner()?.ok_or_else(|| {
            LandLockError::ChildAssertion("lease has no supervised process identity".into())
        })?;
        match self.owner_liveness()? {
            OwnerLiveness::Alive => {}
            OwnerLiveness::Dead(reason) => {
                return Err(LandLockError::ChildAssertion(format!(
                    "recorded supervisor is dead: {reason}"
                )));
            }
            OwnerLiveness::Unknown(reason) => {
                return Err(LandLockError::ChildAssertion(format!(
                    "recorded supervisor cannot be verified: {reason}"
                )));
            }
        }
        let domain = self.read_process_domain()?.ok_or_else(|| {
            LandLockError::ChildAssertion("lease has no supervised child domain".into())
        })?;
        if domain.host != local_host || domain.boot_id != current_boot_id()? {
            return Err(LandLockError::ChildAssertion(
                "supervised child domain is not on the current host boot".into(),
            ));
        }
        match recorded_process_liveness(
            domain.watchdog_pid,
            domain.watchdog_start_ticks,
            "domain watchdog",
        ) {
            OwnerLiveness::Alive => {}
            OwnerLiveness::Dead(reason) | OwnerLiveness::Unknown(reason) => {
                return Err(LandLockError::ChildAssertion(format!(
                    "deadline watchdog is not live: {reason}"
                )));
            }
        }
        if epoch_seconds()? >= domain.deadline_at {
            return Err(LandLockError::ChildAssertion(
                "supervised child domain deadline has expired".into(),
            ));
        }
        let leader_ticks =
            process_start_ticks(domain.leader_pid).map_err(|source| LandLockError::Io {
                action: "read supervised child identity from",
                path: PathBuf::from(format!("/proc/{}/stat", domain.leader_pid)),
                source,
            })?;
        if leader_ticks != domain.leader_start_ticks
            || process_group_id(domain.leader_pid).map_err(|source| LandLockError::Io {
                action: "read supervised child process group from",
                path: PathBuf::from(format!("/proc/{}/stat", domain.leader_pid)),
                source,
            })? != domain.pgid
        {
            return Err(LandLockError::ChildAssertion(
                "supervised child domain identity changed".into(),
            ));
        }
        let leader_parent =
            process_parent_pid(domain.leader_pid).map_err(|source| LandLockError::Io {
                action: "read supervised child parent from",
                path: PathBuf::from(format!("/proc/{}/stat", domain.leader_pid)),
                source,
            })?;
        if leader_parent != owner.pid {
            return Err(LandLockError::ChildAssertion(format!(
                "supervised domain leader {} is not a direct child of owner {}",
                domain.leader_pid, owner.pid
            )));
        }
        if process_relation_depth(args.child_pid, domain.leader_pid)
            .map_err(|source| LandLockError::Io {
                action: "read selected landing child ancestry from",
                path: PathBuf::from(format!("/proc/{}/stat", args.child_pid)),
                source,
            })?
            .is_none()
        {
            return Err(LandLockError::ChildAssertion(format!(
                "selected landing child {} is outside supervised domain leader {}",
                args.child_pid, domain.leader_pid
            )));
        }
        for (role, pid) in [
            ("selected landing child", args.child_pid),
            ("landing verifier", verifier_pid),
        ] {
            let pgid = process_group_id(pid).map_err(|source| LandLockError::Io {
                action: "read supervised process group from",
                path: PathBuf::from(format!("/proc/{pid}/stat")),
                source,
            })?;
            if pgid != domain.pgid {
                return Err(LandLockError::ChildAssertion(format!(
                    "{role} {pid} escaped supervised process group {} into {pgid}",
                    domain.pgid
                )));
            }
        }
        let child_depth = process_ancestry_depth(args.child_pid, owner.pid).map_err(|source| {
            LandLockError::Io {
                action: "read landing child ancestry from",
                path: PathBuf::from(format!("/proc/{}/stat", args.child_pid)),
                source,
            }
        })?;
        let Some(child_depth) = child_depth else {
            return Err(LandLockError::ChildAssertion(format!(
                "landing child {} is not a bounded descendant of recorded supervisor {}",
                args.child_pid, owner.pid
            )));
        };
        let verifier_depth =
            process_ancestry_depth(verifier_pid, args.child_pid).map_err(|source| {
                LandLockError::Io {
                    action: "read landing verifier ancestry from",
                    path: PathBuf::from(format!("/proc/{verifier_pid}/stat")),
                    source,
                }
            })?;
        let Some(verifier_depth) = verifier_depth else {
            return Err(LandLockError::ChildAssertion(format!(
                "verifier {verifier_pid} is not a bounded descendant of landing child {}",
                args.child_pid
            )));
        };
        Ok((
            owner.pid,
            child_depth,
            verifier_depth,
            pending_mutation,
            pending_attempt,
            pending_call_count,
            pending_call_id,
        ))
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

    fn read_process_domain(&self) -> Result<Option<ProcessDomain>, LandLockError> {
        if !self.paths.domain.exists() {
            return Ok(None);
        }
        let content = read_to_string(&self.paths.domain)?;
        Ok(Some(ProcessDomain::parse(&content)?))
    }

    fn assert_watchdog_authority(&self, args: &WatchdogArgs) -> Result<(), LandLockError> {
        let domain = self.read_process_domain()?.ok_or_else(|| {
            LandLockError::ChildAssertion("watchdog has no persisted process domain".into())
        })?;
        let watchdog = current_process_owner()?;
        if domain.host != watchdog.host
            || domain.boot_id != watchdog.boot_id
            || domain.leader_pid != args.leader_pid
            || domain.leader_start_ticks != args.leader_start_ticks
            || domain.pgid != args.pgid
            || domain.deadline_at != args.deadline_at
            || domain.watchdog_pid != watchdog.pid
            || domain.watchdog_start_ticks != watchdog.start_ticks
        {
            return Err(LandLockError::ChildAssertion(
                "watchdog arguments and exact process identity do not match .domain".into(),
            ));
        }
        if process_group_id(watchdog.pid).map_err(|source| LandLockError::Io {
            action: "read watchdog process group from",
            path: PathBuf::from(format!("/proc/{}/stat", watchdog.pid)),
            source,
        })? != watchdog.pid
        {
            return Err(LandLockError::ChildAssertion(
                "watchdog is not isolated in its own process group".into(),
            ));
        }
        let owner = self.read_process_owner()?.ok_or_else(|| {
            LandLockError::ChildAssertion("watchdog has no persisted supervisor owner".into())
        })?;
        let parent_pid = process_parent_pid(watchdog.pid).map_err(|source| LandLockError::Io {
            action: "read watchdog parent from",
            path: PathBuf::from(format!("/proc/{}/stat", watchdog.pid)),
            source,
        })?;
        if parent_pid != owner.pid {
            return Err(LandLockError::ChildAssertion(format!(
                "watchdog parent {parent_pid} is not persisted owner {}",
                owner.pid
            )));
        }
        match self.owner_liveness()? {
            OwnerLiveness::Alive => Ok(()),
            OwnerLiveness::Dead(reason) | OwnerLiveness::Unknown(reason) => {
                Err(LandLockError::ChildAssertion(format!(
                    "watchdog supervisor identity is not live: {reason}"
                )))
            }
        }
    }

    fn write_current_process_owner(&self) -> Result<(), LandLockError> {
        let owner = current_process_owner()?;
        write_truncated(&self.paths.owner, owner.render().as_bytes())
    }

    fn start_process_domain(
        &self,
        leader_pid: u32,
        deadline_seconds: u64,
    ) -> Result<DomainWatchdog, LandLockError> {
        let owner = process_owner(leader_pid)?;
        let pgid = process_group_id(leader_pid).map_err(|source| LandLockError::Io {
            action: "read child process group from",
            path: PathBuf::from(format!("/proc/{leader_pid}/stat")),
            source,
        })?;
        if pgid != leader_pid {
            return Err(LandLockError::InvalidState(format!(
                "supervised child {leader_pid} is in process group {pgid}, not its own group"
            )));
        }
        let deadline_at =
            epoch_seconds()?.saturating_add(i64::try_from(deadline_seconds).unwrap_or(i64::MAX));
        let mut watchdog = DomainWatchdog::spawn(
            pgid,
            leader_pid,
            owner.start_ticks,
            deadline_at,
            &self.paths.lock,
        )?;
        let domain = ProcessDomain {
            host: owner.host,
            boot_id: owner.boot_id,
            leader_pid,
            leader_start_ticks: owner.start_ticks,
            pgid,
            deadline_at,
            watchdog_pid: watchdog.owner.pid,
            watchdog_start_ticks: watchdog.owner.start_ticks,
        };
        if let Err(error) =
            self.with_guard(|| write_truncated(&self.paths.domain, domain.render().as_bytes()))
        {
            watchdog.cancel_unarmed();
            return Err(error);
        }
        if let Err(error) = watchdog.arm() {
            watchdog.cancel_unarmed();
            return Err(error);
        }
        Ok(watchdog)
    }

    fn domain_liveness(&self) -> Result<DomainLiveness, LandLockError> {
        let Some(domain) = self.read_process_domain()? else {
            return Ok(DomainLiveness::Absent);
        };
        let host = current_host();
        if domain.host != host {
            return Ok(DomainLiveness::Unknown(format!(
                "domain host {} differs from local host {host}",
                domain.host
            )));
        }
        let boot_id = current_boot_id()?;
        if domain.boot_id != boot_id {
            return Ok(DomainLiveness::Empty);
        }
        let watchdog = recorded_process_liveness(
            domain.watchdog_pid,
            domain.watchdog_start_ticks,
            "domain watchdog",
        );
        match (process_group_has_live_members(domain.pgid), watchdog) {
            (Ok(true), _) | (Ok(false), OwnerLiveness::Alive) => Ok(DomainLiveness::Active),
            (Ok(false), OwnerLiveness::Dead(_)) => Ok(DomainLiveness::Empty),
            (Ok(false), OwnerLiveness::Unknown(reason)) => Ok(DomainLiveness::Unknown(reason)),
            (Err(_), OwnerLiveness::Alive) => Ok(DomainLiveness::Active),
            (Err(error), OwnerLiveness::Dead(_) | OwnerLiveness::Unknown(_)) => {
                Ok(DomainLiveness::Unknown(format!(
                    "cannot inspect process group {}: {error}",
                    domain.pgid
                )))
            }
        }
    }

    /// A live-but-wedged supervisor stops being an authorization proxy only
    /// after its durable deadline, both TERM/KILL grace windows, and an extra
    /// observation second have elapsed, with the exact watchdog dead and the
    /// payload group proven empty. If the old supervisor later resumes, its
    /// persisted owner tuple has already been replaced, so renew/release and
    /// mutation-barrier writes fail their canonical owner check.
    fn timed_out_empty_domain(&self, now: i64) -> Result<bool, LandLockError> {
        let Some(domain) = self.read_process_domain()? else {
            return Ok(false);
        };
        if domain.host != current_host() || domain.boot_id != current_boot_id()? {
            return Ok(false);
        }
        let recovery_at = domain
            .deadline_at
            .saturating_add((2 * CHILD_TERM_GRACE_SECONDS + 1) as i64);
        if now < recovery_at {
            return Ok(false);
        }
        Ok(matches!(self.domain_liveness()?, DomainLiveness::Empty))
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
        Ok(recorded_process_liveness(
            owner.pid,
            owner.start_ticks,
            "owner",
        ))
    }

    fn assert_current_process_owner(&self, operation: &'static str) -> Result<(), LandLockError> {
        let Some(owner) = self.read_process_owner()? else {
            let supervised_holder = self.read_holder()?.is_some_and(|holder| {
                holder.repo.is_some()
                    || holder.operation.is_some()
                    || holder.pending_mutation.is_some()
            });
            if supervised_holder {
                return Err(LandLockError::InvalidState(format!(
                    "cannot {operation}: supervised holder has no exact owner fence"
                )));
            }
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

fn process_stat_value(pid: u32, index: usize, name: &str) -> io::Result<String> {
    let path = PathBuf::from(format!("/proc/{pid}/stat"));
    let stat = fs::read_to_string(path)?;
    let close = stat.rfind(')').ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no closing comm",
        )
    })?;
    let fields: Vec<_> = stat[close + 1..].split_whitespace().collect();
    fields
        .get(index)
        .map(|value| (*value).to_string())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("process stat has no {name} field"),
            )
        })
}

pub(crate) fn process_start_ticks(pid: u32) -> io::Result<u64> {
    let value = process_stat_value(pid, 19, "starttime")?;
    value.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process starttime {value:?}: {error}"),
        )
    })
}

fn process_parent_pid(pid: u32) -> io::Result<u32> {
    let value = process_stat_value(pid, 1, "parent pid")?;
    value.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process parent pid {value:?}: {error}"),
        )
    })
}

fn process_state(pid: u32) -> io::Result<String> {
    process_stat_value(pid, 0, "state")
}

fn process_group_id(pid: u32) -> io::Result<u32> {
    let value = process_stat_value(pid, 2, "process group")?;
    value.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process group {value:?}: {error}"),
        )
    })
}

fn process_relation_depth(descendant_pid: u32, ancestor_pid: u32) -> io::Result<Option<usize>> {
    if descendant_pid == ancestor_pid {
        return Ok(Some(0));
    }
    process_ancestry_depth(descendant_pid, ancestor_pid)
}

fn process_ancestry_depth(descendant_pid: u32, ancestor_pid: u32) -> io::Result<Option<usize>> {
    let mut current = descendant_pid;
    // Expected warm/cold paths are Python -> rust-script -> verifier. Keep a
    // small hard ceiling so malformed or surprising process topology fails
    // closed instead of turning this into an unbounded proxy walk.
    for depth in 1..=MAX_ASSERT_CHILD_ANCESTRY_DEPTH {
        let parent = process_parent_pid(current)?;
        if parent == ancestor_pid {
            return Ok(Some(depth));
        }
        if parent == 0 || parent == current {
            return Ok(None);
        }
        current = parent;
    }
    Ok(None)
}

/// Return whether a process group contains any non-zombie member. `/proc` is
/// the source of truth because the direct child can exit while a grandchild
/// keeps the group alive. A disappearing entry is a benign scan race; every
/// other inspection failure is propagated so callers fail closed.
fn process_group_has_live_members(pgid: u32) -> io::Result<bool> {
    Ok(!process_group_live_identities(pgid)?.is_empty())
}

/// Exact pid/start tuples for every non-zombie member of one process group.
/// The watchdog carries these observations forward so it never sends a delayed
/// signal after the recorded group was observed empty or replaced.
fn process_group_live_identities(pgid: u32) -> io::Result<BTreeSet<(u32, u64)>> {
    let mut identities = BTreeSet::new();
    for entry in fs::read_dir("/proc")? {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let Ok(pid) = name.parse::<u32>() else {
            continue;
        };
        let member_group = match process_group_id(pid) {
            Ok(group) => group,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        if member_group != pgid {
            continue;
        }
        let state = match process_state(pid) {
            Ok(state) => state,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        if state != "Z" && state != "X" {
            let start_ticks = match process_start_ticks(pid) {
                Ok(start_ticks) => start_ticks,
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error),
            };
            identities.insert((pid, start_ticks));
        }
    }
    Ok(identities)
}

fn wait_for_process_state(
    pid: u32,
    predicate: impl Fn(&str) -> bool,
    timeout: Duration,
) -> io::Result<bool> {
    let deadline = Instant::now() + timeout;
    loop {
        match process_state(pid) {
            Ok(state) if predicate(&state) => return Ok(true),
            Ok(state) if state == "Z" || state == "X" => return Ok(false),
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error),
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn process_owner(pid: u32) -> Result<ProcessOwner, LandLockError> {
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

/// Check a persisted pid/start-time tuple without treating pid reuse or a
/// zombie awaiting reap as the recorded process. Callers use `Unknown` as a
/// fail-closed state rather than silently turning an inspection error into
/// authority to reclaim the lock.
fn recorded_process_liveness(pid: u32, start_ticks: u64, label: &str) -> OwnerLiveness {
    match process_start_ticks(pid) {
        Ok(current_ticks) if current_ticks != start_ticks => OwnerLiveness::Dead(format!(
            "{label} pid {pid} was reused (recorded start_ticks={start_ticks} current={current_ticks})"
        )),
        Ok(_) => match process_state(pid) {
            Ok(state) if state == "Z" || state == "X" => {
                OwnerLiveness::Dead(format!("{label} pid {pid} is {state}"))
            }
            Ok(_) => OwnerLiveness::Alive,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                OwnerLiveness::Dead(format!("{label} pid {pid} is absent"))
            }
            Err(error) => OwnerLiveness::Unknown(format!(
                "cannot inspect {label} pid {pid} state: {error}"
            )),
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            OwnerLiveness::Dead(format!("{label} pid {pid} is absent"))
        }
        Err(error) => OwnerLiveness::Unknown(format!(
            "cannot inspect {label} pid {pid} identity: {error}"
        )),
    }
}

fn current_process_owner() -> Result<ProcessOwner, LandLockError> {
    process_owner(std::process::id())
}

fn render_liveness(liveness: &OwnerLiveness) -> String {
    match liveness {
        OwnerLiveness::Alive => "alive".into(),
        OwnerLiveness::Dead(reason) => format!("dead:{reason}"),
        OwnerLiveness::Unknown(reason) => format!("unknown:{reason}"),
    }
}

fn render_domain_liveness(liveness: &DomainLiveness) -> String {
    match liveness {
        DomainLiveness::Absent => "absent".into(),
        DomainLiveness::Active => "active".into(),
        DomainLiveness::Empty => "empty".into(),
        DomainLiveness::Unknown(reason) => format!("unknown:{reason}"),
    }
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
    file.sync_all()
        .map_err(|source| io_error("sync", path, source))?;
    sync_parent_directory(path)?;
    Ok(())
}

fn sync_parent_directory(path: &Path) -> Result<(), LandLockError> {
    let parent = path
        .parent()
        .ok_or_else(|| LandLockError::InvalidState(format!("{} has no parent", path.display())))?;
    let directory = File::open(parent).map_err(|source| io_error("open parent", parent, source))?;
    directory
        .sync_all()
        .map_err(|source| io_error("sync parent", parent, source))
}

fn remove_if_exists(path: &Path) -> Result<(), LandLockError> {
    match fs::remove_file(path) {
        Ok(()) => sync_parent_directory(path),
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

fn internal_executable() -> Result<PathBuf, LandLockError> {
    #[cfg(test)]
    {
        let source = PathBuf::from(file!());
        let ci_hub_dir = source.parent().and_then(Path::parent).ok_or_else(|| {
            LandLockError::InvalidState(format!(
                "cannot resolve ci-hub executable from {}",
                source.display()
            ))
        })?;
        return Ok(ci_hub_dir.join("ci-hub"));
    }
    #[cfg(not(test))]
    {
        env::current_exe().map_err(|source| LandLockError::Io {
            action: "resolve internal ci-hub executable from",
            path: PathBuf::from("/proc/self/exe"),
            source,
        })
    }
}

enum WatchdogControl {
    Line(String),
    Eof,
    Error(String),
}

fn spawn_watchdog_control_reader() -> mpsc::Receiver<WatchdogControl> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = BufReader::new(stdin.lock());
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = sender.send(WatchdogControl::Eof);
                    break;
                }
                Ok(_) => {
                    let line = line.trim_end_matches(['\r', '\n']).to_string();
                    if sender.send(WatchdogControl::Line(line)).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.send(WatchdogControl::Error(error.to_string()));
                    break;
                }
            }
        }
    });
    receiver
}

/// Hidden child entry point. The launcher remains the exact process-group
/// leader for the entire arbitrary command and ignores TERM itself, while the
/// payload receives default signals. That anchor prevents the numeric PGID
/// from being recycled between the watchdog's TERM and KILL phases.
fn run_payload_launcher(args: &PayloadArgs) -> Result<i32, LandLockError> {
    if args.child.is_empty() {
        return Err(LandLockError::EmptyChild);
    }
    let pid = std::process::id();
    let pgid = process_group_id(pid).map_err(|source| LandLockError::Io {
        action: "read payload launcher process group from",
        path: PathBuf::from(format!("/proc/{pid}/stat")),
        source,
    })?;
    if pgid != pid {
        return Err(LandLockError::InvalidState(format!(
            "payload launcher pid {pid} is not its process-group leader ({pgid})"
        )));
    }
    // The parent resumes this launcher only after the watchdog identity and
    // deadline are durable and ARMED. Before that point PDEATHSIG closes every
    // supervisor-death startup race; afterwards the watchdog owns cleanup.
    unsafe {
        if libc::prctl(libc::PR_SET_PDEATHSIG, 0) != 0 {
            return Err(LandLockError::Io {
                action: "clear payload launcher parent-death signal from",
                path: PathBuf::from("/proc/self/status"),
                source: io::Error::last_os_error(),
            });
        }
        for signal in [libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
            if libc::signal(signal, libc::SIG_IGN) == libc::SIG_ERR {
                return Err(LandLockError::Io {
                    action: "install payload launcher anchor signal from",
                    path: PathBuf::from("/proc/self/status"),
                    source: io::Error::last_os_error(),
                });
            }
        }
    }

    let mut command = Command::new(&args.child[0]);
    command.args(&args.child[1..]);
    // SAFETY: after fork and before exec, only async-signal-safe libc signal
    // disposition changes are made. The payload must not inherit the anchor's
    // ignored signals.
    unsafe {
        command.pre_exec(|| {
            for signal in [libc::SIGTERM, libc::SIGHUP, libc::SIGINT] {
                if libc::signal(signal, libc::SIG_DFL) == libc::SIG_ERR {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    let mut child = command.spawn().map_err(|source| LandLockError::Io {
        action: "launch payload child from",
        path: PathBuf::from(&args.child[0]),
        source,
    })?;
    let status = child.wait().map_err(|source| LandLockError::Io {
        action: "wait for payload child from",
        path: PathBuf::from(format!("/proc/{}", child.id())),
        source,
    })?;

    // A daemonized descendant remains in this group. Keep the exact leader
    // alive as an anchor until the descendant exits naturally or the external
    // watchdog enforces the deadline.
    loop {
        let mut members =
            process_group_live_identities(pgid).map_err(|source| LandLockError::Io {
                action: "scan anchored payload process group from",
                path: PathBuf::from("/proc"),
                source,
            })?;
        members.retain(|(member_pid, _)| *member_pid != pid);
        if members.is_empty() {
            return Ok(exit_status_code(status));
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// Hidden watchdog entry point. READY proves it has observed the exact stopped
/// group leader; ARMED is emitted only after the parent fsyncs the watchdog
/// identity into `.domain`. Once armed, either supervisor EOF/read failure or
/// the absolute deadline triggers immediate TERM/grace/KILL cleanup.
fn run_domain_watchdog(lock: &LandingLock, args: &WatchdogArgs) -> Result<i32, LandLockError> {
    if args.pgid != args.leader_pid {
        return Err(LandLockError::InvalidState(format!(
            "watchdog leader {} does not equal process group {}",
            args.leader_pid, args.pgid
        )));
    }
    let mut previous =
        process_group_live_identities(args.pgid).map_err(|source| LandLockError::Io {
            action: "scan initial watchdog process group from",
            path: PathBuf::from("/proc"),
            source,
        })?;
    if !previous.contains(&(args.leader_pid, args.leader_start_ticks)) {
        return Err(LandLockError::InvalidState(
            "watchdog did not observe the exact recorded group leader".into(),
        ));
    }
    println!("READY");
    io::stdout().flush().map_err(|source| LandLockError::Io {
        action: "publish watchdog readiness through",
        path: PathBuf::from("/proc/self/fd/1"),
        source,
    })?;

    let controls = spawn_watchdog_control_reader();
    match controls.recv_timeout(Duration::from_secs(CHILD_STARTUP_SECONDS)) {
        Ok(WatchdogControl::Line(line)) if line == "armed" => {}
        Ok(WatchdogControl::Eof) | Err(mpsc::RecvTimeoutError::Disconnected) => return Ok(0),
        Ok(WatchdogControl::Line(line)) => {
            return Err(LandLockError::InvalidState(format!(
                "watchdog received {line:?} before it was armed"
            )));
        }
        Ok(WatchdogControl::Error(reason)) => {
            return Err(LandLockError::InvalidState(format!(
                "watchdog control failed before arming: {reason}"
            )));
        }
        Err(mpsc::RecvTimeoutError::Timeout) => return Ok(0),
    }
    lock.assert_watchdog_authority(args)?;
    println!("ARMED");
    io::stdout().flush().map_err(|source| LandLockError::Io {
        action: "publish watchdog arming through",
        path: PathBuf::from("/proc/self/fd/1"),
        source,
    })?;

    let remaining = args.deadline_at.saturating_sub(epoch_seconds()?).max(0) as u64;
    let deadline = Instant::now()
        .checked_add(Duration::from_secs(remaining))
        .ok_or_else(|| LandLockError::InvalidState("watchdog deadline overflows Instant".into()))?;
    let mut payload_observed_empty = false;
    loop {
        let control = match controls.recv_timeout(Duration::from_millis(50)) {
            Ok(control) => Some(control),
            Err(mpsc::RecvTimeoutError::Timeout) => None,
            Err(mpsc::RecvTimeoutError::Disconnected) => Some(WatchdogControl::Eof),
        };

        if !payload_observed_empty {
            let current =
                process_group_live_identities(args.pgid).map_err(|source| LandLockError::Io {
                    action: "scan armed watchdog process group from",
                    path: PathBuf::from("/proc"),
                    source,
                })?;
            if current.is_empty() {
                payload_observed_empty = true;
            } else if previous.is_disjoint(&current) {
                return Err(LandLockError::InvalidState(
                    "watchdog process-group identity continuity was lost".into(),
                ));
            } else {
                previous = current;
            }
        }

        match control {
            Some(WatchdogControl::Line(line)) if line == "done" => {
                if payload_observed_empty {
                    return Ok(0);
                }
                terminate_watchdog_domain(args.pgid, &mut previous)?;
                return Err(LandLockError::InvalidState(
                    "watchdog received done while payload group was still active".into(),
                ));
            }
            Some(WatchdogControl::Line(line)) => {
                if !payload_observed_empty {
                    terminate_watchdog_domain(args.pgid, &mut previous)?;
                }
                return Err(LandLockError::InvalidState(format!(
                    "watchdog received unexpected control {line:?}"
                )));
            }
            Some(WatchdogControl::Eof) => {
                if payload_observed_empty {
                    return Ok(0);
                }
                terminate_watchdog_domain(args.pgid, &mut previous)?;
                return Ok(0);
            }
            Some(WatchdogControl::Error(reason)) => {
                if !payload_observed_empty {
                    terminate_watchdog_domain(args.pgid, &mut previous)?;
                }
                return Err(LandLockError::InvalidState(format!(
                    "watchdog control failed after arming: {reason}"
                )));
            }
            None => {}
        }

        if Instant::now() >= deadline {
            if payload_observed_empty {
                return Ok(0);
            }
            terminate_watchdog_domain(args.pgid, &mut previous)?;
            return Ok(0);
        }
    }
}

fn terminate_watchdog_domain(
    pgid: u32,
    previous: &mut BTreeSet<(u32, u64)>,
) -> Result<(), LandLockError> {
    signal_process_group(pgid, libc::SIGTERM)?;
    let term_deadline = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    loop {
        let current = process_group_live_identities(pgid).map_err(|source| LandLockError::Io {
            action: "scan watchdog TERM process group from",
            path: PathBuf::from("/proc"),
            source,
        })?;
        if current.is_empty() {
            return Ok(());
        }
        if previous.is_disjoint(&current) {
            return Err(LandLockError::InvalidState(
                "watchdog refused to signal a replaced process group after TERM".into(),
            ));
        }
        *previous = current;
        if Instant::now() >= term_deadline {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }

    signal_process_group(pgid, libc::SIGKILL)?;
    let kill_deadline = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    loop {
        let current = process_group_live_identities(pgid).map_err(|source| LandLockError::Io {
            action: "scan watchdog KILL process group from",
            path: PathBuf::from("/proc"),
            source,
        })?;
        if current.is_empty() || previous.is_disjoint(&current) {
            return Ok(());
        }
        *previous = current;
        if Instant::now() >= kill_deadline {
            return Err(LandLockError::DomainActive(format!(
                "watchdog could not empty process group {pgid}"
            )));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn signal_process_group(pgid: u32, signal: i32) -> Result<(), LandLockError> {
    let pgid = i32::try_from(pgid).map_err(|_| {
        LandLockError::InvalidState(format!("process group {pgid} exceeds signed pid range"))
    })?;
    let result = unsafe { libc::kill(-pgid, signal) };
    if result == 0 {
        return Ok(());
    }
    let source = io::Error::last_os_error();
    if source.raw_os_error() == Some(libc::ESRCH) {
        return Ok(());
    }
    Err(LandLockError::Io {
        action: "signal watchdog process group from",
        path: PathBuf::from("/proc"),
        source,
    })
}

/// A lifetime child of the canonical `run` supervisor that can break a flock
/// held by a stopped or deadlocked supervisor. It opens a pidfd for its actual
/// parent before reporting READY; caller-supplied PIDs therefore cannot turn
/// the hidden command into a kill primitive. Every phase update is pipe-atomic
/// and acknowledged before the supervisor enters the bounded phase.
struct SupervisorWatchdog {
    child: Child,
    input: Option<ChildStdin>,
    acknowledgements: mpsc::Receiver<Result<String, String>>,
}

impl SupervisorWatchdog {
    fn spawn() -> Result<Self, LandLockError> {
        let supervisor = current_process_owner()?;
        let executable = internal_executable()?;
        let mut child = Command::new(&executable)
            .args([
                "land-lock",
                "supervisor-watchdog",
                "--supervisor-start-ticks",
                &supervisor.start_ticks.to_string(),
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .map_err(|source| LandLockError::Io {
                action: "launch supervisor watchdog from",
                path: executable,
                source,
            })?;
        let input = child.stdin.take().ok_or_else(|| {
            LandLockError::InvalidState("supervisor watchdog has no control pipe".into())
        })?;
        let output = child.stdout.take().ok_or_else(|| {
            LandLockError::InvalidState("supervisor watchdog has no acknowledgement pipe".into())
        })?;
        let acknowledgements = spawn_line_reader(output);
        let mut watchdog = Self {
            child,
            input: Some(input),
            acknowledgements,
        };
        if let Err(error) = watchdog.expect_acknowledgement("READY") {
            watchdog.cancel();
            return Err(error);
        }
        Ok(watchdog)
    }

    fn arm_for(&mut self, seconds: u64) -> Result<(), LandLockError> {
        if seconds == 0 {
            return Err(LandLockError::InvalidState(
                "supervisor watchdog phase must be bounded".into(),
            ));
        }
        let input = self.input.as_mut().ok_or_else(|| {
            LandLockError::InvalidState("supervisor watchdog control pipe is closed".into())
        })?;
        let command = format!("arm {seconds}\n");
        input
            .write_all(command.as_bytes())
            .map_err(|source| LandLockError::Io {
                action: "arm supervisor watchdog through",
                path: PathBuf::from(format!("/proc/{}/fd/0", self.child.id())),
                source,
            })?;
        self.expect_acknowledgement(&format!("ARMED {seconds}"))
    }

    fn idle(&mut self) -> Result<(), LandLockError> {
        let input = self.input.as_mut().ok_or_else(|| {
            LandLockError::InvalidState("supervisor watchdog control pipe is closed".into())
        })?;
        input
            .write_all(b"idle\n")
            .map_err(|source| LandLockError::Io {
                action: "idle supervisor watchdog through",
                path: PathBuf::from(format!("/proc/{}/fd/0", self.child.id())),
                source,
            })?;
        self.expect_acknowledgement("IDLE")
    }

    fn is_alive(&mut self) -> Result<bool, LandLockError> {
        self.child
            .try_wait()
            .map(|status| status.is_none())
            .map_err(|source| LandLockError::Io {
                action: "observe supervisor watchdog from",
                path: PathBuf::from(format!("/proc/{}", self.child.id())),
                source,
            })
    }

    fn expect_acknowledgement(&mut self, expected: &str) -> Result<(), LandLockError> {
        match self
            .acknowledgements
            .recv_timeout(Duration::from_secs(CHILD_STARTUP_SECONDS))
        {
            Ok(Ok(observed)) if observed == expected => Ok(()),
            Ok(Ok(observed)) => Err(LandLockError::InvalidState(format!(
                "supervisor watchdog emitted {observed:?}, expected {expected:?}"
            ))),
            Ok(Err(reason)) => Err(LandLockError::InvalidState(format!(
                "supervisor watchdog acknowledgement failed: {reason}"
            ))),
            Err(mpsc::RecvTimeoutError::Timeout) => Err(LandLockError::InvalidState(format!(
                "supervisor watchdog did not emit {expected} before startup deadline"
            ))),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(LandLockError::InvalidState(format!(
                "supervisor watchdog closed before emitting {expected}"
            ))),
        }
    }

    fn finish(&mut self) -> Result<(), LandLockError> {
        let input = self.input.as_mut().ok_or_else(|| {
            LandLockError::InvalidState("supervisor watchdog control pipe is closed".into())
        })?;
        input
            .write_all(b"done\n")
            .map_err(|source| LandLockError::Io {
                action: "stop supervisor watchdog through",
                path: PathBuf::from(format!("/proc/{}/fd/0", self.child.id())),
                source,
            })?;
        self.expect_acknowledgement("DONE")?;
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(WATCHDOG_SHUTDOWN_SECONDS);
        loop {
            match self.child.try_wait() {
                Ok(Some(status)) if status.success() => return Ok(()),
                Ok(Some(status)) => {
                    return Err(LandLockError::InvalidState(format!(
                        "supervisor watchdog rejected clean shutdown: {status}"
                    )))
                }
                Ok(None) => {}
                Err(source) => {
                    return Err(LandLockError::Io {
                        action: "reap supervisor watchdog from",
                        path: PathBuf::from(format!("/proc/{}", self.child.id())),
                        source,
                    })
                }
            }
            if Instant::now() >= deadline {
                self.cancel();
                return Err(LandLockError::InvalidState(
                    "supervisor watchdog did not exit after DONE".into(),
                ));
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn cancel(&mut self) {
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(WATCHDOG_SHUTDOWN_SECONDS);
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) | Err(_) => thread::sleep(Duration::from_millis(10)),
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for SupervisorWatchdog {
    fn drop(&mut self) {
        if self.input.is_some() {
            self.cancel();
        }
    }
}

fn open_parent_pidfd(pid: u32) -> Result<File, LandLockError> {
    // SAFETY: pidfd_open does not dereference userspace pointers. The returned
    // fd owns one exact process identity and is wrapped immediately in File.
    let raw = unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0u32) };
    if raw < 0 {
        return Err(LandLockError::Io {
            action: "open exact supervisor pidfd from",
            path: PathBuf::from(format!("/proc/{pid}")),
            source: io::Error::last_os_error(),
        });
    }
    // SAFETY: successful pidfd_open returns a new owned fd.
    Ok(unsafe { File::from_raw_fd(raw as i32) })
}

fn pidfd_sigkill(pidfd: &File, pid: u32) -> Result<(), LandLockError> {
    // SAFETY: pidfd_send_signal receives a valid owned pidfd, a fixed signal,
    // no siginfo pointer, and zero flags.
    let result = unsafe {
        libc::syscall(
            libc::SYS_pidfd_send_signal,
            pidfd.as_raw_fd(),
            libc::SIGKILL,
            std::ptr::null::<libc::siginfo_t>(),
            0u32,
        )
    };
    if result == 0 {
        return Ok(());
    }
    let source = io::Error::last_os_error();
    if source.raw_os_error() == Some(libc::ESRCH) {
        return Ok(());
    }
    Err(LandLockError::Io {
        action: "signal exact supervisor pidfd from",
        path: PathBuf::from(format!("/proc/{pid}")),
        source,
    })
}

fn run_supervisor_watchdog(args: &SupervisorWatchdogArgs) -> Result<i32, LandLockError> {
    let parent_pid = unsafe { libc::getppid() };
    if parent_pid <= 1 {
        return Err(LandLockError::InvalidState(
            "supervisor watchdog has no live direct parent".into(),
        ));
    }
    let parent_pid = parent_pid as u32;
    let pidfd = open_parent_pidfd(parent_pid)?;
    if unsafe { libc::getppid() } != parent_pid as libc::pid_t {
        return Err(LandLockError::InvalidState(
            "supervisor watchdog parent changed while opening pidfd".into(),
        ));
    }
    let parent = process_owner(parent_pid)?;
    if parent.start_ticks != args.supervisor_start_ticks {
        return Err(LandLockError::InvalidState(
            "supervisor watchdog parent start time does not match launcher".into(),
        ));
    }
    if unsafe { libc::getppid() } != parent_pid as libc::pid_t {
        return Err(LandLockError::InvalidState(
            "supervisor watchdog parent changed while verifying start time".into(),
        ));
    }
    let self_pid = std::process::id();
    if process_group_id(self_pid).map_err(|source| LandLockError::Io {
        action: "read supervisor watchdog process group from",
        path: PathBuf::from(format!("/proc/{self_pid}/stat")),
        source,
    })? != self_pid
    {
        return Err(LandLockError::InvalidState(
            "supervisor watchdog is not isolated in its own process group".into(),
        ));
    }
    // READY itself carries a short bound. There is no unarmed interval in
    // which a stopped parent can retain a flock or FIFO position forever.
    let mut deadline = Instant::now()
        .checked_add(Duration::from_secs(
            CHILD_STARTUP_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS,
        ))
        .ok_or_else(|| {
            LandLockError::InvalidState("supervisor startup phase overflows Instant".into())
        })?;
    println!("READY");
    io::stdout().flush().map_err(|source| LandLockError::Io {
        action: "publish supervisor watchdog readiness through",
        path: PathBuf::from("/proc/self/fd/1"),
        source,
    })?;

    let controls = spawn_watchdog_control_reader();
    loop {
        let control = match controls.recv_timeout(Duration::from_millis(50)) {
            Ok(control) => Some(control),
            Err(mpsc::RecvTimeoutError::Timeout) => None,
            Err(mpsc::RecvTimeoutError::Disconnected) => Some(WatchdogControl::Eof),
        };
        match control {
            Some(WatchdogControl::Line(line)) if line == "idle" => {
                // IDLE means no guard is intentionally held, but the process
                // may still own the FIFO head. Keep it bounded through one
                // poll interval rather than creating an unarmed state.
                deadline = Instant::now()
                    .checked_add(Duration::from_secs(
                        POLL_SECONDS + SUPERVISOR_PHASE_MARGIN_SECONDS,
                    ))
                    .ok_or_else(|| {
                        LandLockError::InvalidState(
                            "supervisor idle phase overflows Instant".into(),
                        )
                    })?;
                println!("IDLE");
                io::stdout().flush().map_err(|source| LandLockError::Io {
                    action: "acknowledge supervisor watchdog idle through",
                    path: PathBuf::from("/proc/self/fd/1"),
                    source,
                })?;
            }
            Some(WatchdogControl::Line(line)) if line == "done" => {
                println!("DONE");
                io::stdout().flush().map_err(|source| LandLockError::Io {
                    action: "acknowledge supervisor watchdog shutdown through",
                    path: PathBuf::from("/proc/self/fd/1"),
                    source,
                })?;
                return Ok(0);
            }
            Some(WatchdogControl::Line(line)) if line.starts_with("arm ") => {
                let seconds = line[4..].parse::<u64>().map_err(|_| {
                    LandLockError::InvalidState(
                        "supervisor watchdog arm is not an unsigned duration".into(),
                    )
                })?;
                if seconds == 0 {
                    return Err(LandLockError::InvalidState(
                        "supervisor watchdog refused an unbounded phase".into(),
                    ));
                }
                deadline = Instant::now()
                    .checked_add(Duration::from_secs(seconds))
                    .ok_or_else(|| {
                        LandLockError::InvalidState(
                            "supervisor watchdog phase overflows Instant".into(),
                        )
                    })?;
                println!("ARMED {seconds}");
                io::stdout().flush().map_err(|source| LandLockError::Io {
                    action: "acknowledge supervisor watchdog arm through",
                    path: PathBuf::from("/proc/self/fd/1"),
                    source,
                })?;
            }
            Some(WatchdogControl::Line(line)) => {
                return Err(LandLockError::InvalidState(format!(
                    "supervisor watchdog received unexpected control {line:?}"
                )))
            }
            Some(WatchdogControl::Eof) => return Ok(0),
            Some(WatchdogControl::Error(reason)) => {
                return Err(LandLockError::InvalidState(format!(
                    "supervisor watchdog control failed: {reason}"
                )))
            }
            None => {}
        }
        if Instant::now() >= deadline {
            pidfd_sigkill(&pidfd, parent_pid)?;
            return Ok(0);
        }
    }
}

/// A sibling process that survives an exact supervisor death and enforces the
/// persisted child-domain deadline. Its first pipe message arms cleanup only
/// after the exact watchdog identity has been fsynced into `.domain`; EOF before
/// that message is deliberately inert. Its second message is either `done`
/// during orderly shutdown or EOF after supervisor death.
struct DomainWatchdog {
    child: Child,
    input: Option<ChildStdin>,
    acknowledgements: mpsc::Receiver<Result<String, String>>,
    owner: ProcessOwner,
    deadline_at: i64,
}

impl DomainWatchdog {
    fn spawn(
        pgid: u32,
        leader_pid: u32,
        leader_start_ticks: u64,
        deadline_at: i64,
        lock_path: &Path,
    ) -> Result<Self, LandLockError> {
        let executable = internal_executable()?;
        let mut child = Command::new(&executable)
            .args([
                "land-lock",
                "watchdog",
                "--pgid",
                &pgid.to_string(),
                "--leader-pid",
                &leader_pid.to_string(),
                "--leader-start-ticks",
                &leader_start_ticks.to_string(),
                "--deadline-at",
                &deadline_at.to_string(),
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env("CI_HUB_LANDING_LOCK", lock_path)
            .process_group(0)
            .spawn()
            .map_err(|source| LandLockError::Io {
                action: "launch deadline watchdog from",
                path: executable,
                source,
            })?;
        let input = child.stdin.take().ok_or_else(|| {
            LandLockError::InvalidState("deadline watchdog has no control pipe".into())
        })?;
        let output = child.stdout.take().ok_or_else(|| {
            LandLockError::InvalidState("deadline watchdog has no acknowledgement pipe".into())
        })?;
        let acknowledgements = spawn_line_reader(output);
        let owner = match process_owner(child.id()) {
            Ok(owner) => owner,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        let mut watchdog = Self {
            child,
            input: Some(input),
            acknowledgements,
            owner,
            deadline_at,
        };
        if let Err(error) = watchdog.expect_acknowledgement("READY") {
            watchdog.cancel_unarmed();
            return Err(error);
        }
        Ok(watchdog)
    }

    fn arm(&mut self) -> Result<(), LandLockError> {
        let input = self.input.as_mut().ok_or_else(|| {
            LandLockError::InvalidState("deadline watchdog control pipe is closed".into())
        })?;
        // One short pipe write is atomic: a reported failure cannot leave a
        // truncated `armed` token that the watchdog mistakes for authority.
        input
            .write_all(b"armed\n")
            .map_err(|source| LandLockError::Io {
                action: "arm deadline watchdog through",
                path: PathBuf::from(format!("/proc/{}/fd/0", self.child.id())),
                source,
            })?;
        self.expect_acknowledgement("ARMED")
    }

    fn expect_acknowledgement(&mut self, expected: &str) -> Result<(), LandLockError> {
        match self
            .acknowledgements
            .recv_timeout(Duration::from_secs(CHILD_STARTUP_SECONDS))
        {
            Ok(Ok(observed)) if observed == expected => Ok(()),
            Ok(Ok(observed)) => Err(LandLockError::InvalidState(format!(
                "deadline watchdog emitted {observed:?}, expected {expected:?}"
            ))),
            Ok(Err(reason)) => Err(LandLockError::InvalidState(format!(
                "deadline watchdog acknowledgement failed: {reason}"
            ))),
            Err(mpsc::RecvTimeoutError::Timeout) => Err(LandLockError::InvalidState(format!(
                "deadline watchdog did not emit {expected} before startup deadline"
            ))),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(LandLockError::InvalidState(format!(
                "deadline watchdog closed before emitting {expected}"
            ))),
        }
    }

    fn is_alive(&mut self) -> Result<bool, LandLockError> {
        self.child
            .try_wait()
            .map(|status| status.is_none())
            .map_err(|source| LandLockError::Io {
                action: "observe deadline watchdog from",
                path: PathBuf::from(format!("/proc/{}", self.child.id())),
                source,
            })
    }

    /// Close an unarmed watchdog. This is used only before `arm` succeeds, so
    /// EOF cannot authorize a group signal.
    fn cancel_unarmed(&mut self) {
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(WATCHDOG_SHUTDOWN_SECONDS);
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) | Err(_) => thread::sleep(Duration::from_millis(10)),
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    /// Deliver EOF to an armed watchdog and leave it alive to enforce the
    /// persisted deadline. The `.domain` record keeps reacquisition blocked
    /// until both this exact pid/start tuple and the payload group are gone.
    fn trigger_cleanup(&mut self) {
        self.input.take();
    }

    fn finish(&mut self) -> Result<(), LandLockError> {
        let write_result = match self.input.as_mut() {
            Some(input) => input.write_all(b"done\n"),
            None => Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "deadline watchdog control pipe is closed",
            )),
        };
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(WATCHDOG_SHUTDOWN_SECONDS);
        let status = loop {
            match self.child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) => {}
                Err(source) => {
                    return Err(LandLockError::Io {
                        action: "reap deadline watchdog from",
                        path: PathBuf::from(format!("/proc/{}", self.child.id())),
                        source,
                    });
                }
            }
            if Instant::now() >= deadline {
                let _ = self.child.kill();
                let _ = self.child.wait();
                return Err(LandLockError::InvalidState(
                    "deadline watchdog did not acknowledge clean shutdown".into(),
                ));
            }
            thread::sleep(Duration::from_millis(10));
        };
        if !status.success() {
            if let Err(source) = write_result {
                return Err(LandLockError::Io {
                    action: "stop failed deadline watchdog through",
                    path: PathBuf::from(format!("/proc/{}/fd/0", self.child.id())),
                    source,
                });
            }
            return Err(LandLockError::InvalidState(format!(
                "deadline watchdog rejected clean shutdown: {status}"
            )));
        }
        // The caller proves the payload group empty before invoking `finish`.
        // A successful watchdog may already have exited after observing that
        // emptiness or enforcing the same absolute deadline, so EPIPE on the
        // best-effort `done` acknowledgement is not an ambiguous live domain.
        Ok(())
    }
}

fn spawn_line_reader(output: ChildStdout) -> mpsc::Receiver<Result<String, String>> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut reader = BufReader::new(output);
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = sender.send(Err("acknowledgement pipe reached EOF".into()));
                    break;
                }
                Ok(_) => {
                    let line = line.trim_end_matches(['\r', '\n']).to_string();
                    if sender.send(Ok(line)).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(error.to_string()));
                    break;
                }
            }
        }
    });
    receiver
}

/// How a supervised `run` child ended.
enum ChildOutcome {
    /// The child exited on its own with this status.
    Exited(ExitStatus),
    /// The child exceeded its deadline and was killed.
    TimedOut,
    /// Lease renewal failed, so the child domain was killed before the lease
    /// could become a stale authorization proxy.
    HeartbeatFailed,
}

/// Wait for `child`, killing it if it runs longer than `deadline_secs`.
///
/// The child subtree is signalled SIGTERM, given a short grace period, then
/// SIGKILLed, and the child is reaped so no zombie is left behind. Callers reject
/// zero before acquiring the lock so the pre-guardrail unbounded behavior cannot
/// be re-enabled.
fn supervise_child(
    child: &mut Child,
    deadline_secs: u64,
    pr: &str,
    heartbeat_failed: &AtomicBool,
    watchdog: &mut DomainWatchdog,
    supervisor_watchdog: &mut SupervisorWatchdog,
) -> Result<ChildOutcome, LandLockError> {
    let deadline = Instant::now() + Duration::from_secs(deadline_secs);
    loop {
        if !supervisor_watchdog.is_alive()? {
            if !terminate_child_group(child, pr) {
                return Err(LandLockError::DomainActive(format!(
                    "supervisor watchdog exited and process group {} could not be emptied",
                    child.id()
                )));
            }
            return Err(LandLockError::InvalidState(
                "supervisor watchdog exited before canonical state was released".into(),
            ));
        }
        if heartbeat_failed.load(Ordering::Acquire) {
            eprintln!(
                "landing-lock: lease heartbeat failed for PR #{pr}; terminating supervised domain"
            );
            if !terminate_child_group(child, pr) {
                return Err(LandLockError::DomainActive(format!(
                    "heartbeat failed and process group {} could not be emptied",
                    child.id()
                )));
            }
            return Ok(ChildOutcome::HeartbeatFailed);
        }
        match process_state(child.id()) {
            Ok(state) if state == "Z" || state == "X" => {
                match process_group_has_live_members(child.id()) {
                    Ok(false) => {
                        let status = child.wait().map_err(|source| LandLockError::Io {
                            action: "reap supervised child from",
                            path: PathBuf::from(format!("/proc/{}", child.id())),
                            source,
                        })?;
                        if epoch_seconds()? >= watchdog.deadline_at {
                            return Ok(ChildOutcome::TimedOut);
                        }
                        return Ok(ChildOutcome::Exited(status));
                    }
                    Ok(true) => {
                        eprintln!(
                            "landing-lock: direct child exited for PR #{pr}, but its process group still has live members; terminating them"
                        );
                        let (emptied, status) = terminate_child_group_with_status(child, pr);
                        if !emptied {
                            return Err(LandLockError::DomainActive(format!(
                                "process group {} remained active after direct-child exit",
                                child.id()
                            )));
                        }
                        if epoch_seconds()? >= watchdog.deadline_at {
                            return Ok(ChildOutcome::TimedOut);
                        }
                        return status.map(ChildOutcome::Exited).ok_or_else(|| {
                            LandLockError::InvalidState(
                                "emptied payload group but could not reap its leader".into(),
                            )
                        });
                    }
                    Err(error) => {
                        return Err(LandLockError::DomainActive(format!(
                            "cannot verify process group {} after direct-child exit: {error}",
                            child.id()
                        )));
                    }
                }
            }
            Ok(_) => {}
            Err(source) if source.kind() == io::ErrorKind::NotFound => {
                return Err(LandLockError::InvalidState(format!(
                    "supervised child {} disappeared before it could be reaped",
                    child.id()
                )));
            }
            Err(source) => {
                return Err(LandLockError::Io {
                    action: "observe supervised child from",
                    path: PathBuf::from(format!("/proc/{}/stat", child.id())),
                    source,
                });
            }
        }
        if !watchdog.is_alive()? {
            let watchdog_deadline_reached = epoch_seconds()? >= watchdog.deadline_at;
            if !terminate_child_group(child, pr) {
                return Err(LandLockError::DomainActive(format!(
                    "deadline watchdog exited and process group {} could not be emptied",
                    child.id()
                )));
            }
            if watchdog_deadline_reached {
                return Ok(ChildOutcome::TimedOut);
            }
            return Err(LandLockError::InvalidState(
                "deadline watchdog exited before the payload domain was empty".into(),
            ));
        }
        if Instant::now() >= deadline {
            if !terminate_child_group(child, pr) {
                return Err(LandLockError::DomainActive(format!(
                    "deadline expired and process group {} could not be emptied",
                    child.id()
                )));
            }
            return Ok(ChildOutcome::TimedOut);
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// SIGTERM then (after a grace period) SIGKILL the child's process group, and
/// reap the direct child. The child was spawned with `process_group(0)`, so its
/// pid equals its pgid and signalling `-pid` reaches the whole land subtree
/// (gh / git / cargo), not just the wrapper shell. `/proc` group scans, rather
/// than direct-child exit, decide when the executable domain is empty.
fn terminate_child_group(child: &mut Child, pr: &str) -> bool {
    terminate_child_group_with_status(child, pr).0
}

fn terminate_child_group_with_status(child: &mut Child, pr: &str) -> (bool, Option<ExitStatus>) {
    let group = format!("-{}", child.id());
    eprintln!("landing-lock: terminating PR #{pr}; SIGTERM process group {group}");
    signal_group("TERM", &group);
    let grace = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < grace {
        match process_group_has_live_members(child.id()) {
            Ok(false) => {
                return (true, child.wait().ok());
            }
            Ok(true) | Err(_) => {}
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
    eprintln!("landing-lock: grace expired for PR #{pr}; SIGKILL process group {group}");
    signal_group("KILL", &group);
    let killed_deadline = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < killed_deadline {
        match process_group_has_live_members(child.id()) {
            Ok(false) => {
                return (true, child.wait().ok());
            }
            Ok(true) | Err(_) => {}
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
    if matches!(process_group_has_live_members(child.id()), Ok(false)) {
        (true, child.wait().ok())
    } else {
        (false, None)
    }
}

/// Send `signal` to the process `group` (a negative pid string) via `/bin/kill`.
pub(crate) fn signal_group(signal: &str, group: &str) {
    let _ = signal_group_succeeds(signal, group);
}

fn signal_group_succeeds(signal: &str, group: &str) -> bool {
    Command::new("kill")
        .arg(format!("-{signal}"))
        .arg(group)
        .status()
        .is_ok_and(|status| status.success())
}

pub(crate) fn exit_status_code(status: ExitStatus) -> i32 {
    status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_paths(name: &str) -> LockPaths {
        let root = env::temp_dir().join(format!(
            "ci-hub-landing-lock-{name}-{}-{}",
            std::process::id(),
            epoch_seconds().unwrap()
        ));
        fs::create_dir_all(&root).unwrap();
        let lock = root.join(".landing-lock");
        LockPaths {
            guard: suffix(&lock, ".guard"),
            queue: suffix(&lock, ".queue"),
            owner: suffix(&lock, ".owner"),
            domain: suffix(&lock, ".domain"),
            lock,
        }
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
    fn repository_holder_round_trips_without_changing_legacy_format() {
        let holder = LockState {
            agent: "hermit-lander".into(),
            repo: Some("rrnewton/hermit".into()),
            operation: Some("deadbeef".into()),
            pending_mutation: Some("deadbeef".into()),
            pending_attempt: Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into()),
            pending_call_count: Some(0),
            pending_call_id: None,
            pr: "1628".into(),
            host: "testhost".into(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: None,
        };
        let rendered = "agent=hermit-lander\nrepo=rrnewton/hermit\noperation=deadbeef\npending_mutation=deadbeef\npending_attempt=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\npending_call_count=0\npr=1628\nhost=testhost\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\n";
        assert_eq!(holder.render(), rendered);
        assert_eq!(LockState::parse(rendered).unwrap(), holder);
    }

    #[test]
    fn holder_refuses_one_sided_or_misbound_pending_attempt() {
        let base = "agent=hermit-lander\nrepo=rrnewton/hermit\noperation=deadbeef\npr=1628\nhost=testhost\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\n";
        for planted in [
            format!("pending_mutation=deadbeef\n{base}"),
            format!("pending_attempt={}\n{base}", "a".repeat(32)),
            format!(
                "pending_mutation=cafebabe\npending_attempt={}\n{base}",
                "a".repeat(32)
            ),
            format!("pending_mutation=deadbeef\npending_attempt=not-hex\n{base}"),
        ] {
            assert!(LockState::parse(&planted).is_err());
        }
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
    fn process_domain_sidecar_round_trips() {
        let domain = ProcessDomain {
            host: "testhost".into(),
            boot_id: "boot-1".into(),
            leader_pid: 43,
            leader_start_ticks: 654_321,
            pgid: 43,
            deadline_at: 1_785_000_000,
            watchdog_pid: 44,
            watchdog_start_ticks: 777_888,
        };
        let rendered = "host=testhost\nboot_id=boot-1\nleader_pid=43\nleader_start_ticks=654321\npgid=43\ndeadline_at=1785000000\nwatchdog_pid=44\nwatchdog_start_ticks=777888\n";
        assert_eq!(domain.render(), rendered);
        assert_eq!(ProcessDomain::parse(rendered).unwrap(), domain);
    }

    fn install_assert_child_fixture(lock: &LandingLock, args: &AssertChildArgs) -> ProcessOwner {
        let supervisor_pid = process_parent_pid(args.child_pid).unwrap();
        let supervisor = process_owner(supervisor_pid).unwrap();
        let mut holder = new_holder(&args.agent, &args.pr, 60, None).unwrap();
        holder.repo = Some(args.repo.clone());
        holder.operation = args.operation.clone();
        lock.write_holder(&holder).unwrap();
        write_truncated(&lock.paths.owner, supervisor.render().as_bytes()).unwrap();
        let domain = ProcessDomain {
            host: current_host(),
            boot_id: current_boot_id().unwrap(),
            leader_pid: args.child_pid,
            leader_start_ticks: process_start_ticks(args.child_pid).unwrap(),
            pgid: process_group_id(args.child_pid).unwrap(),
            deadline_at: epoch_seconds().unwrap() + 60,
            watchdog_pid: std::process::id(),
            watchdog_start_ticks: process_start_ticks(std::process::id()).unwrap(),
        };
        write_truncated(&lock.paths.domain, domain.render().as_bytes()).unwrap();
        supervisor
    }

    fn assert_child_args() -> AssertChildArgs {
        AssertChildArgs {
            agent: "hermit-lander".into(),
            repo: "rrnewton/hermit".into(),
            pr: "1628".into(),
            operation: Some("deadbeef".into()),
            child_pid: process_parent_pid(std::process::id()).unwrap(),
        }
    }

    #[test]
    fn exact_live_supervised_child_is_authorized() {
        let paths = temp_paths("assert-child-positive");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let args = assert_child_args();
        let verifier_pid = std::process::id();
        let supervisor = install_assert_child_fixture(&lock, &args);

        assert_eq!(
            lock.assert_child_process(&args, verifier_pid).unwrap(),
            (supervisor.pid, 1, 1, None, None, None, None)
        );
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn assert_child_refuses_wrong_agent_repository_pr_and_host() {
        let paths = temp_paths("assert-child-identities");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let args = assert_child_args();
        let verifier_pid = std::process::id();
        install_assert_child_fixture(&lock, &args);

        for wrong in [
            AssertChildArgs {
                agent: "attacker".into(),
                ..args.clone()
            },
            AssertChildArgs {
                repo: "rrnewton/reverie".into(),
                ..args.clone()
            },
            AssertChildArgs {
                pr: "1629".into(),
                ..args.clone()
            },
            AssertChildArgs {
                operation: Some("cafebabe".into()),
                ..args.clone()
            },
            AssertChildArgs {
                child_pid: verifier_pid,
                ..args.clone()
            },
        ] {
            assert!(matches!(
                lock.assert_child_process(&wrong, verifier_pid),
                Err(LandLockError::ChildAssertion(_))
            ));
        }

        let mut wrong_host = lock.read_holder().unwrap().unwrap();
        wrong_host.host = "some-other-host".into();
        lock.write_holder(&wrong_host).unwrap();
        assert!(matches!(
            lock.assert_child_process(&args, verifier_pid),
            Err(LandLockError::ChildAssertion(_))
        ));
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn assert_child_refuses_non_parent_supervisor_and_reused_pid() {
        let paths = temp_paths("assert-child-process-identity");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let args = assert_child_args();
        let verifier_pid = std::process::id();
        install_assert_child_fixture(&lock, &args);

        let direct_process = current_process_owner().unwrap();
        write_truncated(&paths.owner, direct_process.render().as_bytes()).unwrap();
        let ancestry_error = lock
            .assert_child_process(&args, verifier_pid)
            .unwrap_err()
            .to_string();
        assert!(
            ancestry_error.contains("landing child")
                || ancestry_error.contains("supervised domain leader")
        );

        let mut reused = process_owner(process_parent_pid(args.child_pid).unwrap()).unwrap();
        reused.start_ticks = reused.start_ticks.saturating_add(1);
        write_truncated(&paths.owner, reused.render().as_bytes()).unwrap();
        let reuse_error = lock
            .assert_child_process(&args, verifier_pid)
            .unwrap_err()
            .to_string();
        assert!(reuse_error.contains("pid") && reuse_error.contains("reused"));
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn assert_child_refuses_reboot_missing_owner_and_expired_lease() {
        let paths = temp_paths("assert-child-fail-closed-state");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let args = assert_child_args();
        let verifier_pid = std::process::id();
        let mut owner = install_assert_child_fixture(&lock, &args);

        owner.boot_id = "planted-previous-boot".into();
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        let reboot_error = lock
            .assert_child_process(&args, verifier_pid)
            .unwrap_err()
            .to_string();
        assert!(reboot_error.contains("host rebooted"));

        remove_if_exists(&paths.owner).unwrap();
        let missing_error = lock
            .assert_child_process(&args, verifier_pid)
            .unwrap_err()
            .to_string();
        assert!(missing_error.contains("no supervised process identity"));

        install_assert_child_fixture(&lock, &args);
        let mut expired = lock.read_holder().unwrap().unwrap();
        expired.expires_at = epoch_seconds().unwrap();
        lock.write_holder(&expired).unwrap();
        let expired_error = lock
            .assert_child_process(&args, verifier_pid)
            .unwrap_err()
            .to_string();
        assert!(expired_error.contains("expired"));
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn renewal_preserves_supervised_repository_binding() {
        let paths = temp_paths("renew-preserves-repo");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        lock.acquire_with_binding(
            &AcquireArgs {
                agent: "hermit-lander".into(),
                pr: "1628".into(),
                wait: 0,
                hold: 30,
            },
            Some((Some("rrnewton/hermit"), Some("deadbeef"))),
        )
        .unwrap();
        lock.write_current_process_owner().unwrap();

        lock.renew("hermit-lander", 60, false).unwrap();
        assert_eq!(
            lock.read_holder().unwrap().unwrap().repo.as_deref(),
            Some("rrnewton/hermit")
        );
        assert_eq!(
            lock.read_holder().unwrap().unwrap().operation.as_deref(),
            Some("deadbeef")
        );
        lock.release("hermit-lander", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn supervised_holder_without_owner_fence_cannot_be_renewed_or_released() {
        let paths = temp_paths("missing-supervised-owner-fence");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let mut holder = new_holder("hermit-lander", "1628", 60, None).unwrap();
        holder.repo = Some("rrnewton/hermit".into());
        holder.operation = Some("deadbeef".into());
        lock.write_holder(&holder).unwrap();

        let renew_error = lock.renew("hermit-lander", 60, false).unwrap_err();
        assert!(renew_error.to_string().contains("no exact owner fence"));
        let release_error = lock.release("hermit-lander", false).unwrap_err();
        assert!(release_error.to_string().contains("no exact owner fence"));
        assert_eq!(lock.read_holder().unwrap(), Some(holder));
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn elapsed_empty_domain_supersedes_live_wedged_owner() {
        let paths = temp_paths("elapsed-empty-domain");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let mut holder = new_holder("hermit-lander", "1628", 600, None).unwrap();
        holder.repo = Some("rrnewton/hermit".into());
        holder.operation = Some("deadbeef".into());
        lock.write_holder(&holder).unwrap();
        let mut wedged_owner = Command::new("sleep").arg("60").spawn().unwrap();
        let old_owner = process_owner(wedged_owner.id()).unwrap();
        write_truncated(&paths.owner, old_owner.render().as_bytes()).unwrap();
        let absent_pid = 4_000_000;
        let domain = ProcessDomain {
            host: current_host(),
            boot_id: current_boot_id().unwrap(),
            leader_pid: absent_pid,
            leader_start_ticks: 1,
            pgid: absent_pid,
            deadline_at: epoch_seconds().unwrap() - 30,
            watchdog_pid: absent_pid + 1,
            watchdog_start_ticks: 1,
        };
        write_truncated(&paths.domain, domain.render().as_bytes()).unwrap();

        let acquire = AcquireArgs {
            agent: "hermit-lander".into(),
            pr: "1628".into(),
            wait: 0,
            hold: 60,
        };
        assert_eq!(lock.acquire(&acquire).unwrap(), 1);
        assert_eq!(
            lock.acquire_with_binding(&acquire, Some((Some("rrnewton/hermit"), Some("cafebabe"))),)
                .unwrap(),
            0
        );
        let acquired = lock.read_holder().unwrap().unwrap();
        assert_eq!(acquired.agent, "hermit-lander");
        assert_eq!(acquired.operation.as_deref(), Some("cafebabe"));
        assert_eq!(
            lock.read_process_owner().unwrap(),
            Some(current_process_owner().unwrap())
        );
        assert_ne!(lock.read_process_owner().unwrap(), Some(old_owner));
        assert!(!paths.domain.exists());
        let _ = wedged_owner.kill();
        let _ = wedged_owner.wait();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
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
                repo: None,
                operation: None,
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
    fn run_child_deadline_kills_child_and_releases_the_lock() {
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
                repo: None,
                operation: None,
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
    fn run_rejects_unbounded_deadline_before_acquiring() {
        let paths = temp_paths("unbounded-child-deadline");
        let lock = LandingLock {
            paths: paths.clone(),
        };
        let error = lock
            .run(RunArgs {
                agent: "unbounded-lander".into(),
                pr: "9998".into(),
                repo: None,
                operation: None,
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
