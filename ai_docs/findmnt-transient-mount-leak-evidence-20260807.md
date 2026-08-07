# findmnt determinization: measured evidence, and why `--verify` never catches it

**Task:** `determinize_findmnt_against_transient` · **Issue:** [rrnewton/hermit#1820](https://github.com/rrnewton/hermit/issues/1820)
**Date:** 2026-08-07 · **Host:** `devbig014` · **Binary:** `hermit/target/debug/hermit` (built 2026-08-06 14:43), run in place
**Method:** read-only investigation plus a causal A/B in a private mount namespace. No product code changed, no branch, no PR.

## 1. The leak is real

`hermit run --strict -- /usr/bin/findmnt -rn -o TARGET` shows `/run/user/212630` in the guest
mount view, and it reaches the DETLOG (`--detlog-stack`, grep count 1). There is **no**
`/run/user` handling anywhere in `detcore/src` or `reverie` — the host mount table passes
through unfiltered. Guest sees 91 mounts, host 86.

## 2. Causal proof that host mount state propagates into the guest

Run inside `unshare -Urm --propagation private`, so the added mount is visible only to my own
children and no other agent on this shared box is affected. **Identical hermit command; the only
change is the host mount table.**

| state | host mount table | guest mounts | probe visible |
| --- | --- | ---: | --- |
| A | probe absent | 91 | 0 |
| B | `mount -t tmpfs orc014probe /tmp/orc014_probe` | **92** | **1** |

`diff A B` → `82a83 > /tmp/orc014_probe`. One added host mount, one added line in the guest's
view. The guest mount table is a direct function of transient host state.

## 3. `--verify` is structurally blind to this

Both states pass, identically:

```
state A: no substantive differences found (1032 | 1032 DETLOG messages compared)
         :: Success: deterministic. Determinism verified.
state B: no substantive differences found (1032 | 1032 DETLOG messages compared)
         :: Success: deterministic. Determinism verified.
```

`--verify` compares two runs taken moments apart, so **both observe the same host state**. It
cannot see a difference that lives *between* states. Three independent strict runs ~3s apart also
gave byte-identical sorted mount lists (91/91/91, 0 differing lines).

**Consequence for whoever picks this up: there is no live strict red to reproduce.** A dispatch
that says "verify the live strict failure first" cannot be satisfied as written. The exposure is a
DETLOG recorded now versus a golden/replay later, or two runs separated by a session boundary —
not a failing test today. That is very likely why this has survived.

## 4. The surface is larger than the issue title

Beyond `/run/user/<uid>`, the guest sees xarfuse mounts encoding process IDs and namespace inodes:

```
/mnt/xarfuse/uid-212630/34eac42a-seed-nspid4026531836_cgpid49624-ns-4026531832
/mnt/xarfuse/uid-0/14b96945-seed-chef-ns-4026531832
```

`cgpid<PID>` and `ns-<inode>` are per-process and per-namespace — strictly more volatile than
`/run/user/<uid>`, which is at least stable for a login session. A fix scoped to `/run/user`
alone leaves this untouched.

*Caveat, stated because it is easy to misreport:* the several differing `cgpid` values I first
observed were all present in **one** listing (one xarfuse mount per xar-using process), not churn
across runs. This shows these paths **encode** volatile identifiers; it does not show they varied
during my observation window.

## 5. The constraint: the obvious fix was already rejected on review

PR [#1626](https://github.com/rrnewton/hermit/pull/1626) (head `b045a8ae`) is **closed unmerged**,
2026-08-05T15:46:39Z — rejected on review, not swept:

> The NUMA sanitizer is represented by #1644 on `main` at `f21b22ed` and its P0 fix-forward #1647
> at `72e973f5`. The `/run/user/<uid>` path-only suppression is **rejected as an unsafe proxy
> binding** and will not be carried: a pathname alone does not bind the suppression to
> nondeterministic mount state.

So the NUMA half landed; the findmnt half was rejected on the Proxy Binding axis. **A path-pattern
filter will be rejected again, and correctly** — a path is a correlated proxy, not an observation
of transience.

## 6. Design directions (options, not a decision)

Each binds to the *condition* rather than the *name*. None is validated.

- **Provenance.** Suppress mounts whose source/fstype/owner marks them per-session host
  scaffolding (a tmpfs owned by a non-guest uid under `/run/user`), recording *why*.
- **Observed variability.** A mount whose presence differs between the recorded and replayed
  mount table is transient *by observation* — the honest binding, and the one that generalises to
  the xarfuse surface without enumerating patterns.
- **Virtualize.** Present a fixed, coherent mount table under strict, as procfs virtual files are
  handled. This also avoids hiding legitimate guest mounts, the failure mode the task warns about.

Whichever is chosen, the record must carry the reason a mount was suppressed, so an auditor can
distinguish suppression from absence.

## 7. What is NOT established here

- No cross-run variation of the xarfuse paths was observed (see §4 caveat).
- The A/B used a synthetic tmpfs, not a real session boundary; it proves propagation, not the
  frequency of real-world drift.
- No fix is implemented, and the design call in §6 is not agent-decidable given §5.
