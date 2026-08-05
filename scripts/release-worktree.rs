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
use std::path::{Path, PathBuf};
use std::process::{exit, Command};

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

/// Slot name is a named token [a-z0-9-]+ (for example kvm) or slotNN.
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

struct CleanTarget {
    label: &'static str,
    branch_key: &'static str,
    primary: PathBuf,
    path: PathBuf,
}

enum CheckoutIdentity {
    Branch(String),
    Detached(String),
    Unreadable(String),
}

fn checkout_identity(path: &Path) -> CheckoutIdentity {
    let (inside, _, error) = git(path, &["rev-parse", "--is-inside-work-tree"]);
    if !inside {
        return CheckoutIdentity::Unreadable(if error.is_empty() {
            "not a git worktree".to_string()
        } else {
            error
        });
    }
    let (on_branch, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if on_branch && !branch.is_empty() {
        return CheckoutIdentity::Branch(branch);
    }
    let (has_head, head, error) = git(path, &["rev-parse", "HEAD"]);
    if has_head && !head.is_empty() {
        CheckoutIdentity::Detached(head)
    } else {
        CheckoutIdentity::Unreadable(error)
    }
}

fn checkout_matches(expected: &str, actual: &CheckoutIdentity) -> bool {
    if expected == "detached" {
        return matches!(actual, CheckoutIdentity::Detached(_));
    }
    if let Some(prefix) = expected.strip_prefix("detached:") {
        return matches!(actual, CheckoutIdentity::Detached(head) if head.starts_with(prefix));
    }
    matches!(actual, CheckoutIdentity::Branch(branch) if branch == expected)
}

fn checkout_display(actual: &CheckoutIdentity) -> String {
    match actual {
        CheckoutIdentity::Branch(branch) => branch.clone(),
        CheckoutIdentity::Detached(head) => {
            format!("detached:{}", &head[..head.len().min(12)])
        }
        CheckoutIdentity::Unreadable(reason) => format!("unreadable:{reason}"),
    }
}

/// Resolve the exact product worktrees recorded for one slot before --clean
/// performs its first mutation. A missing child is not evidence that a
/// repository-wide prune is safe: it is an inconsistent allocation and must
/// be repaired explicitly.
fn clean_targets_from_state(
    root: &Path,
    state: &Value,
    slot: &str,
) -> Result<Vec<CleanTarget>, String> {
    let slot_state = state["slots"]
        .get(slot)
        .ok_or_else(|| format!("slot {slot} is not registered"))?;
    let mut targets = Vec::new();

    for (label, branch_key, path_key) in [
        ("hermit", "hermit_branch", "hermit_path"),
        ("reverie", "reverie_branch", "reverie_path"),
        ("liteinst2", "liteinst2_branch", "liteinst2_path"),
    ] {
        let branch = slot_state
            .get(branch_key)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("slot {slot} has missing/non-string {branch_key}"))?;
        if branch == "-" {
            continue;
        }

        let recorded = slot_state[path_key]
            .as_str()
            .ok_or_else(|| format!("slot {slot} has no recorded {label} path"))?;
        let expected_rel = PathBuf::from("worktrees").join(slot).join(label);
        if !expected_rel.is_relative() {
            return Err(format!(
                "slot {slot} produced non-relative {label} path; refusing before mutation"
            ));
        }
        if Path::new(recorded) != expected_rel {
            return Err(format!(
                "slot {slot} records {label} path '{recorded}', expected '{}'",
                expected_rel.display()
            ));
        }

        let path = root.join(&expected_rel);
        if !path.exists() {
            return Err(format!(
                "slot {slot} allocated {label} worktree is missing at {}; refusing before mutation",
                path.display()
            ));
        }
        if std::fs::symlink_metadata(&path)
            .map_err(|e| format!("could not inspect allocated {label} path: {e}"))?
            .file_type()
            .is_symlink()
        {
            return Err(format!(
                "slot {slot} allocated {label} path {} is a symlink; refusing alias",
                path.display()
            ));
        }
        let canonical = std::fs::canonicalize(&path).map_err(|e| {
            format!(
                "could not canonicalize allocated {label} worktree {}: {e}",
                path.display()
            )
        })?;
        let canonical_root = std::fs::canonicalize(root)
            .map_err(|e| format!("could not canonicalize workspace root: {e}"))?;
        let canonical_expected = canonical_root.join(&expected_rel);
        if canonical != canonical_expected {
            return Err(format!(
                "slot {slot} allocated {label} path {} resolves outside its canonical location",
                path.display()
            ));
        }
        let primary = root.join(label);
        let (ok, listing, err) = git(&primary, &["worktree", "list", "--porcelain"]);
        if !ok {
            return Err(format!("could not list {label} worktrees: {err}"));
        }
        let registered = listing.lines().any(|line| {
            let Some(candidate) = line.strip_prefix("worktree ") else {
                return false;
            };
            std::fs::canonicalize(candidate)
                .map(|p| p == canonical)
                .unwrap_or(false)
        });
        if !registered {
            return Err(format!(
                "slot {slot} {label} path {} is not that primary's registered worktree; refusing before mutation",
                path.display()
            ));
        }

        let (top_ok, child_top, top_err) = git(
            &path,
            &["rev-parse", "--path-format=absolute", "--show-toplevel"],
        );
        if !top_ok {
            return Err(format!(
                "could not resolve {label} child top-level {}: {top_err}",
                path.display()
            ));
        }
        let canonical_child_top = std::fs::canonicalize(&child_top).map_err(|e| {
            format!("could not canonicalize {label} child top-level '{child_top}': {e}")
        })?;
        if canonical_child_top != canonical {
            return Err(format!(
                "slot {slot} {label} checkout top-level {} does not equal its canonical target {}",
                canonical_child_top.display(),
                canonical.display()
            ));
        }

        let common_args = ["rev-parse", "--path-format=absolute", "--git-common-dir"];
        let (primary_ok, primary_common, primary_err) = git(&primary, &common_args);
        let (child_ok, child_common, child_err) = git(&path, &common_args);
        if !primary_ok || !child_ok {
            return Err(format!(
                "could not bind {label} child to primary common-dir: primary='{primary_err}' child='{child_err}'"
            ));
        }
        let canonical_primary_common = std::fs::canonicalize(&primary_common).map_err(|e| {
            format!("could not canonicalize {label} primary common-dir '{primary_common}': {e}")
        })?;
        let canonical_child_common = std::fs::canonicalize(&child_common).map_err(|e| {
            format!("could not canonicalize {label} child common-dir '{child_common}': {e}")
        })?;
        if canonical_child_common != canonical_primary_common {
            return Err(format!(
                "slot {slot} {label} child common-dir {} does not match primary {}",
                canonical_child_common.display(),
                canonical_primary_common.display()
            ));
        }

        let actual = checkout_identity(&path);
        if !checkout_matches(branch, &actual) {
            return Err(format!(
                "slot {slot} records {label} checkout '{branch}', actual '{}'; refusing before mutation",
                checkout_display(&actual)
            ));
        }

        targets.push(CleanTarget {
            label,
            branch_key,
            primary,
            path,
        });
    }

    let slot_dir = root.join("worktrees").join(slot);
    if slot_dir.exists() {
        let allowed: std::collections::BTreeSet<&str> =
            targets.iter().map(|target| target.label).collect();
        for entry in std::fs::read_dir(&slot_dir).map_err(|e| {
            format!(
                "could not inspect slot directory {}: {e}",
                slot_dir.display()
            )
        })? {
            let entry = entry.map_err(|e| format!("could not inspect slot entry: {e}"))?;
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                return Err(format!(
                    "slot {slot} contains a non-UTF8 entry; refusing before mutation"
                ));
            };
            if !allowed.contains(name) {
                return Err(format!(
                    "slot {slot} contains unexpected entry '{}'; refusing before mutation",
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

/// Return uncommitted `git status --porcelain` output for a worktree, or None if
/// the path is absent / not a worktree.
fn dirty(path: &Path) -> Option<String> {
    if !path.exists() {
        return None;
    }
    let (ok, out, _) = git(path, &["status", "--porcelain"]);
    if !ok || out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// Authoritative "is this worktree's committed work durable on origin?" check.
///
/// Verifies the worktree's HEAD against `origin` via `git ls-remote` (the same
/// authoritative check the fleet sweep uses), NOT `@{upstream}` — a branch cut
/// from `origin/main` tracks `origin/main`, so an `@{upstream}` count wrongly
/// flags already-pushed feature commits. HEAD is durable iff origin carries the
/// branch at exactly HEAD, or origin is strictly ahead of HEAD (all our commits
/// are reachable from the remote tip).
///
/// Returns None when the work is safe (durable on origin, absent, or a detached
/// checkout parked at a pinned gitlink). Returns Some(reason) when committed work
/// would be lost by removal.
fn missing_on_origin(path: &Path) -> Option<String> {
    if !path.exists() {
        return None;
    }
    // Detached HEAD (parked at a pinned gitlink) has no branch to push; safe.
    let (ok_b, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if !ok_b || branch.is_empty() {
        return None;
    }
    let (ok_h, local_head, _) = git(path, &["rev-parse", "HEAD"]);
    if !ok_h || local_head.is_empty() {
        return None;
    }
    // Fast path: if HEAD is already an ancestor of origin/main, every commit is
    // on origin (nothing unique to this branch) -> durable, no network needed.
    let (on_main, _, _) = git(
        path,
        &["merge-base", "--is-ancestor", &local_head, "origin/main"],
    );
    if on_main {
        return None;
    }
    // Query origin authoritatively (network; with-proxy).
    let out = Command::new("with-proxy")
        .arg("git")
        .arg("-C")
        .arg(path)
        .args(["ls-remote", "origin", &format!("refs/heads/{branch}")])
        .output();
    let remote_sha = match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout)
            .split_whitespace()
            .next()
            .unwrap_or("")
            .to_string(),
        Ok(_) => {
            return Some(format!(
                "branch '{branch}' ls-remote failed (treat as unpushed)"
            ))
        }
        Err(e) => return Some(format!("branch '{branch}' ls-remote could not run ({e})")),
    };
    if remote_sha.is_empty() {
        return Some(format!("branch '{branch}' does not exist on origin"));
    }
    if remote_sha == local_head {
        return None; // exact match: durable
    }
    // Remote differs. Safe ONLY if HEAD is an ancestor of the remote tip (origin
    // is strictly ahead and carries all our commits). This requires the remote
    // object locally; if it is not present we cannot prove safety -> at risk.
    let (ok_anc, _, _) = git(
        path,
        &["merge-base", "--is-ancestor", &local_head, &remote_sha],
    );
    if ok_anc {
        None
    } else {
        Some(format!(
            "HEAD {} not on origin/{branch} (remote tip {})",
            &local_head[..local_head.len().min(12)],
            &remote_sha[..remote_sha.len().min(12)]
        ))
    }
}

/// Push the worktree's current branch to `origin` via with-proxy. Returns true on success.
fn push_branch(path: &Path) -> bool {
    let (ok_b, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if !ok_b || branch.is_empty() {
        eprintln!(
            "  cannot push {}: detached HEAD / no branch",
            path.display()
        );
        return false;
    }
    let refspec = format!("HEAD:refs/heads/{branch}");
    let out = Command::new("with-proxy")
        .arg("git")
        .arg("-C")
        .arg(path)
        .args(["push", "origin", &refspec])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            println!("  pushed {} -> origin/{branch}", path.display());
            true
        }
        Ok(o) => {
            eprintln!(
                "  push failed for {}: {}",
                path.display(),
                String::from_utf8_lossy(&o.stderr).trim()
            );
            false
        }
        Err(e) => {
            eprintln!(
                "  could not spawn with-proxy git push for {}: {e}",
                path.display()
            );
            false
        }
    }
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
            "invalid slot name: '{slot}' (expected [a-z0-9-]+ or slotNN)"
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

    // Bind --clean to the exact product paths recorded for this slot before
    // inspecting or mutating any worktree. Never infer authority from an
    // absent directory, and never widen one-slot cleanup into repo-wide prune.
    let clean_targets = if clean {
        clean_targets_from_state(&root, &state, &slot)
            .unwrap_or_else(|e| die(&format!("clean preflight failed: {e}")))
    } else {
        Vec::new()
    };

    // Inspect all product worktrees for uncommitted work.
    let slot_dir = root.join("worktrees").join(&slot);
    let hpath = slot_dir.join("hermit");
    let rpath = slot_dir.join("reverie");
    let lpath = slot_dir.join("liteinst2");
    let mut any_dirty = false;
    let inspect_paths: Vec<(&str, &Path)> = if clean {
        clean_targets
            .iter()
            .map(|target| (target.label, target.path.as_path()))
            .collect()
    } else {
        vec![
            ("hermit", hpath.as_path()),
            ("reverie", rpath.as_path()),
            ("liteinst2", lpath.as_path()),
        ]
    };
    for (label, p) in inspect_paths {
        if let Some(status) = dirty(p) {
            any_dirty = true;
            eprintln!("⚠  {label} worktree {} has uncommitted work:", p.display());
            for line in status.lines() {
                eprintln!("     {line}");
            }
        }
    }
    if any_dirty {
        eprintln!("⚠  {slot} has uncommitted changes. Commit and push to a feature branch first.");
        if clean && !force {
            die("refusing --clean with uncommitted work; pass --force to override");
        }
    }

    // PRE-RECYCLE GUARDRAIL — push-then-remove: never discard committed-but-unpushed
    // work. Committed work survives `git worktree remove` (the branch ref stays in
    // the primary), but a purely-local branch is not durably recoverable if the box
    // dies. So --clean REFUSES to release a slot whose HEAD is not on origin
    // (verified authoritatively via git ls-remote), unless --push (push-then-verify)
    // or --force (explicit override, discards durability guarantee).
    if clean {
        let mut at_risk: Vec<&str> = Vec::new();
        eprintln!(
            "pre-recycle guardrail: verifying committed work is on origin (git ls-remote)..."
        );
        for target in &clean_targets {
            if let Some(reason) = missing_on_origin(&target.path) {
                at_risk.push(target.label);
                eprintln!("⚠  {}: {reason}", target.label);
            }
        }
        if !at_risk.is_empty() {
            if push {
                println!("--push: pushing at-risk slot branches (with-proxy)...");
                let mut push_ok = true;
                for target in &clean_targets {
                    if at_risk.contains(&target.label) && !push_branch(&target.path) {
                        push_ok = false;
                    }
                }
                // Re-verify authoritatively AFTER pushing; only proceed if now durable.
                let mut still_at_risk: Vec<&str> = Vec::new();
                for target in &clean_targets {
                    if missing_on_origin(&target.path).is_some() {
                        still_at_risk.push(target.label);
                    }
                }
                if !still_at_risk.is_empty() && !force {
                    die(&format!(
                        "push-then-verify failed; still not on origin: {}. Aborting --clean (pass --force to override).",
                        still_at_risk.join(", ")
                    ));
                }
                if push_ok && still_at_risk.is_empty() {
                    println!("✓ all slot branches verified on origin; safe to release");
                }
            } else if !force {
                die(&format!(
                    "REFUSING to release {slot}: committed work not on origin ({}). \
                     Pass --push to push-then-remove, or --force to discard the durability guarantee.",
                    at_risk.join(", ")
                ));
            } else {
                eprintln!(
                    "⚠  --force: releasing {slot} despite work not on origin ({}).",
                    at_risk.join(", ")
                );
            }
        } else {
            eprintln!("✓ all committed work verified on origin");
        }
    }

    if clean {
        for target in &clean_targets {
            let ps = target.path.to_string_lossy().to_string();
            let mut args = vec!["worktree", "remove", &ps];
            if force {
                args.insert(2, "--force");
            }
            let (ok, _, err) = git(&target.primary, &args);
            if ok {
                println!(
                    "  removed {} worktree {}",
                    target.label,
                    target.path.display()
                );
                // Commit per-product progress so a later exact-target failure is
                // retryable and the registry never claims this removed child is
                // still allocated.
                state["slots"][&slot][target.branch_key] = json!("-");
                state["slots"][&slot]["updated"] = json!(now_iso());
                save_state(&root, &mut state);
                regen_active_md(&root, &state);
            } else {
                eprintln!(
                    "  could not remove {} worktree {}: {err}",
                    target.label,
                    target.path.display()
                );
                eprintln!(
                    "     residue retained for recovery (data dir + admin entry kept together): {}",
                    target.path.display()
                );
                die(&format!(
                    "could not remove exact {} target; completed product removals are recorded and retryable",
                    target.label
                ));
            }
        }
        // Remove only the now-empty target slot directory. Failure means
        // unexpected residue exists, so retain registry state for recovery.
        if slot_dir.exists() {
            std::fs::remove_dir(&slot_dir).unwrap_or_else(|e| {
                die(&format!(
                    "could not remove now-empty slot directory {}: {e}; slot state retained",
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
    let root_arg = root.to_string_lossy().into_owned();
    match Command::new(&checker).args(["--root", &root_arg]).status() {
        Ok(s) if s.success() => {}
        Ok(_) => eprintln!(
            "note: worktree registry has drift after this release; run \
             `scripts/allocate-worktree.rs --repair` to reconcile (advisory, not a failure)."
        ),
        Err(e) => eprintln!("note: could not run registry verifier: {e}"),
    }
}
