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
use std::time::{SystemTime, UNIX_EPOCH};

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
    sanitize_git_environment(&mut command);
    // A repository-local config can carry `url.*.insteadOf` too. Resolve the
    // remote from a newly-created non-repository directory so neither the
    // caller's checkout nor its `.git/config` can redirect the canonical URL.
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("cannot timestamp isolated Git lookup: {error}"))?
        .as_nanos();
    let isolation =
        env::temp_dir().join(format!("ci-hub-reverie-ref-{}-{nonce}", std::process::id()));
    std::fs::create_dir(&isolation).map_err(|error| {
        format!(
            "cannot create isolated Git lookup directory {}: {error}",
            isolation.display()
        )
    })?;
    let isolation = std::fs::canonicalize(&isolation).map_err(|error| {
        format!(
            "cannot canonicalize isolated Git lookup directory {}: {error}",
            isolation.display()
        )
    })?;
    let ceiling = isolation.parent().ok_or_else(|| {
        format!(
            "isolated Git lookup directory has no parent: {}",
            isolation.display()
        )
    })?;
    // TMPDIR is caller-controlled and may itself live inside a Git worktree.
    // Stop discovery before its exact canonical parent, otherwise a local
    // `url.*.insteadOf` can rewrite the supposedly canonical live remote.
    command
        .current_dir(&isolation)
        .env("GIT_CEILING_DIRECTORIES", ceiling);
    let result = command
        .output()
        .map_err(|error| format!("could not launch bounded Reverie main resolution: {error}"));
    std::fs::remove_dir(&isolation).ok();
    result
}

/// Keep caller-controlled Git locator state and replacement refs from changing
/// the object/ref that this authority observes.  In particular, a
/// `refs/replace/<tree>` entry can otherwise make `cat-file -t <tree>` report
/// `commit`, defeating the exact-object check.
fn sanitize_git_environment(command: &mut Command) {
    for variable in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
    ] {
        command.env_remove(variable);
    }
    command
        .env("GIT_NO_REPLACE_OBJECTS", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_SYSTEM", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1");
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
    let mut command = Command::new("git");
    command.arg("-C").arg(repo).args(args);
    sanitize_git_environment(&mut command);
    command
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
    dependency_name: &str,
    value: &toml::Value,
    path: &str,
    pins: &mut BTreeSet<String>,
    occurrences: &mut usize,
) -> Result<(), String> {
    let table = value.as_table();
    let package_name = table
        .and_then(|fields| fields.get("package"))
        .and_then(toml::Value::as_str)
        .unwrap_or(dependency_name);
    let git = table
        .and_then(|fields| fields.get("git"))
        .and_then(toml::Value::as_str);
    let semantic_reverie = dependency_name.starts_with("reverie")
        || package_name.starts_with("reverie")
        || git.is_some_and(|source| reverie_repository_identity(source).is_some());
    if !semantic_reverie {
        return Ok(());
    }
    let Some(table) = table else {
        return Err(format!(
            "{path} has non-Git Reverie dependency {dependency_name:?}"
        ));
    };
    // `workspace = true` is an indirection to the root workspace dependency;
    // that root specification and Cargo.lock are scanned independently. Every
    // other semantic Reverie dependency must itself name the expected Git
    // authority and exact revision. This prevents a current-pin decoy from
    // masking the actual path/registry/version dependency.
    if table.get("workspace").and_then(toml::Value::as_bool) == Some(true) && git.is_none() {
        return Ok(());
    }
    let git =
        git.ok_or_else(|| format!("{path} has non-Git Reverie dependency {dependency_name:?}"))?;
    if !is_expected_reverie_source(git)? {
        return Err(format!(
            "{path} has Reverie dependency {dependency_name:?} from a non-Reverie source"
        ));
    }
    let rev = table
        .get("rev")
        .and_then(toml::Value::as_str)
        .ok_or_else(|| {
            format!("{path} has a semantic Reverie git dependency without a pinned rev")
        })?;
    record_pin(path, rev, pins, occurrences)?;
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
    for (name, dependency) in table {
        collect_dependency_spec(name, dependency, path, pins, occurrences)?;
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

fn lock_source_rev(source: &str) -> Result<Option<String>, String> {
    if !is_expected_reverie_source(source)? {
        return Ok(None);
    }
    let (before_fragment, precise) = source
        .rsplit_once('#')
        .ok_or_else(|| "Cargo.lock Reverie source has no precise commit fragment".to_string())?;
    let query = before_fragment
        .split_once('?')
        .map(|(_, rest)| rest)
        .unwrap_or_default();
    let revisions: Vec<&str> = query
        .split('&')
        .filter_map(|field| field.strip_prefix("rev="))
        .collect();
    if revisions.len() != 1 {
        return Err(format!(
            "Cargo.lock Reverie source must carry exactly one rev query parameter: {source:?}"
        ));
    }
    let rev = revisions[0];
    if !is_full_sha(rev) || !is_full_sha(precise) {
        return Err(format!(
            "Cargo.lock Reverie source must carry full lowercase rev and precise commit: {source:?}"
        ));
    }
    if rev != precise {
        return Err(format!(
            "Cargo.lock Reverie source rev {rev} disagrees with precise commit {precise}"
        ));
    }
    Ok(Some(rev.to_string()))
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
        let name = package
            .get("name")
            .and_then(toml::Value::as_str)
            .unwrap_or_default();
        let source = package.get("source").and_then(toml::Value::as_str);
        let semantic_reverie = name.starts_with("reverie")
            || source.is_some_and(|value| reverie_repository_identity(value).is_some());
        if semantic_reverie && source.is_none() {
            return Err(format!(
                "{path} resolves Reverie package {name:?} without the canonical Git source"
            ));
        }
        if let Some(source) = source {
            if semantic_reverie {
                let rev = lock_source_rev(source)?.ok_or_else(|| {
                    format!("{path} resolves Reverie package {name:?} from an unexpected source")
                })?;
                record_pin(path, &rev, pins, occurrences)?;
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
    let object_type = git_output(repo, ["cat-file", "-t", hermit_sha])?;
    if !object_type.status.success()
        || String::from_utf8_lossy(&object_type.stdout).trim() != "commit"
    {
        return Err(format!(
            "exact Hermit object {hermit_sha} in {} is not a commit",
            repo.display()
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
    fn lock_query_and_precise_commit_must_match() {
        let query = "a".repeat(40);
        let precise = "b".repeat(40);
        let lock = format!(
            "version=3\n[[package]]\nname=\"reverie-core\"\nversion=\"0.1.0\"\nsource=\"git+https://github.com/rrnewton/reverie?rev={query}#{precise}\"\n"
        );
        let (repo, head) = temp_repo_with_lock(
            "lock-mismatched-precise",
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n",
            Some(&lock),
        );
        assert!(pinned_sha_at(&repo, &head)
            .unwrap_err()
            .contains("disagrees with precise commit"));
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn duplicate_lock_rev_and_non_git_actual_dependency_refuse_decoys() {
        let pin = "a".repeat(40);
        let duplicate = format!(
            "version=3\n[[package]]\nname=\"reverie-core\"\nversion=\"0.2.0\"\nsource=\"git+https://github.com/rrnewton/reverie?rev={pin}&rev={pin}#{pin}\"\n"
        );
        let (duplicate_repo, duplicate_head) =
            temp_repo_with_lock("duplicate-lock-rev", &manifest(&pin), Some(&duplicate));
        assert!(pinned_sha_at(&duplicate_repo, &duplicate_head)
            .unwrap_err()
            .contains("exactly one rev"));
        fs::remove_dir_all(duplicate_repo).ok();

        let decoy = format!(
            "[package]\nname=\"fixture\"\nversion=\"0.1.0\"\n[dependencies]\nreverie-core={{path=\"vendor/reverie-core\"}}\ndecoy={{git=\"https://github.com/rrnewton/reverie\",rev=\"{pin}\"}}\n"
        );
        let (decoy_repo, decoy_head) = temp_repo("path-plus-decoy", &decoy);
        assert!(pinned_sha_at(&decoy_repo, &decoy_head)
            .unwrap_err()
            .contains("non-Git Reverie dependency"));
        fs::remove_dir_all(decoy_repo).ok();
    }

    #[test]
    fn tree_object_cannot_stand_in_for_a_commit() {
        let pin = "a".repeat(40);
        let (repo, head) = temp_repo("tree-object", &manifest(&pin));
        let tree = git_output(&repo, ["rev-parse", &format!("{head}^{{tree}}")]).unwrap();
        let tree_sha = String::from_utf8(tree.stdout).unwrap().trim().to_string();
        assert!(pinned_sha_at(&repo, &tree_sha)
            .unwrap_err()
            .contains("is not a commit"));
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn replacement_ref_cannot_make_tree_object_a_commit() {
        let pin = "a".repeat(40);
        let (repo, head) = temp_repo("replace-tree-object", &manifest(&pin));
        let tree = git_output(&repo, ["rev-parse", &format!("{head}^{{tree}}")]).unwrap();
        let tree_sha = String::from_utf8(tree.stdout).unwrap().trim().to_string();
        assert!(Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(["update-ref", &format!("refs/replace/{tree_sha}"), &head,])
            .status()
            .unwrap()
            .success());
        let unsafe_type = Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(["cat-file", "-t", &tree_sha])
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&unsafe_type.stdout).trim(),
            "commit"
        );
        assert!(pinned_sha_at(&repo, &tree_sha)
            .unwrap_err()
            .contains("is not a commit"));
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn global_git_config_cannot_redirect_live_tip() {
        const CHILD: &str = "CI_HUB_GIT_CONFIG_REDIRECT_CHILD";
        if env::var_os(CHILD).is_some() {
            let result = resolve_live_main_from("https://127.0.0.1:9/canonical-reverie.git");
            assert!(result.is_err(), "global url.insteadOf redirected authority");
            return;
        }
        let pin = "a".repeat(40);
        let (repo, _) = temp_repo("git-config-redirect", &manifest(&pin));
        let config = repo
            .parent()
            .unwrap()
            .join(format!("ci-hub-git-config-redirect-{}", std::process::id()));
        fs::write(
            &config,
            format!(
                "[url \"file://{}\"]\n\tinsteadOf = https://127.0.0.1:9/canonical-reverie.git\n",
                repo.display()
            ),
        )
        .unwrap();
        let output = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "reverie_pin::tests::global_git_config_cannot_redirect_live_tip",
                "--nocapture",
            ])
            .env(CHILD, "1")
            .env("GIT_CONFIG_GLOBAL", &config)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "redirect child failed: {}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        fs::remove_file(config).ok();
        fs::remove_dir_all(repo).ok();
    }

    #[test]
    fn temp_dir_inside_repo_cannot_redirect_live_tip() {
        const CHILD: &str = "CI_HUB_LOCAL_CONFIG_REDIRECT_CHILD";
        const REMOTE: &str = "https://127.0.0.1:9/canonical-reverie.git";
        if env::var_os(CHILD).is_some() {
            let result = resolve_live_main_from(REMOTE);
            assert!(
                result.is_err(),
                "repository-local url.insteadOf redirected authority"
            );
            return;
        }

        let pin = "a".repeat(40);
        let (repo, head) = temp_repo("local-config-redirect", &manifest(&pin));
        assert!(Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(["update-ref", "refs/heads/main", &head])
            .status()
            .unwrap()
            .success());
        assert!(Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args([
                "config",
                &format!("url.file://{}.insteadOf", repo.display()),
                REMOTE,
            ])
            .status()
            .unwrap()
            .success());
        let caller_tmp = repo.join("caller-tmp");
        fs::create_dir(&caller_tmp).unwrap();

        // Negative control: the planted local config really does redirect an
        // otherwise unreachable canonical URL when Git discovers the parent.
        let unsafe_lookup = Command::new("git")
            .current_dir(&caller_tmp)
            .args(["ls-remote", "--exit-code", REMOTE, REVERIE_MAIN_REF])
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_SYSTEM", "/dev/null")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .output()
            .unwrap();
        assert!(unsafe_lookup.status.success());
        assert_eq!(
            String::from_utf8_lossy(&unsafe_lookup.stdout)
                .split_whitespace()
                .next(),
            Some(head.as_str())
        );

        let output = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "reverie_pin::tests::temp_dir_inside_repo_cannot_redirect_live_tip",
                "--nocapture",
            ])
            .env(CHILD, "1")
            .env("TMPDIR", &caller_tmp)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "redirect child failed: {}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        fs::remove_dir_all(repo).ok();
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
