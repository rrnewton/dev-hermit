# PR #1559 env-hash @ `dfeacd151` — independent adversarial review (re-run after the lost verdict)

**Task:** `env-var-hash-in-info-log` · **Reviewer:** hermit-clone (opus-5, Claude-family), 2026-08-05
**Trigger:** the 05:16Z handoff says the adversarial review launched at 05:14Z left no verdict note →
"review result was lost to recycle; RE-RUN before landing." This is that re-run.
**Constraints:** egress 403 (cannot read PR/CI state); read-only (slot is another agent's).
**No green claimed** — nothing built or run under test here; all findings are from code and git objects.

## Verdict

**PASS on all six checks A–F**, plus one previously-open design decision now **resolved in the
implementation's favour**. Two new findings; Finding 1 is cheap and I recommend fixing before land.

---

## Checks A–F

| | check | result |
|---|---|---|
| A | exact delta, no base drift | ✅ exactly 6 files, **+348 / −0**; 2-commit stack; anchored past `bfb0a9ef` (rc=0). No `procfs.rs`/`files.rs`. |
| B | new Config field at all struct-literal sites | ✅ 4 sites: `testutils` BOTTOM/MIDDLE/TOP_CFG, `metadata.rs`. See caveat below. |
| C | DBI gate set **only** for `Backend::Dbi` | ✅ single assignment in `prepare_backend_config`; ptrace/KVM read path unweakened. |
| D | env-hash test non-vacuous | ✅ asserts zero-cost default, 64-hex shape, run-vs-run determinism, order-independence (`{A,B,C}` vs `{C,B,A}` → identical hash), value-vs-key isolation, key-add. Real positive **and** negative assertions. |
| E | INFO-gating is genuinely zero-cost | ✅ `if tracing::enabled!(Level::INFO)` wraps the whole read+hash at the call site. |
| F | DBI early-return is availability, not correctness | ✅ emits `[env, dtid N] unavailable`; no guest-visible effect, no error propagated. |

**Caveat on B (not a defect, a rebase obligation):** the branch is now **19 commits behind
`origin/main`** (base `d5355051`, origin/main `b64d893a`). Because `Config` is constructed with
struct literals, any literal added upstream in those 19 commits is a compile error after rebase.
Re-run the literal check as part of the rebase, not just `cargo build` on the old base.

---

## Resolved: the fix-shape decision that was left pending

The 04:54Z note deferred the choice — *"if catchable Rust panic → `catch_unwind` belt; else honest
Config-gate"* — pending a read of DBI's memory accessor. **I read it.**

`reverie-memory/src/local.rs:66-74`:

```rust
fn read<'a, A>(&self, addr: A, buf: &mut [u8]) -> Result<usize, Errno> {
    // ... is very unsafe. We need a better way to do this.
    unsafe { ::core::ptr::copy_nonoverlapping(addr.as_ptr(), buf.as_mut_ptr(), buf.len()) };
```

A raw `copy_nonoverlapping` with **no address validation**. A bad address is a hardware **SIGSEGV**,
not a Rust panic — so `catch_unwind` **would not have caught it**. The Config-gate was the correct
choice and the rejected alternative would have silently failed to fix the crash. The claim in
`Config::initial_stack_unavailable_at_post_exec`'s doc comment ("a raw SIGSEGV under the in-process
`LocalMemory` accessor, which never returns a recoverable `Errno`") is **accurate as written**.

This also explains *why* ptrace and KVM are safe: they use out-of-process accessors that return real
`Errno`s, so `hash_guest_env`'s `read_word(...)?` → `EFAULT` → the graceful `[env] unavailable`
handler already works there.

---

## Finding 1 — the gate is a deny-list of one, but the hazard covers **four** backends (recommend fixing before land)

`Backend` has six variants (`hermit-cli/src/lib.rs:595-610`): `Ptrace, Dbi, Liteinst, Sabre, Kvm,
E9patch`. The gate marks exactly one:

```rust
config.initial_stack_unavailable_at_post_exec = backend == Backend::Dbi;
```

But the hazardous precondition — use of the unvalidated `LocalMemory` accessor — is present in
**four** backends:

| crate | uses `LocalMemory` | gated? |
|---|---|---|
| `reverie-dbi` | **yes** | ✅ |
| `reverie-liteinst` (`src/tool_host.rs`) | **yes** | ❌ |
| `reverie-e9patch` (`src/tool_host.rs`) | **yes** | ❌ |
| `experimental/reverie-sabre` (`src/tool.rs`, `callbacks.rs`) | **yes** | ❌ |
| `reverie-ptrace` | no | n/a (safe) |
| `reverie-kvm` | no | n/a (safe) |

Note the coincidence that makes this clean: **the two backends that are empirically validated
(ptrace, KVM) are exactly the two that do not use `LocalMemory`.** The exception list was derived
from the one backend that happened to be exercised by a CI test, not from the mechanism.

So `hermit --log info run --backend liteinst|e9patch|sabre` still performs the initial-stack read
through an accessor where a wrong address is an unrecoverable SIGSEGV — the identical mechanism that
produced `run_dbi_forwards_detcore_info_logs` exit 255.

**Calibration (what is and isn't established).** The *precondition* is **CONFIRMED** for all four by
the grep above. Whether each backend's `%rsp` is actually the entry-point `argc` pointer at its
post-exec hook is **UNVERIFIED** — I could not run them (third-party backends sit behind an
off-by-default cargo feature; local DBI is blocked by the DynamoRIO link/env class). It is entirely
possible e9patch/sabre are fine, since they start the guest at its real entry point. But by the PR's
own stated standard — *"or, worse, hash stack garbage into a spurious difference"* — an unverified
backend should be gated, not read.

**Suggested one-line fix — invert to an allow-list:**

```rust
// Only the out-of-process backends expose the entry-point stack at post-exec
// AND return a recoverable Errno on a bad read; the in-process LocalMemory
// backends (dbi/liteinst/e9patch/sabre) fault hard instead.
config.initial_stack_unavailable_at_post_exec =
    !matches!(backend, Backend::Ptrace | Backend::Kvm);
```

Same shape, same blast radius, and it fails safe for any backend added later — a new in-process
backend inherits `unavailable` rather than inheriting a SIGSEGV.

---

## Finding 2 — silent truncation can manufacture a false divergence (low severity, diagnostic integrity)

`hash_guest_env` bounds the scan:

```rust
const MAX_ENTRIES: usize = 1 << 16;
const MAX_BYTES:   usize = 1 << 20;
while entries.len() < MAX_ENTRIES && total_bytes < MAX_BYTES { ... }
```

On hitting either bound the loop simply `break`s and the partial set is hashed **as if it were the
complete environment**, with no marker. Consequences:

- Two backends that truncate at slightly different points emit **different hashes for an identical
  environment** — a false-positive divergence, which is exactly the failure mode the
  order-independence work was added to prevent ("worse than no hash at all").
- `count` reports the truncated count, which reads as a genuine env-size difference.

Linux permits ~2 MiB total for argv+envp (`MAX_ARG_STRLEN` is 128 KiB per string), so a 1 MiB
environment is legal, if unusual. The bound itself is right — unbounded scanning of a corrupt stack
is the worse failure. **What's missing is that the value doesn't carry its condition.** Per the
repo's own Proxy Binding rule, emit the fact:

```rust
detlog!("[env, dtid {}] count={} hash={} keys_hash={} keys={} truncated={}", ..., truncated);
```

(or emit `unavailable (truncated)`), so a bounded read is never mistaken for a complete one.

---

## Things I could not check (egress 403) — stated as unknown, not assumed

- **ci-portable run `30977055541`** at `dfeacd151` — the hosted leg that would actually prove the DBI
  fix. Cannot query. The DBI leg remains **unproven**; local DBI is blocked by the DynamoRIO
  link/env class.
- PR draft state, labels, mergeability, and whether a Codex-side review exists.

I make no claim about any of these, and I claim no review labels.

---

## Slot / registry discrepancy (flagged, not touched)

The 05:16Z handoff records `worktrees/envhash/hermit` as **CLEAN** at `dfeacd1512`. It is now
**dirty**:

```
## local-rebase-1559...origin/codex/env-var-hash-info-log-v2
 M hermit-cli/src/bin/hermit/backends.rs
```

`backends.rs` is **not** one of the 6 files in this change. Separately, `worktrees/ACTIVE.md:370`
still registers the slot to `hermit-envhash`, while the notes record an owner-authorized takeover by
`orc-coord`. Three-way disagreement between notes, registry, and filesystem. Per Invariants 2 and 5
I did not touch the slot. **Coordinator: attribute `backends.rs` before anyone rebases or
force-pushes this branch** — a rebase would collide with it.

---

## Recommendation

Land-blocking: nothing from me. Recommended before the next force-push (both are one-liners, and a
rebase is required anyway):

1. **Finding 1** — invert the gate to the allow-list form. This is the same bug class just fixed for
   DBI, still live for three backends, and it costs one line.
2. **Finding 2** — add `truncated=` to the emitted line.

Then rebase onto current `origin/main` (19 behind), re-check `Config` struct literals, re-run the
ptrace test, and re-fire hosted ci-portable at the new head.

## Evidence index

- Delta/anchor: `git diff --stat origin/main...dfeacd1512`; `merge-base --is-ancestor bfb0a9ef`
- Gate: `hermit-cli/src/lib.rs:~1474`; field `detcore-model/src/config.rs:85-96`
- Emitter: `detcore/src/lib.rs` `detlog_guest_env` (~L793), `hash_guest_env` (~L824), call site
  (~L1503, INFO-gated)
- Unvalidated accessor: `reverie/reverie-memory/src/local.rs:66-74`
- `LocalMemory` users: `reverie-dbi/src/lib.rs`, `reverie-liteinst/src/tool_host.rs`,
  `reverie-e9patch/src/tool_host.rs`, `experimental/reverie-sabre/src/{tool,callbacks}.rs`
- Backend enum: `hermit-cli/src/lib.rs:595-610`
- Test: `hermit-cli/tests/cli.rs` `run_info_logging_emits_order_independent_env_hash`
