#!/usr/bin/env rust-script
/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 */
//! Per-dtid activity / STARVATION-TAIL analysis of a Hermit/Detcore --log info trace.
//!
//! This is the log-analysis query the demo5 wedge investigation kept needing and
//! that no existing tool answers directly:
//!
//!     "Which dtid stopped getting scheduled, at what committed virtual time,
//!      and how much of the run elapsed after it went silent?"
//!
//! `hermit log-diff` finds the first *line-content* divergence but derails on a
//! benign early reorder and cannot express "thread X disappears"; it only reports
//! "run 1 contains N extra messages". `scripts/log_timeslice.rs` contains the raw
//! turn-taking sequence but as one run-length-encoded string you must eyeball.
//! This query collapses that into a per-dtid table and flags the *starvation
//! tail*: a thread whose last turn is far before the end while committed virtual
//! time keeps racing ahead (the demo5 QEMU-vCPU-starvation signature).
//!
//! Usage:
//!     ./dtid_activity.rs < hermit-info.log
//!     hermit run --log info --strict -- ./guest 2>&1 | ./dtid_activity.rs
//!
//! Markers parsed (all emitted at --log info):
//!   * ` COMMIT turn N, dettid D ... on previously committed <VT>s`
//!   * `[detcore, dtid D] ending timeslice TN. X syscalls ...`
//!   * `DETLOG [syscall]...[detcore, dtid D] inbound syscall:`

use std::collections::BTreeMap;
use std::collections::HashMap;
use std::io::{self, Read};

#[derive(Default, Clone)]
struct Dtid {
    turns: u64,          // scheduler COMMIT turns granted to this dtid
    slices: u64,         // timeslices that ended owned by this dtid
    syscalls: u64,       // inbound syscalls attributed while this dtid was current
    first_slice: Option<u64>,
    last_slice: Option<u64>,
    first_vt_ns: Option<i128>,
    last_vt_ns: Option<i128>,
    exited: bool, // saw an exit()/exit_group() for this dtid: it is DONE, not starved
    syscall_hist: HashMap<String, u64>, // syscall name -> count (role fingerprint)
}

/// The syscall name in `inbound syscall: <name>(...)`.
fn inbound_syscall_name(line: &str) -> Option<&str> {
    let i = line.find("inbound syscall: ")? + "inbound syscall: ".len();
    let rest = &line[i..];
    let end = rest.find(['(', ' ', ':']).unwrap_or(rest.len());
    let name = rest[..end].trim();
    if name.is_empty() { None } else { Some(name) }
}

/// Compact "name×count" of the top-`k` syscalls for a dtid (its role fingerprint).
fn top_syscalls(hist: &HashMap<String, u64>, k: usize) -> String {
    let mut v: Vec<(&String, &u64)> = hist.iter().collect();
    v.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
    v.iter()
        .take(k)
        .map(|(n, c)| format!("{}×{}", n, c))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Parse a virtual-time literal like `1_640_995_199.000_500_000s` into ns.
fn parse_virt_ns(tok: &str) -> Option<i128> {
    let clean: String = tok.chars().filter(|c| *c != '_').collect();
    let clean = clean.trim_end_matches('s');
    let (secs, frac) = clean.split_once('.').unwrap_or((clean, ""));
    let mut ns: i128 = 0;
    for (i, c) in frac.bytes().enumerate() {
        if i >= 9 || !c.is_ascii_digit() {
            break;
        }
        ns = ns * 10 + (c - b'0') as i128;
    }
    for _ in frac.len().min(9)..9 {
        ns *= 10;
    }
    Some(secs.parse::<i128>().ok()? * 1_000_000_000 + ns)
}

/// Extract the integer immediately following `needle` in `line`.
fn num_after(line: &str, needle: &str) -> Option<u64> {
    let i = line.find(needle)? + needle.len();
    let rest = &line[i..];
    let end = rest
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(rest.len());
    rest[..end].parse().ok()
}

fn fmt_s(ns: i128) -> String {
    format!("{:.3}", ns as f64 / 1_000_000_000.0)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");

    let mut map: BTreeMap<u64, Dtid> = BTreeMap::new();
    // A "slice" is one scheduler turn here (COMMIT-granularity); we also honor the
    // explicit "ending timeslice" lines for the owning-dtid attribution.
    let mut slice_ix: u64 = 0;
    let mut last_vt: Option<i128> = None;
    let mut cur_owner: Option<u64> = None; // dtid of the turn currently being described

    for line in input.lines() {
        if let Some(cpos) = line.find("COMMIT turn ") {
            if let Some(d) = num_after(&line[cpos..], "dettid ") {
                let vt = line.find("previously committed ").and_then(|vpos| {
                    let tok = line[vpos + "previously committed ".len()..]
                        .split_whitespace()
                        .next()
                        .unwrap_or("");
                    parse_virt_ns(tok)
                });
                if let Some(v) = vt {
                    last_vt = Some(v);
                }
                cur_owner = Some(d);
                let e = map.entry(d).or_default();
                e.turns += 1;
                e.first_slice.get_or_insert(slice_ix);
                e.last_slice = Some(slice_ix);
                if let Some(v) = last_vt {
                    e.first_vt_ns.get_or_insert(v);
                    e.last_vt_ns = Some(v);
                }
                slice_ix += 1;
            }
            continue;
        }
        if line.contains("inbound syscall:") {
            if let Some(d) = cur_owner {
                let e = map.entry(d).or_default();
                e.syscalls += 1;
                if let Some(name) = inbound_syscall_name(line) {
                    *e.syscall_hist.entry(name.to_string()).or_insert(0) += 1;
                }
                if line.contains("inbound syscall: exit_group(")
                    || line.contains("inbound syscall: exit(")
                {
                    e.exited = true;
                }
            }
            continue;
        }
        if let Some(epos) = line.find("ending timeslice T") {
            // Authoritative owner for the ending slice.
            let seg = &line[..epos];
            if let Some(p) = seg.rfind("dtid ") {
                if let Some(d) = num_after(&seg[p..], "dtid ") {
                    let e = map.entry(d).or_default();
                    e.slices += 1;
                    cur_owner = Some(d);
                }
            }
            continue;
        }
    }

    if map.is_empty() {
        eprintln!(
            "no COMMIT/timeslice lines found. Run hermit with --log info (or debug/trace)."
        );
        std::process::exit(2);
    }

    let total_turns = slice_ix; // last slice index + 1
    let last_slice_ix = total_turns.saturating_sub(1);
    let run_end_vt = last_vt;

    println!("=== Hermit per-dtid activity & starvation analysis ===");
    println!(
        "distinct dtids: {}    total scheduler turns: {}    final committed vtime: {} s",
        map.len(),
        total_turns,
        run_end_vt.map(fmt_s).unwrap_or_else(|| "n/a".into())
    );
    println!();

    // Table, sorted by turns descending (the busy pollers float to the top).
    let mut rows: Vec<(&u64, &Dtid)> = map.iter().collect();
    rows.sort_by_key(|(_, s)| std::cmp::Reverse(s.turns));

    println!(
        "{:>5} {:>8} {:>8} {:>10} {:>10} {:>16} {:>16} {:>12} {:>13}  flags",
        "dtid", "turns", "syscall", "first_trn", "last_trn", "first_vt_s", "last_vt_s",
        "tail_turns", "tail_vt_s"
    );

    // A dtid is "starved" if it stopped running well before the end (last turn in
    // the first 60% of the run) yet committed virtual time kept advancing by a
    // material amount (>1s) after its last turn -- i.e. the clock raced ahead
    // while this thread never ran again.  That is exactly the demo5 wedge.
    let mut starved: Vec<(u64, u64, i128)> = Vec::new(); // (dtid, tail_turns, tail_vt_ns)

    for (d, s) in rows {
        let last_trn = s.last_slice.unwrap_or(0);
        let tail_turns = last_slice_ix.saturating_sub(last_trn);
        let tail_vt = match (run_end_vt, s.last_vt_ns) {
            (Some(end), Some(l)) => (end - l).max(0),
            _ => 0,
        };
        let mut flags = String::new();
        if s.exited {
            flags.push_str("EXITED ");
        }
        let early = total_turns > 0 && (last_trn as f64) < (total_turns as f64) * 0.60;
        // Starvation requires the thread to still be ALIVE: a thread that called
        // exit()/exit_group() is done, not starved, no matter how large its tail.
        if !s.exited && early && tail_vt > 1_000_000_000 {
            flags.push_str("STARVED-TAIL ");
            starved.push((*d, tail_turns, tail_vt));
        }
        if s.turns as f64 > total_turns as f64 * 0.20 {
            flags.push_str("BUSY-POLLER ");
        }
        println!(
            "{:>5} {:>8} {:>8} {:>10} {:>10} {:>16} {:>16} {:>12} {:>13}  {}",
            d,
            s.turns,
            s.syscalls,
            s.first_slice.map(|x| x.to_string()).unwrap_or_default(),
            last_trn,
            s.first_vt_ns.map(fmt_s).unwrap_or_default(),
            s.last_vt_ns.map(fmt_s).unwrap_or_default(),
            tail_turns,
            fmt_s(tail_vt),
            flags.trim_end()
        );
    }
    println!();

    println!("=== wedge witnesses (starvation tail, worst first) ===");
    if starved.is_empty() {
        println!("  none: every dtid ran into the final 40% of the run, or the clock did not");
        println!("  race ahead after any thread went silent. No starvation-tail signature.");
    } else {
        starved.sort_by_key(|(_, _, vt)| std::cmp::Reverse(*vt));
        for (d, tail_turns, tail_vt) in &starved {
            let pct = 100.0 * (*tail_turns as f64) / (total_turns as f64);
            println!(
                "  dtid {:>3}: last ran at turn {} ({} s committed), then {} turns \
                 ({:.1}% of run) with the committed clock advancing +{} s and this \
                 thread NEVER scheduled again.",
                d,
                map[d].last_slice.unwrap_or(0),
                map[d].last_vt_ns.map(fmt_s).unwrap_or_default(),
                tail_turns,
                pct,
                fmt_s(*tail_vt),
            );
        }
        println!();
        println!(
            "INTERPRETATION: a large starvation tail combined with a BUSY-POLLER dtid is the \
             deadline-less unproductive-poller wedge -- the poller keeps the run queue non-empty \
             so committed vtime races ahead while the starved thread's next event is never reached."
        );
    }
    println!();

    // Role fingerprints: the top syscalls per dtid. dtid NUMBERS are assigned by
    // creation order and are NOT stable across runs (an extra thread shifts them),
    // so when diffing two runs, match threads by this fingerprint, not by number.
    println!("=== per-dtid role fingerprint (top syscalls; match ACROSS runs by this, not by dtid#) ===");
    let mut rows2: Vec<(&u64, &Dtid)> = map.iter().collect();
    rows2.sort_by_key(|(_, s)| std::cmp::Reverse(s.turns));
    for (d, s) in rows2 {
        println!("  dtid {:>3} ({:>7} turns): {}", d, s.turns, top_syscalls(&s.syscall_hist, 4));
    }
}
