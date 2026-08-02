#!/usr/bin/env rust-script
//! Render a CI-runtime trend (daily median/p90 per lane) from `gh run list` JSONL dumps.
//!
//! Input: one JSONL file per lane, named `lane-<name>.jsonl`, each line an object with at least
//! `created` (ISO8601), `started` (ISO8601), `updated` (ISO8601), and `concl` (conclusion; null =
//! still running / not completed). Duration = `updated - started` (seconds) for runs that have a
//! non-null `concl`. These dumps come from per-workflow queries:
//!
//!   with-proxy gh api -X GET \
//!     "repos/rrnewton/hermit/actions/workflows/<id>/runs" -f "created=>=<DATE>" --paginate \
//!     --jq '.workflow_runs[] | {branch:.head_branch, concl:.conclusion, created:.created_at,
//!            event:.event, id:.id, started:.run_started_at, updated:.updated_at, wf:.name}'
//!
//! (Per-workflow queries are required: the flat `gh run list` API caps at ~1000 results, which for a
//! high-volume repo covers only ~1 day.)
//!
//! Output: `results/daily-aggregate.csv` (lane,date,n,median_min,p90_min) plus an ASCII trend chart
//! on stdout. Usage: `./render-ci-trend.rs <input-dir>` (defaults to `ignored/raw`).
//!
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

/// Parse an ISO8601 `YYYY-MM-DDTHH:MM:SSZ` timestamp to epoch seconds (UTC, no sub-second).
fn epoch(ts: &str) -> Option<i64> {
    let b = ts.as_bytes();
    if b.len() < 19 {
        return None;
    }
    let g = |a: usize, z: usize| ts[a..z].parse::<i64>().ok();
    let (y, mo, d) = (g(0, 4)?, g(5, 7)?, g(8, 10)?);
    let (h, mi, s) = (g(11, 13)?, g(14, 16)?, g(17, 19)?);
    // Days from civil (Howard Hinnant's algorithm), UTC.
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = if y2 >= 0 { y2 } else { y2 - 399 } / 400;
    let yoe = y2 - era * 400;
    let doy = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    Some(days * 86400 + h * 3600 + mi * 60 + s)
}

fn pctl(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let k = (sorted.len() - 1) as f64 * p;
    let f = k.floor() as usize;
    let c = (f + 1).min(sorted.len() - 1);
    sorted[f] + (sorted[c] - sorted[f]) * (k - f as f64)
}

/// Minimal extractor for a flat JSON object's string/null field value (no nested objects needed).
fn field<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let pat = format!("\"{key}\":");
    let i = line.find(&pat)? + pat.len();
    let rest = line[i..].trim_start();
    if let Some(r) = rest.strip_prefix('"') {
        let end = r.find('"')?;
        Some(&r[..end])
    } else if rest.starts_with("null") {
        Some("")
    } else {
        // number / bool: read until , or }
        let end = rest.find([',', '}'])?;
        Some(rest[..end].trim())
    }
}

fn main() {
    let dir: PathBuf = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "ignored/raw".to_string())
        .into();

    // lane -> date -> durations (minutes)
    let mut data: BTreeMap<String, BTreeMap<String, Vec<f64>>> = BTreeMap::new();
    let mut entries: Vec<_> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.starts_with("lane-") && n.ends_with(".jsonl"))
                .unwrap_or(false)
        })
        .collect();
    entries.sort();

    for path in &entries {
        let name = path.file_name().unwrap().to_str().unwrap();
        let lane = name
            .trim_start_matches("lane-")
            .trim_end_matches(".jsonl")
            .to_string();
        let text = fs::read_to_string(path).unwrap_or_default();
        let per_day = data.entry(lane).or_default();
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let concl = field(line, "concl").unwrap_or("");
            if concl.is_empty() {
                continue; // not completed
            }
            let (Some(created), Some(started), Some(updated)) = (
                field(line, "created"),
                field(line, "started"),
                field(line, "updated"),
            ) else {
                continue;
            };
            let (Some(s), Some(u)) = (epoch(started), epoch(updated)) else {
                continue;
            };
            let dur = (u - s) as f64;
            if dur <= 0.0 {
                continue;
            }
            let day = created.get(0..10).unwrap_or("").to_string();
            per_day.entry(day).or_default().push(dur / 60.0);
        }
    }

    // CSV
    let mut csv = String::from("lane,date,n,median_min,p90_min\n");
    for (lane, per_day) in &data {
        for (day, xs) in per_day {
            let mut v = xs.clone();
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            csv.push_str(&format!(
                "{lane},{day},{},{:.1},{:.1}\n",
                v.len(),
                pctl(&v, 0.5),
                pctl(&v, 0.9)
            ));
        }
    }
    fs::create_dir_all("results").ok();
    fs::write("results/daily-aggregate.csv", &csv).expect("write csv");
    eprintln!("wrote results/daily-aggregate.csv");

    // ASCII chart: per lane, one row per day, bar length ∝ median (with p90 marker).
    println!("CI runtime trend — daily median (bar) and p90 (│), minutes\n");
    for (lane, per_day) in &data {
        if per_day.is_empty() {
            continue;
        }
        let maxp90 = per_day
            .values()
            .map(|xs| {
                let mut v = xs.clone();
                v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                pctl(&v, 0.9)
            })
            .fold(0.0_f64, f64::max)
            .max(1.0);
        let width = 48.0;
        println!("── {lane} ──");
        for (day, xs) in per_day {
            let mut v = xs.clone();
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let med = pctl(&v, 0.5);
            let p90 = pctl(&v, 0.9);
            let bar = (med / maxp90 * width).round() as usize;
            let p90pos = (p90 / maxp90 * width).round() as usize;
            let mut row: Vec<char> = vec![' '; width as usize + 1];
            for c in row.iter_mut().take(bar) {
                *c = '█';
            }
            if p90pos < row.len() {
                row[p90pos] = '│';
            }
            let rs: String = row.into_iter().collect();
            println!(
                "  {day}  n={:4}  {rs}  med={med:5.1}  p90={p90:5.1}",
                v.len()
            );
        }
        println!();
    }
}
