# DETLOG record framing: full backend audit, and the seam that caused the drift

**Task:** `audit_detlog_record_framing`
**Date:** 2026-08-06
**Agent:** `egress-probe2` (opus-5)
**Change:** hermit branch `fix/dbi-detlog-record-framing` @
`60a460bfcb6563662119f08bc4148a62ef5537d1` (follows `c06d70bde`), slot
`worktrees/detlogframe/hermit`. **Not pushed** — egress 403.

---

## 1. The contract being audited against

`detcore/src/logdiff.rs:309` `extract_log_messages` is the only thing that turns
a log stream into comparable records. It splits on a leading RFC3339 stamp
(`\d+-\d\d-\d\dT\d\d:\d\d:\d\d.\d+Z +`), then requires each record to start with
a level tag.

So the stamp is a **load-bearing record separator**, not decoration. A backend
that omits it does not error — the level-tag check still passes — it silently
collapses its entire run into **one** record, and any cross-backend diff then
compares one message against thousands.

## 2. Full enumeration — all six, none assumed

| backend | where the Detcore tool runs | DETLOG path | framing |
| --- | --- | --- | --- |
| **ptrace** | supervisor | CLI `tracing_subscriber::fmt()` | **CANONICAL** (reference) |
| **kvm** | supervisor — `reverie-kvm` has no `reverie-rpc-transport` dep, so `GlobalState` is in-process | CLI `fmt()` | **CANONICAL** |
| **e9patch** | supervisor — `run.rs:1761` `runtime_backend()` maps `E9patch → Ptrace`, so the ptrace path literally executes | CLI `fmt()` | **CANONICAL** |
| **liteinst** | supervisor — `run.rs:2807` banner: "LiteInst host hybrid (reverie-liteinst patch runtime + **ptrace Detcore Tool**)" | CLI `fmt()` | **CANONICAL** |
| **dbi** | in-guest DynamoRIO client | hand-rolled `DbiSubscriber` | was **BROKEN**, fixed `c06d70bde` |
| **sabre** | in-guest plugin | hand-rolled stderr sink | **BROKEN** — found and fixed here |

Supporting negatives, so "not measured" is not confused with "fine":

* **reverie installs no production subscriber.** Its only
  `tracing_subscriber::fmt()` uses are `reverie-ptrace/src/testing.rs` (a test
  helper) and `reverie-util/src/commandline.rs` (a `reverie-examples` CLI helper).
* **detcore's other `impl Subscriber`** — `ErrorSubscriber`, `detcore/src/lib.rs:2460`
  — sits under the `#[cfg(test)]` at `:2430`. Not a production path.
* **`reverie-liteinst`, `reverie-e9patch`, `reverie-preload` emit no DETLOG at
  all** — consistent with their tool running in the supervisor.

## 3. The hypothesis I had filed was wrong

When filing this task I wrote: *"kvm and sabre most likely conform, because they
log through the CLI's `tracing_subscriber::fmt()`… but that is a HYPOTHESIS from
reading the architecture, not a measurement. Confirm each rather than reasoning
from the DBI case."*

KVM conforms. **SaBRe does not.** `hermit/detcore-sabre/src/lib.rs:98` installs a
`DetlogForwarder` that wrote straight to stderr:

```rust
let _ = stderr.write_all(b"INFO detcore: DETLOG ");
let _ = stderr.write_fmt(message);
let _ = stderr.write_all(b"\n");
```

No timestamp — the same defect class as DBI, arrived at independently. Its own
comment explains the deliberate bypass: *"A direct sink avoids tracing's
thread-local dispatcher: libc may issue its final exit_group after Rust TLS
destruction begins."* That is a **legitimate** reason to leave the subscriber —
it just carried an illegitimate framing along with it. Had I reasoned by analogy
from DBI ("in-guest = broken, supervisor = fine") I would have got the right
answer for the wrong reason and never checked KVM properly.

## 4. Root cause: the seam, not the backends

`detcore/src/detlog.rs`:

```rust
pub type DetlogForwarder = for<'a> fn(fmt::Arguments<'a>);
```

A sink receives **only the message** — no level, no target, no stamp. So every
out-of-supervisor backend must re-create the framing by hand, and both that tried
got it wrong. Backends whose tool runs in the supervisor never had to, which is
precisely why four of six were fine and nobody noticed for so long.

Patching two backends would have left the third one to come free to drift again.
So the renderer now lives **once**, in `detcore::detlog`:

* `canonical_record(stamp, level, target, fields)` — a full record.
* `canonical_detlog_line(message)` — the INFO/DETLOG sink shape.
* `next_record_stamp()` — the separator source.

Both offenders call it. **`detcore-dbi`'s private copy — which I added one commit
earlier in `c06d70bde` — is deleted**, rather than left as a second
implementation of the same contract.

The stamp remains a synthetic monotonic counter, not a clock: these sinks run
inside the traced process, so calling the clock would inject a `clock_gettime`
that Detcore intercepts and that advances virtualized time — enabling logging
would change the schedule it exists to observe. The value is discarded by the
splitter anyway.

## 5. Tests

In `detcore`, which compiles here: framing bytes, five-column level alignment per
level, stamp well-formedness across rollovers, monotonicity, and purity of
rendering.

The decisive one, `forwarded_detlog_line_is_split_by_the_real_consumer`, feeds
three sink-rendered lines to the **real** splitter through a crate-internal hook
and requires three records. Asserting against the actual consumer rather than a
copied regex matters: a copied regex keeps passing after the real one changes,
which is exactly how a framing contract rots.

**370 detcore tests pass**; `cargo fmt --check` and `cargo clippy -D warnings`
clean on detcore.

## 6. Verification limit

**`detcore-dbi` and `detcore-sabre` cannot be compiled on this host.**
`reverie-dbi` and `reverie-sabre` configure DynamoRIO/SaBRe with cmake, and cmake
is not installed (`failed to configure DynamoRIO: No such file or directory`;
`failed to configure SaBRe: …`).

This is better than the previous commit's position, though: the renderer they now
call **is** compiled and tested here. What is unverified is the two three-line
call sites. Both crates must still be compiled and the framing observed end to
end on a cmake-equipped host before this is treated as verified.

No runtime measurement of any backend's stream was possible; the enumeration is
source-derived plus detcore-level tests.

## 7. Residue

1. **Nothing pushed** (egress 403); needs a PR and an exact-head receipt.
2. **`dbi_log_file_is` [P2]** — `--log-file` is still ignored on DBI. With framing
   fixed the records now split correctly, but the two backends still deliver logs
   through different channels, so a comparison harness must special-case DBI
   (read stderr) instead of reading one artifact per backend. Cross-repo: needs a
   reverie emitter change plus a pin bump.
3. **cmake absent on this box** is now blocking three separate pieces of work
   (this, the DBI producer tests, and any DBI/SaBRe runtime audit). Worth fixing
   at the host level rather than routing around a fourth time.
4. **`dbi-detlog-heap-stack-parity`** is further unblocked: with both in-guest
   backends framing correctly, its decisive dtid-canonicalization experiment can
   finally compare real records. Its remaining confound — DBI's `dtid` being the
   raw host TID — is a determinism defect, not framing, and stays on that task.
