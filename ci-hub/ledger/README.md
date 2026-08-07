# Validation ledger

Append-only, Git-backed history of validation runs, sharded per team and
machine. Schema: `ai_docs/validate-ledger-team-machine-schema_20260807.md`.

```
ledger/<team>/<short-host>/<YYYY>-<MM>.jsonl
```

Two machines never write the same path, so the usual case merges trivially in
Git. The only contended file is one host's current month, and local `flock`
serialises that. The host component is a **short** name: an FQDN is an
infrastructure detail that does not belong in a tracked file, and since the
path is also the authority for the event's `host` field, the two cannot
silently disagree.

## Modules

| module | role |
| --- | --- |
| `ledger.py` | schema semantics — parse, lint, union, fold, query, append |
| `publisher.py` | getting events into shared Git history without losing any |

`ledger.py` and its 38 invariant tests landed first, in
`ci-hub/ledger: deterministic global union and green/timeline/bisect queries`.
This document and `publisher.py` were added on top of it.

`validate_event` is the **linter** and `fold` is the **reader**, deliberately
separate. The linter refuses an enrichment that overwrites a set value; the
reader applies whatever is in the log, in order, without re-litigating it. A
reader that enforced the lint could not replay history that was already
written — and the real corpus contains an eleven-deep enrichment chain.

## Events, not rows

Committed bytes are never edited. A later fact about an earlier run is a new
event referencing it: `run.enrich` supplies something absent, `run.correct`
changes something wrong. So a correction never destroys what was believed at
the time — which is what makes `timeline()` meaningful, and what makes a
rewritten or deleted line a *detectable violation* rather than an ordinary
update.

## Publishing

```python
from publisher import spool, publish
spool(spool_dir, team, host, events)     # durable locally, before any Git work
publish(repo, spool_dir)                 # returns commit + ancestry evidence
```

Three properties, in the order they matter:

1. **A local event is never lost.** Events hit the spool before any Git
   operation, and a spool entry is dropped only once its commit is confirmed to
   be an *ancestor of the freshly-fetched branch*. A push exit code is not that
   confirmation: a push can succeed against a ref that is then replaced.
2. **A concurrent publisher never costs a row.** A rejected push is retried
   with an **append-aware merge** — re-read the remote shard, keep every remote
   line in place, re-append only what is not already there. Never
   `checkout --theirs`, never a whole-file replacement.
3. **No row is ever rewritten.** `verify_append_only` runs against the remote
   content before each push; a non-extension is refused rather than forced.

## Tests

```
python3 -m pytest ci-hub/ledger/tests/ -q
```

`test_ledger_invariants.py` (38) pins the schema semantics and was written
before this implementation. `test_ledger_publisher.py` (13) pins the publishing
behaviour the schema does not describe.

Three of those publisher tests exist because a **mutation sweep** caught tests
that passed without exercising what they claimed.

* **The truncation check was unexercised.** `test_truncated_final_line_...`
  passes even with the check disabled, because its fragment is also invalid
  JSON and `parse_line` rejects it first. Measured: disable
  `read_shard`'s newline check and all 38 invariant tests still pass, while
  `test_a_valid_final_line_without_a_newline_is_still_truncated` fails. A final
  line that is *valid JSON but unterminated* is the only case that binds it,
  and a writer killed between its write and its newline produces exactly that.
* **The push retry was unexercised.** The pre-push fetch means an ordinary
  concurrent append never actually causes a rejection, so removing the retry
  left the suite green.
* **The ancestry gate was unexercised.** Nothing in the happy path can make
  ancestry disagree with a successful push.

The last two are now forced deterministically with server-side hooks on a bare
test remote: an `update` hook that rejects the first push, and a `post-receive`
hook that resets the branch after accepting one.

If you change this code, re-run the sweep: plant a defect and confirm a test
goes red. Currently caught, measured against the landed code — drain-before-
ancestry, no-retry-on-rejection, re-appending an already-published event,
skipping validation before publish, accepting a truncated remote shard, and
disabling the newline check.
