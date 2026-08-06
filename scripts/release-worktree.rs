#!/usr/bin/env rust-script
//! Release a worktree slot allocated by `allocate-worktree.rs`.
//!
//! CANONICAL LAYOUT v3 (nested):
//! worktrees/<slot>/{hermit,reverie,liteinst2}.
//!
//! Refuses to silently discard work: it inspects each product worktree for
//! uncommitted changes and warns loudly. `--clean` physically removes the
//! worktrees (and the now-empty slot dir), but only when they are clean or
//! `--force` is given. Ownership is updated in `worktree-state.json` and the
//! machine-parseable table block in `worktrees/ACTIVE.md` is regenerated.
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::fs;
use std::io::ErrorKind;
use std::path::{Component, Path, PathBuf};
use std::process::{exit, Command, Output};

const USAGE: &str = r#"Usage: release-worktree.rs --slot SLOT [OPTIONS]

Release a worktree slot (worktrees/<slot>/{hermit,reverie,liteinst2}) and
update ownership.

Required:
  --slot SLOT         Slot to release (named token or slotNN).

Options:
  --agent NAME        If the slot is shared, drop only this agent. When other
                      agents remain, the slot stays active.
  --clean             Physically remove the git worktree(s) and the slot dir.
                      Refused if a worktree has uncommitted work OR unpushed
                      commits, unless --push (safe) or --force (dangerous).
  --push              Before removal, push any unpushed slot feature branches to
                      their upstream (with-proxy) so removal is fully recoverable
                      (the "push-then-remove" safe reclaim protocol).
  --force             Allow --clean despite uncommitted/unpushed work (dangerous).
  -h, --help          Show this help.

Without --clean the physical worktree is left in place (cache retained) and the
slot is marked released in state. With --clean the worktree is removed and the
slot entry is dropped from state. Feature branches are NEVER deleted.

Examples:
  ./scripts/release-worktree.rs --slot kvm
  ./scripts/release-worktree.rs --slot slot01 --clean
  ./scripts/release-worktree.rs --slot kvm --agent hermit-ci   # drop one sharer
"#;

fn die(msg: &str) -> ! {
    eprintln!("release-worktree: {msg}");
    exit(1);
}

fn find_root() -> PathBuf {
    let mut dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if dir.join(".gitmodules").is_file()
            && dir.join("hermit").is_dir()
            && dir.join("reverie").is_dir()
            && dir.join("liteinst2").is_dir()
        {
            return dir;
        }
        if !dir.pop() {
            die("could not locate dev-hermit root (need .gitmodules + hermit/ + reverie/ + liteinst2/)");
        }
    }
}

fn now_iso() -> String {
    match Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
    {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        _ => "unknown".to_string(),
    }
}

fn git(dir: &Path, args: &[&str]) -> (bool, String, String) {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .unwrap_or_else(|e| die(&format!("failed to spawn git: {e}")));
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).trim().to_string(),
        String::from_utf8_lossy(&out.stderr).trim().to_string(),
    )
}

fn git_output(dir: &Path, args: &[&str]) -> Result<Output, String> {
    Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .map_err(|e| {
            format!(
                "could not run git -C {} {}: {e}",
                dir.display(),
                args.join(" ")
            )
        })
}

/// Run a Git inspection that must succeed before cleanup may continue.
fn git_inspect(dir: &Path, args: &[&str]) -> Result<String, String> {
    let out = git_output(dir, args)?;
    if !out.status.success() {
        return Err(format!(
            "git -C {} {} failed (exit {}): {}",
            dir.display(),
            args.join(" "),
            out.status
                .code()
                .map(|code| code.to_string())
                .unwrap_or_else(|| "signal".to_string()),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Slot name is a lowercase named token (for example `kvm`) or `slotNN`.
fn valid_slot(name: &str) -> bool {
    !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && name
            .chars()
            .next()
            .map(|c| c.is_ascii_alphanumeric())
            .unwrap_or(false)
}

#[derive(Clone, Debug)]
struct CleanTarget {
    label: &'static str,
    branch_key: &'static str,
    primary: PathBuf,
    path: PathBuf,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct RepoSnapshot {
    path: PathBuf,
    head: String,
    origin_url: String,
}

#[derive(Clone, Debug)]
struct RepoInspection {
    snapshot: RepoSnapshot,
    status: String,
}

fn canonical_exact(root: &Path, relative: &Path) -> Result<PathBuf, String> {
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!(
            "target path '{}' is not a normalized relative path",
            relative.display()
        ));
    }

    let canonical_root = fs::canonicalize(root)
        .map_err(|e| format!("could not canonicalize workspace root: {e}"))?;
    let target = root.join(relative);
    let canonical = fs::canonicalize(&target)
        .map_err(|e| format!("could not canonicalize target {}: {e}", target.display()))?;
    let expected = canonical_root.join(relative);
    if canonical != expected {
        return Err(format!(
            "target {} resolves to {}, not exact path {}; refusing symlink/path alias",
            target.display(),
            canonical.display(),
            expected.display()
        ));
    }
    Ok(canonical)
}

/// Reuse the repository's canonical state/ACTIVE/branch reconciler. Exact
/// physical-path binding remains local below because that verifier deliberately
/// checks checkout state, not destructive-target identity.
fn verify_registry(root: &Path) -> Result<(), String> {
    let checker = root.join("scripts/check-worktree-registry.rs");
    if !checker.is_file() {
        return Err(format!(
            "canonical registry verifier is missing: {}",
            checker.display()
        ));
    }
    let root_arg = root.to_string_lossy().into_owned();
    let output = Command::new(&checker)
        .args(["--root", &root_arg])
        .output()
        .map_err(|error| format!("could not run {}: {error}", checker.display()))?;
    if !output.status.success() {
        return Err(format!(
            "canonical registry verifier refused cleanup: {}{}",
            String::from_utf8_lossy(&output.stdout).trim(),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

/// Bind cleanup to each exact state-recorded canonical product path and the
/// corresponding product repository's physical worktree registration.
fn clean_targets_from_state(
    root: &Path,
    state: &Value,
    slot: &str,
) -> Result<Vec<CleanTarget>, String> {
    let slot_state = state["slots"]
        .get(slot)
        .ok_or_else(|| format!("slot {slot} is not registered"))?;
    let agents = slot_state["agents"]
        .as_array()
        .ok_or_else(|| format!("slot {slot} has no readable agents array"))?;
    if agents.is_empty() {
        return Err(format!("slot {slot} has no recorded owner"));
    }

    let mut targets = Vec::new();
    for (label, branch_key, path_key) in [
        ("hermit", "hermit_branch", "hermit_path"),
        ("reverie", "reverie_branch", "reverie_path"),
        ("liteinst2", "liteinst2_branch", "liteinst2_path"),
    ] {
        let expected_checkout = slot_state
            .get(branch_key)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("slot {slot} has missing/non-string {branch_key}"))?;
        if expected_checkout == "-" {
            continue;
        }

        let recorded = slot_state
            .get(path_key)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("slot {slot} has missing/non-string {path_key}"))?;
        let expected_recorded = format!("worktrees/{slot}/{label}");
        if recorded != expected_recorded {
            return Err(format!(
                "slot {slot} records {label} path '{recorded}', expected '{expected_recorded}'"
            ));
        }
        let expected_relative = PathBuf::from(&expected_recorded);
        let canonical = canonical_exact(root, &expected_relative)?;
        let primary = root.join(label);

        let listing = git_inspect(&primary, &["worktree", "list", "--porcelain"])?;
        let registered_matches = listing
            .lines()
            .filter_map(|line| line.strip_prefix("worktree "))
            .filter(|candidate| Path::new(candidate) == canonical)
            .count();
        if registered_matches != 1 {
            return Err(format!(
                "slot {slot} {label} target {} has {registered_matches} matching physical worktree registrations; expected exactly 1",
                canonical.display()
            ));
        }

        targets.push(CleanTarget {
            label,
            branch_key,
            primary,
            path: canonical,
        });
    }

    let slot_relative = PathBuf::from("worktrees").join(slot);
    let slot_dir = root.join(&slot_relative);
    if slot_dir.exists() {
        canonical_exact(root, &slot_relative)?;
        let allowed: BTreeSet<&str> = targets.iter().map(|target| target.label).collect();
        for entry in fs::read_dir(&slot_dir).map_err(|e| {
            format!(
                "could not inspect slot directory {}: {e}",
                slot_dir.display()
            )
        })? {
            let entry = entry.map_err(|e| format!("could not inspect slot entry: {e}"))?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| format!("slot {slot} contains a non-UTF8 entry"))?;
            if !allowed.contains(name.as_str()) {
                return Err(format!(
                    "slot {slot} contains unexpected entry {}; refusing before mutation",
                    entry.path().display()
                ));
            }
        }
    }
    Ok(targets)
}

fn state_path(root: &Path) -> PathBuf {
    root.join("worktree-state.json")
}

fn load_state(root: &Path) -> Value {
    let p = state_path(root);
    if !p.exists() {
        die("worktree-state.json not found; nothing to release");
    }
    let txt = std::fs::read_to_string(&p).unwrap_or_else(|e| die(&format!("read state: {e}")));
    let mut v: Value = serde_json::from_str(&txt)
        .unwrap_or_else(|e| die(&format!("parse worktree-state.json: {e}")));
    if !v.get("slots").map(|s| s.is_object()).unwrap_or(false) {
        v["slots"] = json!({});
    }
    v
}

fn save_state(root: &Path, state: &mut Value) {
    state["updated"] = json!(now_iso());
    state["version"] = json!(3);
    let txt = serde_json::to_string_pretty(state).unwrap();
    std::fs::write(state_path(root), txt + "\n")
        .unwrap_or_else(|e| die(&format!("write state: {e}")));
}

fn regen_active_md(root: &Path, state: &Value) {
    const BEGIN: &str = "<!-- BEGIN worktree-state (managed by scripts/allocate-worktree.rs; do not edit inside) -->";
    const END: &str = "<!-- END worktree-state -->";

    let mut rows = String::new();
    rows.push_str("| Slot | Agent | HermitBranch | ReverieBranch | LiteInst2Branch | Task | Status | ReadOnly |\n");
    rows.push_str("| --- | --- | --- | --- | --- | --- | --- | --- |\n");
    if let Some(slots) = state["slots"].as_object() {
        let mut names: Vec<&String> = slots.keys().collect();
        names.sort();
        for name in names {
            let s = &slots[name];
            let agents = s["agents"].as_array().cloned().unwrap_or_default();
            let owner = agents
                .iter()
                .find(|a| !a["read_only"].as_bool().unwrap_or(false))
                .and_then(|a| a["name"].as_str())
                .unwrap_or("-");
            let ro_agents: Vec<String> = agents
                .iter()
                .filter(|a| a["read_only"].as_bool().unwrap_or(false))
                .filter_map(|a| a["name"].as_str().map(|n| n.to_string()))
                .collect();
            let agent_cell = if ro_agents.is_empty() {
                owner.to_string()
            } else {
                format!("{owner} (+ro: {})", ro_agents.join(", "))
            };
            let hb = s["hermit_branch"].as_str().unwrap_or("-");
            let rb = s["reverie_branch"].as_str().unwrap_or("-");
            let lb = s["liteinst2_branch"].as_str().unwrap_or("-");
            let task = s["task"].as_str().unwrap_or("-");
            let status = s["status"].as_str().unwrap_or("active");
            let read_only = if ro_agents.is_empty() { "no" } else { "shared" };
            rows.push_str(&format!(
                "| {name} | {agent_cell} | {hb} | {rb} | {lb} | {task} | {status} | {read_only} |\n"
            ));
        }
    }
    let block = format!("{BEGIN}\n{rows}{END}\n");

    let path = root.join("worktrees").join("ACTIVE.md");
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let new_content = if let (Some(b), Some(e)) = (existing.find(BEGIN), existing.find(END)) {
        let e_end = e + END.len();
        let after = existing[e_end..]
            .strip_prefix('\n')
            .unwrap_or(&existing[e_end..]);
        format!("{}{}{}", &existing[..b], block, after)
    } else {
        let sep = if existing.is_empty() || existing.ends_with('\n') {
            ""
        } else {
            "\n"
        };
        format!("{existing}{sep}\n## Machine-managed slot table\n\n{block}")
    };
    std::fs::write(&path, new_content).unwrap_or_else(|e| die(&format!("write ACTIVE.md: {e}")));
}

/// Enumerate the target plus every recursively initialized submodule. Git owns
/// the recursion semantics; a malformed `.gitmodules` or any other inspection
/// error is a hard refusal rather than an empty/clean result.
fn initialized_repositories(target: &Path) -> Result<Vec<PathBuf>, String> {
    let canonical_target = fs::canonicalize(target)
        .map_err(|e| format!("could not canonicalize target {}: {e}", target.display()))?;
    let out = git_output(
        &canonical_target,
        &[
            "submodule",
            "foreach",
            "--quiet",
            "--recursive",
            r#"printf '%s\0' "$displaypath""#,
        ],
    )?;
    if !out.status.success() {
        return Err(format!(
            "could not enumerate initialized submodules below {} (exit {}): {}",
            canonical_target.display(),
            out.status
                .code()
                .map(|code| code.to_string())
                .unwrap_or_else(|| "signal".to_string()),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }

    let mut repositories = vec![canonical_target.clone()];
    for raw in out
        .stdout
        .split(|byte| *byte == 0)
        .filter(|raw| !raw.is_empty())
    {
        let relative = std::str::from_utf8(raw).map_err(|_| {
            format!(
                "initialized submodule below {} has a non-UTF8 display path",
                canonical_target.display()
            )
        })?;
        let canonical = canonical_exact(&canonical_target, Path::new(relative))?;
        if !repositories.contains(&canonical) {
            repositories.push(canonical);
        }
    }
    repositories.sort();
    Ok(repositories)
}

fn inspect_repository(path: &Path) -> Result<RepoInspection, String> {
    let status = git_inspect(path, &["status", "--porcelain=v1", "--untracked-files=all"])?;
    let head = git_inspect(path, &["rev-parse", "--verify", "HEAD"])?;
    if head.is_empty() {
        return Err(format!(
            "repository {} has no readable HEAD",
            path.display()
        ));
    }
    let origin_url = git_inspect(path, &["remote", "get-url", "origin"])?;
    if origin_url.is_empty() {
        return Err(format!(
            "repository {} has an empty origin URL",
            path.display()
        ));
    }
    Ok(RepoInspection {
        snapshot: RepoSnapshot {
            path: path.to_path_buf(),
            head,
            origin_url,
        },
        status,
    })
}

fn inspect_target_repositories(target: &Path) -> Result<Vec<RepoInspection>, String> {
    initialized_repositories(target)?
        .iter()
        .map(|path| inspect_repository(path))
        .collect()
}

fn proxy_git_inspect(path: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("with-proxy")
        .arg("git")
        .arg("-C")
        .arg(path)
        .args(args)
        .output()
        .map_err(|error| format!("could not run with-proxy git: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "with-proxy git -C {} {} failed: {}",
            path.display(),
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(output.stdout)
}

/// Prove HEAD is reachable from at least one current ref advertised by this
/// repository's own `origin`. Detached submodule HEADs receive the same proof
/// as branch checkouts; merely being pinned by the outer repository is not a
/// durability claim.
fn remote_durable(snapshot: &RepoSnapshot) -> Result<bool, String> {
    let stdout = String::from_utf8(proxy_git_inspect(&snapshot.path, &["ls-remote", "origin"])?)
        .map_err(|_| {
            format!(
                "git ls-remote origin returned non-UTF8 output for {}",
                snapshot.path.display()
            )
        })?;
    let mut remote_tips = BTreeSet::new();
    let mut branch_tips = BTreeSet::new();
    for (index, line) in stdout.lines().enumerate() {
        let mut fields = line.split_whitespace();
        let sha = fields.next().ok_or_else(|| {
            format!(
                "malformed ls-remote output line {} for {}",
                index + 1,
                snapshot.path.display()
            )
        })?;
        let remote_ref = fields.next().ok_or_else(|| {
            format!(
                "malformed ls-remote output line {} for {}",
                index + 1,
                snapshot.path.display()
            )
        })?;
        if fields.next().is_some()
            || !matches!(sha.len(), 40 | 64)
            || !sha.bytes().all(|byte| byte.is_ascii_hexdigit())
            || !(remote_ref == "HEAD" || remote_ref.starts_with("refs/"))
        {
            return Err(format!(
                "malformed ls-remote output line {} for {}: {line}",
                index + 1,
                snapshot.path.display()
            ));
        }
        remote_tips.insert(sha.to_string());
        if remote_ref.starts_with("refs/heads/") {
            branch_tips.insert(sha.to_string());
        }
    }

    if remote_tips.contains(&snapshot.head) {
        return Ok(true);
    }
    if branch_tips.is_empty() {
        return Ok(false);
    }

    // Make the advertised branch-tip objects available for an observed
    // ancestry proof. A fetch or later Git error refuses cleanup; it cannot be
    // converted into a user-force bypass.
    proxy_git_inspect(
        &snapshot.path,
        &[
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
    )?;
    for remote_tip in branch_tips {
        let ancestry = git_output(
            &snapshot.path,
            &["merge-base", "--is-ancestor", &snapshot.head, &remote_tip],
        )?;
        match ancestry.status.code() {
            Some(0) => return Ok(true),
            Some(1) => {}
            _ => {
                return Err(format!(
                    "could not inspect ancestry {}..{} in {}: {}",
                    snapshot.head,
                    remote_tip,
                    snapshot.path.display(),
                    String::from_utf8_lossy(&ancestry.stderr).trim()
                ))
            }
        }
    }
    Ok(false)
}

/// Push the current branch only. Detached nested repositories cannot be given
/// an invented publication ref by cleanup; they remain at risk and are refused.
fn push_branch(path: &Path) -> Result<bool, String> {
    let branch = git_inspect(path, &["rev-parse", "--abbrev-ref", "HEAD"])?;
    if branch == "HEAD" {
        eprintln!("  cannot push {}: detached HEAD", path.display());
        return Ok(false);
    }
    let refspec = format!("HEAD:refs/heads/{branch}");
    let out = Command::new("with-proxy")
        .arg("git")
        .arg("-C")
        .arg(path)
        .args(["push", "origin", &refspec])
        .output()
        .map_err(|e| format!("could not spawn git push for {}: {e}", path.display()))?;
    if out.status.success() {
        println!("  pushed {} -> origin/{branch}", path.display());
        Ok(true)
    } else {
        eprintln!(
            "  push failed for {}: {}",
            path.display(),
            String::from_utf8_lossy(&out.stderr).trim()
        );
        Ok(false)
    }
}

fn link_inside_target(link: &Path, targets: &[PathBuf]) -> bool {
    targets
        .iter()
        .any(|target| link == target || link.starts_with(target))
}

fn read_proc_link(path: &Path) -> Result<Option<PathBuf>, String> {
    match fs::read_link(path) {
        Ok(link) => Ok(Some(link)),
        // Sandboxed sibling agents may hide their /proc links even though all
        // fleet processes share a Unix uid. Such a process cannot be
        // attributed to this target from this namespace; continue scanning
        // visible owners rather than making every release permanently fail.
        Err(error)
            if matches!(
                error.kind(),
                ErrorKind::NotFound | ErrorKind::PermissionDenied
            ) =>
        {
            Ok(None)
        }
        Err(error) => Err(format!(
            "could not inspect live process link {}: {error}",
            path.display()
        )),
    }
}

/// Refuse while a visible process has its cwd or executable below a target. The
/// coordinator may retry after that owner exits; cleanup never signals it.
fn live_process_users(targets: &[PathBuf]) -> Result<Vec<String>, String> {
    let mut users = Vec::new();
    for entry in fs::read_dir("/proc").map_err(|e| format!("could not enumerate /proc: {e}"))? {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("could not enumerate /proc entry: {error}")),
        };
        let name = entry.file_name();
        let Some(pid_text) = name.to_str() else {
            continue;
        };
        let Ok(pid) = pid_text.parse::<u32>() else {
            continue;
        };
        for kind in ["cwd", "exe"] {
            if let Some(link) = read_proc_link(&entry.path().join(kind))? {
                if link_inside_target(&link, targets) {
                    users.push(format!("pid {pid} {kind}={}", link.display()));
                }
            }
        }
    }
    users.sort();
    users.dedup();
    Ok(users)
}

fn snapshots(inspections: &[RepoInspection]) -> Vec<RepoSnapshot> {
    inspections
        .iter()
        .map(|inspection| inspection.snapshot.clone())
        .collect()
}

/// Final boundary immediately before Git-level force removal. It repeats exact
/// target identity, recursive enumeration, all fail-closed status inspections,
/// HEAD/origin identity, and live-process ownership after every network call.
/// The final outer `git status` is the last subprocess before removal, so a
/// write planted during durability checks cannot be discarded.
fn final_removal_boundary(
    root: &Path,
    slot: &str,
    expected_target: &CleanTarget,
    expected_repositories: &[RepoSnapshot],
    allow_dirty: bool,
) -> Result<(), String> {
    verify_registry(root)?;
    let current_state = load_state(root);
    let rebound = clean_targets_from_state(root, &current_state, slot)?
        .into_iter()
        .find(|target| target.label == expected_target.label)
        .ok_or_else(|| format!("{} target disappeared from state", expected_target.label))?;
    if rebound.path != expected_target.path || rebound.primary != expected_target.primary {
        return Err(format!(
            "{} target identity changed before removal",
            expected_target.label
        ));
    }

    let final_inspections = inspect_target_repositories(&rebound.path)?;
    if snapshots(&final_inspections) != expected_repositories {
        return Err(format!(
            "{} recursive repository identity/HEAD/origin changed before removal",
            expected_target.label
        ));
    }
    if !allow_dirty {
        for inspection in &final_inspections {
            if !inspection.status.is_empty() {
                return Err(format!(
                    "final cleanliness check found work in {}: {}",
                    inspection.snapshot.path.display(),
                    inspection.status.replace('\n', "; ")
                ));
            }
        }
    }

    let users = live_process_users(&[rebound.path.clone()])?;
    if !users.is_empty() {
        return Err(format!(
            "live process ownership below {}: {}",
            rebound.path.display(),
            users.join(", ")
        ));
    }

    // This is deliberately last: no network query, registry rewrite, or other
    // subprocess may open a write window between this fail-closed status and
    // `git worktree remove --force` in the caller.
    let outer_status = git_inspect(
        &rebound.path,
        &["status", "--porcelain=v1", "--untracked-files=all"],
    )?;
    if !allow_dirty && !outer_status.is_empty() {
        return Err(format!(
            "final outer cleanliness check found work in {}: {}",
            rebound.path.display(),
            outer_status.replace('\n', "; ")
        ));
    }
    Ok(())
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut slot = String::new();
    let mut agent: Option<String> = None;
    let mut clean = false;
    let mut force = false;
    let mut push = false;

    let mut i = 0;
    let take = |i: &mut usize, argv: &[String], flag: &str| -> String {
        *i += 1;
        if *i >= argv.len() {
            die(&format!("{flag} requires a value"));
        }
        argv[*i].clone()
    };
    while i < argv.len() {
        match argv[i].as_str() {
            "--slot" => slot = take(&mut i, &argv, "--slot"),
            "--agent" => agent = Some(take(&mut i, &argv, "--agent")),
            "--clean" => clean = true,
            "--force" => force = true,
            "--push" => push = true,
            "-h" | "--help" => {
                print!("{USAGE}");
                return;
            }
            other => die(&format!("unknown argument: {other}\n\n{USAGE}")),
        }
        i += 1;
    }

    if slot.is_empty() {
        die(&format!("--slot is required\n\n{USAGE}"));
    }
    if !valid_slot(&slot) {
        die(&format!(
            "invalid slot name: '{slot}' (expected a lowercase [a-z0-9-]+ token)"
        ));
    }

    let root = find_root();
    let mut state = load_state(&root);

    if state["slots"].get(&slot).is_none() {
        die(&format!(
            "slot {slot} is not registered in worktree-state.json"
        ));
    }

    // If dropping one sharer and others remain, do not tear down the slot.
    if let Some(name) = &agent {
        let agents = state["slots"][&slot]["agents"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let remaining: Vec<Value> = agents
            .into_iter()
            .filter(|a| a["name"].as_str() != Some(name.as_str()))
            .collect();
        if !remaining.is_empty() {
            let n = remaining.len();
            state["slots"][&slot]["agents"] = json!(remaining);
            state["slots"][&slot]["updated"] = json!(now_iso());
            save_state(&root, &mut state);
            regen_active_md(&root, &state);
            println!(
                "✓ dropped agent '{name}' from {slot}; {n} agent(s) remain, slot still active"
            );
            return;
        }
        println!("agent '{name}' was the last owner of {slot}; releasing whole slot");
    }

    let slot_dir = root.join("worktrees").join(&slot);
    if !clean {
        for label in ["hermit", "reverie", "liteinst2"] {
            let path = slot_dir.join(label);
            if !path.exists() {
                continue;
            }
            match git_inspect(
                &path,
                &["status", "--porcelain=v1", "--untracked-files=all"],
            ) {
                Ok(status) if !status.is_empty() => {
                    eprintln!("⚠  retained {label} worktree has uncommitted work:\n{status}")
                }
                Err(error) => eprintln!("⚠  could not inspect retained {label} worktree: {error}"),
                _ => {}
            }
        }
    }
    if clean {
        verify_registry(&root)
            .unwrap_or_else(|error| die(&format!("clean preflight failed: {error}")));
        let clean_targets = clean_targets_from_state(&root, &state, &slot)
            .unwrap_or_else(|error| die(&format!("clean preflight failed: {error}")));
        let mut proofs: Vec<(&'static str, Vec<RepoSnapshot>)> = Vec::new();
        let mut any_dirty = false;

        for target in &clean_targets {
            let inspections = inspect_target_repositories(&target.path).unwrap_or_else(|error| {
                die(&format!(
                    "could not inspect {} target recursively: {error}",
                    target.label
                ))
            });
            for inspection in &inspections {
                if !inspection.status.is_empty() {
                    any_dirty = true;
                    eprintln!(
                        "⚠  repository {} has uncommitted work:",
                        inspection.snapshot.path.display()
                    );
                    for line in inspection.status.lines() {
                        eprintln!("     {line}");
                    }
                }
            }
            proofs.push((target.label, snapshots(&inspections)));
        }
        if any_dirty {
            eprintln!(
                "⚠  {slot} has uncommitted changes. Commit and push to a feature branch first."
            );
            if !force {
                die("refusing --clean with uncommitted work; pass --force to override");
            }
        }

        eprintln!(
            "pre-recycle guardrail: verifying every outer and initialized nested HEAD is reachable from its own origin..."
        );
        let mut at_risk = Vec::new();
        for (_, repositories) in &proofs {
            for repository in repositories {
                match remote_durable(repository) {
                    Ok(true) => {}
                    Ok(false) => {
                        eprintln!(
                            "⚠  {}: HEAD {} is not reachable from any current origin ref",
                            repository.path.display(),
                            &repository.head[..repository.head.len().min(12)]
                        );
                        at_risk.push(repository.path.clone());
                    }
                    Err(error) => die(&format!("origin durability inspection failed: {error}")),
                }
            }
        }

        if !at_risk.is_empty() && push {
            println!("--push: pushing at-risk repositories that have branches (with-proxy)...");
            for repository in &at_risk {
                push_branch(repository).unwrap_or_else(|error| {
                    die(&format!(
                        "could not inspect/push {}: {error}",
                        repository.display()
                    ))
                });
            }
            at_risk.retain(|path| {
                let proof = proofs
                    .iter()
                    .flat_map(|(_, repositories)| repositories)
                    .find(|repository| repository.path == *path)
                    .expect("at-risk repository must have a proof");
                match remote_durable(proof) {
                    Ok(durable) => !durable,
                    Err(error) => die(&format!("post-push durability inspection failed: {error}")),
                }
            });
        }
        if !at_risk.is_empty() {
            let paths = at_risk
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(", ");
            if !force {
                die(&format!(
                    "REFUSING to release {slot}: committed work not on origin ({paths}). \
                     Pass --push to push branch checkouts, or --force to discard the durability guarantee."
                ));
            }
            eprintln!("⚠  --force: releasing {slot} despite work not on origin ({paths}).");
        } else {
            let count: usize = proofs
                .iter()
                .map(|(_, repositories)| repositories.len())
                .sum();
            eprintln!("✓ verified {count} outer/nested repository HEAD(s) on origin");
        }

        let target_paths: Vec<PathBuf> = clean_targets
            .iter()
            .map(|target| target.path.clone())
            .collect();
        let users = live_process_users(&target_paths)
            .unwrap_or_else(|error| die(&format!("live-process inspection failed: {error}")));
        if !users.is_empty() {
            die(&format!(
                "refusing --clean while live processes use the slot: {}",
                users.join(", ")
            ));
        }

        for target in &clean_targets {
            let expected_repositories = proofs
                .iter()
                .find(|(label, _)| *label == target.label)
                .map(|(_, repositories)| repositories.as_slice())
                .expect("clean target must have recursive repository proofs");
            final_removal_boundary(&root, &slot, target, expected_repositories, force)
                .unwrap_or_else(|error| {
                    die(&format!(
                        "final removal boundary refused {}: {error}",
                        target.label
                    ))
                });

            let path = target.path.to_string_lossy().into_owned();
            let (ok, _, error) = git(&target.primary, &["worktree", "remove", "--force", &path]);
            if !ok {
                die(&format!(
                    "could not remove exact {} target {}: {error}; data and registry state retained",
                    target.label,
                    target.path.display()
                ));
            }
            println!(
                "  removed {} worktree {}",
                target.label,
                target.path.display()
            );

            // Record each exact product removal before attempting the next one,
            // so a later failure is retryable without lying about physical state.
            state["slots"][&slot][target.branch_key] = json!("-");
            state["slots"][&slot]["updated"] = json!(now_iso());
            save_state(&root, &mut state);
            regen_active_md(&root, &state);
        }

        if slot_dir.exists() {
            fs::remove_dir(&slot_dir).unwrap_or_else(|error| {
                die(&format!(
                    "could not remove now-empty exact slot directory {}: {error}; slot state retained",
                    slot_dir.display()
                ))
            });
        }
        state["slots"].as_object_mut().unwrap().remove(&slot);
        println!("✓ released and cleaned {slot} (removed from state)");
    } else {
        state["slots"][&slot]["status"] = json!("released");
        state["slots"][&slot]["updated"] = json!(now_iso());
        println!("✓ released {slot} (worktree retained; marked 'released' in state)");
        println!("  run again with --clean to remove the physical worktree");
    }

    save_state(&root, &mut state);
    regen_active_md(&root, &state);
    println!("  state:  {}", state_path(&root).display());
    println!("  active: {}", root.join("worktrees/ACTIVE.md").display());

    // Advisory post-mutation reconciliation check: call the canonical verifier
    // rather than trusting our own write. Endemic fleet drift means this is
    // advisory (never blocks the release); a FAIL points at
    // `allocate-worktree.rs --repair` to reconcile recorded branch cells.
    verify_registry_advisory(&root);
}

/// Run the canonical registry verifier and report its verdict without failing.
/// Every registry mutator routes through this one predicate instead of
/// re-parsing ACTIVE.md / worktree-state.json itself.
fn verify_registry_advisory(root: &Path) {
    let checker = root.join("scripts/check-worktree-registry.rs");
    if !checker.exists() {
        return;
    }
    if let Err(error) = verify_registry(root) {
        eprintln!(
            "note: worktree registry has drift after this release ({error}); run \
             `scripts/allocate-worktree.rs --repair` to reconcile (advisory, not a failure)."
        );
    }
}
