# Patching-backend arc: live audit of shared main and in-flight PRs

**Task:** `audit-patching-backend-arc-current-main-and-prs` · **Agent:** hermit-w2 (opus-5)
**Date:** 2026-08-06 · **Read-only.** No issues created, no PRs edited, no repository mutated.
**Slot:** audited from `worktrees/w2/hermit` (branch `feat/info-tier-exact-comparator`, 0 commits, clean)
and the `reverie/` primary (read-only inspection; still on `main`).

## Anchors

Fresh explicit main refs, taken this session via `git ls-remote` — not from any local checkout:

| repo | main |
| --- | --- |
| `rrnewton/hermit` | `a8951eff4c259f363a71836e8e93271a6231230a` |
| `rrnewton/reverie` | `0ae0c01b5e4c9fbf85c97adc66c2740f280727df` |
| `rrnewton/liteinst2` | `8bf704feb06a62e7a05bee3b237d70793e4e2689` |
| `facebookexperimental/hermit` (reference) | `8a9a7d7afb6ce6fc936bd6e05cb0b6236d8f10f8` |
| `facebookexperimental/reverie` (reference) | `5757d95eddb1655d5c030b5bf6c5ae80c39e4f45` |

**The parent gitlinks are all behind these** (`hermit b4e94ce4455d`, `reverie dd3c178ea955`,
`liteinst2 8bffae9da68e`). Anything read from the parent pin is stale relative to what is audited here.

### PR query denominator

`gh pr list --state open --limit 500` per repo: **hermit 95, reverie 9, liteinst2 0 — 104 total.** All
below the limit, so the window is not truncated and no orphan is inferred from a short list.

**One truncation found and corrected.** `gh pr list --json files` caps at 100 files per PR. Exactly two
PRs sat at the cap, so their first attribution came from a truncated list. Re-pulled paginated:
`hermit#1754` 100 → **147** files; `reverie#390` 100 → **921** files. All attribution below uses the
paginated lists for those two, and is by **changed path**, never by title.

## B-level classification (two independent source censuses)

**Census A — every `impl Backend for` in reverie** (the full `Backend::run<T>` lifecycle contract):

```
reverie-ptrace/src/backend.rs:47     PtraceBackend
reverie-liteinst/src/backend.rs:584  LiteinstBackend
reverie-e9patch/src/backend.rs:989   E9patchBackend
```

…and nowhere else. **`reverie-dbi`, `reverie-kvm` and `reverie-sabre` have no `Backend` impl.**

**Census B — every `impl Guest<T>`:** ptrace `TracedTask`, liteinst `LiteinstGuest`
(`tool_host.rs:545`), e9patch `E9patchGuest` (`tool_host.rs:448`), dbi `DbiGuest`, kvm `KvmGuest`,
sabre `SabreGuest` (`experimental/reverie-sabre/src/reverie_adapter.rs:1089`).

The two censuses agree:

| backend | `Backend` | `Guest` | tree | classification |
| --- | --- | --- | --- | --- |
| **LiteInst** | ✅ | ✅ + in-guest `tool_host` | supported | full B-level contract |
| **e9patch** | ✅ | ✅ + in-guest `tool_host` | supported | full B-level contract |
| **SaBRe** | ❌ | ✅ | `experimental/` | **adapter, not a B-level backend** |
| DBT/DBI *(non-patching)* | ❌ | ✅ | supported | driven by DynamoRIO client `.so` |
| KVM *(non-patching)* | ❌ | ✅ | supported | — |

## In-guest handler, and the ptracer's role

The hybrid is stated in source, not inferred:

- `reverie-e9patch/src/lib.rs:13` — *"hybrid `reverie::Backend`. Ptrace remains the lifecycle and `Guest`…"*
- `reverie-e9patch/src/runtime.rs:56` — *"the shared fallback ptracer owns…"*
- `backend.rs:595` banner: `:: Backend: e9patch hybrid; recovered_sites=..; patched_sites=..; b0_sites=..; event_source=..; controller=ptrace`
- `backend.rs:577` **degraded** banner: `patched_sites=0; b0_sites=0; event_source=ptrace; controller=ptrace; main_executable=non-ELF`

> **A run can report success while patching contributed nothing and ptrace serviced everything.**
> `patched_sites` / `event_source` are the only fields separating the two banners, so any
> e9patch/liteinst result quoted without them is not evidence that the patching path ran.

Cargo deps confirm the shape: `reverie-liteinst` and `reverie-e9patch` both depend on
**`reverie-ptrace` + `reverie-preload` + `reverie-rpc-transport`**; dbi/kvm/sabre depend on neither
ptrace nor preload. In-guest trap surface (grep counts over `<crate>/src`): SIGSYS — liteinst 60,
e9patch 33, sabre 1, dbi 0, kvm 0; seccomp — kvm 44, e9patch 27, liteinst 21, dbi 0, sabre 0.

**Strace-attach / zero-ptracer litmus: not on main.** Zero matches for `litmus` anywhere in
`reverie-liteinst` or `reverie-e9patch` at `0ae0c01b`. It exists only in flight (reverie#391, #389).

## RCB accounting — `read_clock()` per backend, quoted from source

| backend | result | source comment |
| --- | --- | --- |
| **ptrace** | `Ok(self.timer.read_clock())` | real, PMU-backed |
| DBT/DBI | `Ok(self.branch_count)` | *"sampled at the most recent syscall entry, not a continuously updated clock"*; `TODO-STUB(#31)` |
| LiteInst | `Ok(0)` | *"LiteInst has no sample yet, so zero is the honest lower bound"* |
| KVM | `Ok(0)` | *"does not yet expose a PMU"* |
| SaBRe | `Ok(0)` | *"No branch clock is exposed by SaBRe yet"* |
| **e9patch** | `Err(Unsupported)` | *"e9patch direct Tool host does not implement an RCB clock"* |

**ptrace is the only backend with a real RCB clock. No patching backend has one at all.**

## Nondeterministic-instruction coverage

Handler-name census over `<crate>/src` on fresh main. This bounds where handlers are **implemented** —
it is a floor, not a pass, and says nothing about correctness or runtime reachability.

| | liteinst | e9patch | sabre | dbi | kvm |
| --- | --- | --- | --- | --- | --- |
| `handle_cpuid_event` | 0 | 0 | 0 | 0 | 0 |
| `handle_rdtsc_event` | 0 | 0 | 3 | 0 | 0 |
| rdrand / rdseed | 0 | 0 | 0 | 0 | 2 / 2 |
| vdso | 1 | 6 | 62 | 1 | 20 |
| post_exec | 3 | 1 | 15 | 2 | 8 |

**No patching backend implements `handle_cpuid_event`.**

## Shared code

`reverie-rpc-transport` (UDS + bincode `GlobalState`) is used by liteinst, e9patch, dbi **and** sabre.
SaBRe additionally still depends on the older `experimental/reverie-rpc` — the only crate on both RPC
stacks. KVM depends on neither (in-process `GlobalState`).

## CLI capability gate

`hermit-cli/src/bin/hermit/main.rs:232-264`, `validate_backend_scope`:

| backend | allowed subcommands |
| --- | --- |
| SaBRe | `Strace(_) \| Run(_)` |
| e9patch | `Run(_)`, or `Record(r)` when `r.starts_recording()` |
| LiteInst | `Run(_)` only |
| KVM | `Run(_)` only |

**Source inconsistency:** the SaBRe guard admits `Run`, but its `bail!` message names only the strace
path. Predicate and message disagree.

## Strict + verify envelope

**No patching backend can produce an L2 claim today.** L2 requires `--verify-strict --verify-json` with
`bitwise_parity: true` and both `compared_log_messages.{left,right} > 0`. Blockers, source-verified
above: e9patch's `read_clock` returns `Err(Unsupported)` and liteinst/sabre return `Ok(0)`, so there is
no RCB clock to compare; and the CLI gate restricts LiteInst and e9patch to `run` (e9patch also
`record`). **No hermit run was executed for this audit** — read-only, and no tier is quoted that was not
measured.

## In-flight PRs (16, source-diff attributed)

### LiteInst

| PR | head | branch | state | CI |
| --- | --- | --- | --- | --- |
| [reverie#389](https://github.com/rrnewton/reverie/pull/389) | `097868594f53` | `stack-ptracer/liteinst-stats-off-ptrace-*` | draft, MERGEABLE | 6ok/21, **RED** merge-gate-v2 |
| [reverie#391](https://github.com/rrnewton/reverie/pull/391) | `af3fda4b672a` | `liteinst/zero-ptracer-litmus-negative-ha*` | draft, MERGEABLE | **0ok/0 — no CI has ever run** |
| [hermit#1840](https://github.com/rrnewton/hermit/pull/1840) | `5da30bca4787` | `pin/reverie-0ae0c01b` | **ready**, MERGEABLE | 4ok/8, **RED** merge-gate-v4 |
| [hermit#1754](https://github.com/rrnewton/hermit/pull/1754) | `a8ef3de82af4` | `rename/dbi-to-dbt` | draft, **CONFLICTING** | 0ok/0 |
| [hermit#1767](https://github.com/rrnewton/hermit/pull/1767) | `7c7838fb3516` | `codex/hermit-dynamorio-plugin` | draft, **CONFLICTING** | 14ok/27, RED |

#389 moves instrumentation counters out of the ptrace crate — the core of the zero-ptracer story.
#391 is the litmus itself. #1840 is the only path by which landed reverie arc work reaches hermit.

### SaBRe

| PR | head | branch | state | CI |
| --- | --- | --- | --- | --- |
| [hermit#1729](https://github.com/rrnewton/hermit/pull/1729) | `22475a53d6d9` | `feat/sabre-routed-syscall-signal` | draft, MERGEABLE | 3ok/8, RED merge-gate-v4 |
| [hermit#1747](https://github.com/rrnewton/hermit/pull/1747) | `01c6f6aec678` | `sabre-handshake-version-guard` | draft, MERGEABLE | 3ok/8, RED merge-gate-v4 |
| [hermit#1811](https://github.com/rrnewton/hermit/pull/1811) | `957438055bff` | `fix/clippy-nonminimal-bool-sabre-ptrace` | draft, MERGEABLE | 5ok/21, RED |
| [hermit#1718](https://github.com/rrnewton/hermit/pull/1718) | `57af652a7056` | `fix/dbi-detlog-record-framing` | draft, MERGEABLE | 6ok/28, RED incl. P0 demo gate |

#1729 directly attacks the `patched_sites=0` ambiguity above. #1718 is shared DETLOG-renderer code
hitting both SaBRe and DBI. Also incidental: hermit#1744, #1754, #1767, #1840, reverie#390.

### e9patch

| PR | head | branch | state | CI |
| --- | --- | --- | --- | --- |
| [reverie#377](https://github.com/rrnewton/reverie/pull/377) | `bea5abfca122` | `feat/e9patch-hybridptrace-lifecycle-owne*` | **ready**, MERGEABLE, `post-facto-human-review` | 6ok/17, **RED** merge-gate-v2 |

The furthest-along patching-backend PR and the only arc PR both ready and labelled.

### DBT / DBI — listed separately, non-patching

| PR | head | branch | state | CI |
| --- | --- | --- | --- | --- |
| [reverie#390](https://github.com/rrnewton/reverie/pull/390) | `fa447d969db2` | `rename/dbi-to-dbt` | draft, MERGEABLE | 0ok/0 · **921 files** |
| [hermit#1754](https://github.com/rrnewton/hermit/pull/1754) | `a8ef3de82af4` | `rename/dbi-to-dbt` | draft, **CONFLICTING** | 0ok/0 · 147 files |
| [hermit#1689](https://github.com/rrnewton/hermit/pull/1689) | `ece949c5ba8b` | `dbi-honour-log-file` | draft | 7ok/28, RED |
| [hermit#1771](https://github.com/rrnewton/hermit/pull/1771) | `b9989fc45284` | `rebase/pr-1147-code` | draft | 4ok/8, RED |
| [hermit#1772](https://github.com/rrnewton/hermit/pull/1772) | `6a6f41c9777b` | `rebase/1147-b3` | draft | 2ok/31, RED |
| [reverie#394](https://github.com/rrnewton/reverie/pull/394) | `bb7b17f4dfa1` | `fix/dbi-scrub-dr-init-residue-from-guest*` | draft | 2ok/5, RED |

### CI shape across the arc

16 PRs: **14 draft, 2 ready. Zero have a green authoritative gate** — every one is red or unrun on
`merge-gate-v2` (reverie) / `merge-gate-v4` or `core-review-protocol` (hermit). Three have no checks at
all (hermit#1754, reverie#390, #391). Two are CONFLICTING. **`reviewDecision` is `-` on all 16** — no
arc PR has a recorded review decision.

## Gaps and next critical path

1. **reverie#377** (e9patch HybridPtrace lifecycle owner) is furthest along and the only ready+labelled
   PR; its `merge-gate-v2` is red. Unblocking that gate is the highest-leverage single action.
2. **reverie#391** (zero-ptracer litmus) has never run CI. Until it lands there is no in-tree assertion
   that the ptracer is quiet — so every "liteinst works" claim rests on a banner whose degraded form
   (`patched_sites=0; controller=ptrace`) looks like success.
3. **The dbi→dbt rename pair** (reverie#390 at 921 files, hermit#1754 at 147 and conflicting, both with
   0 CI) touches `reverie-liteinst/src/tool_host.rs`. Sequence it before or after the patching work,
   never concurrently.
4. **hermit#1840** is the sole conduit from landed reverie arc work into hermit; red on merge-gate-v4.
5. **Structural, not a PR:** no patching backend implements `handle_cpuid_event`, and none has an RCB
   clock. Those two gate a real strict envelope, and nothing in flight adds either.
