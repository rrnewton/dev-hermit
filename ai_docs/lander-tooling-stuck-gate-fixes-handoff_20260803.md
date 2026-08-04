# Handoff: lander-tooling stuck-gate fixes + e2e-manifest batch closeout

**Date:** 2026-08-03
**Author:** hermit-lander (coordinator/landing-lead, opus-4.8)
**Task landed:** `e2e_union_rebase_batch_textutil` (closed by coordinator)
**Related open task:** `lander-tooling-stuck-gate-starves-fifo-queue`

## TL;DR

A lander that gates on the wrong signals can sit **20–40 minutes** stuck at a
failing/`UNKNOWN` merge-gate. Because manifest-touching lands serialize behind a
single FIFO land-lock (`ci-hub/ci-hub land-lock`), **one stuck lander is a
head-of-line block for the entire drain** — it holds the lock (renewing its lease
every ~4 min up to `--hold`) and every other agent's manifest land waits behind
it. This likely explains throughput we've blamed on CI. The three fixes below are
proven in `scratch/land-one-e2e-pr.sh` and should be lifted into the shared
lander tooling.

## The three fixes (implement these in the shared lander)

Context: for e2e/backend-parity fixture PRs the required check is **`merge-gate`**,
satisfied by the `locally-validated` label OR green portable CI. The label path is
what fixture PRs use. Three distinct races defeat a naive "stamp then poll" lander.

### Fix 1 — Race-tolerant gate poll (ride the transient FAILURE)

**Symptom:** land bails (or wedges) the instant it sees `merge-gate=FAILURE`.
**Cause (label-strip race):** a push/`synchronize` triggers merge-gate run #A while
the `invalidate-local-validation` job has just stripped the label → run #A FAILS
(label absent). Adding the label back triggers run #B → SUCCESS. Both are on the
same head SHA; `statusCheckRollup` can show either.
**Fix:** never bail on a FAILURE. Poll the **latest** merge-gate run *by
`startedAt`* and continue through transient `FAILURE`/`IN_PROGRESS`/`QUEUED` until
`COMPLETED/SUCCESS` or timeout:

```bash
cj=$(gh pr view "$PR" -R "$R" --json statusCheckRollup -q \
  '[.statusCheckRollup[]|select(.name=="merge-gate")]|sort_by(.startedAt)|last|(.status//"?")+"/"+(.conclusion//"PENDING")')
[ "$cj" = "COMPLETED/SUCCESS" ] && break   # else sleep 15 and re-poll
```

### Fix 2 — The merge command is the mergeability arbiter (NOT `mergeStateStatus`)

**Symptom:** gate is green but the lander spins forever because it waits for
`mergeStateStatus` to become `CLEAN`/`UNSTABLE`.
**Cause:** GitHub computes mergeability lazily; `mergeStateStatus` frequently
**sticks at `UNKNOWN`** indefinitely even when the required check is green.
**Fix:** once the latest merge-gate run is `COMPLETED/SUCCESS`, stop reading
`mergeStateStatus`. Attempt `gh pr merge --rebase` directly in a retry loop — the
merge call itself forces GitHub to (re)compute mergeability, so `UNKNOWN`
resolves here. Treat "already merged" as success; a genuine block surfaces as a
persistent non-transient error after the retry budget:

```bash
for i in $(seq 12); do
  out=$(gh pr merge "$PR" -R "$R" --rebase 2>&1) && { merged=ok; break; }
  grep -qi 'already merged' <<<"$out" && { merged=ok; break; }
  sleep 15
done
```

### Fix 3 — Self-heal the LAGGING-INVALIDATE strip

**Symptom:** lander stamps `locally-validated`, verifies it stuck, yet a later
poll shows the label GONE and merge-gate stuck FAILURE — with **no new push**
(branch head unchanged).
**Cause:** the `invalidate-local-validation` job (runs on `synchronize`) from the
lander's own step-2 push runs **asynchronously** and removes the label *after*
step-4 add+verify already saw it present. So the stamp is silently reverted.
**Fix:** inside the gate poll, when the latest run is `COMPLETED/FAILURE` and the
label is now absent, re-add it. The `labeled` event fires a fresh run that
evaluates WITH the label and passes. Only re-stamp on a *completed* FAILURE (not
while `IN_PROGRESS`/`QUEUED`) to avoid churn:

```bash
if [ "$cj" = "COMPLETED/FAILURE" ]; then
  lb=$(gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")')
  grep -q locally-validated <<<"$lb" || gh pr edit "$PR" -R "$R" --add-label locally-validated
fi
```

**All three are live in `scratch/land-one-e2e-pr.sh`** (steps 5–6). Fix 3 was
observed self-healing #1278 fully autonomously. Reference impl:
`scratch/e2e-union-rebase-lander.sh` (worktree-unique branch names to avoid the
fleet-wide `_union_wip` collision — a separate known bug in
`scripts/e2e-union-rebase.sh`).

## Operational guardrail (do this too)

Give `land-lock run` a bounded `--hold` and, in the lander, a hard overall
deadline that **aborts and releases** rather than renewing forever. A lander that
cannot reach SUCCESS within budget must release the lock and report, never keep
the lease alive. A lock held by a dead/stuck agent is exactly the head-of-line
block. `release` is ownership-checked (refuses if you're not the holder), and a
lapsed lease auto-clears for the next FIFO waiter — but don't rely on lapse;
release explicitly on every exit path.

## Batch result — 10/10 landed, ancestry-confirmed on origin/main (tip 7d5b8f93)

| PR | branch | landed SHA |
|----|--------|-----------|
| #1266 | e2e (prior session) | `fe896da96b00ec7c6a99cb302c0fbae9c11d5eb8` |
| #1269 | e2e (prior session) | `4b635a8373abe96b924dbc86c6b350646fdde3bf` |
| #1273 | codex/e2e-openssl-genpkey | `20859ecd15f630a21dc1ecb970d715eb72c806ba` |
| #1278 | codex/e2e-openssl-enc | `7d5b8f9323bcc44b732c7c30af1969dedb695929` |
| #1281 | codex/e2e-sort-random | `66c6b7010ae0a182c4cf6eb363484648b0a7b632` |
| #1285 | codex/e2e-uuidgen-random | `834bf2522868b8be66bc22cbd486e1f9cc7476d1` |
| #1287 | codex/e2e-perl-random | `e31b67699a5800b2238772f3a40a655498d253b8` |
| #1291 | codex/e2e-bash-random | `2636868d4605308d7ecbefaeea65e9cbb25ce20f` |
| #1354 | codex/e2e-mcookie-random | `e072d313ba62fdbd46c6708b40e5b407006946af` |
| #1369 | codex/e2e-ssh-keygen | `8da56b9ee1c959b26f96ab07289925a80129841f` |

Every land: pure-additive union-rebase onto fresh main (symmetry-lint + ratchet
clean, no row dropped from either side), `locally-validated` stamp, land-lock
FIFO mutex, `merge --rebase` (**never `--admin`**), ancestry-verify. **No PR was
demo-gated** — the broadened `tests/e2e/**` + `tests/backend-parity/**` exemption
is confirmed working end to end.

**Abandoned:** none from my batch. #1278 was originally being landed by
hermit-dbi; dbi's attempt lapsed (lease expired without a merge, PR untouched
>1h), so I completed it — it was in my assigned batch and no one was actively
working it.

## Land-lock state at handoff

I do **NOT** hold the land-lock. Last checked: held by `227b-lander` for #1278
(redundant — #1278 already landed at `7d5b8f9…`; that is 227b-lander's own
stuck-holder instance of the bug above, and a live example of the head-of-line
block). My explicit release was correctly refused (I'm not the holder). Nothing
of mine to release.
