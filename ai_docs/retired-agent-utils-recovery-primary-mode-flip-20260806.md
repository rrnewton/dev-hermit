# Retired: agent-utils `recovery/primary-mode-flip-20260804`

**Task:** `retire_or_publish_agent`
**Date:** 2026-08-06
**Agent:** `egress-probe2` (opus-5)
**Verdict:** RETIRE. The branch carried one accidental file-mode bit and no work.
**This file is the preservation record**, committed *before* the branch was
deleted, standing in for the `origin/recovery/*` push that GitHub egress (403 on
CONNECT, re-probed this session) made impossible.

---

## 1. The complete contents of the deleted branch

This is not a summary — it is the whole thing. `git format-patch -1` of the tip,
verbatim:

```
From bcb82a63d842dbdf1249dcc86d6b72603ef3bac8 Mon Sep 17 00:00:00 2001
From: Ryan Newton <rrnewton@gmail.com>
Date: Wed, 5 Aug 2026 19:21:59 -0700
Subject: [PATCH] Preserve recovered cpuset allocator mode change

---
 py/safe_ci_dag_runner/cpuset_allocator.py | 0
 1 file changed, 0 insertions(+), 0 deletions(-)
 mode change 100644 => 100755 py/safe_ci_dag_runner/cpuset_allocator.py

diff --git a/py/safe_ci_dag_runner/cpuset_allocator.py b/py/safe_ci_dag_runner/cpuset_allocator.py
old mode 100644
new mode 100755
-- 
2.53.0-Meta
```

A mode-only change has an empty patch body, so the artifact above is *lossless*:
nothing about this commit exists that is not written down here.

**Restore, if it is ever wanted:**

```bash
# The change itself — the only thing the branch carried:
git -C agent-utils update-index --chmod=+x py/safe_ci_dag_runner/cpuset_allocator.py

# Or the exact ref, valid while the object survives (unreferenced but ungc'd):
git -C agent-utils branch recovery/primary-mode-flip-20260804 \
    bcb82a63d842dbdf1249dcc86d6b72603ef3bac8
```

## 2. Branch shape: exactly one bit was unpushed

| fact | value |
| --- | --- |
| tip | `bcb82a63d842dbdf1249dcc86d6b72603ef3bac8` |
| parent | `570e78655e4cbfd398748b278252bfbaf4cc5930` — the currently **pinned** agent-utils SHA |
| parent on origin? | yes, `git merge-base --is-ancestor` confirms it is an ancestor of `origin/main` |
| `git diff --raw bcb82a6^ bcb82a6` | `:100644 100755 0cd9740 0cd9740 M py/safe_ci_dag_runner/cpuset_allocator.py` |
| remote refs containing the tip | **0** |
| local refs containing the tip | that branch alone |

Same blob (`0cd9740`) on both sides of the diff: the content was identical and the
exec bit was the entire delta. Everything else on the branch was already on
`origin`, so retiring it could not strand anything.

## 3. Why RETIRE and not PUBLISH — five independent checks

1. **The exec bit never existed upstream.** Walking *every* commit on `origin/main`
   that touches the file and reading its mode: `100755` appears in **zero** of them.
2. **No mode transition has ever occurred.** Added as `100644` at `e0427b8`
   (2026-08-04, "Add cpuset-alloc"), modified three times since — `a598929`,
   `5ef91c5`, `57c7d55` — every one `100644 → 100644`.
3. **It was captured against a stale revision.** The branch holds blob `0cd9740`;
   `origin/main` is two revisions past it (`0cd9740 → 00af523 → 853f9b1`).
4. **The file is not a script.** `origin/main`'s version opens with a module
   docstring and has no shebang. In `py/safe_ci_dag_runner/`, 22 of the 23
   non-symlink files are `100644`; the only `100755` is `__main__.py`, the actual
   entrypoint.
5. **Nothing on disk wants it.** ~20 live checkouts stat'ed
   (`worktrees/{scwidth,vcache,val1147,gatesexp,perf,coord,cleanbuild,ghdagval,covnode,dbi,e9patch,lander,pinlint,dagmeasure,nlockgate,sabre}/hermit/agent-utils/…`
   plus four `scratch/` checkouts) — **every one is 644**.

Provenance fits: the branch was created 2026-08-05 alongside
`scratch/recovered-agent-utils-primary-20260805`, i.e. a mechanical recovery of the
agent-utils primary swept up a stray local mode change and preserved it rather
than discarding it. Being precise about the claim: this was **not merged and not
superseded by an equivalent upstream change** — it was *never intended*, and it
applies to a file revision that no longer exists.

## 4. Why the serialize + re-pin path did not apply

agent-utils content changes land one-at-a-time direct-to-main and are followed by a
parent gitlink bump. Deleting a local-only ref does none of that: it changes no
repo content, pushes nothing, moves no gitlink, and does not touch the primary
checkout's HEAD or working tree. So the serialize queue is not the right gate here.

Recorded anyway, since it was cheap: at the time of this change the serialize
surface was **clear** — primary checkout `status --short` empty (nobody mid-edit),
`origin/main..HEAD` = 0 unlanded commits, HEAD `570e786` detached.

## 5. Result

`check-agent-utils-pin`'s classes (introduced at `39c89a0`, see
`ai_docs/stale-backup-branch-prune-and-pin-check-classification-20260806.md`):

| | before | after |
| --- | --- | --- |
| `local_unpushed_commits` | 5 | 4 |
| `unpushed_in_flight_commits` | 4 | 4 |
| `unpushed_stranded_commits` | **1** | **0** |
| `unpushed_unattributed_commits` | 0 | 0 |

The four remaining unpushed commits are all in flight on branches with live
worktrees (`codex/cgroups-cap-land` ×2, `codex/cpu-timeout-platform-multiplier`,
`worker-thread-exception-fails-loudly`) — unpushed only because egress is down,
and correctly *not* a failure.

**Still open and unrelated:** the agent-utils checkout is detached and the parent
gitlink is 64 behind `origin/main`. That is genuine pin drift needing a deliberate
bump, and it is what keeps `check-agent-utils-pin` at `state=drift`. Do not read
the remaining drift as a leftover from this branch.
