# Active Coordinator Checkouts

The coordinator keeps fixed checkouts for the reconstituted frontier and the
monotonic main branches. These are shared integration surfaces, not feature
development slots.

| Path | Repository | Branch | Purpose |
| --- | --- | --- | --- |
| `hermit/` | Hermit | `frontier` | Frontier integration, execution, and validation |
| `reverie/` | Reverie | `frontier` | Frontier dependency integration and validation |
| `main/hermit/` | Hermit | `main` | Monotonic main updates and rebase base |
| `main/reverie/` | Reverie | `main` | Monotonic main updates and rebase base |

Physical worktrees under `main/` are machine-local and ignored by the parent
repository. Feature work remains isolated in assigned slots under `worktrees/`.
