# DynamoRIO `undefined reference` — root cause, and why there is nothing to delete

**Task:** `reverie-dbi-build-rs-dynamorio-panic-blocks-unrelated-prs` (P0)
**Date:** 2026-08-05 · **Mode:** local read + filesystem verification. No egress, **nothing deleted, nothing rebuilt.**

---

## The requested action could not be performed: the truncated object does not exist

I was asked to `rm` the truncated `scheduler_impl.cpp.o`, rebuild, and verify the link. **There is no
such file on this box.** Verified before touching anything:

| Check | Result |
|---|---|
| `find … -path '*dynamorio*' -name '*.o' -size 0` | **0 hits** |
| every real `scheduler_impl.cpp.o` (10 build trees) | **~13.23 MB each — all healthy** |
| zero-byte `.o` anywhere in workspace + `~/.cargo/git` | 55 — **all Rust `.rcgu.o` codegen units** (legitimately empty) plus old `scratch/` fixtures |

The only small `scheduler_impl.cpp.o` (1504 B) is at
`scratch/cmake-mtime-2026-08-06/proj/obj/` — a **synthetic reproducer** someone built for the
cmake-mtime experiment, not a DynamoRIO artifact.

Deleting nothing is the correct outcome here: there is no corrupt artifact to clear, so a `rm` +
rebuild would have proved nothing and the "link is green now" claim would have been unfounded.

## Root cause: the link failure is a *symptom* of an OOM kill, not an independent fault

Established from the failing run's own 922 KB log and the 2026-08-04 diagnosis, which records
**three** PR-independent environmental modes at `reverie-dbi/build.rs:339`:

1. **OOM** — `cc1plus` SIGKILLed at the inner cgroup `MemoryMax` **while compiling
   `drmemtrace scheduler_impl.cpp`** (#1470 / #1591; #1592 `strict_compat` at a 6.0 GiB cap).
2. **LINK** — `drmemtrace_launcher` undefined references to `scheduler_impl_tmpl_t<...>`, with the
   note that **`scheduler_impl.cpp` object is absent from the link line** (#1595 head `310a3689`,
   reaches ~43 %).
3. **TOOL CRASH** — `ext/drsyms` vendored elfutils `Aborted (core dumped)`.

**Modes 1 and 2 are one chain, not two bugs:**

```
per-step cgroup MemoryMax reached
   → cc1plus compiling scheduler_impl.cpp is SIGKILLed
   → scheduler_impl.cpp.o never produced (or produced truncated)
   → object absent from the link line
   → ld: undefined reference to scheduler_impl_tmpl_t<…>::set_cur_input(int,int,bool)
   → collect2 exit 1 → cmake exit 2 → build.rs:339 panic
```

So the symptom that reads as *"missing C++ template instantiation in DynamoRIO"* is not a source
defect at all. Corroboration already on file: **the same source builds clean rc=0 at `-j8` and
`-j32`** when memory is not constrained, and hosted `ci-portable.yml` / `ci-privileged.yml` build
and link this exact DynamoRIO correctly at exact head. Only the **locally boxed validate**, with
tight per-step caps, fails.

## Why it *stuck* — and this is where cmake-trusts-mtime is genuinely load-bearing

Two mechanisms compose to make an OOM-killed compile reproduce deterministically per slot:

1. **`memory.oom.group = 1` defeats delete-on-error.** I verified this session that the runner sets
   `memory.oom.group=1` at both scope and per-step level (`cgroup.py:582`; outer audit prints
   `memory.oom.group=1 (enabled)`). The whole cgroup dies **as a unit** — including the build tool
   that would otherwise unlink its partial output. Nothing is left to clean up after itself.
2. **cmake then trusts mtime, not content.** A zero-length or truncated `.o` bearing a *fresh*
   timestamp is considered up-to-date, so it is never recompiled — and the next build goes straight
   to the link with a symbol-less object.

That is the connection to `cmake-trusts-mtime-not-content`, and **the durable fix belongs there:
content-hash (or size≥0 sanity) validation of build outputs**, so a truncated object is rebuilt
instead of trusted. Note this is a *persistence* mechanism, not the trigger — it explains why one
OOM poisons a slot indefinitely, not why the OOM happened.

## The fix, both halves already in motion

**Trigger (memory) — largely addressed on main.** My cap audit earlier today measured the raised
values now live in `ci/dag/portable.json`: `test.strict_compat` **24.00 GiB** (was the 6.0 GiB that
OOMed), `test.hermit_unit` and `lint.clippy` **16.00 GiB** each, and Σ portable caps = 451 GiB =
59.8 % of the 754.8 GiB box even at full concurrency. The specific cap that produced the #1592 OOM
is gone. *Not re-validated here* — no validate was run.

**Blast radius (topology) — designed, unlanded.** The reason a third-party build fault reds
*unrelated* PRs is the DAG shape, which I quantified earlier today: `build.workspace` has **32
transitive dependents (68 % of the DAG)** and `build.runtime_release` a further 9 — and **24 of the
38 coupled nodes contain no third-party content at all**. The coupling is `--workspace`, not the
feature flag: `detcore-dbi` is a workspace member with a non-optional `reverie-dbi` dep, so even
`cargo clippy --workspace` builds DynamoRIO. PR **#1607** implements the product-only root +
downstream `build.third_party`; it is **not on main** (`build.third_party` absent at `b64d893a`),
and it needs the symmetric release-side split to cover `build.runtime_release`. See
`ai_docs/dag-third-party-downstream-edge-design-20260805.md`.

**Persistence (cmake) — the genuinely open item**, per above: validate build-output content, don't
trust mtime.

## Recommended sequence

1. **Do not chase a truncated object** — none exists; if one appears, it is a *consequence* of an
   OOM kill, and the cap is the thing to inspect first.
2. **Land the cmake content-validation fix** (`cmake-trusts-mtime-not-content`) so an OOM cannot
   poison a slot durably. This is the only unaddressed link in the chain.
3. **Land #1607 + the release-side split** so a third-party fault can only fail third-party nodes.
4. Re-run the previously blocked heads (`fedc81ed`, `310a3689`) through
   `ci-hub validate-run` **after** 2–3, and attribute any remaining red from its own `log_file`.

## Provenance

| Claim | Status |
|---|---|
| No zero-byte `.o` in any DynamoRIO tree; all `scheduler_impl.cpp.o` ~13.23 MB; 55 zero-byte `.o` are Rust `.rcgu.o` | **verified this session** |
| Raised caps (24/16/16 GiB), Σ 451 GiB = 59.8 % of box | **measured this session** |
| `memory.oom.group=1` set at scope + per-step | **verified this session** |
| `build.third_party` absent at `b64d893a`; 32/9 dependents; 24 incidentally coupled | **measured this session** |
| Three fault modes; OOM on `scheduler_impl.cpp`; clean rc=0 at `-j8`/`-j32`; hosted CI links fine; `--workspace` (not feature) is the coupling | inherited from 2026-08-04 notes — **not re-run** |
| PR #1607 / #371 state | inherited; **not verifiable — egress down** |
