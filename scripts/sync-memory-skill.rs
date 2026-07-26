#!/usr/bin/env rust-script
//! Sync CORE memories to their mirror skill files (memory is the source of truth).
//!
//! Companion to scripts/lint-memory-skill-sync.rs. See that file's header for the
//! model: a memory is CORE when its frontmatter has `core_memory: true`; each
//! core memory mirrors to `.claude/skills/core-memory/<slug>.md`.
//!
//! Memory store is FILE-BASED markdown (not sqlite); override with HERMIT_MEMORY_DIR.
//!
//! Usage:
//!   scripts/sync-memory-skill.rs                     # regenerate ALL mirrors
//!   scripts/sync-memory-skill.rs --promote <slug>..  # mark memories core + mirror
//!   scripts/sync-memory-skill.rs --demote  <slug>..  # unmark + remove mirror
//!   scripts/sync-memory-skill.rs --check             # dry-run (no writes), list plan
//!
//! Promote is idempotent: it adds `core_memory: true` + `core_skill: <path>` under
//! `metadata:`, inserts a visible `> **CORE-MEMORY**` body tag, then writes the
//! mirror. Regenerate mode (no args) refreshes every mirror from its memory body.
use std::path::{Path, PathBuf};

const SKILL_SUBDIR: &str = ".claude/skills/core-memory";
const DEFAULT_MEMORY_DIR: &str =
    "/home/newton/.claude/projects/-home-newton-work-dev-hermit/memory";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let root = find_root();
    let memory_dir = memory_dir();
    let skill_dir = root.join(SKILL_SUBDIR);
    std::fs::create_dir_all(&skill_dir).ok();

    if !memory_dir.is_dir() {
        eprintln!("memory dir not found: {} (set HERMIT_MEMORY_DIR)", memory_dir.display());
        std::process::exit(2);
    }

    let check = args.iter().any(|a| a == "--check");
    let mode = args.first().map(|s| s.as_str());

    match mode {
        Some("--promote") => {
            let slugs: Vec<String> = args[1..].iter().filter(|a| !a.starts_with("--")).cloned().collect();
            if slugs.is_empty() {
                eprintln!("--promote needs at least one memory slug");
                std::process::exit(2);
            }
            for slug in &slugs {
                promote(&memory_dir, &skill_dir, slug, check);
            }
        }
        Some("--demote") => {
            let slugs: Vec<String> = args[1..].iter().filter(|a| !a.starts_with("--")).cloned().collect();
            if slugs.is_empty() {
                eprintln!("--demote needs at least one memory slug");
                std::process::exit(2);
            }
            for slug in &slugs {
                demote(&memory_dir, &skill_dir, slug, check);
            }
        }
        _ => {
            // Regenerate every mirror from its core memory.
            let mut n = 0;
            for entry in read_md_files(&memory_dir) {
                let slug = stem(&entry);
                if slug == "MEMORY" {
                    continue;
                }
                let content = std::fs::read_to_string(&entry).unwrap_or_default();
                if parse_meta(&content).core_memory {
                    write_mirror(&skill_dir, &slug, &content, check);
                    n += 1;
                }
            }
            println!("{} {} mirror(s)", if check { "would refresh" } else { "refreshed" }, n);
        }
    }
}

fn promote(memory_dir: &Path, skill_dir: &Path, slug: &str, check: bool) {
    let mem_path = memory_dir.join(format!("{slug}.md"));
    if !mem_path.is_file() {
        eprintln!("promote {slug}: memory file not found ({})", mem_path.display());
        return;
    }
    let mut content = std::fs::read_to_string(&mem_path).unwrap_or_default();
    let skill_rel = format!("{SKILL_SUBDIR}/{slug}.md");

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
    write_mirror(skill_dir, slug, &content, check);
}

fn demote(memory_dir: &Path, skill_dir: &Path, slug: &str, check: bool) {
    let mem_path = memory_dir.join(format!("{slug}.md"));
    if let Ok(content) = std::fs::read_to_string(&mem_path) {
        let cleaned: String = content
            .lines()
            .filter(|l| {
                let t = l.trim();
                !(t.starts_with("core_memory:")
                    || t.starts_with("core_skill:")
                    || t.trim_start().starts_with("> **CORE-MEMORY**"))
            })
            .collect::<Vec<_>>()
            .join("\n");
        let cleaned = if content.ends_with('\n') { format!("{cleaned}\n") } else { cleaned };
        if check {
            println!("would demote memory {slug}");
        } else {
            std::fs::write(&mem_path, cleaned).ok();
            println!("demoted memory {slug}");
        }
    }
    let skill_path = skill_dir.join(format!("{slug}.md"));
    if skill_path.is_file() {
        if check {
            println!("would remove mirror {slug}.md");
        } else {
            std::fs::remove_file(&skill_path).ok();
            println!("removed mirror {slug}.md");
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
                    let idx = content.find("\n---").map(|i| i + 4).unwrap_or(content.len());
                    out.push(content[idx..].trim_start_matches('\n').to_string());
                    return out.join("\n");
                }
            }
            out.push(line.to_string());
        }
    }
    let joined = out.join("\n");
    if content.ends_with('\n') { format!("{joined}\n") } else { joined }
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

fn write_mirror(skill_dir: &Path, slug: &str, memory_content: &str, check: bool) {
    let meta = parse_meta(memory_content);
    let body = canonical_body(memory_content);
    let desc = meta.description.replace('"', "'");
    let skill = format!(
        "---\n\
         name: core-memory-{slug}\n\
         description: \"{desc} (CORE-MEMORY mirror of memory/{slug}.md)\"\n\
         ---\n\
         \n\
         # CORE-MEMORY: {slug}\n\
         \n\
         <!-- GENERATED MIRROR of core memory `{slug}`. Source of truth is the memory\n\
         \x20    file `{slug}.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in\n\
         \x20    sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the\n\
         \x20    markers — edit the memory and re-run sync. -->\n\
         \n\
         <!-- BEGIN CORE-MEMORY-MIRROR (source: {slug}.md) -->\n\
         {body}\n\
         <!-- END CORE-MEMORY-MIRROR -->\n"
    );
    let path = skill_dir.join(format!("{slug}.md"));
    if check {
        let exists = path.is_file();
        let cur = if exists { std::fs::read_to_string(&path).unwrap_or_default() } else { String::new() };
        let verb = if !exists { "create" } else if cur != skill { "update" } else { "keep" };
        println!("would {verb} mirror {slug}.md");
        return;
    }
    std::fs::write(&path, skill).unwrap_or_else(|e| {
        eprintln!("write mirror {slug}: {e}");
        std::process::exit(1);
    });
    println!("wrote mirror {slug}.md");
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
    let mut m = Meta { name: String::new(), description: String::new(), core_memory: false, core_skill: None };
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

fn memory_dir() -> PathBuf {
    match std::env::var("HERMIT_MEMORY_DIR") {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => PathBuf::from(DEFAULT_MEMORY_DIR),
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
    p.file_stem().and_then(|s| s.to_str()).unwrap_or("").to_string()
}

fn find_root() -> PathBuf {
    let mut dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    loop {
        if dir.join(".gitmodules").is_file() && dir.join("hermit").is_dir() && dir.join("reverie").is_dir()
        {
            return dir;
        }
        if !dir.pop() {
            eprintln!("could not locate dev-hermit root (need .gitmodules + hermit/ + reverie/)");
            std::process::exit(2);
        }
    }
}
