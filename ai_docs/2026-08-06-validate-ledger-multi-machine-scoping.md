# Scoping the validate ledger for multi-machine global storage

**Task:** `scope-validate-ledger-multi-machine-global-storage`
**Date:** 2026-08-06 · **Producer box:** `devbig014` · **Status:** design scoping, no code changed
**Every number below was measured on the running system this session**, not recalled.

This note answers: what the local validate ledger *is* today, who writes it, who reads
it, what a multi-machine store would have to add, and what the storage options cost.
It does **not** pick an option — that is the owner's decision.

---

## 1. Current state (measured)

### 1.1 The ledger file

| Fact | Value | Evidence |
| --- | --- | --- |
| Path | `ignored/validate-run-ledger.jsonl` at the parent root | `ci-hub/lib/validate_status.rs:87` (`pub const LEDGER_REL`) — the literal lives **only** there |
| Git-tracked? | **No** | `git ls-files` → 0 entries; `git check-ignore -v` → `.gitignore:118 ignored/` |
| Live size | **635 rows**, 697 KB | `wc -l` |
| Producer hosts | **`devbig014` on 635/635 rows** — single-producer today | field census |
| Resolution | `HERMIT_VALIDATE_LEDGER`, else `$DEV_HERMIT_PARENT/ignored/…` | `hermit/validate.sh:464-466` |

A run whose `DEV_HERMIT_PARENT` is unset skips the append entirely
(`ci-hub/validate/aggregate.py:12-18`), so **the parent ledger already sees only a
fraction of the machine's runs.** That is a pre-existing completeness hole, independent
of the multi-machine question.

### 1.2 Record schema — field prevalence over all 635 rows

Universal (635/635): `schema_version`, `started_at`, `finished_at`, `host`, `slot`,
`cwd`, `profile`, `commit`, `git_depth`, `git_ahead`, `git_behind`, `result`,
`exit_code`, `checks`, `failures`, `real_seconds`, `user_seconds`, `sys_seconds`,
`log_file`, `gates`.

Partial: `cache_state` 559 · `selection_mode`/`commit_anchored`/`tree_dirty` 539 ·
`executed_tests`/`filtered_tests` 333 · `gates_run`/`gates_expected` 177 ·
`concurrent_validates`/`concurrency_proof`/`known_flaky_failure`/
`flaky_failed_substeps`/`solo_rerun_confirmation`/`solo_rerun_of` 174 · `dag_jobs` 173 ·
`toolchain` 141 · `tree` 139 · `raw_result`/`interruption_signal` 85 ·
`zero_byte_purged` 79 · `first_error_line`/`failed_substep_classes` 54 · `coverage` 51 ·
`reverie_pin_current` 47 · `repo`/`full_coverage` 17 · `reclassified_reason` 3 ·
`product_failures`/`killed_by_bound`/`killed_by_signal`/`incomplete_gates` 1.

`schema_version` distribution: **v1=76, v2=20, v3=346, v4=142, v5=51.** The producer
currently writes **v4** (`hermit/validate.sh:1553`); v5 rows are finalizer-minted.

Profiles present: `full` 406 · `portable-strict-compat-only` 182 · `portable-only` 24 ·
`only-portable` 18 · `quick` 2 · `envelope-only`/`selective`/`rr-compat-only` 1 each.
Note `portable-only` and `only-portable` are **two spellings of one concept** — a
consumer matching one string silently misses 18 or 24 rows.

### 1.3 Writers

| # | Writer | Site | Mechanism |
| --- | --- | --- | --- |
| W1 | `hermit/validate.sh` — the sole *primary* append | line construction `:1553-1576`, append `:1578-1596` | `flock -x 9` around `printf >&9` with fd `9>>`; **falls back to an unlocked `>>` when `flock` is absent** (`:1592`) |
| W2 | `reverie/validate.sh` | `:108` sets `VALIDATION_HOST` the same way | writes the same schema to its **own checkout-local** `ignored/`, not the parent ledger |
| W3 | `ci-hub/validate/finalize_receipt.py` — `upgrade_ledger()` | `:142-173` | read-all + `open(path,"w")` **full-file rewrite** |
| W4 | `ci-hub/validate/finalize_receipt.py` — `scan_and_finalize()` | `:228-289` | appends a schema-5 **clone** (`_clone_upgraded`, `:189-197`) |

**W3 has a self-documented data-loss race** (`finalize_receipt.py:180-188`): the rewrite
races a concurrent `validate.sh` `O_APPEND`, and an append landing between the read and
the rewrite is silently lost. W4 exists precisely to avoid it, and the clone **copies
`host`**, so provenance survives minting. Any multi-machine design must not reintroduce
a read-modify-write over a shared file.

`ci-hub validate-run` / `validate-lock` are **admission and locking**, not ledger
writers — they launch `validate.sh`, which does the append.

### 1.4 Consumers

The repo already maintains an authoritative reader registry:
`ci-hub/validate/tests/test_ledger_reader_allowlist.py`, a lint that fails on any
undeclared `ci-hub/` file naming the ledger. Declared readers (`DECLARED_READERS`,
`:56-92`):

| Consumer | Site | Role |
| --- | --- | --- |
| `validate/qualified_rows.py` | canonical accessor | defines the two invariants (order by `finished_at`, drop incomplete/aborted/zero-executed) |
| `lib/validate_status.rs` | `assess()`, `is_clean_full_pass` | authoritative Rust row parser |
| `ci-hub.rs` | CLI front door | dispatches; `ledger_path()` `:3253-3257`, `load_ledger_rows` |
| `validate/aggregate.py` | machine-wide unifier | see §1.5 |
| `validate/wall_cpu_ratchet.py` | `_baseline()` | drops non-pass before medianing |
| `history/query.py` | `LEDGER_REL` `:936`, `load_ledger_index` `:940` | `green_ledger` class `:927` |
| `validate/attribute_reds.py` | `:48` | RED taxonomy |
| `validate/anchor_select.py` | `:98`, reads `host` at `:436` | anchor selection |
| `health/pr_status.py` | `:555` | shells out to `ci-hub ledger qualified-rows` |
| `landing/rebase_wrapper.py` | `:166` docstring, reads `host` at `:355` | prose reference |

Named CLI consumers:

- **`ci-hub newest-green`** — `ci-hub.rs:3766` (`run_newest_green`) → `lib/history_queries.rs:173`
  (`newest_green`), which walks first-parent commits and calls `assess(rows, sha)` at `:190`.
  Cache read/write `ci-hub.rs:3610`/`:3624`.
- **`ci-hub apply-local-label`** — `ci-hub.rs:4193-4272`. Loads rows, `assess()`s the PR
  head, and on `Validated` shells to `ci-hub/validation/publish_receipt.py`. Called from
  `ci-hub/landing/land-pr.sh:238`.
- **Merge gate `verify_receipt.sh`** — `ci-hub/validation/verify_receipt.sh`. Invoked by
  `hermit/.github/workflows/merge-gate.yml:209` and `:575`, fetched **from an immutable
  dev-hermit commit** (`?ref=4b78d727f35bc8612ac460a6e270dda5f5df304c`), never from the PR
  under test — the policy holds.

**Known bypass, already counted** (`KNOWN_BYPASSES`, `:100-115`):
`ci-hub/remediation/protocol.py` `estimate_local_validate_cost()` takes `samples[-50:]`
— the last 50 by **file position**, unqualified. Violates both invariants; impact
currently near zero by luck, not design.

**Coverage hole I found in the registry itself:** `ci-hub/validation/publish_receipt.py`
and `ci-hub/validation/verify_receipt.sh` are **not** in `DECLARED_READERS`, and the lint
cannot see them because it text-matches the basename `validate-run-ledger`, which neither
file contains — `publish_receipt.py` receives the path as `--ledger`
(`:226`, read at `:237` via `read_rows`). So the two consumers closest to *landing
authority* are outside the reader registry. This is a registry defect, not a
correctness bug today, but it is exactly the "one verifier per authority, and audit
every call site" predicate failing at the audit step.

### 1.5 There is already a machine-wide tier, and already a *global* tier

**Machine-wide:** `ci-hub/validate/aggregate.py` sweeps every ledger and raw log on the
box into `ignored/validate-run-global.jsonl` (`:696`), described in its own docstring as
"the validate-run-ledger, extended to machine-wide" (`:27`). Glob set at `:158-163`.
Still one machine.

**Global (this is the important one):** a cross-machine, append-only receipt store
**already exists and is live**:

- Store: `rrnewton/dev-hermit`, branch **`validation-receipts`**
  (`publish_receipt.py:27-28`). Confirmed present at the remote, head `13f2ba8a`.
- Path: `validation-receipts/<owner>/<repo>/<sha>/<sha256-of-receipt>.json`
  (`publish_receipt.py:240`). **78 receipts published** (tree query).
- Content: `receipt["ledger_record"] = row` (`:106`) — the **entire ledger row,
  including `host`** — plus `log_sha256` of a preserved durable log
  (`preserve_log`, `:79-93`; 68 logs currently under `ignored/validation-evidence/`).
- Write mechanism: GitHub Contents API `PUT` (`publish:156-181`) — a **server-side
  commit**, no local clone, and immutability is enforced (`:161-166` refuses a path that
  exists with different content).
- Binding: an `<!-- locally-validated-receipt commit=… path=… sha256=… -->` comment on
  the PR (`bind_pr:194-219`), which `verify_receipt.sh:116` dereferences via
  `repos/<repo>/contents/<path>?ref=<commit>`.

**Consequence for this design task:** "should we use a git-tracked store?" is partly
settled — one is in production for the merge-gate leg. It avoids append conflicts not by
locking but by **one immutable file per receipt**. What does *not* exist is a *queryable*
global ledger: nothing reads `validation-receipts` to answer `newest-green`.

---

## 2. The host axis — what a multi-machine record must carry

### 2.1 What is recorded today

`host` is written by `hermit/validate.sh:461` as `hostname -s` → **short name**, and is
parsed by the Rust reader (`ci-hub/lib/records.rs:103`). `toolchain`
(`rustc --version`, `validate.sh:482`) is on 141/635 rows.

### 2.2 What is *not* recorded, and why it matters

**No consumer decision reads `host`.** The single shared qualifying predicate,
`ci-hub/validate/qualifying-receipt.json`, requires only `commit_anchored`, `tree_dirty`,
`profile`, `selection_mode`, `result`, `failures_max`, `executed_tests_min`, plus the
coverage clause — **zero host terms**. Grep for host reads across consumers finds only
`anchor_select.py:436` and `rebase_wrapper.py:355`, both of which *display* it. The one
genuine host-conditional consumer is `history_estimate` in `hermit/validate.sh:936-1010`
— a **runtime estimator**, which degrades same-host → any-host. So the precedent for
host-aware degradation exists, but only for a cosmetic quantity.

**The hardware-gated cell is invisible.** `validate.sh:2273` probes
`[[ -r /dev/kvm && -w /dev/kvm ]]`; when it fails, `run_full_backend_gates` calls
`note_backend_skip "KVM" …` (`:2296`). `note_backend_skip` (`:2283-2288`) writes **only
to stdout and the log file**. It never calls `record_ledger_gate` (`:1330-1334`), the
sole appender to `ledger_gate_names`. Therefore:

- the skipped backend appears in **no** `gates` entry,
- it is not counted in `gates_run`,
- `gates_expected` is derived **from `gates_run` itself** for a completed full run
  (`validate.sh:1517-1519`), so completeness is self-referential and cannot detect the
  omission,
- and it is not in `coverage`, whose `planned/executed/absent` nodes are DAG *test*
  nodes; I checked `hermit/ci/dag/{portable,privileged}.json` (47 and 8 steps) and **no
  step id names kvm/dbi/sabre/liteinst/backend/parity/pmu** — the backend gates are outer
  gates in `validate.sh`, not DAG nodes.

**Net: a box without `/dev/kvm` produces a `profile:"full"`, `result:"pass"`,
`failures:0` row that is byte-shaped identically to one from a box with KVM.** Today this
is masked because there is exactly one producer. It becomes a correctness hazard the
moment a second producer appears — which is precisely what this task proposes.

The `profile` field is a *partial* proxy: `portable-*` profiles are the ones declared to
need no PMU/CPUID (`validate.sh:84-85, 237-238`). But 406 of 635 rows are `full`, and
`full` is the profile whose hardware content silently varies.

### 2.3 Host identity is not even a stable key yet

`ci-hub/validate/aggregate.py:367` sets `"host": os.uname().nodename`, which on this box
returns the **fully-qualified** form, while `validate.sh` writes `hostname -s`. Measured
in `ignored/validate-run-global.jsonl`: **181 rows short-form `devbig014`, 145 rows
fully-qualified** — the same physical machine under two keys. Two consequences:

1. Any host-class filter would treat one box as two.
2. It violates the short-names-only rule in a durable artifact. **Recommended
   precondition for any multi-machine work:** normalize at `aggregate.py:367` to the
   short name, and add a lint that refuses a domain-suffixed host value.

### 2.4 The minimum host-class the record should carry

"Carry the condition with the value" applied to the host axis. A cross-machine consumer
needs enough to decide *by itself* whether a foreign green counts:

- `host` (short name, normalized) — provenance, not a trust decision.
- `kernel_release` (`uname -r`) — the CPUID/kernel-behaviour axis.
- `cpu_model` + vendor/family — the Zen5-vs-older discriminator the owner named.
- `kvm_available` (bool, from the probe already at `:2273`).
- `pmu_available` / real-counter access (the privileged profile's precondition).
- `cpuid_faulting_available` (the `--no-virtualize-cpuid` axis, `validate.sh:1233`).
- `backends_exercised: [...]` and `backends_skipped: [{backend, reason}]` — the direct
  fix for §2.2; cheap, since `note_backend_skip` already has both strings.
- `toolchain` — already present, should become universal.
- `container/cgroup boxing state` — a boxed and unboxed run are not the same claim.

The first six are one-line probes the script already performs or can perform for free.
`backends_skipped` is the highest-value single field: it converts a silent omission into
an observable condition, and it is useful **today, single-machine**, independent of any
storage change.

---

## 3. Trust model: is a green from box A honoured on box B?

Not uniformly, and the store should not decide it — the **consumer** should, from
fields the record carries.

Three classes:

1. **Host-invariant cells.** Pure-Rust unit/integration tests, lints, build gates. A
   green reproduces anywhere with the same `toolchain` and `tree`. Cross-machine
   honouring is sound.
2. **Host-parameterised cells.** Anything whose *result* is the same but whose *cost* or
   flake profile differs (timeouts, contention-sensitive tests, the skid/timeslice
   family). Honourable, but the record must carry the conditions that make a red
   attributable — `dag_jobs`, `concurrent_validates`, `real/user/sys_seconds` already do.
3. **Hardware-gated cells.** KVM (`/dev/kvm`), real-PMU counters, CPUID faulting. A green
   from a box lacking the hardware is **not evidence** for a box that has it, and vice
   versa a green *with* the hardware over-claims for a box without it. These must be
   host-class-scoped, and today they are not even labelled.

Practical shape: keep a single `accepts_green_class`-style clause in the one shared
predicate file (`qualifying-receipt.json` already has exactly this pattern for
`hard`/`soft-*`), and add a host clause there — e.g. `requires_host_class`, with
`same-host` / `same-class` / `any` levels. One edit tightens or loosens the floor for
every consumer, which is the existing design intent of that file. **Do not** scatter host
logic into the individual consumers; that is the drift the registry was built to remove.

One caution the record makes visible: `assess()` validates a commit if **any** row
qualifies. Merging N machines' rows into one queryable set therefore *monotonically
increases* what counts as green. Without a host clause, going multi-machine is a
silent loosening of the landing gate, not a neutral change.

---

## 4. Storage options

Scored against the four axes the task named. "Egress" note: the task brief stated egress
is blocked; **measured today from an agent shell, `with-proxy git ls-remote` and
`with-proxy gh api` against `github.com` both succeeded** (this session opened hermit PR
#1752 that way). The earlier 403 was a per-destination allowlist state, not a permanent
outage — but it *was* real, it may return, and `ci-hub validate-run` still fails closed
on its own fetch. Any design must survive intermittent egress.

| Option | Concurrent multi-writer append | Offline availability | Provenance / host-class | `newest-green` query cost |
| --- | --- | --- | --- | --- |
| **A. Status quo — per-machine JSONL, unshared** | Good on one box (`flock`, `validate.sh:1584`); W3 rewrite race is the one hole | Perfect — local file, no network | Carries `host`/`slot`/`cwd`; no host-class; nothing cross-machine | Cheapest: one local file scan, cached (`ci-hub.rs:3610`) |
| **B. Shared git-tracked *single* JSONL on a branch** | **Bad** — every producer appends to one file; two concurrent appends conflict on every push; this is why it is gitignored today | Good — full history after one clone; writes queue behind egress | Same fields; needs the host-class additions | Cheap after fetch; but conflict-resolution churn dominates |
| **C. Append-only object store, one immutable file per receipt** (what `validation-receipts` already is) | **Best** — writers never touch a shared file; content-addressed path; PUT refuses a differing body (`publish_receipt.py:161-166`) | Fetchable and cacheable; a full mirror is one clone; **writes need egress** | Already carries the whole ledger row incl. `host`; host-class additions ride along free | Needs an index — 78 receipts is a fine tree walk, 10k is not. Wants a derived index file or a per-branch cache |
| **D. Small service with an API** | Best — server serialises; can enforce schema and host-class at admission | **Worst** — a down/unreachable service blocks both write and read; needs an owner, uptime, auth | Best — can validate and normalise host-class at write time | Best — a real query endpoint, O(1) `newest-green` |
| **E. Database (shared Postgres/SQLite-over-NFS/etc.)** | Good (real transactions) — SQLite over a network FS is not | Poor — same availability coupling as D, plus schema-migration burden | Good | Best |

Observations, not a recommendation:

- **C is the only option with a live production precedent in this repo**, and it already
  solves the axis B fails on. Its weak axis is query cost, which is an *index* problem,
  not a store problem — and the local JSONL can remain the fast path (option A) with C as
  the durable cross-machine tier. That two-tier shape is what exists today, half-built:
  local ledger = authority for local queries; `validation-receipts` = authority for the
  merge gate. Nothing currently reads C for `newest-green`.
- **B is the option to be most sceptical of.** The file is gitignored for a reason, and
  the reason is exactly its failing axis.
- **D/E buy query power at the price of the availability property that A has for free.**
  On a box where egress has already been observed at 403, a store no machine can reach is
  worse than no store.
- Whatever is chosen, the **W3 rewrite race must not be replicated**: no consumer or
  finalizer may read-modify-write a shared multi-writer file.

---

## 5. What I could not determine

1. **How many machines are actually in scope.** All 635 ledger rows are `devbig014`. A
   second coordinator on another box (`devbig30`) is referenced in project memory, but I
   found no ledger, receipt, or config in this checkout that proves it produces validate
   runs, and I did not query it. The fleet size — and whether the boxes differ in
   kernel/CPU/KVM at all — is unmeasured, and it changes which options are worth the cost.
2. **Whether any receipt in `validation-receipts` came from a non-`devbig014` producer.**
   I counted 78 receipts and read the publishing code path, but did not download and
   census their `ledger_record.host` values.
3. **Query cost at scale.** `ci-hub.rs:1081-1089` records the honest basis strings
   "not measured: ledger/store scan cost history is not retained" for `newest-green`,
   `history`, and `local-history`. I did not benchmark the 635-row scan, so I cannot say
   where option C's tree-walk starts to hurt.
4. **Whether `git_ahead`/`git_behind`/`git_depth` are meaningful across machines.** They
   are relative to that box's fetch state; I did not check whether any consumer treats
   them as absolute.
5. **The full hardware delta that actually matters.** I enumerated the axes the code
   already gates on (`/dev/kvm`, PMU, CPUID faulting). I did **not** empirically show that
   a given test's verdict flips across two real machines — nobody has run the same
   validate on two boxes and diffed it. That experiment is the honest precondition for
   choosing a trust level, and it does not exist yet.
6. **`reverie/validate.sh`'s ledger, in practice.** I confirmed it is a second producer
   with the same host derivation writing checkout-locally, but did not census its rows.

---

## 6. Cheapest useful next steps (ordered; none require picking a store)

1. Record `backends_skipped` / `backends_exercised` in the ledger row — turns a silent
   hardware-conditional omission into an observable condition. Useful single-machine.
2. Normalize `aggregate.py:367` to `hostname -s` and lint against domain-suffixed host
   values; today one box appears under two keys, 181 vs 145 rows.
3. Add the host-class probes (`kernel_release`, `cpu_model`, `kvm_available`,
   `pmu_available`, `cpuid_faulting`) as additive fields at schema 6.
4. Declare `publish_receipt.py` and `verify_receipt.sh` in the reader allowlist, or widen
   the lint beyond basename text-matching so a `--ledger`-argument reader is visible.
5. Only then decide the store — and note that a host clause in
   `qualifying-receipt.json` is required *before* any second producer's rows become
   visible to `assess()`, because `assess()` qualifies on **any** matching row.
