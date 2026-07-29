#!/usr/bin/env rust-script
//! Check out dev-hermit's opt-in Reverie backend submodules.

use std::env;
use std::path::{Path, PathBuf};
use std::process::{self, Command, Output};

const USAGE: &str = r#"Usage: scripts/checkout-optional-submodules.rs <e9patch|sabre|all>

Checks out one or both optional Reverie backend source trees at their pinned
revisions. These submodules use `update = none`, so ordinary recursive
submodule initialization intentionally skips them.
"#;

#[derive(Clone, Copy)]
struct OptionalSubmodule {
    name: &'static str,
    path: &'static str,
}

const E9PATCH: OptionalSubmodule = OptionalSubmodule {
    name: "e9patch",
    path: "third-party/e9patch",
};
const SABRE: OptionalSubmodule = OptionalSubmodule {
    name: "sabre",
    path: "third-party/sabre",
};

fn main() {
    if let Err(message) = run() {
        eprintln!("checkout-optional-submodules: {message}");
        process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let requested = match env::args().nth(1).as_deref() {
        Some("e9patch") => vec![E9PATCH],
        Some("sabre") => vec![SABRE],
        Some("all") => vec![E9PATCH, SABRE],
        Some("-h" | "--help") => {
            print!("{USAGE}");
            return Ok(());
        }
        Some(value) => return Err(format!("unknown selection {value:?}\n\n{USAGE}")),
        None => return Err(format!("missing selection\n\n{USAGE}")),
    };
    if env::args().nth(2).is_some() {
        return Err(format!("too many arguments\n\n{USAGE}"));
    }

    let root = find_workspace_root()?;
    let reverie = root.join("reverie");

    run_git(
        &root,
        &[
            "-c",
            "submodule.reverie.update=checkout",
            "submodule",
            "update",
            "--init",
            "--checkout",
            "--",
            "reverie",
        ],
    )?;

    for submodule in requested {
        checkout_and_verify(&reverie, submodule)?;
    }
    Ok(())
}

fn checkout_and_verify(reverie: &Path, submodule: OptionalSubmodule) -> Result<(), String> {
    run_git(reverie, &["submodule", "sync", "--", submodule.path])?;

    let update_override = format!("submodule.{}.update=checkout", submodule.path);
    run_git(
        reverie,
        &[
            "-c",
            &update_override,
            "submodule",
            "update",
            "--init",
            "--checkout",
            "--recursive",
            "--",
            submodule.path,
        ],
    )?;

    let expected = git_stdout(reverie, &["rev-parse", &format!(":{}", submodule.path)])?;
    let actual = git_stdout(&reverie.join(submodule.path), &["rev-parse", "HEAD"])?;
    if actual != expected {
        return Err(format!(
            "reverie/{} is at {actual}, expected pinned revision {expected}",
            submodule.path
        ));
    }

    println!(
        "Optional submodule {} ready: reverie/{} @ {}",
        submodule.name, submodule.path, actual
    );
    Ok(())
}

fn find_workspace_root() -> Result<PathBuf, String> {
    let mut directory = env::current_dir().map_err(|error| error.to_string())?;
    loop {
        if directory.join(".gitmodules").is_file()
            && directory
                .join("scripts/checkout-optional-submodules.rs")
                .is_file()
        {
            return Ok(directory);
        }
        if !directory.pop() {
            return Err("could not locate the dev-hermit workspace root".to_string());
        }
    }
}

fn git_stdout(directory: &Path, args: &[&str]) -> Result<String, String> {
    let output = git_output(directory, args)?;
    if !output.status.success() {
        return Err(command_failure(args, &output));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn run_git(directory: &Path, args: &[&str]) -> Result<(), String> {
    let output = git_output(directory, args)?;
    if !output.status.success() {
        return Err(command_failure(args, &output));
    }
    if !output.stdout.is_empty() {
        print!("{}", String::from_utf8_lossy(&output.stdout));
    }
    if !output.stderr.is_empty() {
        eprint!("{}", String::from_utf8_lossy(&output.stderr));
    }
    Ok(())
}

fn git_output(directory: &Path, args: &[&str]) -> Result<Output, String> {
    let mut command = if command_in_path("with-proxy") {
        let mut wrapped = Command::new("with-proxy");
        wrapped.arg("git");
        wrapped
    } else {
        Command::new("git")
    };
    command
        .arg("-C")
        .arg(directory)
        .args(args)
        .output()
        .map_err(|error| format!("failed to run git: {error}"))
}

fn command_in_path(program: &str) -> bool {
    env::var_os("PATH").is_some_and(|path| {
        env::split_paths(&path).any(|directory| directory.join(program).is_file())
    })
}

fn command_failure(args: &[&str], output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr);
    format!(
        "git {} failed with {}: {}",
        args.join(" "),
        output.status,
        stderr.trim()
    )
}
