# Bounding the verify-strict INFO-scope artefact, without waiting for the fix

**Task:** `rederive-bitwise-parity-after-verify-fix` · **Agent:** hermit-w2 · **2026-08-07**
**Backend:** ptrace · **Host:** devbig014 · **Slot:** `worktrees/w2/hermit`
**Hermit:** stock tree, no local edits · **fresh main** `0041130ccb0daa54ffe7dce2792c1f1495c57e58`

## The task could not be run as written

It begins *"After `fix-verify-strict-compare-info-only` lands"*. **It has not landed.**

```
gh pr view -R rrnewton/hermit 1692 --json state,isDraft,mergedAt,mergeCommit
  {"state":"OPEN","isDraft":true,"mergedAt":null,"mergeCommit":null,
   "headRefOid":"c01d247405697c8c8c11489948fdcd6b67ed72e8","baseRefName":"main"}

git show origin/main:detcore/src/logdiff.rs | sed -n 790p
      let infos_a = filter_infos(&all_a);
```

The residual defect is on main verbatim. Checked at three SHAs (`1fadc0377`, `a8951eff`, `0041130c`) to
rule out a revert: all identical, and `git log -S` shows the form arrived with `38cf53737` (#1661) and was
never changed. Re-running the corpus under the unfixed comparator would reproduce the very number the
task exists to replace, so no corrected count is published here.

## What is measurable anyway

The artefact/divergence split is decidable **offline** from one retained log pair. Both selections are
replicated from their source predicates and applied to the same pair; the difference *is* the artefact.

| selection | source | definition |
| --- | --- | --- |
| SHIPPED | `logdiff.rs:790` | `filter_infos(&all_a)` |
| PR #1692 | branch `fix/verify-info-scope-guest-only` | `filter_infos(&detcore_a)` |

Two separate single runs of a trivially deterministic guest (`printf("constant-output")`), captured at
`--log=info` with `--log-file`, 72 raw lines each.

## Result

```
SHIPPED  filter_infos(&all_a)        left=  56 right=  56 divergent=0
PR#1692  filter_infos(&detcore_a)    left=  54 right=  54 divergent=0

non-guest INFO lines admitted by the shipped selection: 2 of 56
    INFO reverie_ptrace::task: [tool] (tid 3) beginning tail_inject of syscall: exit_group
    INFO hermit::backend_stats: backend run complete backend=ptrace stats=metrics=none
```

This independently reproduces the predecessor task's count (2 non-guest lines) and pins the denominators.

## The delta the task asked for

**Same-backend (what produced all 346 cells): the artefact contributes ZERO divergence — measured.** Both
selections score 0 divergent on the control pair. The two admitted non-guest lines are byte-identical
across a ptrace-vs-ptrace double-run, so they cannot manufacture a failure there.

**Cross-backend: exactly one line diverges by construction — source-derived, not measured here.**
`hermit::backend_stats: ... backend=ptrace` embeds the backend name, so any dbi/kvm/sabre-vs-ptrace
comparison diverges on that line regardless of guest behaviour. The predecessor checked ptrace-vs-e9patch
and found both print `backend=ptrace` (e9patch runs the ptrace runtime), so the defect is latent for that
pair and live for the others.

### Therefore

> **The artefact share of the reported 0-of-346 is 0.** Every one of the 346 came from a *same-backend*
> `hermit run --verify` double-run, and on same-backend pairs this artefact is provably silent.

The 0/346 is not explained by the verify-strict INFO-scope defect. It is explained by the producers
selecting the **`stripped`** comparator, which reports `bitwise_parity: false` by construction — a
different defect, already established in
`experiments/strict-certification-mutation-sweep_20260806` and re-confirmed in
`experiments/info-tier-premise-recheck_20260806`, where `--verify-strict` on the same control certifies at
`bitwise_parity: true`, 56\|56.

So on the question the task poses — *are the backends closer or further from parity than we have been
claiming?* — this artefact moves the answer **neither way** for the 346. It is a real defect worth landing
(#1692), but its blast radius is cross-backend comparison, which is not what produced those cells.

## Scope and limits

- **ptrace only, one guest, one run pair.** The same-backend zero is measured; the cross-backend claim is
  derived from the line's content, not executed.
- Two separate single runs, not `--verify`'s internal double-run. Chosen so the same pair can be scored
  twice offline; the prior experiment cross-checked that the two agree at INFO.
- This is a **bound**, not the corrected corpus count. That count remains blocked on #1692.

## Reproduction

```bash
cd experiments/verify-info-scope-artefact-bound_20260807
python3 score.py logs/run1.log logs/run2.log
```

To regenerate the logs (hermit built from a stock tree at the SHA in `metadata.json`):

```bash
gcc -O0 -static -o /tmp/clean_ctrl \
  ../info-tier-premise-recheck_20260806/mutants/clean_ctrl.c
for i in 1 2; do
  hermit --log=info --log-file=logs/run$i.log run --strict --base-env=minimal \
    --max-timeslice=disabled --tmp=/tmp -- /tmp/clean_ctrl
done
```
