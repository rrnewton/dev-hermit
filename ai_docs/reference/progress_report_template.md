# Hermit Progress Report Template

> **This is a template, not a report.** Copy it to a dated file under
> `ai_docs/transient/` (e.g. `ai_docs/transient/YYYYMMDD_progress_report.md`),
> then replace every `{{PLACEHOLDER}}` and delete the `> INSTRUCTION` callouts.
> A finished instance must contain **no** `{{...}}` tokens and **no** `>
> INSTRUCTION` lines. The template is derived from the assurance-level rubric in
> `ai_docs/transient/20260722_progress_rubric_v2.md`; keep it in sync with that
> rubric when the ladder or backend set changes.

> INSTRUCTION — Before writing anything, gather the raw facts once:
> 1. **Date/time:** capture the snapshot instant (local + UTC).
> 2. **SHAs:** `with-proxy git ls-remote https://github.com/rrnewton/hermit.git refs/heads/main refs/heads/frontier`
>    and the same for reverie. Record the exact SHA behind every claim.
> 3. **Counts:** run the level-defining and inventory commands (bottom of this
>    file). Fill the **Test accounting summary** table FIRST; every later number
>    must cite a bucket from that table, never a fresh ad-hoc count.
> 4. **Backend:** every grade names the backend it was measured on
>    (ptrace / dbi / kvm) and the `--log` verbosity in effect.

## How to read a grade (the assurance-level ladder)

Every result is reported at **the strongest level whose exact command exits 0**,
with the backend named and any voiding relaxation disclosed. Never report a level
you did not run the exact command for; "not measured at Lx" is not a failure
claim, it means the Lx command was never run green.

| Level | Exact command | What it proves | What it does NOT prove |
| --- | --- | --- | --- |
| **L0** | `cargo test <target>` (or `cargo nextest run`) exit 0 | Code compiles; the guest/unit test runs to a passing assertion under whatever mode the harness sets. | Nothing about determinism unless the harness itself invokes a strict Hermit run. |
| **L1** | `hermit run --strict -- <prog>` exit 0 | One deterministic execution in which any unsupported syscall panics (fail-closed) instead of passing through to the host kernel. | Reproducibility: one run is not compared against a second. |
| **L2** | `hermit run --strict --verify -- <prog>` exit 0 | Bitwise-reproducible observable behavior: two back-to-back strict runs produce identical stdout, stderr, exit status, and internal scheduler-step log. **This is the definition of "PASSING."** | Deep memory determinism: heap/stack contents are not hashed by default. |
| **L3** | `hermit run --strict --verify --detlog-heap --detlog-stack -- <prog>` exit 0 | Everything in L2, plus per-run content hashes of heap and stack maps written into the deterministic log and compared across the two runs. | Freedom from flakiness under load: one L3 pass can still be nondeterministic across many trials. |
| **L4** | The L2 **or** L3 command run 20 times, 20/20 exit 0 (state which) | Stability: the reproducibility guarantee holds under repetition/load, not by luck on one trial. | Anything about backends or modes other than the exact one measured. |

### Relaxations that VOID a level

A run using any of the following does **not** count at L1+ regardless of exit
code. Report it explicitly as "relaxed" and at **L0 only**:

- omitting `--strict` (default/permissive), or the bundle
  `--no-strict`/`--no-deterministic-io`/`--no-virtualize-*`;
- `--no-sequentialize-threads` (removes deterministic single-CPU thread order);
- `--no-rcb-time` when the workload's determinism depends on RCB preemption;
- `--verify-allow failure`/`both` used to mask a nonzero guest exit.

### Verbosity convention

State the `--log` level for each measurement (default `--log=info`). The L2/L3
pass/fail signal comes from Hermit's internal scheduler-step log compared by
`--verify`, **not** from `--log` output; `--log=debug`/`trace` change no verdict.

## Snapshot header

> INSTRUCTION — Fill every field. This block anchors the whole report.

- **Status date:** {{YYYY-MM-DD}} (live snapshot {{HH:MM TZ}} / {{HH:MM UTC}}).
- **Author / agent:** {{agent-id}}
- **Reporting branch (all frontier claims are against this branch):** {{branch}}

| Ref | SHA | Meaning |
| --- | --- | --- |
| Hermit `main` | `{{sha}}` | {{current landed product; PR #NNN is the tip}} |
| Hermit `frontier` | `{{sha}}` | {{N commits ahead / M behind main; merge base `{{sha}}`; CI state}} |
| Reverie `main` | `{{sha}}` | {{mechanism baseline; CI state}} |

> INSTRUCTION — "On the frontier branch" means code is merged into that branch;
> it does **not** imply any assurance level if frontier CI is red. Do not read
> code aggregation as an Lx result. Report the frontier diff size vs merge base
> ({{files changed, +ins/-del}}) as scope, not as a pass count.

## Test accounting summary (single source of truth for all denominators)

> INSTRUCTION — Fill this table FIRST. Every count elsewhere in the report must
> reference a bucket named here — never introduce a new denominator inline. A raw
> `cargo nextest list` count is an **inventory** (enumeration), not an Lx pass
> count; label it as such. Keep `main` and `frontier` denominators separate.

| Bucket | Ref / mode | Count | Level | Interpretation |
| --- | --- | --- | --- | --- |
| Cargo inventory (all suites) | {{main\|frontier}} | {{N funcs / M suites, K ignored}} | Inventory | `cargo nextest list`; enumeration only. |
| Hermit crate only | {{ref}} | {{N funcs}} | Inventory | Not all launch a guest. |
| System-binary smoke (e.g. `/bin/echo`, `/bin/ls`) | {{ref}} | {{pass/total}} | {{L0-relaxed\|L1\|L2}} | {{note strictness}} |
| Strict fail-closed applicable set | {{ref}} | {{pass / fail / ignored; mode-N/A}} | **L1 per case; L2 where harness verifies** | {{pass}}/{{applicable}} = {{%}}; the strict-policy metric. |
| rr syscall suite | {{ref}} | {{N funcs}} | Inventory / L0 | {{enabled rr program cases + harness invariants}} |
| Record/replay generated tests | {{ref}} | {{N}} | L0 | Generated coverage, not the OSS-app L2 comparison. |
| OSS application workloads (Node/JVM/SQLite/curl/…) | {{ref}} | {{pass/total byte-identical}} | {{L2-class; state if branch-only}} | stdout/stderr/exit comparison; name each failure + cause. |
| Unit tests (detcore, etc.) | {{ref}} | {{pass/total}} | L0 | {{PMU/CPUID-sensitive cases noted}} |

> INSTRUCTION — Add or remove buckets to match the actual campaign, but never
> merge two denominators into one number. If a count is a build result
> (`cargo ... --no-run` produced N executables) say "build result, not an Lx
> pass".

## Frontier status (primary focus)

> INSTRUCTION — This is the main body. The frontier is where active development
> happens; grade it honestly per backend. Every claim states its **branch**,
> **backend**, **level**, and the **bucket** from the summary table it draws on.

### Executive per-backend grade

| Frontier / capability | Backend | Highest level reached | Exact status (cite bucket) | Blocker to next level |
| --- | --- | --- | --- | --- |
| `hermit run` | ptrace | **{{Lx}}** | {{e.g. 69/89 applicable strict cases exit 0 (77.5%); L2 per-case by strict harness at --log=info}} | {{blocker}} |
| Explicit strict / fail-closed | ptrace | **{{Lx}}** | {{first-blocking syscalls + counts; subscription coverage gaps}} | {{blocker}} |
| DBI / DynamoRIO | dbi | **{{Lx}}** | {{e.g. N/89 guest cases exit 0 under cargo test = L0; L1 undefined until deterministic scheduler}} | {{blocker}} |
| KVM | kvm | **{{Lx or "below L0"}}** | {{does it execute the requested ELF? smoke count}} | {{blocker}} |
| Record/replay | ptrace | **{{Lx}}** | {{generated L0 count; OSS-app L2 count, state if branch-only}} | {{blocker}} |
| `run --verify` | ptrace | **{{mechanism note}}** | {{what --verify compares; heap/stack opt-in for L3}} | {{blocker}} |
| Debugging (GDB/LLDB/MCP) | ptrace | **functional axis (works/doesn't), not Lx** | {{what replay/breakpoints/etc. work; what is not landed}} | {{blocker}} |

> INSTRUCTION — Then write one subsection per backend/capability below, each with
> an explicit L0/L1/L2/L3/L4 status list. Mark unreached levels "not measured at
> Lx". Name the exact backend and log verbosity.

#### 1. Ptrace `hermit run`
- **L1:** {{...}}
- **L2:** {{...}}
- **L3:** {{...}}
- **L4:** {{... e.g. not established; no 20x campaign recorded}}
- Risks / open issues: {{...}}
- Promotion to next level: {{...}}

#### 2. DBI (`--backend dbi`)
- **L0:** {{count + caveats, e.g. xfails that returned before executing bodies}}
- **L1+:** {{undefined until deterministic scheduler exists?}}
- Promotion: {{...}}

#### 3. KVM (`--backend kvm`)
- **Level:** {{below L0? what does it actually run?}}
- Promotion: {{...}}

#### 4. Record/replay
- **L0:** {{generated tests}}
- **L2-class:** {{OSS-app comparison; branch-only? list failures + fix branches}}
- Promotion: {{...}}

#### 5. `run --verify`
- Scope: {{stdout/stderr/exit + normalized scheduler log = L2; heap/stack = L3 opt-in}}
- Caveats: {{idempotency limits, log normalization, known UX bugs}}

#### 6. Debugging (GDB, LLDB, MCP)
- **Works (functional):** {{...}}
- **Not landed / broken:** {{...}}

### Frontier CI and runner status

> INSTRUCTION — Queued/cancelled/failed results are **not** green and must not be
> reported as passing at any level. Name both lanes (GitHub-hosted vs
> self-hosted/PMU) and the exact run IDs.

- Hermit frontier: {{run id, lane, state}}
- Reverie: {{run id, state}}

### Open issues / blockers on the frontier

> INSTRUCTION — Group by area (rr gaps, record/replay fidelity, syscall/network,
> toolchain, QEMU/VM, tests/docs). Cite issue numbers.

| Area | Issues |
| --- | --- |
| {{area}} | {{#nn, #nn}} |

## Main catch-up status (secondary, reported at the end)

> INSTRUCTION — This section is deliberately last. `main` is the landed product
> baseline; the report's focus is the frontier. Here, summarize what `main`
> currently guarantees and what frontier work still needs to land on `main`.

### Main per-capability grade

| Capability | Main level (cite bucket) | Frontier delta to land | Level note |
| --- | --- | --- | --- |
| Ptrace | {{Lx on passing set}} | {{...}} | {{...}} |
| Strict/fail-closed | {{pass/89}} | {{...}} | {{stale frontier status docs? note if counts disagree}} |
| rr | {{...}} | {{...}} | Inventory only. |
| DBI | {{no selector? L0?}} | {{...}} | Frontier DBI is L0 at best. |
| KVM | {{...}} | {{...}} | Below L0. |
| Record/replay | {{N funcs}} | {{OSS-app L2 fixes}} | L0; OSS-app L2 is branch-only. |
| CI | {{main lane state}} | — | {{green? queued? red?}} |

### validate.sh — what a green local gate does and does NOT establish

> INSTRUCTION — `hermit/validate.sh` shares relaxed `HERMIT_RUN_ARGS` that do NOT
> include `--strict`. A green `validate.sh` establishes **L0** across the
> workspace plus relaxed smoke runs; it does **not** by itself establish L1–L4
> for any guest. Verify the current args and restate the per-check level.

| validate.sh check | Level | Notes |
| --- | --- | --- |
| Build / inventory | Build / Tooling | Not a test. |
| Workspace + integrations | **L0** | Harness sets the mode; wrapper adds no `--strict`. |
| detcore package | **L0** | PMU/CPUID-sensitive. |
| Concurrency stress / chaos | **L0** | Chaos, not `--strict --verify`. |
| rr suite | **L0** | rr guests under Hermit. |
| run / verify smoke | **L0-relaxed** | No `--strict` → void at L1+. |

## Recommended promotion order

> INSTRUCTION — Ordered, actionable. Anchor each step to a level transition or a
> named blocker from the frontier section.

1. {{Restore green main in both CI lanes …}}
2. {{Regenerate frontier evidence from generated results only …}}
3. {{Grade DBI honestly at L0 …}}
4. {{Keep KVM scoped as research (below L0) …}}
5. {{Integrate record/replay fixes; re-run OSS-app L2 …}}
6. {{Stabilize debugging bottom-up …}}
7. {{Run L4 (20x at L2/L3) campaigns on the ptrace passing set once main is green.}}

## Reproduction notes (commands used to produce this report)

Level-defining commands (backend defaults to ptrace; add `--backend dbi/kvm`):

```bash
# L0
cargo test --workspace                       # or: cargo nextest run --workspace
# L1
target/debug/hermit run --strict -- <prog>
# L2 (= "PASSING")
target/debug/hermit run --strict --verify -- <prog>
# L3
target/debug/hermit run --strict --verify --detlog-heap --detlog-stack -- <prog>
# L4: run the L2 or L3 command 20 times; require 20/20 exit 0.
```

Inventory / read-only evidence (not pass counts):

```bash
with-proxy gh pr list -R rrnewton/hermit --state open --limit 300
with-proxy gh issue list -R rrnewton/hermit --state open --limit 300
with-proxy gh run list -R rrnewton/hermit --limit 30
with-proxy gh run list -R rrnewton/reverie --limit 30
with-proxy git ls-remote https://github.com/rrnewton/hermit.git \
  refs/heads/main refs/heads/frontier
cargo nextest list --workspace --message-format json   # inventory only
git rev-list --left-right --count origin/main...origin/frontier
```

Voiding relaxations to reject when reading any run's logs: missing `--strict`,
`--no-strict`, `--no-sequentialize-threads`, `--no-deterministic-io`,
`--no-virtualize-*` on a determinism claim, or `--verify-allow failure|both`
masking a nonzero exit. Any such run is L0-relaxed, never L1+. No open-head or
frontier code-aggregation figure is a combined release claim at any level.

## Instantiation checklist

> INSTRUCTION — Before publishing an instance, confirm:
> - [ ] File copied to `ai_docs/transient/YYYYMMDD_*.md`; no `{{...}}` tokens left.
> - [ ] All `> INSTRUCTION` callouts removed.
> - [ ] Snapshot date + local/UTC time filled.
> - [ ] Exact SHAs for hermit main, hermit frontier, reverie main recorded.
> - [ ] Test accounting summary filled first; every later count cites a bucket.
> - [ ] Every grade names backend, level, and `--log` verbosity.
> - [ ] Every frontier claim names the branch it is measured against.
> - [ ] Voiding relaxations disclosed for any run reported below its nominal level.
> - [ ] Frontier section is primary; main catch-up is last.
> - [ ] CI results are green/queued/red as observed — never queued reported as passing.
