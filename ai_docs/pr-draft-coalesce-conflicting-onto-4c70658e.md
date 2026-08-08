[impl agent, opus-5] Coalesce 4 conflicting PRs onto current main (#1612, #1618, #1629, #1638)

## Summary

This is a **coalescing branch**, not new work. It rebases four already-reviewed open PRs onto the current
`origin/main` tip `4c70658e785834737cbe1524f77330c781a6f5ea` and carries them as one reviewable unit so they
consume **one** box-exclusive validate slot instead of four.

The four were the conflict-bearing subset of the open-PR inventory; each needed real conflict resolution
against the new tip, which is why they are handled together:

| PR | commits | conflict resolved | resolution class |
|---|---|---|---|
| #1612 | 2 | `validate.sh` | **additive union** — `--ignore-cache` (main) and `--allow-stale-reverie-pin` (PR) are independently-added flags; both kept |
| #1629 | 1 | `.github/workflows/ci-portable-autoretry.yml` | **semantic union** — main added `vars.CI_MODE != 'constrained'`, the PR added cancelled-main-push coverage; both kept |
| #1638 | 1 | `hermit-cli/Cargo.toml` | **pin-vs-feature** — PR was authored against reverie `79517704`; took main's `dd3c178ea` for every existing dep and carried the new `reverie-e9patch` dep forward *retargeted* to `dd3c178ea` |
| #1618 | 4 | `ci/dag/portable.json` | **union** — main added the `run-with-reverie-dbi-budget.sh` wrapper, the PR added `--test register_file_hashing`; both kept |

Commits are **cherry-picked, not squashed**, so all 8 stay individually attributable and a red bisects to a
single source PR.

**Conflict resolution only — no drive-by refactors.** One note on that: resolving `ci/dag/portable.json` by
round-tripping it through a JSON pretty-printer reformats the whole file (745 insertions / 297 deletions for a
2-line change). That was reverted and redone as a surgical text substitution: **2 insertions, 2 deletions**.

### Landing note

The four source PRs cover the same content. Land **either** this branch **or** the four individually, not
both. Landing is serial and owned by `hermit-det2`; this branch is offered as the cheaper path.

## Determinism

**This branch introduces no determinism semantics of its own — it is a rebase.** Each carried change keeps the
determinism argument it was reviewed with. What the *coalescing* must not do is silently alter any of them, so
the argument here is about the four resolutions:

1. **`validate.sh` (#1612)** and **the autoretry workflow (#1629)** are validation/CI harness only. Neither is
   compiled into `hermit`, neither runs in the guest, and neither participates in scheduling, virtual time, or
   any determinization decision. They cannot change guest-visible behaviour.

2. **The reverie pin (#1638)** is the one resolution that *could* have changed product behaviour, and it was
   resolved in the direction that preserves determinism: the whole tree is pinned to a **single** reverie
   commit. Verified by census — `dd3c178ea` appears at **22** `Cargo.toml` sites and **27** `Cargo.lock`
   entries, and no other reverie SHA appears anywhere except the two pre-existing `95ee5e69` hits in
   `liteinst-runtime-build/Cargo.lock`, which are already on the tip and untouched by this branch (0 lines of
   this diff reach them). A mixed pin is precisely how two backends end up built against different Reverie
   semantics, so single-pin is the determinism-preserving resolution. `reverie-e9patch` v0.2.0 was confirmed to
   exist at `dd3c178ea` before retargeting, rather than assumed.

3. **`ci/dag/portable.json` (#1618)** changes which tests CI executes, not what they do. Taking the union
   *strengthens* the determinism signal: the DBI budget wrapper and the `register_file_hashing` both-directions
   test both now run, where taking either side alone would have silently dropped one.

The carried product changes (#1618's register-file hashing in `--verify` detlogs, #1638's ptrace-free e9patch
in-guest Detcore tool host) are unmodified from their reviewed form; their determinism arguments live on the
source PRs.

## Validation

**No validate receipt. This is disclosed, not omitted.** No validate can be admitted from an agent sandbox at
present: `ci-hub/validate/preflight_validate.py::resolve_current_base()` runs `with-proxy git fetch` and raises
`AdmissionError` on non-zero exit, with no `--no-fetch`/`--offline` option in its argparse. It is fail-closed on
*reachability*, not on staleness — `origin/main` in the checkout already equals the target tip, so the fetch it
demands is a no-op. `herdr-run` provides working egress for `git`/`gh` but refuses `python3`
(`program 'python3' is not allowlisted`), so it cannot carry the preflight. **This branch must not be landed
until a receipt exists at `3958ab2b9087acac074e1eb8f464c67df9584d88`.**

Offline checks that did run, all green:

| check | result |
|---|---|
| `bash -n validate.sh` | parses |
| PyYAML parse of `ci-portable-autoretry.yml` + resolved `if:` condition | valid; condition is the intended union |
| `json.load(ci/dag/portable.json)` | valid, 47 steps |
| `cargo metadata --no-deps` | OK — workspace coherent after the pin surgery |
| reverie pin census | single SHA `dd3c178ea` across 22 manifest + 27 lock sites |
| `git merge-base --is-ancestor 4c70658e7 HEAD` | true — genuinely based on the current tip |
| remote re-read (`ls-remote`) | `3958ab2b9…` at `refs/heads/coalesce/conflicting-onto-4c70658e`, and `origin/main` still `4c70658e7` |

A cross-check worth recording on #1629: the union condition is not merely a textual merge — the *unconflicted*
body below it already branches on `RUN_EVENT == "push"`, so the condition must admit `push` or that code is
dead. The union is what makes the existing body reachable.

## Relationship to gVisor

Not applicable — no KVM code is touched.

## Human Review Required

Not applied. Checked against the four triggers: (1) no new syscall support; (2) no Reverie API or
core-abstraction change — the reverie dependency is *repinned*, not modified; (3) no new determinization
strategy; (4) no DetCore scheduling change. #1638 changes the e9patch backend, which is routine backend-parity
work toward the golden ptrace reference and does not by itself trigger review. If a reviewer reads #1638's
in-guest Detcore tool host as a new determinization strategy rather than a backend execution mode, apply
`post-facto-human-review` under trigger 3 — flagging the judgment rather than burying it.

## Commits

```
3958ab2b9 ci(portable): execute register_file_hashing both-directions test in CI      (#1618)
bf17adfd0 Add test inventory disposition for register_marker.c                        (#1618)
4187b0867 Suppress [regs] determinism digest for backends with nondeterministic
          syscall-boundary registers (SaBRe)                                          (#1618)
0cb2f46b3 detcore: hash the guest register file in --verify determinism logs          (#1618)
65d13a0de Run the e9patch backend ptrace-free with an in-guest Detcore tool host      (#1638)
199ce53a5 ci: cover cancelled main pushes in the portable auto-retry guard            (#1629)
37f3de0ab validate.sh: bind unharnessed-run admission to the live lease owner         (#1612)
91a5d151b validate.sh: refuse unharnessed dev-hermit runs and stale reverie pins      (#1612)
```

Base `4c70658e785834737cbe1524f77330c781a6f5ea` · head `3958ab2b9087acac074e1eb8f464c67df9584d88`
