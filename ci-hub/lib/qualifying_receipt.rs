//! The SINGLE qualifying-receipt predicate, shared by every consumer.
//!
//! # Why this module exists
//!
//! "Does this ledger row / receipt qualify as a full green?" was answered by
//! FIVE independent inline certifiers across three languages
//! (`validate_status.rs`, `history_queries.rs`, `query.py`,
//! `publish_receipt.py`, `verify_receipt.sh`). Each was its own floor and each
//! drifted from the others: the count-schema constant was redefined three times,
//! one consumer gated `filtered_tests == 0` while the others did not, `result ==
//! pass` was belt-and-suspendered in two places and keyed alone in two others,
//! and the per-node coverage clause was present in three and absent in one. That
//! is the hardcoded-list-of-a-growing-set defect applied to a PREDICATE: a
//! duplicated rule drifts in N-1 places and each copy looks correct in isolation
//! (see task `one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry`,
//! source sweep `ai_docs/2026-08-04-floor-consumer-sweep.md`).
//!
//! # The fix
//!
//! ONE Rust semantic verifier reads the data artifact
//! `ci-hub/validate/qualifying-receipt.json`, colocated with the other gate
//! registries. Every Rust, Python, and shell authority reaches this function
//! directly or through `ci-hub validate-status` / `ledger qualified-rows`;
//! Python and jq no longer restate the predicate. A mutation of the datum (for
//! example `executed_tests_min`) therefore moves every consumer's answer through
//! one executable path.
//!
//! # Resolution order (single source at run time)
//!
//! 1. `$QUALIFYING_RECEIPT_PREDICATE` — an explicit override path only when
//!    `$CI_HUB_TEST_PREDICATE_OVERRIDE=1`; mutation tests set both. An unguarded
//!    inherited override fails closed and can never loosen a production query.
//! 2. the on-disk `ci-hub/validate/qualifying-receipt.json` resolved against the
//!    repo root — this is PRIMARY in production so an edit to the JSON takes
//!    effect with no recompile (the "one edit" guarantee).
//! 3. the compile-time [`EMBEDDED`] snapshot of that same file — a last-resort
//!    fallback for unit tests that run the compiled binary from a temp dir where
//!    the on-disk file is not reachable. It is `include_str!`-ed from the exact
//!    file, so it cannot silently diverge from the source at compile time.
//!
//! A malformed override or on-disk file is a deploy defect and PANICS (loud),
//! never silently falls through to the embed — silent fallback would mask the
//! very drift this module exists to prevent.

use crate::records::{CoverageRow, HistoryRow};
use crate::reverie_pin::ReverieBinding;
use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Repo-relative path of the canonical predicate. The literal lives ONLY here.
pub const PREDICATE_REL: &str = "ci-hub/validate/qualifying-receipt.json";

/// Environment override consulted first, so the mutation test can point every
/// consumer at a tightened copy without touching the live file.
pub const PREDICATE_ENV: &str = "QUALIFYING_RECEIPT_PREDICATE";
pub const PREDICATE_TEST_SENTINEL_ENV: &str = "CI_HUB_TEST_PREDICATE_OVERRIDE";

/// Compile-time snapshot of the live file (fallback only; see module docs).
pub const EMBEDDED: &str = include_str!("../validate/qualifying-receipt.json");

/// The mandatory receipt/ledger-row fields and their required values. A `pass`
/// row must satisfy every one; sourcing them here means a consumer cannot omit
/// one and quietly become more lenient than its peers.
#[derive(Clone, Debug, Deserialize)]
pub struct RequireClause {
    pub commit_anchored: bool,
    pub tree_dirty: bool,
    pub profile: String,
    pub selection_mode: String,
    pub result: String,
    pub failures_max: u64,
    pub executed_tests_min: i64,
}

/// The per-node coverage obligation and the schema at/after which it is enforced.
#[derive(Clone, Debug, Deserialize)]
pub struct CoverageClause {
    pub applies_at_schema_min: u32,
    pub per_node: bool,
}

/// Cross-repository dependency identity required of Hermit receipts.  The
/// dynamic SHA is supplied by the fresh verifier; this datum owns the stable
/// schema/repository/ref conditions.
#[derive(Clone, Debug, Deserialize)]
pub struct ReverieBindingClause {
    pub applies_at_schema_min: u32,
    pub repository: String,
    #[serde(rename = "ref")]
    pub reference: String,
}

/// The whole predicate, deserialized from the JSON. Unknown keys (e.g.
/// `_comment`) are ignored on purpose so the file can carry documentation.
#[derive(Clone, Debug, Deserialize)]
pub struct QualifyingPredicate {
    /// The `schema_version` at/after which a receipt's writer GUARANTEES it emits
    /// both count fields and per-node coverage, so an absent count/coverage on
    /// such a receipt is a writer DEFECT (reject), not a pre-count receipt.
    pub counts_schema: u32,
    pub require: RequireClause,
    /// When true, additionally require `filtered_tests == 0`. Default false: a
    /// full run legitimately filters other-lane/other-shard tests, so a positive
    /// aggregate filtered count says nothing about coverage (`executed>0` plus
    /// the per-node clause already guard emptiness/narrowing).
    #[serde(default)]
    pub gate_filtered_tests: bool,
    pub coverage: CoverageClause,
    pub reverie_binding: ReverieBindingClause,
}

/// Trusted product identity supplied by the verifier call site. Never derive
/// this decision from `HistoryRow.repo`: receipt bytes are attacker-controlled.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReceiptTarget {
    Hermit,
    Reverie,
}

impl ReceiptTarget {
    pub fn from_repository(repository: &str) -> Result<Self, String> {
        match repository.rsplit('/').next() {
            Some("hermit") => Ok(Self::Hermit),
            Some("reverie") => Ok(Self::Reverie),
            _ => Err(format!(
                "unsupported validation target {repository:?}; expected owner/hermit or owner/reverie"
            )),
        }
    }

    pub fn row_matches(self, row: &HistoryRow) -> bool {
        match self {
            // Historical Hermit rows omitted repo. That compatibility does not
            // let an explicit Reverie row enter the Hermit authority path.
            Self::Hermit => matches!(row.repo.as_deref(), None | Some("hermit")),
            Self::Reverie => row.repo.as_deref() == Some("reverie"),
        }
    }
}

impl QualifyingPredicate {
    /// Parse from JSON text, mapping errors to a message that names the source.
    pub fn parse(text: &str, origin: &str) -> Result<Self, String> {
        serde_json::from_str(text)
            .map_err(|e| format!("{origin}: malformed qualifying predicate: {e}"))
    }
}

/// A per-node coverage obligation is SATISFIED iff the run planned at least one
/// test-bearing DAG node AND no planned test node was inert or absent. Carrying
/// the node NAMES in the receipt lets this be re-derived without re-reading a log
/// (Proxy Binding: the condition travels with the value).
pub fn coverage_satisfied(cov: &CoverageRow) -> bool {
    cov.planned_test_nodes > 0
        && cov.executed_test_nodes == cov.planned_test_nodes
        && cov.zero_executed_nodes.is_empty()
        && cov.absent_nodes.is_empty()
        && cov.failed_nodes.is_empty()
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// THE qualifying-receipt predicate. Every Rust consumer routes here; the
/// control flow mirrors what the inline certifiers did, but every constant and
/// flag is sourced from `pred` so there is one place to tighten.
///
/// Over and above clean full coverage (the `require` string/bool fields) and
/// `result == require.result`:
///   * `failures` must be present and `<= require.failures_max` — missing is not
///     zero, and a `pass`/`failures>0` row is malformed and must not qualify.
///   * a demonstrated zero-test run (`executed_tests == Some(0)`) is never a
///     green at any schema — never grandfathered.
///   * if `gate_filtered_tests`, additionally require `filtered_tests == 0`.
///   * COUNT-CAPABLE receipts (`schema_version >= counts_schema`) are held to the
///     FULL per-node contract: `executed_tests >= require.executed_tests_min`
///     AND a `coverage` object that satisfies its obligation (when
///     `coverage.per_node` and `schema_version >= coverage.applies_at_schema_min`).
///     Missing either on a count-capable receipt is a writer defect -> reject.
///   * receipts carrying counts under an OLDER schema prove nonzero execution but
///     predate per-node coverage: held only to `executed_tests >=
///     require.executed_tests_min`.
///   * a receipt carrying NEITHER count proves nothing and is rejected
///     (NotValidated / re-dispatch), never treated as green.
pub fn row_qualifies(
    row: &HistoryRow,
    sha: &str,
    pred: &QualifyingPredicate,
    target: ReceiptTarget,
    expected_reverie: Option<&ReverieBinding>,
) -> bool {
    let req = &pred.require;
    if !(row.commit.as_deref() == Some(sha)
        && row.commit_anchored == Some(req.commit_anchored)
        && row.tree_dirty == Some(req.tree_dirty)
        && row.selection_mode.as_deref() == Some(req.selection_mode.as_str())
        && row.profile.as_deref() == Some(req.profile.as_str())
        && row.result.as_deref() == Some(req.result.as_str())
        && target.row_matches(row))
    {
        return false;
    }
    // Every green carries its observed failure count. Missing is not zero.
    if !matches!(row.failures, Some(failures) if failures <= req.failures_max) {
        return false;
    }
    // A demonstrated zero-test run is never a full green, at any schema.
    if row.executed_tests == Some(0) {
        return false;
    }
    if pred.gate_filtered_tests && row.filtered_tests != Some(0) {
        return false;
    }
    let schema = row.schema_version.unwrap_or(0);
    // Reverie validates itself and has no cross-repository Hermit pin. Every
    // Hermit row (historical rows omit `repo`, which means Hermit) must use the
    // new schema and carry the exact dynamic dependency identity. Old rows with
    // only `reverie_pin_current: true` prove no SHA and fail closed.
    if target == ReceiptTarget::Hermit {
        let Some(expected) = expected_reverie else {
            return false;
        };
        let Some(observed) = row.reverie_binding.as_ref() else {
            return false;
        };
        if schema < pred.reverie_binding.applies_at_schema_min
            || pred.reverie_binding.repository != expected.repository
            || pred.reverie_binding.reference != expected.reference
            || !observed.is_well_formed()
            || observed != expected
        {
            return false;
        }
        if !row.source_log_sha256.as_deref().is_some_and(is_sha256)
            || !row
                .log_file
                .as_deref()
                .is_some_and(|path| std::path::Path::new(path).is_absolute())
        {
            return false;
        }
    }
    let count_capable = schema >= pred.counts_schema;
    let counts_present = row.executed_tests.is_some() && row.filtered_tests.is_some();
    let executed_ok = matches!(row.executed_tests, Some(n) if n >= req.executed_tests_min);
    if count_capable {
        let coverage_ok = !pred.coverage.per_node
            || schema < pred.coverage.applies_at_schema_min
            || row.coverage.as_ref().is_some_and(coverage_satisfied);
        counts_present && executed_ok && coverage_ok
    } else if counts_present {
        // Old-schema writer that carried counts but predates per-node coverage:
        // hold it to the strongest thing it can prove — nonzero execution.
        executed_ok
    } else {
        // Neither count present: an uncounted receipt is UNVERIFIED, not green.
        false
    }
}

/// Resolve the on-disk predicate path against the repo root. The literal path
/// lives only in [`PREDICATE_REL`].
pub fn predicate_path(root: &Path) -> PathBuf {
    root.join(PREDICATE_REL)
}

/// Load the predicate, honoring the resolution order in the module docs.
/// `root` supplies the on-disk location; a malformed override/on-disk file is a
/// hard error (the caller decides whether to panic or fail closed).
pub fn load(root: &Path) -> Result<QualifyingPredicate, String> {
    if let Some(path) = std::env::var_os(PREDICATE_ENV).filter(|v| !v.is_empty()) {
        if std::env::var(PREDICATE_TEST_SENTINEL_ENV).ok().as_deref() != Some("1") {
            return Err(format!(
                "{PREDICATE_ENV} is test-only and requires {PREDICATE_TEST_SENTINEL_ENV}=1"
            ));
        }
        let path = PathBuf::from(path);
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("{}: cannot read {PREDICATE_ENV}: {e}", path.display()))?;
        return QualifyingPredicate::parse(&text, &path.display().to_string());
    }
    let path = predicate_path(root);
    match std::fs::read_to_string(&path) {
        Ok(text) => QualifyingPredicate::parse(&text, &path.display().to_string()),
        // Not reachable on disk (e.g. a unit-test temp dir): fall back to the
        // compile-time snapshot of the very same file. A malformed on-disk file,
        // by contrast, propagates as an error above and is never masked.
        Err(_) => QualifyingPredicate::parse(EMBEDDED, "embedded qualifying-receipt.json"),
    }
}

/// The process-wide active predicate for callers without a `root` in hand
/// (`is_clean_full_pass`, `assess`). Resolved once and cached. Resolution
/// mirrors `ci-hub.rs::workspace_root` (script/exe dir -> git toplevel) so a run
/// from the parent finds the live file; on any failure it falls back to the
/// embedded snapshot rather than stranding every green.
pub fn active() -> &'static QualifyingPredicate {
    static ACTIVE: OnceLock<QualifyingPredicate> = OnceLock::new();
    ACTIVE.get_or_init(|| {
        let root = resolve_root();
        match load(&root) {
            Ok(pred) => pred,
            // load() only errors on a malformed override/on-disk file — a real
            // deploy defect. Fail LOUD: a landing gate must not run on a guessed
            // predicate.
            Err(message) => panic!("qualifying-receipt predicate unusable: {message}"),
        }
    })
}

/// Best-effort repo root for [`active`], mirroring `ci-hub.rs::workspace_root`.
/// Falls back to the current directory (the embed then covers a missing file).
fn resolve_root() -> PathBuf {
    let source = std::env::var_os("RUST_SCRIPT_PATH")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::current_exe().ok());
    let start = source
        .as_deref()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(&start)
        .args(["rev-parse", "--show-toplevel"])
        .output();
    match output {
        Ok(out) if out.status.success() => {
            PathBuf::from(String::from_utf8_lossy(&out.stdout).trim())
        }
        _ => start,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, name: &str, body: &str) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, body).unwrap();
        path
    }

    /// The embedded snapshot must always parse — it is the fallback of last
    /// resort and a parse failure would strand every green in a temp-dir run.
    #[test]
    fn embedded_snapshot_parses() {
        let pred = QualifyingPredicate::parse(EMBEDDED, "embedded").unwrap();
        assert!(pred.counts_schema >= 1);
        assert!(pred.require.executed_tests_min >= 1);
    }

    /// A tightened override MOVES the answer for the same row — the core of the
    /// mutation proof, exercised here at the Rust predicate boundary.
    #[test]
    fn tightening_executed_min_moves_the_answer() {
        let dir = std::env::temp_dir().join(format!(
            "qrp-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let live = write(&dir, "live.json", EMBEDDED);
        let tightened = write(
            &dir,
            "tight.json",
            &EMBEDDED.replace(
                "\"executed_tests_min\": 1",
                "\"executed_tests_min\": 999999",
            ),
        );
        let sha = "a".repeat(40);
        // A genuine schema-6 Hermit green carrying counts, coverage, and its
        // exact cross-repository dependency identity.
        let row: HistoryRow = serde_json::from_str(&format!(
            r#"{{"schema_version":6,"profile":"full","selection_mode":"full","commit":"{sha}",
                "commit_anchored":true,"tree_dirty":false,"result":"pass","failures":0,
                "executed_tests":740,"filtered_tests":3,
                "log_file":"/tmp/ci-hub-test.log","source_log_sha256":"1111111111111111111111111111111111111111111111111111111111111111",
                "coverage":{{"planned_test_nodes":4,"executed_test_nodes":4,
                    "zero_executed_nodes":[],"absent_nodes":[]}},
                "reverie_binding":{{"repository":"rrnewton/reverie","ref":"refs/heads/main",
                    "pinned_sha":"{sha}","resolved_sha":"{sha}"}}}}"#
        ))
        .unwrap();
        let live_pred =
            QualifyingPredicate::parse(&std::fs::read_to_string(&live).unwrap(), "live").unwrap();
        let tight_pred =
            QualifyingPredicate::parse(&std::fs::read_to_string(&tightened).unwrap(), "tight")
                .unwrap();
        assert!(
            row_qualifies(
                &row,
                &sha,
                &live_pred,
                ReceiptTarget::Hermit,
                row.reverie_binding.as_ref(),
            ),
            "live must accept the genuine green"
        );
        assert!(
            !row_qualifies(
                &row,
                &sha,
                &tight_pred,
                ReceiptTarget::Hermit,
                row.reverie_binding.as_ref(),
            ),
            "tightened must reject it"
        );
        std::fs::remove_dir_all(&dir).ok();
    }
}
