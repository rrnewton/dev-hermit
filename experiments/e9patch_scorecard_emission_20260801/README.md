# e9patch preprocessing-invariance → compat scorecard emission (honest L1 vs L2)

## Question

The owner asked for e9patch data in the compat-envelope scorecard, with an honest
L1-vs-L2 distinction (never report an L1 result as L2). e9patch is **not** a
Detcore backend (`hermit/AGENTS.md`) — it is binary-rewriting *preprocessing*
applied to a guest ELF and then run under the ptrace backend. So the honest
question is **preprocessing-invariance**, not cross-backend parity:

> For each freestanding raw-syscall corpus guest, is the e9tool-rewritten ELF's
> output under the ptrace backend bitwise-identical (L2) to the same guest run
> under ptrace **without** rewriting (the golden reference)?

Both arms run the ptrace Detcore backend; only the AOT e9tool rewrite differs.

## Method

`compat-envelope/collect-e9patch-compat.rs` writes the same 19-column CSV schema
as the other collectors, into a **dedicated** `compat-envelope/e9patch-scorecard.csv`
(mirroring how reverie tool-parity lives in its own `reverie-scorecard.csv`),
never as a column in the backend `scorecard.csv` — a literal `e9patch` token in a
backend field would misread as a Detcore backend and violate the #152
anti-fakery gate.

Two arms per guest:

- **ptrace** — golden, un-rewritten reference arm (the denominator; `parity`
  blank/None).
- **e9patch** — e9tool-rewritten variant arm; `parity` = e9 stdout == golden
  stdout, both under ptrace.

Honest L1 vs L2:

- `deterministic=1` **iff** the arm reached **L2**: `hermit run --strict --verify`
  printed "Determinism verified" and exited cleanly.
- An arm that ran under `--strict` (**L1**) but whose `--verify` leg did not
  confirm a bitwise repeat is `deterministic=0` with a `reason` distinguishing a
  verify **wedge** (PMU env, `outcome=l1`, not a regression) from a genuine
  **divergence** (`outcome=diverge`, a real finding). L1 is never reported as L2.
- Both the strict and verify legs are retried (default 3) on wedge/skid under
  fleet PMU load. If even the strict leg never clears, the arm is unmeasurable env
  noise: `outcome=skip` with **blank** determinism — never a confirmed red. Only a
  real non-124 strict exit is `outcome=fail`.
- `--assert-green` counts only parity divergences and real run failures as
  regressions; env wedges are reported, not failed.

## Results

At hermit `b1fdeaf6d7bc` / reverie `2112c0045f25`, run_id `e9patch-20260801`:

- **227/227** corpus guests reach **L2 preprocessing-invariance**.
- e9patch arm: **100% parity, 100% determinism** (all `det=1, par=1`).
- golden ptrace arm: 227 `pass`, `det=1`, parity blank (reference denominator).
- **0** parity divergences, **0** run failures; `--assert-green` = GREEN.

Rendered table:

```
bucket                  ptrace           e9patch
------------------------------------------------
e9patch-corpus             227        100%, 100%
------------------------------------------------
TOTAL                      227        100%, 100%
```

The CSV (454 data rows + header) is committed at
`compat-envelope/e9patch-scorecard.csv`; the collector at
`compat-envelope/collect-e9patch-compat.rs`; docs in `compat-envelope/README.md`.
Parent commit `15d4726` on `rrnewton/dev-hermit:main`.

## Reproduction

```bash
cd ~/work/dev-hermit/compat-envelope
export HERMIT_E9TOOL=../worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=../worktrees/e9patch/reverie/third-party/e9patch/e9patch
./collect-e9patch-compat.rs --csv e9patch-scorecard.csv \
    --run-id e9patch-$(date +%Y%m%d) --assert-green
./render-scorecard.rs --csv e9patch-scorecard.csv --backends e9patch --latest
```

Requires a hermit built `--features e9patch` (defaults resolve to the
`worktrees/e9patch` checkout) plus `e9tool`/`e9patch` on disk. See
`metadata.json` for exact SHAs and environment.
