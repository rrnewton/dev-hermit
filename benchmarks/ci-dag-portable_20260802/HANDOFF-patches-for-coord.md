# Main-recovery handoff patches (hermit-ci → coord)

Two ready-to-apply fixes for the **primary** `hermit/` checkout. hermit-ci does
not touch the primary (coord owns it); these are for coord to apply + land.

Status of the third fix (`make validate` .PHONY): **already landed** direct-to-main
by hermit-ci before the role change — commit `78f9b926`, push `57a3ea41..78f9b926`.

---

## Patch 1 — Fan-out Pack resilience (`tar: target/e2e/build: Cannot stat`)

> **STATUS: LANDED by hermit-ci direct-to-main — commit `ef4a524b`**
> (`mkdir -p target/e2e/build` guard added before the Pack `tar` in
> `ci-portable-fanout.yml`). No coord action needed. Kept below for the record.


**Symptom:** `ci-portable-fanout.yml` build job "Build hermit + stage guests once"
failed after ~15 min on #1476 with
`tar: target/e2e/build: Cannot stat: No such file or directory`.

**Root cause:** `target/e2e/build` is created lazily by
`test_harness.sh build --lane portable --ci-only` **only** when ≥1 guest fixture
stages (it is `$RESULT_ROOT/build/$SOURCE_TREE_SHA`'s parent, RESULT_ROOT defaults
to `target/e2e`). On a tree that stages zero fixtures the dir never exists, so the
Pack `tar` aborts. The compile itself succeeded — only the Pack step failed.

**Fix:** materialize the dir before `tar`. Cells already run `--allow-empty
--prebuilt`, so an empty staged tree is handled correctly.

File: `.github/workflows/ci-portable-fanout.yml`, "Pack prebuilt tree" step
(~line 118), add one line before the `tar`:

```yaml
      - name: Pack prebuilt tree
        run: |
          set -euo pipefail
          mkdir -p target/e2e/build   # <-- ADD: dir is lazily-created; guard tar
          tar --zstd -cf "$BUILD_TARBALL" \
            --exclude='target/release/deps' \
            ...
            target/e2e/build
          ls -lh "$BUILD_TARBALL"
```

(The same guard is already present in the new `ci-portable-parallel.yml`, PR #1478.)

---

## Patch 2 — merge-gate FALSE-RED de-taint (why #1460/#1476 needed admin-merge)

**Symptom:** merge-gate reports failure and blocks landing even when the
authoritative portable tests pass, forcing owner `admin --merge`.

**Root cause:** the merge-gate step "Require successful CI or local validation"
keys on the **whole** `ci-portable.yml` run `.conclusion`. That run contains TWO
jobs:
  - `regular` / "Regular tests (GitHub-managed portable)" — authoritative
  - `reverie-pin` / "Reverie pin is current" — fails on any stale pin

A stale/behind Reverie pin fails `reverie-pin` → whole-run conclusion = `failure`
→ merge-gate red, even though `regular` succeeded. #1460 exhibited exactly this
(regular=success, run=failure). #1476 was merged while CI was `queued/none`.

**Fix:** gate on the authoritative **job** conclusion, not the run conclusion.

File: `.github/workflows/merge-gate.yml`, replace the run-conclusion block
(current lines ~327–344) with a job-level check:

```yaml
              runs="$(
                gh api "repos/${REPO}/actions/workflows/ci-portable.yml/runs?head_sha=${head_sha}&per_page=100"
              )"
              run="$(jq --arg sha "$head_sha" '
                [.workflow_runs[] | select(.head_sha == $sha)]
                | sort_by(.created_at) | last // {}
              ' <<< "$runs")"
              run_id="$(jq -r '.id // ""' <<< "$run")"
              url="$(jq -r '.html_url // ""' <<< "$run")"

              # De-taint: gate on the AUTHORITATIVE job, not the whole run.
              # ci-portable.yml also runs `reverie-pin` ("Reverie pin is current");
              # a stale pin fails that job and would sink the whole-run conclusion
              # to `failure` even when "Regular tests (GitHub-managed portable)"
              # passed. Key merge-gate on that job alone.
              if [ -z "$run_id" ]; then
                echo "::error::No ci-portable.yml run for PR #${pr_number} at $head_sha."
                failed=1
              else
                job_conclusion="$(
                  gh api "repos/${REPO}/actions/runs/${run_id}/jobs?per_page=100" --paginate \
                    --jq '.jobs[] | select(.name == "Regular tests (GitHub-managed portable)") | .conclusion' \
                  | tail -n1
                )"
                if [ "$job_conclusion" = success ]; then
                  echo "Authoritative portable job passed for PR #${pr_number} at $head_sha: $url"
                else
                  echo "::error::Regular tests (GitHub-managed portable) for PR #${pr_number} at $head_sha is ${job_conclusion:-missing}: ${url:-no run URL}."
                  failed=1
                fi
              fi
```

**Alternative (structurally cleaner, larger blast radius):** move `reverie-pin`
out of `ci-portable.yml` into its own non-required workflow. Then the whole-run
conclusion of `ci-portable.yml` == the authoritative result and the current
merge-gate code needs no change. Trade-off: the pin check stops being visible on
the same run and needs its own (non-required) status.

**Blast radius:** Patch 2 changes the landing gate for **all** PRs. Coord's call
whether to own/land it or have hermit-ci open it as a reviewable PR. Unblocks the
GitHub merge queue: the queue admits only when the required `merge-gate` check is
green on the synthetic queue commit; today a stale-pin taint kicks everything out.
