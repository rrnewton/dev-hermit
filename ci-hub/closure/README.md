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

## `--artifact`: the authority is `origin/main`, never your checkout

A research-only task closes on a durable artifact rather than a PR. The gateway
resolves a repo-relative `--artifact` path **entirely against the freshly
fetched `origin/main`** — it does not look at your working tree or your index:

| check | how it is established |
|---|---|
| exists, and is a file not a directory | `git cat-file -t origin/main:<path>` is `blob` |
| content commit | `git log -1 --format=%H origin/main -- <path>` |
| ancestry | `merge-base --is-ancestor <content-commit> origin/main` |
| recorded tuple | `rrnewton/dev-hermit:<path>@<content-commit>;target=main@<tip>` |

**This is deliberate, and it used to be wrong.** The gate previously required
`path.is_file()` plus `git ls-files` in the parent primary. That primary runs
tens of commits behind origin (41 behind on 2026-08-06), and the only safe way
to publish a parent artifact is from a worktree off `origin/main` — so a
correctly published artifact was routinely absent from the primary's working
tree, and closure was refused with `artifact is not a file`, a message naming
the wrong cause. Nothing was weakened by removing those two checks: existence,
version-control, and blob-ness are all re-established against `origin/main`,
which is strictly the stronger authority. A file that exists locally but was
never pushed is now **refused** (`artifact is not on parent main`), where the
old working-tree check would have waved a locally-modified copy through to the
published content commit.

`--check-only` runs every verification and records nothing — use it first.
Absolute paths outside the workspace are refused before any git call; use a
full `https://` URL for anything genuinely external.

## `--observation-command`: re-observe a machine-local repair

An operational repair has no commit, published artifact, or hosted run. Close
it with a read-only postcondition command and the output observed when the
repair was verified:

```bash
./ci-hub/bin/close-task TASK \
  --observation-command '["git","-C","hermit","rev-list","--left-right","--count","HEAD...origin/main"]' \
  --observation-output $'0\t0'
```

The gateway decodes a JSON argv array, refuses shells, interpreters, `tg`, Git
mutation subcommands, and every unclassified executable, then re-runs the
command directly without a shell. It closes only when the fresh command exits
zero and its non-empty canonical output exactly matches the quoted output.
The executable is resolved to a binary that the caller cannot rewrite or
replace through a writable parent directory before it runs; the
`CLOSURE-VERIFIED` note carries that exact resolved JSON argv, return code, and
output.
A mismatch, nonzero command, empty output, malformed argv, or non-read-only
command is `REFUSED` before any TaskGraph mutation.

Record a postcondition, not the mutating repair command: after
`git -C hermit checkout main`, the example verifies that `HEAD` and
`origin/main` have zero commits on either side.
Like artifact closure, this proves the recorded authority (the postcondition is
freshly reproducible); the coordinator still checks that the observation
answers the task's stated operational goal. `--check-only` exercises the same
verifier without recording or closing.

## `--code`: a bare PR number needs an explicit `--repo`

`--repo` defaults to `rrnewton/hermit` and `--source` to `ROOT/hermit`, so a
**parent**-repo task closed with a bare `--code 56` silently verified *hermit's*
#56. That happened: `execute-ambiguous-zero-fix-order-a3-a4-first`, a task about
`compat-envelope/render-scorecard.rs`, closed against hermit's
`docs: add Hermit error catalog (#56)` merged three weeks earlier. The ancestry
check was real; nothing bound the **repository** to the task's subject.

A 40-hex SHA is self-identifying — the verifier can only resolve it where it
exists — so it still works with the default. A bare `#N` is not: every
repository has one. `--code <N>` without `--repo` is therefore **refused**.

The recorded tuple now carries the repository with the SHA:

```
CLOSURE-VERIFIED: kind=code reference=<ref> resolved=<owner/repo>@<sha> …
```

That is also what makes the note *readable*. `ci-hub/directives/tg_landed.py`
derives landing state from it and accepts an explicit SHA token or a typed
`@sha` tuple — the old bare `resolved=<40hex>` matched **neither** and extracted
nothing, so code closures had been recording a SHA their own consumer could not
read. A test in this directory binds the two formats together.

Tests: `python3 -m pytest ci-hub/closure/ -q`
