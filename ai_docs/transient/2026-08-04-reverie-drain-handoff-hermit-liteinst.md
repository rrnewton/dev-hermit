# HANDOFF @2026-08-04T09:56Z (hermit-liteinst) — reverie PR drain duty
# (supersedes the @09:49Z body below; deltas here at TOP)

## DELTA @09:56Z — NEW BLOCKER on remaining 4 (UNRESOLVED, needs successor)
- **2 LANDED total: #358 (d5b95fea), #344 (e0db0812). Both ancestry-verified.**
- #350, #361, #363, #347: ALL now ci.yml `completed:success` + merge-gate `completed/success` + up-to-date (main static at e0db0812) + auto-merge armed --rebase — **yet BLOCKED and NOT merging.**
- Direct `gh pr merge 350 --rebase` → **"base branch policy prohibits the merge"** (suggests --auto/--admin). NOT the up-to-date error, NOT merge-gate (that's green). This is a DIFFERENT policy block than what #358/#344 passed through.
- HYPOTHESIS (unproven): the `update-branch` (merge method) I used added a MERGE COMMIT to these branches; a repo rule (linear-history? rebase-clean?) may prohibit rebase-merge of a branch containing merge commits. #344 went through same path though — so uncertain. Could also be a required review/ruleset that #344 happened to satisfy.
- **NEXT STEP TO TRY:** (a) check `gh api repos/rrnewton/reverie/rulesets` + each ruleset's rules for review/linear-history requirements; (b) if merge-commit is the problem, do a LOCAL rebase (like #350 recipe below) instead of update-branch — force-push a linear single/few-commit branch, then merge; (c) just wait longer — auto-merge may fire (armed). Heads at 09:56Z: #350 f174f7d5, #361 19483fa3, #363 f15cc2f8, #347 fcbab783.

## Current state (LANDED so far this session)
- **#358 LANDED** — mergeCommit `d5b95fea4da7d42c88a9b69830775dc79db3b88a`, ancestry rc=0 on origin/main. (docs: LiteInst Mode A/B)
- **#344 LANDED** — mergeCommit `e0db081208878b8dd5777dffc6907d73645494a2`, ancestry rc=0. (reverie-kvm: determinize seccomp(2)→EOPNOTSUPP; carries `post-facto-human-review` label, non-blocking)

## IN FLIGHT (auto-merge ARMED --rebase on all 4; re-updated to main ~09:48Z; CI running)
- **#350** codex/kvm-tcp-info-canonicalize — PRIORITY. Rebased locally (see below), CI green at prior head 1db6ed72. Re-updated 09:48Z; awaiting new CI + merge-gate refire.
- **#361** codex/force-skid-margin-override (ptrace REVERIE_SKID_MARGIN_OVERRIDE)
- **#363** codex/reverie-perf-rdpmc-read-primitive (perf rdpmc read primitive)
- **#347** codex/kvm-unix-seqpacket-socket (AF_UNIX SOCK_SEQPACKET)

## MECHANISM (ESTABLISHED — measured, this is the winning recipe)
reverie main: strict protection (up-to-date REQUIRED), ONLY required check = `merge-gate`, **merge queue is NOT enabled** (`mergeQueue: null` despite merge_group trigger in workflow). authoritative gates `Regular tests`+`Host-dependent tests` both feed ci.yml.
- merge-gate passes iff ci.yml `completed:success` at exact head OR PR has `locally-validated` label.
- merge-gate goes STALE (fires at 4s on open/label before CI done). **Refire with:** `gh workflow run merge-gate.yml --repo rrnewton/reverie --ref <headbranch> -f pr_number=<n>` — refire ONCE only (redundant refire re-queues → re-BLOCKS).
- **LANDING RECIPE per PR:** (1) `gh pr ready <n>` if draft; (2) `gh pr merge <n> --rebase --auto`; (3) `gh api --method PUT repos/rrnewton/reverie/pulls/<n>/update-branch` (merges main in, triggers fresh CI ~5min); (4) wait ci.yml `completed:success` at new head; (5) refire merge-gate ONCE; (6) auto-merge fires within seconds → merged.
- verify landed: `git merge-base --is-ancestor <mergeCommit.oid> origin/main` (NOT the MERGED flag).

## TRAP PAID FOR (do not repeat)
- **THE RACE:** main churns from other landers every ~4min (< CI's 5min). Each landing re-BEHINDs all other armed PRs; repo does NOT auto-update behind branches. So **only ~1 PR lands per CI cycle** — re-update-branch the BEHIND ones each cycle. Do NOT fake `locally-validated` to skip CI (green must be at exact SHA).
- **I over-fired merge-gate** on #361 (refired while a success was current → re-queued → BLOCKED). Refire once, then WAIT ~60s.
- `ci-hub newest-green --repo-dir reverie` = NOT-VALIDATED (exit 4): reverie has NO ledger receipt writer — this is INFRASTRUCTURE, not a failure. Do not treat as red.

## #350 rebase detail (STARTED, PUSHED)
- Original branch had 2 commits: `48b072b` (=#349 AF_INET, ALREADY LANDED) + `a2be216` (TCP_INFO, the real change).
- Rebased in slot `worktrees/liteinst/reverie` branch `land-350-tcp-info`: `git rebase --onto origin/main 48b072b` → clean, single commit `bac31ca` (+208 in reverie-kvm/src/executor.rs, has regression test `getsockopt_tcp_info_is_canonicalized`, audit tags present).
- Force-pushed with lease to codex/kvm-tcp-info-canonicalize (a2be216→bac31ca). Base retargeted to main (`gh pr edit 350 --base main`). Content = KVM getsockopt-option parity (NOT a new syscall) → no post-facto-human-review label needed.

## Slot
`worktrees/liteinst/reverie` — clean checkout, branch `land-350-tcp-info` exists (local, for #350 rebase). origin/main was e0db0812 last fetch.

## Candidates NOT taken (state as of 09:4xZ)
- #335 CLEAN but base is a feature branch (codex/liteinst-perf-attribution-fastpath) — needs retarget to main first. #334/#337 CLOSED. #345/#349/#351 already MERGED.

## What I would do next
1. Poll ci.yml at each in-flight head; when green, refire merge-gate ONCE; let auto-merge land the up-to-date one.
2. After each landing, `update-branch` the re-behinded ones and repeat. Prioritize #350.
3. Verify each via mergeCommit ancestry. Tag corresponding tg tasks `implemented` (do NOT close — coordinator closes).

## NOTE
Could not resolve my tg task id (`tg list`/`--assignee` unsupported; tg takes task IDs). Coordinator: this file is the authoritative handoff.
