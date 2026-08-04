# crates.io name availability survey — Reverie/Hermit backend split

Task: `survey-cratesio-reverie-names` (2026-07-30). Method: crates.io API
`GET https://crates.io/api/v1/crates/<NAME>` via `with-proxy` — HTTP **404 =
AVAILABLE**, **200 = TAKEN**. Owner/activity pulled from the same JSON for taken
names. Checked interactively; re-run any row before relying on it (names can be
claimed at any time).

## Key structural fact

**crates.io has no namespace / prefix ownership.** Owning `reverie` does *not*
reserve `reverie-*`, and publishing `reverie-ptrace` does *not* require owning
`reverie`. So a prefix is never "squatted" wholesale — only exact-name
collisions matter. The flip side: you can never *own the family root* if someone
else already holds the bare word, which invites confusion and can't be unified
later under one top-level crate.

## Requested set

| Crate | Status | Owner / notes (if taken) |
|-------|--------|--------------------------|
| `reverie` | **TAKEN** | DiligentDilettante, "The agent framework of your dreams", v0.0.1, 22 dl, created 2026-03-26 — unrelated, single-version placeholder |
| `reverie-ptrace` | **AVAILABLE** | |
| `reverie-kvm` | **AVAILABLE** | |
| `reverie-liteinst` | **AVAILABLE** | |
| `reverie-sabre` | **AVAILABLE** | |
| `reverie-e9patch` | **AVAILABLE** | |
| `reverie-dynamorio` | **AVAILABLE** | |
| `reverie-dbi` | **AVAILABLE** | |
| `reverie-dbt` | **AVAILABLE** | |
| `reverie-syscalls` | **AVAILABLE** | |
| `reverie-process` | **AVAILABLE** | |
| `reverie-util` | **TAKEN** | yuma140902, "Utilities for reverie-engine", v0.7.0, **5251 dl**, since 2022 — ACTIVE unrelated project (a game engine) |
| `detcore` | **TAKEN** | aasyanov, "Minimal no_std deterministic state machine engine", v0.1.0, 21 dl, 2026-03-10 — unrelated, low-value |
| `hermit` | **TAKEN** | hermit-os, "The Hermit unikernel for Rust", v0.13.0, **20 804 dl**, since 2019 — PROMINENT, active project |
| `hermit-cli` | **AVAILABLE** | |
| `hermetic-reverie` | **AVAILABLE** | |

**In-set summary:** 12 AVAILABLE, 4 TAKEN. The only collisions are the two
family roots (`reverie`, `hermit`) plus `reverie-util` and `detcore`. Every
backend-specific `reverie-<backend>` name is free.

## Alternatives probed (for the recommendation)

| Candidate | Status | Note |
|-----------|--------|------|
| `reverie-utils` | AVAILABLE | drop-in replacement for taken `reverie-util` |
| `reverie-common` | AVAILABLE | alt replacement for `reverie-util` |
| `hermit-ptrace` / `hermit-kvm` / `hermit-backend` / `hermit-syscalls` / `hermit-detcore` | AVAILABLE | but root `hermit` TAKEN |
| `detcore-sched` | AVAILABLE | but root `detcore` TAKEN |
| `hermetic` | **TAKEN** | root not ownable |
| `hermetic-ptrace` / `hermetic-kvm` | AVAILABLE | root `hermetic` TAKEN |
| `detrace` | **AVAILABLE** | **root + members both free** |
| `detrace-ptrace` | AVAILABLE | confirms `detrace-*` root is ownable |

Roots that are TAKEN and thus NOT fully ownable: `reverie`, `hermit`, `detcore`,
`hermetic`. A root that IS fully ownable: `detrace` (illustrative; pick a final
coinage deliberately).

## Recommendation

Two workable schemes, depending on how much the team values owning the family
root word.

### Scheme A — keep `reverie-*` for the backends (lowest churn)

Publish the backends as `reverie-ptrace`, `reverie-kvm`, `reverie-liteinst`,
`reverie-sabre`, `reverie-e9patch`, `reverie-dynamorio`, `reverie-dbi`,
`reverie-dbt`, `reverie-syscalls`, `reverie-process` — all free today. Handle
the two in-set collisions:

- `reverie-util` → **`reverie-utils`** (or `reverie-common`).
- `detcore` → **`reverie-detcore`** (free; keeps it in the family) rather than
  fighting the taken bare `detcore`.

Caveats to accept: the bare `reverie` crate and `reverie-util` belong to
unrelated authors, so there is no top-level `reverie` crate to anchor the family
and mild confusion is possible. This is cosmetic, not blocking, given no prefix
ownership exists.

### Scheme B — adopt a fully-ownable distinct prefix (RECOMMENDED for a clean split)

Because **both** natural roots (`reverie`, `hermit`) are already taken by
unrelated projects, the only way to own the *entire* namespace — root crate plus
every backend — is a fresh, deliberately coined prefix whose bare word is also
free (e.g. the confirmed-free `detrace`, or another coinage the team vets with
this same API check). Then: `<prefix>` (umbrella), `<prefix>-ptrace`,
`<prefix>-kvm`, `<prefix>-syscalls`, `<prefix>-process`, `<prefix>-detcore`, etc.
This gives a coherent, unambiguous, disputes-free namespace and a real top-level
crate to hang docs/re-exports on.

**Guidance:** if minimizing rename churn dominates, take Scheme A (rename only
`reverie-util`→`reverie-utils` and `detcore`→`reverie-detcore`). If a clean,
self-owned published identity for the backend split matters, take Scheme B and
lock the coined root + all member names in one publishing pass (names are
first-come; reserve them together). Do **not** attempt to reuse bare `hermit`
(prominent unikernel, 20k downloads) — `hermit-cli` is free but the root is not.

## Addendum — core `reverie` crate contents + `reverie-traits`/`reverie-core` fit

Extra names checked: **`reverie-traits` = AVAILABLE**, **`reverie-core` =
AVAILABLE** (both 404).

Inspected `reverie/reverie/src/` (the core crate, `name = "reverie"`,
`version 0.1.0`). **Verdict: NOT trait-only — it is traits + event/syscall types
*plus* real supporting infrastructure.**

Traits (the API surface):
- `Tool`, `GlobalTool`, `GlobalRPC` (`tool.rs`), `Guest` (`guest.rs`),
  `Backend` (`backend.rs`), `RegDisplay` (`regs.rs`), `Stack` (`stack.rs`).

Non-trait / impl / infra code that also lives in the crate:
- **`backtrace/` (844 LOC, ~11 structs)** — symbolization + unwinding
  (`Symbolizer`, symbol cache, loaded-library map) built on `addr2line` +
  `object` + `memmap2`. Substantial implementation.
- **`auxv.rs` (147 LOC, 8 fns)** — ELF auxv-vector parsing.
- **`subscription.rs` (226 LOC, struct + 13 fns)** — the syscall `Subscription`
  filter set: a real data structure with logic, not a trait.
- **`regs.rs` / `stack.rs` / `rdtsc.rs` / `timer.rs` / `error.rs`** — register &
  stack pretty-printing, `Rdtsc` result type + parsing, timer/error types.
- Re-exports `reverie_process` (as `process`) and `reverie_syscalls` (as
  `syscalls`).

Dependency tell: the crate pulls `addr2line`, `object`, `memmap2`, `procfs`,
`raw-cpuid`, `typed-arena`, and `nix` with the full `ptrace` feature set — infra
deps, not what a trait-only crate would carry.

**Naming fit:**
- **`reverie-core` fits the crate as-is** — "core" accurately covers central
  traits + shared supporting types/utilities (backtrace, auxv, subscription,
  reg/stack display). Recommended if no refactor is planned.
- **`reverie-traits` / `reverie-api` (trait-only) does NOT fit as-is** — it would
  misdescribe the ~1.2k LOC of backtrace/auxv/subscription/reg impl living here.
  It only fits after splitting that infra out (e.g. `backtrace` → its own crate,
  `subscription`/`regs`/`rdtsc` → a shared types crate). That is a *refactor*,
  not a rename.
- Both names are free, so the choice is about accuracy, not availability. If the
  team wants the trait-only name, plan the split first; otherwise ship
  `reverie-core`.

## Reproduce

```bash
for n in reverie reverie-ptrace ... hermit-cli hermetic-reverie; do
  with-proxy curl -s -o /dev/null -w "%{http_code} $n\n" \
    "https://crates.io/api/v1/crates/$n"   # 404=available, 200=taken
done
```
