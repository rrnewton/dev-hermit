//! Typed implementation of the shared-file landing mutex.

use chrono::{Local, TimeZone};
use clap::{Args, Subcommand};
use fs2::FileExt;
use std::env;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
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
/// Grace period between SIGTERM and SIGKILL when terminating a timed-out child.
const CHILD_TERM_GRACE_SECONDS: u64 = 5;
/// Interval at which `run` polls a live child for completion or deadline breach.
const CHILD_POLL_MILLIS: u64 = 500;

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
    /// Acquire, run one command with a heartbeat, then release.
    Run(RunArgs),
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
    /// Kill the child and release the lock if it runs longer than this many
    /// seconds. Bounds the head-of-line block a stuck lander would otherwise
    /// impose on the FIFO queue. Must be positive: unbounded lock holders are
    /// forbidden.
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
            pr: required(pr, "pr")?,
            host: required(host, "host")?,
            acquired_at: required(acquired_at, "acquired_at")?,
            acquired_human: required(acquired_human, "acquired_human")?,
            expires_at: required(expires_at, "expires_at")?,
            reclaimed_from,
        })
    }

    fn render(&self) -> String {
        let mut output = format!(
            "agent={}\npr={}\nhost={}\nacquired_at={}\nacquired_human={}\nexpires_at={}\n",
            self.agent, self.pr, self.host, self.acquired_at, self.acquired_human, self.expires_at
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
}

impl LandLockError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::RenewNotOwner { .. }
            | Self::ReleaseNotOwner { .. }
            | Self::ProcessNotOwner { .. }
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
        LandLockCommand::Run(args) => lock.run(args),
    }
}

impl LandingLock {
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
            if holder.agent != agent {
                return Err(LandLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            self.assert_current_process_owner("renew")?;
            self.write_holder(&new_holder(agent, &holder.pr, hold, None)?)?;
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

    fn reclaim_dead(&self) -> Result<i32, LandLockError> {
        let reclaimed = self.with_guard(|| {
            let Some(holder) = self.read_holder()? else {
                remove_if_exists(&self.paths.owner)?;
                return Ok(None);
            };
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => {
                    remove_if_exists(&self.paths.lock)?;
                    remove_if_exists(&self.paths.owner)?;
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

        let (stop_tx, stop_rx) = mpsc::channel();
        let heartbeat_paths = self.paths.clone();
        let heartbeat_agent = args.agent.clone();
        let heartbeat_hold = args.hold;
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
                    break;
                }
            }
        });

        // Run the child in its own process group so a deadline kill reaches the
        // whole land subtree (gh / git / cargo), not just the wrapper shell.
        let spawn_result = Command::new(&args.child[0])
            .args(&args.child[1..])
            .process_group(0)
            .spawn();
        let outcome = match spawn_result {
            Ok(mut child) => Ok(supervise_child(&mut child, args.child_deadline, &args.pr)),
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
                // The lock was just released above so the next FIFO waiter can
                // proceed. Emit a loud, single-line ABANDON signal so the stuck
                // land does not silently languish (the #244 pattern).
                release_result?;
                eprintln!(
                    "landing-lock: ABANDON PR #{}: child exceeded --child-deadline {}s; \
                     killed the land subtree and RELEASED the lock so the FIFO can proceed. \
                     PR left open for retry.",
                    args.pr, args.child_deadline
                );
                Ok(CHILD_DEADLINE_EXIT_CODE)
            }
            Err(source) => {
                release_result?;
                Err(LandLockError::Io {
                    action: "launch child from",
                    path: PathBuf::from(&args.child[0]),
                    source,
                })
            }
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

fn current_host() -> String {
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

fn process_start_ticks(pid: u32) -> io::Result<u64> {
    let path = PathBuf::from(format!("/proc/{pid}/stat"));
    let stat = fs::read_to_string(path)?;
    let close = stat.rfind(')').ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no closing comm",
        )
    })?;
    let fields: Vec<_> = stat[close + 1..].split_whitespace().collect();
    let value = fields.get(19).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "process stat has no starttime field",
        )
    })?;
    value.parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid process starttime {value:?}: {error}"),
        )
    })
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

fn required<T>(value: Option<T>, name: &str) -> Result<T, LandLockError> {
    value.ok_or_else(|| LandLockError::InvalidState(format!("holder has no {name} field")))
}

fn suffix(path: &Path, suffix: &str) -> PathBuf {
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
    Exited(ExitStatus),
    /// The child exceeded its deadline and was killed.
    TimedOut,
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
            Ok(Some(status)) => return ChildOutcome::Exited(status),
            Ok(None) => {}
            Err(source) => {
                // Cannot supervise a child we can't wait on; block on it so we do
                // not spin, and report whatever status it finally yields.
                match child.wait() {
                    Ok(status) => return ChildOutcome::Exited(status),
                    Err(_) => {
                        eprintln!("landing-lock: cannot wait on child: {source}");
                        return ChildOutcome::TimedOut;
                    }
                }
            }
        }
        if Instant::now() >= deadline {
            terminate_child_group(child, pr);
            return ChildOutcome::TimedOut;
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// SIGTERM then (after a grace period) SIGKILL the child's process group, and
/// reap the direct child. The child was spawned with `process_group(0)`, so its
/// pid equals its pgid and signalling `-pid` reaches the whole land subtree
/// (gh / git / cargo), not just the wrapper shell. Uses `/bin/kill`, whose
/// negative-pid convention targets a process group, to avoid a libc dependency
/// in the shared cargo manifest.
fn terminate_child_group(child: &mut Child, pr: &str) {
    let group = format!("-{}", child.id());
    eprintln!("landing-lock: child-deadline reached for PR #{pr}; SIGTERM process group {group}");
    signal_group("TERM", &group);
    let grace = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < grace {
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
    }
    eprintln!("landing-lock: grace expired for PR #{pr}; SIGKILL process group {group}");
    signal_group("KILL", &group);
    let _ = child.wait();
}

/// Send `signal` to the process `group` (a negative pid string) via `/bin/kill`.
fn signal_group(signal: &str, group: &str) {
    let _ = Command::new("kill")
        .arg(format!("-{signal}"))
        .arg(group)
        .status();
}

fn exit_status_code(status: ExitStatus) -> i32 {
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
            lock,
        }
    }

    #[test]
    fn holder_format_is_byte_compatible() {
        let holder = LockState {
            agent: "hermit-ci".into(),
            pr: "1533".into(),
            host: "devbig014".into(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: Some("hermit-dbi".into()),
        };
        let rendered = "agent=hermit-ci\npr=1533\nhost=devbig014\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\nreclaimed_from=hermit-dbi\n";
        assert_eq!(holder.render(), rendered);
        assert_eq!(LockState::parse(rendered).unwrap(), holder);
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
            host: "devbig014".into(),
            boot_id: "boot-1".into(),
            pid: 42,
            start_ticks: 123_456,
        };
        let rendered = "host=devbig014\nboot_id=boot-1\npid=42\nstart_ticks=123456\n";
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
