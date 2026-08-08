# ci-hub ledger health and deferred-log audit

- **Date:** 2026-08-08
- **Task:** `audit-ci-hub-ledger-health-and-deferred-logs`
- **Scope:** source and local-artifact audit only; no validation dispatch or product mutation
- **Audited tree:** dev-hermit `349d39cf8d9477f5c8e9cfb6811285e691fd77e9`, Hermit `592f7abd0ba29b77209d112c480711de3e8a766c`

## TLDR

| Question | Answer | Consequence |
| --- | --- | --- |
| Does `validate-run` reliably write a version-controlled ledger? | **No.** It writes a best-effort, gitignored machine-local JSONL file. | A successful default fresh-checkout launch guards against an invisible green, but the underlying write is neither version-controlled nor crash-durable, and most live rows lack producer provenance. |
| Can ledger history union multiple devservers? | **The library can; the deployed producer path cannot yet.** | Per-host tracked shards, deterministic union, and safe publication exist, but `validate-run` does not automatically import or publish into them. |
| Can it union GitHub CI and cache expensive queries with a TTL? | **No end-to-end path exists.** | Actions history, the validation-comment index, and local validation history are separate stores. The network readers have no shared TTL cache. |
| Is there one “was this commit green?” query across local, fleet, and GitHub CI? | **No.** | Existing commands answer different axes. A new read-only `commit-health` query should report each source, freshness, disagreements, and the policy-combined verdict. |
| Are centralized full logs useful for deferred determinism/parity? | **Yes, strongly recommended.** | Store immutable, compressed raw bundles under `ignored/validate-logs/`; recompute scorecards on demand; cache only digests and compact verdicts in the ledger; monitor and rotate by measured bytes. |

## 1. `validate-run` ledger write reliability

### Actual write path

The Rust front door dispatches `validate-run` to `ci-hub/validate/start_unit.py`
([`ci-hub/ci-hub.rs:1745`](../../ci-hub/ci-hub.rs#L1745)). The service binds
`DEV_HERMIT_PARENT` to the parent workspace, and Hermit resolves the canonical
live ledger to `ignored/validate-run-ledger.jsonl`
([`ci-hub/validate/start_unit.py:457`](../../ci-hub/validate/start_unit.py#L457),
[`hermit/validate.sh:556`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L556)).
The Rust reader uses that same single path
([`ci-hub/lib/validate_status.rs:80`](../../ci-hub/lib/validate_status.rs#L80)).

That ledger is **not version-controlled**: the parent ignores the entire
`ignored/` directory ([`.gitignore:119`](../../.gitignore#L119)). Read-only
checks confirmed that `ignored/validate-run-ledger.jsonl` is ignored and absent
from `git ls-files`.

The tracked global ledger is a different system. Its importer explicitly names
the ignored live file as its input and a tracked shard as a required separate
output ([`ci-hub/ledger/import_validate_runs.py:64`](../../ci-hub/ledger/import_validate_runs.py#L64),
[`ci-hub/ledger/import_validate_runs.py:197`](../../ci-hub/ledger/import_validate_runs.py#L197)).
No `validate-run` call automatically invokes that importer or its publisher.

### Atomicity and failure modes

Hermit installs an exit cleanup that normally appends a row on success,
ordinary failure, and handled `INT`/`TERM`/`HUP`
([`hermit/validate.sh:1855`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1855),
[`hermit/validate.sh:1913`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1913)).
It does not cover `SIGKILL`, host loss, or failure before trap installation.

The append obtains an advisory exclusive `flock` and writes one JSON line, but
does not use a temporary file, atomic rename, `fsync`, or directory sync. If
`flock` is unavailable, it performs an unlocked append
([`hermit/validate.sh:1759`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1759)).
Directory creation and append failures only print warnings; they do not change
the validation result. Readers skip malformed lines rather than failing the
whole ledger ([`ci-hub/ci-hub.rs:3343`](../../ci-hub/ci-hub.rs#L3343)).

The ordinary detached fresh-checkout path has a valuable compensating guard:
after the service completes, it re-reads the exact SHA/run row from the
canonical ledger before deleting the checkout
([`ci-hub/validate/start_unit.py:863`](../../ci-hub/validate/start_unit.py#L863)).
A missing qualifying row makes the caller return `2`. This does not make the
writer durable, and the guard is absent from `--in-place`; `--attach` returns
the service status without repeating it.

### Producer provenance

The shell ledger-row constructor contains no `producer` field
([`hermit/validate.sh:1729`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1729)).
The separate run-handle JSON says `systemd-user-v1`
([`ci-hub/validate/start_unit.py:755`](../../ci-hub/validate/start_unit.py#L755)),
while the qualifying-receipt registry expects `hermit-validate-sh`. Enforcement
is currently dormant because its activation epoch is null
([`ci-hub/validate/qualifying-receipt.json:37`](../../ci-hub/validate/qualifying-receipt.json#L37)).

Measured live state during this audit: **146 of 147 rows lacked `producer`**;
the sole stamped row was a later `ci-hub-finalize-receipt` clone.

### Finding

`validate-run` normally produces usable machine-local evidence, and the default
fresh-checkout caller refuses a missing green receipt. It does **not** reliably
write a version-controlled ledger. Required repair is:

1. make the live append fail-closed for an otherwise-green run;
2. add truthful producer provenance at the original writer;
3. use a durable append protocol (`flock`, complete single write, `fsync`) and
   retain a recoverable spool before publication;
4. automatically translate/spool qualifying events into the tracked per-host
   ledger without making Git publication part of validation success.

## 2. Multi-machine ledger union

### What is implemented

The tracked ledger layout is
`ledger/<team>/<short-host>/<YYYY>-<MM>.jsonl`; different machines normally
write different paths ([`ci-hub/ledger/README.md:1`](../../ci-hub/ledger/README.md#L1)).
`ledger.union(paths)` reads arbitrary shards, deduplicates identical event IDs,
refuses conflicting bodies, and deterministically orders the result
([`ci-hub/ledger/ledger.py:155`](../../ci-hub/ledger/ledger.py#L155)).

The publisher first spools events locally, merges concurrent remote appends,
refuses non-append changes, pushes without force, freshly fetches, and drains
the spool only after ancestry proves publication
([`ci-hub/ledger/README.md:43`](../../ci-hub/ledger/README.md#L43),
[`ci-hub/ledger/publisher.py:131`](../../ci-hub/ledger/publisher.py#L131)).
Mechanically, this is a sound multi-machine union design.

### What is not deployed

The live producer still writes the one legacy ignored ledger. The importer’s
own history explains that it originally had no entry point or scheduler, so the
tracked shard drifted about 28 hours behind and important evidence existed only
in the ignored file
([`ci-hub/ledger/import_validate_runs.py:2`](../../ci-hub/ledger/import_validate_runs.py#L2)).
Current `validate-status` and `newest-green` still resolve one legacy ledger path
([`ci-hub/ci-hub.rs:3336`](../../ci-hub/ci-hub.rs#L3336),
[`ci-hub/ci-hub.rs:4516`](../../ci-hub/ci-hub.rs#L4516)).

The tracked tree currently contains only the `devbig014` Hermit shard. The
schema design itself records that its measured corpus had one team and one
machine, so the first real second-machine rollout remains the important test
([`ai_docs/validate-ledger-team-machine-schema_20260807.md:380`](../validate-ledger-team-machine-schema_20260807.md#L380)).

### Why the prior degraded agent spun

There is no single freshness contract across:

1. the live ignored parent ledger;
2. the manually imported tracked shard;
3. the machine-wide reconstructed aggregate, which explicitly is not
   fleet-wide ([`ci-hub/validate/aggregate.py:2`](../../ci-hub/validate/aggregate.py#L2)); and
4. hosted GitHub authority.

An agent trying to establish “ledger truth” must repeatedly reconcile these
independently fresh views. That is an architectural ambiguity, not merely a
slow query. The multi-host library supports union; the running producer and
consumer path does not yet make that union authoritative or current.

## 3. GitHub CI union and TTL caching

There are two separate GitHub-related stores:

- `ci-hub/history/ingest.py` incrementally UPSERTs GitHub Actions runs into
  `ignored/ci-hub/gha-runs.csv`, with a cursor and an offline reader
  ([`ci-hub/history/README.md:24`](../../ci-hub/history/README.md#L24),
  [`ci-hub/history/README.md:50`](../../ci-hub/history/README.md#L50)). This is an
  explicitly refreshed local snapshot, not TTL-on-query caching.
- `ci-hub/ledger/github_index.py` stores receipt-backed validation events in
  commit comments. It is a cache/index for local validation events, **not** the
  GitHub Actions authority ([`ci-hub/ledger/README.md:100`](../../ci-hub/ledger/README.md#L100)).
  Every `fetch()` performs a GitHub GET and has no TTL layer
  ([`ci-hub/ledger/github_index.py:318`](../../ci-hub/ledger/github_index.py#L318)).

The index can fold receipt-verified GitHub-published validation events together
with local shard events
([`ci-hub/ledger/github_index.py:471`](../../ci-hub/ledger/github_index.py#L471)).
That answers “was this commit locally validated anywhere whose event was
published?” It does not ingest required GitHub Actions jobs. Source search found
no front-controller wiring for `github_index.py`, `import_validate_runs.py`, or
`publisher.py`, despite the modules and tests existing.

### Finding and cache recommendation

There is no end-to-end `green-history` union of fleet-local validation and
GitHub Actions, and no shared TTL cache for expensive exact-SHA queries.

Use one exact-SHA hosted cache keyed by:

`(repo, commit, required-workflow-policy-version)`.

It should carry fetch time, selected workflow/run/job IDs, latest-attempt
identity, classifications, and discarded-record denominators. Pending,
queued, and `NO_RESULT` entries require a short configurable TTL; terminal
entries may use a longer configurable TTL but are not immutable because a
workflow can be rerun at the same SHA. Support explicit `--refresh`, conditional
requests/ETags where GitHub permits them, and never let cache absence or expiry
become green.

The existing `gha-runs.csv` and job rows should be the persisted cache substrate
rather than introducing another independently maintained store.

## 4. Combined “was this commit green?” query

No current read-only command combines all three requested authorities.

- `ledger.bisect_verdict()` considers the latest validation run per commit in
  the event union only ([`ci-hub/ledger/ledger.py:249`](../../ci-hub/ledger/ledger.py#L249)).
- `github_index.merge_with_local()` adds receipt-backed local validation events
  from GitHub, but no Actions jobs
  ([`ci-hub/ledger/github_index.py:483`](../../ci-hub/ledger/github_index.py#L483)).
- `hosted-status` computes hosted required-job authority separately.
- Obligation evaluation can combine legacy local and hosted outcomes, but it is
  an obligation workflow, not an arbitrary read-only SHA query, and it still
  reads the single legacy ledger.

### Recommended interface

Add:

```text
ci-hub commit-health --repo OWNER/REPO --sha 40HEX [--refresh] [--json]
```

Return evidence axes, not only a boolean:

- `local_live`: exact matching legacy row and qualification reason;
- `fleet_ledger`: tracked-shard union, event/run counts, contributing hosts,
  latest result, and publication freshness;
- `github_validation_index`: accepted/rejected receipt-backed events and
  rejection reasons;
- `github_actions`: required workflows/jobs, latest attempts, conclusion,
  cache age/TTL, and live-vs-cache provenance;
- `combined`: the versioned policy verdict and a disagreement list.

The command must preserve `PASS`, `FAIL`, `NO_RESULT`, and `NO_DATA`; report the
source timestamp and denominator beside each value; and never let one source
silently outrank another. The policy layer may accept either qualifying local
or hosted positive where repository policy allows it, but a genuine product red
must remain visible and blocking.

## 5. Deferred full-log determinism and backend-parity plan

### Is it useful?

**Yes.** It is the right way to make determinism/parity results re-auditable and
recomputable without putting enormous logs into Git.

Today, Hermit creates its full transcript in unmanaged `/tmp`
([`hermit/validate.sh:1160`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1160))
and stores only the absolute path in the ledger
([`hermit/validate.sh:1757`](https://github.com/rrnewton/hermit/blob/592f7abd0ba29b77209d112c480711de3e8a766c/validate.sh#L1757)).
The detached wrapper separately writes a small outer log under
`ignored/validate/` ([`ci-hub/validate/start_unit.py:733`](../../ci-hub/validate/start_unit.py#L733)).
There is no validate-log size/retention monitor; current tick disk controls cover
worktree-slot residue, not this evidence
([`ci-hub/health/tick-hub.yaml:74`](../../ci-hub/health/tick-hub.yaml#L74)).

Measured during this audit:

- `/tmp/hermit-validate.*.log`: **135 files, 401,916,526 bytes**;
- largest single full log: **344,282,563 bytes**;
- detached unit logs: **5 files, 7,243 bytes**;
- publication-copied validation evidence: **29 logs, 18,681,264 bytes**.

Current parity collectors also overwrite or discard raw operands. The strict
comparator is already designed to recompute from raw combined streams, rejects
empty evidence, and can derive detlog/stack/heap relationships
([`compat-envelope/strict_verdict.py:116`](../../compat-envelope/strict_verdict.py#L116),
[`compat-envelope/strict_verdict.py:161`](../../compat-envelope/strict_verdict.py#L161)).
It also warns that cross-backend record counts differ, so a universal DETLOG
equality boolean would be unsound
([`compat-envelope/strict_verdict.py:192`](../../compat-envelope/strict_verdict.py#L192)).

### Canonical location and bundle

Use one gitignored store per devserver parent:

```text
ignored/validate-logs/v1/<short-host>/<commit>/<run-id>/
```

“Centralized” here means one authoritative directory on each machine. Fleet-wide
raw-log retention requires an artifact/object store; raw logs should not be
committed to Git. The tracked ledger can union their compact manifests and
verdicts.

Each run bundle should contain:

- `manifest.json`: exact Hermit/Reverie/tree SHAs, host, producer/version,
  profile, argv, selected/executed counts, declared coverage, approved
  environment subset, timestamps, and every artifact’s size and SHA-256;
- `validate.log.zst`: full outer validation transcript;
- per test/backend/attempt: `command.json`, raw `stdout.zst`, raw combined
  `stderr-info.zst`, `verify.json`, and `exit.json`;
- run A and run B for ptrace and every measured backend, with `--log=info` and
  stack/heap capture whenever that comparison tier is claimed;
- derived `summary.json` and `scorecard.csv`, explicitly marked as recomputable
  cache rather than raw authority.

Do **not** prefilter DETLOG lines before storage. Preserve the full ordered raw
stream and let the versioned comparator perform extraction.

### Write, recompute, and ledger semantics

1. Stream into a unique `<run-id>.partial/` directory.
2. After all children close, compress and hash every file, `fsync` files, write
   and `fsync` the manifest, then same-filesystem rename to the final directory.
3. A missing/incomplete bundle remains `.partial` and yields parity
   `NO_RESULT`; log capture failure must not manufacture green.
4. Add a pure reader:

   ```text
   ci-hub parity recompute --bundle PATH [--comparator COMMIT]
   ```

   It reads only the bundle, runs the versioned strict comparator, and writes
   derived output inside that bundle. Updating tracked
   `compat-envelope/*.csv` remains a separate explicit publication step.
5. Append a compact `run.enrich` ledger event containing `bundle_id`, manifest
   digest, comparator version/blob SHA, summary digest, result, and denominators.
   Never copy full logs or silently overwrite an earlier verdict. Cache validity
   is `(manifest digest, comparator version, policy version)`.

### Size monitoring and rotation

Add `ci-hub validate-logs audit` to health monitoring. It should report actual
allocated bytes, file/bundle count, largest and oldest bundle, incomplete
partials, compression ratio, and configured soft/hard caps. Reuse the existing
actual-block disk-accounting approach rather than apparent file length.

Rotation must be scoped to this exact root, locked, and explicit. Never remove
an active `.partial` bundle. Pin:

- receipt-referenced bundles;
- the newest qualifying green per relevant policy/profile;
- the latest red and unresolved disagreement;
- owner-promoted certification or benchmark evidence; and
- any run named by an open remediation obligation.

Rotate only finalized, unpinned oldest bundles after configurable retention and
byte thresholds. Record retirement as a ledger event so a later query can say
“verdict retained, raw bundle retired” rather than presenting a broken path.
Choose numeric caps from measured compressed production rate and free-space
headroom; the observed 344 MB single log makes compression and byte caps
mandatory.

## Recommended implementation order

1. **Reliable local evidence:** producer provenance, fail-closed green append,
   durable spool.
2. **Operational fleet union:** automatic import/spool, safe tracked-shard
   publication, and freshness reporting; validate on a real second machine.
3. **One read-only query:** `commit-health`, incorporating the Actions cache and
   preserving all source disagreements.
4. **Deferred raw-log store:** atomic compressed bundles, pure recomputation,
   compact ledger enrichment, then monitored retention/rotation.

This order removes the current truth ambiguity first. The deferred-log mechanism
then adds richer auditable evidence without making large artifacts or scorecard
side effects prerequisites for ordinary validation.
