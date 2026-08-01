# Reference docs (durable)

Long-lived architecture, design, roadmap, strategy, and assessment documents
that stay useful across many sessions. These are the canonical references an
engineer returns to; they are not point-in-time measurements.

Put a document here when it describes *how the system is meant to work* or
*where it is headed*, e.g. design proposals, backend assessments, roadmaps,
strategy notes, setup procedures, and stable coverage maps.

Recommended homes for existing top-level `ai_docs/` files (moves deferred:
`ai_docs/*` research files are owned in-flight by `slot11` / `hermit-docs`; do
not relocate them until that task is idle):

- `hermit-v2-roadmap.md`
- `kvm_backend_design.md`
- `sabre_backend_assessment.md`
- `sabre-determinism-analysis.md`
- `scx-sim-replay-strategy.md`
- `qemu_vng_setup.md`
- `syscall-coverage-map.md`
- `arbitrary-binary-matrix.md`
- `nondeterministic-preemption-record-replay.md`
- `test-coverage-status.md`

## Staleness machinery

A reference doc rots as the code it describes moves. To detect and drive
updates, a doc may opt in to machine-readable staleness tracking by starting
with a YAML front-matter block:

```yaml
---
doc_type: reference
title: "DetCore Virtual Time Architecture"
last_updated: 2026-08-01          # ISO date the doc was brought current
tracks_repo: hermit               # repo the SHA + watch_files belong to (hermit|reverie)
tracks_sha: <40-hex commit>       # the commit the doc is current as of
watch_files:                      # repo-relative paths/dirs the content depends on
  - detcore/src/scheduler.rs
  - detcore/src/scheduler/        # a trailing slash means "recurse this directory"
  - detcore/src/time.rs
staleness_max_days: 30            # optional soft age threshold (default 30)
---
```

Required keys: `last_updated`, `tracks_repo`, `tracks_sha`, and a non-empty
`watch_files`. Docs without front-matter are left untouched.

Two rust-scripts operate on this:

- **`check-staleness.rs`** — scans this directory and prints one line per doc:
  its verdict (`FRESH`, `STALE-AGE`, `STALE-DRIFT`, `DIVERGED`, `UNKNOWN-SHA`),
  age in days, and how many intervening commits touched its watch-files. Use
  `--fail-on-stale` as a CI gate.

  ```bash
  ai_docs/reference/check-staleness.rs                 # dashboard
  ai_docs/reference/check-staleness.rs --json          # machine-readable
  ```

- **`update-driver.rs <doc.md>`** — for one doc, computes the full *light cone*
  of change and crafts an agent prompt to update it. Starting from the
  watch-files, it finds the intervening commits, then EXPANDS the causal set two
  ways: partial Rust parsing of `mod` declarations (both those present at HEAD
  and those *added* within the window — the precise "a new file entered the
  tree" signal that catches refactors), and co-change coupling (files that
  changed in the same commits as a watch-file). The resulting superset is
  winnowed to same-crate source, ranked, and offered as proposed watch-list
  additions; the generated prompt asks the updating agent to winnow it further
  with judgment. `--json` emits the analysis; `--emit-prompt PATH` writes just
  the prompt.

  ```bash
  ai_docs/reference/update-driver.rs detcore_vtime_architecture.md
  ai_docs/reference/update-driver.rs detcore_vtime_architecture.md --json
  ```

Verdicts: `STALE-DRIFT` (watch-files changed since `tracks_sha`), `STALE-AGE`
(older than `staleness_max_days` with no code drift), `DIVERGED` (`tracks_sha`
is no longer an ancestor of HEAD — a rebase), `UNKNOWN-SHA` (`tracks_sha` is not
a commit in the repo — fix the front-matter or fetch), `FRESH` otherwise.
