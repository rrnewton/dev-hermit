# `--backend` prefix matching + CLI cleanup: UX baseline, exact patch, port inventory

**Task:** `backend-prefix-match-and-cli-cleanup` (P2, owner 2026-08-02)
**Date:** 2026-08-05
**Status:** UX-tested and fully specified. **No code written** — see §6 (no worktree slot;
policy bars feature work in the primary checkout). The patch below is mechanical.

Prior PR #1444 is CLOSED and its substance is **not** on `origin/main` (verified by an
earlier drain sweep). This is a re-implementation from scratch, scoped per the recorded
disposition: *"a small CLI-only change with exhaustive help and explicit zero/multiple-match
errors. Exclude performance tests, artifact relocation, inventory, and compatibility-count
churn."*

---

## 1. UX tire-kick: what a first-time user hits today

Run against the built binary (`hermit/target/debug/hermit`, read-only, no build):

**(a) The owner's target spelling does not parse — there is no `-b` at all.**

```
$ hermit -b ptr run /bin/true
error: unexpected argument '-b' found
```

The task says *"`hermit -b ptr run ls` resolves to ptrace"*, which presupposes a `-b` short
flag. It does not exist. Global short flags today are only `-l/--log` and `-h/--help`. **So
this task is two changes, not one: add `-b`, and add prefix matching.** The task description
states only the second.

**(b) No prefix matching, and the error is actually decent.**

```
$ hermit --backend ptr run /bin/true
error: invalid value 'ptr' for '--backend <BACKEND>'
  [possible values: ptrace, dbi, liteinst, sabre, kvm, e9patch]
```

clap already prints the exhaustive list on a bad value. Whatever replaces this must not
regress that — a hand-rolled parser that just says `unknown backend 'ptr'` would be worse
than what ships today.

**(c) The backwards-compat clutter is exactly where the owner said, verbatim in `--help`:**

```
      --backend <BACKEND>
          Select the process instrumentation backend. This is the preferred, global position
          (e.g. `hermit --backend ptrace run ...`); for backwards compatibility `run` also
          accepts `--backend` after the subcommand
```

Two sentences of parser trivia in the user-facing description of a flag. This is the
"complication" to drop.

**(d) Requirement #2 is already satisfied — preserve it, don't add it.** `--help` already
lists all six backends with per-variant descriptions:

```
          Possible values:
          - ptrace:   Use Reverie's ptrace backend
          - dbi:      Use the DynamoRIO backend
          - liteinst: Use the ptrace-hosted LiteInst hybrid with one Detcore Tool
          - sabre:    Use the SaBRe static binary rewriting backend
          - kvm:      Use the KVM backend
          - e9patch:  Preprocess the main ELF with e9patch, then use the ptrace runtime
```

> **A false lead I chased and killed:** my first grep used `-A 6` and showed only four
> backends, which looked like "help is missing kvm and e9patch" — a nice bug. It was my own
> truncation. There are no `hide` attributes on the enum. **No bug; requirement (2) holds
> today.** Recording it so nobody re-reports it.

**(e) The compat path works and has defined precedence.** `hermit run --backend ptrace
/bin/true` succeeds. `run.rs:1806-1810`: `self.backend = self.backend.or(global.backend)` —
subcommand-level wins, else global.

---

## 2. The change

### 2a. Add `-b` and a prefix-resolving value parser

`hermit-cli/src/bin/hermit/global_opts.rs:48-52` becomes:

```rust
    /// Select the process instrumentation backend.
    ///
    /// Accepts any unambiguous prefix: `-b ptr` resolves to `ptrace`, `-b kv` to
    /// `kvm`. An ambiguous prefix is an error that lists the candidates.
    #[clap(short = 'b', long, value_name = "BACKEND",
           value_parser = parse_backend_prefix)]
    pub backend: Option<Backend>,
```

Note `value_enum` is **replaced** by `value_parser`. That is the part to get right: `clap`
derives the `Possible values:` help block from `ValueEnum`, so a custom parser drops it
unless the possible values are restored explicitly. Keep them with
`#[clap(value_parser = parse_backend_prefix, value_name = "BACKEND")]` plus an explicit
`long_help` that renders the list from `Backend::ALL`, or retain `value_enum` and layer the
prefix logic in a `TypedValueParser`. **Either way, requirement (2) is a regression risk of
this change and must be asserted by a test** (§4), not assumed.

The resolver, alongside `Backend::ALL` / `as_str()` which already exist at
`hermit-cli/src/lib.rs:613-630`:

```rust
/// Resolve a backend name by exact match, else by UNAMBIGUOUS prefix.
///
/// Exact match always wins, so a name that is a prefix of another name stays
/// selectable. (No such pair exists today; this keeps the rule stable if one is
/// added, e.g. adding `ptrace2` must not make `ptrace` ambiguous.)
pub fn parse_backend_prefix(s: &str) -> Result<Backend, String> {
    if let Some(b) = Backend::ALL.into_iter().find(|b| b.as_str() == s) {
        return Ok(b);
    }
    let hits: Vec<Backend> = Backend::ALL
        .into_iter()
        .filter(|b| b.as_str().starts_with(s))
        .collect();
    match hits.as_slice() {
        [one] => Ok(*one),
        [] => Err(format!(
            "unknown backend '{s}' (possible values: {})",
            Backend::ALL.map(Backend::as_str).join(", ")
        )),
        many => Err(format!(
            "ambiguous backend prefix '{s}' matches: {}",
            many.iter().map(|b| b.as_str()).collect::<Vec<_>>().join(", ")
        )),
    }
}
```

**Exact-match-first is load-bearing**, not decoration: without it, adding any backend whose
name extends an existing one silently makes the shorter name unselectable. Cheap now,
impossible to retrofit after someone depends on the broken behaviour.

Ambiguity on today's six: `d`→dbi, `k`→kvm, `p`→ptrace, `e`→e9patch, `l`→liteinst are all
unique at one character; only `s`→sabre. So every backend is reachable by its first letter
today — worth saying in the help text.

### 2b. Delete the compat clutter from the help text

Only the user-facing doc comment changes (§2a shows the replacement). The mechanism at
`run.rs:1806-1810` **stays** until §3 lands, but its explanatory comment moves from a doc
comment (rendered in `--help`) to an ordinary `//` implementation comment. Users stop
reading about it; maintainers still see it.

### 2c. Retire the compat path (after §3)

Once no tracked caller uses `run --backend`, remove the `run`-level `--backend` field and
the `.or(global.backend)` merge. Until then it is live and must keep working.

---

## 3. Port inventory — every tracked `run --backend`

`git grep -l -- "run --backend"` = **17 files**, categorised:

| category | n | files |
| --- | --- | --- |
| **CODE / SCRIPT / TEST** (port first — these gate §2c) | **8** | `ci/test_harness.sh`, `detcore-dbi/src/lib.rs`, `hermit-cli/src/bin/hermit/run.rs`, `scripts/compat-map.sh`, `scripts/manifest-to-commands.rs`, `tests/e2e/lib/system-utils/_common.sh`, `tests/manifest-cli.rs`, `validate.sh` |
| user docs | 6 | `README.md`, `WHATS_WORKING.md`, `docs/{E9PATCH_COMPATIBILITY,OCI_RUNTIME_DESIGN,SABRE_COMPATIBILITY,USER_GUIDE}.md` |
| agent skills | 2 | `.claude/skills/{backend-reality-reviewer,progress-rubric}/SKILL.md` |
| **`ai_docs/transient/` — EXCLUDE** | 1 | `kvm-backend-results.md` |

Also present: the `run --backend=X` equals-spelling in `README.md` (×2),
`docs/OCI_RUNTIME_DESIGN.md`, `docs/USER_GUIDE.md` — a plain `s/run --backend/--backend/`
misses nothing here but the `=` form must be moved too, i.e. `run --backend=ptrace --` →
`--backend=ptrace run --`.

**Excluding `ai_docs/transient/`** per the disposition's "no inventory churn": those are
dated AI scratch notes recording what was run at the time. Rewriting history in them makes
the record less accurate, not more.

---

## 4. Test plan — both directions

Add to the existing CLI test suite (`tests/manifest-cli.rs` is the natural home; the prior
attempt cited 77 CLI tests):

**Positive**
1. `-b ptr` → `Backend::Ptrace`; `-b kv` → `Kvm`; `-b e` → `E9patch`; `-b l` → `Liteinst`.
2. Full names still parse: all six via `--backend <full>` and `-b <full>`.
3. Exact-match-first: a synthetic pair where one name prefixes another resolves the exact
   one. (Construct in the unit test over the resolver; no new backend needed.)

**Negative**
4. `-b s` is fine today (unique) — but `-b x` → error naming all six.
5. **Ambiguity fires:** the resolver must return the ambiguous-match error when >1 hit.
   Since no ambiguous prefix exists over today's six, test the *function* with an injected
   candidate list rather than asserting a CLI invocation — otherwise this branch is
   unreachable and untested, which is exactly the "guard with no failing mutant" problem.

**Regression guard on requirement (2)**
6. `hermit --help` output contains all six backend names **and** their descriptions. This
   is the one that catches the `value_enum` → `value_parser` regression risk in §2a. Assert
   on the rendered help string, not on the enum.

**Cleanup assertions**
7. `hermit --help` does **not** contain "backwards compat".
8. Repo grep: no tracked `run --backend` outside `ai_docs/transient/`.

**UX re-tire-kick after building** — re-run §1 (a)–(e) against the new binary and record the
before/after help block in the PR description.

---

## 5. Scope discipline

In: the resolver, `-b`, the help-text cleanup, the 8 code/script/test callsites, the 6 user
docs, the 2 skills, and the tests above. Out, per the disposition: performance tests,
artifact relocation, inventory regeneration, compatibility-count churn, and
`ai_docs/transient/`.

---

## 6. Why no code was written, and what unblocks it

`CLAUDE.md` Hard Invariant 1 — *"Never do feature development in a primary checkout"* — and
the Primary Checkout Invariant — *"all validation, testing, and feature work happens in
worktree slots only"*. **No slot is registered to `hermit-verify`** in `worktrees/ACTIVE.md`,
and provisioning is coordinator-only.

I checked whether to self-allocate and decided against it, because the pool is already in a
state a new slot would worsen — `scripts/allocate-worktree.rs --check-only`:

```
SLOT SPRAWL: 57 physical slot dirs under worktrees/ (advisory soft limit 12)
LANGUISHING: 39 slots with no file edits in >24h
worktree-registry: FAIL rows=57 correct_rows=48 drift_rows=9
```

Adding a 58th slot to a registry that already fails its own consistency check is not a
routine judgement call.

**Unblock:** a coordinator runs
`scripts/allocate-worktree.rs --agent hermit-verify --task backend-prefix-match-and-cli-cleanup --product hermit --hermit-branch cli/backend-prefix-match --purpose "..."`.
Then §2–§4 is a mechanical edit plus `cargo test -p hermit --bin hermit`. Building also needs
`LD_LIBRARY_PATH` to a libunwind (`ignored/haskell-drb/hostlibs` works on this host; the
system has none).

---

## 7. Limitations

- The binary I tire-kicked was built 2026-08-03 and predates #1595. The `--backend` surface
  is untouched by that work, so the baseline holds — but it is not literally HEAD.
- I did not build, so §2's code is unit-compiled only in my head; the `value_enum` →
  `value_parser` interaction with clap's help rendering (§2a) is the one place I'd expect a
  surprise, which is why test 6 exists.
- The port inventory is `git grep` over tracked files at this HEAD; untracked or
  generated callers are not covered.
- Ambiguity behaviour is unexercisable end-to-end today (no ambiguous prefix over six
  names); test 5 therefore targets the function, and that limitation should be re-checked
  whenever a backend is added.
