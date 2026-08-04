# backend-parity matrix.tsv conflict-cluster consolidation plan

Task: `matrix-tsv-schema-consolidation` (P1). Owner directive: PREP/PLAN only —
do not land yet (main-green full-validate pending). Coordinate with
hermit-lander. Date: 2026-08-02.

## TL;DR

- **No open PR carries a pending schema migration.** No open PR modifies the
  `matrix.tsv` header row. The "611-col schema migration" in the task title is
  the **6→11-column** migration (L2 ratchet columns), which **already landed on
  main** (current blob `56e8441a52f6a2b8bbc97a7e954cc3dfdc7cfca8`, 11 columns).
- **Canonical schema (already on main):**
  `test_name  ptrace  dbi  kvm  dbi_reason  kvm_reason  ptrace_l2  dbi_l2  kvm_l2  dbi_l2_reason  kvm_l2_reason`
- **Real root cause of the ~100 red PRs:** 91 of them branched *before* the
  L2-column migration (6-col base blob `1113d25b6`) and every PR appends its
  contribution to the **same 4 shared append-point files**. So they are
  doubly-conflicted: structural (6-col rows vs the 11-col schema) + positional
  (same insertion region). It is a serialization problem, not a semantic one.
- **Fix shape:** deterministic **UNION consolidation landed in family waves**,
  validated once per wave by `run_matrix.py`. NOT a 100-deep serial rebase.

## Cluster census (101 PRs, measured 2026-08-02)

Base-schema split (by `matrix.tsv` pre-image blob):

| base blob | schema | count | rebase cost |
| --- | --- | ---: | --- |
| `1113d25b6` | **OLD 6-col** (L1 only) | 91 | structural upgrade 6→11 + positional |
| `302f5e4c6` | 11-col, behind main | 3 | positional only (#1464 #1461 #1393) |
| `56e8441a5` | **11-col = current main** | 7 | positional only (#1470–1475, #1477) |

Every PR adds exactly **1** matrix row except **#1477** which adds **3**
(fd_duplication + dup_shared_offset + lseek_positioning) — the existing
consolidation pilot proving the union approach for one family.

Conflict surface per PR (4 shared files + 1 unique):

1. `tests/backend-parity/matrix.tsv` — append 1 row, keyed by unique `test_name`.
2. `tests/backend-parity/run_matrix.py` — 2 dict insertions: `sources`
   (fixture→source+cflags) and `cases` (test→cmd,exit,expected-stdout), both
   keyed by unique name.
3. `tests/backend-parity/README.md` — 1 prose row + the summary count tables.
4. `tests/e2e/manifests/inventory/test-files.json` — inventory entry for the
   fixture (required or the `e2e.metadata` gate goes RED — see memory
   `backend-parity-fixture-needs-e2e-inventory-entry`); not all PRs include it.
5. `tests/backend-parity/fixtures/<name>.c` — **NEW unique file, never conflicts.**

**Key property:** every contribution is *additively disjoint and keyed*
(unique test_name, unique dict keys, unique fixture filename). The git conflicts
are purely textual/positional. Therefore the merge of N PRs is a **deterministic
UNION**, computable without manual conflict resolution.

Family grouping (for wave batching):

| family | count | PRs (newest→oldest) |
| --- | ---: | --- |
| file/fd | 53 | 1477,1387,1379,1378,1376,1365,1363,1360,1356,1353,1346,1342,1328,1326,1318,1317,1308,1306,1303,1301,1299,1289,1286,1282,1280,1279,1277,1274,1272,1270,1268,1265,1264,1263,1262,1261,1259,1258,1257,1255,1253,1252,1250,1249,1248,1247,1246,1245,1242,1239,1235,1233,1227 |
| sched/proc | 25 | 1475,1474,1473,1472,1471,1470,1393,1388,1370,1358,1352,1351,1349,1348,1347,1337,1336,1332,1324,1323,1316,1314,1311,1284,1221 |
| security/refuse | 6 | 1350,1343,1339,1334,1331,1330 |
| socket/net | 5 | 1385,1384,1383,1382,1380 |
| mem | 5 | 1374,1340,1321,1320,1312 |
| signal | 4 | 1461,1297,1295,1292 |
| other | 3 | 1464,1355,1260 |

## Why land-once beats mass-rebase-the-100

- **Serial rebase = O(100) CI rounds.** All PRs touch the same append region, so
  each rebase invalidates the next; wall-clock is weeks and each of the 91
  old-base PRs also needs a hand 6→11-col upgrade. Non-starter.
- **Union consolidation = O(#waves) CI rounds (~7).** The union is mechanical
  because contributions are disjoint+keyed. One validation sweep per wave
  re-establishes each contract's *true* status at HEAD instead of trusting each
  PR's stale self-report.

## Plan: canonical-schema union consolidation, in family waves

### Phase 0 — canonical schema (DONE, verify only)
The 11-col L1+L2 schema is already on main. No schema PR to land. Record it as
the canonical target. (This corrects the task premise.)

### Phase 1 — build the deterministic union tool
A `dev-hermit` script (rust-script per hermit convention, kept in
`experiments/` — it is tooling, not product) that, given a list of PR head SHAs:
1. Collects each PR's added `fixtures/*.c` (disjoint copy).
2. Extracts each added `matrix.tsv` row; **upgrades 6-col rows to 11-col** by
   appending the 5 L2 fields. L2 status is NOT fabricated — set to a sentinel
   (`ptrace_l2=pending dbi_l2=pending kvm_l2=pending`) that Phase 2 overwrites
   with measured results. (Alternatively drop L2 to `gap` + "L2 unassessed"
   reason and let a follow-up ratchet fill it — decision for lander/owner.)
3. Unions the `run_matrix.py` `sources` + `cases` dict entries.
4. Unions the `test-files.json` inventory entries.
5. Re-sorts `matrix.tsv` data rows deterministically and regenerates the
   README count tables from `matrix.tsv` (single source of truth).
6. Emits one consolidation branch per wave off current `origin/main`.

### Phase 2 — validate each wave
Run `run_matrix.py` (L1 ×3 byte-identical + L2 `--verify`) across ptrace/DBI/KVM
on the union branch. This is the real gate: it writes each row's *measured*
status. A fixture whose contract fails is either (a) marked `gap` with a real
reason, or (b) held back to its own follow-up PR — it must not turn the wave red.
Respect the portable-CI DEBUG-binary ≤120s/test ceiling (memory
`e2e-portable-ci-debug-binary-speed-ceiling`); if a wave's matrix run exceeds CI
budget, split the wave.

### Phase 3 — land one PR per wave (hermit-lander)
Order waves smallest-first to derisk the tool: signal(4) → mem(5) →
socket/net(5) → security/refuse(6) → other(3) → sched/proc(25) → file/fd(53).
The 53-PR file/fd wave likely splits into ~3 sub-waves of ~18 to stay under CI
budget. Total ≈ 7–10 landings replacing ~100 conflicts.
The 10 already-on-11-col PRs (#1464,1461,1393 + #1470–1475,1477) can go in the
first wave of their family as a rebase-free warm-up.

### Phase 4 — close superseded PRs (coordinator, post-land)
After each wave lands, close its member PRs with a note pointing to the
consolidation PR + the exact fixture/row that carried over. Any contract that
failed Phase-2 validation keeps its PR open (status `in_progress`) for a real
fix — it was never actually green.

## Division of labor / coordination with hermit-lander

- **This task (`matrix-tsv-schema-consolidation`)** owns: the union tool, wave
  construction, and Phase-2 validation in a worktree slot; produces
  consolidation branch SHAs + a per-wave manifest of superseded PRs.
- **hermit-lander** owns: merge-to-main + authoritative CI gate at each
  consolidation PR head, and closing the superseded member PRs after land.
- **Prerequisite gate (owner):** do not land until main is green on the full
  validate sweep (owner directive). Build + validate may proceed now.

## Answers to the task's explicit questions

- **Which PR carries the canonical schema?** None — it already landed; main is
  canonical (11-col, blob `56e8441a5`). No open PR modifies the header.
- **Consolidation approach:** deterministic union in ~7–10 family waves, each
  validated once by `run_matrix.py`, replacing the ~100-deep conflict cluster.
