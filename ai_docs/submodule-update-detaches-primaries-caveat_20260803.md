# `git submodule update` detaches the primaries — use `make submodules` — 2026-08-03

Task: `submodule-update-detaches-primaries-wrapper` (owner: hermit-coord).
No-surprises bar #176.

## The caveat

The parent's product submodules are declared `update = checkout` in
`.gitmodules` (`hermit`, `reverie`, `liteinst2`; `agent-utils` is `update =
none` on demand). With that mode, the raw

```bash
git submodule update --init --recursive
```

checks each product out **at the parent's pinned gitlink SHA in DETACHED HEAD**.
Run in a primary checkout that was sitting on `main` (or, for `liteinst2`, its
feature branch), it silently detaches that primary and violates the **Primary
Checkout Invariant** — "`~/work/dev-hermit/hermit` and `.../reverie` must ALWAYS
be on the latest main." Two existing entry points call the raw command:

- `make checkout-all` → `git submodule update --init --recursive`
- `make init-hermit` → `check-submodules` → `checkout-all` (so `make build`,
  `make build-full`, `make compat-envelope*`, `make validate` all reach it).

Nothing here is a bug in git; it is the documented behavior of `update =
checkout`. The surprise is only that our build graph runs it against primaries
that must stay attached.

## The safe wrapper

Use **`make submodules`** (→ `scripts/submodules.sh`) instead of `checkout-all`
when you want submodules initialized/updated **without detaching a primary**.

Per product, on a clean checkout it:

| state before            | wrapper action                                              |
| ----------------------- | ---------------------------------------------------------- |
| not initialized         | init that one submodule (non-recursive), then attach to `main` |
| clean + detached        | reattach to `main` — **only** when HEAD is already reachable from `origin/main` (or on some branch), so no unique commit is orphaned |
| clean + on a branch     | left as-is (`hermit`/`reverie` expected on `main`; `liteinst2` may be on a feature branch — preserved), fast-forwarded to upstream unless `--no-pull` |
| dirty                   | **warned and skipped** — never reset/cleaned/stashed (exit 1) |
| detached w/ unreachable commits | **warned and preserved** — never force-reattached (exit 1) |

It is deliberately **non-recursive** (heavy/optional nested submodules such as
e9patch and SaBRe are provisioned on demand by
`scripts/checkout-optional-submodules.rs`) and it **never edits `.gitmodules`**.

```bash
make submodules                       # init/reattach + ff-update all three primaries
make submodules ARGS=--no-pull        # init/reattach only, no fast-forward
scripts/submodules.sh --products reverie
scripts/submodules.sh --with-agent-utils   # also init the on-demand tooling submodule
```

Networked git runs through `with-proxy` when present (`SUBMODULES_DISABLE_PROXY=1`
or `WITH_PROXY=<cmd>` to override).

## How it differs from `make checkout-fresh`

`scripts/primary_checkout.py` (`make checkout-fresh`) already fetches,
reattaches to `main`, fast-forwards, preserves dirty primaries, and can publish
a coherent parent gitlink snapshot. But it **requires each primary already
initialized** (errors on a missing `.git`) and **forces all three to `main`**
(it does not preserve `liteinst2`'s feature branch). `make submodules` fills the
init gap and the branch-preserving gap; it does not touch pins or publish
snapshots. Use `checkout-fresh` when you also want the parent gitlink snapshot
advanced; use `submodules` for a safe, attach-preserving init/update.

## Verification (2026-08-03, this host)

- **Attached (idempotent):** all three primaries on `main` → `make submodules`
  left `hermit@36ee7e70`, `reverie@d2fb9a0`, `liteinst2@8bffae9` on `main`,
  clean, exit 0.
- **Detached → reattached:** `git -C reverie checkout --detach` then
  `scripts/submodules.sh --products reverie --no-pull` → back on `main` at the
  same SHA, clean, exit 0.
- **Dirty → preserved:** a throwaway untracked file in `reverie` → wrapper
  warned "DIRTY; preserved and skipped", left the file and branch untouched,
  exit 1.
- `shellcheck --severity=warning scripts/submodules.sh` clean.

## Coordination note

This change is orthogonal to hermit-250's in-flight `.gitmodules` skip fix
(restoring `agent-utils` to `update = none`). The wrapper only reads submodule
config via git and operates on the three products; it never writes
`.gitmodules`, so the two do not collide regardless of what `agent-utils`'s
`update` mode is set to.
