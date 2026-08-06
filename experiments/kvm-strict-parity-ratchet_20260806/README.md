# KVM strict-parity ratchet: blocked on reverie #387, and exactly what it unblocks

**Date:** 2026-08-06 · **Task:** `kvm-strict-parity-ratchet` · Local, no egress beyond the push.
**Result: ZERO cells ratcheted.** Not for lack of surface — every `--backend kvm` run on this
host hangs before the guest produces output, for a cause that is already diagnosed and fixed on
an **unmerged reverie branch**.

## The blocker, as a causal chain rather than "kvm is broken"

| link | state | evidence |
|---|---|---|
| Fix exists | `reverie-kvm: separate traced-tree root identity from guest-visible getppid` | commit `ad1b845c` |
| Fix is unmerged | **reverie PR #387 OPEN**, branch `fix/kvm-tree-root-identity-vs-guest-ppid` | `gh pr list` |
| Not in reverie main | `ad1b845c` is **not** an ancestor of `origin/main`; `tree_root` absent from `reverie-kvm` on main | `merge-base --is-ancestor` → NO |
| Parent pins a reverie without it | gitlink `dd3c178e` | `git ls-tree HEAD reverie` |
| No local build carries it | no `hermit` checkout pins `ad1b845c`; no `reverie` worktree is on that branch | repo-wide grep |
| ⇒ every available binary livelocks | reproduced below | this artifact |

The underlying defect (from the prior diagnosis): hermit `8b7345103` correctly set the KVM root
pid to Detcore's canonical `ROOT_DETPID`, which made `parent_pid()` return `Some(1)` instead of
`None`, so `is_root_thread()` went false, the root was never admitted to the run queue, and the
scheduler daemon waited forever. **The hermit change is correct parity work and must stay;** the
fix is on the reverie side.

## Reproduced here, at a named binary

`worktrees/audit/hermit/target/release/hermit`, `hermit 0.2.0 (2026-08-06, gfad50bc75543)`,
sha256 `aa5b0705…fe332027`, against parent-pinned reverie `dd3c178e`:

| probe | result | wall | stdout |
|---|---|---|---|
| `run --backend kvm --base-env=minimal -- /bin/echo hi` | **HUNG** | 48.1 s (bounded) | empty |
| `run --backend kvm --strict --base-env=minimal -- /bin/echo hi` | **HUNG** | 48.1 s (bounded) | empty |

Both hang on `/bin/echo` — i.e. **before any guest output**, so this is not a strict-mode or
parity issue. `--strict` never gets a chance to be the discriminator.

### One correction to the received wisdom, measured

Earlier notes record that KVM "**ignores SIGTERM** and needs SIGKILL after 10 minutes". In this
probe `SIGTERM` **did** reap it — but sent to the **process group**. The difference is almost
certainly the reaping mechanism, not KVM: `timeout(1)` signals only its direct child, and the
spinning work is in a descendant, so the earlier runs looked SIGTERM-proof.

**Practical rule either way:** bound KVM with `start_new_session=True` and
`os.killpg(pgid, SIGKILL)`, never bare `timeout`. `kvm_probe.py` does this and was verified to
leave **zero** surviving processes — which matters on a 316-core box shared with ~15 agents,
where a leaked KVM guest burns a core indefinitely.

## What landing #387 actually buys — the denominator matters

"KVM has ~1000 red cells" would be badly misleading. Classifying every KVM cell across **all**
manifests by its disabled-reason text:

| class | cells | meaning |
|---|---:|---|
| **no evidence** | **215** | *"not evaluated"* / *"the L2 `--verify` witness was not recorded"* — **the qualifiable surface** |
| documented limitation | 986 | *"KVM requires the privileged runner"*, *"Record/replay is unsupported by KVM"*, *"Chaos scheduling is unsupported by KVM"* — by design, not gaps |
| already enabled | 23 | |

The 215 by bucket/mode:

| bucket / mode | cells |
|---|---:|
| c-programs/verify | 152 |
| backend-parity-c/verify | 49 |
| determinism-stress-c/verify | 5 |
| shared-futex-c/verify | 4 |
| bin-c, chaos-c, debugger-c, util-c (verify) | 5 |

So **215 cells become qualifiable the moment #387 lands**, and the ~986 remainder should not be
counted as a KVM parity debt — most are unsupported-by-design combinations.

Note the 24-odd *specific* KVM reasons in `backend-parity-c` (ENOSYS for `sendfile`,
`memfd_create`, `select`, `preadv2/pwritev2`, `syncfs`, the inotify family, …) are genuine
capability gaps in the ElfExecutor personality and will stay red after #387; they are counted in
the 986, not the 215.

## Recommended order

1. **Land reverie PR #387** — it is the single blocker, and nothing else in this lane can proceed.
2. Repin the parent's reverie gitlink past it and rebuild one dbi/sabre/kvm-capable binary.
3. Re-run the qualification sweep from
   [`backend-parity-cell-qualification_20260806`](../backend-parity-cell-qualification_20260806/README.md)
   with `kvm` added — it already carries the engagement gate and the no-evidence classifier, so
   the KVM column is a parameter change, not new tooling. The **engagement witness for kvm still
   needs choosing**: dbi has `rewritten=N`, sabre has `guest_rpc_observed=true`, and the
   equivalent affirmative signal for kvm is not yet identified.

## Not done, stated plainly

- **Zero cells ratcheted, zero manifest edits, no `ci=true`, no ratchet regeneration.** The
  backend does not run here, so any "pass" would have been a no-result reported as a finding
  (#319).
- **No per-cell KVM tier recorded** — the task's deliverable — because no cell can produce one.
  The surface sizing above is the closest honest substitute.
- **The fix itself was not built or tested.** Confirming #387 actually clears the livelock needs
  a slot to build hermit against `ad1b845c`; this lane has no allocated slot
  (no `hermit-w10` row in `worktrees/ACTIVE.md`) and provisioning is coordinator-only.
- `/dev/kvm` is **not** the obstacle: it is `crw-rw-rw-` and readable/writable by this agent.

## Reproduction

```sh
python3 kvm_probe.py     # ~100s, bounded, reaps by PGID, leaks nothing
```
