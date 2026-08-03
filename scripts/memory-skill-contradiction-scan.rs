#!/usr/bin/env rust-script
//! REPORT-ONLY scanner for memory<->skill CONTRADICTIONS + bidirectional drift.
//!
//! WHY: the sync tooling ([[core-memory-skill-sync-tooling]]) keeps mapped skills
//! BYTE-EQUAL to their source memory, and lint-memory-skill-sync.rs enforces that
//! mapping is complete. Neither catches the harder failure: a skill and a memory
//! that are each internally well-formed but assert CONTRADICTORY facts (e.g. an
//! old skill says "main is unprotected" while a newer memory records that main
//! was locked down). This scanner surfaces those, plus which side of the mapping
//! is missing, so the coordinator can reconcile.
//!
//! This tool NEVER edits anything. It PROPOSES; the coordinator APPLIES.
//!
//! Two detectors:
//!   1. CONTRADICTION — a denylist of known-false claims
//!      (ci-hub/health/skill-contradiction-denylist.txt) matched against active
//!      skills / memories / slugs. This is the mechanical floor for claims we
//!      have already PROVEN false; unknown/new contradictions are for the woken
//!      coordinator to judge against `orc.sessionMemories()`.
//!   2. DRIFT (bidirectional) —
//!        MEMORY_WITHOUT_SKILL: a `core_memory: true` memory whose mapped
//!          `.claude/skills/<slug>.md` is missing.
//!        SKILL_WITHOUT_MEMORY: an active `.claude/skills/*.md` no core memory
//!          maps to. (lint-memory-skill-sync.rs is the authoritative gate for
//!          this; here it is a one-line summary alongside contradictions.)
//!
//! Memory store is FILE-BASED markdown (NOT sqlite / NOT the ORC session store).
//! Override its location with HERMIT_MEMORY_DIR.
//!
//! !!! ORC session-memory deletion hazard (for the coordinator who ACTS on this) !!!
//! `orc.sessionForget(index)` is 1-BASED POSITIONAL and indices SHIFT after each
//! removal. If a proposed reconciliation deletes MULTIPLE session memories, apply
//! the deletions in DESCENDING index order or you will corrupt unrelated entries.
//! This scanner therefore only ever REPORTS deletions; it does not perform them.
//!
//! Usage:
//!   scripts/memory-skill-contradiction-scan.rs           # human report
//!   scripts/memory-skill-contradiction-scan.rs --gate    # tick-hub gate:
//!                                                         #   prints state=/summary=/...
//!                                                         #   exits non-zero on any finding
use std::path::{Path, PathBuf};

const SKILL_DIR: &str = ".claude/skills";
const DENYLIST_REL: &str = "ci-hub/health/skill-contradiction-denylist.txt";

#[derive(Clone)]
enum Scope {
    Skill,
    Memory,
    Slug,
}

struct DenyEntry {
    scope: Scope,
    needles: Vec<String>, // all must be present (case-insensitive), joined by '+'
    reason: String,
    raw_needles: String,
}

struct Finding {
    kind: &'static str, // "CONTRADICTION" | "MEMORY_WITHOUT_SKILL" | "SKILL_WITHOUT_MEMORY"
    subject: String,    // file or slug the finding is about
    detail: String,
}

const USAGE: &str = "\
memory-skill-contradiction-scan.rs — report memory<->skill contradictions and drift (never edits)

USAGE:
  scripts/memory-skill-contradiction-scan.rs           human report
  scripts/memory-skill-contradiction-scan.rs --gate    tick-hub gate: state=/summary=, nonzero on any finding
  scripts/memory-skill-contradiction-scan.rs --list    one line per memory (name<TAB>slug<TAB>core)
  scripts/memory-skill-contradiction-scan.rs -h|--help show this help and exit (no side effects)
  scripts/memory-skill-contradiction-scan.rs --version print version and exit (no side effects)

Report-only: proposes, never applies. Memory store is file-based Markdown; override with HERMIT_MEMORY_DIR.";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return;
    }
    if args.iter().any(|a| a == "--version") {
        println!("memory-skill-contradiction-scan.rs 1.0");
        return;
    }
    let gate = args.iter().any(|a| a == "--gate");
    let list = args.iter().any(|a| a == "--list");
    let root = find_root();
    let memory_dir = memory_dir(&root);
    let skill_dir = root.join(SKILL_DIR);

    // --list: emit one line per FILE-STORE memory as `name<TAB>slug<TAB>core`,
    // for a caller (e.g. the ORC memory-skill-sync workflow) to set-diff against
    // `orc.sessionMemories()`. Names come from frontmatter (fallback: slug).
    if list {
        if !memory_dir.is_dir() {
            eprintln!("memory dir not found: {}", memory_dir.display());
            std::process::exit(2);
        }
        for entry in read_md_files(&memory_dir) {
            let slug = stem(&entry);
            if slug == "MEMORY" {
                continue;
            }
            let content = std::fs::read_to_string(&entry).unwrap_or_default();
            let meta = parse_meta(&content);
            let name = if meta.name.is_empty() {
                slug.clone()
            } else {
                meta.name.clone()
            };
            println!(
                "{name}\t{slug}\t{}",
                if meta.core_memory { "core" } else { "plain" }
            );
        }
        return;
    }

    if !memory_dir.is_dir() {
        // In gate mode a broken scanner must itself alert (drift detection is down).
        if gate {
            emit("error", "memory-dir-missing (set HERMIT_MEMORY_DIR)", 0, 0);
        } else {
            eprintln!(
                "memory dir not found: {} (set HERMIT_MEMORY_DIR)",
                memory_dir.display()
            );
        }
        std::process::exit(2);
    }

    // ---- load memories ----
    let mut memories: Vec<(String, String, Meta)> = Vec::new(); // (slug, body_content, meta)
    for entry in read_md_files(&memory_dir) {
        let slug = stem(&entry);
        if slug == "MEMORY" {
            continue;
        }
        let content = std::fs::read_to_string(&entry).unwrap_or_default();
        let meta = parse_meta(&content);
        memories.push((slug, content, meta));
    }
    memories.sort_by(|a, b| a.0.cmp(&b.0));

    // ---- load active skills ----
    let mut skills: Vec<(String, String)> = Vec::new(); // (slug, content)
    for path in read_skill_files(&skill_dir) {
        let slug = stem(&path);
        let content = std::fs::read_to_string(&path).unwrap_or_default();
        skills.push((slug, content));
    }
    skills.sort_by(|a, b| a.0.cmp(&b.0));

    // ---- load denylist ----
    let (denylist, denylist_note) = load_denylist(&root.join(DENYLIST_REL));

    let mut findings: Vec<Finding> = Vec::new();

    // ---- detector 1: contradictions ----
    for entry in &denylist {
        match entry.scope {
            Scope::Skill => {
                for (slug, content) in &skills {
                    if all_needles_present(content, &entry.needles) {
                        findings.push(Finding {
                            kind: "CONTRADICTION",
                            subject: format!("{SKILL_DIR}/{slug}.md"),
                            detail: format!(
                                "matches [{}] -> {}\n        {}",
                                entry.raw_needles,
                                entry.reason,
                                example_lines(content, &entry.needles)
                            ),
                        });
                    }
                }
            }
            Scope::Memory => {
                for (slug, content, _m) in &memories {
                    if all_needles_present(content, &entry.needles) {
                        findings.push(Finding {
                            kind: "CONTRADICTION",
                            subject: format!("memory/{slug}.md"),
                            detail: format!(
                                "matches [{}] -> {}\n        {}",
                                entry.raw_needles,
                                entry.reason,
                                example_lines(content, &entry.needles)
                            ),
                        });
                    }
                }
            }
            Scope::Slug => {
                let target = entry.needles.join("+").to_ascii_lowercase();
                for (slug, _c) in &skills {
                    if slug.to_ascii_lowercase() == target {
                        findings.push(Finding {
                            kind: "CONTRADICTION",
                            subject: format!("{SKILL_DIR}/{slug}.md"),
                            detail: format!("slug is denylisted -> {}", entry.reason),
                        });
                    }
                }
                for (slug, _c, _m) in &memories {
                    if slug.to_ascii_lowercase() == target {
                        findings.push(Finding {
                            kind: "CONTRADICTION",
                            subject: format!("memory/{slug}.md"),
                            detail: format!("slug is denylisted -> {}", entry.reason),
                        });
                    }
                }
            }
        }
    }

    // ---- detector 2: bidirectional drift ----
    // core memories and the flat skill each maps to.
    let mut mapped_skill_slugs: Vec<String> = Vec::new();
    for (slug, _content, meta) in &memories {
        if !meta.core_memory {
            continue;
        }
        let expected_rel = flat_skill_rel(slug);
        // trust the memory's declared mapping only if it matches the flat convention.
        let declared_ok = meta.core_skill.as_deref() == Some(expected_rel.as_str());
        let skill_exists = root.join(&expected_rel).is_file();
        if declared_ok && skill_exists {
            mapped_skill_slugs.push(slug.clone());
        } else {
            findings.push(Finding {
                kind: "MEMORY_WITHOUT_SKILL",
                subject: format!("memory/{slug}.md"),
                detail: if !declared_ok {
                    format!(
                        "core_memory:true but core_skill != '{expected_rel}' (got {:?}); run sync-memory-skill.rs",
                        meta.core_skill
                    )
                } else {
                    format!("mapped skill {expected_rel} is missing; run sync-memory-skill.rs")
                },
            });
        }
    }
    for (slug, _content) in &skills {
        if !mapped_skill_slugs.iter().any(|s| s == slug) {
            findings.push(Finding {
                kind: "SKILL_WITHOUT_MEMORY",
                subject: format!("{SKILL_DIR}/{slug}.md"),
                detail: "active skill has no core_memory source; adopt via sync-memory-skill.rs --adopt-skill or delete".to_string(),
            });
        }
    }

    // ---- output ----
    let contradictions = findings
        .iter()
        .filter(|f| f.kind == "CONTRADICTION")
        .count();
    let drift = findings.len() - contradictions;

    if gate {
        let state = match (contradictions, drift) {
            (0, 0) => "ok",
            (_, 0) => "contradiction",
            (0, _) => "drift",
            _ => "both",
        };
        let summary = if findings.is_empty() {
            format!(
                "in-sync: {} skills / {} memories, denylist {} entries clean",
                skills.len(),
                memories.len(),
                denylist.len()
            )
        } else {
            let mut parts: Vec<String> = Vec::new();
            if contradictions > 0 {
                parts.push(format!("{contradictions} contradiction(s)"));
            }
            if drift > 0 {
                parts.push(format!("{drift} drift"));
            }
            // name the first few subjects so the wakeup title is actionable.
            let named: Vec<String> = findings.iter().take(3).map(|f| f.subject.clone()).collect();
            format!("{} — {}", parts.join(" + "), named.join(", "))
        };
        emit(state, &summary, contradictions, drift);
        // Full detail on stdout too, so the plugin's wakeup body carries it.
        if !findings.is_empty() {
            print_report(&findings, &denylist_note, true);
        }
        std::process::exit(if findings.is_empty() { 0 } else { 1 });
    }

    // human report
    print_report(&findings, &denylist_note, false);
    println!();
    println!(
        "scanned: {} active skills, {} memories, {} denylist entries",
        skills.len(),
        memories.len(),
        denylist.len()
    );
    println!("findings: {contradictions} contradiction(s), {drift} drift");
    if findings.is_empty() {
        println!("RESULT: CLEAN");
        std::process::exit(0);
    }
    println!(
        "RESULT: FINDINGS (report-only — coordinator reconciles; see deletion hazard in header)"
    );
    std::process::exit(1);
}

fn emit(state: &str, summary: &str, contradictions: usize, drift: usize) {
    println!("state={state}");
    println!("summary={}", one_line(summary));
    println!("contradictions={contradictions}");
    println!("drift={drift}");
}

fn print_report(findings: &[Finding], denylist_note: &str, gate: bool) {
    if !denylist_note.is_empty() {
        println!("NOTE: {denylist_note}");
    }
    if findings.is_empty() {
        return;
    }
    // ACTION: line so the plugin's actionable-line filter forwards this in gate runs.
    if gate {
        println!(
            "ACTION: memory<->skill reconciliation needed ({} finding(s)) — REPORT-ONLY, coordinator applies",
            findings.len()
        );
    }
    for f in findings {
        println!("  {:<20} {}", f.kind, f.subject);
        println!("        {}", f.detail);
    }
    println!(
        "  RECONCILE: this is a PROPOSAL. If reconciliation deletes ORC session memories,\n\
         \x20            apply orc.sessionForget() in DESCENDING index order (1-based, indices shift)."
    );
}

fn all_needles_present(haystack: &str, needles: &[String]) -> bool {
    let lower = haystack.to_ascii_lowercase();
    needles
        .iter()
        .all(|n| lower.contains(&n.to_ascii_lowercase()))
}

/// Up to two example lines from `content` containing the rarest needle, for context.
fn example_lines(content: &str, needles: &[String]) -> String {
    // pick the last needle as the "focus" term (denylist authors put the specific one last).
    let focus = needles
        .last()
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_default();
    let mut hits: Vec<String> = Vec::new();
    for (i, line) in content.lines().enumerate() {
        if line.to_ascii_lowercase().contains(&focus) {
            hits.push(format!("L{}: {}", i + 1, one_line(line.trim())));
            if hits.len() >= 2 {
                break;
            }
        }
    }
    if hits.is_empty() {
        "(matched across lines)".to_string()
    } else {
        hits.join("  |  ")
    }
}

fn one_line(s: &str) -> String {
    let joined = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if joined.len() > 240 {
        format!("{}…", &joined[..240])
    } else {
        joined
    }
}

/// Returns (entries, note). `note` is non-empty when the denylist is absent/empty.
fn load_denylist(path: &Path) -> (Vec<DenyEntry>, String) {
    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => {
            let note = format!(
                "denylist {} not found — contradiction detection disabled (drift check still runs)",
                path.display()
            );
            return (Vec::new(), note);
        }
    };
    let mut entries = Vec::new();
    for (lineno, line) in content.lines().enumerate() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = t.splitn(3, '|').map(|p| p.trim()).collect();
        if parts.len() != 3 {
            eprintln!(
                "denylist:{}: skipping malformed line (need 'scope | needles | reason'): {t}",
                lineno + 1
            );
            continue;
        }
        let scope = match parts[0] {
            "skill" => Scope::Skill,
            "memory" => Scope::Memory,
            "slug" => Scope::Slug,
            other => {
                eprintln!(
                    "denylist:{}: unknown scope '{other}' (skill|memory|slug)",
                    lineno + 1
                );
                continue;
            }
        };
        let needles: Vec<String> = parts[1]
            .split('+')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        if needles.is_empty() {
            eprintln!("denylist:{}: no needles", lineno + 1);
            continue;
        }
        entries.push(DenyEntry {
            scope,
            needles,
            reason: parts[2].to_string(),
            raw_needles: parts[1].to_string(),
        });
    }
    let note = if entries.is_empty() {
        format!("denylist {} has no usable entries", path.display())
    } else {
        String::new()
    };
    (entries, note)
}

// ---- shared extraction (mirrors lint-memory-skill-sync.rs) ----

struct Meta {
    #[allow(dead_code)]
    name: String,
    #[allow(dead_code)]
    description: String,
    core_memory: bool,
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

// ---- filesystem helpers (mirror lint-memory-skill-sync.rs) ----

fn flat_skill_rel(slug: &str) -> String {
    format!("{SKILL_DIR}/{slug}.md")
}

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

fn read_skill_files(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file() {
            let name = path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("");
            if name != "README.md" && path.extension().and_then(|ext| ext.to_str()) == Some("md") {
                out.push(path);
            }
            continue;
        }
        if !path.is_dir() {
            continue;
        }
        let folder_skill = path.join("SKILL.md");
        if folder_skill.is_file() {
            out.push(folder_skill);
            continue;
        }
        if path.file_name().and_then(|name| name.to_str()) == Some("core-memory") {
            out.extend(read_md_files(&path).into_iter().filter(|entry| {
                entry.file_name().and_then(|name| name.to_str()) != Some("README.md")
            }));
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
