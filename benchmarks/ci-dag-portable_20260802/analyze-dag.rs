#!/usr/bin/env rust-script
//! Analyze the portable CI DAG: real critical path, parallel utilization, DOT.
//! ```cargo
//! [dependencies]
//! serde_json = "1"
//! ```
use std::collections::HashMap;
use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dag_path = &args[1]; // ci/dag/portable.json
    let csv_path = &args[2]; // step_profiles CSV with real elapsed_s
    let dot_out = &args[3];  // output .dot

    let dag: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dag_path).unwrap()).unwrap();
    let steps = dag["steps"].as_array().unwrap();

    // Real per-node elapsed from CSV (step column -> elapsed_s).
    let csv = std::fs::read_to_string(csv_path).unwrap();
    let mut real: HashMap<String, f64> = HashMap::new();
    let mut hdr: Vec<&str> = vec![];
    for (i, line) in csv.lines().enumerate() {
        let cols: Vec<&str> = line.split(',').collect();
        if i == 0 { hdr = cols; continue; }
        let get = |name: &str| -> &str {
            hdr.iter().position(|h| *h == name).map(|p| cols[p]).unwrap_or("")
        };
        let step = get("step").to_string();
        if let Ok(e) = get("elapsed_s").parse::<f64>() { real.insert(step, e); }
    }

    // Build node table keyed "group.job".
    let mut key_of = HashMap::new();
    let mut est = HashMap::new();
    let mut cls = HashMap::new();
    let mut deps: HashMap<String, Vec<String>> = HashMap::new();
    let mut order: Vec<String> = vec![];
    for s in steps {
        let g = s["group"].as_str().unwrap();
        let j = s["job"].as_str().unwrap();
        let k = format!("{g}.{j}");
        key_of.insert(k.clone(), true);
        est.insert(k.clone(), s["hint"]["est_duration_s"].as_f64().unwrap_or(0.0));
        cls.insert(k.clone(), s["hint"]["classification"].as_str().unwrap_or("?").to_string());
        let d: Vec<String> = s["deps"].as_array().map(|a| a.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default();
        deps.insert(k.clone(), d);
        order.push(k);
    }

    let w = |k: &str| -> f64 { *real.get(k).unwrap_or(&est.get(k).copied().unwrap_or(0.0)) };

    // Longest-path (critical path) with memo over the DAG (deps -> finish time).
    let mut finish: HashMap<String, f64> = HashMap::new();
    let mut pred: HashMap<String, Option<String>> = HashMap::new();
    fn dfs(k: &str, deps: &HashMap<String, Vec<String>>, w: &dyn Fn(&str) -> f64,
           finish: &mut HashMap<String, f64>, pred: &mut HashMap<String, Option<String>>) -> f64 {
        if let Some(f) = finish.get(k) { return *f; }
        let mut best = 0.0f64; let mut bp: Option<String> = None;
        for d in deps.get(k).cloned().unwrap_or_default() {
            let df = dfs(&d, deps, w, finish, pred);
            if df > best { best = df; bp = Some(d); }
        }
        let f = best + w(k);
        finish.insert(k.to_string(), f); pred.insert(k.to_string(), bp);
        f
    }
    for k in &order { dfs(k, &deps, &w, &mut finish, &mut pred); }

    // Critical path = trace back from max-finish node.
    let (mut cur, &cplen) = finish.iter().max_by(|a, b| a.1.partial_cmp(b.1).unwrap()).unwrap();
    let mut cur = cur.clone();
    let mut cp: Vec<String> = vec![];
    loop { cp.push(cur.clone()); match pred.get(&cur).cloned().flatten() { Some(p) => cur = p, None => break } }
    cp.reverse();
    let cp_set: std::collections::HashSet<String> = cp.iter().cloned().collect();

    let total_work: f64 = order.iter().map(|k| w(k)).sum();

    eprintln!("=== REAL DAG ANALYSIS (portable) ===");
    eprintln!("nodes            : {}", order.len());
    eprintln!("total node-work  : {:.0}s ({:.1} min)", total_work, total_work / 60.0);
    eprintln!("critical path    : {:.0}s ({:.1} min)", cplen, cplen / 60.0);
    eprintln!("ideal speedup    : {:.2}x (total_work / critical_path)", total_work / cplen);
    eprintln!("critical path chain:");
    for k in &cp { eprintln!("   {:>7.1}s  {}", w(k), k); }
    eprintln!("\ntop 10 nodes by real elapsed:");
    let mut byt: Vec<(&String, f64)> = order.iter().map(|k| (k, w(k))).collect();
    byt.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    for (k, t) in byt.iter().take(10) { eprintln!("   {:>7.1}s  {}", t, k); }

    // Emit DOT. Node weight -> color; critical path bold red.
    let mut f = std::fs::File::create(dot_out).unwrap();
    writeln!(f, "digraph portable_ci_dag {{").unwrap();
    writeln!(f, "  rankdir=LR; node [shape=box,style=\"rounded,filled\",fontname=\"Helvetica\",fontsize=10]; edge [color=\"#888888\"];").unwrap();
    writeln!(f, "  labelloc=t; fontsize=16; label=\"Hermit portable CI DAG @c7531a83  |  46 nodes  |  total work {:.0}s ({:.0}m)  |  critical path {:.0}s ({:.0}m)  |  ideal speedup {:.1}x  |  measured wall 1920s @4cores 68.6%busy\";",
        total_work, total_work/60.0, cplen, cplen/60.0, total_work/cplen).unwrap();
    for k in &order {
        let t = w(k);
        let color = if cp_set.contains(k) { "#ff6b6b" }
            else { match cls.get(k).map(|s| s.as_str()) {
                Some("cpu-bound") => "#ffd39b",
                Some("latency-bound") => "#b3d9ff",
                Some("light") => "#e8e8e8",
                _ => "#ffffff" } };
        let pen = if cp_set.contains(k) { 3 } else { 1 };
        writeln!(f, "  \"{k}\" [label=\"{k}\\n{t:.0}s\",fillcolor=\"{color}\",penwidth={pen}];").unwrap();
    }
    for k in &order {
        for d in deps.get(k).cloned().unwrap_or_default() {
            let crit = cp_set.contains(k) && cp_set.contains(&d);
            let (col, pw) = if crit { ("#d00000", 3) } else { ("#888888", 1) };
            writeln!(f, "  \"{d}\" -> \"{k}\" [color=\"{col}\",penwidth={pw}];").unwrap();
        }
    }
    writeln!(f, "}}").unwrap();
    eprintln!("\nDOT written: {}", dot_out);
}
