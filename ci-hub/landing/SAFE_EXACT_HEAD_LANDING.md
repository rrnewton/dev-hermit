# Safe exact-head landing

`ci-hub/bin/safe-exact-head-land` is the Hermit-only, no-rewrite landing path for
an already reviewed and exactly validated PR head. It accepts only
`rrnewton/hermit`, only PRs targeting `main`, and only a lowercase 40-hex
expected head `X`.

This command performs a real GitHub merge. Inspect the PR and pass its current
head explicitly:

```text
ci-hub/bin/safe-exact-head-land \
  --repo rrnewton/hermit --pr PR --expected-head X --json
```

The entrypoint automatically runs inside the canonical fleet
`ci-hub land-lock`. This is a process-bound authorization, not an environment
marker: the outer process removes `CI_HUB_LANDING_LOCK`, landing/obligation
store overrides, and `CI_HUB_DOCS_PARSE_ONLY`, then starts `land-lock run` with the exact
agent/repository/PR/operation-`X` tuple. The hidden inner flag is control flow only. Before
any checkout or GitHub operation, canonical Rust `land-lock assert-child`
requires that same tuple and current holder host, verifies the supervisor's
boot ID/PID/start time and liveness, and dereferences two bounded kernel process
ancestry paths: recorded supervisor to the selected Python child, then that
child to the assertion verifier. The bounded paths permit the host's Python and
rust-script launchers; unrelated, over-deep, PID-reused, manually acquired, or
repository-less leases refuse. Both processes must remain in the persisted
child process group. Heartbeat renewal preserves the repository and operation
bindings; renewal failure terminates and empties that group before release.
The inner process always uses the canonical landing and obligation stores;
neither public flags nor inherited environment can split recovery history.

The executor never checks out, rebases, pushes, force-pushes, labels, or
otherwise writes the PR branch. GitHub receives one synchronous REST
`PUT /repos/rrnewton/hermit/pulls/PR/merge` request with `merge_method=rebase`
and the atomic expected-head field `sha=X`. The queue-capable `gh pr merge`
path is deliberately not used.

## Authorization and proof

Before the merge request, the executor requires all of these facts:

- before any fetch or graph proof, the checkout has exactly one `origin` URL
  and it canonically identifies `rrnewton/hermit` through GitHub HTTPS or SSH;
  missing, multiple, local-path, credential-bearing, port-bearing, or lookalike
  origins are refused;
- the PR is open, non-draft, still reports head `X`, targets `main`, and has no
  changes-requested/review-required decision or unresolved review thread;
- a fresh PR-ref fetch is exactly `X`;
- GitHub's complete ordered PR commit list equals the local linear, merge-free
  `S..X` list, where `S = merge-base(observed-main, X)`;
- canonical
  `ci-hub validate-status --repo rrnewton/hermit --sha X --json` accepts at
  least one dereferenced receipt bound to that exact repository, SHA, and tree;
  the receipt must be clean and commit-anchored, full/full, zero-failure, have
  nonzero and arithmetically bound discovered/selected/executed counts,
  complete passing gates, satisfied coverage, durable host/slot/log provenance,
  and a canonical receipt identity tuple plus SHA-256 digest;
- if observed main is not an ancestor of `X`, exact observed main independently
  has the same hard-green receipt. This prevents a soft-only base from creating
  an untracked second soft hop.

The intent is appended and file/directory-fsynced before any merge mutation. It
contains a unique attempt identity, `X`, observed main, `S`, the exact local and
GitHub source lists/count, source/base trees, and dereferenced receipt envelopes
plus their report digests. Immediately before requesting the merge, the tool
freshly rechecks main, PR/review identity, both commit lists, source provenance,
and every required enriched receipt. It then asks canonical Rust
`land-lock arm-mutation` to file/directory-fsync `pending_mutation=X` together
with the exact durable attempt id. Before every REST invocation it appends
`merge_call_started(call_id)`, then advances Rust's fsynced call high-water
`{attempt_id, call_count, last_call_id}`. Recovery proceeds only when the
retained barrier and append-only history match exactly; missing, truncated, or
cross-attempt history stays quarantined and cannot turn a later refusal into a
false first-call negative. A sparse `VALIDATED` report, bare receipt count, or
profile/result summary is refused.

After GitHub reports `MERGED`, it must still report the original head `X`, base
`main`, and a full `mergeCommit.oid` named `MC`. A transient `MERGED` response
with a null oid is bounded, recoverable pending state: the same durable attempt
polls and resumes without submitting a duplicate accepted request. Fresh Git state
then proves:

1. `MC` is reachable from freshly fetched `origin/main` (temporary propagation
   delay remains recoverable and is polled with a bound);
2. `Y = MC~N`, where `N` is the source count bound to both Git and GitHub;
3. observed main is an ancestor of actual replay base `Y`;
4. `Y..MC` contains exactly `N` first-parent, merge-free commits;
5. `merge-base(Y, X) == S`;
6. `git merge-tree --write-tree Y X` is conflict-free; and
7. its tree is exactly `MC^{tree}`.

Tree equality is the final composition authority. Commit-message similarity,
patch IDs, PR state, labels, and branch names are not substitutes.

## Hard and soft green

- If `Y` is an ancestor of `X`, the exact `X` full receipt covers the complete
  resulting tree. The record says `green_class=hard_green` and `soft_green=null`.
- If `Y` is not an ancestor of `X`, the conflict-free composition is one hop of
  inherited confidence. Exact hard-green receipts for both `X` and actual `Y`
  are mandatory, and the record says `green_class=soft_green` plus
  `soft_green=soft-green(zero-conflict)`.

The actual `Y` rule is re-evaluated after the merge; it is not inferred from the
pre-request base. If main races after the last preflight and actual `Y` has no
exact hard receipt, no success record or obligation is emitted. The attempt
stays recoverably pending. Once exact `Y` is independently validated, rerunning
the same command resumes the intent, repeats the proof, and may proceed.

## Crash recovery and obligation order

The forced append-only store is
`ignored/ci-hub/safe-exact-head-landings.jsonl`; obligations are forced to
`ignored/ci-hub/obligations.jsonl`. Every transition is locked,
appended, file-fsynced, and directory-fsynced. Malformed events, inconsistent
receipt envelopes, duplicate events/intents, changed attempt identity, and arm
events not exactly matching a prior replay proof are refused.

The fsynced receipt envelope is provenance and a recovery cache, never receipt
authority. Before any recovered merge continuation, and again immediately
before every post-land arm, the executor reruns canonical `validate-status` for
each persisted hard receipt (`X` and, for soft composition, `Y`). The freshly
selected canonical receipt identity digest and every dereferenced receipt field
must equal the persisted selection. Missing or changed evidence refuses before
a merge request, or stays recoverably failed/pending after a request or verified
landing. Recomputing the envelope's outer JSON hash around a forged inner
receipt digest therefore cannot authorize recovery.

A synchronous response with `merged=true` is never resubmitted during recovery;
the executor polls GitHub's state. Each call persists the size-bounded raw
`gh api --include` HTTP envelope and reparses it on recovery. Exactly one known
HTTP status line and one JSON object must agree. Only 404, 405, 409, or 422 (or
a well-formed HTTP 200 body with `merged=false`) from the first fully paired
call, with no earlier ambiguity, proves no mutation and permits barrier clear.
A transport error, malformed/oversized envelope, 5xx response, unmatched call,
or any later negative after an ambiguous call retains the barrier. After the replay proof, the tool
first fsyncs `landing_verified`, then calls canonical `ci-hub arm-land` for exact
`MC`, checks the armer exit result, dereferences the resulting obligation, and
calls the single canonical `protocol.obligation_launch_durable(record)`
predicate. Both the local producer/result and the watcher must have durable
evidence. A missing or pending watcher records `arm_failed` and remains
recoverable; it never records `obligation_armed`. Only a positive canonical
predicate permits the final fsynced `obligation_armed` event. Recovery from that
event dereferences the exact obligation id/repository/`MC` and reruns the same
durability predicate; the copied landing event is never clear authority. Only then does the
verified child clear `pending_mutation=X` and exit successfully. Rust refuses to
release if a nominally successful child leaves the barrier armed. A crash after
proof or during arming resumes from the same attempt without another merge.

A merged result whose PR commit list or replay/tree proof no longer matches is
recorded as nonterminal `landing_quarantined`, not laundered into failure or
success. The same attempt remains globally serialized and submits no new merge;
only a later fresh proof can proceed to obligation arming. There is no manual
quarantine-clear bypass.

Before the canonical supervisor enters any guard, an isolated lifetime watchdog
opens a pidfd for its actual parent and acknowledges a short monotonic phase
deadline. Phase bounds remain armed through FIFO polling, atomic acquisition,
startup, heartbeat shutdown, and final release; after `.domain` is fsynced and
armed, the bound covers the child deadline plus both cleanup graces. Thus
SIGSTOP or deadlock while holding the advisory flock causes exact-parent
SIGKILL without signaling a reused numeric PID. The watchdog never clears the
holder or mutation itself.

The supervisor also persists the exact child leader PID/start time, process
group, sibling watchdog identity, host/boot, and deadline in
`.landing-lock.domain`. Supervisor SIGKILL,
lease expiry, or a missing holder record cannot admit another land while that
group has a live member. A self-stopping exec wrapper prevents user code from
starting until Rust has fsynced that domain and explicitly resumed it. Normal
exit and deadline cleanup inspect the complete
group, not just the direct child; lingering members receive TERM then KILL.
After an armed child exits nonzero, only the same agent/repository/PR/`X` run may
adopt the retained operation, and its exact attempt/call high-water must match
the canonical event store. Generic `release`, `reclaim-dead`, a different
operation, or a successful child that fails to clear the barrier is refused.

This guarantee is scoped to `safe-exact-head-land`. The tracked legacy
`land-pr.sh` is still executable and `parallel-prevalidate.sh` still has an
active default call path to it outside this change. That is an unresolved
fleet-wide migration blocker: operators must not use that route as authority
or fallback, and this document does not claim the repository has mechanically
disabled it.

Exit codes are `0` landed-and-armed, `2` refused, `3` environmental/store error,
and `4` recoverably pending.

## Tests

```text
python3 -m pytest -q ci-hub/landing/test_safe_exact_head_land.py
python3 -m py_compile ci-hub/landing/safe_exact_head_land.py
bash -n ci-hub/bin/safe-exact-head-land
```

The behavioral suite brackets hard/soft positives and the inert negatives:
head push/drift, main advance, forbidden repository/ref, vacuous receipt,
origin identity/lookalikes, GitHub-vs-Git commit-list mismatch, synchronous
response propagation and duplicate submission, null/delayed `MC` propagation, crash recovery,
malformed/tampered records, sparse or condition-mismatched receipt reports,
all four definitive HTTP negatives, ambiguous-call retention, exact
attempt/call-high-water truncation, quarantine recovery, missing/tampered live
obligations, non-durable obligation launch, actual-`Y` receipt race, and real-Git replay/tree
mismatch. Lock tests additionally bracket forged environment/hidden-flag
entry, wrong tuple/host/child identity, owner reboot/PID reuse, missing/expired
state, real supervised process ancestry, an over-deep process chain, supervisor
SIGKILL with a surviving grandchild, a TERM-ignoring deadline grandchild,
SIGSTOP recovery, direct-watchdog authority refusal, heartbeat failure,
missing-holder acquisition, and exact-operation/attempt mutation
recovery. The
suite never invokes a live merge.
