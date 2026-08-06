# The ledger is the source of truth on landing — `tg` is a tracker

**Task:** `single-lane-agents-own-one-linear-fat-pr-and-shepherd-it-to-landing` (ledger-as-source-of-truth scope)
hermit-clone (opus-5), 2026-08-06 · **Local, no egress.**
Delivered: `ci-hub/closure/landing_evidence.py`, `ci-hub/closure/test_landing_evidence.py`
(17 tests), `ci-hub/closure/README.md`. **422 passed** across closure + landing + validate.

## Scope note: the parent task is gated, and I did not touch it

The task carries an explicit gate — *"post-backlog. The owner framed it as 'as we move forward and
get past this big PR backlog'. **Do not restructure lanes mid-drain.**"* So the one-fat-PR-per-lane
restructuring is **not** implemented and should not be, yet. The dispatch scoped this run to the
ledger-as-source-of-truth half, which is tooling and compatible with the gate.

## What already existed, checked first

- **`verified_close.py`** (behind `ci-hub/bin/close-task`) fetches the remote and *then* tests
  `mergeCommit.oid` ancestry, returning `CLOSED` / `REFUSED` / `UNVERIFIABLE`. Verified the fetch is
  real, not assumed (`verified_close.py:144-161`, and it refuses when the fetch fails).
- **`ci-hub/directives/README.md`** already states the rule for obligations, verbatim: *"A quotation,
  dispatch, design document, branch, or open pull request is not completion. `check.py` reports
  `satisfied` only after the claimed full commit SHA or pull request `mergeCommit.oid` is an ancestor
  of the freshly fetched named target branch."*
- **No code consumer treats `tg` state as landing evidence.** I grepped for it; the exposure is
  *agent behaviour*, not a wiring defect.

So both enforcement points work. **The gap is that neither is reusable by a third party.** An agent
asking *"does this thing prove it landed?"* had only prose to consult — and a rule that must be
remembered is one that decays. That is what this adds, and it is why I did not rebuild either
mechanism.

## The predicate

```bash
python3 ci-hub/closure/landing_evidence.py --list                   # both lists, with reasons
python3 ci-hub/closure/landing_evidence.py --kind tg-status-closed  # exit 1
```

**Authoritative:** `merge-commit-ancestry` (on a freshly fetched target) and `directives-ledger`
(`satisfied`) — the ledger qualifying *because it performs that ancestry check itself*: it is a cache
**with a dereference**, not a label.

**Not landing evidence**, each with what it *actually* tells you — the useful half:

| source | what it really says |
|---|---|
| `tg` status `closed` | a coordinator closed the task |
| `tg` `implemented` tag | published, explicitly **not** landed |
| a task note | one agent's unverified belief |
| PR `MERGED` flag | a later force-push **orphans** the replay SHA (~12 PRs, 2026-08-03) |
| `is-ancestor <PR head>` | nothing after a rebase replay — read **79 unlanded when 46 had landed** |
| `locally-validated` label | a cache of a fact; live on four PRs with no backing record |
| a green check | CI passed on some commit, not that it reached `main` |
| a pushed branch | a proposal |
| merge-queue position | the opposite of having landed |

## Two design decisions worth naming

**Non-authoritative sources do not accumulate.** `require_landing_evidence()` accepts iff **at least
one** source is authoritative — deliberately not a score or a best-of. `closed` + `implemented` +
`MERGED` + a green check still is not landing, and a test pins that four weak sources aggregate to a
refusal reading *"do not accumulate"*. That summing instinct is the arithmetic error this whole class
of defect is made of.

**An unknown evidence kind is `UNVERIFIABLE`, not allowed.** A source nobody has classified is
precisely the one to refuse, because the reader cannot tell which list it belongs to. A permissive
default would let the next dashboard quietly become landing evidence.

## Verification

17 tests: every catalogued non-authoritative source refused *and required to explain what it does
tell you*; the headline `tg-status-closed` case; the three ways the authoritative form still fails
(stale fetch → `UNVERIFIABLE`, non-ancestor → refused, undetermined ancestry → `UNVERIFIABLE`); the
accumulation test; and **positive controls** — a fresh-fetch ancestry and a satisfied ledger are
accepted, so the predicate cannot pass its negatives by refusing everything. The CLI's bare
`--kind merge-commit-ancestry` exits 1: a kind cannot self-certify without the fetch and ancestry
data.

## Honest limits

- **The predicate is appealable, not enforced.** Nothing calls it yet; it does not intercept an agent
  that reads `tg` and believes it. Wiring it into the lander or the preflight is the obvious next
  step, and I am flagging rather than assuming it — an unwired guard is the present-but-inert shape
  this lane keeps finding.
- **The catalogue is a list, not a proof of completeness.** Nine sources are classified because nine
  have actually been mistaken for landing evidence. The `UNVERIFIABLE` default is what makes the
  incompleteness safe rather than silent.
- **The gated parent task remains open by design.** Everything above is orthogonal to the fat-PR
  restructuring; none of it presumes or prepares that change.

## Files

`ci-hub/closure/landing_evidence.py` (new) · `ci-hub/closure/test_landing_evidence.py` (new) ·
`ci-hub/closure/README.md` (new). Uncommitted — egress down.
