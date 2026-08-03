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

/// Number of commits on HEAD not reachable from its upstream. Returns None when
/// the worktree is absent. Returns Some(n>0) when there are unpushed commits, and
/// -1 (as Some) when there is no configured upstream at all (also "unpushed").
fn unpushed(path: &Path) -> Option<i64> {
    if !path.exists() {
        return None;
    }
    // Detached HEAD or no branch: nothing tracked to push, treat as pushed.
    let (ok_b, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if !ok_b || branch.is_empty() {
        return Some(0);
    }
    let (ok_up, _, _) = git(path, &["rev-parse", "--abbrev-ref", "@{upstream}"]);
    if !ok_up {
        return Some(-1); // branch exists but has no upstream -> unpushed/unrecoverable
    }
    let (ok, out, _) = git(path, &["rev-list", "--count", "@{upstream}..HEAD"]);
    if !ok {
        return Some(-1);
    }
    Some(out.trim().parse::<i64>().unwrap_or(0))
}

/// Push the worktree's current branch to `origin` via with-proxy. Returns true on success.
fn push_branch(path: &Path) -> bool {
    let (ok_b, branch, _) = git(path, &["symbolic-ref", "--quiet", "--short", "HEAD"]);
    if !ok_b || branch.is_empty() {
        eprintln!("  cannot push {}: detached HEAD / no branch", path.display());
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
            eprintln!("  could not spawn with-proxy git push for {}: {e}", path.display());
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

    // Inspect all product worktrees for uncommitted work.
    let slot_dir = root.join("worktrees").join(&slot);
    let hpath = slot_dir.join("hermit");
    let rpath = slot_dir.join("reverie");
    let lpath = slot_dir.join("liteinst2");
    let mut any_dirty = false;
    for (label, p) in [
        ("hermit", &hpath),
        ("reverie", &rpath),
        ("liteinst2", &lpath),
    ] {
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

    // Push-then-remove: never discard committed-but-unpushed work. Committed work
    // survives `git worktree remove` (the branch ref stays in the primary), but if
    // the only copy is a local branch it is not durably recoverable. So --clean
    // requires every slot branch pushed, unless --push (push now) or --force.
    if clean {
        let mut any_unpushed = false;
        for (label, p) in [
            ("hermit", &hpath),
            ("reverie", &rpath),
            ("liteinst2", &lpath),
        ] {
            match unpushed(p) {
                Some(n) if n > 0 => {
                    any_unpushed = true;
                    eprintln!("⚠  {label} worktree has {n} unpushed commit(s).");
                }
                Some(-1) => {
                    any_unpushed = true;
                    eprintln!("⚠  {label} worktree branch has no upstream (nothing pushed).");
                }
                _ => {}
            }
        }
        if any_unpushed {
            if push {
                println!("--push: pushing slot feature branches (with-proxy)...");
                let mut push_ok = true;
                for p in [&hpath, &rpath, &lpath] {
                    if matches!(unpushed(p), Some(n) if n != 0) && !push_branch(p) {
                        push_ok = false;
                    }
                }
                if !push_ok && !force {
                    die("one or more branches failed to push; aborting --clean (pass --force to override)");
                }
            } else if !force {
                die("refusing --clean with unpushed commits; pass --push to push-then-remove, or --force to discard");
            }
        }
    }

    if clean {
        let mut remove_failed = false;
        for (label, primary, p) in [
            ("hermit", root.join("hermit"), &hpath),
            ("reverie", root.join("reverie"), &rpath),
            ("liteinst2", root.join("liteinst2"), &lpath),
        ] {
            if p.exists() {
                let ps = p.to_string_lossy().to_string();
                let mut args = vec!["worktree", "remove", &ps];
                if force {
                    args.insert(2, "--force");
                }
                let (ok, _, err) = git(&primary, &args);
                if ok {
                    println!("  removed {label} worktree {}", p.display());
                } else {
                    remove_failed = true;
                    eprintln!("  could not remove {label} worktree {}: {err}", p.display());
                }
            }
        }
        git(&root.join("hermit"), &["worktree", "prune"]);
        git(&root.join("reverie"), &["worktree", "prune"]);
        git(&root.join("liteinst2"), &["worktree", "prune"]);
        if remove_failed {
            die("one or more product worktrees could not be removed; slot state retained");
        }
        // Remove the now-empty slot dir.
        std::fs::remove_dir(&slot_dir).ok();
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
}
