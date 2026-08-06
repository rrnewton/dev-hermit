# `-b` short alias for `--backend` — collision audit, patch, and UX evidence

**Task:** `backend-short-flag-b` (P2) · **Date:** 2026-08-06 · **Author:** hermit-design
**Status:** change specified + UX-verified offline; **not applied** — needs a slot (see §6). No egress.
**Patch:** `ai_docs/backend-short-flag-b-patch-20260806.patch`
**Harness:** `scratch/backend-b-ux/harness.rs` (machine-local)
**Bound to:** hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef`

---

## 1. Why this is being re-done

PR #1444 implemented this on 2026-08-02 and was **closed**; the substance is not on `main`
(content-ancestry verified in the drain winnow, task refiled 2026-08-05 with the `implemented` tag
stripped). Confirmed independently here: `grep -rn "short = 'b'"` across hermit returns nothing, and
both declaration sites still read `#[clap(long, …)]`. So this is a fresh implementation, not a revival
of the seven-commit branch.

---

## 2. Collision audit — `-b` is free

Two declaration sites accept `--backend` today:

| Site | Current attribute |
| --- | --- |
| `hermit-cli/src/bin/hermit/global_opts.rs:51` (preferred, global) | `#[clap(long, value_enum, value_name = "BACKEND")]` |
| `hermit-cli/src/bin/hermit/run.rs:129` (backwards-compat, after the subcommand) | `#[clap(long, value_enum)]` |

Exhaustive sweep of every short flag in `hermit-cli` (`#[clap(…short…)]` **and** `#[arg(…short…)]`,
including the derived form where clap takes the first letter of the field name):

| Short | Field | Struct |
| --- | --- | --- |
| `-l` | `log` (derived) | `GlobalOpts`, and separately `bisect` |
| `-g` | `guest_log` (derived) | `analyze` |
| `-e` | `env` (explicit) | `run` |
| `-e` | `execution_context` (derived) | `analyze` — different subcommand, no clash |
| `-a` | (explicit) | `analyze` |
| `-o` | (explicit) | `bnz` |
| `-u` | (explicit) | `run` |

`grep -rn "short = 'b'"` over the whole hermit repo: **no hits.** `-b` is unused in `GlobalOpts`, in
`RunOpts`, and everywhere else.

---

## 3. The change

Add `short = 'b'` at **both** sites. Nothing else changes: the option id stays `backend`, so the
`conflicts_with = "backend"` reference at `run.rs:223` keeps working; the `value_enum`, the value set,
and the optionality are all untouched.

**Why both sites and not just the global one.** Both already accept `--backend`. Giving the short form
to only one would make `hermit -b kvm run …` work while `hermit run -b kvm …` fails (or vice versa) —
a surface where the short form works *sometimes* is worse for a first-time user than one that does not
exist, because the failure teaches the wrong lesson.

---

## 4. UX test from a first-time-user POV

### The blocker, stated first

**No hermit binary can run on this box.** Every prebuilt binary (primary release + debug, and the slot
builds) dies with:

```
error while loading shared libraries: libunwind-x86_64.so.8: cannot open shared object file
```

and `ldconfig -p | grep unwind` is empty — libunwind is simply not installed here, though
`AGENTS.md` lists `libunwind-dev` as a build prerequisite. So the owner's `./target/release/hermit`
verification could not be performed, and this section is **not** that verification.

### What was done instead

`scratch/backend-b-ux/harness.rs` reproduces both real declarations against the same clap major
version (4.6, matching `hermit-cli/Cargo.toml`), with `--after` flipping only the two attributes under
test. Everything else is held identical, so any difference is attributable to the change. Built and run
fully offline (`cargo run --offline`).

### BEFORE — what a first-time user actually hits

```
  -l, --log <LEVEL>        [env: HERMIT_LOG=]
      --log-file <FILE>    [env: HERMIT_LOG_FILE=]
      --backend <BACKEND>  Select the process instrumentation backend [possible values: ptrace, kvm, …]

  -b kvm            -> ERR: error: unexpected argument '-b' found
  -bkvm             -> ERR: error: unexpected argument '-b' found
  -b=kvm            -> ERR: error: unexpected argument '-b' found
  -l info -b dbi    -> ERR: error: unexpected argument '-b' found
```

The papercut is not merely that `-b` is absent — it is that the error says **"unexpected argument"**
and never mentions that `--backend` exists. A user who reaches for the short form gets no path to the
long one. Note also that `-l` already sets the expectation that this CLI has short flags, which is
exactly what makes reaching for `-b` the natural first move.

### AFTER

```
  -l, --log <LEVEL>        [env: HERMIT_LOG=]
      --log-file <FILE>    [env: HERMIT_LOG_FILE=]
  -b, --backend <BACKEND>  Select the process instrumentation backend [possible values: ptrace, kvm, …]

  -b kvm            -> Ok(Some(Kvm))
  --backend kvm     -> Ok(Some(Kvm))          <- same option, both spellings
  -bkvm             -> Ok(Some(Kvm))          <- clap's attached-value form, free
  -b=kvm            -> Ok(Some(Kvm))
  -b KVM            -> ERR: invalid value 'KVM' for '--backend <BACKEND>'
  -b nope           -> ERR: invalid value 'nope' for '--backend <BACKEND>'
  -b                -> ERR: a value is required for '--backend <BACKEND>' but none was supplied
  -l info -b dbi    -> Ok(Some(Dbi))          <- composes with the existing short
```

Three things worth noticing, because they are the difference between an alias and a second option:

* **`--help` advertises it** as `-b, --backend <BACKEND>`. An undiscoverable alias is barely an alias;
  the owner's acceptance criterion was exactly this line, and it is asserted in the tests.
* **Errors still name `--backend`**, the canonical long form, even when the user typed `-b`. One
  option, two spellings — not two options that can drift apart.
* **`-bkvm` and `-b=kvm` come free** from clap. Nothing extra was written for them; they are worth
  recording so nobody later "adds" them.

### The papercut this change does NOT fix

`-b KVM` is rejected — the value set is case-sensitive. That is a genuine first-time-user stumble and
it sits on the *value* half of this flag surface, not the flag-name half. It is deliberately left
alone here (§5).

---

## 5. Composition with the prefix-matching work

The two changes touch the same flag but different halves of it, and they compose cleanly **provided
`-b` is an alias rather than a new argument**:

| Half | Change | Owner |
| --- | --- | --- |
| flag **name** | `--backend` gains the spelling `-b` | this task |
| flag **value** | how `ptrace`/`kvm`/… are matched (prefix, and possibly case) | the prefix-matching work |

Because `short = 'b'` adds a spelling to the existing `backend` arg and leaves `value_enum` untouched,
any value-level matching the other change installs applies identically whether the user typed `-b` or
`--backend`. There is no second parse path to keep in sync. **The way to break this composition would
be to declare `-b` as its own argument** with its own value handling — which is why the patch's first
test asserts that `-b kvm` and `--backend kvm` produce the *same* parse, rather than merely asserting
that `-b kvm` works.

Two coordination notes:

* I could not locate the prefix-matching work in the tree (`grep -rn "prefix"` over `hermit-cli/src`
  finds only path-manipulation and tempfile prefixes; no slot under `worktrees/*/hermit` contains
  `infer_long_args` / prefix-matching code). So the composition claim above is reasoned from the
  attribute semantics, **not** verified against their actual diff. If their change adds
  `ignore_case = true` or a custom `value_parser` to these same two attributes, the two patches will
  textually conflict on the same lines even though they are semantically compatible — a trivial
  rebase, but worth expecting.
* Case-insensitivity (`-b KVM`) belongs on their side, not this one. Adding `ignore_case` here would
  be reaching into their surface.

---

## 6. Status and what remains

**The patch is not applied.** It touches `hermit/`, which requires a slot and a PR; slot allocation is
coordinator-only, the one slot I hold (`worktrees/coord/hermit`) is on an unrelated branch for
`port_validate_sh_to`, and mixing a second task onto that branch would violate one-task-per-branch.
Egress is 403, so nothing could be pushed regardless.

To land, in order:

1. Allocate a slot off current `origin/main`.
2. Apply `ai_docs/backend-short-flag-b-patch-20260806.patch` (two attribute lines + five tests).
3. `cargo test -p hermit-cli` — the five tests are pure clap parsing, no guest execution, so they run
   anywhere the crate compiles.
4. **Re-run the owner's own check on a host that has libunwind**:
   `./target/release/hermit --help | grep backend` must show `-b, --backend`, and
   `hermit -b kvm run ls` must parse. That is the acceptance criterion as stated, and it has *not*
   been met here.
5. Rebase if the prefix-matching change landed first (§5).

---

## 7. Not established

* **No hermit binary was run** — libunwind is absent from this box (§4). The UX evidence is from a
  clap harness reproducing the two declarations, not from hermit itself.
* **The patch has not been compiled against hermit.** The five tests are written against `RunOpts` /
  `GlobalOpts` as they read at `b64d893a`; the `crate::global_opts::GlobalOpts` path in the last test
  assumes the test module's position in `run.rs` and may need adjusting to the actual module path.
* **The prefix-matching change was not located**, so §5 is reasoned from clap semantics rather than
  read off their diff.
* `-b` freedom was established by grep over `hermit-cli/src`; a short flag declared through a
  `clap::Command` builder call rather than a derive attribute would not have been caught, though no
  such builder usage was found.
