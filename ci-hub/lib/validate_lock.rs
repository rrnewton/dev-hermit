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
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus};
use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use thiserror::Error;

// Reuse the PURE, stateless primitives from landing_lock rather than copy tricky
// code. None of these carry a LandLockError or print a `landing-lock:` message,
// so they are safe to share verbatim across both locks.
use crate::landing_lock::{
    capture_and_freeze_residuals, current_host, enable_child_subreaper, exact_process_liveness,
    exit_status_code, heartbeat_test_helper_delay, print_cleanup_record, process_group_exists,
    process_start_ticks, reap_exited_children, remove_cleanup_record, signal_group,
    spawn_gated_child, suffix, verify_cleanup_record, write_cleanup_record, CleanupPhase,
    CleanupRecord, CleanupVerification, GatedChild, ProcessIdentity, ResidualCapture,
};

const DEFAULT_WAIT_SECONDS: u64 = 1_800;
const DEFAULT_HOLD_SECONDS: u64 = 900;
const POLL_SECONDS: u64 = 3;
const GUARD_WAIT_SECONDS: u64 = 30;
/// Box-exclusive cap. Raising this is UNPROVEN: detcore_misc false-FAILED
/// residual is monotonic in load (`experiments/multisect_detcore_misc_20260803`),
/// so >1 requires hermit-250 evidence, not a config pick.
const BOX_EXCLUSIVE_CAP: u64 = 1;
/// Hard ceiling on how long a `run` child may execute before it is killed and
/// cleanup is proved. A complete empty-domain proof releases the box; otherwise
/// it stays quarantined. A warm validate runs ~8-16 min; this default (3600s)
/// allows generous headroom while still bounding cleanup or quarantine — an
/// unbounded holder is unboxed compute and a head-of-line block.
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
/// Exit code reported when admission REFUSES a validate whose head predates a
/// fixed floor or the freshly fetched origin/main tip. Same 3 as an ownership
/// refusal: a refusal, not a crash.
const STALE_BASE_EXIT_CODE: i32 = 3;
/// Env override for the composite validation-admission command (space-split
/// argv). The target head is appended as `--head <sha>`. Defaults to the in-tree
/// `ci-hub/validate/preflight_validate.py`, which checks both fixed floors and
/// freshly fetched origin/main. Overridable so Rust tests need neither Python,
/// egress, nor a Hermit checkout.
const ADMIT_PREFLIGHT_CMD_ENV: &str = "CI_HUB_ADMIT_PREFLIGHT_CMD";

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
    /// Census an UNCENSUSED payload domain post-hoc, after the supervisor died
    /// before it could take one, so `reclaim-dead` can finish.
    CensusOrphanedDomain(CensusOrphanedDomainArgs),
    /// Acquire and run with a heartbeat + child-deadline; release after complete
    /// cleanup proof, otherwise retain a quarantine.
    Run(RunArgs),
}

/// Restated identity plus the one operator attestation that discharges an
/// UNCENSUSED quarantine. Every field is re-checked against the durable record;
/// a mismatch refuses, so this cannot be run blind against whatever happens to
/// be quarantined at the time.
#[derive(Args, Clone, Debug)]
pub struct CensusOrphanedDomainArgs {
    /// Must equal the recorded `agent` of the quarantined operation.
    #[arg(long)]
    pub agent: String,
    /// Must equal the recorded `operation` (`<kind>:<target>`).
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
    /// cgroup the payload can migrate into empty). This is the ONLY fact the
    /// kernel cannot answer once the subreaper anchor died; everything else is
    /// checked mechanically and is not attestable.
    #[arg(long)]
    pub attest_domain_empty: bool,
    /// The exact observations backing `--attest-domain-empty`. Recorded in the
    /// refusal/acceptance transcript so the attestation is auditable rather than
    /// anonymous. Must be non-empty.
    #[arg(long)]
    pub evidence: String,
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
    /// Retained only to give old callers a named refusal. Qualifying validation
    /// can no longer bypass base admission. Historical differential debugging
    /// must run outside the receipt-producing validate path.
    #[arg(long, default_value_t = false)]
    pub skip_base_check: bool,
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
    /// Kill the child if it runs longer than this many seconds. Release follows
    /// only after complete cleanup proof; otherwise the box remains quarantined.
    /// Must be positive: unbounded box-exclusive holders are forbidden.
    #[arg(long, default_value_t = DEFAULT_CHILD_DEADLINE_SECONDS)]
    pub child_deadline: u64,
    #[arg(long, default_value_t = BOX_EXCLUSIVE_CAP)]
    pub max: u64,
    /// Retained only to give old callers a named refusal. Qualifying validation
    /// can no longer bypass base admission. Historical differential debugging
    /// must run outside the receipt-producing validate path.
    #[arg(long, default_value_t = false)]
    pub skip_base_check: bool,
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
    /// Admission refused a validate whose head predates a fixed floor or the
    /// freshly fetched origin/main tip. The message names the exact base and
    /// remedy. This is mechanical REBASE-BEFORE-VALIDATE: a stale base never
    /// gets a slot.
    #[error("{0}")]
    StaleBase(String),
    #[error(
        "validate-lock: {operation}: process {pid} owns the supervised lease, not this process"
    )]
    ProcessNotOwner { operation: &'static str, pid: u32 },
    #[error("validate-lock: cannot reclaim lease: {0}")]
    ReclaimNotProven(String),
    #[error("validate-lock: cleanup quarantine: {0}")]
    CleanupQuarantined(String),
    /// A post-hoc census was requested but at least one MECHANICALLY checkable
    /// precondition failed. These are never attestable — an operator may only
    /// attest the one fact the kernel cannot answer once the subreaper anchor is
    /// gone (see `census_orphaned_domain`), never that a live process is dead.
    #[error("validate-lock: cannot census orphaned domain: {0}")]
    RecoveryNotProven(String),
}

impl ValidateLockError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::RenewNotOwner { .. }
            | Self::ReleaseNotOwner { .. }
            | Self::ProcessNotOwner { .. }
            | Self::ReclaimNotProven(_)
            | Self::RecoveryNotProven(_)
            | Self::CleanupQuarantined(_)
            | Self::GuardTimeout
            | Self::StaleBase(_)
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
    cleanup: PathBuf,
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
            cleanup: suffix(&lock, ".cleanup-required"),
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
            base_admission_check(root, &args.target, args.kind, args.skip_base_check)?;
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
        ValidateLockCommand::CensusOrphanedDomain(args) => lock.census_orphaned_domain(args),
        ValidateLockCommand::Run(args) => lock.run(args, root),
    }
}

/// "Derive don't pick": any cap other than 1 is refused before touching the lock.
fn reject_bad_max(max: u64) -> Result<(), ValidateLockError> {
    if max != BOX_EXCLUSIVE_CAP {
        return Err(ValidateLockError::BadMax);
    }
    Ok(())
}

/// A 40-char lowercase-hex commit SHA — the only target shape that can bind the
/// admission verdict to the receipt-producing child.
fn is_full_sha(target: &str) -> bool {
    target.len() == 40
        && target
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// MECHANICAL rebase-before-validate: refuse to admit a `Validate` unless its
/// exact head contains both every fixed floor and the freshly fetched
/// origin/main tip. Thus every qualifying receipt describes a state that can
/// land without another rebase. The refusal names the exact missing base and
/// remedy — a bare "refused" gets a gate disabled.
///
/// Non-Validate kinds pass through. Validate targets that are not exact SHAs and
/// the legacy `--skip-base-check` flag are refused: either would sever the
/// observable binding between the admission verdict and the validated commit.
/// The predicate is shelled out to `preflight_validate.py`, the same composite
/// authority used by all other ci-hub validation producers. Overridable via
/// `CI_HUB_ADMIT_PREFLIGHT_CMD` so tests need neither Python nor a checkout.
///
/// Fail-CLOSED: preflight exit 2 (REFUSED) and any other non-zero (ERROR: head
/// unresolved) both become `StaleBase`. An admission gate that admitted on "I
/// couldn't check" would be advisory, not mechanical.
fn base_admission_check(
    root: &Path,
    target: &str,
    kind: Kind,
    skip: bool,
) -> Result<(), ValidateLockError> {
    if kind != Kind::Validate {
        return Ok(()); // bench/diagnostic work is not landing evidence
    }
    if skip {
        return Err(ValidateLockError::StaleBase(format!(
            "REFUSE: --skip-base-check cannot authorize qualifying validation of \
             {target}. Rebase onto freshly fetched origin/main before validating. \
             Historical differential debugging is non-qualifying and must run \
             outside validate-run so it cannot mint landing evidence."
        )));
    }
    if !is_full_sha(target) {
        return Err(ValidateLockError::StaleBase(format!(
            "REFUSE: validation target {target:?} is not an exact lowercase \
             40-hex commit SHA, so base ancestry cannot be bound to the child. \
             Resolve the rebased PR head exactly before validating."
        )));
    }

    let mut argv: Vec<OsString> = match env::var(ADMIT_PREFLIGHT_CMD_ENV) {
        Ok(cmd) if !cmd.trim().is_empty() => cmd.split_whitespace().map(OsString::from).collect(),
        _ => vec![
            OsString::from("python3"),
            root.join("ci-hub/validate/preflight_validate.py")
                .into_os_string(),
        ],
    };
    argv.push(OsString::from("--head"));
    argv.push(OsString::from(target));

    let (program, rest) = argv.split_first().expect("argv is never empty");
    let output = match Command::new(program).args(rest).output() {
        Ok(o) => o,
        Err(err) => {
            return Err(ValidateLockError::StaleBase(format!(
                "validate-lock: validation admission check could not run \
                 ({err}); base for {target} is UNRESOLVED. Rebase onto current \
                 origin/main before validating."
            )));
        }
    };
    let code = output.status.code();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    match code {
        Some(0) => Ok(()),
        Some(2) => {
            // preflight prints its NAMED refuse line (with the rebase remedy) on
            // stdout; carry it verbatim so the operator sees the exact floor.
            let msg = if stdout.is_empty() {
                format!(
                    "REFUSE: base for {target} predates a rebase-base floor. \
                     Rebase onto current origin/main before validating."
                )
            } else {
                stdout
            };
            Err(ValidateLockError::StaleBase(msg))
        }
        other => {
            // ERROR (3) or an unexpected code: fail closed — an unresolved base
            // is treated as stale, not waved through.
            let detail = if !stderr.is_empty() {
                stderr
            } else if !stdout.is_empty() {
                stdout
            } else {
                format!("exit {other:?}")
            };
            Err(ValidateLockError::StaleBase(format!(
                "validate-lock: validation admission check for {target} did not \
                 resolve ({detail}); treating the base as STALE. Rebase onto \
                 current origin/main before validating."
            )))
        }
    }
}

impl ValidateLock {
    fn cleanup_verification(&self, holder: Option<&ValidateLockState>) -> CleanupVerification {
        let operation = holder.map(|holder| format!("{}:{}", holder.kind, holder.target));
        let expected = holder
            .zip(operation.as_deref())
            .map(|(holder, operation)| (holder.agent.as_str(), operation));
        verify_cleanup_record(&self.paths.cleanup, expected)
    }

    fn require_no_cleanup(
        &self,
        holder: Option<&ValidateLockState>,
    ) -> Result<(), ValidateLockError> {
        match self.cleanup_verification(holder) {
            CleanupVerification::None => Ok(()),
            CleanupVerification::Armed { reason, .. } => Err(
                ValidateLockError::CleanupQuarantined(format!("ARMED: {reason}")),
            ),
            CleanupVerification::Active { reason, .. } => Err(
                ValidateLockError::CleanupQuarantined(format!("ACTIVE: {reason}")),
            ),
            CleanupVerification::Uncensused { reason, .. } => Err(
                ValidateLockError::CleanupQuarantined(format!("UNCENSUSED: {reason}")),
            ),
            CleanupVerification::Recoverable { reason, .. } => Err(
                ValidateLockError::CleanupQuarantined(format!(
                    "payload absence is proven but explicit reclaim-dead recovery is required: {reason}"
                )),
            ),
            CleanupVerification::Unknown { reason, .. } => Err(
                ValidateLockError::CleanupQuarantined(format!("UNVERIFIABLE: {reason}")),
            ),
        }
    }

    fn arm_run(&self, agent: &str, kind: Kind, target: &str) -> Result<(), ValidateLockError> {
        let record = CleanupRecord::new(
            agent,
            format!("{}:{target}", kind.as_str()),
            CleanupPhase::Armed,
        )
        .map_err(|source| io_error("construct", &self.paths.cleanup, source))?;
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                ValidateLockError::InvalidState("cannot arm cleanup without a lock holder".into())
            })?;
            if holder.agent != agent || holder.kind != kind.as_str() || holder.target != target {
                return Err(ValidateLockError::InvalidState(
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
        kind: Kind,
        target: &str,
        expected: fn(&CleanupPhase) -> bool,
        next: CleanupPhase,
    ) -> Result<(), ValidateLockError> {
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                ValidateLockError::InvalidState("cleanup transition has no lock holder".into())
            })?;
            if holder.agent != agent || holder.kind != kind.as_str() || holder.target != target {
                return Err(ValidateLockError::InvalidState(
                    "cleanup transition does not match the current operation".into(),
                ));
            }
            let mut record = match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Armed { record, .. }
                | CleanupVerification::Active { record, .. }
                | CleanupVerification::Uncensused { record, .. }
                | CleanupVerification::Recoverable { record, .. } => record,
                CleanupVerification::None => {
                    return Err(ValidateLockError::InvalidState(
                        "cleanup transition has no durable authority".into(),
                    ))
                }
                CleanupVerification::Unknown { reason, .. } => {
                    return Err(ValidateLockError::CleanupQuarantined(format!(
                        "cannot transition unverifiable authority: {reason}"
                    )))
                }
            };
            if !expected(&record.phase) {
                return Err(ValidateLockError::InvalidState(format!(
                    "cleanup transition rejected phase {:?}",
                    record.phase
                )));
            }
            record.phase = next;
            write_cleanup_record(&self.paths.cleanup, &record)
                .map_err(|source| io_error("write", &self.paths.cleanup, source))
        })
    }

    fn publish_run(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        gated: &GatedChild,
    ) -> Result<(), ValidateLockError> {
        self.transition_run_cleanup(
            agent,
            kind,
            target,
            |phase| matches!(phase, CleanupPhase::Armed),
            CleanupPhase::Published {
                leader: gated.leader.clone(),
                pgid: gated.pgid,
            },
        )
    }

    fn clear_unstarted_run(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
    ) -> Result<(), ValidateLockError> {
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                ValidateLockError::InvalidState("unstarted cleanup has no lock holder".into())
            })?;
            if holder.agent != agent || holder.kind != kind.as_str() || holder.target != target {
                return Err(ValidateLockError::InvalidState(
                    "unstarted cleanup does not match current operation".into(),
                ));
            }
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Armed { .. } => {
                    self.assert_current_process_owner("clear unstarted run")?;
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))
                }
                other => Err(ValidateLockError::InvalidState(format!(
                    "unstarted cleanup requires armed authority, got {other:?}"
                ))),
            }
        })
    }

    fn record_residuals(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        pgid: u32,
        capture: ResidualCapture,
    ) -> Result<(), ValidateLockError> {
        self.transition_run_cleanup(
            agent,
            kind,
            target,
            |phase| matches!(phase, CleanupPhase::CensusPending { .. }),
            CleanupPhase::Residual {
                pgid,
                domain_complete: capture.complete,
                residuals: capture.identities,
            },
        )
    }

    fn begin_run_census(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        gated: &GatedChild,
    ) -> Result<(), ValidateLockError> {
        self.transition_run_cleanup(
            agent,
            kind,
            target,
            |phase| matches!(phase, CleanupPhase::Published { .. }),
            CleanupPhase::CensusPending {
                leader: gated.leader.clone(),
                pgid: gated.pgid,
            },
        )
    }

    fn clear_proven_run(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        pgid: u32,
    ) -> Result<(), ValidateLockError> {
        self.record_residuals(
            agent,
            kind,
            target,
            pgid,
            ResidualCapture {
                complete: true,
                identities: Vec::new(),
            },
        )?;
        self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                ValidateLockError::InvalidState("cleanup clear has no lock holder".into())
            })?;
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Recoverable { .. } => {
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))
                }
                other => Err(ValidateLockError::InvalidState(format!(
                    "cleanup clear requires proven absence, got {other:?}"
                ))),
            }
        })
    }

    fn renew_run_lease(
        &self,
        agent: &str,
        kind: Kind,
        target: &str,
        hold: u64,
    ) -> Result<(), ValidateLockError> {
        self.with_guard(|| {
            let holder = self
                .read_holder()?
                .ok_or_else(|| ValidateLockError::RenewNotOwner {
                    agent: agent.to_string(),
                })?;
            if holder.agent != agent || holder.kind != kind.as_str() || holder.target != target {
                return Err(ValidateLockError::RenewNotOwner {
                    agent: agent.to_string(),
                });
            }
            match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Active { record, .. }
                    if matches!(record.phase, CleanupPhase::Published { .. }) => {}
                other => {
                    return Err(ValidateLockError::CleanupQuarantined(format!(
                        "run heartbeat requires an active published domain, got {other:?}"
                    )))
                }
            }
            self.assert_current_process_owner("run heartbeat")?;
            heartbeat_test_helper_delay();
            self.write_holder(&new_holder(agent, kind, target, hold, None)?)
        })
    }

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
        let holder = self.read_holder()?;
        self.require_no_cleanup(holder.as_ref())?;
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
            self.require_no_cleanup(Some(&holder))?;
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
            let holder = self.read_holder()?;
            self.require_no_cleanup(holder.as_ref())?;
            let Some(holder) = holder else {
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
        // The authoritative owner PROCESS IDENTITY, read from the flock-guarded
        // owner sidecar (the ONLY writer is an actual `validate-lock run`/lease
        // acquisition). Consumers that must prove a run descends from the live
        // lease holder -- e.g. hermit validate.sh's admission guard -- MUST read
        // owner_pid/owner_boot_id/owner_start_ticks from HERE, never from an env
        // var or an agent-writable file, because only a real acquisition writes
        // this record. boot_id defeats reboots; start_ticks defeats PID reuse.
        let process_owner = self.read_process_owner()?;
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
                // Uncensused used to name NO action while `reclaim-dead` refused
                // it, which read as "wait" when nothing was ever coming. Name the
                // exit, with the identity already restated for the operator.
                if let CleanupPhase::Published { leader, pgid }
                | CleanupPhase::CensusPending { leader, pgid } = &record.phase
                {
                    println!(
                        "  recovery=ci-hub validate-lock census-orphaned-domain \
                         --agent {} --operation {} --leader {}:{} --pgid {pgid} \
                         --attest-domain-empty --evidence '<observations>'",
                        record.agent, record.operation, leader.pid, leader.start_ticks
                    );
                    println!(
                        "  recovery_precondition=confirm the payload's unit cgroup is absent/empty \
                         AND every cgroup it migrates into is empty; reclaim-dead alone CANNOT \
                         clear this state"
                    );
                }
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
            print_owner_identity(process_owner.as_ref());
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
                    print_owner_identity(process_owner.as_ref());
                    println!("  owner_process={}", render_liveness(&liveness));
                    println!("  secs_left={}", holder.expires_at - now);
                }
                Some(holder) if holder.live_at(now) => {
                    println!("HELD:");
                    for line in holder.render().lines() {
                        println!("  {line}");
                    }
                    print_owner_identity(process_owner.as_ref());
                    println!("  owner_process={}", render_liveness(&liveness));
                    println!("  secs_left={}", holder.expires_at - now);
                }
                Some(holder) => {
                    println!("LAPSED (reclaimable):");
                    for line in holder.render().lines() {
                        println!("  {line}");
                    }
                    print_owner_identity(process_owner.as_ref());
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

    fn reclaim_dead(&self) -> Result<i32, ValidateLockError> {
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
                        Err(ValidateLockError::ReclaimNotProven(reason))
                    }
                    CleanupVerification::Recoverable { .. } => {
                        Err(ValidateLockError::ReclaimNotProven(
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
                    return Err(ValidateLockError::ReclaimNotProven(reason));
                }
                CleanupVerification::Recoverable { .. } | CleanupVerification::None => {}
            }
            match self.owner_liveness()? {
                OwnerLiveness::Dead(reason) => {
                    remove_if_exists(&self.paths.lock)?;
                    remove_if_exists(&self.paths.owner)?;
                    remove_cleanup_record(&self.paths.cleanup)
                        .map_err(|source| io_error("remove", &self.paths.cleanup, source))?;
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

    /// Take the residual census that a dead supervisor never got to take, so the
    /// existing `reclaim-dead` path can finish.
    ///
    /// WHY THIS EXISTS. `capture_and_freeze_residuals` enumerates the payload
    /// domain with `scan_descendants(supervisor_pid)` — a PPID-tree walk that is
    /// only exhaustive because `run` made the supervisor a
    /// `PR_SET_CHILD_SUBREAPER`, so even a `setsid`'d double-fork reparents back
    /// to it. When the supervisor itself dies (e.g. systemd TERMs the whole unit
    /// cgroup) that anchor is destroyed: survivors reparent to `systemd --user`
    /// or pid 1 and NO ppid walk can reconstruct the domain. So `Published` ->
    /// `Uncensused` became a one-way door — `reclaim_dead` refuses `Uncensused`
    /// before it ever checks owner liveness, and the only writer of a complete
    /// census lives inside `run`. The box stayed refused for every agent.
    ///
    /// WHAT IS AND IS NOT ATTESTABLE. Everything the kernel can still answer is
    /// checked mechanically here and REFUSES on failure — the restated identity,
    /// a proven-dead supervisor, the leader's exact pid+start_ticks absence, and
    /// an absent process group. None of those may be attested away. The single
    /// residual unknown is whether a descendant ESCAPED the recorded process
    /// group before dying out of view: `pgid` membership does not survive
    /// `setsid()`, so an absent pgid is necessary but not sufficient. Cgroup
    /// membership DOES survive `setsid()` and reparenting, so a
    /// supervisor-independent census is possible — but the version-2 record
    /// carries no cgroup anchor to check it against. Until it does (see the
    /// follow-up that records the payload cgroup at publish time), that one fact
    /// is supplied by `--attest-domain-empty` with the observations in
    /// `--evidence`, which are echoed into the transcript.
    fn census_orphaned_domain(
        &self,
        args: CensusOrphanedDomainArgs,
    ) -> Result<i32, ValidateLockError> {
        if args.evidence.trim().is_empty() {
            return Err(ValidateLockError::RecoveryNotProven(
                "--evidence must record the observations backing --attest-domain-empty; \
                 an anonymous attestation is not auditable"
                    .into(),
            ));
        }
        let restated = parse_leader_identity(&args.leader)?;
        let (leader, pgid) = self.with_guard(|| {
            let holder = self.read_holder()?.ok_or_else(|| {
                ValidateLockError::RecoveryNotProven(
                    "no lock holder; a census only discharges a quarantine bound to one".into(),
                )
            })?;

            // (1) The quarantine must be exactly the one this path addresses.
            let record = match self.cleanup_verification(Some(&holder)) {
                CleanupVerification::Uncensused { record, .. } => record,
                CleanupVerification::None => {
                    return Err(ValidateLockError::RecoveryNotProven(
                        "no cleanup authority is quarantined; nothing to census".into(),
                    ))
                }
                CleanupVerification::Recoverable { reason, .. } => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "payload absence is ALREADY proven; run reclaim-dead instead: {reason}"
                    )))
                }
                CleanupVerification::Active { reason, .. } => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "payload identities are STILL ALIVE; a census cannot bury a live \
                         domain: {reason}"
                    )))
                }
                CleanupVerification::Armed { reason, .. } => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "cleanup is only ARMED, so no payload was ever published and there is \
                         no domain to census: {reason}"
                    )))
                }
                CleanupVerification::Unknown { reason, .. } => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "cleanup authority is UNVERIFIABLE; refusing to census it: {reason}"
                    )))
                }
            };

            let (leader, pgid) = match &record.phase {
                CleanupPhase::Published { leader, pgid }
                | CleanupPhase::CensusPending { leader, pgid } => (leader.clone(), *pgid),
                other => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "uncensused authority has unexpected phase {other:?}"
                    )))
                }
            };

            // (2) The caller must restate the recorded identity exactly, so this
            // cannot be aimed blind at whatever is quarantined right now.
            if record.agent != args.agent {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "restated agent {:?} does not match recorded agent {:?}",
                    args.agent, record.agent
                )));
            }
            if record.operation != args.operation {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "restated operation {:?} does not match recorded operation {:?}",
                    args.operation, record.operation
                )));
            }
            if leader != restated {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "restated leader {}:{} does not match recorded leader {}:{}",
                    restated.pid, restated.start_ticks, leader.pid, leader.start_ticks
                )));
            }
            if pgid != args.pgid {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "restated pgid {} does not match recorded pgid {pgid}",
                    args.pgid
                )));
            }

            // (3) A LIVE supervisor owns its own census; never race it.
            match self.owner_liveness()? {
                OwnerLiveness::Dead(_) => {}
                OwnerLiveness::Alive => {
                    return Err(ValidateLockError::RecoveryNotProven(
                        "the recorded supervisor process is ALIVE and owns this census; \
                         refusing to take it out from under a running validate"
                            .into(),
                    ))
                }
                OwnerLiveness::Unknown(reason) => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "supervisor liveness is UNVERIFIABLE, so its death is not proven: {reason}"
                    )))
                }
            }

            // (4) Re-assert the mechanically checkable half of absence at the
            // instant of the write, under the guard — not from the earlier
            // classification, which was computed before the caller was admitted.
            match exact_process_liveness(&leader) {
                Ok(false) => {}
                Ok(true) => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "recorded leader {}:{} is ALIVE",
                        leader.pid, leader.start_ticks
                    )))
                }
                Err(reason) => {
                    return Err(ValidateLockError::RecoveryNotProven(format!(
                        "cannot verify recorded leader absence: {reason}"
                    )))
                }
            }
            if process_group_exists(pgid) {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "recorded process group {pgid} is still populated"
                )));
            }

            // (5) Only now is the attestation consumed, and only for the one
            // fact no mechanical check above can supply.
            if !args.attest_domain_empty {
                return Err(ValidateLockError::RecoveryNotProven(format!(
                    "every mechanical precondition passes (supervisor dead, leader {}:{} absent, \
                     pgid {pgid} absent), but an escaped descendant cannot be excluded from the \
                     record alone. Re-run with --attest-domain-empty --evidence '<observations>' \
                     after confirming the payload's unit cgroup is absent/empty AND every cgroup \
                     the payload migrates into is empty.",
                    leader.pid, leader.start_ticks
                )));
            }

            let mut recovered = record.clone();
            recovered.phase = CleanupPhase::Residual {
                pgid,
                domain_complete: true,
                residuals: Vec::new(),
            };
            write_cleanup_record(&self.paths.cleanup, &recovered)
                .map_err(|source| io_error("write", &self.paths.cleanup, source))?;
            Ok((leader, pgid))
        })?;
        eprintln!(
            "validate-lock: CENSUSED orphaned domain agent={} operation={} leader={}:{} pgid={pgid}; \
             supervisor proven dead, leader and process group proven absent, empty domain attested: {}",
            args.agent, args.operation, leader.pid, leader.start_ticks, args.evidence.trim()
        );
        eprintln!(
            "validate-lock: quarantine is now RECOVERABLE; run `validate-lock reclaim-dead` to \
             release the box."
        );
        Ok(0)
    }

    fn run(&self, args: RunArgs, root: &Path) -> Result<i32, ValidateLockError> {
        if args.child.is_empty() {
            return Err(ValidateLockError::EmptyChild);
        }
        if args.child_deadline == 0 {
            return Err(ValidateLockError::UnboundedChildDeadline);
        }
        reject_bad_max(args.max)?;
        enable_child_subreaper().map_err(|source| {
            io_error("enable child subreaper at", Path::new("/proc/self"), source)
        })?;

        // MECHANICAL rebase-before-validate at the admission chokepoint: refuse a
        // stale-base validate BEFORE spending a ~17-min box slot. Refuse cleanly
        // with STALE_BASE_EXIT_CODE (mirroring the --no-wait Held refusal) so a
        // wrapper reads a distinct, non-crashing exit and can surface the remedy.
        if let Err(err) = base_admission_check(root, &args.target, args.kind, args.skip_base_check)
        {
            match err {
                ValidateLockError::StaleBase(msg) => {
                    eprintln!("{msg}");
                    return Ok(STALE_BASE_EXIT_CODE);
                }
                other => return Err(other),
            }
        }

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

        // Install AFTER acquisition (so a stop during a long FIFO wait keeps its
        // default, immediate disposition — there is no cleanup record yet, and a
        // dead owner with no record is plain `reclaim-dead` territory) and BEFORE
        // `arm_run` writes the first durable cleanup authority, so every state
        // that `reclaim-dead` refuses is covered by a census on the way out.
        install_termination_handlers().map_err(|source| {
            io_error(
                "install termination handlers at",
                Path::new("/proc/self"),
                source,
            )
        })?;
        self.arm_run(&args.agent, args.kind, &args.target)?;
        #[cfg(test)]
        if crate::landing_lock::take_run_crash_hook(
            &format!("{}:{}", args.kind.as_str(), args.target),
            crate::landing_lock::RunCrashPoint::AfterArm,
        ) {
            return Err(ValidateLockError::CleanupQuarantined(
                "test-injected supervisor crash after arm".into(),
            ));
        }
        let owner_file = self.paths.owner.clone();
        let mut gated = match spawn_gated_child(&args.child[0], &args.child[1..], |command| {
            // The child may record `concurrent_validates=0` only when it can
            // prove it is descended from this live, process-bound lock owner.
            command
                .env(
                    "CI_HUB_VALIDATE_LOCK_OWNER_PID",
                    std::process::id().to_string(),
                )
                .env("CI_HUB_VALIDATE_LOCK_OWNER_FILE", &owner_file);
        }) {
            Ok(gated) => gated,
            Err(source) => {
                self.clear_unstarted_run(&args.agent, args.kind, &args.target)?;
                self.release(&args.agent, true)?;
                return Err(ValidateLockError::Io {
                    action: "launch start-gated child from",
                    path: PathBuf::from(&args.child[0]),
                    source,
                });
            }
        };
        #[cfg(test)]
        if crate::landing_lock::take_run_crash_hook(
            &format!("{}:{}", args.kind.as_str(), args.target),
            crate::landing_lock::RunCrashPoint::AfterSpawn,
        ) {
            return Err(ValidateLockError::CleanupQuarantined(
                "test-injected supervisor crash after spawn".into(),
            ));
        }
        self.publish_run(&args.agent, args.kind, &args.target, &gated)?;
        gated.release().map_err(|source| {
            io_error("release child start gate at", &self.paths.cleanup, source)
        })?;
        #[cfg(test)]
        if crate::landing_lock::take_run_crash_hook(
            &format!("{}:{}", args.kind.as_str(), args.target),
            crate::landing_lock::RunCrashPoint::AfterPublish,
        ) {
            return Err(ValidateLockError::CleanupQuarantined(
                "test-injected supervisor crash after publication".into(),
            ));
        }

        let (stop_tx, stop_rx) = mpsc::channel();
        let heartbeat_paths = self.paths.clone();
        let heartbeat_agent = args.agent.clone();
        let heartbeat_kind = args.kind;
        let heartbeat_target = args.target.clone();
        let heartbeat_hold = args.hold;
        let heartbeat = thread::spawn(move || {
            let heartbeat_lock = ValidateLock {
                paths: heartbeat_paths,
            };
            let interval = Duration::from_secs((heartbeat_hold / 3).max(1));
            while stop_rx.recv_timeout(interval).is_err() {
                if heartbeat_lock
                    .renew_run_lease(
                        &heartbeat_agent,
                        heartbeat_kind,
                        &heartbeat_target,
                        heartbeat_hold,
                    )
                    .is_err()
                {
                    break;
                }
            }
        });

        let outcome = supervise_child(&mut gated.child, args.child_deadline, &args.target);
        self.begin_run_census(&args.agent, args.kind, &args.target, &gated)?;
        let _ = stop_tx.send(());
        let _ = heartbeat.join();
        reap_exited_children();
        let pgid = outcome.pgid();
        let capture = capture_and_freeze_residuals(std::process::id());
        let domain_empty =
            !process_group_exists(pgid) && capture.complete && capture.identities.is_empty();
        if !domain_empty {
            let residual_count = capture.identities.len();
            self.record_residuals(&args.agent, args.kind, &args.target, pgid, capture)?;
            return Err(ValidateLockError::CleanupQuarantined(format!(
                "completed process group {pgid} did not yield an empty payload domain; {residual_count} exact residual identities recorded and lock retained"
            )));
        }
        self.clear_proven_run(&args.agent, args.kind, &args.target, pgid)?;
        self.release(&args.agent, true)?;
        match outcome {
            ChildOutcome::Exited { status, pgid } => {
                debug_assert_eq!(pgid, gated.pgid);
                Ok(exit_status_code(status))
            }
            ChildOutcome::TimedOut { pgid } => {
                debug_assert_eq!(pgid, gated.pgid);
                eprintln!(
                    "validate-lock: ABANDON {}: child exceeded --child-deadline {}s; \
                     killed the subtree and RELEASED the box so the FIFO can proceed.",
                    args.target, args.child_deadline
                );
                Ok(CHILD_DEADLINE_EXIT_CODE)
            }
            ChildOutcome::Signalled { pgid, signal } => {
                debug_assert_eq!(pgid, gated.pgid);
                eprintln!(
                    "validate-lock: ABANDON {}: supervisor was signalled ({signal}); \
                     killed the subtree, PROVED the payload domain empty, and RELEASED the box \
                     so the FIFO can proceed.",
                    args.target
                );
                // Same convention as `exit_status_code` for a signal death, so a
                // stopped supervisor is not mistaken for a payload exit code.
                Ok(128 + signal)
            }
            ChildOutcome::Uncertain { reason, .. } => Err(ValidateLockError::InvalidState(
                format!("child supervision failed after payload domain was proven empty: {reason}"),
            )),
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

/// Parse a restated `<pid>:<start_ticks>` payload-leader identity. `start_ticks`
/// is what defeats PID reuse, so a bare pid is refused rather than tolerated.
fn parse_leader_identity(value: &str) -> Result<ProcessIdentity, ValidateLockError> {
    let refuse = || {
        ValidateLockError::RecoveryNotProven(format!(
            "--leader must be <pid>:<start_ticks> exactly as `status` prints it, got {value:?}"
        ))
    };
    let (pid, start_ticks) = value.trim().split_once(':').ok_or_else(refuse)?;
    Ok(ProcessIdentity {
        pid: pid.parse().map_err(|_| refuse())?,
        start_ticks: start_ticks.parse().map_err(|_| refuse())?,
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

/// Emit the authoritative owner process identity in a stable, greppable form so
/// a consumer can bind admission to *actually descending from the live lease
/// owner*. Only a real lease acquisition writes the owner sidecar under the
/// flock guard, so these lines cannot be produced without holding the lease --
/// unlike an env var or a hand-written file, which any agent can forge. A legacy
/// or manual lease with no sidecar prints nothing here, so a consumer that
/// requires owner_pid will correctly refuse to treat it as admission.
fn print_owner_identity(owner: Option<&ProcessOwner>) {
    if let Some(owner) = owner {
        println!("  owner_pid={}", owner.pid);
        println!("  owner_boot_id={}", owner.boot_id);
        println!("  owner_start_ticks={}", owner.start_ticks);
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
    Exited { status: ExitStatus, pgid: u32 },
    TimedOut { pgid: u32 },
    /// The SUPERVISOR was signalled (systemd stopping the unit TERMs the whole
    /// control group, so this is the common death path — not an exotic one).
    Signalled { pgid: u32, signal: i32 },
    Uncertain { pgid: u32, reason: String },
}

impl ChildOutcome {
    fn pgid(&self) -> u32 {
        match self {
            Self::Exited { pgid, .. }
            | Self::TimedOut { pgid }
            | Self::Signalled { pgid, .. }
            | Self::Uncertain { pgid, .. } => *pgid,
        }
    }
}

/// First termination signal delivered to the supervisor, or 0. Written ONLY by
/// `record_termination_signal`.
static SUPERVISOR_TERMINATION_SIGNAL: AtomicI32 = AtomicI32::new(0);

/// Async-signal-safe handler: one lock-free atomic CAS, nothing else. No
/// allocation, no libc call, no I/O — everything real happens back in
/// `supervise_child`, which polls this flag.
extern "C" fn record_termination_signal(signal: libc::c_int) {
    let _ = SUPERVISOR_TERMINATION_SIGNAL.compare_exchange(
        0,
        signal as i32,
        Ordering::SeqCst,
        Ordering::SeqCst,
    );
}

/// Replace the DEFAULT disposition of the termination signals with a recorder,
/// so the supervisor survives long enough to census its payload domain.
///
/// This is the fix for the fleet-wide wedge of 2026-08-06: `validate-lock run`
/// had no handler, so when systemd stopped the transient unit (`KillMode`
/// defaults to `control-group`, TERMing every process in the cgroup at once) the
/// supervisor died instantly inside `supervise_child` and never reached
/// `begin_run_census`/`capture_and_freeze_residuals`. The record was left at
/// `phase=published`, which `verify_cleanup_record` classifies as `Uncensused` —
/// a state `reclaim_dead` refuses and, because the census can only be taken from
/// the live subreaper, nothing could ever discharge. Every agent's validate was
/// refused until it was cleared by hand.
///
/// SIGKILL and SIGSTOP remain uncatchable, so this narrows the window rather
/// than closing it; `census-orphaned-domain` covers the residue. systemd's
/// default `TimeoutStopSec` (90s) is far longer than a census (~11s worst case:
/// a 5s TERM grace, a 5s KILL settle, then four freeze iterations), so the
/// common path now completes.
fn install_termination_handlers() -> io::Result<()> {
    for signal in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
        // SAFETY: zeroed sigaction is a valid empty set; we install a plain
        // handler with no flags and read back nothing.
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = record_termination_signal as usize;
        // SAFETY: sigemptyset initialises the mask field we just zeroed.
        unsafe { libc::sigemptyset(&mut action.sa_mask) };
        // SAFETY: `action` is fully initialised and outlives the call.
        if unsafe { libc::sigaction(signal, &action, std::ptr::null_mut()) } != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

/// Wait for `child`, killing its process group if it runs longer than
/// `deadline_secs`. Copied (not reused) from landing_lock so the deadline-kill
/// messages carry the `validate-lock:` prefix; `signal_group` itself is shared.
fn supervise_child(child: &mut Child, deadline_secs: u64, target: &str) -> ChildOutcome {
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
            Err(source) => match child.wait() {
                Ok(status) => {
                    return ChildOutcome::Exited {
                        status,
                        pgid: child.id(),
                    }
                }
                Err(_) => {
                    eprintln!("validate-lock: cannot wait on child: {source}");
                    return ChildOutcome::Uncertain {
                        pgid: child.id(),
                        reason: source.to_string(),
                    };
                }
            },
        }
        // A termination signal aimed at the SUPERVISOR must still fall through to
        // the census below, never skip it. Check before the deadline so a stop
        // during the final poll is not misreported as a deadline breach.
        let signal = SUPERVISOR_TERMINATION_SIGNAL.load(Ordering::SeqCst);
        if signal != 0 {
            let pgid = child.id();
            eprintln!(
                "validate-lock: supervisor received signal {signal} while running {target}; \
                 terminating the payload subtree and censusing it before releasing the box."
            );
            terminate_child_group(child, target);
            return ChildOutcome::Signalled { pgid, signal };
        }
        if Instant::now() >= deadline {
            let pgid = child.id();
            terminate_child_group(child, target);
            return ChildOutcome::TimedOut { pgid };
        }
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
}

/// SIGTERM then (after a grace period) SIGKILL the child's process group and reap
/// the direct child. `signal_group` is reused from landing_lock.
fn terminate_child_group(child: &mut Child, target: &str) {
    let pgid = child.id();
    let group = format!("-{pgid}");
    eprintln!("validate-lock: child-deadline reached for {target}; SIGTERM process group {group}");
    signal_group(libc::SIGTERM, pgid);
    let grace = Instant::now() + Duration::from_secs(CHILD_TERM_GRACE_SECONDS);
    while Instant::now() < grace {
        thread::sleep(Duration::from_millis(CHILD_POLL_MILLIS));
    }
    eprintln!("validate-lock: grace expired for {target}; SIGKILL process group {group}");
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::landing_lock::{exact_process_liveness, signal_exact_process, ProcessIdentity};
    use std::os::unix::process::ExitStatusExt;

    const HARD_DEATH_ROOT_ENV: &str = "CI_HUB_VALIDATE_HARD_DEATH_ROOT";
    const HARD_DEATH_POINT_ENV: &str = "CI_HUB_VALIDATE_HARD_DEATH_POINT";

    fn paths_at(root: &Path) -> LockPaths {
        let lock = root.join(".validate-lock");
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
            "ci-hub-validate-lock-{name}-{}-{}",
            std::process::id(),
            epoch_seconds().unwrap()
        ));
        fs::create_dir_all(&root).unwrap();
        paths_at(&root)
    }

    // 1. Byte round-trip for the holder (kind+target) and a queue entry.
    #[test]
    fn holder_and_queue_round_trip() {
        let holder = ValidateLockState {
            agent: "hermit-247".into(),
            kind: "validate".into(),
            target: "0123456789abcdef0123456789abcdef01234567".into(),
            host: "testhost".into(),
            acquired_at: 100,
            acquired_human: "1970-01-01T00:01:40+0000".into(),
            expires_at: 1_000,
            reclaimed_from: Some("hermit-opt".into()),
        };
        let rendered = "agent=hermit-247\nkind=validate\ntarget=0123456789abcdef0123456789abcdef01234567\nhost=testhost\nacquired_at=100\nacquired_human=1970-01-01T00:01:40+0000\nexpires_at=1000\nreclaimed_from=hermit-opt\n";
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

    // 2. N=3 sequential box-exclusive jobs all admitted, lock ends FREE.
    #[test]
    fn positive_non_starvation_three_sequential() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("three-sequential");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        for i in 0..3 {
            let code = lock
                .run(
                    RunArgs {
                        agent: format!("validate-agent-{i}"),
                        kind: Kind::Bench,
                        target: format!("sha-{i}"),
                        no_wait: false,
                        wait: 0,
                        hold: 30,
                        child_deadline: 30,
                        max: 1,
                        skip_base_check: false,
                        child: vec![OsString::from("/bin/true")],
                    },
                    paths.lock.parent().unwrap(),
                )
                .unwrap();
            assert_eq!(code, 0, "sequential validate {i} should be admitted");
            assert!(
                lock.read_holder().unwrap().is_none(),
                "lock must be FREE after validate {i}"
            );
        }
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn run_child_receives_process_bound_exclusivity_proof() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("child-proof");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let command = "test \"$CI_HUB_VALIDATE_LOCK_OWNER_PID\" = \"$PPID\" \
            && test -r \"$CI_HUB_VALIDATE_LOCK_OWNER_FILE\" \
            && test \"$(sed -n 's/^pid=//p' \"$CI_HUB_VALIDATE_LOCK_OWNER_FILE\")\" = \"$PPID\"";
        let code = lock
            .run(
                RunArgs {
                    agent: "proof-agent".into(),
                    kind: Kind::Bench,
                    target: "proof-sha".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    max: 1,
                    skip_base_check: false,
                    child: vec![
                        OsString::from("/bin/sh"),
                        OsString::from("-c"),
                        OsString::from(command),
                    ],
                },
                paths.lock.parent().unwrap(),
            )
            .unwrap();
        assert_eq!(code, 0, "child must verify the live wrapper owner proof");
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
            .run(
                RunArgs {
                    agent: "agent2".into(),
                    kind: Kind::Bench,
                    target: "sha2".into(),
                    no_wait: true,
                    wait: 0,
                    hold: 60,
                    child_deadline: 30,
                    max: 1,
                    skip_base_check: false,
                    child: vec![OsString::from("/bin/true")],
                },
                paths.lock.parent().unwrap(),
            )
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
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("child-deadline");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let started = Instant::now();
        let code = lock
            .run(
                RunArgs {
                    agent: "stuck-validate".into(),
                    kind: Kind::Bench,
                    target: "sha-stuck".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 1,
                    max: 1,
                    skip_base_check: false,
                    child: vec![OsString::from("sleep"), OsString::from("120")],
                },
                paths.lock.parent().unwrap(),
            )
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

    #[test]
    fn run_kills_stubborn_descendant_before_releasing_box() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("stubborn-descendant");
        let lock = ValidateLock {
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
            .run(
                RunArgs {
                    agent: "stubborn-validate".into(),
                    kind: Kind::Validate,
                    target: "sha-stubborn".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 1,
                    max: 1,
                    skip_base_check: false,
                    child: vec![
                        OsString::from("/bin/bash"),
                        OsString::from("-c"),
                        OsString::from(script),
                        OsString::from("stubborn-descendant"),
                        identity_path.clone().into_os_string(),
                    ],
                },
                paths.lock.parent().unwrap(),
            )
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
            "exact descendant {pid}@{start_ticks} survived box release"
        );
        assert!(!process_group_exists(pgid));
        assert!(lock.read_holder().unwrap().is_none());
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn escaped_payload_quarantine_refuses_consumers_until_exact_recovery() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("escaped-quarantine");
        let lock = ValidateLock {
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
            .run(
                RunArgs {
                    agent: "escaped-validate".into(),
                    kind: Kind::Validate,
                    target: "sha-escaped".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 1,
                    max: 1,
                    skip_base_check: false,
                    child: vec![
                        OsString::from("/bin/bash"),
                        OsString::from("-c"),
                        OsString::from(script),
                        OsString::from("escaped-leader"),
                        identity_path.clone().into_os_string(),
                    ],
                },
                paths.lock.parent().unwrap(),
            )
            .unwrap_err();
        assert!(matches!(
            run_error,
            ValidateLockError::CleanupQuarantined(_)
        ));

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

        assert!(matches!(
            lock.acquire("replacement-validate", Kind::Validate, "sha-next", 0, 30),
            Err(ValidateLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.renew("escaped-validate", 30, false),
            Err(ValidateLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.release("escaped-validate", false),
            Err(ValidateLockError::CleanupQuarantined(_))
        ));
        let mut owner = lock.read_process_owner().unwrap().unwrap();
        owner.pid = u32::MAX;
        write_truncated(&paths.owner, owner.render().as_bytes()).unwrap();
        assert!(matches!(
            lock.acquire("replacement-validate", Kind::Validate, "sha-next", 0, 30),
            Err(ValidateLockError::CleanupQuarantined(_))
        ));
        assert!(matches!(
            lock.reclaim_dead(),
            Err(ValidateLockError::ReclaimNotProven(_))
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
        assert!(matches!(
            lock.acquire("replacement-validate", Kind::Validate, "sha-next", 0, 30),
            Err(ValidateLockError::CleanupQuarantined(_))
        ));

        assert_eq!(lock.reclaim_dead().unwrap(), 0);
        assert!(!paths.cleanup.exists());
        assert_eq!(
            lock.acquire("replacement-validate", Kind::Validate, "sha-next", 0, 30)
                .unwrap(),
            0
        );
        lock.release("replacement-validate", false).unwrap();
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
    }

    #[test]
    fn hard_death_run_subprocess_entry() {
        let Some(root) = env::var_os(HARD_DEATH_ROOT_ENV).map(PathBuf::from) else {
            return;
        };
        let point = match env::var(HARD_DEATH_POINT_ENV).as_deref() {
            Ok("after-arm") => crate::landing_lock::RunCrashPoint::AfterArm,
            Ok("after-spawn") => crate::landing_lock::RunCrashPoint::AfterSpawn,
            other => panic!("invalid hard-death point {other:?}"),
        };
        let paths = paths_at(&root);
        let ready = root.join("supervisor-ready");
        let marker = root.join("payload-started");
        let target = "hard-death";
        crate::landing_lock::install_run_hard_death_hook(
            &format!("validate:{target}"),
            point,
            ready,
        );
        let result = ValidateLock { paths }.run(
            RunArgs {
                agent: "hard-death-validate".into(),
                kind: Kind::Validate,
                target: target.into(),
                no_wait: false,
                wait: 0,
                hold: 30,
                child_deadline: 30,
                max: 1,
                skip_base_check: true,
                child: vec![
                    OsString::from("/bin/sh"),
                    OsString::from("-c"),
                    OsString::from("printf started > \"$1\"; while :; do sleep 60; done"),
                    OsString::from("payload"),
                    marker.into_os_string(),
                ],
            },
            &root,
        );
        panic!("hard-death subprocess resumed instead of being SIGKILLed: {result:?}");
    }

    #[test]
    fn hard_death_before_publication_retains_armed_barrier() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        for point_name in ["after-arm", "after-spawn"] {
            let paths = temp_paths(&format!("hard-death-{point_name}"));
            let root = paths.lock.parent().unwrap().to_path_buf();
            let ready = root.join("supervisor-ready");
            let marker = root.join("payload-started");
            let mut supervisor = Command::new(env::current_exe().unwrap())
                .arg("--exact")
                .arg("validate_lock::tests::hard_death_run_subprocess_entry")
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

            let lock = ValidateLock {
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

            assert!(matches!(
                lock.acquire("replacement-validate", Kind::Validate, "replacement", 0, 30),
                Err(ValidateLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.reclaim_dead(),
                Err(ValidateLockError::ReclaimNotProven(_))
            ));
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn crash_windows_retain_armed_or_uncensused_barrier_after_owner_death() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        for (index, point) in [
            crate::landing_lock::RunCrashPoint::AfterArm,
            crate::landing_lock::RunCrashPoint::AfterSpawn,
            crate::landing_lock::RunCrashPoint::AfterPublish,
        ]
        .into_iter()
        .enumerate()
        {
            let paths = temp_paths(&format!("crash-window-{index}"));
            let lock = ValidateLock {
                paths: paths.clone(),
            };
            let marker = paths.lock.parent().unwrap().join("payload-started");
            let target = format!("crash-{index}");
            crate::landing_lock::install_run_crash_hook(&format!("validate:{target}"), point);
            let error = lock
                .run(
                    RunArgs {
                        agent: "crash-validate".into(),
                        kind: Kind::Validate,
                        target: target.clone(),
                        no_wait: false,
                        wait: 0,
                        hold: 30,
                        child_deadline: 30,
                        max: 1,
                        skip_base_check: true,
                        child: vec![
                            OsString::from("/bin/sh"),
                            OsString::from("-c"),
                            OsString::from("printf started > \"$1\"; while :; do sleep 60; done"),
                            OsString::from("payload"),
                            marker.clone().into_os_string(),
                        ],
                    },
                    paths.lock.parent().unwrap(),
                )
                .unwrap_err();
            assert!(matches!(error, ValidateLockError::CleanupQuarantined(_)));

            let record =
                CleanupRecord::parse(&fs::read_to_string(&paths.cleanup).unwrap()).unwrap();
            let published = match (&point, &record.phase) {
                (
                    crate::landing_lock::RunCrashPoint::AfterArm
                    | crate::landing_lock::RunCrashPoint::AfterSpawn,
                    CleanupPhase::Armed,
                ) => None,
                (
                    crate::landing_lock::RunCrashPoint::AfterPublish,
                    CleanupPhase::Published { leader, pgid },
                ) => Some((leader.clone(), *pgid)),
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
            assert!(matches!(
                lock.acquire("replacement-validate", Kind::Validate, "replacement", 0, 30),
                Err(ValidateLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.renew("crash-validate", 30, false),
                Err(ValidateLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.release("crash-validate", false),
                Err(ValidateLockError::CleanupQuarantined(_))
            ));
            assert!(matches!(
                lock.reclaim_dead(),
                Err(ValidateLockError::ReclaimNotProven(_))
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
                    Err(ValidateLockError::ReclaimNotProven(_))
                ));
            }
            let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
        }
    }

    #[test]
    fn normal_leader_exit_cannot_clear_an_escaped_payload_barrier() {
        let _domain_guard = crate::landing_lock::process_domain_test_guard();
        let paths = temp_paths("normal-exit-escaped");
        let lock = ValidateLock {
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
            .run(
                RunArgs {
                    agent: "normal-exit-validate".into(),
                    kind: Kind::Validate,
                    target: "normal-exit".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    max: 1,
                    skip_base_check: true,
                    child: vec![
                        OsString::from("/bin/sh"),
                        OsString::from("-c"),
                        OsString::from(script),
                        OsString::from("normal-leader"),
                        identity_path.clone().into_os_string(),
                    ],
                },
                paths.lock.parent().unwrap(),
            )
            .unwrap_err();
        assert!(matches!(error, ValidateLockError::CleanupQuarantined(_)));
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

    // 5. --max != 1 is rejected before acquiring; the box stays FREE.
    #[test]
    fn max_greater_than_one_rejected() {
        let paths = temp_paths("bad-max");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let error = lock
            .run(
                RunArgs {
                    agent: "greedy".into(),
                    kind: Kind::Bench,
                    target: "sha-greedy".into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    max: 2,
                    skip_base_check: false,
                    child: vec![OsString::from("/bin/true")],
                },
                paths.lock.parent().unwrap(),
            )
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

    // --- composite admission gate (mechanical REBASE-BEFORE-VALIDATE) ---
    // These serialize on ENV_LOCK because they mutate a process-global env var.
    // Bench-kind tests above never read the env, so they are unaffected.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn write_stub(name: &str, body: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let path = env::temp_dir().join(format!(
            "ci-hub-preflight-stub-{name}-{}-{}",
            std::process::id(),
            epoch_seconds().unwrap()
        ));
        fs::write(&path, body).unwrap();
        let mut perm = fs::metadata(&path).unwrap().permissions();
        perm.set_mode(0o755);
        fs::set_permissions(&path, perm).unwrap();
        path
    }

    const HEX_SHA: &str = "0123456789abcdef0123456789abcdef01234567";

    // NEGATIVE direction: a floor-blocked head is REFUSED, and the refusal
    // carries the NAMED remedy (not a bare "refused"). This is the mutation that
    // proves the gate fires — swap the stub to exit 0 and it must stop refusing.
    #[test]
    fn base_admission_refuses_stale_and_names_remedy() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let stub = write_stub(
            "refuse",
            "#!/bin/sh\necho 'REFUSE: head 01234567 predates merge-gate floor \
             c369be3f; Rebase onto current origin/main (>= c369be3f) before \
             validating/landing.'\nexit 2\n",
        );
        env::set_var(ADMIT_PREFLIGHT_CMD_ENV, &stub);
        let res = base_admission_check(Path::new("/nonexistent"), HEX_SHA, Kind::Validate, false);
        env::remove_var(ADMIT_PREFLIGHT_CMD_ENV);
        let _ = fs::remove_file(&stub);
        match res {
            Err(ValidateLockError::StaleBase(m)) => assert!(
                m.contains("Rebase onto"),
                "refusal must NAME the remedy, got: {m}"
            ),
            other => panic!("expected StaleBase refusal, got {other:?}"),
        }
    }

    // POSITIVE direction: a current head is GRANTED. Required so the gate is not
    // one that "refuses everything" — such a gate gets disabled.
    #[test]
    fn base_admission_admits_current_head() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let stub = write_stub("ok", "#!/bin/sh\necho OK\nexit 0\n");
        env::set_var(ADMIT_PREFLIGHT_CMD_ENV, &stub);
        let res = base_admission_check(Path::new("/nonexistent"), HEX_SHA, Kind::Validate, false);
        env::remove_var(ADMIT_PREFLIGHT_CMD_ENV);
        let _ = fs::remove_file(&stub);
        assert!(res.is_ok(), "a current head must be GRANTED, got {res:?}");
    }

    // FAIL-CLOSED: an ERROR (unresolvable base) is treated as stale, never waved
    // through — an admission gate that admits on "couldn't check" is advisory.
    #[test]
    fn base_admission_fails_closed_on_error() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let stub = write_stub("err", "#!/bin/sh\necho boom >&2\nexit 3\n");
        env::set_var(ADMIT_PREFLIGHT_CMD_ENV, &stub);
        let res = base_admission_check(Path::new("/nonexistent"), HEX_SHA, Kind::Validate, false);
        env::remove_var(ADMIT_PREFLIGHT_CMD_ENV);
        let _ = fs::remove_file(&stub);
        match res {
            Err(ValidateLockError::StaleBase(m)) => assert!(
                m.contains("STALE") || m.contains("Rebase"),
                "ERROR must fail closed to a stale-base refusal, got: {m}"
            ),
            other => panic!("ERROR must fail closed to StaleBase, got {other:?}"),
        }
    }

    // PASS-THROUGH: bench work is not landing evidence and does not invoke the
    // predicate (the stub would REFUSE if it ran).
    #[test]
    fn base_admission_passes_through_bench() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let stub = write_stub(
            "pass",
            "#!/bin/sh\necho 'REFUSE: would block if invoked'\nexit 2\n",
        );
        env::set_var(ADMIT_PREFLIGHT_CMD_ENV, &stub);
        let r = Path::new("/nonexistent");
        assert!(
            base_admission_check(r, HEX_SHA, Kind::Bench, false).is_ok(),
            "bench is never floor-gated"
        );
        env::remove_var(ADMIT_PREFLIGHT_CMD_ENV);
        let _ = fs::remove_file(&stub);
    }

    // BYPASS NEGATIVES: neither an ambiguous target nor the former skip flag can
    // mint qualifying evidence. Both refuse before invoking the predicate.
    #[test]
    fn base_admission_refuses_nonhex_and_skip_bypass() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let r = Path::new("/nonexistent");
        for result in [
            base_admission_check(r, "sha-3", Kind::Validate, false),
            base_admission_check(r, HEX_SHA, Kind::Validate, true),
        ] {
            assert!(
                matches!(result, Err(ValidateLockError::StaleBase(_))),
                "a validate admission bypass must be refused, got {result:?}"
            );
        }
    }

    // END-TO-END through run(): a stale base is refused with STALE_BASE_EXIT_CODE
    // and NEVER consumes the box — a doomed ~17-min validate never starts.
    #[test]
    fn run_refuses_stale_base_without_consuming_box() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let paths = temp_paths("run-stale-base");
        let lock = ValidateLock {
            paths: paths.clone(),
        };
        let stub = write_stub(
            "run-refuse",
            "#!/bin/sh\necho 'REFUSE: predates floor; Rebase onto current \
             origin/main before validating/landing.'\nexit 2\n",
        );
        env::set_var(ADMIT_PREFLIGHT_CMD_ENV, &stub);
        let code = lock
            .run(
                RunArgs {
                    agent: "stale-runner".into(),
                    kind: Kind::Validate,
                    target: HEX_SHA.into(),
                    no_wait: false,
                    wait: 0,
                    hold: 30,
                    child_deadline: 30,
                    max: 1,
                    skip_base_check: false,
                    // If the gate DIDN'T fire, this child would run and exit 0.
                    child: vec![OsString::from("/bin/true")],
                },
                paths.lock.parent().unwrap(),
            )
            .unwrap();
        env::remove_var(ADMIT_PREFLIGHT_CMD_ENV);
        assert_eq!(
            code, STALE_BASE_EXIT_CODE,
            "stale base must refuse (not run the child to a 0 exit)"
        );
        assert!(
            lock.read_holder().unwrap().is_none(),
            "a refused validate must not have held the box"
        );
        let _ = fs::remove_dir_all(paths.lock.parent().unwrap());
        let _ = fs::remove_file(&stub);
    }
}
