---
name: validate-sh-rr-compat-counter-conflict
description: "validate.sh RR_COMPAT_EXPECTED is a recurring rebase-conflict hotspot; it must EXACTLY equal the RR_COMPAT_PASSING_LABELS array size. Reconcile = base's current count + the PR's net-new additions, then verify the array count."
---

> **CI-HUB** — Current CI code, live query entrypoints, history, runner operations, and health truth are centralized at `ci-hub/README.md`. This memory records role/policy or historical context; do not treat dated paths or state below as the live tool location.

Recurring merge-conflict class when rebasing any hermit PR that adds programs to the record/replay corpus (seen landing PR #662, 2026-07-26). `validate.sh` has TWO coupled values that MUST agree:
- `readonly RR_COMPAT_EXPECTED=<N>` (~line 291)
- `declare -Ar RR_COMPAT_PASSING_LABELS=( [prog]=1 ... )` (~line 332)
- guard at ~line 360: `if ((${#RR_COMPAT_PASSING_LABELS[@]} != RR_COMPAT_EXPECTED)); then ... "must contain exactly $RR_COMPAT_EXPECTED rows"` — CI (and any run) fails if they mismatch.

When main and the PR both bump the RR corpus, the labels ARRAY usually auto-merges (both sets of `[prog]=1` additions coexist) but `RR_COMPAT_EXPECTED` conflicts. **Correct resolution = current main's count + the PR's net-new programs**, NOT either conflict side. Then VERIFY by counting the merged array:
`sed -n '/declare -Ar RR_COMPAT_PASSING_LABELS=(/,/^)/p' validate.sh | grep -oE '\[[a-zA-Z0-9_+.-]+\]=1' | wc -l` must equal RR_COMPAT_EXPECTED. Ex #662: main 131 (incl ruby/dc/tcl from #729) + #662's 12 (chmod,cp,diff,install,mkdir,mkfifo,mv,node,rm,rmdir,tar,touch) = 143. `git merge-tree --write-tree` predicts the conflict cheaply before starting a worktree rebase. Related counters that also live here and may need main's newer value: STRICT_COMPAT_TOTAL, LITEINST_COMPAT_EXPECTED, SABRE/E9PATCH_*.

**CI gates for landing a hermit PR (merge-gate.yml):** the required check `merge-gate` passes iff EITHER the latest `ci-hosted.yml` run for the exact head SHA is `completed:success` OR the PR carries the `locally-validated` label. It does NOT check self-hosted PMU or `validation-levels.yml` ("Portable validation (PR)"). A merge-gate that ran while hosted CI was `in_progress` fails STALE; re-fire with `gh workflow run merge-gate.yml --ref <branch> -f pr_number=<n>` once ci-hosted is green. See [[undraft-does-not-trigger-ci]], [[self-hosted-ci-sigsegv-blocks-all-prs]], [[validate-sh-cannot-be-green-on-devserver]].

**Flaky CI timeouts (not code regressions):** ci-hosted "Portable test lane" and validation-levels "Portable validation (PR)" intermittently fail on DBI/heavy-L2 program TIMEOUTS in the no-PMU hosted runner: `run_dbi_verifies_shell_process_lifecycle`/`dbi/argument_forwarding` (600s gate, exit 124) and rustc/java/javac/zstd L2 (exit 124 -> panic at reverie-ptrace/src/stack.rs:49, the unwinder after the forced kill). These clear on re-run; main's own ci-hosted passes them on faster runners. Don't attribute them to a record/replay or ptrace-fs PR.
