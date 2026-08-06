# Submodule checked-out-by-default — full audit and the enumeration nobody did

**Task:** `reverie-gitmodules-still-has-shallow-skipping` (P0)
**Date:** 2026-08-06 · **Author:** hermit-design · **Status:** audit complete; two prose fixes applied locally, uncommitted (egress-gated for landing).
**Bound to:** dev-hermit working tree · hermit `b64d893a` · reverie `025d37800d347c32711038bd0a3889e8e4774c2b`

---

## 0. Verdict up front

**The stated premise is STALE.** `reverie/.gitmodules` does **not** carry `shallow = true` at current
reverie `main`. The fix landed as `04a46b43` *"gitmodules: drop shallow=true from all three
submodules"*, and `git merge-base --is-ancestor 04a46b43 025d3780` returns 0 — it is on `main`. The
owner's cited checkout `39923c27` is not present in the local object store, so it cannot be dated
here, but the task's own later note (2026-08-04 15:06) already recorded this correction against a
fresh fetch.

That is not the end of the task, because the owner also asked the question nobody had answered:

> *"AND ENUMERATE: are there OTHER `.gitmodules` anywhere in the tree? I checked three. The purge
> missed one of three because nobody listed them."*

There is a **fourth tracked `.gitmodules`** (§2). It is harmless, for a reason worth writing down.
And the sweep turned up **three live residues of the retired opt-out policy** that are not in any
`.gitmodules` at all (§4) — which is the same set-scoped-directive shape the owner named: the purge
covered the file everyone was looking at.

---

## 1. Every tracked `.gitmodules`, audited

`find` reports 1172 `.gitmodules` paths under the workspace, but almost all are inside `scratch/`,
`ignored/`, build trees, or slot copies. The set that matters is the files **tracked by git** in the
repos we own:

| # | Tracked path | Entries | `shallow` | `update = none` | `sparse` | `branch =` | Verdict |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| 1 | `dev-hermit/.gitmodules` | 4 | — | — | — | — | **clean** (all `update = checkout`) |
| 2 | `hermit/.gitmodules` | 2 | — | — | — | — | **clean** (no `update` key ⇒ git default `checkout`) |
| 3 | `reverie/.gitmodules` | 3 | — | — | — | — | **clean** (all `update = checkout`) |
| 4 | `reverie/reverie-e9patch/vendor/e9patch/contrib/zydis/.gitmodules` | 1 | — | — | — | — | **clean and inert** (§2) |

Nested `.gitmodules` inside checked-out third-party submodules — which a recursive init *does*
descend into — were also checked: `third-party/dynamorio/.gitmodules` and
`third-party/e9patch/contrib/zydis/.gitmodules` carry no `shallow`/`none`/`sparse`/`branch`.

Command, for re-running the enumeration rather than trusting this table:

```bash
for r in . hermit reverie; do
  for f in $(git -C "$r" ls-files '*.gitmodules'); do
    printf '%s/%s: ' "$r" "$f"
    grep -cEi 'shallow|update *= *none|sparse|branch *=' "$r/$f" || echo 0
  done
done
```

---

## 2. The fourth file, and why it is inert

`reverie/reverie-e9patch/vendor/e9patch/contrib/zydis/.gitmodules` declares one submodule
(`dependencies/zycore`). It never affects a recursive init, and the reason is checkable rather than
assumed:

```
$ git -C reverie ls-files -s reverie-e9patch/vendor | awk '{print $1}' | sort -u
100644
100755
```

Every entry under `reverie-e9patch/vendor/` is a **blob** (mode `100644`/`100755`), not a **gitlink**
(mode `160000`). The vendored e9patch is ordinary checked-in content that happens to include upstream's
`.gitmodules` as a file. `git submodule update --init --recursive` only descends into gitlinks, so this
file is never read as configuration.

Worth recording precisely because a future sweep will find it again and either "fix" a file that does
nothing or panic about it. The discriminator is `ls-files -s` mode, not the filename.

---

## 3. Machine-local config — the thing that makes a warm box lie

The owner's verification bar is *"a FRESH CLONE on a machine with NO warm cargo cache … a warm-box
check would pass while the defect persists."* The specific mechanism behind that warning is real and
is documented in reverie's own Makefile: **`git submodule init` never overwrites an existing local
value**, so a long-lived checkout created under the retired `update = none` policy keeps that value in
`.git/config` and silently prints `Skipping submodule …` even after `.gitmodules` says `checkout`.

Swept on this box — no such stale value survives:

| Repo | Local `submodule.*` overrides |
| --- | --- |
| `dev-hermit` | 4 submodules, all `active = true`, all `update = checkout` |
| `hermit` | `agent-utils`, `third-party/rr` — both `active = true`, no `update` override |
| `reverie` | `third-party/dynamorio`, `third-party/sabre` — `active = true`; sabre `update = checkout` |

Reverie already has the durable cure wired: `make checkout-all` depends on `sync-submodule-policy`,
which copies every `update` value from `.gitmodules` (the source of truth) into `.git/config` before
the recursive init. That is the right shape — the config is repaired from the tracked policy rather
than trusted.

---

## 4. Three live residues that are NOT in any `.gitmodules`

This is where the purge actually stopped.

### 4.1 The parent policy file still documented the retired opt-out — FIXED

`AGENTS.md` (which `CLAUDE.md` symlinks to) said, at line 43:

> `agent-utils/`: shared tooling incl. `tick-hub`; **`update = none` keeps it out of ordinary recursive
> init, materialized on demand.**

That is false against `dev-hermit/.gitmodules`, which records `update = checkout` for `agent-utils`.
This is not cosmetic: it is the policy file every agent reads, telling them a submodule is excluded
from recursive init when it is not — and it is exactly the kind of stale instruction that reproduces
the retired behavior by hand. Line 38's "one **optional** tooling submodule" was the same residue.

**Fixed locally** (both lines). Uncommitted; landing is egress-gated.

*Corroborating incident from this session:* building `scripts/validate.rs` in a slot required
`git -C worktrees/coord/hermit submodule update --init agent-utils` as a separate manual step, because
the slot's `agent-utils` gitlink was an empty directory. Whether that was allocator behavior or a
stale local config, the operator-visible symptom is precisely the one the doc line predicts.

### 4.2 Reverie's authoritative CI checks out **no** submodules — REPORTED, not changed

`reverie/.github/workflows/ci.yml` sets `submodules: false` on the `actions/checkout` step of **both**
authoritative gates:

| Line | Job |
| ---: | --- |
| 22 | `Regular tests (GitHub-hosted)` |
| 137 | the host-dependent (self-hosted) job |

So reverie's required CI never materializes `third-party/{dynamorio,sabre,e9patch}`, and therefore
never builds the third-party backends. `hermit/.github/workflows/demo-hot-path.yml` does the same at
lines 147 and 154.

Whether that is a *violation* or a deliberate cost choice depends on whether those jobs are supposed
to cover the third-party backends — and flipping them to `submodules: recursive` would add a
DynamoRIO build to every run of the required gate. **I did not change it.** Removing "residue" by
silently adding a heavy build to the authoritative gate is a bigger decision than this task
authorizes, and it belongs to whoever owns reverie CI cost. It is reported here because the owner's
policy is "checked out by default" and this is the largest place that is not true.

For contrast, hermit already does the recursive thing where it matters:
`validation-levels.yml:101,182` and `ci-dag.yml:88` all use `submodules: recursive`.

### 4.3 `third-party/e9patch` is uninitialized in the reverie primary right now — BLOCKED

```
$ git -C reverie submodule status --recursive
 929840ad… third-party/dynamorio (cronbuild-11.91.20651-4-g929840ad9)
 302252…   third-party/dynamorio/third_party/elfutils
 c848a8…   third-party/dynamorio/third_party/libipt
 51b7f2…   third-party/dynamorio/third_party/zlib
-6c2c03c1da74b14daf1788a9f8dccfa354ce04a6 third-party/e9patch      <-- leading '-' = NOT initialized
 41113f…   third-party/sabre
```

The config is correct (`update = checkout`, no local override); the *checkout* is simply incomplete.
`git submodule update --init --recursive` would materialize it — and needs to fetch from GitHub.
**Egress is down (box-wide 403), so this could not be completed.** It is an operational state, not a
config defect, but it is the concrete instance of "a fresh recursive init should get everything" not
currently holding on this box.

---

## 5. What the owner's verification bar still needs

> *"a FRESH CLONE on a machine with NO warm cargo cache, then `git submodule update --init --recursive`
> followed by `make`."*

**Not performed, and not performable here** — a fresh clone and a cold cargo fetch both require
network egress, which is 403 box-wide. What *was* established locally is the config-level property
(§1–§3) plus the residues in §4. Those are different claims and this document does not conflate them:
a clean `.gitmodules` set is necessary for the cold-clone bar, not sufficient for it.

The honest cold-clone check, when egress returns:

```bash
git clone https://github.com/rrnewton/reverie.git /tmp/cold-reverie
cd /tmp/cold-reverie
git submodule update --init --recursive
git submodule status --recursive        # NO leading '-' or '+' on any line
CARGO_HOME=$(mktemp -d) make            # cold cargo cache, per the owner's bar
```

The `git submodule status --recursive` line is the discriminator worth keeping: a leading `-` is
uninitialized and a leading `+` is at the wrong revision, so a fully clean listing is the observable
that "everything got checked out" — rather than inferring it from `make` exiting 0.

---

## 6. Changes made

| File | Change | State |
| --- | --- | --- |
| `AGENTS.md` line 43 | `agent-utils` described as `update = none` → corrected to `update = checkout`, with the retirement dated | edited, **uncommitted** |
| `AGENTS.md` line 38 | "one **optional** tooling submodule" → "all four checked out by default" | edited, **uncommitted** |

**No `.gitmodules` file was changed** — all four were already clean. Claiming a `.gitmodules` fix here
would be fabricating work the ledger of commits does not support.

---

## 7. Not established

* The cold-clone + cold-cargo verification (§5) — blocked on egress.
* `third-party/e9patch` initialization (§4.3) — blocked on egress.
* Whether reverie CI's `submodules: false` (§4.2) is intended. Reported, deliberately not changed.
* The owner's checkout `39923c27` could not be inspected (absent from the local object store), so
  "the premise is stale" rests on `04a46b43` being an ancestor of current `main` plus the task's own
  2026-08-04 fresh-fetch note — not on dating the owner's snapshot directly.
