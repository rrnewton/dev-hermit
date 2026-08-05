#!/usr/bin/env rust-script
//! Export repository-owned coordinator skills to optional local memories.
//!
//! Versioned `.claude/skills/<slug>/SKILL.md` packages are authoritative. This
//! tool never imports local memory into the repository and never removes a
//! repository skill.
//!
//! Memory storage is file-based Markdown; override it with HERMIT_MEMORY_DIR.
//!
//! Usage:
//!   scripts/sync-memory-skill.rs --adopt-skill <path>.. # create memories from active skills
//!   scripts/sync-memory-skill.rs --check                # explain authority; no writes
use std::path::{Path, PathBuf};

const SKILL_DIR: &str = ".claude/skills";

const USAGE: &str = "\
sync-memory-skill.rs — export authoritative repository skills to optional local memory

USAGE:
  scripts/sync-memory-skill.rs --check                  report authority and exit without writing
  scripts/sync-memory-skill.rs --adopt-skill <path>..   create source memories from active skills
  scripts/sync-memory-skill.rs -h | --help              show this help and exit (no side effects)
  scripts/sync-memory-skill.rs --version                print version and exit (no side effects)

Repository skills are authoritative. Local memory is optional and never writes
or deletes repository files. Override its file-based location with
HERMIT_MEMORY_DIR. Companion linter: scripts/lint-memory-skill-sync.rs.";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    // Safe probes must be PURE: no root lookup, no filesystem writes, no state
    // changes. A user runs --help precisely because they do not yet know what
    // this tool does; it must never mutate the working tree on the discovery path.
    if args.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return;
    }
    if args.iter().any(|a| a == "--version") {
        println!("sync-memory-skill.rs 2.0");
        return;
    }

    let root = find_root();
    let memory_dir = memory_dir(&root);

    if !memory_dir.is_dir() && args.first().map(String::as_str) == Some("--adopt-skill") {
        eprintln!(
            "memory dir not found: {} (set HERMIT_MEMORY_DIR)",
            memory_dir.display()
        );
        std::process::exit(2);
    }

    let check = args.iter().any(|a| a == "--check");
    let mode = args.first().map(|s| s.as_str());

    match mode {
        Some("--adopt-skill") => {
            let skills: Vec<String> = args[1..]
                .iter()
                .filter(|arg| !arg.starts_with("--"))
                .cloned()
                .collect();
            if skills.is_empty() {
                eprintln!("--adopt-skill needs at least one parent-relative skill path");
                std::process::exit(2);
            }
            for skill in &skills {
                adopt_skill(&root, &memory_dir, skill, check);
            }
        }
        Some("--check") => println!(
            "repository skills are authoritative; local memory is optional and cannot import into the repository"
        ),
        Some("--promote") | Some("--demote") => {
            eprintln!(
                "memory-to-repository sync is disabled; review the local memory and apply an explicit repository patch instead"
            );
            std::process::exit(2);
        }
        None => {
            eprintln!("no implicit sync: use --check or explicit --adopt-skill paths");
            std::process::exit(2);
        }
        Some(other) => {
            eprintln!("unknown mode {other:?}; use --help");
            std::process::exit(2);
        }
    }
}

fn adopt_skill(root: &Path, memory_dir: &Path, skill_rel: &str, check: bool) {
    let path = Path::new(skill_rel);
    if path.file_name().and_then(|name| name.to_str()) != Some("SKILL.md")
        || path.parent().and_then(Path::parent) != Some(Path::new(SKILL_DIR))
    {
        eprintln!("adopt {skill_rel}: path must be .claude/skills/<name>/SKILL.md");
        std::process::exit(2);
    }

    let skill_path = root.join(skill_rel);
    let skill = std::fs::read_to_string(&skill_path).unwrap_or_else(|error| {
        eprintln!("adopt {skill_rel}: {error}");
        std::process::exit(1);
    });
    let meta = parse_meta(&skill);
    if meta.name.is_empty() || meta.description.is_empty() {
        eprintln!("adopt {skill_rel}: skill needs name and description frontmatter");
        std::process::exit(1);
    }
    if !meta
        .name
        .chars()
        .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-')
    {
        eprintln!("adopt {skill_rel}: invalid memory slug '{}'", meta.name);
        std::process::exit(1);
    }
    let canonical = package_skill_rel(&meta.name);
    if skill_rel != canonical {
        eprintln!(
            "adopt {skill_rel}: skill name '{}' requires path {canonical}",
            meta.name
        );
        std::process::exit(1);
    }

    let body = canonical_body(&skill);
    let description = meta.description.replace('"', "'");
    let tag = format!(
        "> **CORE-MEMORY** — mirrored to skill `{skill_rel}` (sync: `scripts/sync-memory-skill.rs`; lint: `scripts/lint-memory-skill-sync.rs`)."
    );
    let memory = format!(
        "---\nname: {}\ndescription: \"{}\"\nmetadata:\n  core_memory: true\n  core_skill: {}\n  node_type: memory\n  type: reference\n---\n\n{}\n\n{}\n",
        meta.name, description, skill_rel, tag, body
    );
    let memory_path = memory_dir.join(format!("{}.md", meta.name));
    if memory_path.is_file() {
        let current = std::fs::read_to_string(&memory_path).unwrap_or_default();
        if current != memory {
            eprintln!(
                "adopt {skill_rel}: memory {} already exists with different content",
                memory_path.display()
            );
            std::process::exit(1);
        }
        println!("memory {} already adopts {skill_rel}", meta.name);
        return;
    }

    if check {
        println!("would adopt {skill_rel} as memory {}.md", meta.name);
    } else {
        std::fs::write(&memory_path, memory).unwrap_or_else(|error| {
            eprintln!("write memory {}: {error}", meta.name);
            std::process::exit(1);
        });
        println!("adopted {skill_rel} as memory {}.md", meta.name);
    }
}

fn package_skill_rel(slug: &str) -> String {
    format!("{SKILL_DIR}/{slug}/SKILL.md")
}

// ---- shared extraction/normalization (kept identical in lint-memory-skill-sync.rs) ----

struct Meta {
    name: String,
    description: String,
}

fn parse_meta(content: &str) -> Meta {
    let mut m = Meta {
        name: String::new(),
        description: String::new(),
    };
    if !content.starts_with("---") {
        return m;
    }
    for line in content.lines().skip(1) {
        if line.trim() == "---" {
            break;
        }
        let t = line.trim();
        if let Some(v) = t.strip_prefix("name:") {
            if m.name.is_empty() {
                m.name = unquote(v.trim());
            }
        } else if let Some(v) = t.strip_prefix("description:") {
            if m.description.is_empty() {
                m.description = unquote(v.trim());
            }
        }
    }
    m
}

fn unquote(s: &str) -> String {
    let s = s.trim();
    let s = s.strip_prefix('"').unwrap_or(s);
    let s = s.strip_suffix('"').unwrap_or(s);
    s.to_string()
}

fn strip_frontmatter(content: &str) -> &str {
    if let Some(rest) = content.strip_prefix("---\n") {
        if let Some(idx) = rest.find("\n---\n") {
            return &rest[idx + 5..];
        }
        if let Some(idx) = rest.find("\n---") {
            return &rest[idx + 4..];
        }
    }
    content
}

fn canonical_body(content: &str) -> String {
    let body = strip_frontmatter(content);
    let mut lines: Vec<String> = Vec::new();
    for line in body.lines() {
        let t = line.trim_start();
        if t.starts_with("> **CORE-MEMORY**") {
            continue;
        }
        let tl = line.trim();
        if tl.starts_with("<!--") && tl.ends_with("-->") {
            continue;
        }
        lines.push(line.trim_end().to_string());
    }
    normalize_lines(lines)
}

fn normalize_lines(lines: Vec<String>) -> String {
    let mut res: Vec<String> = Vec::new();
    let mut pending_blank = false;
    for l in lines {
        if l.trim().is_empty() {
            if !res.is_empty() {
                pending_blank = true;
            }
        } else {
            if pending_blank {
                res.push(String::new());
                pending_blank = false;
            }
            res.push(l);
        }
    }
    res.join("\n")
}

// ---- filesystem helpers ----

fn memory_dir(root: &Path) -> PathBuf {
    match std::env::var("HERMIT_MEMORY_DIR") {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => {
            let home = std::env::var_os("HOME")
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    eprintln!("HOME is not set (set HERMIT_MEMORY_DIR)");
                    std::process::exit(2);
                });
            let project_key: String = root
                .to_string_lossy()
                .chars()
                .map(|ch| if ch == '/' || ch == '\\' { '-' } else { ch })
                .collect();
            home.join(".claude/projects")
                .join(project_key)
                .join("memory")
        }
    }
}

fn find_root() -> PathBuf {
    let mut dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if dir.join(".gitmodules").is_file()
            && dir.join("hermit").is_dir()
            && dir.join("reverie").is_dir()
        {
            return dir;
        }
        if !dir.pop() {
            eprintln!("could not locate dev-hermit root (need .gitmodules + hermit/ + reverie/)");
            std::process::exit(2);
        }
    }
}
