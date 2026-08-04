//! Box-exclusive-compute admission controller: at most ONE validate OR benchmark
//! job runs on this box at a time.
//!
//! Concurrent heavy load manufactures false test FAILEDs (detcore_misc residual
//! is monotonic in load; see `experiments/multisect_detcore_misc_20260803`), so a
//! second box-exclusive job is never silently admitted. This is a SEPARATE lock
//! from the landing mutex (`landing_lock.rs`): a validate must never block a
//! lander and vice versa, so they key on different lockfiles. The lease engine
//! (guard flock + FIFO queue + evidence-based dead-owner reclaim + bounded
//! child-deadline heartbeat) mirrors `landing_lock.rs` exactly on its own files;
//! pure, stateless helpers are reused from that module rather than re-derived.

use chrono::{Local, TimeZone};
use clap::{Args, Subcommand, ValueEnum};
use fs2::FileExt;
use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use thiserror::Error;

// Reuse the PURE, stateless primitives from landing_lock rather than copy tricky
// code. None of these carry a LandLockError or print a `landing-lock:` message,
// so they are safe to share verbatim across both locks.
use crate::landing_lock::{
    current_host, exit_status_code, process_start_ticks, signal_group, suffix,
};

const DEFAULT_WAIT_SECONDS: u64 = 1_800;
const DEFAULT_HOLD_SECONDS: u64 = 900;
const POLL_SECONDS: u64 = 3;
const GUARD_WAIT_SECONDS: u64 = 30;
/// Box-exclusive cap. Raising this is UNPROVEN: detcore_misc false-FAILED
/// residual is monotonic in load (`experiments/multisect_detcore_misc_20260803`),
/// so >1 requires hermit-250 evidence, not a config pick.
const BOX_EXCLUSIVE_CAP: u64 = 1;
/// Hard ceiling on how long a `run` child may execute before it is killed and the
/// lock is released. A warm validate runs ~8-16 min; this default (3600s) allows
/// generous headroom while still guaranteeing the box is freed for the next FIFO
/// waiter — an unbounded holder is unboxed compute and a head-of-line block.
const DEFAULT_CHILD_DEADLINE_SECONDS: u64 = 3_600;
/// Exit code reported when a `run` child is killed for exceeding its deadline.
const CHILD_DEADLINE_EXIT_CODE: i32 = 124;
/// Exit code reported when `--no-wait` refuses admission (lock not immediately
/// grantable). Distinct, non-zero, and mapped to 3 like an ownership refusal.
const REFUSED_EXIT_CODE: i32 = 3;
/// Grace period between SIGTERM and SIGKILL when terminating a timed-out child.
const CHILD_TERM_GRACE_SECONDS: u64 = 5;
/// Interval at which `run` polls a live child for completion or deadline breach.
const CHILD_POLL_MILLIS: u64 = 500;

#[derive(Args, Clone, Debug)]
pub struct ValidateLockArgs {
    #[command(subcommand)]
    pub command: ValidateLockCommand,
}

#[derive(Subcommand, Clone, Debug)]
pub enum ValidateLockCommand {
    /// Wait in FIFO order and acquire the box-exclusive lease.
    Acquire(AcquireArgs),
    /// Refresh a lease owned by this agent.
    Renew(RenewArgs),
    /// Release a lease owned by this agent.
    Release(ReleaseArgs),
    /// Print holder metadata and the FIFO queue (with 1-based positions).
    Status,
    /// Reclaim a lease only when its recorded owner process is proven dead.
    ReclaimDead,
    /// Acquire, run one command with a heartbeat + child-deadline, then release.
    Run(RunArgs),
}

/// A box-exclusive job kind. BOTH kinds share the ONE lock, so a validate and a
/// bench are mutually exclusive — the point is total box exclusivity, not
/// per-kind exclusivity.
#[derive(Copy, Clone, Debug, Eq, PartialEq, ValueEnum)]
pub enum Kind {
    Validate,
    Bench,
}

impl Kind {
    fn as_str(self) -> &'static str {
        match self {
            Kind::Validate => "validate",
            Kind::Bench => "bench",
        }
    }

    fn parse(value: &str) -> Result<Self, ValidateLockError> {
        match value {
            "validate" => Ok(Kind::Validate),
            "bench" => Ok(Kind::Bench),
            other => Err(ValidateLockError::InvalidState(format!(
                "unknown kind {other:?}, expected validate|bench"
            ))),
        }
    }
}

#[derive(Args, Clone, Debug)]
pub struct AcquireArgs {
    #[arg(long)]
    pub agent: String,
    #[arg(long, value_enum)]
    pub kind: Kind,
    #[arg(long)]
    pub target: String,
    #[arg(long, default_value_t = DEFAULT_WAIT_SECONDS)]
    pub wait: u64,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
    #[arg(long, default_value_t = BOX_EXCLUSIVE_CAP)]
    pub max: u64,
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
    #[arg(long, value_enum)]
    pub kind: Kind,
    #[arg(long)]
    pub target: String,
    /// Do not block: if the box is not immediately grantable, print a REFUSED
    /// reason to stderr and exit 3 instead of queueing indefinitely.
    #[arg(long, default_value_t = false)]
    pub no_wait: bool,
    #[arg(long, default_value_t = DEFAULT_WAIT_SECONDS)]
    pub wait: u64,
    #[arg(long, default_value_t = DEFAULT_HOLD_SECONDS)]
    pub hold: u64,
    /// Kill the child and release the box if it runs longer than this many
    /// seconds. Must be positive: unbounded box-exclusive holders are forbidden.
    #[arg(long, default_value_t = DEFAULT_CHILD_DEADLINE_SECONDS)]
    pub child_deadline: u64,
    #[arg(long, default_value_t = BOX_EXCLUSIVE_CAP)]
    pub max: u64,
    #[arg(last = true, required = true)]
    pub child: Vec<OsString>,
}

impl ValidateLockCommand {
    pub fn consumes_meaningful_time(&self) -> bool {
        matches!(self, Self::Acquire(_) | Self::Run(_))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidateLockState {
    pub agent: String,
    pub kind: String,
    pub target: String,
    pub host: String,
    pub acquired_at: i64,
    pub acquired_human: String,
    pub expires_at: i64,
    pub reclaimed_from: Option<String>,
}

impl ValidateLockState {
    fn parse(content: &str) -> Result<Self, ValidateLockError> {
        let mut agent = None;
        let mut kind = None;
        let mut target = None;
        let mut host = None;
        let mut acquired_at = None;
        let mut acquired_human = None;
        let mut expires_at = None;
        let mut reclaimed_from = None;
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                ValidateLockError::InvalidState(format!(
                    "holder line {} is not key=value",
                    line_number + 1
                ))
            })?;
            match key {
                "agent" => agent = Some(value.to_string()),
                "kind" => kind = Some(value.to_string()),
                "target" => target = Some(value.to_string()),
                "host" => host = Some(value.to_string()),
                "acquired_at" => acquired_at = Some(parse_integer(key, value)?),
                "acquired_human" => acquired_human = Some(value.to_string()),
                "expires_at" => expires_at = Some(parse_integer(key, value)?),
                "reclaimed_from" => reclaimed_from = Some(value.to_string()),
                unknown => {
                    return Err(ValidateLockError::InvalidState(format!(
                        "unknown holder field {unknown:?}"
                    )));
                }
            }
        }
        Ok(Self {
            agent: required(agent, "agent")?,
            kind: required(kind, "kind")?,
            target: required(target, "target")?,
            host: required(host, "host")?,
            acquired_at: required(acquired_at, "acquired_at")?,
            acquired_human: required(acquired_human, "acquired_human")?,
            expires_at: required(expires_at, "expires_at")?,
            reclaimed_from,
        })
    }

    fn render(&self) -> String {
        let mut output = format!(
            "agent={}\nkind={}\ntarget={}\nhost={}\nacquired_at={}\nacquired_human={}\nexpires_at={}\n",
            self.agent,
            self.kind,
            self.target,
            self.host,
            self.acquired_at,
            self.acquired_human,
            self.expires_at
        );
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
    pub target: String,
}

impl QueueEntry {
    fn parse(line: &str, line_number: usize) -> Result<Self, ValidateLockError> {
        let fields: Vec<_> = line.split('\t').collect();
        if fields.len() != 3 {
            return Err(ValidateLockError::InvalidState(format!(
                "queue line {line_number} must have three tab-separated fields"
            )));
        }
        Ok(Self {
            enqueued_at: parse_integer("queue timestamp", fields[0])?,
            agent: fields[1].to_string(),
            target: fields[2].to_string(),
        })
    }

    fn render(&self) -> String {
        format!("{}\t{}\t{}\n", self.enqueued_at, self.agent, self.target)
    }
}

/// Process-identity sidecar: proves whether the recorded owner is still the same
/// live process (boot_id defeats reboots; start_ticks defeats pid reuse).
#[derive(Clone, Debug, Eq, PartialEq)]
struct ProcessOwner {
    host: String,
    boot_id: String,
    pid: u32,
    start_ticks: u64,
}

impl ProcessOwner {
    fn parse(content: &str) -> Result<Self, ValidateLockError> {
        let mut host = None;
        let mut boot_id = None;
        let mut pid = None;
        let mut start_ticks = None;
        for (line_number, line) in content.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                ValidateLockError::InvalidState(format!(
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
                    return Err(ValidateLockError::InvalidState(format!(
                        "unknown owner field {unknown:?}"
                    )));
                }
            }
        }
        Ok(Self {
            host: required(host, "owner host")?,
            boot_id: required(boot_id, "owner boot_id")?,
            pid: u32::try_from(required(pid, "owner pid")?)
                .map_err(|_| ValidateLockError::InvalidState("owner pid exceeds u32".into()))?,
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

#[derive(Debug, Error)]
pub enum ValidateLockError {
    #[error("validate-lock: {action} {path}: {source}")]
    Io {
        action: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("validate-lock: invalid on-disk state: {0}")]
    InvalidState(String),
    #[error("validate-lock: timed out taking internal guard after {GUARD_WAIT_SECONDS}s")]
    GuardTimeout,
    #[error("validate-lock: renew: {agent} does not hold the lock")]
    RenewNotOwner { agent: String },
    #[error("validate-lock: release: lock is held by {holder}, not {agent}; refusing")]
    ReleaseNotOwner { agent: String, holder: String },
    #[error("validate-lock: child command is empty")]
    EmptyChild,
    #[error(
        "validate-lock: --child-deadline must be positive; unbounded lock holders are forbidden"
    )]
    UnboundedChildDeadline,
    #[error("validate-lock: --max must be 1; box-exclusive cap >1 is unproven (detcore_misc residual is monotonic in load, experiments/multisect_detcore_misc_20260803); raising N requires hermit-250 evidence")]
    BadMax,
    #[error(
        "validate-lock: {operation}: process {pid} owns the supervised lease, not this process"
    )]
    ProcessNotOwner { operation: &'static str, pid: u32 },
    #[error("validate-lock: cannot reclaim lease: {0}")]
    ReclaimNotProven(String),
}

impl ValidateLockError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::RenewNotOwner { .. }
            | Self::ReleaseNotOwner { .. }
            | Self::ProcessNotOwner { .. }
            | Self::ReclaimNotProven(_)
            | Self::GuardTimeout
            | Self::InvalidState(_) => 3,
            Self::Io { .. } | Self::EmptyChild | Self::UnboundedChildDeadline | Self::BadMax => 2,
        }
    }
}

#[derive(Clone, Debug)]
struct LockPaths {
    lock: PathBuf,
    guard: PathBuf,
    queue: PathBuf,
    owner: PathBuf,
}

impl LockPaths {
    fn for_workspace(root: &Path) -> Self {
        // Env override mirrors CI_HUB_LANDING_LOCK; default base is DISTINCT from
        // `.landing-lock` so a validate never contends with a lander.
        let lock = env::var_os("CI_HUB_VALIDATE_LOCK")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".validate-lock"));
        Self {
            guard: suffix(&lock, ".guard"),
            queue: suffix(&lock, ".queue"),
            owner: suffix(&lock, ".owner"),
            lock,
        }
    }
}

struct ValidateLock {
    paths: LockPaths,
}

/// Result of one guarded acquisition attempt.
enum AcquireToken {
    Acquired,
    AcquiredReclaimed(String),
    /// The box is held by a live owner. `ahead` is how many FIFO waiters precede
    /// this agent (0-based) so callers can print a 1-based position.
    Held {
        agent: String,
        kind: String,
        target: String,
        seconds_left: i64,
        ahead: usize,
    },
    /// The box is free but this agent is not at the head of the FIFO queue.
    WaitTurn {
        head: String,
        ahead: usize,
    },
}

pub fn execute(root: &Path, args: ValidateLockArgs) -> Result<i32, ValidateLockError> {
    let lock = ValidateLock {
        paths: LockPaths::for_workspace(root),
    };
    match args.command {
        ValidateLockCommand::Acquire(args) => {
            reject_bad_max(args.max)?;
            lock.acquire(&args.agent, args.kind, &args.target, args.wait, args.hold)
        }
        ValidateLockCommand::Renew(args) => {
            lock.renew(&args.agent, args.hold, true)?;
            Ok(0)
        }
        ValidateLockCommand::Release(args) => {
            lock.release(&args.agent, true)?;
            Ok(0)
        }
        ValidateLockCommand::Status => {
            lock.status()?;
            Ok(0)
        }
        ValidateLockCommand::ReclaimDead => lock.reclaim_dead(),
        ValidateLockCommand::Run(args) => lock.run(args),
    }
}

/// "Derive don't pick": any cap other than 1 is refused before touching the lock.
fn reject_bad_max(max: u64) -> Result<(), ValidateLockError> {
    if max != BOX_EXCLUSIVE_CAP {
        return Err(ValidateLockError::BadMax);
    }
    Ok(())
}

impl ValidateLock {
    fn acquire(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        wait: u64,
        hold: u64,
    ) -> Result<i32, ValidateLockError> {
        let started = Instant::now();
        let mut last = String::new();
        loop {
            let token = self.with_guard(|| self.try_acquire(agent, kind, target, hold))?;
            match token {
                AcquireToken::Acquired => {
                    eprintln!(
                        "validate-lock: ACQUIRED by {agent} running {} {target} (lease {hold}s)",
                        kind.as_str()
                    );
                    return Ok(0);
                }
                AcquireToken::AcquiredReclaimed(previous) => {
                    eprintln!(
                        "validate-lock: ACQUIRED by {agent} running {} {target}; evidence-reclaimed lease from {previous}",
                        kind.as_str()
                    );
                    return Ok(0);
                }
                AcquireToken::Held {
                    agent: holder,
                    kind: holder_kind,
                    ahead,
                    ..
                } => {
                    let position = ahead + 1;
                    let token = format!("HELD:{holder}:{holder_kind}:{position}");
                    if token != last {
                        eprintln!(
                            "validate-lock: waiting: held by {holder} running {holder_kind}; queued position {position}"
                        );
                        last = token;
                    }
                }
                AcquireToken::WaitTurn { head, ahead } => {
                    let position = ahead + 1;
                    let token = format!("WAIT:{head}:{position}");
                    if token != last {
                        eprintln!(
                            "validate-lock: waiting: box free; {head} is ahead; queued position {position}"
                        );
                        last = token;
                    }
                }
            }
            if started.elapsed() >= Duration::from_secs(wait) {
                self.with_guard(|| {
                    self.remove_from_queue(agent)?;
                    Ok(())
                })?;
                eprintln!("validate-lock: TIMEOUT after {wait}s");
                return Ok(1);
            }
            thread::sleep(Duration::from_secs(POLL_SECONDS));
        }
    }

    fn try_acquire(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        hold: u64,
    ) -> Result<AcquireToken, ValidateLockError> {
        let now = epoch_seconds()?;
        let mut queue = self.read_queue()?;
        if !queue.iter().any(|entry| entry.agent == agent) {
            queue.push(QueueEntry {
                enqueued_at: now,
                agent: agent.to_string(),
                target: target.to_string(),
            });
        }
        let cutoff = now - (2 * DEFAULT_WAIT_SECONDS) as i64;
        queue.retain(|entry| entry.enqueued_at >= cutoff);
        let my_index = queue
            .iter()
            .position(|entry| entry.agent == agent)
            .unwrap_or(0);

        let holder = self.read_holder()?;
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
                kind: holder.kind.clone(),
                target: holder.target.clone(),
                seconds_left: holder.expires_at - now,
                ahead: my_index,
            });
        }

        if let Some(head) = queue.first() {
            if head.agent != agent {
                let head_agent = head.agent.clone();
                self.write_queue(&queue)?;
                return Ok(AcquireToken::WaitTurn {
                    head: head_agent,
                    ahead: my_index,
                });
            }
        }

        let reclaimed = holder.map(|holder| {
            if let Some(reason) = &dead_owner {
                format!("{} (dead owner: {reason})", holder.agent)
            } else {
                holder.agent
            }
        });
        self.write_holder(&new_holder(agent, kind, target, hold, reclaimed.clone())?)?;
        remove_if_exists(&self.paths.owner)?;
        queue.retain(|entry| entry.agent != agent);
        self.write_queue(&queue)?;
        Ok(match reclaimed {
            Some(previous) if !previous.is_empty() => AcquireToken::AcquiredReclaimed(previous),
            _ => AcquireToken::Acquired,
        })
    }

    fn renew(&self, agent: &str, hold: u64, announce: bool) -> Result<(), ValidateLockError> {
        self.with_guard(|| {
            let holder = self
                .read_holder()?
                .ok_or_else(|| ValidateLockError::RenewNotOwner {
                    agent: agent.to_string(),
                })?;
            if holder.agent != agent {
                return Err(ValidateLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            self.assert_current_process_owner("renew")?;
            let kind = Kind::parse(&holder.kind)?;
            self.write_holder(&new_holder(agent, kind, &holder.target, hold, None)?)?;
            Ok(())
        })?;
        if announce {
            eprintln!("validate-lock: renewed {agent} lease {hold}s");
        }
        Ok(())
    }

    fn release(&self, agent: &str, announce: bool) -> Result<(), ValidateLockError> {
        let (released, next) = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                remove_if_exists(&self.paths.owner)?;
                return Ok((false, None));
            };
            if holder.agent != agent {
                return Err(ValidateLockError::ReleaseNotOwner {
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
                (false, _) => eprintln!("validate-lock: release: no lock held"),
                (true, None) => {
                    eprintln!("validate-lock: RELEASED by {agent}; box FREE (queue empty)")
                }
                (true, Some(next)) => {
                    eprintln!("validate-lock: RELEASED by {agent}; box FREE -> next: {next}")
                }
            }
        }
        Ok(())
    }

    fn status(&self) -> Result<(), ValidateLockError> {
        let now = epoch_seconds()?;
        let holder = self.read_holder()?;
        let liveness = self.owner_liveness()?;
        match holder {
            Some(holder) if holder.live_at(now) && matches!(liveness, OwnerLiveness::Dead(_)) => {
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
        let queue = self.read_queue()?;
        if !queue.is_empty() {
            println!("queue (FIFO):");
            for (index, entry) in queue.iter().enumerate() {
                print!("  {:>6}\t{}", index + 1, entry.render());
            }
        }
        Ok(())
    }

    fn reclaim_dead(&self) -> Result<i32, ValidateLockError> {
        let reclaimed = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                remove_if_exists(&self.paths.owner)?;
                return Ok(None);
            };
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => {
                    remove_if_exists(&self.paths.lock)?;
                    remove_if_exists(&self.paths.owner)?;
                    Ok(Some((holder.agent, holder.kind, holder.target, reason)))
                }
                OwnerLiveness::Alive => Err(ValidateLockError::ReclaimNotProven(
                    "recorded owner process is alive".into(),
                )),
                OwnerLiveness::Unknown(reason) => Err(ValidateLockError::ReclaimNotProven(reason)),
            }
        })?;
        match reclaimed {
            Some((agent, kind, target, reason)) => eprintln!(
                "validate-lock: evidence-reclaimed dead owner agent={agent} kind={kind} target={target}: {reason}"
            ),
            None => eprintln!("validate-lock: reclaim-dead: no lock held"),
        }
        Ok(0)
    }

    fn run(&self, args: RunArgs) -> Result<i32, ValidateLockError> {
        if args.child.is_empty() {
            return Err(ValidateLockError::EmptyChild);
        }
        if args.child_deadline == 0 {
            return Err(ValidateLockError::UnboundedChildDeadline);
        }
        reject_bad_max(args.max)?;

        // Admission: block FIFO, or refuse immediately under --no-wait. Never
        // silently admit a second box-exclusive holder.
        if args.no_wait {
            let token = self
                .with_guard(|| self.try_acquire(&args.agent, args.kind, &args.target, args.hold))?;
            match token {
                AcquireToken::Acquired => eprintln!(
                    "validate-lock: ACQUIRED by {} running {} {} (lease {}s)",
                    args.agent,
                    args.kind.as_str(),
                    args.target,
                    args.hold
                ),
                AcquireToken::AcquiredReclaimed(previous) => eprintln!(
                    "validate-lock: ACQUIRED by {} running {} {}; evidence-reclaimed lease from {previous}",
                    args.agent,
                    args.kind.as_str(),
                    args.target
                ),
                AcquireToken::Held {
                    agent,
                    kind,
                    target,
                    seconds_left,
                    ahead,
                } => {
                    eprintln!(
                        "REFUSED: box-exclusive lock held by {agent} running {kind} {target}, ~{seconds_left}s left; {ahead} ahead in queue"
                    );
                    return Ok(REFUSED_EXIT_CODE);
                }
                AcquireToken::WaitTurn { head, ahead } => {
                    eprintln!(
                        "REFUSED: box-exclusive lock free but {head} is ahead of {} in the FIFO queue, ~0s left; {ahead} ahead in queue",
                        args.agent
                    );
                    return Ok(REFUSED_EXIT_CODE);
                }
            }
        } else {
            let acquire_status =
                self.acquire(&args.agent, args.kind, &args.target, args.wait, args.hold)?;
            if acquire_status != 0 {
                return Ok(acquire_status);
            }
        }

        if let Err(error) = self.with_guard(|| self.write_current_process_owner()) {
            let _ = self.release(&args.agent, true);
            return Err(error);
        }

        let (stop_tx, stop_rx) = mpsc::channel();
        let heartbeat_paths = self.paths.clone();
        let heartbeat_agent = args.agent.clone();
        let heartbeat_hold = args.hold;
        let heartbeat = thread::spawn(move || {
            let heartbeat_lock = ValidateLock {
                paths: heartbeat_paths,
            };
            let interval = Duration::from_secs((heartbeat_hold / 3).max(1));
            while stop_rx.recv_timeout(interval).is_err() {
                if heartbeat_lock
                    .renew(&heartbeat_agent, heartbeat_hold, false)
                    .is_err()
                {
                    break;
                }
            }
        });

        // Run the child in its own process group so a deadline kill reaches the
        // whole validate subtree (bash / cargo / hermit), not just the wrapper.
        let spawn_result = Command::new(&args.child[0])
            .args(&args.child[1..])
            .process_group(0)
            .spawn();
        let outcome = match spawn_result {
            Ok(mut child) => Ok(supervise_child(
                &mut child,
                args.child_deadline,
                &args.target,
            )),
            Err(source) => Err(source),
        };
        let _ = stop_tx.send(());
        let _ = heartbeat.join();
        let release_result = self.release(&args.agent, true);

        match outcome {
            Ok(ChildOutcome::Exited(status)) => {
                release_result?;
                Ok(exit_status_code(status))
            }
            Ok(ChildOutcome::TimedOut) => {
                release_result?;
                eprintln!(
                    "validate-lock: ABANDON {}: child exceeded --child-deadline {}s; \
                     killed the subtree and RELEASED the box so the FIFO can proceed.",
                    args.target, args.child_deadline
                );
                Ok(CHILD_DEADLINE_EXIT_CODE)
            }
            Err(source) => {
                release_result?;
                Err(ValidateLockError::Io {
                    action: "launch child from",
                    path: PathBuf::from(&args.child[0]),
                    source,
                })
            }
        }
    }

    fn with_guard<T>(
        &self,
        operation: impl FnOnce() -> Result<T, ValidateLockError>,
    ) -> Result<T, ValidateLockError> {
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
                        return Err(ValidateLockError::GuardTimeout);
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

    fn read_holder(&self) -> Result<Option<ValidateLockState>, ValidateLockError> {
        if !self.paths.lock.exists() {
            return Ok(None);
        }
        let content = read_to_string(&self.paths.lock)?;
        Ok(Some(ValidateLockState::parse(&content)?))
    }

    fn read_process_owner(&self) -> Result<Option<ProcessOwner>, ValidateLockError> {
        if !self.paths.owner.exists() {
            return Ok(None);
        }
        let content = read_to_string(&self.paths.owner)?;
        Ok(Some(ProcessOwner::parse(&content)?))
    }

    fn write_current_process_owner(&self) -> Result<(), ValidateLockError> {
        let owner = current_process_owner()?;
        write_truncated(&self.paths.owner, owner.render().as_bytes())
    }

    fn owner_liveness(&self) -> Result<OwnerLiveness, ValidateLockError> {
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

    fn assert_current_process_owner(
        &self,
        operation: &'static str,
    ) -> Result<(), ValidateLockError> {
        let Some(owner) = self.read_process_owner()? else {
            return Ok(());
        };
        let current = current_process_owner()?;
        if owner == current {
            Ok(())
        } else {
            Err(ValidateLockError::ProcessNotOwner {
                operation,
                pid: owner.pid,
            })
        }
    }

    fn write_holder(&self, holder: &ValidateLockState) -> Result<(), ValidateLockError> {
        write_truncated(&self.paths.lock, holder.render().as_bytes())
    }

    fn read_queue(&self) -> Result<Vec<QueueEntry>, ValidateLockError> {
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

    fn write_queue(&self, queue: &[QueueEntry]) -> Result<(), ValidateLockError> {
        let mut content = String::new();
        for entry in queue {
            content.push_str(&entry.render());
        }
        let temporary = suffix(&self.paths.queue, ".tmp");
        write_truncated(&temporary, content.as_bytes())?;
        fs::rename(&temporary, &self.paths.queue)
            .map_err(|source| io_error("replace queue", &self.paths.queue, source))
    }

    fn remove_from_queue(&self, agent: &str) -> Result<(), ValidateLockError> {
        let mut queue = self.read_queue()?;
        queue.retain(|entry| entry.agent != agent);
        self.write_queue(&queue)
    }
}

fn new_holder(
    agent: &str,
    kind: Kind,
    target: &str,
    hold: u64,
    reclaimed_from: Option<String>,
) -> Result<ValidateLockState, ValidateLockError> {
    let acquired_at = epoch_seconds()?;
    let acquired_human = Local
        .timestamp_opt(acquired_at, 0)
        .single()
        .ok_or_else(|| ValidateLockError::InvalidState("current timestamp is out of range".into()))?
        .format("%Y-%m-%dT%H:%M:%S%z")
        .to_string();
    let host = current_host();
    Ok(ValidateLockState {
        agent: agent.to_string(),
        kind: kind.as_str().to_string(),
        target: target.to_string(),
        host,
        acquired_at,
        acquired_human,
        expires_at: acquired_at.saturating_add(hold as i64),
        reclaimed_from,
    })
}

// --- small helpers copied from landing_lock, retyped to ValidateLockError ---
// (Reusing landing_lock's would export its LandLockError into this module's
// error surface; these are small and stateless, so a faithful copy keeps the
// error type clean without touching any landing_lock logic.)

fn epoch_seconds() -> Result<i64, ValidateLockError> {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| {
            ValidateLockError::InvalidState(format!("system clock before epoch: {error}"))
        })?
        .as_secs();
    i64::try_from(seconds)
        .map_err(|_| ValidateLockError::InvalidState("system time exceeds i64".into()))
}

fn parse_integer(name: &str, value: &str) -> Result<i64, ValidateLockError> {
    value.parse().map_err(|error| {
        ValidateLockError::InvalidState(format!(
            "{name} must be an integer, got {value:?}: {error}"
        ))
    })
}

fn parse_unsigned(name: &str, value: &str) -> Result<u64, ValidateLockError> {
    value.parse().map_err(|error| {
        ValidateLockError::InvalidState(format!(
            "{name} must be an integer, got {value:?}: {error}"
        ))
    })
}

fn current_boot_id() -> Result<String, ValidateLockError> {
    let path = Path::new("/proc/sys/kernel/random/boot_id");
    fs::read_to_string(path)
        .map(|value| value.trim().to_string())
        .map_err(|source| io_error("read", path, source))
}

fn current_process_owner() -> Result<ProcessOwner, ValidateLockError> {
    let pid = std::process::id();
    let start_ticks = process_start_ticks(pid).map_err(|source| ValidateLockError::Io {
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

fn required<T>(value: Option<T>, name: &str) -> Result<T, ValidateLockError> {
    value.ok_or_else(|| ValidateLockError::InvalidState(format!("holder has no {name} field")))
}

fn read_to_string(path: &Path) -> Result<String, ValidateLockError> {
    let mut file = File::open(path).map_err(|source| io_error("open", path, source))?;
    let mut content = String::new();
    file.read_to_string(&mut content)
        .map_err(|source| io_error("read", path, source))?;
    Ok(content)
}

fn write_truncated(path: &Path, bytes: &[u8]) -> Result<(), ValidateLockError> {
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

fn remove_if_exists(path: &Path) -> Result<(), ValidateLockError> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("remove", path, source)),
    }
}

fn io_error(action: &'static str, path: &Path, source: io::Error) -> ValidateLockError {
    ValidateLockError::Io {
        action,
        path: path.to_path_buf(),
        source,
    }
}

/// How a supervised `run` child ended.
enum ChildOutcome {
    Exited(ExitStatus),
    TimedOut,
}

/// Wait for `child`, killing its process group if it runs longer than
/// `deadline_secs`. Copied (not reused) from landing_lock so the deadline-kill
/// messages carry the `validate-lock:` prefix; `signal_group` itself is shared.
fn supervise_child(child: &mut Child, deadline_secs: u64, target: &str) -> ChildOutcome {
    let deadline = Instant::now() + Duration::from_secs(deadline_secs);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return ChildOutcome::Exited(status),
            Ok(None) => {}
            Err(source) => match child.wait() {
                Ok(status) => return ChildOutcome::Exited(status),
                Err(_) => {
                    eprintln!("validate-lock: cannot wait on child: {source}");
                    return ChildOutcome::TimedOut;
                }
            },
        }
        if Instant::now() >= deadline {
            terminate_child_group(child, target);
            return ChildOutcome::TimedOut;
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// SIGTERM then (after a grace period) SIGKILL the child's process group and reap
/// the direct child. `signal_group` is reused from landing_lock.
fn terminate_child_group(child: &mut Child, target: &str) {
    let group = format!("-{}", child.id());
    eprintln!("validate-lock: child-deadline reached for {target}; SIGTERM process group {group}");
    signal_group("TERM", &group);
    let grace = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < grace {
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
    }
    eprintln!("validate-lock: grace expired for {target}; SIGKILL process group {group}");
    signal_group("KILL", &group);
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_paths(name: &str) -> LockPaths {
        let root = env::temp_dir().join(format!(
            "ci-hub-validate-lock-{name}-{}-{}",
            std::process::id(),
            epoch_seconds().unwrap()
        ));
        fs::create_dir_all(&root).unwrap();
        let lock = root.join(".validate-lock");
        LockPaths {
            guard: suffix(&lock, ".guard"),
            queue: suffix(&lock, ".queue"),
            owner: suffix(&lock, ".owner"),
            lock,
        }
    }

    // 1. Byte round-trip for the holder (kind+target) and a queue entry.
    #[test]
    fn holder_and_queue_round_trip() {
        let holder = ValidateLockState {
            agent: "hermit-247".into(),
            kind: "validate".into(),
            target: "0123456789abcdef0123456789abcdef01234567".into(),
            host: "devbig014".into(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: Some("hermit-opt".into()),
        };
        let rendered = "agent=hermit-247\nkind=validate\ntarget=0123456789abcdef0123456789abcdef01234567\nhost=devbig014\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\nreclaimed_from=hermit-opt\n";
        assert_eq!(holder.render(), rendered);
        assert_eq!(ValidateLockState::parse(rendered).unwrap(), holder);

        let entry = QueueEntry {
            enqueued_at: 1785770487,
            agent: "hermit-opt".into(),
            target: "bench:cpu-vs-syscall".into(),
        };
        assert_eq!(
            entry.render(),
            "1785770487\thermit-opt\tbench:cpu-vs-syscall\n"
        );
        assert_eq!(
            QueueEntry::parse(entry.render().trim_end(), 1).unwrap(),
            entry
        );
    }

    // 2. N=3 sequential validates all admitted, lock ends FREE.
    #[test]
    fn positive_non_starvation_three_sequential() {
        let paths = temp_paths("three-sequential");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        for i in 0..3 {
            let code = lock
                .run(RunArgs {
                    agent: format!("validate-agent-{i}"),
                    kind: Kind::Validate,
                    target: format!("sha-{i}"),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    max: 1,
                    child: vec![OsString::from("/bin/true")],
                })
                .unwrap();
            assert_eq!(code, 0, "sequential validate {i} should be admitted");
            assert!(
                lock.read_holder().unwrap().is_none(),
                "lock must be FREE after validate {i}"
            );
        }
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    // 3. Second box-exclusive holder is refused/queued; both kinds share the lock.
    #[test]
    fn negative_second_holder_refused_and_queued() {
        let paths = temp_paths("second-holder");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        // agent1 holds the box as a validate.
        assert_eq!(
            lock.acquire("agent1", Kind::Validate, "sha1", 0, 60)
                .unwrap(),
            0
        );

        // (a) a second agent's try_acquire returns Held (not Acquired), naming agent1.
        let token = lock
            .with_guard(|| lock.try_acquire("agent2", Kind::Validate, "sha2", 60))
            .unwrap();
        assert!(
            matches!(&token, AcquireToken::Held { agent, .. } if agent == "agent1"),
            "second validate must see agent1 holding the box"
        );

        // (b) the --no-wait refuse path returns exit code 3.
        let code = lock
            .run(RunArgs {
                agent: "agent2".into(),
                kind: Kind::Validate,
                target: "sha2".into(),
                no_wait: true,
                wait: 0,
                hold: 60,
                child_deadline: 30,
                max: 1,
                child: vec![OsString::from("/bin/true")],
            })
            .unwrap();
        assert_eq!(code, REFUSED_EXIT_CODE, "no-wait must refuse with exit 3");

        // (c) a BENCH-kind second request is ALSO blocked by the validate holder.
        let bench_token = lock
            .with_guard(|| lock.try_acquire("agent3", Kind::Bench, "bench:x", 60))
            .unwrap();
        assert!(
            matches!(&bench_token, AcquireToken::Held { agent, .. } if agent == "agent1"),
            "a bench must be blocked by a validate holder (shared lock)"
        );

        // Release agent1; the queued agent2 (head of FIFO) then acquires.
        lock.release("agent1", false).unwrap();
        assert_eq!(
            lock.acquire("agent2", Kind::Validate, "sha2", 0, 60)
                .unwrap(),
            0
        );
        let holder = lock.read_holder().unwrap().unwrap();
        assert_eq!(holder.agent, "agent2");
        lock.release("agent2", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    // 4. run child-deadline kills the child quickly and releases the box.
    #[test]
    fn run_child_deadline_kills_and_releases() {
        let paths = temp_paths("child-deadline");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let started = Instant::now();
        let code = lock
            .run(RunArgs {
                agent: "stuck-validate".into(),
                kind: Kind::Validate,
                target: "sha-stuck".into(),
                no_wait: false,
                wait: 0,
                hold: 30,
                child_deadline: 1,
                max: 1,
                child: vec![OsString::from("sleep"), OsString::from("120")],
            })
            .unwrap();
        assert_eq!(code, CHILD_DEADLINE_EXIT_CODE);
        assert!(
            started.elapsed() < Duration::from_secs(30),
            "child-deadline run took {:?}",
            started.elapsed()
        );
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    // 5. --max != 1 is rejected before acquiring; the box stays FREE.
    #[test]
    fn max_greater_than_one_rejected() {
        let paths = temp_paths("bad-max");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let error = lock
            .run(RunArgs {
                agent: "greedy".into(),
                kind: Kind::Validate,
                target: "sha-greedy".into(),
                no_wait: false,
                wait: 0,
                hold: 30,
                child_deadline: 30,
                max: 2,
                child: vec![OsString::from("/bin/true")],
            })
            .unwrap_err();
        assert!(matches!(error, ValidateLockError::BadMax));
        assert!(lock.read_holder().unwrap().is_none());
        // The Display carries the exact unproven-cap rationale.
        assert!(error
            .to_string()
            .contains("box-exclusive cap >1 is unproven"));
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    // 6. A proven-dead owner is reclaimed before lease expiry.
    #[test]
    fn dead_owner_reclaimed() {
        let paths = temp_paths("dead-owner");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        assert_eq!(
            lock.acquire("dead-validate", Kind::Validate, "sha-dead", 0, 3_600)
                .unwrap(),
            0
        );
        // Plant a process-owner sidecar whose pid cannot exist -> proven dead.
        let mut owner = current_process_owner().unwrap();
        owner.pid = u32::MAX;
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        assert!(matches!(
            lock.owner_liveness().unwrap(),
            OwnerLiveness::Dead(_)
        ));
        // A replacement acquires despite the still-live lease, recording provenance.
        assert_eq!(
            lock.acquire("replacement-validate", Kind::Validate, "sha-new", 0, 60)
                .unwrap(),
            0
        );
        let holder = lock.read_holder().unwrap().unwrap();
        assert_eq!(holder.agent, "replacement-validate");
        assert!(holder.reclaimed_from.unwrap().contains("dead owner"));
        lock.release("replacement-validate", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }
}
