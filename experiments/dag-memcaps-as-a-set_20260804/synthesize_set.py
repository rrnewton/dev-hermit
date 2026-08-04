#!/usr/bin/env python3
"""Unified DAG memory-cap SET view: every node's hard cap vs measured peak,
tightness ratio, classification, compile-bearing flag, and derivation status.

The task's thesis: 54/54 caps are hand-picked round constants NEVER derived in
relation to each other, so OOM migrates to whichever node is next-tightest. This
assembles the ONE table that exposes the set. Each measured peak CARRIES ITS
CONDITION {j, warm/cold, lane, source}: a peak at j=8 warm is a LOWER BOUND for
the production run where CARGO_BUILD_JOBS leaks as NUM_JOBS=nproc (~284/316).
"""
import json, os
GiB=2**30; MiB=2**20
HERMIT="hermit"

# Measured peaks with their measurement CONDITION. bytes, {j, state, lane, src}.
# src=csv: experiments/dag-mem-caps-pinned-jobs_20260804/results.csv (j8 warm-shared)
# src=oom: OOM-kill point (peak>=cap; a floor, real peak is higher/unbounded-at-j)
# src=codex: task note 2026-08-04 14:30 (238b/codex, j1 serial, cgroup CSV)
MEAS={
 "build.workspace":       (6.0*GiB,   "j8 warm; LOWER BOUND (prod j~284 OOMs higher)", "csv"),
 "build.dbi_release":     (6.8*GiB,   "j8 warm", "csv"),
 "build.sabre_release":   (944*MiB,   "j8 warm", "csv"),
 "doc.doctests":          (208*MiB,   "j8 warm", "csv"),
 "test.hermit_unit":      (4.6*GiB,   "j8 warm", "csv"),
 "lint.clippy":           (4.0*GiB,   "j8 warm", "csv"),
 "doc.rustdoc":           (1.1*GiB,   "j8 warm", "csv"),
 "test.regular_crates":   (2.0*GiB,   "j8 warm", "csv"),
 "build.flaky_harnesses": (143.8*MiB, "j8 warm", "csv"),
 "test.detcore_unit":     (1.8*GiB,   "j8 warm", "csv"),
 "test.rr_suite_contract":(2.0*GiB,   "OOM@cap j-unpinned (floor)", "oom"),
 "test.strict_compat":    (6.0*GiB,   "OOM@cap j-unpinned prod, 8 oom_kill (floor)", "oom"),
 "check.backend_abstraction":(3.61*MiB,"j1 serial", "codex"),
 "check.portability_paths":(57.03*MiB,"j1 serial", "codex"),
 "check.script_sigpipe":  (46.46*MiB, "j1 serial", "codex"),
 "e2e.metadata":          (150732800, "j1 serial", "codex"),
 "lint.rustfmt":          (39.75*MiB, "j1 serial", "codex"),
}
# compile-bearing = spawns rustc/cc1plus fan-out that scales the peak with -j
COMPILE={"build.workspace","build.dbi_release","build.sabre_release",
 "build.liteinst_runtime_release","build.manifest_guests","build.flaky_harnesses",
 "build.privileged_tests","lint.clippy","doc.doctests","doc.rustdoc",
 "test.hermit_unit","test.detcore_unit","test.detcore_misc","test.detcore_parallel",
 "test.regular_crates","test.strict_compat","test.rr_suite_contract","setup.nextest",
 "test.hermit_integration","test.arbitrary_binaries","test.cli","test.liteinst_strict",
 "test.sabre_examples","test.hermit_modes","test.app_strict_verify",
 "test.command_strict_verify","test.ignored_syscall_regressions","test.dbi_parity",
 "test.envelope_levels","cpuid.faulting","pmu.preemption"}

def h(b):
  if b is None: return "?"
  return f"{b/GiB:.2f}G" if b>=GiB else f"{b/MiB:.0f}M"

rows=[]
for f,lane in [("portable.json","PORT"),("privileged.json","PRIV")]:
  d=json.load(open(os.path.join(HERMIT,"ci/dag",f)))
  for s in d["steps"]:
    hint=s.get("hint",{}); nid=f'{s["group"]}.{s["job"]}'
    hard=hint.get("hard_mem_max_bytes"); base=hint.get("rss_baseline_bytes")
    m=MEAS.get(nid)
    peak,cond,src=(m if m else (None,"UNMEASURED",""))
    ratio=(peak/hard) if (peak and hard) else None
    rows.append((lane,nid,hint.get("classification"),hard,base,peak,ratio,
                 nid in COMPILE,cond,src))

def verdict(r):
  lane,nid,cls,hard,base,peak,ratio,comp,cond,src=r
  if src=="oom": return "OOM-BINDING (fired)"
  if ratio is None: return "UNMEASURED"
  if ratio>=0.85: return "BINDING (<15% headroom)"
  if ratio<0.25:  return "SLACK (>75% waste)"
  return "ok"

print(f"{'LANE':4} {'node':33} {'cls':13} {'hard':>6} {'base':>6} {'peak':>7} {'peak/hard':>9} {'C':1} verdict / condition")
print("-"*130)
nmeas=0; ncomp_unmeas=0
for r in sorted(rows,key=lambda r:(r[0], -(r[6] or -1))):
  lane,nid,cls,hard,base,peak,ratio,comp,cond,src=r
  if peak is not None: nmeas+=1
  if comp and peak is None: ncomp_unmeas+=1
  rr=f"{ratio:.2f}" if ratio is not None else "  -"
  print(f"{lane:4} {nid:33} {str(cls):13} {h(hard):>6} {h(base):>6} {h(peak):>7} {rr:>9} {'C' if comp else '.':1} {verdict(r)} [{cond}]")
print("-"*130)
tot=len(rows)
print(f"TOTAL nodes={tot}  measured={nmeas}  unmeasured={tot-nmeas}  compile-bearing-UNMEASURED={ncomp_unmeas} (the latent OOM chain)")
