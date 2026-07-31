#!/usr/bin/env rust-script
//! Slow-drip crates.io squat publisher for the Hermit/Reverie name reservation.
//!
//! Respects crates.io's ~1-new-crate-per-10-min limit by spacing publishes ~11
//! min apart. Detached (nohup/setsid) so it survives the launching agent turn.
//! Idempotent: skips any name already live, so it is safe to re-run.
//!
//! dbi->dbt owner adjustment applied: NO `-dbi` names are published; the `-dbt`
//! equivalents (reverie-dbt, detcore-dbt) are reserved instead.
use std::fs::OpenOptions;
use std::io::Write;
use std::process::Command;
use std::{thread, time::Duration};

const CRATES_DIR: &str = "/home/newton/work/dev-hermit/crates-squat-staging/crates";
const LOG: &str = "/home/newton/work/dev-hermit/crates-squat-staging/ignored/crates-squat-drip.log";
const SPACING_SECS: u64 = 660; // ~11 min, > the 10-min new-crate replenish window
const MAX_ATTEMPTS: u32 = 10;
const UA: &str = "hermit-squat-drip (hermit@rrnewton.github.io)";

fn log(msg: &str) {
    let line = format!("[{}] {}\n", stamp(), msg);
    print!("{line}");
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(LOG) {
        let _ = f.write_all(line.as_bytes());
    }
}

fn stamp() -> String {
    // Cheap UTC HH:MM:SS via `date` to avoid extra crate deps.
    Command::new("date")
        .args(["-u", "+%H:%M:%S"])
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

fn is_live(name: &str) -> bool {
    Command::new("with-proxy")
        .args([
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-A", UA,
            &format!("https://crates.io/api/v1/crates/{name}"),
        ])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim() == "200")
        .unwrap_or(false)
}

fn publish(name: &str) -> (bool, String) {
    let manifest = format!("{CRATES_DIR}/{name}/Cargo.toml");
    match Command::new("with-proxy")
        .args(["cargo", "publish", "--manifest-path", &manifest, "--allow-dirty"])
        .output()
    {
        Ok(o) => {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr));
            (o.status.success(), s)
        }
        Err(e) => (false, format!("spawn error: {e}")),
    }
}

fn main() {
    // 17 remaining names (6 already live from the burst are omitted).
    let remaining = [
        "reverie-process", "reverie-preload", "reverie-core", "reverie-ptrace",
        "reverie-kvm", "reverie-liteinst", "reverie-dbt", "reverie-e9patch",
        "reverie-dynamorio", "reverie-sabre", "test-allocator", "detcore-model",
        "detcore-dbt", "hermit-resources", "hermit-verify", "hermetic-infra",
        "hermit-run",
    ];
    log(&format!("=== DRIP START: {} names, {}s spacing (dbi excluded, dbt included) ===", remaining.len(), SPACING_SECS));

    for name in remaining {
        if is_live(name) {
            log(&format!("SKIP {name} (already live)"));
            continue;
        }
        let mut published = false;
        for attempt in 1..=MAX_ATTEMPTS {
            log(&format!("PUBLISH {name} (attempt {attempt})"));
            let (ok, out) = publish(name);
            let low = out.to_lowercase();
            if ok || low.contains("already exists") || low.contains("already uploaded") {
                log(&format!("PASS {name}"));
                published = true;
                break;
            }
            let rate = out.contains("429") || low.contains("too many") || low.contains("rate limit");
            let tail: Vec<&str> = out.lines().filter(|l| !l.trim().is_empty()).collect();
            let tail = tail.iter().rev().take(2).rev().cloned().collect::<Vec<_>>().join(" | ");
            log(&format!("{} {name} :: {tail}", if rate { "RATELIMIT" } else { "FAIL" }));
            if !rate {
                log(&format!("STOP retrying {name} (non-rate-limit error)"));
                break;
            }
            log(&format!("sleep {SPACING_SECS}s then retry {name}"));
            thread::sleep(Duration::from_secs(SPACING_SECS));
        }
        if published {
            log(&format!("PACE sleep {SPACING_SECS}s before next name"));
            thread::sleep(Duration::from_secs(SPACING_SECS));
        }
    }
    log("=== DRIP DONE ===");
}
