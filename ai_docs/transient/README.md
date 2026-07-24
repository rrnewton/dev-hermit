# Transient docs (point-in-time)

Session-scoped or snapshot documents: profiles, one-off measurements, and
effectiveness/analysis captures that reflect a specific run or moment and go
stale quickly. Keep them here so the durable `reference/` set stays clean.

Truly disposable output (logs, binaries, cores, coverage dumps) still belongs
in ignored `scratch/` or external artifact storage, not here.

Recommended homes for existing top-level `ai_docs/` files (moves deferred:
`ai_docs/*` research files are owned in-flight by `slot11` / `hermit-docs`; do
not relocate them until that task is idle):

- `performance-profile.md`
- `intel-pmu-analysis.md`
- `chaos-effectiveness.md`
