#!/usr/bin/env rust-script
#![allow(dead_code)]
//! Export repository-owned coordinator skills to optional local memories.
//!
//! Versioned `.claude/skills/` files are authoritative. This tool never imports
//! local memory into the repository and never removes a repository skill.
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
    if path.parent() != Some(Path::new(SKILL_DIR))
        || path.extension().and_then(|ext| ext.to_str()) != Some("md")
    {
        eprintln!("adopt {skill_rel}: path must be a flat .claude/skills/<name>.md file");
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
    let canonical = flat_skill_rel(&meta.name);
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

fn flat_skill_rel(slug: &str) -> String {
    format!("{SKILL_DIR}/{slug}.md")
}

fn flatten_mapping(
    memory_path: &Path,
    slug: &str,
    content: &str,
    check: bool,
) -> (String, Option<String>) {
    let old_skill = parse_meta(content).core_skill;
    let skill_rel = flat_skill_rel(slug);
    let tag = format!(
        "> **CORE-MEMORY** — mirrored to skill `{skill_rel}` (sync: `scripts/sync-memory-skill.rs`; lint: `scripts/lint-memory-skill-sync.rs`)."
    );
    let mut in_frontmatter = false;
    let mut seen_frontmatter = false;
    let mut changed = false;
    let mut lines = Vec::new();

    for line in content.lines() {
        if line.trim() == "---" {
            if !seen_frontmatter {
                seen_frontmatter = true;
                in_frontmatter = true;
            } else if in_frontmatter {
                in_frontmatter = false;
            }
            lines.push(line.to_string());
            continue;
        }
        if in_frontmatter && line.trim_start().starts_with("core_skill:") {
            let replacement = format!("  core_skill: {skill_rel}");
            changed |= line != replacement;
            lines.push(replacement);
        } else if line.trim_start().starts_with("> **CORE-MEMORY**") {
            changed |= line != tag;
            lines.push(tag.clone());
        } else {
            lines.push(line.to_string());
        }
    }

    let mut flattened = lines.join("\n");
    if content.ends_with('\n') {
        flattened.push('\n');
    }
    if changed {
        if check {
            println!("would flatten memory {slug} -> {skill_rel}");
        } else {
            std::fs::write(memory_path, &flattened).unwrap_or_else(|error| {
                eprintln!("write memory {slug}: {error}");
                std::process::exit(1);
            });
            println!("flattened memory {slug} -> {skill_rel}");
        }
    }
    (flattened, old_skill)
}

fn remove_old_mapping(root: &Path, old_skill: Option<&str>, flat_skill: &str) {
    let Some(old_skill) = old_skill else {
        return;
    };
    if old_skill == flat_skill {
        return;
    }
    let old_path = root.join(old_skill);
    if old_path.is_file() {
        std::fs::remove_file(&old_path).unwrap_or_else(|error| {
            eprintln!("remove old mapped skill {old_skill}: {error}");
            std::process::exit(1);
        });
        println!("removed old mapped skill {old_skill}");
    }
}

fn promote(root: &Path, memory_dir: &Path, slug: &str, check: bool) {
    let mem_path = memory_dir.join(format!("{slug}.md"));
    if !mem_path.is_file() {
        eprintln!(
            "promote {slug}: memory file not found ({})",
            mem_path.display()
        );
        return;
    }
    let mut content = std::fs::read_to_string(&mem_path).unwrap_or_default();
    let skill_rel = flat_skill_rel(slug);

    let mut changed = false;
    // 1. frontmatter keys under `metadata:`. Check the FRONTMATTER only — a
    //    memory body may legitimately mention `core_memory:` in prose.
    if !parse_meta(&content).core_memory {
        content = insert_frontmatter_keys(&content, slug, &skill_rel);
        changed = true;
    }
    // 2. visible body tag right after the frontmatter. Match the actual tag
    //    LINE, not any prose mention of "**CORE-MEMORY**".
    if !has_body_tag(&content) {
        content = insert_body_tag(&content, &skill_rel);
        changed = true;
    }
    if changed {
        if check {
            println!("would promote memory {slug} (add core_memory + CORE-MEMORY tag)");
        } else {
            std::fs::write(&mem_path, &content).unwrap_or_else(|e| {
                eprintln!("write memory {slug}: {e}");
                std::process::exit(1);
            });
            println!("promoted memory {slug}");
        }
    } else {
        println!("memory {slug} already core");
    }
    let (content, old_skill) = flatten_mapping(&mem_path, slug, &content, check);
    write_mapped_skill(root, slug, &content, check);
    if !check {
        remove_old_mapping(root, old_skill.as_deref(), &flat_skill_rel(slug));
    }
}

fn demote(root: &Path, memory_dir: &Path, slug: &str, check: bool) {
    let mem_path = memory_dir.join(format!("{slug}.md"));
    let Ok(content) = std::fs::read_to_string(&mem_path) else {
        eprintln!(
            "demote {slug}: memory file not found ({})",
            mem_path.display()
        );
        return;
    };
    let mapped_skill = parse_meta(&content).core_skill;
    let cleaned: String = content
        .lines()
        .filter(|line| {
            let trimmed = line.trim();
            !(trimmed.starts_with("core_memory:")
                || trimmed.starts_with("core_skill:")
                || trimmed.starts_with("> **CORE-MEMORY**"))
        })
        .collect::<Vec<_>>()
        .join("\n");
    let cleaned = if content.ends_with('\n') {
        format!("{cleaned}\n")
    } else {
        cleaned
    };
    if check {
        println!("would demote memory {slug}");
    } else {
        std::fs::write(&mem_path, cleaned).ok();
        println!("demoted memory {slug}");
    }

    let Some(skill_rel) = mapped_skill else {
        return;
    };
    let skill_path = root.join(&skill_rel);
    if skill_path.is_file() {
        if check {
            println!("would remove mapped skill {skill_rel}");
        } else {
            std::fs::remove_file(&skill_path).ok();
            println!("removed mapped skill {skill_rel}");
        }
    }
}

/// Insert `core_memory: true` + `core_skill: <rel>` right after the `metadata:`
/// line (2-space indent to match the store's frontmatter style).
fn insert_frontmatter_keys(content: &str, _slug: &str, skill_rel: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut inserted = false;
    for line in content.lines() {
        out.push(line.to_string());
        if !inserted && line.trim() == "metadata:" {
            out.push("  core_memory: true".to_string());
            out.push(format!("  core_skill: {skill_rel}"));
            inserted = true;
        }
    }
    if !inserted {
        // No metadata block: add keys just before the closing frontmatter `---`.
        out.clear();
        let mut seen_open = false;
        for line in content.lines() {
            if line.trim() == "---" {
                if !seen_open {
                    seen_open = true;
                    out.push(line.to_string());
                    continue;
                } else {
                    out.push("metadata:".to_string());
                    out.push("  core_memory: true".to_string());
                    out.push(format!("  core_skill: {skill_rel}"));
                    out.push(line.to_string());
                    // append the rest verbatim below by breaking to a second pass
                    let idx = content
                        .find("\n---")
                        .map(|i| i + 4)
                        .unwrap_or(content.len());
                    out.push(content[idx..].trim_start_matches('\n').to_string());
                    return out.join("\n");
                }
            }
            out.push(line.to_string());
        }
    }
    let joined = out.join("\n");
    if content.ends_with('\n') {
        format!("{joined}\n")
    } else {
        joined
    }
}

/// True when the body already carries the visible CORE-MEMORY tag LINE (as
/// opposed to prose that merely mentions the string "**CORE-MEMORY**").
fn has_body_tag(content: &str) -> bool {
    strip_frontmatter(content)
        .lines()
        .any(|l| l.trim_start().starts_with("> **CORE-MEMORY**"))
}

/// Insert the visible CORE-MEMORY blockquote tag immediately after frontmatter.
fn insert_body_tag(content: &str, skill_rel: &str) -> String {
    let tag = format!(
        "> **CORE-MEMORY** — mirrored to skill `{skill_rel}` (sync: `scripts/sync-memory-skill.rs`; lint: `scripts/lint-memory-skill-sync.rs`)."
    );
    // Find the end of the frontmatter block.
    if let Some(rest) = content.strip_prefix("---\n") {
        if let Some(idx) = rest.find("\n---\n") {
            let fm_end = 4 + idx + 5; // len("---\n") + idx + len("\n---\n")
            let (head, body) = content.split_at(fm_end);
            let body = body.trim_start_matches('\n');
            return format!("{head}\n{tag}\n\n{body}");
        }
    }
    // Fallback: prepend.
    format!("{tag}\n\n{content}")
}

fn write_mapped_skill(root: &Path, slug: &str, memory_content: &str, check: bool) {
    let meta = parse_meta(memory_content);
    let Some(skill_rel) = &meta.core_skill else {
        eprintln!("write {slug}: core memory has no core_skill path");
        std::process::exit(1);
    };
    let expected = flat_skill_rel(slug);
    if skill_rel != &expected {
        eprintln!("write {slug}: mapped skill must use flat path '{expected}'");
        std::process::exit(1);
    }

    let body = canonical_body(memory_content);
    let description = meta.description.replace('"', "'");
    let skill = format!(
        "---\nname: {}\ndescription: \"{}\"\n---\n\n{}\n",
        meta.name, description, body
    );

    let path = root.join(skill_rel);
    if check {
        let current = std::fs::read_to_string(&path).unwrap_or_default();
        let verb = if !path.is_file() {
            "create"
        } else if current != skill {
            "update"
        } else {
            "keep"
        };
        println!("would {verb} mapped skill {skill_rel}");
        return;
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    std::fs::write(&path, skill).unwrap_or_else(|error| {
        eprintln!("write mapped skill {skill_rel}: {error}");
        std::process::exit(1);
    });
    println!("wrote mapped skill {skill_rel}");
}

// ---- shared extraction/normalization (kept identical in lint-memory-skill-sync.rs) ----

struct Meta {
    #[allow(dead_code)]
    name: String,
    description: String,
    core_memory: bool,
    #[allow(dead_code)]
    core_skill: Option<String>,
}

fn parse_meta(content: &str) -> Meta {
    let mut m = Meta {
        name: String::new(),
        description: String::new(),
        core_memory: false,
        core_skill: None,
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
        } else if let Some(v) = t.strip_prefix("core_memory:") {
            m.core_memory = v.trim() == "true";
        } else if let Some(v) = t.strip_prefix("core_skill:") {
            m.core_skill = Some(unquote(v.trim()));
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

fn read_md_files(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.extension().and_then(|s| s.to_str()) == Some("md") {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}

fn stem(p: &Path) -> String {
    p.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string()
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
