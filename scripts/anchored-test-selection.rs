#!/usr/bin/env rust-script
//! Copyright (c) Meta Platforms, Inc. and affiliates.
//! All rights reserved.
//!
//! Anchored, one-hop test selection for the Hermit CI DAG.
//!
//! This is the owner's test-selection policy, made executable:
//!
//!   1. ANCHOR on a previous FULL GREEN result.
//!   2. DIFF the paths changed since that anchor.
//!   3. MAP changed paths -> the DAG STEP(S) they hit.
//!   4. RUN ONLY those steps (emit a dependency-closed subset DAG).
//!
//! Soundness rests entirely on the anchor: everything untouched since a full
//! green was already proven green AT that anchor, so it need not run again.
//!
//! HARD CONSTRAINTS (enforced here, not by convention):
//!   * ONE HOP ONLY. The anchor MUST be a FULL green (`selection_mode == full`),
//!     never another incremental/selective green. We take the anchor from
//!     `ci-hub newest-green`, which already returns only clean full greens
//!     at/above the merge-gate floor, and we re-assert `selection_mode == full`
//!     before trusting it. No full-green anchor => conservative FULL run.
//!   * A NARROWED RUN STATES its anchor SHA, the changed paths, the DAG steps
//!     SELECTED, and the DAG steps SKIPPED. A selection that silently
//!     under-runs is a fake green; enumerating the skipped set makes any
//!     under-run visible.
//!   * CONSERVATIVE MAPPING. Any path not in the map => RUN EVERYTHING. A
//!     missing entry is a full run, never a skip. Over-running only costs time;
//!     under-running produces a false green.
//!
//! THE MAP: changed path -> DAG step(s), keyed on the node names that already
//! exist in `ci/dag/{portable,privileged}.json` (no parallel taxonomy). The
//! load-bearing distinction, matched MOST-SPECIFIC-FIRST:
//!
//!   * `reverie/` CORE (reverie, reverie-ptrace, reverie-syscalls, ...) lights
//!     up ALL of Hermit that depends on it => FULL. No narrowing; we say so.
//!   * `reverie/reverie-kvm/` -> only the KVM (privileged-lane) steps.
//!     `reverie/reverie-dbi/`, `reverie/reverie-liteinst/`,
//!     `reverie/reverie-e9patch/` likewise narrow to one backend.
//!   * Hermit backend dirs (`detcore-dbi/`, `detcore-sabre/`) -> that backend.
//!
//! A naive `reverie/` prefix match would collapse core-vs-backend and destroy
//! the whole benefit, so backend-crate rules are matched BEFORE the general
//! `reverie/**` -> FULL rule (first-match-wins, most-specific-first).
//!
//! Usage:
//!   scripts/anchored-test-selection.rs                      # anchor auto, diff HEAD
//!   scripts/anchored-test-selection.rs --files a.rs b.rs    # explicit path list
//!   git diff --name-only A..B | scripts/anchored-test-selection.rs --files -
//!   scripts/anchored-test-selection.rs --anchor <sha> --files ...   # fixed anchor
//!   scripts/anchored-test-selection.rs --format json
//!   scripts/anchored-test-selection.rs --emit-dag portable --out /tmp/sub.json
//!   scripts/anchored-test-selection.rs --self-test
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;

use serde_json::Value;

// ---------------------------------------------------------------------------
// Glob matching (dependency-free, git-pathspec-like). Same semantics as
// hermit/ci/select-tests.rs so the two tools agree on what a pattern means.
// A pattern with no `/` (e.g. `Cargo.toml`) matches only that exact path, so a
// root-level `Cargo.toml` is distinct from `reverie/reverie-kvm/Cargo.toml`.
// ---------------------------------------------------------------------------

fn glob_match(pattern: &str, text: &str) -> bool {
    glob_inner(pattern.as_bytes(), text.as_bytes())
}

fn glob_inner(p: &[u8], t: &[u8]) -> bool {
    if p.is_empty() {
        return t.is_empty();
    }
    if p[0] == b'*' {
        if p.len() >= 2 && p[1] == b'*' {
            let mut rest = &p[2..];
            if rest.first() == Some(&b'/') {
                rest = &rest[1..];
            }
            let mut i = 0;
            loop {
                if glob_inner(rest, &t[i..]) {
                    return true;
                }
                if i >= t.len() {
                    return false;
                }
                i += 1;
            }
        } else {
            let mut i = 0;
            loop {
                if glob_inner(&p[1..], &t[i..]) {
                    return true;
                }
                if i >= t.len() || t[i] == b'/' {
                    return false;
                }
                i += 1;
            }
        }
    } else if p[0] == b'?' {
        !t.is_empty() && t[0] != b'/' && glob_inner(&p[1..], &t[1..])
    } else {
        !t.is_empty() && p[0] == t[0] && glob_inner(&p[1..], &t[1..])
    }
}

fn matches_any(globs: &[&str], file: &str) -> bool {
    globs.iter().any(|g| glob_match(g, file))
}

// ---------------------------------------------------------------------------
// DAG universe: read the REAL node names + deps + duration hints from the CI
// graph. Node identity is lane-qualified ("portable:test.dbi_parity") because
// several job names (e2e.metadata, e2e.manifest_backend_parity_c, ...) exist in
// BOTH lanes and mean different things.
// ---------------------------------------------------------------------------

#[allow(dead_code)] // id/lane retained for readability; derivable from the map key
struct Node {
    id: String, // "lane:group.job"
    lane: String,
    deps: Vec<String>, // lane-qualified
    est_s: u64,
}

struct Dag {
    nodes: BTreeMap<String, Node>,
}

impl Dag {
    fn load(hermit: &Path) -> Dag {
        let mut nodes = BTreeMap::new();
        for lane in ["portable", "privileged"] {
            let path = hermit.join(format!("ci/dag/{lane}.json"));
            let raw = std::fs::read_to_string(&path)
                .unwrap_or_else(|e| fail(&format!("cannot read {}: {e}", path.display())));
            let v: Value = serde_json::from_str(&raw)
                .unwrap_or_else(|e| fail(&format!("invalid JSON in {}: {e}", path.display())));
            for s in v["steps"].as_array().unwrap_or(&vec![]) {
                let job = format!(
                    "{}.{}",
                    s["group"].as_str().unwrap_or(""),
                    s["job"].as_str().unwrap_or("")
                );
                let id = format!("{lane}:{job}");
                let deps = s["deps"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_str().map(|d| format!("{lane}:{d}")))
                            .collect()
                    })
                    .unwrap_or_default();
                let est_s = s["hint"]["est_duration_s"].as_u64().unwrap_or(0);
                nodes.insert(id.clone(), Node { id, lane: lane.to_string(), deps, est_s });
            }
        }
        if nodes.is_empty() {
            fail("no DAG nodes loaded (is --hermit pointing at a Hermit checkout?)");
        }
        Dag { nodes }
    }

    fn all_ids(&self) -> BTreeSet<String> {
        self.nodes.keys().cloned().collect()
    }

    /// Add every transitive dep predecessor of the given nodes (so the subset
    /// DAG is runnable: a selected test pulls in the builds it needs).
    fn close_over_deps(&self, seed: &BTreeSet<String>) -> BTreeSet<String> {
        let mut out = seed.clone();
        let mut stack: Vec<String> = seed.iter().cloned().collect();
        while let Some(n) = stack.pop() {
            if let Some(node) = self.nodes.get(&n) {
                for d in &node.deps {
                    if out.insert(d.clone()) {
                        stack.push(d.clone());
                    }
                }
            }
        }
        out
    }

    fn est_of(&self, ids: &BTreeSet<String>) -> u64 {
        ids.iter().filter_map(|i| self.nodes.get(i)).map(|n| n.est_s).sum()
    }

    /// The "manifest gate": the guest build + every e2e manifest execution node,
    /// across both lanes. This is the dominant wall-time cost the owner tracks.
    fn manifest_gate(&self, ids: &BTreeSet<String>) -> BTreeSet<String> {
        ids.iter()
            .filter(|id| {
                let job = id.split(':').nth(1).unwrap_or("");
                job.starts_with("e2e.manifest_") || job == "build.manifest_guests"
            })
            .cloned()
            .collect()
    }
}

// ---------------------------------------------------------------------------
// The path -> DAG-step map. An ordered, most-specific-first rule list;
// FIRST MATCH WINS per changed path. Step sets reference REAL lane-qualified
// node names; `load()` asserts every one exists in the live DAG (stale-map
// guard) so the map can never silently select a node that no longer runs.
// ---------------------------------------------------------------------------

#[derive(Clone)]
enum Action {
    Full,               // this path forces the whole suite
    Irrelevant,         // provably inert (docs); contributes no steps
    Steps(Vec<String>), // this path narrows to these lane-qualified steps
}

struct Rule {
    globs: Vec<&'static str>,
    action: Action,
    why: &'static str,
}

// Backend step sets, keyed on the exact nodes in ci/dag/{portable,privileged}.json.
// deps (builds) are added later by dependency closure; here we name the leaf work.
fn kvm_steps() -> Vec<String> {
    // KVM has no node literally named "kvm": KVM IS the privileged lane
    // (resource_caps {kvm:1}; the parity + applications cells carry resources
    // {kvm:1}). These are "the KVM steps".
    vec![
        "privileged:e2e.manifest_backend_parity_c".into(),
        "privileged:e2e.manifest_applications".into(),
    ]
}
fn dbi_steps() -> Vec<String> {
    vec![
        "portable:test.dbi_parity".into(),
        "portable:e2e.manifest_backend_parity_c".into(),
    ]
}
fn sabre_steps() -> Vec<String> {
    vec![
        "portable:test.sabre_examples".into(),
        "portable:e2e.manifest_backend_parity_c".into(),
    ]
}
fn liteinst_steps() -> Vec<String> {
    vec![
        "portable:test.liteinst_strict".into(),
        "portable:build.liteinst_runtime_release".into(),
        "portable:e2e.manifest_backend_parity_c".into(),
    ]
}
fn e9patch_steps() -> Vec<String> {
    // e9patch is binary-rewriting PREPROCESSING used with the ptrace backend
    // (hermit/AGENTS.md), not its own backend, so it has no dedicated node. It
    // is exercised in backend parity; narrow to those cells in both lanes.
    vec![
        "portable:e2e.manifest_backend_parity_c".into(),
        "privileged:e2e.manifest_backend_parity_c".into(),
    ]
}

// Always-on cheap safety gates added to any selective (non-full) run.
const PREFLIGHT: &[&str] = &[
    "portable:lint.rustfmt",
    "portable:check.backend_abstraction",
    "portable:check.portability_paths",
];

fn rules() -> Vec<Rule> {
    // ORDER IS SIGNIFICANT — first match wins, most-specific-first.
    vec![
        // (1) Build config / CI machinery => FULL (a change here can invalidate
        //     any node's result). Root Cargo.toml is exact (no `/`), so a
        //     backend crate's own Cargo.toml does NOT match here.
        Rule {
            globs: vec![
                "Cargo.toml",
                "Cargo.lock",
                "rust-toolchain.toml",
                ".cargo/**",
                "ci/**",
                "validate.sh",
                "scripts/lib/**",
                ".github/workflows/ci-portable.yml",
                ".github/workflows/ci-privileged.yml",
                ".github/workflows/merge-gate.yml",
            ],
            action: Action::Full,
            why: "build/CI machinery — can invalidate any node",
        },
        // (2) Provably inert => no steps. If EVERY changed path is inert the
        //     whole run is a skip; otherwise these paths simply add nothing.
        Rule {
            globs: vec![
                "**/*.md",
                "docs/**",
                "ai_docs/**",
                "experiments/**",
                "LICENSE",
                "CODEOWNERS",
                ".gitignore",
                "**/.gitignore",
                ".gitattributes",
                ".editorconfig",
                "**/*.png",
                "**/*.svg",
                "**/*.jpg",
            ],
            action: Action::Irrelevant,
            why: "docs/notes — CI-irrelevant",
        },
        // (3) BACKEND-SPECIFIC crates/dirs => that one backend. These MUST come
        //     before the general `reverie/**` rule below: this is the asymmetry.
        Rule { globs: vec!["reverie/reverie-kvm/**"], action: Action::Steps(kvm_steps()), why: "reverie-kvm backend crate -> KVM (privileged) steps only" },
        Rule { globs: vec!["reverie/reverie-dbi/**", "detcore-dbi/**"], action: Action::Steps(dbi_steps()), why: "DBI backend crate/dir -> DBI steps only" },
        Rule { globs: vec!["reverie/reverie-liteinst/**"], action: Action::Steps(liteinst_steps()), why: "reverie-liteinst backend crate -> LiteInst steps only" },
        Rule { globs: vec!["reverie/reverie-e9patch/**"], action: Action::Steps(e9patch_steps()), why: "reverie-e9patch preprocessing -> backend-parity steps only" },
        Rule { globs: vec!["detcore-sabre/**"], action: Action::Steps(sabre_steps()), why: "detcore-sabre backend dir -> SaBRe steps only" },
        // (4) REVERIE CORE (everything else under reverie/) => FULL. Core Reverie
        //     is a dependency of all of Hermit; a change here touches everything.
        Rule {
            globs: vec!["reverie/**"],
            action: Action::Full,
            why: "reverie CORE — depended on by all of Hermit",
        },
    ]
    // NO trailing catch-all: an unmatched path falls through to conservative
    // FULL in classify(), so a NEW/unmapped area can never be silently skipped.
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------

#[derive(Debug, PartialEq, Clone, Copy)]
enum Decision {
    Skip,
    Selective,
    Full,
}

fn decision_str(d: Decision) -> &'static str {
    match d {
        Decision::Skip => "skip",
        Decision::Selective => "selective",
        Decision::Full => "full",
    }
}

struct Selection {
    decision: Decision,
    selected: BTreeSet<String>, // lane-qualified, dependency-closed
    reasons: Vec<String>,
}

/// Classify ONE path against the ordered rule list (first match wins). A path
/// that matches nothing is FULL (conservative).
fn classify(path: &str) -> (Action, &'static str) {
    for r in rules() {
        if matches_any(&r.globs, path) {
            return (r.action.clone(), r.why);
        }
    }
    (Action::Full, "unmapped path — conservative full")
}

fn select(dag: &Dag, files: &[String]) -> Selection {
    let mut reasons = Vec::new();

    if files.is_empty() {
        return Selection {
            decision: Decision::Full,
            selected: dag.all_ids(),
            reasons: vec!["no changed-path information -> full suite".into()],
        };
    }

    let mut steps: BTreeSet<String> = BTreeSet::new();
    let mut any_full = false;
    let mut any_narrowing = false; // at least one path mapped to concrete steps
    let mut all_inert = true;
    let mut full_reasons: Vec<String> = Vec::new();

    for f in files {
        let (action, why) = classify(f);
        match action {
            Action::Full => {
                any_full = true;
                all_inert = false;
                full_reasons.push(format!("{f} -> FULL ({why})"));
            }
            Action::Irrelevant => {
                reasons.push(format!("{f} -> inert ({why})"));
            }
            Action::Steps(s) => {
                all_inert = false;
                any_narrowing = true;
                for id in &s {
                    steps.insert(id.clone());
                }
                reasons.push(format!("{f} -> {} ({why})", s.join(", ")));
            }
        }
    }

    if any_full {
        for r in full_reasons {
            reasons.push(r);
        }
        reasons.push("at least one path forces the whole suite".into());
        return Selection { decision: Decision::Full, selected: dag.all_ids(), reasons };
    }
    if !any_narrowing && all_inert {
        reasons.push("every changed path is CI-irrelevant -> nothing to run".into());
        return Selection { decision: Decision::Skip, selected: BTreeSet::new(), reasons };
    }
    if steps.is_empty() {
        // Defensive: not full, not all-inert, yet no steps. Fail safe to full.
        reasons.push("no steps mapped but paths not proven inert -> full suite".into());
        return Selection { decision: Decision::Full, selected: dag.all_ids(), reasons };
    }

    // Selective: add always-on preflight, verify every step exists, close deps.
    for pf in PREFLIGHT {
        if dag.nodes.contains_key(*pf) {
            steps.insert((*pf).to_string());
        }
    }
    let missing: Vec<String> =
        steps.iter().filter(|s| !dag.nodes.contains_key(*s)).cloned().collect();
    if !missing.is_empty() {
        reasons.push(format!(
            "map referenced {} node(s) absent from the live DAG ({}) -> full suite (stale map)",
            missing.len(),
            missing.join(", ")
        ));
        return Selection { decision: Decision::Full, selected: dag.all_ids(), reasons };
    }
    let closed = dag.close_over_deps(&steps);
    reasons.push(format!(
        "{} leaf step(s) + preflight + build deps -> {} of {} DAG steps",
        steps.len(),
        closed.len(),
        dag.nodes.len()
    ));
    Selection { decision: Decision::Selective, selected: closed, reasons }
}

// ---------------------------------------------------------------------------
// Anchor resolution (ONE HOP: full green only)
// ---------------------------------------------------------------------------

struct Anchor {
    sha: String,
    source: String,
}

/// Resolve the newest FULL-green anchor via `ci-hub newest-green --json`.
/// Returns None (=> conservative full) if there is no such anchor, or if the
/// returned record is not a full-coverage full-selection green. ONE HOP is
/// enforced here: we refuse anything whose `selection_mode` is not `full`.
fn resolve_anchor(ci_hub: &Path, branch: &str, no_fetch: bool) -> Option<Anchor> {
    let mut args = vec![
        "newest-green".to_string(),
        "--branch".to_string(),
        branch.to_string(),
        "--json".to_string(),
    ];
    if no_fetch {
        args.push("--no-fetch".to_string());
    }
    let out = Command::new(ci_hub).args(&args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    // newest-green wraps its JSON in `# COST` comment lines; take the JSON body.
    let stdout = String::from_utf8_lossy(&out.stdout);
    let json_start = stdout.find('{')?;
    let json_end = stdout.rfind('}')?;
    let body = &stdout[json_start..=json_end];
    let v: Value = serde_json::from_str(body).ok()?;
    let green = &v["report"]["green"];
    let sha = green["sha"].as_str()?.to_string();
    // Belt-and-suspenders one-hop assertion: newest-green already restricts to
    // clean full greens, but we re-check the carried conditions rather than
    // trust the tool's name.
    let mode = green["selection_mode"].as_str().unwrap_or("");
    let coverage = green["coverage"].as_str().unwrap_or("");
    let result = green["result"].as_str().unwrap_or("");
    if mode != "full" || coverage != "full" || result != "pass" {
        return None;
    }
    Some(Anchor { sha, source: format!("ci-hub newest-green --branch {branch}") })
}

// ---------------------------------------------------------------------------
// Changed-path discovery
// ---------------------------------------------------------------------------

fn git_lines(repo: &Path, args: &[&str]) -> Vec<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .unwrap_or_else(|e| fail(&format!("git {:?} failed: {e}", args)));
    if !out.status.success() {
        fail(&format!(
            "git -C {} {:?} failed: {}",
            repo.display(),
            args,
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Paths changed since the anchor: committed(anchor..HEAD) U staged/unstaged U
/// untracked. This is exactly the set whose green status the anchor does NOT
/// vouch for.
fn changed_since(repo: &Path, anchor: &str) -> Vec<String> {
    let mut set: BTreeSet<String> = BTreeSet::new();
    for f in git_lines(repo, &["diff", "--name-only", &format!("{anchor}..HEAD")]) {
        set.insert(f);
    }
    for f in git_lines(repo, &["diff", "--name-only", "HEAD"]) {
        set.insert(f);
    }
    for f in git_lines(repo, &["ls-files", "--others", "--exclude-standard"]) {
        set.insert(f);
    }
    set.into_iter().collect()
}

fn read_stdin_lines() -> Vec<String> {
    use std::io::Read;
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).ok();
    s.lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect()
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

fn by_lane(ids: &BTreeSet<String>) -> BTreeMap<String, Vec<String>> {
    let mut m: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for id in ids {
        let (lane, job) = id.split_once(':').unwrap_or(("?", id.as_str()));
        m.entry(lane.to_string()).or_default().push(job.to_string());
    }
    m
}

fn emit_human(
    dag: &Dag,
    anchor: &Option<Anchor>,
    files: &[String],
    sel: &Selection,
) {
    println!("=== anchored, one-hop test selection ===");
    match anchor {
        Some(a) => println!("anchor (FULL green): {}  [{}]", a.sha, a.source),
        None => println!("anchor (FULL green): NONE FOUND -> conservative FULL run"),
    }
    println!("decision: {}", decision_str(sel.decision));
    println!();
    println!("changed paths ({}):", files.len());
    for f in files {
        println!("  {f}");
    }
    if files.is_empty() {
        println!("  (none)");
    }
    println!();
    println!("reasoning:");
    for r in &sel.reasons {
        println!("  - {r}");
    }
    println!();

    let universe = dag.all_ids();
    let skipped: BTreeSet<String> = universe.difference(&sel.selected).cloned().collect();

    println!(
        "DAG steps SELECTED ({}/{}):",
        sel.selected.len(),
        universe.len()
    );
    for (lane, jobs) in by_lane(&sel.selected) {
        println!("  [{lane}] {}", jobs.join(", "));
    }
    if sel.selected.is_empty() {
        println!("  (none)");
    }
    println!();
    println!("DAG steps SKIPPED ({}/{}):", skipped.len(), universe.len());
    for (lane, jobs) in by_lane(&skipped) {
        println!("  [{lane}] {}", jobs.join(", "));
    }
    if skipped.is_empty() {
        println!("  (none)");
    }
    println!();

    // Wall-time model (est_duration_s hints; a proxy, not a measured run).
    let total_est = dag.est_of(&universe);
    let sel_est = dag.est_of(&sel.selected);
    let gate_all = dag.manifest_gate(&universe);
    let gate_sel = dag.manifest_gate(&sel.selected);
    let gate_all_est = dag.est_of(&gate_all);
    let gate_sel_est = dag.est_of(&gate_sel);
    println!("est-model work (sum of est_duration_s hints; NOT a measured wall):");
    println!(
        "  all steps:      {total_est}s   selected: {sel_est}s   ({:.0}% of full)",
        if total_est > 0 { 100.0 * sel_est as f64 / total_est as f64 } else { 0.0 }
    );
    println!(
        "  manifest gate:  {gate_all_est}s over {} nodes  ->  selected {gate_sel_est}s over {} nodes",
        gate_all.len(),
        gate_sel.len()
    );
    if sel.decision == Decision::Selective && gate_sel.len() < gate_all.len() {
        println!(
            "  => selection PRUNES the manifest gate: {} of {} manifest nodes skipped.",
            gate_all.len() - gate_sel.len(),
            gate_all.len()
        );
    } else if sel.decision == Decision::Full {
        println!("  => FULL run: the manifest gate runs in its entirety (no narrowing).");
    } else if sel.decision == Decision::Skip {
        println!("  => SKIP: the manifest gate does not run at all.");
    }
}

fn emit_json(dag: &Dag, anchor: &Option<Anchor>, files: &[String], sel: &Selection) {
    let universe = dag.all_ids();
    let skipped: BTreeSet<String> = universe.difference(&sel.selected).cloned().collect();
    let obj = serde_json::json!({
        "anchor_sha": anchor.as_ref().map(|a| a.sha.clone()),
        "anchor_source": anchor.as_ref().map(|a| a.source.clone()),
        "decision": decision_str(sel.decision),
        "changed_paths": files,
        "selected_steps": sel.selected.iter().cloned().collect::<Vec<_>>(),
        "skipped_steps": skipped.iter().cloned().collect::<Vec<_>>(),
        "selected_count": sel.selected.len(),
        "skipped_count": skipped.len(),
        "total_count": universe.len(),
        "est_total_s": dag.est_of(&universe),
        "est_selected_s": dag.est_of(&sel.selected),
        "manifest_gate_total_nodes": dag.manifest_gate(&universe).len(),
        "manifest_gate_selected_nodes": dag.manifest_gate(&sel.selected).len(),
        "reasons": sel.reasons,
    });
    println!("{}", serde_json::to_string_pretty(&obj).unwrap());
}

/// Write a dependency-closed subset of ci/dag/<lane>.json containing only the
/// selected nodes of that lane (with deps pruned to survivors) so run-dag.sh can
/// execute exactly the selection via RUN_DAG_FILE_OVERRIDE. Returns node count.
fn emit_dag(hermit: &Path, lane: &str, sel: &Selection, out: &Path) -> usize {
    let path = hermit.join(format!("ci/dag/{lane}.json"));
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| fail(&format!("cannot read {}: {e}", path.display())));
    let mut v: Value = serde_json::from_str(&raw)
        .unwrap_or_else(|e| fail(&format!("invalid JSON in {}: {e}", path.display())));
    let keep: BTreeSet<String> = sel
        .selected
        .iter()
        .filter_map(|id| id.strip_prefix(&format!("{lane}:")).map(String::from))
        .collect();
    let steps = v["steps"].as_array().cloned().unwrap_or_default();
    let kept: Vec<Value> = steps
        .into_iter()
        .filter_map(|mut s| {
            let job = format!(
                "{}.{}",
                s["group"].as_str().unwrap_or(""),
                s["job"].as_str().unwrap_or("")
            );
            if !keep.contains(&job) {
                return None;
            }
            if let Some(deps) = s["deps"].as_array() {
                let pruned: Vec<Value> = deps
                    .iter()
                    .filter(|d| d.as_str().map(|d| keep.contains(d)).unwrap_or(false))
                    .cloned()
                    .collect();
                s["deps"] = Value::Array(pruned);
            }
            Some(s)
        })
        .collect();
    let n = kept.len();
    v["steps"] = Value::Array(kept);
    std::fs::write(out, serde_json::to_string_pretty(&v).unwrap())
        .unwrap_or_else(|e| fail(&format!("cannot write {}: {e}", out.display())));
    n
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

fn fail(msg: &str) -> ! {
    eprintln!("anchored-test-selection: {msg}");
    std::process::exit(2);
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "-h" || a == "--help") {
        print_help();
        return;
    }
    if args.iter().any(|a| a == "--self-test") {
        self_test();
        return;
    }

    let mut hermit = PathBuf::from("hermit");
    let mut ci_hub = PathBuf::from("ci-hub/ci-hub");
    let mut branch = "main".to_string();
    let mut format = "human".to_string();
    let mut explicit_files: Option<Vec<String>> = None;
    let mut anchor_override: Option<String> = None;
    let mut no_fetch = false;
    let mut emit_dag_lane: Option<String> = None;
    let mut out_path: Option<PathBuf> = None;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--hermit" => { hermit = PathBuf::from(need(&args, i)); i += 2; }
            "--ci-hub" => { ci_hub = PathBuf::from(need(&args, i)); i += 2; }
            "--branch" => { branch = need(&args, i); i += 2; }
            "--anchor" => { anchor_override = Some(need(&args, i)); i += 2; }
            "--no-fetch" => { no_fetch = true; i += 1; }
            "--format" => { format = need(&args, i); i += 2; }
            "--emit-dag" => { emit_dag_lane = Some(need(&args, i)); i += 2; }
            "--out" => { out_path = Some(PathBuf::from(need(&args, i))); i += 2; }
            "--files" => {
                if args.get(i + 1).map(|s| s.as_str()) == Some("-") {
                    explicit_files = Some(read_stdin_lines());
                    i += 2;
                } else {
                    let mut files = Vec::new();
                    let mut j = i + 1;
                    while j < args.len() && !args[j].starts_with("--") {
                        files.push(args[j].clone());
                        j += 1;
                    }
                    explicit_files = Some(files);
                    i = j;
                }
            }
            other => fail(&format!("unknown argument: {other} (see --help)")),
        }
    }

    let dag = Dag::load(&hermit);

    // Resolve the ONE-HOP full-green anchor (unless the caller fixed one).
    let anchor = match &anchor_override {
        Some(sha) => Some(Anchor { sha: sha.clone(), source: "--anchor override".into() }),
        None => resolve_anchor(&ci_hub, &branch, no_fetch),
    };

    // Changed paths: explicit list wins; else diff since the anchor.
    let files = match (&explicit_files, &anchor) {
        (Some(f), _) => f.clone(),
        (None, Some(a)) => changed_since(&hermit, &a.sha),
        (None, None) => {
            // No anchor and no explicit files: cannot establish a delta.
            // Conservative FULL with an explicit reason.
            let sel = Selection {
                decision: Decision::Full,
                selected: dag.all_ids(),
                reasons: vec![
                    "no full-green anchor available and no --files given -> full suite \
                     (one hop cannot be established)".into(),
                ],
            };
            finish(&dag, &hermit, &anchor, &[], &sel, &format, &emit_dag_lane, &out_path);
            return;
        }
    };

    let mut sel = select(&dag, &files);
    if anchor.is_none() && sel.decision != Decision::Full {
        // Without a proven full-green anchor the delta is meaningless: nothing
        // vouches for the un-run steps. Fail safe to FULL.
        sel = Selection {
            decision: Decision::Full,
            selected: dag.all_ids(),
            reasons: vec![
                "no full-green anchor -> cannot trust any skip; conservative full suite".into(),
            ],
        };
    }

    finish(&dag, &hermit, &anchor, &files, &sel, &format, &emit_dag_lane, &out_path);
}

#[allow(clippy::too_many_arguments)]
fn finish(
    dag: &Dag,
    hermit: &Path,
    anchor: &Option<Anchor>,
    files: &[String],
    sel: &Selection,
    format: &str,
    emit_dag_lane: &Option<String>,
    out_path: &Option<PathBuf>,
) {
    if let Some(lane) = emit_dag_lane {
        let out = out_path
            .clone()
            .unwrap_or_else(|| PathBuf::from(format!("/tmp/anchored-{lane}.json")));
        let n = emit_dag(hermit, lane, sel, &out);
        eprintln!(
            "anchored-test-selection: wrote {n}-node {lane} subset DAG to {}",
            out.display()
        );
        eprintln!(
            "  run it with: RUN_DAG_FILE_OVERRIDE={} ./ci/run-dag.sh {lane}",
            out.display()
        );
    }
    match format {
        "human" => emit_human(dag, anchor, files, sel),
        "json" => emit_json(dag, anchor, files, sel),
        other => fail(&format!("unknown --format {other} (human|json)")),
    }
}

fn need(args: &[String], i: usize) -> String {
    args.get(i + 1).cloned().unwrap_or_else(|| fail(&format!("{} needs a value", args[i])))
}

fn print_help() {
    print!(
        "\
Usage: scripts/anchored-test-selection.rs [OPTIONS]

Anchored, one-hop test selection for the Hermit CI DAG. Anchor on the newest
FULL green, diff paths changed since it, map paths -> DAG steps, report the
steps SELECTED and SKIPPED. Conservative: any unmapped path -> full suite.

  --hermit <dir>     Hermit checkout (default: hermit)
  --ci-hub <path>    ci-hub binary (default: ci-hub/ci-hub)
  --branch <name>    branch for newest-green anchor (default: main)
  --anchor <sha>     use a fixed anchor instead of newest-green (testing/repro)
  --no-fetch         pass --no-fetch to newest-green (offline)
  --files <paths…>   classify an explicit path list (space-separated)
  --files -          read the path list from stdin (one per line)
  --format <fmt>     human (default) | json
  --emit-dag <lane>  write a dependency-closed subset DAG for <lane>
                     (portable|privileged) so run-dag.sh can execute it
  --out <path>       output path for --emit-dag (default /tmp/anchored-<lane>.json)
  --self-test        run built-in unit tests and exit non-zero on failure

ONE HOP: the anchor must be a FULL green (selection_mode==full); newest-green
already guarantees this and we re-assert it. No full-green anchor => full run.
"
    );
}

// ---------------------------------------------------------------------------
// Built-in tests
// ---------------------------------------------------------------------------

fn self_test() {
    let mut failures = 0;
    let mut total = 0;
    let mut check = |name: &str, cond: bool| {
        total += 1;
        if cond {
            println!("ok   - {name}");
        } else {
            println!("FAIL - {name}");
            failures += 1;
        }
    };

    // --- glob matcher ---
    check("glob exact root Cargo.toml", glob_match("Cargo.toml", "Cargo.toml"));
    check(
        "glob root Cargo.toml is not backend Cargo.toml",
        !glob_match("Cargo.toml", "reverie/reverie-kvm/Cargo.toml"),
    );
    check("glob prefix dir", glob_match("reverie/reverie-kvm/**", "reverie/reverie-kvm/src/lib.rs"));
    check("glob **/*.md", glob_match("**/*.md", "reverie/reverie-kvm/README.md"));

    // --- classify: THE ASYMMETRY (owner's load-bearing case) ---
    let (a, _) = classify("reverie/reverie-kvm/src/lib.rs");
    check("reverie-kvm -> Steps (not Full)", matches!(a, Action::Steps(_)));
    if let (Action::Steps(s), _) = classify("reverie/reverie-kvm/src/lib.rs") {
        check("reverie-kvm -> privileged parity step", s.contains(&"privileged:e2e.manifest_backend_parity_c".to_string()));
        check("reverie-kvm -> NO portable dbi step", !s.contains(&"portable:test.dbi_parity".to_string()));
    }
    check(
        "reverie CORE -> Full",
        matches!(classify("reverie/reverie/src/lib.rs").0, Action::Full),
    );
    check(
        "reverie-ptrace (core default backend) -> Full",
        matches!(classify("reverie/reverie-ptrace/src/lib.rs").0, Action::Full),
    );
    check(
        "reverie-syscalls (core) -> Full",
        matches!(classify("reverie/reverie-syscalls/src/lib.rs").0, Action::Full),
    );

    // --- classify: backends narrow to their own backend ---
    check("detcore-dbi -> Steps", matches!(classify("detcore-dbi/src/lib.rs").0, Action::Steps(_)));
    check("detcore-sabre -> Steps", matches!(classify("detcore-sabre/src/lib.rs").0, Action::Steps(_)));
    check("reverie-liteinst -> Steps", matches!(classify("reverie/reverie-liteinst/src/x.rs").0, Action::Steps(_)));
    check("reverie-e9patch -> Steps", matches!(classify("reverie/reverie-e9patch/src/x.rs").0, Action::Steps(_)));

    // --- classify: conservative + inert ---
    check("root Cargo.lock -> Full", matches!(classify("Cargo.lock").0, Action::Full));
    check("ci/** -> Full", matches!(classify("ci/dag/portable.json").0, Action::Full));
    check("validate.sh -> Full", matches!(classify("validate.sh").0, Action::Full));
    check("core hermit detcore/ -> Full (unmapped)", matches!(classify("detcore/src/scheduler.rs").0, Action::Full));
    check("hermit-cli/ -> Full (unmapped)", matches!(classify("hermit-cli/src/x.rs").0, Action::Full));
    check("brand new area -> Full (unmapped)", matches!(classify("some/new/area/x.py").0, Action::Full));
    check("docs -> Irrelevant", matches!(classify("docs/Users.md").0, Action::Irrelevant));
    check("top-level md -> Irrelevant", matches!(classify("README.md").0, Action::Irrelevant));

    // --- selection over the LIVE DAG ---
    let dag = Dag::load(Path::new("hermit"));
    let n_all = dag.all_ids().len();
    check("DAG has both lanes' nodes", dag.nodes.keys().any(|k| k.starts_with("portable:")) && dag.nodes.keys().any(|k| k.starts_with("privileged:")));

    // Every step named in the map exists in the live DAG (stale-map guard).
    let mut mapped: BTreeSet<String> = BTreeSet::new();
    for r in rules() {
        if let Action::Steps(s) = r.action {
            for id in s { mapped.insert(id); }
        }
    }
    for pf in PREFLIGHT { mapped.insert((*pf).to_string()); }
    let stale: Vec<String> = mapped.iter().filter(|s| !dag.nodes.contains_key(*s)).cloned().collect();
    check(&format!("all mapped steps exist in live DAG (stale: {stale:?})"), stale.is_empty());

    // reverie-kvm-only diff => selective, includes privileged parity + deps,
    // excludes portable backend/core work.
    let kvm = select(&dag, &vec!["reverie/reverie-kvm/src/lib.rs".into()]);
    check("reverie-kvm diff => selective", kvm.decision == Decision::Selective);
    check("reverie-kvm selects privileged parity", kvm.selected.contains("privileged:e2e.manifest_backend_parity_c"));
    check("reverie-kvm pulls privileged build dep", kvm.selected.contains("privileged:build.privileged_tests"));
    check("reverie-kvm pulls privileged manifest_guests dep", kvm.selected.contains("privileged:build.manifest_guests"));
    check("reverie-kvm does NOT run portable strict_compat", !kvm.selected.contains("portable:test.strict_compat"));
    check("reverie-kvm does NOT run portable dbi_parity", !kvm.selected.contains("portable:test.dbi_parity"));
    check("reverie-kvm is a strict subset", kvm.selected.len() < n_all);
    check("reverie-kvm prunes the manifest gate", dag.manifest_gate(&kvm.selected).len() < dag.manifest_gate(&dag.all_ids()).len());

    // reverie CORE diff => FULL (honest: no narrowing).
    let core = select(&dag, &vec!["reverie/reverie/src/lib.rs".into()]);
    check("reverie core diff => full", core.decision == Decision::Full);
    check("reverie core selects everything", core.selected.len() == n_all);

    // dbi-only => selective, dbi steps, not sabre/kvm.
    let dbi = select(&dag, &vec!["detcore-dbi/src/lib.rs".into()]);
    check("dbi diff => selective", dbi.decision == Decision::Selective);
    check("dbi selects portable dbi_parity", dbi.selected.contains("portable:test.dbi_parity"));
    check("dbi does NOT select privileged kvm parity", !dbi.selected.contains("privileged:e2e.manifest_backend_parity_c"));
    check("dbi does NOT select sabre_examples", !dbi.selected.contains("portable:test.sabre_examples"));

    // mixed backend + docs => selective (docs inert); backend + core => full.
    let mixed = select(&dag, &vec!["reverie/reverie-kvm/src/x.rs".into(), "README.md".into()]);
    check("kvm + docs => selective", mixed.decision == Decision::Selective);
    let mixed2 = select(&dag, &vec!["reverie/reverie-kvm/src/x.rs".into(), "Cargo.lock".into()]);
    check("kvm + Cargo.lock => full (force wins)", mixed2.decision == Decision::Full);
    let mixed3 = select(&dag, &vec!["reverie/reverie-kvm/src/x.rs".into(), "detcore/src/scheduler.rs".into()]);
    check("kvm + core hermit => full (unmapped core wins)", mixed3.decision == Decision::Full);

    // pure docs => skip; empty => full.
    let docs = select(&dag, &vec!["docs/a.md".into(), "README.md".into()]);
    check("pure docs => skip", docs.decision == Decision::Skip && docs.selected.is_empty());
    let empty = select(&dag, &vec![]);
    check("empty change => full", empty.decision == Decision::Full);

    // dependency closure is transitive (parity -> build.manifest_guests -> e2e.metadata).
    check("closure pulls e2e.metadata for kvm", kvm.selected.contains("privileged:e2e.metadata"));

    drop(check);
    println!("\n{total} check(s), {failures} failure(s)");
    if failures > 0 {
        std::process::exit(1);
    }
}
