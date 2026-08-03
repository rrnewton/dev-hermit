#!/usr/bin/env python3
"""TRUE-dependency audit of the portable CI DAG.

Reads the SHIPPED DAG (hermit/ci/dag/portable.json) for declared deps + resource
tags, overlays MEASURED warm durations (owner green-run a034f39c, measured_
portable_nodes.tsv) and the hint est_duration_s as a COLD proxy, then compares
the AS-DECLARED dependency graph against a TRUE-DEP graph where edges that are
ordering/serialization choices (not data dependencies) are pruned.

Question answered: do the declared edges lengthen the achievable makespan beyond
what real data dependencies require? Prints critical path + list-scheduler
makespan (deps + resource_caps + -j) for both graphs, warm and cold.
"""
import json, os, heapq

HERE = os.path.dirname(os.path.abspath(__file__))
DAG = os.path.join(HERE, "..", "..", "hermit", "ci", "dag", "portable.json")

# ---- load shipped DAG -------------------------------------------------------
raw = json.load(open(DAG))
CAPS_SHIPPED = raw["resource_caps"]           # {"hermit_guest":1,"manifest_guest":4}
nodes = {}
for s in raw["steps"]:
    tag = f'{s["group"]}.{s["job"]}'
    res = (s.get("hint", {}) or {}).get("resources", {}) or {}
    # top-level manifest nodes declare their manifest_guest via hint.resources;
    # a couple of build nodes carry no resource tag.
    nodes[tag] = {
        "deps": list(s.get("deps", [])),
        "res": dict(res),
        "est": float((s.get("hint", {}) or {}).get("est_duration_s", 0)),
        "cls": (s.get("hint", {}) or {}).get("classification", "?"),
    }

# ---- overlay measured warm durations ---------------------------------------
measured = {}
for line in open(os.path.join(HERE, "measured_portable_nodes.tsv")):
    line = line.rstrip("\n")
    if not line:
        continue
    dur, tag, _desc = line.split("\t", 2)
    measured[tag] = float(dur)
for tag, n in nodes.items():
    n["warm"] = measured.get(tag, 0.0)
    n["cold"] = n["est"]  # est_duration_s is the pessimistic (~cold) proxy

# ---- TRUE-dep graph: prune edges that are ordering, not data ----------------
# Confidence-tagged. Only DATA deps are kept; ordering/quality-gate edges pruned.
PRUNE = {
    # build.workspace gated on e2e.metadata: compiling the workspace does NOT
    # consume E2E-metadata validation. Pure ordering. (HIGH confidence)
    "build.workspace": [],
    # strict_compat runs ./validate.sh --portable-strict-compat-only, which needs
    # the built hermit binary but NOT clippy/doctests/rustdoc/regular_crates/
    # flaky_harnesses/hermit_unit/detcore_unit/rr_suite_contract passing. Those 7
    # are "gate the big blocking matrix behind the cheap checks" ordering.
    # TRUE data dep = build.workspace only. (HIGH ordering / MED that nothing else
    # is needed — flagged to verify the binary is prebuilt, not rebuilt in-gate.)
    "test.strict_compat": ["build.workspace"],
}
def true_deps(tag):
    return PRUNE.get(tag, nodes[tag]["deps"])

# ---- critical path (longest chain by duration) -----------------------------
def critical_path(dur_key, deps_fn):
    memo = {}
    def f(t):
        if t in memo: return memo[t]
        best = (0.0, [])
        for d in deps_fn(t):
            l, p = f(d)
            if l > best[0]: best = (l, p)
        memo[t] = (best[0] + nodes[t][dur_key], best[1] + [t])
        return memo[t]
    return max((f(t) for t in nodes), key=lambda x: x[0])

# ---- list scheduler: deps + resource caps + J workers ----------------------
def simulate(dur_key, deps_fn, J, caps):
    running = []                      # heap of (finish, tag)
    done = set(); t = 0.0; free = J
    used = {k: 0 for k in caps}
    remaining = set(nodes)
    indeg = {tag: set(deps_fn(tag)) for tag in nodes}
    def ready():
        run_tags = {r[1] for r in running}
        return [x for x in remaining if x not in run_tags and indeg[x] <= done]
    # Run until every node is BOTH dispatched and finished. The old `while
    # remaining` exited as soon as the last node was dispatched, leaving in-flight
    # jobs (incl. the 175s strict_compat tail) undrained and undercounting makespan.
    while remaining or running:
        progressed = True
        while progressed and remaining:
            progressed = False
            for x in sorted(ready(), key=lambda x: -nodes[x][dur_key]):
                if free <= 0: break
                r = nodes[x]["res"]
                if any(used.get(k, 0) + v > caps.get(k, 1 << 30) for k, v in r.items()):
                    continue
                free -= 1
                for k, v in r.items(): used[k] = used.get(k, 0) + v
                heapq.heappush(running, (t + nodes[x][dur_key], x))
                remaining.discard(x)
                progressed = True
        if not running: break        # resource-starved deadlock guard
        ft, x = heapq.heappop(running)
        t = ft; done.add(x); free += 1
        for k, v in nodes[x]["res"].items(): used[k] -= v
    return t

def report(dur_key, label):
    total = sum(n[dur_key] for n in nodes.values())
    cl_d, cp_d = critical_path(dur_key, lambda t: nodes[t]["deps"])
    cl_t, cp_t = critical_path(dur_key, true_deps)
    print(f"\n===== {label} durations  (total work {total:.0f}s, {len(nodes)} nodes) =====")
    print(f"  dep-only critical path  AS-DECLARED : {cl_d:6.0f}s  {' -> '.join(cp_d)}")
    print(f"  dep-only critical path  TRUE-DEPS   : {cl_t:6.0f}s  {' -> '.join(cp_t)}")
    print(f"  theoretical max speedup (TRUE-DEPS, infinite workers, no caps): {total/cl_t:.2f}x")
    print(f"  makespan (list-sched, caps + -j):")
    for hg in [1, 2, 4, 8]:
        caps = {"hermit_guest": hg, "manifest_guest": 4}
        d = simulate(dur_key, lambda t: nodes[t]["deps"], 16, caps)
        tt = simulate(dur_key, true_deps, 16, caps)
        print(f"    hermit_guest={hg} j16 : as-declared={d:6.0f}s  true-deps={tt:6.0f}s  gain={d-tt:5.0f}s")

report("warm", "WARM (measured, owner a034f39c)")
report("cold", "COLD (hint est_duration_s proxy)")

# hermit_guest serial floor (why cap=1 == the wall)
hg_sum = sum(n["warm"] for n in nodes.values() if "hermit_guest" in n["res"])
print(f"\nhermit_guest warm serial sum (the cap=1 floor): {hg_sum:.0f}s over "
      f'{sum(1 for n in nodes.values() if "hermit_guest" in n["res"])} nodes')
print(f"single largest hermit_guest node (the hard floor): test.strict_compat = "
      f'{nodes["test.strict_compat"]["warm"]:.0f}s warm / {nodes["test.strict_compat"]["cold"]:.0f}s cold')
