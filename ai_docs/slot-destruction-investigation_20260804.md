# Active worktree slots are being deleted outside the registry

**Date:** 2026-08-04 (evening PDT / 2026-08-05 UTC)
**Investigator:** drainer-3, task `investigate-slot-destruction`
**Method:** read-only. No `git clean`, no deletions, no state changes.

## Finding

Slots are removed by a path that **bypasses the registry entirely**.

`worktree-state.json` lists **26 slots, all `status: active`**. Only **5 slot
directories exist** on disk. `release-worktree.rs` is the designated single
writer of that file and would have updated every removed slot. So ~21
registered-*active* slots were deleted without the registry ever being told.

That is the Hard Invariant 14 violation, and it is **systemic**, not specific to
`drainer-3`.

`scripts/check-worktree-registry.rs` already detects it:

```
worktree-registry: FAIL rows=26 correct_rows=0 drift_rows=26 product_cells=78 drift_cells=52
```

Zero correct rows out of 26, and nothing gates on it. This is precisely the hole
the Authority Registry already records as **PARTIAL** for *Workspace ownership*:
"`allocate-worktree.rs --check-only` calls it, but normal allocation,
`release-worktree.rs`, and `worktree-gc.sh` do not all run the verifier before
acting."

## `worktree-gc.sh` is exonerated

Checked, not assumed:

1. Tiers 2 and 3 hard-require `SLOT_STATE == released`
   (`worktree-gc.sh:285`, `:305` — `skip $slot (not released)`). `drainer-3`
   was, and still is, `active`. gc would have skipped it.
2. The residue is the **opposite** of what gc does. Tier 1 removes only
   `target/*/incremental/`; tier 2 removes the whole `target/`. In every wiped
   slot `target/` is what **survived** and the tracked source is what vanished.
3. Nothing invokes it automatically: no crontab, no dev-hermit systemd timer,
   no gc log, references only in docs. Manual-only.

**Latent hazard in it anyway.** `slot_busy()` (`worktree-gc.sh:127-134`) pgreps
only for `cargo|rustc|cc1|cc1plus|ld|make|ld.lld|lld` cwd'd under the slot. An
agent editing files, or running `git`/`python`/`jq`, is **invisible**. The first
wipe here happened mid-edit-pass with no compiler running — that slot reads as
idle. gc did not cause this, but if ever pointed at an active slot it will not
see a live agent.

## Evidence

**Residue signature**, identical in three slots — `drainer-3/hermit`,
`drainer-4/hermit`, `reviewer-2/hermit` each contain **only `target/`**
(`drainer-3` also `.safe-ci-dag-runner/`). All tracked source gone, ignored and
untracked dirs left, and the `.git` file removed. Proof the `.git` file went:

```
DRIFT slot=reviewer-2 hermit recorded=detached actual=codex-coord
```

That worktree now resolves to the **parent repo's** branch — the same symptom
seen in `drainer-3`. A whole-directory `rm` or `git worktree remove` would have
taken `target/` too; stripping tracked content while leaving ignored content is
a different operation.

`drainer-2` and `drainer-5` are intact because both were (re)allocated **after**
the sweep (state updated `03:24:59Z`, `03:25:49Z`).

**Timeline** (directory mtimes, PDT): `drainer-3/hermit` emptied `20:24:54.98`;
`drainer-2` reallocated `20:24:58.9`; `drainer-5` reallocated `20:25:49.7`;
`ACTIVE.md` rewritten `20:25:49.86`. The first wipe was ~`20:0x`; between the two
allocations the homeostasis banner fell from **1605 GB to 109 GB** apparent —
~1500 GB freed. That is the mass sweep, far more than any single slot.

## A second session is operating in this checkout

- The parent repo is on branch **`codex-coord`**, not `main`.
- The **primary** hermit checkout was **detached from main** at `20:05:33` PDT:

  ```
  b4e94ce4 HEAD@{2026-08-04 20:05:33 -0700}: checkout: moving from main to b4e94ce4...
  ```

  A direct Primary Checkout Invariant violation ("must ALWAYS be on latest main.
  Never detach HEAD"), in the same second as parent commit `e35eda7`.
- Parent commits through `20:05` are tagged `[coordinator, opus-4.8]`.

Read-only evidence **cannot name the exact command**, and it is not guessed here.
What is established: a concurrent coordinator session is mutating this checkout,
including the primary, in violation of the invariant, in the same window as the
sweeps.

**Related new mechanism** (commit `6835385`, 19:54, "ci-hub: stop detached
validates explicitly"): `ci-hub validate-stop --all` enumerates every active
detached validate unit and `systemctl --user stop`s it — a one-command way for
one session to kill another agent's in-flight validation. It deletes no files, so
it is not the wipe, but it belongs in the same coordination discussion.

## Correction to an earlier report

An earlier note cited `wall 9m39s | CPU 0s | CPU/wall 0.0x` as evidence the
process did nothing. **Wrong.** The same log shows
`.../hermit-validate.VqN9sV/cpu-times: No such file or directory` — the `0s` is
an artifact of the accounting file being deleted with the tree, not a
measurement. The real evidence the tree died under a live process is the ENOENT
on tracked scripts (`./ci/run-dag.sh`, `./ci/test_harness.sh`) that existed when
the run started.

## Prevention, in priority order

1. **Gate every removal path on the verifier.** Make
   `check-worktree-registry.rs` a hard precondition for `release-worktree.rs` and
   `worktree-gc.sh`, and make both **refuse to act while it reports FAIL**. It
   reports FAIL with 0/26 correct rows right now and nothing cares. This single
   change would have prevented every wipe here.
2. **Make `status=active` a refusal, not one tool's skip-condition.** Any path
   that can remove a slot directory must consult the registry; removing an active
   slot must require an explicit override plus a recorded recovery SHA
   (Hard Invariant 14).
3. **Fix `slot_busy()`.** Detect liveness by slot mtime and by *any* process
   cwd'd under the slot, not a hard-coded compiler list.
4. **Reconcile the registry now.** 21 slots recorded active with no directory
   will mislead the next allocator and the next audit. Use the single-writer
   `--repair` mode (parent commit `0f53a3c`), run once, by the coordinator.
5. **Restore the primary.** `~/work/dev-hermit/hermit` is detached at
   `b4e94ce4`; return it to latest main.
6. **Coordinate the second session.** Coordinator's call: two sessions drive the
   same checkout, one detached the primary and commits on `codex-coord`, and
   `validate-stop --all` gives either a one-command kill over the other's work.
