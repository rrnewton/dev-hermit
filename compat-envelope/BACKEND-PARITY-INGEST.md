# Backend-parity bucket → scorecard ingest (coordination handoff)

**From:** dbt ratchet lane (`dbt-corpus-round-nongated-3`, impl agent opus-4.8)
**To:** compat-scorecard owner (agent hermit-235)
**Producer:** `collect-backend-parity.rs` (additive; does not touch the three
existing collectors or `scorecard.csv`)

## Why this exists

The cross-backend parity contracts in `hermit/tests/backend-parity/`
(`matrix.tsv`, enforced by `run_matrix.py` and `hermit/ci/dag/portable.json`)
are their **own** CI-gated ratchet. They never flow through
`hermit/ci/test_harness.sh`, so `collect-envelope.rs` never sees them and their
green cells never reach `scorecard.csv`. This producer bridges that gap.

## What it does

`collect-backend-parity.rs` reads the authoritative 11-column `matrix.tsv` and
emits rows in the **exact** scorecard schema (byte-identical header, verified
via `diff`), tagged `bucket = backend-parity`:

```
./collect-backend-parity.rs            # → ignored/backend-parity-scorecard.csv
./collect-backend-parity.rs --stdout   # → stdout
```

- One matrix row → up to 6 scorecard rows: `{ptrace,dbi,kvm}` × `{L1,L2}`.
- `test_mode`: **L1 → `strict`** (`hermit run --strict`, 3× byte-identical
  stdout); **L2 → `verify`** (`hermit run --strict --verify`).
- `test_id`: `backend-parity/<test_name>`.
- `lane`: `portable` for ptrace/dbi, **`privileged` for kvm** (needs `/dev/kvm`).
- `outcome`: `pass` for a `pass`/`detlog`/`guest` cell, `gap` otherwise;
  `deterministic`/`parity` blank+`0` on a gap, `1`/`1` on a pass.
- **Anti-fakery (#152):** KVM's L2 is **guest-visible only** (stdout+exit
  compared, internal DETLOG trace NOT compared). That weaker assurance is
  written verbatim into the `reason` column
  (`L2 guest-visible only (...)`) so the scorecard can never present KVM
  guest-visible L2 as full DETLOG determinism. ptrace/DBI L2 rows carry
  `L2 DETLOG-bitwise (...)`.
- `output_hash`/`duration_ms`/`max_rss_kb` are blank: these are static ratchet
  claims, not a timed live run.

## How to fold it into the master scorecard (owner action)

The renderer keys logical cells on `(bucket, test_id, test_mode, backend)`
(README §rendering), and `backend-parity` is a brand-new bucket, so there is
**zero collision** with existing cells:

```bash
./collect-backend-parity.rs
tail -n +2 ignored/backend-parity-scorecard.csv >> scorecard.csv
./render-scorecard.rs --csv scorecard.csv          # backend-parity bucket appears
```

The producer writes only to `ignored/` (gitignored raw output); folding into
`scorecard.csv` is deliberately left to you so it never races your active edits.

## Current numbers @ hermit `82a8e853` (origin/main, 23-row matrix)

138 rows. Ratchet mirrored exactly:

| backend | L1 (strict) | L2 (verify) | L2 kind |
| --- | --- | --- | --- |
| ptrace | 23/23 | 23/23 | DETLOG-bitwise |
| dbi | 22/23 | 21/23 | DETLOG-bitwise |
| kvm | 22/23 | 21/23 | guest-visible only |

L1 gaps: dbi `pthread_lifecycle`; kvm `process_wait_lifecycle`. Extra L2 gaps:
dbi `exit_status`; kvm `process_wait_accounting`.

## Pending: pidfd_open_self (PR #1393, not yet merged)

The dbt lane's batch-93 contract `pidfd_open_self` (TRIPLE-PASS support,
`pass/pass/pass` L1, `detlog/detlog/guest` L2) is open at
<https://github.com/rrnewton/hermit/pull/1393>. When it lands, the matrix
becomes 24 rows and each backend gains +1 at both levels (144 producer rows).
Just re-run the producer against updated `../hermit` — no converter change
needed; it auto-guards the schema and will refuse to run if the 11-column layout
ever changes again.
