# Prefix-parity rungs: harnesses and raw results

**Agent:** hermit-det4 · **2026-08-06** · local, no egress.
**Anchor:** hermit `4c70658e785834737cbe1524f77330c781a6f5ea`, reverie pin `dd3c178`,
binary `hermit 0.2.0 (2026-08-06, g4c70658e7858)` release `--features third-party-backends`,
ptrace, `--strict`, relaxations none.

These are the harnesses and raw results behind three landed artifacts. They lived under
`ignored/` during the work, which is gitignored — they are copied here so the reproduction
commands in those artifacts survive.

| artifact | content commit |
| --- | --- |
| `ai_docs/prefix-parity-depth-remeasured_20260806.md` | `699963ae4aebdf8231e7320d850366aef09a3245` |
| `ai_docs/ptrace-golden-self-determinism-per-rung_20260806.md` | `20fa5c5398209ed959f436f2736d2d9c44fbc3ba` |
| `ai_docs/rung-ladder-bracketing-the-record-gap_20260806.md` | `c08e259468956574da3b8bd0644182a5b7b65ab1` |

## Question

How many leading INFO-log records does a backend keep identical to the ptrace golden (`Y/Z`),
at rungs spanning the record-count range — and does the golden itself reproduce at each rung?

## Contents

| file | what it does |
| --- | --- |
| `parity-depth.sh` | per-rung prefix-parity depth, sabre and dbi vs the ptrace golden; enforces the golden self-check first |
| `golden-selfdet.sh` | ptrace golden self-determinism, n runs, three-valued verdict |
| `rung-sizing.sh` | sizes candidate guests (how many detcore records each produces) |
| `rung-qualify.sh` | n=3 self-determinism for the sized candidates |
| `d5-selfdet.sh`, `d5-controlled.sh` | demo05 self-determinism attempts (unresolved — see the artifact) |
| `*.tsv` | raw results |

## Dependency

All four drivers invoke the cross-backend DETLOG diff harness, which is **product code and is not
vendored here**: `scripts/cross-backend-detlog-diff.rs`, hermit PR
[#1709](https://github.com/rrnewton/hermit/pull/1709), branch
`feat/cross-backend-detlog-diff-harness` @ `bc461a2608e2d7dca2f56293312e9bc2aa270182`. The
`HARNESS=` line at the top of `parity-depth.sh` points at a worktree path and must be repointed.

## Method notes that matter

* Only the real wall-clock prefix is stripped. Virtual time, RCB counts, syscall arguments,
  results, sizes and flags are compared verbatim. Nothing was loosened to produce a green.
* Verdicts are three-valued: **IDENTICAL / FAIL / TOOL-ERROR**. A crash, timeout, or zero-record
  run is TOOL-ERROR and never FAIL. This caught three false results.
* Guests are passed as argv arrays; a shell is used only where the rung genuinely needs one.
* `PYTHONDONTWRITEBYTECODE=1` throughout — the shared `__pycache__` otherwise varies between runs.
* Record counts for the `tar`/`grep`/`find` rungs traverse `/usr/include` and `/usr/lib64` and are
  **not portable constants**.
