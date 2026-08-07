# hermit-w7 wind-down handoff — three live changes that existed only as untracked files

**2026-08-07.** Written at fleet wind-down. Everything here was **live on this machine but
versioned nowhere**: it would survive the agent, but not a machine rebuild, and nobody could
review it. That is the gap this directory closes.

## Live changes, and how to revert each

| what | live at | revert |
| --- | --- | --- |
| **gchat attribution shim** — agents may message the owner, never *as* him | `~/orc-bin/gchat` (PATH pos 3, before `/usr/local/bin` at 19) | `rm ~/orc-bin/gchat` |
| **canonical hook dispatcher** — hooks materialised from `origin/main` per invocation | `~/.dev-hermit-canonical-hooks-v2/` + `core.hooksPath` on the parent repo | `git -C ~/work/dev-hermit config core.hooksPath '.githooks'` |
| **`setup-hooks.sh` patch** — makes the above reproducible per machine | NOT APPLIED — patch only | n/a |

None of the three is in a tracked path. `scripts/hook-dispatch`, `scripts/hooks-currency` and the
`setup-hooks.sh` patch are the intended tracked home; `gchat-attribution-shim` needs an owner
decision on where a PATH shim should live before it is tracked anywhere.

## What each does, and what it does NOT do

**`gchat-attribution-shim`** — rewrites `gchat … send` to inject `--as-bot` (google-mux defaults
to `--as-user`, which is why an agent post arrived as the owner) and to prefix
`[This is the <agent> agent.] `. Identity comes from the sender's own tmux pane, scoped with
`-t "$TMUX_PANE"`; an unscoped lookup returns the *attached client's* window and would mislabel
every post as coord. 9/9 tests against a recording stub, covering all four text-input paths.
**Does not close:** an explicit `/usr/local/bin/gchat`, an explicit `--as-user`, or web/API. And
`--as-bot` is **unverified live** — the one hidden self-message check timed out; nothing arrived.

**`hook-dispatch`** — holds no hook content. Resolves `origin/main`, materialises `.githooks/*`
from the object store into a commit-keyed cache, verifies the blob hash before exec, and runs it.
Resync is eliminated rather than automated. Fails **closed** for `pre-push` and **open** for every
other hook, deliberately: bricking commits in 41 checkouts is worse than an unenforced hygiene
warning. Took stale-checkout gating from 2/41 to **41/41**, measured behaviourally.
**Does not close:** fresh clones — `.git/config` is not cloned and git never runs hooks from a
clone by design.

**`hooks-currency`** — exits nonzero on an unset `core.hooksPath`, a frozen-copy `hooksPath`
(one with no dispatcher), or a source ref older than `HOOKS_CURRENCY_MAX_AGE_MIN` (default 120).

## Guests — the reproductions behind published findings

`ioprio.c` (PR #881 confirmation), `sockts.c` (PR #912 refutation), `notsc.c` / `tscleak.c` /
`notsc_mut.c` (LiteInst stack, TSC-leak, and planted-mutation controls). Compile with `gcc -O0`;
`notsc_mut.c` differs from `notsc.c` by one extra `getpid()` and is the mutation control.

`detlog_parity.py`, `matrix_collect.sh`, `e9_collect.sh` are the DETLOG collection/scoring
harnesses; the maintained copies live in `experiments/detlog-parity-matrix_20260807/`.
