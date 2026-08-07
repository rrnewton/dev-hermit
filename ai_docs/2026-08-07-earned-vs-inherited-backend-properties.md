# EARNED vs INHERITED backend properties: 824 of 1054 patching-backend passes route through a ptracer

**Task:** `distinguish-inherited-from-earned-backend-properties` · **Agent:** hermit-w2 · **2026-08-07**
**Sources checked live:** hermit `9c233ed0b` (`worktrees/cc/hermit`), reverie primary `dd3c178e` (`main`).
**Scope:** research only — no code or schema was changed. The schema change this record implies is
specified in *"What to add"* and is left for an authorized task.

## The premise is confirmed, and it is larger than filed

The task cites `reverie-e9patch/README.md:34`. Verified verbatim on reverie `dd3c178e`, lines 34-38 —
and it enumerates **more** than the filed quote:

> Ptrace remains attached for process lifecycle, signals, timers, CPUID/RDTSC, **syscalls in the
> loader and shared libraries**, and **complete arbitrary-tool `Guest` semantics**. The current
> hybrid therefore prioritizes correctness over the intended in-guest fast path and **still incurs a
> ptrace stop at rewritten sites**.

So the inheritance is not limited to TSC. It covers the whole `Guest` contract, and persists *even at
the sites e9patch rewrote*.

## For e9patch it is not inheritance at all — the backend does not run

`hermit-cli/src/bin/hermit/run.rs:1681-1687` (hermit `9c233ed0b`):

```rust
fn runtime_backend(&self) -> Backend {
    if self.selected_backend() == Backend::E9patch {
        Backend::Ptrace
    } else {
        self.selected_backend()
    }
}
```

`runtime_backend()` is what is handed to the actual run (`run.rs:2941`, `:2950`, `:2984`). Hermit's own
test asserts this by name: **`e9patch_preserves_executable_identity_and_uses_ptrace_runtime`**
(`run.rs:729-748`), whose body is `assert_eq!(ro.runtime_backend(), Backend::Ptrace);`.

**Selecting `--backend e9patch` executes the ptrace runtime.** Every runtime property of e9patch is
therefore ptrace's *by construction*, not by resemblance. The only thing e9patch contributes to a run
is the ahead-of-time rewrite plus the overlay mount of the patched binary.

### The reporting layer does not say so

`run.rs:2657-2666` emits a backend banner for exactly two backends:

```rust
let backend_banner = match self.selected_backend() {
    Backend::Kvm => Some("KVM (reverie-kvm KvmGuest<Detcore>)"),
    Backend::Liteinst => Some("LiteInst host hybrid (reverie-liteinst patch runtime + ptrace Detcore Tool)"),
    _ => None,
};
```

LiteInst's banner is honest — it names the ptrace Detcore Tool. **e9patch falls into `_ => None` and
prints nothing at all**, so the one backend whose runtime is entirely ptrace is also the one that
announces nothing. Note the banner keys on `selected_backend()` while the run uses
`runtime_backend()`: the label and the executed thing are read from different functions.

## Per-property record

**INHERITED** = the property is produced by a ptracer in the syscall path, so it would become unproven
the moment the ptracer leaves. **EARNED** = evidence exists that does not route through ptrace.

### e9patch — 1 earned, 7 inherited

| property | verdict | source |
|---|---|---|
| ahead-of-time rewrite / patched-site reach | **EARNED** | `E9patchRewriter::prepare` (`reverie-e9patch/README.md`, "Preparation"); measured by counting mapped sites, not by executing a syscall |
| syscall determinization (DETLOG) | **INHERITED** | `run.rs:1682` → `Backend::Ptrace` |
| CPUID / RDTSC ("TSC-CLEAN") | **INHERITED** | `reverie-e9patch/README.md:34`; `run.rs:1682` |
| process lifecycle (fork/exec/exit) | **INHERITED** | `reverie-e9patch/README.md:34` |
| signals | **INHERITED** | `reverie-e9patch/README.md:34` |
| timers | **INHERITED** | `reverie-e9patch/README.md:34` |
| loader & shared-library syscalls | **INHERITED** | `reverie-e9patch/README.md:35` |
| arbitrary-tool `Guest` semantics | **INHERITED** | `reverie-e9patch/README.md:35-36` |

### liteinst — 1 earned, rest inherited

| property | verdict | source |
|---|---|---|
| in-guest patch-site interception | **EARNED** | `reverie-liteinst/src/tool_host.rs:309` calls the shared `drive_tool_syscall` |
| syscall determinization and all `Guest` semantics | **INHERITED** | hermit calls the **host-side** entry `LiteinstBackend::run_host_with_preload::<Detcore>` at `hermit-cli/src/lib.rs:1529`; a ptrace round-trip remains on every syscall. Its own banner says so (`run.rs:2660`). |

**The in-guest entry `run_with_preload` has no hermit caller** — verified by grep across
`hermit-cli/` on `9c233ed0b`: zero hits outside `run_host_with_preload`. The shared in-guest path
exists and is simply not dispatched to.

### sabre — inherited from a *private* ptracer

| property | verdict | source |
|---|---|---|
| in-guest patch-site interception | **EARNED** | `reverie-sabre` rewriting; but see the reach caveat below |
| syscall determinization and all `Guest` semantics | **INHERITED** | `sabre_ptrace::run` at `hermit-cli/src/lib.rs:1047` — a **private 1843-line ptracer**, not the shared one |

SaBRe is the worst case for a results table: its properties are inherited from a ptracer that is
*its own*, so a reader comparing "sabre vs ptrace" sees two columns that are both ptrace-mediated by
**different** ptracers. Separately, the fallback is a first-class counted path in that ptracer —
`ptrace_fallback_sites: usize` (`hermit-cli/src/sabre_ptrace.rs:53`) with its own log target
`hermit::sabre::fallback` (`:295`, `:312`, `:331`). When sites fall back, even the EARNED row above
becomes inherited, **with no change in how the cell renders** — the scorecard has no column for
`ptrace_fallback_sites`, so a fully-fallen-back run and a fully-patched run are the same row.

**0 of 3 backends have the ptracer out of the syscall path on landed code** — consistent with
`ai_docs/patching-backend-convergence-scorecard-20260806.md`, whose two cited line numbers have since
drifted (`lib.rs:1056`→`1047`, `lib.rs:1555`→`1529`, and the `run.rs:1924` downgrade citation no
longer matches anything); the facts hold at the corrected lines.

## How many current PASS verdicts are inherited

Counted from the four committed scorecards. **Every ratio names its corpus**, because they disagree:

| corpus | backend | PASS / n |
|---|---|---|
| `e9patch-scorecard.csv` | e9patch | **227 / 227** |
| `fullcorpus-scorecard.csv` | e9patch | 179 / 200 |
| `fullcorpus-scorecard.csv` | liteinst | 118 / 200 |
| `fullcorpus-scorecard.csv` | sabre | 164 / 200 |
| `scorecard.csv` | liteinst | 136 / 220 |
| `scorecard.csv` | sabre | **0 / 7** |

| backend | PASS / n (all corpora) |
|---|---|
| e9patch | 406 / 427 |
| liteinst | 254 / 420 |
| sabre | 164 / 207 |
| **all three** | **824 / 1054** |

> **824 of 1054 patching-backend cells are PASS, and every one of them was produced with a ptracer in
> the syscall path. At the runtime level the earned count is 0.**

For e9patch specifically all **406** passes are inherited in the strong sense — the backend's runtime
never executed.

### The corpus warning is not hypothetical here

`sabre` is **0/7** on `scorecard.csv` and **164/200** on `fullcorpus-scorecard.csv`. Same backend,
same table family, verdicts that would read as "totally broken" and "82% green". The seven
`scorecard.csv` sabre rows additionally carry an **empty `output_hash`** (0 of 7 non-empty), so that
denominator is not merely small, it is unpopulated.

Likewise e9patch is **20/20 reach** on the dedicated `-nostdlib -static -ffreestanding` corpus and
**4/137** on the shared dynamically-linked corpus
(`experiments/e9patch-detlog-parity_20260807/README.md`, hermit-w6). Those are the "20/20 and 4/137
are the same backend" figures from the dispatch, and they are 34× apart.

### One empirical corroboration, and its limit

On `e9patch-scorecard.csv` — where e9patch and ptrace were produced under a **single `run_id`**
(`e9patch-20260801`, hermit `b1fdeaf6`) — all 227 paired cells have **identical `output_hash` and
identical `outcome`**, with no empty hashes. That is what a tautological column looks like.

On `fullcorpus-scorecard.csv` the two backends are **separate `run_id`s** (`ptrace-fullcorpus-scorecard`
vs `e9patch-fullcorpus-scorecard`, both hermit `82a8e853`), and hash agreement drops to 25/200 while
**outcome** agreement stays at 198/200. **I do not claim the hash divergence is meaningful** — across
separate runs `output_hash` can move for reasons unrelated to the backend, so it is not a sound
discriminator here. The load-bearing evidence for e9patch remains the source fact (`run.rs:1682`), not
the hash identity.

## The schema cannot express any of this

`compat-envelope/scorecard.csv` has 23 columns — `run_id, run_utc, hermit_sha, reverie_sha, dirty,
run_mode, lane, bucket, test_id, test_mode, backend, cell_state, outcome, deterministic, parity,
output_hash, duration_ms, max_rss_kb, reason, verify_compare, bitwise_parity, compared_log_messages,
tier`. **None records provenance.** Grepping `earned|inherited|provenance` across all four CSVs,
`check_scorecard_schema.py`, and `render-scorecard.rs` returns zero hits.

So an inherited PASS and an earned PASS are today **byte-identical rows**, which is exactly the
failure the task was filed against.

### What to add (not implemented here)

A `property_provenance` column with domain `earned | inherited | unknown`, defaulting to **`unknown`
and never to `earned`** — the same fail-closed discipline the `tier` column adopted. Backfill: every
existing `e9patch` row is `inherited` (derivable mechanically from `run.rs:1682`); `liteinst` and
`sabre` rows are `inherited` for syscall-mediated properties. A row claiming `earned` should be
rejected unless it cites evidence not routed through ptrace.

The value of writing it down now is the task's own point: when the ptracer leaves the syscall path,
**824 verdicts become unproven simultaneously**, and without this record there would be no way to
tell which.

## Scope and limits

- **Static/source audit plus counts over committed CSVs.** I executed no backend runs for this task;
  every EARNED/INHERITED call is traced to a source line, and every ratio to a named corpus file.
- The scorecards are snapshots at their recorded SHAs (`b1fdeaf6`, `82a8e853`, and others), **not**
  current main. The counts describe what is committed, not a fresh measurement.
- `cell_state`/`outcome` semantics were taken at face value; I did not re-derive whether each PASS is
  itself well-founded (that is the falsifiability work in
  `ai_docs/newly-green-cell-falsifiability-and-scorecard-denominators_20260807.md`).
- DBI and KVM were not classified — neither is a patching backend, and the task scopes to
  e9patch/sabre/liteinst.
