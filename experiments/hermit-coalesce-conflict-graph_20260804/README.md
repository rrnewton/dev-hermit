# Hermit coalesce conflict graph — 2026-08-04

**Question.** When Reverie main goes final, hermit staging gets built ONCE. Which of the
open `rrnewton/hermit` PRs merge into staging **free** (the bulk win), which must land as a
**coherent stack**, and which **cannot merge at all** until rebased?

**Base:** `origin/main b384187efd725c504d69281f043d442325d4fcb2` · 73 open PRs · 71 target
`main` (2 are stacked on codex branches: #1585, #1451 — excluded from the graph).

## Method (analysis only — no merge, no validate, no reverie)

Two axes, deliberately kept separate (they answer different questions):

1. **Base-mergeability** — `git merge-tree --write-tree --name-only <main> <head>` per PR,
   then filter conflicting paths against the **glue set** (append-style manifests/lockfiles
   that resolve mechanically at the staging tip, not real blockers).
2. **Pairwise real-source conflict** — file-overlap union-find over **non-glue** files to
   find candidate edges (218), then verify **every** edge with `merge-tree` and keep only
   edges that conflict on a **non-glue path**. Connected components over the *verified* edges.

**Glue set** (excluded — append-merge at staging): `Cargo.lock`, `**/Cargo.toml`,
`tests/backend-parity/{matrix.tsv,README.md}`, `ci/expected-e2e-plan.json`,
`tests/e2e/manifests/**`, `docs/*COMPATIBILITY.md`, `.github/workflows/**`, `.gitignore`.

Why glue-filtering matters: naive file-overlap OR merge-tree-on-heads collapses everything
into one blob-of-~100 (hermit-243's earlier run) because every parity PR appends to
`matrix.tsv`/`test-files.json`/`Cargo.lock`. Those are the same file textually but resolve
once at the staging tip. Filtering them reveals the **real** structure. Cost: fetch 1.6s,
all merge-tree probes 14.9s — the deep path is cheap once fetched (confirms hermit-243).

## RESULT — the graph

**71 main-targeted PRs = 33 free singletons + Component A (23) + Component B (15).**
The two components share **no** real-source conflict file → they are **parallel lanes**.

### 1. FREE BULK — 33 PRs conflict with NOTHING on real source, all base clean/glue-only
These merge into staging with at most a one-time append-glue resolution. This is the win.

```
1213 1221 1229 1243 1244 1254 1275 1296 1303 1306 1308 1314 1316 1317 1318
1320 1323 1365 1380 1393 1397 1422 1470 1471 1491 1514 1532 1544 1551 1558
1579 1586 1588
```
(Includes #1544 — the "e9patch" PR that is actually a manifest-family PR, correctly free
here; do NOT fold it into the patching stack. Base mix: 25 CLEAN + glue-only remainder.)

### 2. COMPONENT B (15) — the `run_matrix.py` chain — trivial single-file stack
Bound by **exactly one** real file: `tests/backend-parity/run_matrix.py` (all 15 conflict
there). Land as one stack base→tip; a dropped member re-chains trivially (single file).
```
1227 1233 1235 1242 1245 1246 1247 1250 1252 1464 1472 1473 1477 1498 1590
```
14 of these are in the codex "43 reverie-disjoint" list — reverie-disjoint ≠ pairwise
independent (codex flagged this). 4 also source-conflict vs main: 1227 1464 1472 1473.
**rebases_avoided by stacking = 14.**

### 3. COMPONENT A (23) — core + CI-infra + patching entanglement — the hard cluster
Bound by a spread of load-bearing files: `detcore/src/lib.rs` (7), `ci/dag/portable.json`
(7), `validate.sh` (7), `hermit-cli/src/lib.rs` (5), `hermit-cli/tests/cli.rs` (4), plus
sabre/liteinst source. Contains **7 of the 8 patching PRs**.
```
1147 1200 1302 1381 1412 1430 1443 1445 1467 1468 1515 1543 1546 1547 1549
1552 1555 1559 1571 1576 1578 1587 1591
```
7 source-conflict vs main (need real rebase in-stack): 1147 1302 1381 1445 1467 1555 1571.
**rebases_avoided by stacking = 22.**

### CANNOT MERGE AT ALL (now) — 11 PRs source-conflict vs main
All 11 are **inside** the two components (7 in A: 1147 1302 1381 1445 1467 1555 1571;
4 in B: 1227 1464 1472 1473). **Zero** isolated-but-unmergeable PRs — every real blocker is
resolved during its component's stack rebase. Nothing is orphaned.

## PATCHING CLUSTER (separated per owner — land LAST)
Only **8** PRs touch patching-backend source; 7 live in Component A, so patching is *not* a
free-standing clique — it is entangled with core via `detcore-sabre/src/lib.rs`,
`detcore-dbi/src/lib.rs`, `hermit-cli/src/sabre_ptrace.rs`, `hermit-cli/tests/common/liteinst.rs`.

| PR | backend | base vs main | note |
|----|---------|--------------|------|
| #1147 | dbi | SOURCE-CONFLICT | detcore-dbi/src/lib.rs |
| #1302 | sabre | SOURCE-CONFLICT | detcore-sabre/src/lib.rs |
| #1381 | sabre | SOURCE-CONFLICT | detcore-sabre/src/lib.rs |
| #1467 | sabre | SOURCE-CONFLICT | detcore-sabre + sabre_ptrace.rs |
| #1468 | sabre | CLEAN | sabre_ptrace.rs (chained behind 1467) |
| #1443 | liteinst | GLUE-ONLY | liteinst_advanced.rs + runtime-build |
| #1576 | liteinst | GLUE-ONLY | liteinst_advanced.rs |
| #1591 | liteinst | CLEAN | liteinst.rs test helper + stage script |

**e9patch source share = 0** (confirmed: no `hermit-cli/src/e9patch.rs` / corpus edits in any
open PR). #1544 "e9patch" is a mislabeled manifest-family PR → it is in the FREE BULK, not here.
Sabre sub-chain within A: **1302 → 1381 → 1467 → 1468** (detcore-sabre/src/lib.rs +
sabre_ptrace.rs). This is the "compat expansion stopped pending architecture" lane.

## Staging build plan implied
1. Merge the **33 free singletons** + resolve append-glue once (parallel, no rebase chain).
2. Land **Component B** as a single-file `run_matrix.py` stack (14 rebases avoided).
3. Land **Component A** last as one stack (22 rebases avoided), with the patching sabre
   sub-chain at the tip — it is the architecture-blocked lane.

**Total rebases_avoided by stacking the two components = 36** (not 99 — that figure was the
glue-driven blob-of-100 artifact). The larger real win is the 33 free merges needing no chain.

Reproduce: `cluster.py` (overlap), `verify.py` (merge-tree base + pairwise),
`characterize.py` (binding files + patching membership). Inputs: `pr-snapshot.json`.
Output: `conflict-graph.json`.

## Emitted stack orders (base → tip)

Ordering heuristic: **CLEAN → GLUE-ONLY → SOURCE-CONFLICT**, tie-break by PR number. Risky
(source-conflict-vs-main) and droppable members sit at the **tip**, so dropping one only
re-chains what is above it — the clean base is never disturbed. The sabre sub-chain
(1302→1381→1467→1468, bound by detcore-sabre/src/lib.rs + sabre_ptrace.rs) is kept contiguous
at A's tip = the architecture-blocked patching lane, landed last.

### Component B (15) — single-file run_matrix.py stack
```
1590(CLEAN) 1233 1235 1242 1245 1246 1247 1250 1252 1477 1498(glue) | 1227 1464 1472 1473(src-conf)
```

### Component A (23)
```
1200 1412 1430 1515 1543 1546 1547 1549 1559 1578 1587 1591(CLEAN)
  1443 1552 1576(glue)
  1147 1445 1555 1571(src-conf)
  1302 1381 1467 1468(sabre sub-chain, tip, land LAST)
```

## End-to-end staging simulation (cumulative)

Sequentially merged all 71 heads in the proposed staging order into a growing synthetic
staging commit chain (read-only: `git merge-tree` + `commit-tree`, no refs/worktree; final
tree ae97012). CONFIRMS the graph end-to-end (not just pairwise):
- **Steps 1-35 (all 33 FREE + first 2 Comp-B): ZERO real conflicts** -> free bulk coalesces
  cumulatively; only append-glue touched.
- **Comp-B: 13 real conflicts, EVERY one on `tests/backend-parity/run_matrix.py`** -> single-file stack.
- **Comp-A: 11 real conflicts** on detcore/src/lib.rs, ci/dag/{portable,privileged}.json,
  hermit-cli/src/lib.rs, sabre_ptrace.rs, liteinst.rs (sabre sub-chain conflicts at the tip).
- **All 24 real conflicts are INSIDE the two stacks; none in the free phase** = the graph is correct.
