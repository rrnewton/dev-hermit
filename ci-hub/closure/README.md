# Closure: what proves a change reached `main`

## The rule

Landing is a fact about `main`. It is established **only** by

1. **`mergeCommit.oid` ancestry on a freshly fetched target**, or
2. **the directives ledger reporting `satisfied`** — which is authoritative
   *because it performs that same ancestry check*, not because it is a record.

Everything else is a report *about* landing, not landing.

> **`tg` CANNOT VERIFY LANDING. It is a TRACKER, not a source of truth on `main`.**
>
> A task reads `closed` because a coordinator closed it. The close is a **record
> of a verification, never the verification**. Reading it back as proof is
> circular — the tracker would be certifying the thing it was told.

## Ask the predicate instead of remembering the rule

```bash
python3 ci-hub/closure/landing_evidence.py --list          # both lists, with reasons
python3 ci-hub/closure/landing_evidence.py --kind tg-status-closed   # exit 1
```

Library use — `classify_evidence(kind, …)` returns
`AUTHORITATIVE` / `NON-AUTHORITATIVE` / `UNVERIFIABLE`, and
`require_landing_evidence([...])` accepts **iff at least one source is
authoritative**. That is deliberately not a score: *ten non-authoritative
sources do not sum to one authoritative one*, which is the arithmetic error this
class of defect is made of.

An **unknown** evidence kind is `UNVERIFIABLE`, not allowed — a source nobody has
classified is precisely the one to refuse, because the reader cannot tell which
list it belongs to.

## Not landing evidence, and what each one actually tells you

| source | what it really says |
|---|---|
| `tg` status `closed` | a coordinator closed the task |
| `tg` `implemented` tag | published, explicitly **not** landed |
| a task note | one agent's unverified belief |
| PR `MERGED` flag | merged at some point — a later force-push **orphans** the replay SHA (~12 PRs, 2026-08-03) |
| `is-ancestor <PR head>` | nothing after a rebase replay: always false, and it read **79 unlanded when 46 had landed** |
| `locally-validated` label | a cache of a fact — it was live on four PRs with no backing record |
| a green check | CI passed on some commit, not that the commit reached `main` |
| a pushed branch | a proposal |
| merge-queue position | the opposite of having landed |

## Where this is already enforced

- **`verified_close.py`** (the `close-task` gateway) fetches the remote, then
  tests `mergeCommit.oid` ancestry, returning `CLOSED` / `REFUSED` /
  `UNVERIFIABLE`. No close happens without it.
- **`ci-hub/directives/`** — `check.py` reports `satisfied` only on
  freshly-fetched ancestry; its README states the same rule for obligations.

`landing_evidence.py` does not replace either. It makes the rule **appealable by
a third party**: an agent asking "does this thing prove it landed?" previously
had only prose to consult, and a rule that must be remembered is one that decays.

Tests: `python3 -m pytest ci-hub/closure/ -q`
