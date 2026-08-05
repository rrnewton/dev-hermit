//! Fresh cross-repository authority for Hermit's Reverie dependency frontier.
//!
//! A Hermit validation result is meaningful only under the exact Reverie
//! revision that Hermit records.  This module binds three identities in one
//! predicate:
//!
//! * the exact Hermit commit being admitted;
//! * the single Reverie revision found in every tracked Cargo manifest/lockfile
//!   at that commit; and
//! * one freshly resolved `rrnewton/reverie:refs/heads/main` tip.
//!
//! The scan is performed with `git show <hermit-sha>:<path>` over the exact
//! commit, never by trusting the caller's working tree or a receipt's copied
//! boolean.  Callers resolve the live tip once per command and pass it to every
//! scan in that decision.  A timeout, malformed/mixed/missing pin, missing
//! commit, or moved main ref is a refusal.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::env;
use std::ffi::OsStr;
use std::path::Path;
use std::process::{Command, Output};

pub const REVERIE_REPOSITORY: &str = "rrnewton/reverie";
pub const REVERIE_REMOTE: &str = "https://github.com/rrnewton/reverie.git";
pub const REVERIE_MAIN_REF: &str = "refs/heads/main";
const RESOLUTION_TIMEOUT: &str = "30s";
const RESOLUTION_KILL_AFTER: &str = "2s";

/// The dependency conditions carried by a qualifying schema-6 receipt.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct ReverieBinding {
    pub repository: String,
    #[serde(rename = "ref")]
    pub reference: String,
    pub pinned_sha: String,
    pub resolved_sha: String,
}

impl ReverieBinding {
    pub fn expected(sha: &str) -> Self {
        Self {
            repository: REVERIE_REPOSITORY.to_string(),
            reference: REVERIE_MAIN_REF.to_string(),
            pinned_sha: sha.to_string(),
            resolved_sha: sha.to_string(),
        }
    }

    pub fn is_well_formed(&self) -> bool {
        self.repository == REVERIE_REPOSITORY
            && self.reference == REVERIE_MAIN_REF
            && is_full_sha(&self.pinned_sha)
            && is_full_sha(&self.resolved_sha)
            && self.pinned_sha == self.resolved_sha
    }
}

pub fn is_full_sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn on_path(program: &str) -> bool {
    env::var_os("PATH").is_some_and(|paths| {
        env::split_paths(&paths).any(|directory| directory.join(program).is_file())
    })
}

fn bounded_ls_remote(remote: &str) -> Result<Output, String> {
    // `timeout` owns and bounds the exact child tree.  This is deliberately not
    // an unbounded `Command::output`: network loss must become UNVERIFIABLE,
    // never a landing process wedged indefinitely.
    let mut command = Command::new("timeout");
    command.args([
        "--signal=TERM",
        "--kill-after",
        RESOLUTION_KILL_AFTER,
        RESOLUTION_TIMEOUT,
    ]);
    if on_path("with-proxy") {
        command.arg("with-proxy");
    }
    command.args(["git", "ls-remote", "--exit-code", remote, REVERIE_MAIN_REF]);
    command
        .output()
        .map_err(|error| format!("could not launch bounded Reverie main resolution: {error}"))
}

/// Resolve the live Reverie main ref exactly once for one caller decision.
pub fn resolve_live_main() -> Result<String, String> {
    resolve_live_main_from(REVERIE_REMOTE)
}

/// Parameterized for the positive/negative fixture brackets; production uses
/// [`resolve_live_main`] and therefore cannot select a different authority.
pub fn resolve_live_main_from(remote: &str) -> Result<String, String> {
    let output = bounded_ls_remote(remote)?;
    if !output.status.success() {
        return Err(format!(
            "bounded git ls-remote {remote} {REVERIE_MAIN_REF} failed (status {}): {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let sha = String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .next()
        .unwrap_or_default()
        .to_string();
    if !is_full_sha(&sha) {
        return Err(format!("Reverie main resolved to invalid SHA {sha:?}"));
    }
    Ok(sha)
}

fn git_output<I, S>(repo: &Path, args: I) -> Result<Output, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|error| format!("could not launch git in {}: {error}", repo.display()))
}

/// Normalize a semantic Cargo git/source URL only when it targets a repository
/// named `reverie`. Comments and unrelated string values never reach this
/// function: callers first parse TOML and inspect the `git`/`source` keys.
fn reverie_repository_identity(raw: &str) -> Option<String> {
    let raw = raw.strip_prefix("git+").unwrap_or(raw);
    let base = raw
        .split(|character| matches!(character, '?' | '#'))
        .next()
        .unwrap_or_default()
        .trim_end_matches('/');
    let base = base.strip_suffix(".git").unwrap_or(base);
    let identity = base.to_ascii_lowercase();
    let repository = identity
        .rsplit(|character| matches!(character, '/' | ':'))
        .next()
        .unwrap_or_default();
    (repository == "reverie").then_some(identity)
}

fn is_expected_reverie_source(raw: &str) -> Result<bool, String> {
    let Some(identity) = reverie_repository_identity(raw) else {
        return Ok(false);
    };
    let expected = matches!(
        identity.as_str(),
        "https://github.com/rrnewton/reverie"
            | "ssh://git@github.com/rrnewton/reverie"
            | "git://github.com/rrnewton/reverie"
            | "git@github.com:rrnewton/reverie"
    );
    if !expected {
        return Err(format!(
            "Reverie dependency uses unexpected repository {raw:?}; expected {REVERIE_REMOTE}"
        ));
    }
    Ok(true)
}

fn record_pin(
    path: &str,
    rev: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    if !is_full_sha(rev) {
        return Err(format!(
            "{path} has non-40-lowercase-hex Reverie rev {rev:?}"
        ));
    }
    *occurrences += 1;
    pins.insert(rev.to_string());
    Ok(())
}

fn collect_dependency_spec(
    value: &toml::Value,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    let Some(table) = value.as_table() else {
        return Ok(());
    };
    if let Some(git) = table.get("git").and_then(toml::Value::as_str) {
        if is_expected_reverie_source(git)? {
            let rev = table
                .get("rev")
                .and_then(toml::Value::as_str)
                .ok_or_else(|| {
                    format!("{path} has a semantic Reverie git dependency without a pinned rev")
                })?;
            record_pin(path, rev, pins, occurrences)?;
        }
    }
    Ok(())
}

fn collect_dependency_table(
    value: Option<&toml::Value>,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    let Some(table) = value.and_then(toml::Value::as_table) else {
        return Ok(());
    };
    for dependency in table.values() {
        collect_dependency_spec(dependency, path, pins, occurrences)?;
    }
    Ok(())
}

fn collect_dependency_sections(
    table: &toml::map::Map<String, toml::Value>,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    for key in ["dependencies", "dev-dependencies", "build-dependencies"] {
        collect_dependency_table(table.get(key), path, pins, occurrences)?;
    }
    Ok(())
}

fn collect_manifest_pins(
    value: &toml::Value,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    let root = value
        .as_table()
        .ok_or_else(|| format!("{path} manifest root is not a TOML table"))?;
    collect_dependency_sections(root, path, pins, occurrences)?;

    if let Some(workspace) = root.get("workspace").and_then(toml::Value::as_table) {
        collect_dependency_table(workspace.get("dependencies"), path, pins, occurrences)?;
    }
    if let Some(targets) = root.get("target").and_then(toml::Value::as_table) {
        for target in targets.values().filter_map(toml::Value::as_table) {
            collect_dependency_sections(target, path, pins, occurrences)?;
        }
    }
    // Cargo also treats patch and replace entries as dependency specifications.
    // Restrict traversal to their documented tables rather than recursively
    // accepting arbitrary package/workspace metadata that merely looks similar.
    if let Some(patches) = root.get("patch").and_then(toml::Value::as_table) {
        for registry in patches.values() {
            collect_dependency_table(Some(registry), path, pins, occurrences)?;
        }
    }
    collect_dependency_table(root.get("replace"), path, pins, occurrences)?;
    Ok(())
}

fn lock_source_rev(source: &str) -> Result<Option<&str>, String> {
    if !is_expected_reverie_source(source)? {
        return Ok(None);
    }
    let query = source
        .split_once('?')
        .map(|(_, rest)| rest)
        .and_then(|rest| rest.split('#').next())
        .unwrap_or_default();
    let rev = query
        .split('&')
        .find_map(|field| field.strip_prefix("rev="))
        .ok_or_else(|| "Cargo.lock Reverie source has no rev query parameter".to_string())?;
    Ok(Some(rev))
}

fn collect_lock_pins(
    value: &toml::Value,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    let root = value
        .as_table()
        .ok_or_else(|| format!("{path} lock root is not a TOML table"))?;
    let Some(packages) = root.get("package").and_then(toml::Value::as_array) else {
        return Ok(());
    };
    for package in packages.iter().filter_map(toml::Value::as_table) {
        if let Some(source) = package.get("source").and_then(toml::Value::as_str) {
            if let Some(rev) = lock_source_rev(source)? {
                record_pin(path, rev, pins, occurrences)?;
            }
        }
    }
    Ok(())
}

/// Derive the single tracked Reverie revision from the exact Hermit commit.
pub fn pinned_sha_at(repo: &Path, hermit_sha: &str) -> Result<String, String> {
    if !is_full_sha(hermit_sha) {
        return Err(format!(
            "Hermit commit is not lowercase full SHA: {hermit_sha:?}"
        ));
    }
    let tree = git_output(
        repo,
        ["ls-tree", "-r", "-z", "--name-only", hermit_sha, "--"],
    )?;
    if !tree.status.success() {
        return Err(format!(
            "cannot read exact Hermit commit {hermit_sha} in {}: {}",
            repo.display(),
            String::from_utf8_lossy(&tree.stderr).trim()
        ));
    }
    let paths: Vec<String> = tree
        .stdout
        .split(|byte| *byte == 0)
        .filter(|raw| !raw.is_empty())
        .map(|raw| {
            String::from_utf8(raw.to_vec())
                .map_err(|_| "Hermit tree contains a non-UTF-8 path".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|path| {
            path == "Cargo.toml"
                || path == "Cargo.lock"
                || path.ends_with("/Cargo.toml")
                || path.ends_with("/Cargo.lock")
        })
        .collect();
    if paths.is_empty() {
        return Err(format!(
            "Hermit commit {hermit_sha} tracks no Cargo metadata"
        ));
    }

    let mut pins = BTreeSet::new();
    let mut occurrences = 0usize;
    for path in paths {
        let object = format!("{hermit_sha}:{path}");
        let contents = git_output(repo, ["show", &object])?;
        if !contents.status.success() {
            return Err(format!(
                "cannot read {object}: {}",
                String::from_utf8_lossy(&contents.stderr).trim()
            ));
        }
        let text = String::from_utf8(contents.stdout)
            .map_err(|_| format!("{object} is not UTF-8 Cargo metadata"))?;
        let parsed: toml::Value = toml::from_str(&text)
            .map_err(|error| format!("{object} is not valid TOML: {error}"))?;
        if path.ends_with("Cargo.lock") {
            collect_lock_pins(&parsed, &path, &mut pins, &mut occurrences)?;
        } else {
            collect_manifest_pins(&parsed, &path, &mut pins, &mut occurrences)?;
        }
    }
    if occurrences == 0 {
        return Err(format!(
            "Hermit commit {hermit_sha} has no pinned GitHub Reverie dependency"
        ));
    }
    if pins.len() != 1 {
        return Err(format!(
            "Hermit commit {hermit_sha} contains {} distinct Reverie revisions: {}",
            pins.len(),
            pins.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    Ok(pins.into_iter().next().expect("one pin"))
}

/// Verify one exact Hermit head against a tip already resolved by the caller.
pub fn verify_exact_head(
    repo: &Path,
    hermit_sha: &str,
    resolved_sha: &str,
) -> Result<ReverieBinding, String> {
    if !is_full_sha(resolved_sha) {
        return Err(format!(
            "resolved Reverie main is not lowercase full SHA: {resolved_sha:?}"
        ));
    }
    let pinned_sha = pinned_sha_at(repo, hermit_sha)?;
    if pinned_sha != resolved_sha {
        return Err(format!(
            "Hermit {hermit_sha} pins Reverie {pinned_sha}, but live {REVERIE_REPOSITORY}:{REVERIE_MAIN_REF} is {resolved_sha}"
        ));
    }
    Ok(ReverieBinding::expected(resolved_sha))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_repo_with_lock(name: &str, manifest: &str, lock: Option<&str>) -> (PathBuf, String) {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = env::temp_dir().join(format!("ci-hub-reverie-pin-{name}-{nonce}"));
        fs::create_dir_all(&root).unwrap();
        assert!(Command::new("git")
            .arg("init")
            .arg("-q")
            .arg(&root)
            .status()
            .unwrap()
            .success());
        assert!(Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["config", "user.email", "ci-hub@example.invalid"])
            .status()
            .unwrap()
            .success());
        assert!(Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["config", "user.name", "ci-hub test"])
            .status()
            .unwrap()
            .success());
        fs::write(root.join("Cargo.toml"), manifest).unwrap();
        if let Some(lock) = lock {
            fs::write(root.join("Cargo.lock"), lock).unwrap();
        }
        assert!(Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["add", "Cargo.toml"])
            .status()
            .unwrap()
            .success());
        if lock.is_some() {
            assert!(Command::new("git")
                .arg("-C")
                .arg(&root)
                .args(["add", "Cargo.lock"])
                .status()
                .unwrap()
                .success());
        }
        assert!(Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["commit", "-q", "-m", "fixture"])
            .status()
            .unwrap()
            .success());
        let out = Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap();
        (
            root,
            String::from_utf8(out.stdout).unwrap().trim().to_string(),
        )
    }

    fn temp_repo(name: &str, manifest: &str) -> (PathBuf, String) {
        temp_repo_with_lock(name, manifest, None)
    }

    fn manifest(pin: &str) -> String {
        format!(
            "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\n[dependencies]\nreverie = {{ git = \"https://github.com/rrnewton/reverie\", rev = \"{pin}\" }}\n"
        )
    }

    #[test]
    fn same_tip_accepts_and_moved_tip_refuses() {
        let pin = "a".repeat(40);
        let (repo, head) = temp_repo("same-tip", &manifest(&pin));
        assert_eq!(
            verify_exact_head(&repo, &head, &pin).unwrap(),
            ReverieBinding::expected(&pin)
        );
        let moved = "b".repeat(40);
        assert!(verify_exact_head(&repo, &head, &moved)
            .unwrap_err()
            .contains("but live"));
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn malformed_and_missing_pins_refuse() {
        let (malformed, malformed_head) = temp_repo("malformed", &manifest("abc123"));
        assert!(pinned_sha_at(&malformed, &malformed_head)
            .unwrap_err()
            .contains("non-40"));
        fs::remove_dir_all(malformed).ok();

        let (missing, missing_head) = temp_repo(
            "missing",
            "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\n",
        );
        assert!(pinned_sha_at(&missing, &missing_head)
            .unwrap_err()
            .contains("no pinned"));
        fs::remove_dir_all(missing).ok();

        let first = "a".repeat(40);
        let second = "b".repeat(40);
        let mixed_manifest = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[dependencies]\nreverie-core={{git=\"https://github.com/rrnewton/reverie\",rev=\"{first}\"}}\nreverie-ptrace={{git=\"https://github.com/rrnewton/reverie.git\",rev=\"{second}\"}}\n"
        );
        let (mixed, mixed_head) = temp_repo("mixed", &mixed_manifest);
        assert!(pinned_sha_at(&mixed, &mixed_head)
            .unwrap_err()
            .contains("2 distinct"));
        fs::remove_dir_all(mixed).ok();

        let wrong_source = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[dependencies]\nreverie={{git=\"https://github.com/other/reverie\",rev=\"{first}\"}}\n"
        );
        let (unexpected, unexpected_head) = temp_repo("unexpected-source", &wrong_source);
        assert!(pinned_sha_at(&unexpected, &unexpected_head)
            .unwrap_err()
            .contains("unexpected repository"));
        fs::remove_dir_all(unexpected).ok();
    }

    #[test]
    fn comments_cannot_override_the_semantic_dependency() {
        let stale = "d".repeat(40);
        let live = "e".repeat(40);
        let manifest = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[dependencies]\nreverie={{git=\"https://github.com/rrnewton/reverie\",rev=\"{stale}\"}}\n# https://github.com/rrnewton/reverie.git rev=\"{live}\"\n"
        );
        let (repo, head) = temp_repo("comment-proxy", &manifest);
        assert_eq!(pinned_sha_at(&repo, &head).unwrap(), stale);
        assert!(verify_exact_head(&repo, &head, &live)
            .unwrap_err()
            .contains("but live"));
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn real_target_workspace_and_lock_dependencies_qualify() {
        let pin = "f".repeat(40);
        let target_manifest = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[target.'cfg(unix)'.dependencies]\nreverie={{git=\"https://github.com/rrnewton/reverie\",rev=\"{pin}\"}}\n"
        );
        let (target, target_head) = temp_repo("target-dependency", &target_manifest);
        assert_eq!(pinned_sha_at(&target, &target_head).unwrap(), pin);
        fs::remove_dir_all(target).ok();

        let workspace_manifest = format!(
            "[workspace]\nmembers=[]\n[workspace.dependencies]\nreverie={{git=\"https://github.com/rrnewton/reverie.git\",rev=\"{pin}\"}}\n"
        );
        let (workspace, workspace_head) = temp_repo("workspace-dependency", &workspace_manifest);
        assert_eq!(pinned_sha_at(&workspace, &workspace_head).unwrap(), pin);
        fs::remove_dir_all(workspace).ok();

        let lock = format!(
            "version=3\n[[package]]\nname=\"reverie-core\"\nversion=\"0.1.0\"\nsource=\"git+https://github.com/rrnewton/reverie?rev={pin}#{pin}\"\n"
        );
        let (locked, locked_head) = temp_repo_with_lock(
            "lock-package",
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n",
            Some(&lock),
        );
        assert_eq!(pinned_sha_at(&locked, &locked_head).unwrap(), pin);
        fs::remove_dir_all(locked).ok();
    }

    #[test]
    fn metadata_and_non_package_lock_sources_cannot_authorize() {
        let fake = "1".repeat(40);
        let metadata_manifest = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[package.metadata.fake]\ngit=\"https://github.com/rrnewton/reverie\"\nrev=\"{fake}\"\n"
        );
        let (metadata, metadata_head) = temp_repo("metadata-spoof", &metadata_manifest);
        assert!(pinned_sha_at(&metadata, &metadata_head)
            .unwrap_err()
            .contains("no pinned"));
        fs::remove_dir_all(metadata).ok();

        let fake_lock = format!(
            "version=3\n[metadata.fake]\nsource=\"git+https://github.com/rrnewton/reverie?rev={fake}#{fake}\"\n"
        );
        let (lock_metadata, lock_metadata_head) = temp_repo_with_lock(
            "lock-metadata-spoof",
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n",
            Some(&fake_lock),
        );
        assert!(pinned_sha_at(&lock_metadata, &lock_metadata_head)
            .unwrap_err()
            .contains("no pinned"));
        fs::remove_dir_all(lock_metadata).ok();
    }

    #[test]
    fn binding_shape_is_exact() {
        let pin = "c".repeat(40);
        assert!(ReverieBinding::expected(&pin).is_well_formed());
        let mut tampered = ReverieBinding::expected(&pin);
        tampered.reference = "refs/heads/other".into();
        assert!(!tampered.is_well_formed());
    }
}
