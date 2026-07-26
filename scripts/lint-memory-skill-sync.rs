#!/usr/bin/env rust-script
//! Lint that every CORE memory stays in 1-1 sync with its mirror skill file.
//!
//! WHY: coordinator memories and the `.claude/skills/` files drift apart when a
//! policy/protocol/architecture decision is updated in one place but not the
//! other. This check makes the drift mechanical and loud instead of silent.
//!
//! MODEL (single-writer, mirrors ai_docs/transient/worktree-management-map.md):
//!   * The MEMORY file is the source of truth.
//!   * A memory is CORE when its frontmatter declares `core_memory: true`
//!     (under `metadata:`). It then also declares `core_skill: <path>`.
//!   * Each CORE memory has a generated MIRROR skill at
//!     `.claude/skills/core-memory/<slug>.md` whose body between the
//!     `<!-- BEGIN/END CORE-MEMORY-MIRROR -->` markers DUPLICATES the memory
//!     body (content, not a pointer).
//!
//! NOTE ON "sqlite": the task brief assumed memories live in a sqlite DB. This
//! project's memory store is FILE-BASED markdown; this linter reads that store.
//! Override its location with HERMIT_MEMORY_DIR.
//!
//! Exit code: 0 when every core memory has an in-sync mirror; 1 on any
//! MISSING / STALE / ORPHAN / metadata problem.
//!
//! Usage:
//!   scripts/lint-memory-skill-sync.rs            # lint, human report
//!   scripts/lint-memory-skill-sync.rs --quiet    # only print problems + summary
use std::path::{Path, PathBuf};

const SKILL_SUBDIR: &str = ".claude/skills/core-memory";
const DEFAULT_MEMORY_DIR: &str =
    "/home/newton/.claude/projects/-home-newton-work-dev-hermit/memory";

fn main() {
    let quiet = std::env::args().any(|a| a == "--quiet");
    let root = find_root();
    let memory_dir = memory_dir();
    let skill_dir = root.join(SKILL_SUBDIR);

    if !memory_dir.is_dir() {
        eprintln!("memory dir not found: {} (set HERMIT_MEMORY_DIR)", memory_dir.display());
        std::process::exit(2);
    }

    // Collect CORE memories from the file-based store.
    let mut core: Vec<(String, PathBuf, Meta)> = Vec::new();
    for entry in read_md_files(&memory_dir) {
        let slug = stem(&entry);
        if slug == "MEMORY" {
            continue;
        }
        let content = std::fs::read_to_string(&entry).unwrap_or_default();
        let meta = parse_meta(&content);
        if meta.core_memory {
            core.push((slug, entry, meta));
        }
    }
    core.sort_by(|a, b| a.0.cmp(&b.0));

    let mut problems: Vec<String> = Vec::new();
    let mut ok = 0usize;
    let mut seen_skill_slugs: Vec<String> = Vec::new();

    for (slug, path, meta) in &core {
        seen_skill_slugs.push(slug.clone());
        let want_skill_rel = format!("{SKILL_SUBDIR}/{slug}.md");
        // 1. metadata self-consistency.
        match &meta.core_skill {
            Some(decl) if decl == &want_skill_rel => {}
            Some(decl) => problems.push(format!(
                "META  {slug}: core_skill='{decl}' but expected '{want_skill_rel}'"
            )),
            None => problems.push(format!(
                "META  {slug}: core_memory:true but no core_skill declared"
            )),
        }
        // 2. body must carry the visible CORE-MEMORY tag.
        let content = std::fs::read_to_string(path).unwrap_or_default();
        if !content.contains("**CORE-MEMORY**") {
            problems.push(format!("TAG   {slug}: memory body missing '**CORE-MEMORY**' marker"));
        }
        // 3. mirror skill presence + content equality.
        let skill_path = skill_dir.join(format!("{slug}.md"));
        if !skill_path.is_file() {
            problems.push(format!("MISS  {slug}: no mirror skill at {want_skill_rel}"));
            continue;
        }
        let skill = std::fs::read_to_string(&skill_path).unwrap_or_default();
        let want = canonical_body(&content);
        match extract_mirror(&skill) {
            None => problems.push(format!(
                "MISS  {slug}: mirror skill lacks BEGIN/END CORE-MEMORY-MIRROR markers"
            )),
            Some(have) if have == want => {
                ok += 1;
                if !quiet {
                    println!("OK    {slug}");
                }
            }
            Some(_) => problems.push(format!(
                "STALE {slug}: mirror skill body differs from memory (run sync-memory-skill.rs)"
            )),
        }
    }

    // 4. orphan mirror skills whose source memory is gone / no longer core.
    if skill_dir.is_dir() {
        for entry in read_md_files(&skill_dir) {
            let slug = stem(&entry);
            if seen_skill_slugs.contains(&slug) {
                continue;
            }
            // Only generated mirror files (carrying the BEGIN marker) can be
            // orphans; hand-written notes like README.md are ignored.
            let body = std::fs::read_to_string(&entry).unwrap_or_default();
            if body.contains(BEGIN) {
                problems.push(format!(
                    "ORPH  {slug}: mirror skill exists but memory is missing or not core"
                ));
            }
        }
    }

    println!();
    println!("core memories: {}  in-sync: {}  problems: {}", core.len(), ok, problems.len());
    if problems.is_empty() {
        println!("RESULT: PASS — every core memory has an in-sync mirror skill.");
        std::process::exit(0);
    }
    println!("RESULT: FAIL");
    for p in &problems {
        println!("  {p}");
    }
    std::process::exit(1);
}

// ---- shared extraction/normalization (kept identical in sync-memory-skill.rs) ----

struct Meta {
    #[allow(dead_code)]
    name: String,
    #[allow(dead_code)]
    description: String,
    core_memory: bool,
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

/// Everything after the leading YAML frontmatter block.
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

/// Canonical, comparable form of a memory body: frontmatter removed, the visible
/// CORE-MEMORY marker line dropped, full-line HTML comments dropped, whitespace
/// normalized.
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

const BEGIN: &str = "<!-- BEGIN CORE-MEMORY-MIRROR";
const END: &str = "<!-- END CORE-MEMORY-MIRROR";

/// The normalized body between the mirror markers, if present.
fn extract_mirror(skill: &str) -> Option<String> {
    let bstart = skill.find(BEGIN)?;
    let after_begin = skill[bstart..].find("-->")? + bstart + 3;
    let estart = skill[after_begin..].find(END)? + after_begin;
    let inner = &skill[after_begin..estart];
    let lines: Vec<String> = inner.lines().map(|l| l.trim_end().to_string()).collect();
    Some(normalize_lines(lines))
}

/// Collapse any run of blank lines to a single blank; strip leading/trailing blanks.
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
