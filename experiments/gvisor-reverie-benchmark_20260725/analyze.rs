#!/usr/bin/env rust-script
//! Analysis for the gVisor vs Reverie benchmark v2.
//!
//! Reads the authoritative raw TSVs and produces:
//!   * real-report.tsv    : per (workload,backend) median wall, syscall count,
//!                          syscalls/sec, slowdown, amortized per-syscall overhead.
//!   * marginal-report.tsv: per backend fitted slope (ns/syscall) + intercept,
//!                          with a deterministic-bootstrap 95% slope CI, and the
//!                          four per-N medians so nonlinearity is visible.
//!
//! Validity rule matches run_benchmarks.rs exactly:
//!   status == expected_status && !stderr_tail.contains("Function not implemented")

use std::collections::BTreeMap;
use std::env;
use std::fs;

// --- deterministic PRNG (SplitMix64) for reproducible bootstrap ---
struct SplitMix64(u64);
impl SplitMix64 {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }
    // Unbiased index in [0, n) via rejection.
    fn index(&mut self, n: usize) -> usize {
        let n = n as u64;
        let zone = u64::MAX - (u64::MAX % n);
        loop {
            let r = self.next_u64();
            if r < zone {
                return (r % n) as usize;
            }
        }
    }
}

#[derive(Clone)]
struct Row {
    workload: String,
    backend: String,
    phase: String,
    wall_ns: u128,
    status: i32,
    observed: Option<u64>,
    operations: Option<u64>,
    stderr: String,
}

fn expected_status(workload: &str) -> i32 {
    if workload.starts_with("find-usr") { 1 } else { 0 }
}

fn parse(path: &str) -> Vec<Row> {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("read {path}: {e}"));
    let mut rows = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if i == 0 {
            continue;
        }
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() < 9 {
            continue;
        }
        rows.push(Row {
            workload: f[1].to_string(),
            backend: f[2].to_string(),
            phase: f[3].to_string(),
            wall_ns: f[5].parse().unwrap_or(0),
            status: f[6].parse().unwrap_or(-999),
            observed: f[7].parse().ok(),
            operations: f[8].parse().ok(),
            stderr: f.get(9).copied().unwrap_or("").to_string(),
        });
    }
    rows
}

fn valid(r: &Row) -> bool {
    r.phase == "measure"
        && r.status == expected_status(&r.workload)
        && !r.stderr.contains("Function not implemented")
}

fn median_u128(v: &mut [u128]) -> u128 {
    v.sort_unstable();
    v[v.len() / 2]
}

const BACKEND_ORDER: [&str; 7] = [
    "native",
    "gvisor-systrap",
    "gvisor-kvm",
    "reverie-ptrace",
    "reverie-dbi",
    "reverie-kvm",
    "reverie-sabre",
];

fn main() {
    let root = env::var("BENCH_ROOT")
        .unwrap_or_else(|_| "/home/newton/work/dev-hermit/hermit/target/gvisor-benchmark-v2".into());
    real_report(&root);
    marginal_report(&root);
}

// ---------------- REAL WORKLOAD REPORT ----------------
fn real_report(root: &str) {
    let rows = parse(&format!("{root}/results-cpu112-v3/real-raw.tsv"));
    // group valid measure samples
    let mut walls: BTreeMap<(String, String), Vec<u128>> = BTreeMap::new();
    let mut counts: BTreeMap<(String, String), Vec<u64>> = BTreeMap::new();
    let mut ops: BTreeMap<String, Option<u64>> = BTreeMap::new();
    for r in &rows {
        ops.entry(r.workload.clone()).or_insert(r.operations);
        if !valid(r) {
            continue;
        }
        walls
            .entry((r.workload.clone(), r.backend.clone()))
            .or_default()
            .push(r.wall_ns);
        if let Some(c) = r.observed {
            counts
                .entry((r.workload.clone(), r.backend.clone()))
                .or_default()
                .push(c);
        }
    }

    // native median per workload
    let mut native_med: BTreeMap<String, u128> = BTreeMap::new();
    for ((w, b), v) in walls.iter_mut() {
        if b == "native" {
            native_med.insert(w.clone(), median_u128(v));
        }
    }

    // canonical syscall count per workload: exact operations for getpid & dd,
    // else median ptrace observed count.
    let workloads: Vec<String> = {
        let mut s: Vec<String> = walls.keys().map(|(w, _)| w.clone()).collect();
        s.sort();
        s.dedup();
        s
    };

    let mut out = String::from(
        "workload\tbackend\tsamples\tmedian_wall_ns\tmedian_wall_s\tsyscall_count\tcount_basis\tsyscalls_per_s\tslowdown_vs_native\tper_syscall_overhead_ns\n",
    );
    println!("\n================= REAL WORKLOAD REPORT (CPU112, median of 9) =================");
    for w in &workloads {
        // canonical count
        let exact_ops = ops.get(w).and_then(|o| *o);
        let use_exact = (w.starts_with("getpid") || w.starts_with("dd-byte-io")) && exact_ops.is_some();
        let ptrace_count = counts
            .get(&(w.clone(), "reverie-ptrace".to_string()))
            .map(|v| {
                let mut vv: Vec<u128> = v.iter().map(|x| *x as u128).collect();
                median_u128(&mut vv) as u64
            });
        let (canon_count, basis) = if use_exact {
            (exact_ops, "exact-operations".to_string())
        } else {
            (ptrace_count, "median-ptrace-observed".to_string())
        };
        let nat = *native_med.get(w).unwrap();
        println!(
            "\n--- {w}  (canonical syscall count = {}  [{}]) ---",
            canon_count.map(|c| c.to_string()).unwrap_or("NA".into()),
            basis
        );
        println!(
            "{:<16} {:>6} {:>14} {:>10} {:>14} {:>10} {:>14}",
            "backend", "n", "median_s", "slowdown", "syscalls/s", "us/call", "count"
        );
        for b in BACKEND_ORDER {
            let key = (w.clone(), b.to_string());
            let Some(v) = walls.get(&key) else { continue };
            let mut vv = v.clone();
            let med = median_u128(&mut vv);
            let med_s = med as f64 / 1e9;
            let slow = med as f64 / nat as f64;
            let (sps, us_call) = match canon_count {
                Some(c) if c > 0 => {
                    let sps = c as f64 / med_s;
                    let overhead_ns = (med as f64 - nat as f64) / c as f64;
                    (sps, overhead_ns / 1000.0)
                }
                _ => (f64::NAN, f64::NAN),
            };
            println!(
                "{:<16} {:>6} {:>14.3} {:>10.1} {:>14.0} {:>10.4} {:>14}",
                b,
                v.len(),
                med_s,
                slow,
                sps,
                us_call,
                canon_count.map(|c| c.to_string()).unwrap_or("NA".into())
            );
            let overhead_ns = canon_count
                .filter(|c| *c > 0)
                .map(|c| (med as f64 - nat as f64) / c as f64);
            out.push_str(&format!(
                "{}\t{}\t{}\t{}\t{:.4}\t{}\t{}\t{:.1}\t{:.3}\t{}\n",
                w,
                b,
                v.len(),
                med,
                med_s,
                canon_count.map(|c| c.to_string()).unwrap_or("NA".into()),
                basis,
                canon_count.map(|c| c as f64 / med_s).unwrap_or(f64::NAN),
                slow,
                overhead_ns.map(|o| format!("{o:.1}")).unwrap_or("NA".into()),
            ));
        }
    }
    let p = format!("{root}/real-report.tsv");
    fs::write(&p, out).unwrap();
    println!("\nwrote {p}");
}

// ---------------- MARGINAL SLOPE REPORT ----------------
fn ols_slope(xs: &[f64], ys: &[f64]) -> (f64, f64) {
    let n = xs.len() as f64;
    let sx: f64 = xs.iter().sum();
    let sy: f64 = ys.iter().sum();
    let sxx: f64 = xs.iter().map(|x| x * x).sum();
    let sxy: f64 = xs.iter().zip(ys).map(|(x, y)| x * y).sum();
    let slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);
    let intercept = (sy - slope * sx) / n;
    (slope, intercept)
}

fn marginal_report(root: &str) {
    let path = format!("{root}/marginal-cpu112/marginal-raw.tsv");
    if !std::path::Path::new(&path).exists() {
        println!("\n[marginal] {path} not present yet; skipping marginal report.");
        return;
    }
    let rows = parse(&path);
    let ns: [u64; 4] = [1_000, 10_000, 100_000, 1_000_000];
    // samples[backend][N] = Vec<wall_ns>
    let mut samples: BTreeMap<String, BTreeMap<u64, Vec<f64>>> = BTreeMap::new();
    for r in &rows {
        if !valid(r) {
            continue;
        }
        // workload name is getpid-<N>
        let Some(nstr) = r.workload.strip_prefix("getpid-") else { continue };
        let Ok(n) = nstr.parse::<u64>() else { continue };
        samples
            .entry(r.backend.clone())
            .or_default()
            .entry(n)
            .or_default()
            .push(r.wall_ns as f64);
    }

    let mut out = String::from(
        "backend\tslope_ns_per_syscall\tintercept_ns\tslope_ci95_lo\tslope_ci95_hi\tmed_1k_ns\tmed_10k_ns\tmed_100k_ns\tmed_1M_ns\tn_per_cell\n",
    );
    println!("\n============ MARGINAL getpid SLOPE (ns/syscall), CPU112 ============");
    println!(
        "{:<16} {:>14} {:>16} {:>22} {:>10}",
        "backend", "slope_ns/call", "intercept_ns", "95% CI (ns/call)", "n/cell"
    );
    for b in BACKEND_ORDER {
        let Some(bymap) = samples.get(b) else { continue };
        if ns.iter().any(|n| bymap.get(n).map_or(true, |v| v.is_empty())) {
            println!("{b:<16} incomplete (not all N present yet)");
            continue;
        }
        // point medians
        let xs: Vec<f64> = ns.iter().map(|n| *n as f64).collect();
        let meds: Vec<f64> = ns
            .iter()
            .map(|n| {
                let mut v: Vec<u128> = bymap[n].iter().map(|x| *x as u128).collect();
                median_u128(&mut v) as f64
            })
            .collect();
        let (slope, intercept) = ols_slope(&xs, &meds);

        // bootstrap: resample the raw observations at each N independently
        let replicates = 10_000usize;
        let mut rng = SplitMix64(0xC0FFEE_u64.wrapping_add(
            b.bytes().fold(1469598103934665603u64, |h, c| {
                (h ^ c as u64).wrapping_mul(1099511628211)
            }),
        ));
        let mut slopes = Vec::with_capacity(replicates);
        for _ in 0..replicates {
            let bmeds: Vec<f64> = ns
                .iter()
                .map(|n| {
                    let obs = &bymap[n];
                    let m = obs.len();
                    let mut resampled: Vec<u128> =
                        (0..m).map(|_| obs[rng.index(m)] as u128).collect();
                    median_u128(&mut resampled) as f64
                })
                .collect();
            let (s, _) = ols_slope(&xs, &bmeds);
            slopes.push(s);
        }
        slopes.sort_by(|a, c| a.partial_cmp(c).unwrap());
        let lo = slopes[(0.025 * replicates as f64) as usize];
        let hi = slopes[(0.975 * replicates as f64) as usize];
        let ncell = bymap[&ns[0]].len();
        println!(
            "{:<16} {:>14.2} {:>16.0} {:>10.2}..{:<10.2} {:>10}",
            b, slope, intercept, lo, hi, ncell
        );
        out.push_str(&format!(
            "{}\t{:.3}\t{:.0}\t{:.3}\t{:.3}\t{:.0}\t{:.0}\t{:.0}\t{:.0}\t{}\n",
            b, slope, intercept, lo, hi, meds[0], meds[1], meds[2], meds[3], ncell
        ));
    }
    let p = format!("{root}/marginal-report.tsv");
    fs::write(&p, out).unwrap();
    println!("\nwrote {p}");
    // Also print per-N medians in us/call terms for readability
    println!("\n--- per-N median wall (ns) and implied us/call (median_wall/N) ---");
    println!("{:<16} {:>12} {:>12} {:>12} {:>12}", "backend", "1k", "10k", "100k", "1M");
    for b in BACKEND_ORDER {
        let Some(bymap) = samples.get(b) else { continue };
        if ns.iter().any(|n| bymap.get(n).map_or(true, |v| v.is_empty())) {
            continue;
        }
        let cells: Vec<String> = ns
            .iter()
            .map(|n| {
                let mut v: Vec<u128> = bymap[n].iter().map(|x| *x as u128).collect();
                let m = median_u128(&mut v) as f64;
                format!("{:.3}", m / *n as f64 / 1000.0)
            })
            .collect();
        println!(
            "{:<16} {:>12} {:>12} {:>12} {:>12}  (us/call)",
            b, cells[0], cells[1], cells[2], cells[3]
        );
    }
}
