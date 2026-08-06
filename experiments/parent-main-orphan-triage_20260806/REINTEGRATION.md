# Reintegrating the 45 rescued orphan commits

Rescued to `rescue/orphan-<sha>` and verified at the remote, so they are safe from gc. Safe is not
integrated. This is the content triage: which of them still represent work that main does not have.

**Method, and why SHA is useless here.** The incident rewrote SHAs, so `merge-base --is-ancestor`
on an orphan SHA reads NOT-LANDED even when the content *is* on main. Every row below is classified
by CONTENT, in this order, and the method is recorded per row:

1. `git patch-id --stable` against an index of all 572 main commits since 2026-07-20. Patch-id is
   invariant under rebase and cherry-pick, which is exactly the transform this incident applied.
2. Failing that, per-path blob comparison: is each touched path byte-identical on main?
3. Failing that, **added-path presence**: does a file the commit *created* exist on main at all?
   This step matters — step 2 alone misclassifies a commit that adds a new file while also touching
   files that main still has, and it initially lost the QEMU demo for exactly that reason.

## Result: 45 rows

| bucket | rows | meaning |
| --- | ---: | --- |
| `a-already-on-main` | 4 | equivalent patch, or every touched path byte-identical |
| `a-already-on-main-evolved` | 19 | content is on main and has since been changed further |
| `b-genuinely-missing` | 15 | adds a file main does not have |
| `c-superseded` | 7 | merge commits and empty commits, carrying no distinct content |

The 15 bucket-b rows are **8 distinct work items** — the duplication is the incident itself, the
same change rescued under several SHAs from repeated rebases.

Per-row detail with evidence: `reintegration-triage.tsv`.

## Two corrections to the task's premise

Both commits the task named as bucket (b) required correction:

* **`Add ASPLOS'20 DRB reproducibility experiment` is NOT missing.** All 13 of its files are on
  main: 11 byte-identical, and the other 2 (`PILOT_RESULTS.md`, `rebuild.sh`) are present with
  *newer* content, last modified on main by `d83a34b` (2026-08-03) and `6834ba9` (2026-08-04). It
  landed and then evolved. Re-landing it would revert two later commits.
* **`demos: package deterministic QEMU Linux L2 boot` IS missing, but cannot be re-landed as-is.**
  `demos/07-qemu-strict-l2.sh` is genuinely absent — and **the demo-7 slot has since been reused**:
  `demos/Makefile` now wires `demo7` to `07-drgn-kernel.sh`. Re-landing the script under its
  original name collides. It needs a new slot number plus `demos/Makefile` wiring, which is a
  design decision rather than a mechanical restore, and QEMU itself was only just restored on this
  host. Left for a scoped PR rather than forced here.

There is a third hazard in the same commit: the most complete QEMU SHA, `38a1b3bcf`, also carries a
`hermit` **gitlink** moving the submodule to `75a315c6` while main is at `b4e94ce4`. Replaying that
commit wholesale would rewind the hermit pin. Any re-land must take the three non-gitlink paths
only.

## The 8 distinct bucket-b items

| representative sha | added file absent from main |
| --- | --- |
| `38a1b3bcf` | `demos/07-qemu-strict-l2.sh` *(slot collision, see above)* |
| `832c89153` | `demos/lib/test_demo_common.py` |
| `b0239f568` | `ai_docs/flaky-failure-attribution-procedure_20260803.md` |
| `7028f6999` | `ai_docs/reverie-portable-vs-privileged-split-audit_20260803.md` |
| `0f2a122d6` | `ai_docs/ci-hub-local-ci-history-store-handoff_20260803.md` |
| `99cac6ec9` | `ai_docs/reverie-single-runner-spof-analysis-and-mitigations_20260803.md` |
| `e55db2494` | `ai_docs/unified-patching-backend-constructor-feasibility_20260803.md` |
| `f04ee23bc` | `debug/demo5-regression/BIGREPORT.txt` *(subject is "override test"; likely scratch, confirm before re-landing)* |

Six of the eight are single `ai_docs/` research documents — mechanical to restore, one PR each, no
conflict surface. Those are the cheap wins. The two with real integration cost are the QEMU demo
and `demos/lib/test_demo_common.py`, which is a demo-harness library other demo scripts may now
expect in a different form.

## Reproduction

```bash
git rev-list --since=2026-07-20 <main> > /tmp/mainlist.txt
while read c; do echo "$(git show $c | git patch-id --stable | awk '{print $1}') $c"; done \
  < /tmp/mainlist.txt > /tmp/main_patchids.txt
# then per orphan: patch-id lookup, per-path blob compare, added-path presence
```
