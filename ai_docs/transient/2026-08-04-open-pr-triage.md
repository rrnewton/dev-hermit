# Open PR triage — rrnewton/hermit + rrnewton/reverie

**Captured 2026-08-04 ~11:57–11:58 PDT** by the coordinator identity into
`scratch/pr-status.json`, `scratch/hermit-prs.json`, `scratch/reverie-prs.json`; grouped here
offline by `pr-surveyor-3` (agent `claude_code` has no GitHub egress — see task notes
2026-08-04T18:01/18:08/18:26/18:56). Every number below is derived from those three files by
script, not hand-written. `available=True` on both repos, so these are results, not the
no-result the blocked agent saw.

## Headline

**The landing drain is EMPTY: 0 free-to-land PRs across both repos.** Not one open PR is
green. There is nothing for the lander to pick up, and no new PR should be opened ahead of
clearing this.

| | hermit | reverie |
|---|---|---|
| total open | 74 | 15 |
| ready (non-draft) | 41 | 5 |
| **FREE-TO-LAND (green + non-draft)** | **0** | **0** |
| CI-failing — real-red | 14 | 4 |
| CI-failing — stale-base | 26 | 0 |
| pending | 1 | 1 |
| drafts | 33 | 10 |
| `post-facto-human-review` (informational) | 12 (7 ready) | 6 (1 ready) |
| `outage_suspected` | False | **True** |

Three things the raw counts do not say:

1. **hermit's dominant blocker is not test failures, it is rebase debt.** 26 of 40 reds are
   `stale-base`/`DIRTY` — the base is conflicted, so CI red is a *consequence*, not a product
   defect. Re-running CI on these cannot turn them green; they need rebasing. Only 14 are
   genuine `real-red`, and every one of those is `merge_state=BLOCKED` (mergeable, blocked on
   checks).
2. **reverie's 4 real-reds are NOT established product failures.** The tool set
   `outage_suspected=True` for reverie (0 green out of 5 ready). That is the classifier saying
   the reds may share an infrastructure cause. Treat reverie red attribution as unresolved
   until one PR is diagnosed; do not file 4 product bugs off this.
3. **Both "pending" PRs are also `mergeable=CONFLICTING`.** hermit #1576 and reverie #222 are
   pending *and* dirty — neither becomes landable when CI finishes.

## Pre-anchor status (bfb0a9ef) — MOSTLY UNDETERMINABLE OFFLINE, stated as such

The deliverable asked for a live per-head anchor re-derivation. I ran
`git merge-base --is-ancestor bfb0a9ef1c303d1977f5f02903b70cc93e514cb5 <headRefOid>` locally
against all 41 hermit ready heads. **Only 1 of 41 head objects exists in the local clone**
(branch refs are stale to 2026-08-02 and I am instructed to make no network call), so:

- **determinable: 1/41** — #1147 `683fb5ca25b6b4af2391c634a01f5245349a46ad` = **PRE-ANCHOR**.
- **undeterminable offline: 40/41** — marked `OBJECT-ABSENT-LOCALLY` in the tables. This is a
  missing measurement, **not** a pass. Do not read it as anchored.

To complete it, someone with egress runs `git -C hermit fetch origin` (the current
`.git/FETCH_HEAD` is a main-only fetch) and re-runs the same one-liner, or
`ci-hub/validate/preflight_anchor.py --head <sha>` per head (exit 2 = REFUSE).

**Useful correction to the stale list in AGENTS.md:** of its 23 hard-coded pre-anchor PR
numbers, **19 are still open and ready** (1227, 1229, 1233, 1235, 1246, 1247, 1250, 1252,
1254, 1393, 1397, 1445, 1464, 1472, 1473, 1477, 1549, 1552, 1558) and **4 are no longer open**
(1242, 1244, 1245, 1296). The AGENTS.md instruction to re-derive rather than trust the list is
correct — the list has already drifted.

## Role tags

The deliverable asked for a role tag per PR. **Not available in the captured data** — role tags
live in PR *descriptions*, and the three files carry only number/title/head/labels/draft/ci.
Reporting the field as absent rather than inferring it from branch names.


## rrnewton/hermit

`available=True` `open(ready)=41` `outage_suspected=False` — total open incl. drafts: **74** (41 ready + 33 draft)

### FREE-TO-LAND (ci green AND not draft): **0**

**(none)** — zero green ready PRs in this repo. Nothing is free to land.

### CI-FAILING: **40** (real-red 14 + stale-base 26)

**real-red (14)** — genuine failing checks; `merge_state`: ['BLOCKED']

| PR | head SHA | anchor vs bfb0a9ef | merge_state | title |
|---|---|---|---|---|
| #1200 | `a03a8761e071bfc4ad8349e50979a53d878d4c86` | OBJECT-ABSENT-LOCALLY | BLOCKED | Defer racy run-queue admissions to a deterministic drain point |
| #1213 | `8a6fd5933178de1289375cf28d7d6e796eda6eae` | OBJECT-ABSENT-LOCALLY | BLOCKED | Virtualize timerfd against virtual time (determinize GHC RTS ticker; stock parallel GHC reproducible) |
| #1246 | `b6b3a26fd9a587a2f5bb90c2a4d9db4baee345a9` | OBJECT-ABSENT-LOCALLY | BLOCKED | Add append_pwrite positional-write/O_APPEND parity contract |
| #1397 | `d344a5ea5b400790ce66103eb954072a3eaf32cb` | OBJECT-ABSENT-LOCALLY | BLOCKED | Preserve LiteInst arch-prctl GS state |
| #1412 | `1c170f12816238e38fce4daa807628adab235777` | OBJECT-ABSENT-LOCALLY | BLOCKED | Add lazy common backend statistics reporting |
| #1430 | `e856c479577372c395e1e62ebb1bfa30e70da070` | OBJECT-ABSENT-LOCALLY | BLOCKED | detcore: hash guest environment into DETLOG at exec for cross-backend diffing |
| #1445 | `0ed6bbbbe7215959237f6372d6ec5ee8d26c8119` | OBJECT-ABSENT-LOCALLY | BLOCKED | detcore: determinize terminal geometry/attributes (fixed winsize for TCGETS/TIOCGWINSZ) |
| #1468 | `da72794a54b06999cb0e40fc82d412c81f708f6d` | OBJECT-ABSENT-LOCALLY | BLOCKED | Ratchet SaBRe non-gated L2 support accounting |
| #1470 | `c6f7e9eb1419a9d68dcb1dd9f6944cd9375ec30f` | OBJECT-ABSENT-LOCALLY | BLOCKED | Add prctl_identity backend-parity contract (ptrace/DBI pass, KVM gap) |
| #1471 | `b56d4b412d213269d643cab9decdde263b369015` | OBJECT-ABSENT-LOCALLY | BLOCKED | Add rlimit_identity backend-parity contract (ptrace/DBI pass; KVM gap) |
| #1514 | `412e6e62add0d28f5cc50befb55955c3a8ff5ace` | OBJECT-ABSENT-LOCALLY | BLOCKED | scripts: bust rust-script caches when the shared prelude changes |
| #1549 | `caace5a5e590d8032023b25d6bae3adfd8c6c571` | OBJECT-ABSENT-LOCALLY | BLOCKED | detcore: determinize credential-query syscalls (getuid family) for backend-independent virtual-root identity |
| #1558 | `4a9c415166e6e327c8c81454b7238e98f3dffef0` | OBJECT-ABSENT-LOCALLY | BLOCKED | Determinize sysfs module reference counts |
| #1595 | `310a3689575ab9cd33c38fea9fa40935543f1742` | OBJECT-ABSENT-LOCALLY | BLOCKED | verify: expose verification verdict independent of guest exit code |

**stale-base (26)** — red is a consequence of a stale/conflicted base, NOT a product failure; `merge_state`: ['DIRTY']

| PR | head SHA | anchor vs bfb0a9ef | mergeable | title |
|---|---|---|---|---|
| #1147 | `683fb5ca25b6b4af2391c634a01f5245349a46ad` | PRE-ANCHOR | CONFLICTING | dbi: land shared coordinator and robust-list parity |
| #1221 | `4df1b2bd1577fdcdecd0490fba5674541b7b3cfc` | OBJECT-ABSENT-LOCALLY | CONFLICTING | tests(backend-parity): add multiprocess fork+exec parity contract |
| #1227 | `781a5f752f04ac4ed9107716b5c4ea2dc72f0441` | OBJECT-ABSENT-LOCALLY | CONFLICTING | backend-parity: add pipe_ipc cross-process pipe contract (DBI 22/23->23/24) |
| #1229 | `5b735eec7fdc64f6b2e247a6e66c2c40143312d8` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add python-dict-hash-iteration e2e determinism test |
| #1233 | `eef9650f18f8d509005e8d56ec537b5d3d38f707` | OBJECT-ABSENT-LOCALLY | CONFLICTING | backend-parity: add vectored_io scatter/gather contract (triple pass, DBI/KVM 22/23->23/24) |
| #1235 | `7de6c37fbbdcc03b1e1d78f4f60b086201e0f33f` | OBJECT-ABSENT-LOCALLY | CONFLICTING | backend-parity: add eventfd_semantics contract (triple pass, DBI/KVM 22/23->23/24) |
| #1243 | `8559279317ac652b03c9309e4707de8fcef66608` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Enable SaBRe arch_prctl strict verification |
| #1247 | `36ca9169415e439342c964793ee6f44e8aa9a9c5` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add ftruncate_sparse sparse-hole/shrink truncation parity contract |
| #1250 | `fd2b53d0c49f3f527c2d6e350ccb6735e81fed58` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add vectored_file_io regular-file scatter/gather parity contract |
| #1252 | `2b26837f75219edc1a827f047d28bba1ffaa4bd2` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add openat_flags open-flag semantics parity contract |
| #1254 | `bd223218a0dc897a9bbc92fa68ab9db4a0538b88` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add language-runtimes/tcl-rand-clock e2e determinism test |
| #1275 | `5d388b72a398598317cfc38d1b3adef8b17141a7` | OBJECT-ABSENT-LOCALLY | CONFLICTING | e2e: ratchet SaBRe file-state coverage |
| #1365 | `7f07fcb1dcfdb66cd3f9b4579703a209af057d68` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add fchmodat2 flags-argument backend-parity contract |
| #1380 | `863cc621a1e0062439b5062e82fda75a6864e1ef` | OBJECT-ABSENT-LOCALLY | CONFLICTING | [impl agent, opus-4.8] backend-parity: socketpair_flags contract (KVM getsockopt gap) |
| #1393 | `d7177c7c02206d1996e56a114bcb7db200931488` | OBJECT-ABSENT-LOCALLY | CONFLICTING | backend-parity: pidfd_open_self cross-backend contract (pass/pass/pass L1, detlog/detlog/guest L2) |
| #1443 | `90f263bbe65c45bc984fdbd2b97ab26b8e32ebb6` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Use the quiescent in-guest LiteInst fast path |
| #1464 | `6dc88098980e21cf98079d11e022206d871a6d23` | OBJECT-ABSENT-LOCALLY | CONFLICTING | tests: add cpu-virtualization backend-parity contract |
| #1467 | `05f7146a8d200002e64eecd45191c5b89a717bf5` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Report SaBRe patch and slow-path statistics |
| #1472 | `d123ed123caeee412444513e7fa9298e55e93a64` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add getcpu_identity backend-parity contract fixture |
| #1473 | `a10efa2f1dc9f16a18255c4e2a70d95556a86352` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Add sched_getaffinity_identity backend-parity contract fixture |
| #1477 | `1f97b86615276c12743d30c44bce7e534ade0128` | OBJECT-ABSENT-LOCALLY | CONFLICTING | backend-parity: consolidated file-fd family (fd_duplication + dup_shared_offset + lseek_positioning) |
| #1498 | `83dbe44103e475f0f3ba140fd799dc5498a5341b` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Relocate backend parity tracking to outer scorecard |
| #1532 | `affa6e573de49e4e71baf5f46af735289a27cabd` | OBJECT-ABSENT-LOCALLY | CONFLICTING | ci(demo-gate): stop triggering demo runs for test-metadata-only PRs (follow-up) |
| #1552 | `42735278dba53f13a258746648d5d4b2e278f144` | OBJECT-ABSENT-LOCALLY | CONFLICTING | ci: extract select-tests preflight + backend build map into policy data |
| #1555 | `fe86e4a6c9c050badf2d923c29e82556602ed13d` | OBJECT-ABSENT-LOCALLY | CONFLICTING | ci/dag: declare cpu_timeout on all DAG nodes (generous runaway-catcher) |
| #1591 | `b6710caa1406fa1b0f8660318675eb9bb68032f1` | OBJECT-ABSENT-LOCALLY | CONFLICTING | Enforce latest Reverie main in every testing path |

### PENDING: **1**

- #1576 `b15a29daee9943ed75eae988945052829a93b33a` — ci=pending, merge_state=DIRTY, mergeable=**CONFLICTING** — detcore+tests: one skid-overshoot marker and structural skid-gated retry

### DRAFTS: **33** (not landable; excluded from the ready buckets)

#1302, #1303, #1306, #1308, #1314, #1316, #1317, #1318, #1320, #1323, #1381, #1422, #1451, #1491, #1515, #1543, #1544, #1546, #1547, #1551, #1559, #1568, #1585, #1586, #1587, #1588, #1590, #1593, #1594, #1596, #1598, #1603, #1604

### `post-facto-human-review` label set: **12** (7 ready, 5 draft)

All: #1147, #1200, #1213, #1302, #1397, #1443, #1445, #1451, #1576, #1588, #1594, #1598

Ready subset: #1147, #1200, #1213, #1397, #1443, #1445, #1576

**This label is INFORMATIONAL and NEVER a landing blocker** (AGENTS.md:411) — it marks a change a human reviews *after* it lands. It is not a hold. Zero PRs in either repo carry the obsolete `human-review` label (verified).

Adversarial-review protocol state for those 12 PRs: **complete = 0/12**. Ready ones and what they still owe:

| PR | review_rounds | current_approvals | missing |
|---|---|---|---|
| #1576 | partial | missing | current-approval-codex, review-round-claude, current-approval-claude |
| #1445 | missing | missing | review-round-codex, current-approval-codex, review-round-claude, current-approval-claude |
| #1443 | complete | missing | current-approval-codex, current-approval-claude |
| #1397 | missing | missing | review-round-codex, current-approval-codex, review-round-claude, current-approval-claude |
| #1213 | complete | missing | current-approval-codex, current-approval-claude |
| #1200 | complete | missing | current-approval-codex, current-approval-claude |
| #1147 | complete | missing | current-approval-codex, current-approval-claude |


## rrnewton/reverie

`available=True` `open(ready)=5` `outage_suspected=True` — total open incl. drafts: **15** (5 ready + 10 draft)

### FREE-TO-LAND (ci green AND not draft): **0**

**(none)** — zero green ready PRs in this repo. Nothing is free to land.

### CI-FAILING: **4** (real-red 4 + stale-base 0)

**real-red (4)** — genuine failing checks; `merge_state`: ['BEHIND', 'CLEAN']

| PR | head SHA | anchor vs bfb0a9ef | merge_state | title |
|---|---|---|---|---|
| #221 | `6527f60d1442d8922d4f45c495e2d2e5e45a86f1` | n/a (hermit anchor) | BEHIND | kvm: advance direct worker logical clocks |
| #359 | `d3c60dc5d6b16fd1a5925aaa1ff9383dadd69302` | n/a (hermit anchor) | BEHIND | Package third-party backend payloads from pinned source |
| #366 | `8254c8c572845d4bbe58fb5595a193a837e2ca14` | n/a (hermit anchor) | BEHIND | reverie-ptrace: strengthen skid-overshoot witness test (behaviour, not arithmetic) + fix collapsible_if |
| #369 | `975c9fa8c432fefb6c7ae88b27ef97685fd94f50` | n/a (hermit anchor) | CLEAN | reverie-liteinst: trim per-hop coordinator RPC syscalls |

**stale-base (0)** — red is a consequence of a stale/conflicted base, NOT a product failure

*(none)*

### PENDING: **1**

- #222 `180e0285d627ee97ef0c5c36012ae5be374c713d` — ci=pending, merge_state=DIRTY, mergeable=**CONFLICTING** — kvm: emulate timestamp counters deterministically

### DRAFTS: **10** (not landable; excluded from the ready buckets)

#312, #313, #335, #338, #341, #343, #346, #352, #367, #368

### `post-facto-human-review` label set: **6** (1 ready, 5 draft)

All: #221, #312, #313, #335, #352, #367

Ready subset: #221

**This label is INFORMATIONAL and NEVER a landing blocker** (AGENTS.md:411) — it marks a change a human reviews *after* it lands. It is not a hold. Zero PRs in either repo carry the obsolete `human-review` label (verified).

Adversarial-review protocol state for those 6 PRs: **complete = 0/6**. Ready ones and what they still owe:

| PR | review_rounds | current_approvals | missing |
|---|---|---|---|
| #221 | missing | missing | review-round-codex, current-approval-codex, review-round-claude, current-approval-claude |


