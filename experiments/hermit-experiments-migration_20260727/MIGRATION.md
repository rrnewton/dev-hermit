# Hermit Experiment Migration

This directory preserves the tracked `experiments/` tree removed from
`rrnewton/hermit` at Hermit commit
`2b38e75d8a35a72f8b6bbfceeaa09a0cbf0fe6c4`.

## Scope

- Source: `hermit/experiments/`
- Tracked source files: 933
- Preserved files from the source tree: 664
- Original tracked tree size: approximately 3.5 MiB
- Migration date: 2026-07-27
- Parent destination: `dev-hermit/experiments/hermit-experiments-migration_20260727/`

The export was produced with `git archive` from the committed Hermit tree, so
ignored build output and untracked machine-local files were not copied.

## Artifact Exclusion

The tracked binary
`arbitrary-binaries-wave2_20260721/fixtures/wave2.jar` was intentionally
excluded because binaries do not belong in the parent repository. Its source
remains in `arbitrary-binaries-wave2_20260721/fixtures/Wave2.java`.
The binary remains recoverable from the cited Hermit commit and has:

- Git blob: `6c1a777468f88a466d02b463290c08cb20bf6e4a`
- SHA-256: `a35dbed470e7a8206b31002e9829496a0aaddfcc077ed95a8d22abcc5e81200a`

The `stress_20260721/` tree also contained 268 generated `stdout` and `stderr`
captures with embedded NUL bytes (90,863 bytes total). Those binary logs were
excluded; their textual summaries and observations remain preserved. Every
omitted path is recoverable from the cited Hermit commit.

## Use

Each child directory retains its original README, scripts, fixtures, manifests,
and textual results. Commands referring to paths inside the Hermit repository
must be run from this migration directory or updated to use an explicit Hermit
checkout path. Treat the cited Hermit SHA as the source and behavior provenance.
