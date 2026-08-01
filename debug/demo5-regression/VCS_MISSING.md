# VCS_MISSING — demo5-regression

What is **not** checked in for this episode (won't exist on a fresh clone). Keep
current: whenever an artifact is left untracked (e.g. under `ignored/`), add a row.
`dbg vcs-check demo5-regression` lists the currently git-ignored paths.

| path | what it is | regeneratable? | how to regenerate | tracked code READS it? |
|---|---|---|---|---|
| `ignored/smoke/` | 226's raw smoke-run hermit logs (e.g. `smoke-norcb/info.log`) behind E12/E14 | yes | `./run_sweep.sh` / `./spin_sweep.sh` / `./preempt_timeline.sh` (re-run a `--no-rcb-time` demo5 boot at `--log info`) | no |
| `ignored/` (general) | any other raw logs/boot artifacts written here | yes | re-run the relevant boot | no |

## Artifacts referenced by this episode that live OUTSIDE this dir (also not on a fresh clone)

The `evidence.json` `artifact` fields and `NOTEBOOK.md` point at large raw traces
that are **gitignored at their source** and must be regenerated:

- `experiments/demo5-rootcause-20260731/log-science/sameconfig/…` and the raw
  `scratch/demo5-icount-sleep/out/wedge-{sock,filecon,off}-run1/hermit-info.log`
  (357/235 MB) — the H6 same-config A/B logs. Regenerate: the `boot_qemu_*.sh`
  scripts under `scratch/demo5-icount-sleep/`.
- `ignored/logs/demo5-{good,broken}-trace.log` (736 MB / 37 MB) — the cross-SHA
  good/bad traces. Regenerate: re-run demo5 at the GOOD/BAD SHAs with `--log info`.
- `experiments/demo5_bisect_20260731/ignored/…` — bisect boot logs.

## Runtime dependency

**None.** No tracked script or tool in this episode READS a missing artifact at
runtime; the CLI operates only on the tracked JSON. The missing paths are
human/evidence references (dangling on a fresh clone until regenerated), not code
inputs — so nothing here *fails* on another machine; it only loses the raw
evidence backing until re-run. (Re-confirm this line if any tracked script gains a
read of an `ignored/` path.)
