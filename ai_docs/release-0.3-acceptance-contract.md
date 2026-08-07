# Hermit 0.3 release acceptance contract

**Status:** binding gate definition. **Nothing here is claimed to pass.**
**Slug:** `release-0.3-acceptance-contract`
**Machine-readable twin:** [`release-0.3-acceptance-contract.json`](release-0.3-acceptance-contract.json)
**Authored:** 2026-08-06 PT, against parent `origin/main` `19b2804bf4708f7195295c191e536dd95d76634f`

The final `0.2.0 -> 0.3.0` bump is **forbidden** until every criterion below is
`PASS` at one single release-candidate SHA pair. This document decides *what
counts*; it does not measure anything.

---

## 0. How to read a criterion

Every criterion has eight fields. A criterion missing any of them is not a
gate — it is an opinion, and it does not block or unblock the release.

| Field | Meaning |
| --- | --- |
| **Source of truth** | The one artifact that answers it. A label, a copied status, a task note, or a prose summary is a *cache*, never a source. |
| **Denominator** | What the number is out of. A bare count is not a result. |
| **Threshold** | The exact comparison that decides PASS. |
| **Forbidden state** | A condition that makes the criterion FAIL *even if the threshold arithmetic passes*. This is where "green with zero tests executed" dies. |
| **Positive control** | Plant a qualifying case; the check must fire. Proves the check is not inert. |
| **Negative control** | Plant a violating case; the check must refuse. Proves the check is not a rubber stamp. |
| **Command** | Exactly what to run. |
| **Artifact** | Exactly where the evidence lands, committed. |

Three rules govern the whole contract.

**RRC-1 — One RC pair.** Every criterion is evaluated at one
`(hermit_sha, reverie_sha)` pair, re-read from the remote at evaluation time.
Evidence from any other SHA is not evidence. The operational-health audit
measured all three mains advancing within 17 minutes; a quoted SHA is stale by
default.

**RRC-2 — The tightening gate is binding.** Where a criterion concerns
determinism parity, `--verify` alone (the `Stripped` comparator) is
**forbidden**. A mutation test showed `Stripped` missed 3 of 5 planted
divergences. Only `--verify-strict --verify-json` with `bitwise_parity: true`
and both `compared_log_messages.left > 0` and `.right > 0` may be recorded. No
criterion in this contract may be satisfied by relaxing this.

**RRC-3 — Absence is not PASS.** A criterion with no artifact is `NOT-RUN`,
which blocks the release exactly as hard as `FAIL`. There is no third state
that lets the cut proceed.

**Version freeze.** While this contract is open, no crate version may change.
The `0.3.0` bump belongs to `release-0.3-semver-cut` and to no other task.

---

## 1. Strict determinism matrix (`AC-STRICT-*`)

### AC-STRICT-01 — Ptrace reference corpus is bitwise-green

- **Source of truth:** per-cell `--verify-json` reports under the artifact
  directory, one JSON per corpus cell. Not `SCORECARD-CURRENT.md`, which the
  state audit found to be a pre-head render (content commit `20b4a7d5`, from
  runs at Hermit `82a8e853` / Reverie `a4f33d69`, 2026-08-01) carrying **zero**
  bitwise cells.
- **Denominator:** the full published corpus at the RC SHA, cell count read
  from the corpus manifest at that SHA — **not** the historical 200. The
  contract does not hardcode 200; it requires the denominator to be *stated*
  and to equal the manifest count.
- **Threshold:** every cell reports `bitwise_parity: true`.
- **Forbidden state:** any cell with `compared_log_messages.left == 0` or
  `.right == 0` (a no-result wearing a green tick); any cell whose comparator
  is `Stripped`; any cell absent from the report set.
- **Positive control:** a known-deterministic guest (`/bin/echo`) is included
  and reports `bitwise_parity: true` with nonzero compared counts.
- **Negative control:** the planted-divergence mutation from the tightening
  work is re-run at the RC SHA; **all** planted divergences must be caught. If
  `Stripped` would have been used, ≥3 of 5 escape — that ratio is the proof the
  comparator choice is load-bearing.
- **Command:**
  `hermit run --strict --verify --verify-strict --verify-json <cell>.json -- <guest>`
- **Artifact:** `compat-envelope/rc/<rc-tag>/ptrace/*.json` + a `SUMMARY.md`
  stating the denominator, the pass count, and the two control outcomes.

### AC-STRICT-02 — Every backend claim names its execution context

- **Source of truth:** the same per-cell JSON, each carrying backend, log level,
  and relaxations.
- **Denominator:** all cells in AC-STRICT-01's report set.
- **Threshold:** 100% of cells record backend ∈ {ptrace, kvm, liteinst, dbt,
  sabre}, log level, and an explicit relaxation list (`none` when empty).
- **Forbidden state:** any cell with an unqualified "pass"; any cell where
  `relaxations` is absent rather than `none`; any KVM cell claiming L2 (KVM
  compares only exit status/stdout/stderr and reports `bitwise_parity: false`).
- **Positive control:** a KVM cell is present and is recorded as *not* L2.
- **Negative control:** a cell with a stripped relaxation field is rejected by
  the summariser rather than defaulted to `none`.
- **Command:** summariser over the report set.
- **Artifact:** the `SUMMARY.md` above.

### AC-STRICT-03 — Local validation receipt at the exact RC head

- **Source of truth:** `./ci-hub/ci-hub validate-status <rc-hermit-sha> --json`.
- **Denominator:** one RC head.
- **Threshold:** `qualifying_count >= 1` and `newest_qualifying` equals the RC
  head.
- **Forbidden state:** `verdict: NOT-VALIDATED` (the state audit measured
  exactly this at `1fadc037`, `exit_code 4`, `qualifying_count 0`); a receipt
  inherited from an ancestor; GitHub green substituted for a local receipt.
- **Positive control:** the receipt dereferences to a durable log whose
  executed-test count is nonzero.
- **Negative control:** `validate-status` on a fabricated SHA returns
  `NOT-VALIDATED`, not a silent pass.
- **Command:** `./ci-hub/ci-hub validate-status <rc-hermit-sha> --json`
- **Artifact:** `compat-envelope/rc/<rc-tag>/validate-status.json`

---

## 2. Runner-up backend threshold (`AC-RUNNERUP-01`)

The owner goal is "the runner-up backend exceeds 50%". That sentence is not yet
decidable: it names neither the measured quantity nor the denominator.

- **Source of truth:** the AC-STRICT-01 report set, extended to every non-ptrace
  backend at the RC SHA.
- **Denominator:** the set of cells that are **ptrace-green at the RC SHA**.
  Fixed here, because this is the field most likely to be quietly swapped.
  Not the whole corpus. The baseline used 179 ptrace-green of 200; that ratio
  must be recomputed at the RC SHA and stated. A backend cannot be penalised for
  a guest the reference backend cannot run either.
- **Threshold:** rank the non-ptrace first-party backends by
  *bitwise-determinism* rate on that denominator; the **second-highest**
  (the runner-up) must be `> 50%`. Both the metric and the ranking must be
  restated in the artifact: stdout parity and determinism are different numbers
  (the 2026-08-01 baseline had SaBRe at 78.8% stdout vs 91.6% determinism) and
  quoting whichever is higher is the failure mode this criterion exists to stop.
- **Forbidden state:** a rate computed on the full corpus rather than the
  ptrace-green denominator; a rate quoted from the stripped 2026-08-01 baseline;
  a rate where the runner-up is a *preprocessing path* rather than a backend —
  **e9patch is not a backend** and is ineligible for this ranking (it is ELF
  preprocessing followed by ptrace); a tie broken silently.
- **Positive control:** the winner and runner-up are both named with their
  numerator and denominator, and the ranking is reproducible from the JSON.
- **Negative control:** recomputing the same rate over the full corpus produces
  a *different* number, and the artifact shows both, so denominator substitution
  is visible rather than invisible.
- **Command:** ranking script over `compat-envelope/rc/<rc-tag>/*/*.json`.
- **Artifact:** `compat-envelope/rc/<rc-tag>/RUNNER-UP.md` with the full ranking
  table, both denominators, and the eligibility rule.

> The 2026-08-01 baseline shows every measured runner-up already above 50% on
> its eligible denominator. That is a **planning signal, not a pass**: none of
> those cells is bitwise-certified, and Table 2 of that scorecard is
> last-writer-wins across 7 Hermit SHAs.

---

## 3. Clean-checkout UX (`AC-UX-*`)

### AC-UX-01 — The documented default command is the supported default command

- **Source of truth:** a fresh clone, empty `CARGO_HOME`, no warm target.
- **Denominator:** the set of build commands a new user is told to run —
  currently `make` (Makefile `.DEFAULT_GOAL := build`), the README's
  `cargo build --workspace`, and the README's `cargo build --release`.
- **Threshold:** every documented command either (a) builds the first-party
  core with `rc=0` and zero warnings, or (b) is corrected in the docs before
  the cut. The cold-build audit measured
  `cargo build --locked -p hermit` at `rc=0`, 0 warnings, 51.08s wall —
  but that is **not** what the docs tell a user to type.
- **Forbidden state:** a documented default that enables
  `third-party-backends` while the crate declares `default = []` (measured:
  `make` does exactly this); **any package-manager mutation** — `make build`
  depends on `install-deps`, which can invoke `sudo -n apt-get install` /
  `dnf install`, and a release build must never touch host packages; a README
  claim that `cargo build --release` produces `install_pkg` when it does not.
- **Positive control:** a real strict guest run after the documented build:
  `hermit run --strict --verify --verify-strict` on `/bin/echo`, nonzero
  compared counts. (Cold-build audit precedent: 588 | 588 compared.)
- **Negative control:** the build is run with `LD_LIBRARY_PATH`,
  `LIBRARY_PATH` and `PKG_CONFIG_PATH` **unset**; it must still succeed with no
  environment fixups.
- **Command:** the documented command verbatim, in a throwaway clone, with
  `CARGO_HOME` created empty and verified empty (0 entries) before the run.
- **Artifact:** `experiments/release-0.3-rc-cold-build_<date>/` with `README.md`,
  `metadata.json`, `results.csv`, and raw logs.

### AC-UX-02 — Stated coldness limits

- **Source of truth:** the same artifact's `metadata.json`.
- **Denominator:** the isolation dimensions claimed.
- **Threshold:** the artifact explicitly records which dimensions were isolated
  and which were not.
- **Forbidden state:** an unqualified "cold build" claim. The prior audit
  isolated `CARGO_HOME` but **not** `RUSTUP_HOME`; the nightly toolchain was a
  host prerequisite and a host without it is uncovered. Also unresolved and
  required to be stated: the default build still reaches three git forges
  (`rrnewton/reverie`, `facebookexperimental/rust-shed`, `rrnewton/liteinst2`),
  so an air-gapped default build is currently impossible.
- **Positive control:** `CARGO_HOME` emptiness asserted by entry count, plus the
  `Downloaded`/`Compiling` line counts that prove the cache was cold (prior
  audit: 202 / 217).
- **Negative control:** re-running immediately produces materially smaller
  download counts, demonstrating the first run really was cold.
- **Command:** as AC-UX-01.
- **Artifact:** as AC-UX-01.

### AC-UX-03 — Backends refuse cleanly and help is truthful

- **Source of truth:** CLI output at the RC SHA.
- **Denominator:** every backend selector the CLI accepts.
- **Threshold:** a backend absent from the build exits nonzero with an
  actionable message naming the feature (measured precedent: `rc=1`,
  "backend `X` is unavailable: ... was not included in this build"); every
  backend named in `--help` is either buildable or documented as optional.
- **Forbidden state:** a crash, a hang, or an orphaned child process — the UX
  audit measured **KVM hanging and orphaning a child**, which is a release
  blocker in its own right; `--help` naming a backend the default build cannot
  produce, with no note; docs describing e9patch as a peer backend.
- **Positive control:** each available backend runs a real guest.
- **Negative control:** each unavailable backend refuses before starting a
  guest, and `pgrep`-free child accounting shows no orphan survives.
- **Command:** `hermit run --backend=<b> --strict -- /bin/echo rc-probe` for
  every `<b>`.
- **Artifact:** `experiments/release-0.3-rc-cold-build_<date>/backend-matrix.csv`

---

## 4. Packaging and distribution contract (`AC-PKG-*`)

### AC-PKG-00 — The distribution contract is chosen, in writing, first

This is the gate that unblocks or deletes the rest of §4, and it is an **owner
decision, not an engineering task**.

- **Source of truth:** a committed decision record naming exactly one of:
  **(A)** GitHub tag + source release, or **(B)** crates.io publication.
- **Denominator:** one decision.
- **Threshold:** the record exists, is committed, and names A or B.
- **Forbidden state:** starting §4's crates.io criteria without it; treating
  crates.io publication as an incidental final-cut step. Measured reasons this
  cannot be incidental: only 1 of 5 publishable crates
  (`hermit-resources`) packages cleanly; the other 4 fail through one
  dependency chain (`reverie-* 0.0.1 placeholder -> detcore-model ->
  hermit-detcore -> {hermit, hermit-verify}`); 21 git dependencies must become
  registry dependencies; and **the crates.io name `hermit` is not ours** — it is
  hermit-os's unikernel at **v0.13.0**, which already exceeds any 0.3.x, so the
  CLI cannot ship under that name at any version.
- **Positive/negative controls:** not applicable — this is a decision, not a
  measurement. It is listed as a criterion because the release cannot be
  mechanically decided while it is open.
- **Artifact:** `ai_docs/release-0.3-distribution-contract.md`

### AC-PKG-01 — (Contract A) Source release is complete and reproducible

- **Source of truth:** the published GitHub release for the RC tag.
- **Denominator:** the release asset set.
- **Threshold:** a SemVer tag exists; release notes exist; the source archive
  builds under AC-UX-01 from the archive alone.
- **Forbidden state:** no tag (measured: `rrnewton/hermit` has **0** releases
  and only `demo-*`/`archive/*` tags); no changelog (measured: Hermit has none;
  Reverie's stops at 0.1.0 in 2021); an archive that requires submodules the
  archive does not carry.
- **Positive control:** unpack the archive into an empty directory and complete
  AC-UX-01 there.
- **Negative control:** an archive with a deliberately removed required file
  fails the same build, proving the check reads the archive and not the
  worktree.
- **Command:** `gh release view <rc-tag> --repo rrnewton/hermit --json assets`
  then the AC-UX-01 build inside the unpacked archive.
- **Artifact:** `experiments/release-0.3-rc-source-release_<date>/`

### AC-PKG-02 — (Contract B) Registry publication order is satisfied

Applies **only** if AC-PKG-00 chose B.

- **Source of truth:** `cargo publish --dry-run` per crate, in dependency order.
- **Denominator:** all publishable crates (currently 5 of 13).
- **Threshold:** every publishable crate dry-runs `rc=0`.
- **Forbidden state:** any git dependency remaining in a published manifest; a
  flagship crate name that is not ours; publishing out of dependency order.
- **Positive control:** `hermit-resources` (zero path deps) dry-runs clean, as
  it already does — the one crate proving the pipeline works at all.
- **Negative control:** a crate with an unpublished path dep fails dry-run,
  as the current chain does; this must remain a failure, not be bypassed with
  `--no-verify`.
- **Command:** `cargo publish --dry-run -p <crate>` in order.
- **Artifact:** `experiments/release-0.3-rc-publish-dryrun_<date>/results.csv`

### AC-PKG-03 — First-party core excludes third-party payloads

- **Source of truth:** `cargo tree --locked` on the default feature set, plus
  the contents of the built package archive.
- **Denominator:** the full default dependency closure.
- **Threshold:** zero occurrences of DBT/DynamoRIO, SaBRe, or e9patch in the
  default closure **and** in the archive.
- **Forbidden state:** the monolithic installer staging all payloads into the
  core package (measured: `hermit-install` always stages DynamoRIO, DBI, SaBRe,
  e9patch, LiteInst and licenses); `release-core` initialising unrelated
  submodules; treating feature gating alone as the distribution boundary.
  LiteInst **is** first-party and is expected in the core closure — including
  `libreverie_liteinst.so` and the `liteinst2` dependency — so its presence is
  not a violation.
- **Positive control:** the opt-in tree
  (`--features third-party-backends`) *does* contain the payload crates, proving
  the check discriminates rather than always reporting absent.
- **Negative control:** a deliberately added payload dependency is detected.
- **Command:** `cargo tree --locked -p <core-cli> --no-default-features` and an
  archive listing.
- **Artifact:** `experiments/release-0.3-rc-core-closure_<date>/`

### AC-PKG-04 — Third-party payload packages carry licence and provenance

- **Source of truth:** each payload package archive.
- **Denominator:** the three payload groups: DynamoRIO/DBT (858 files,
  24,396,159 bytes, mixed BSD/LGPL/Valgrind), SaBRe (286 files, 1,510,679
  bytes, GPL/LGPL/BSD/MIT), e9patch (779 files, 12,334,251 bytes,
  GPL/LGPL/MIT).
- **Threshold:** each archive carries every applicable licence and a provenance
  record naming upstream source and revision.
- **Forbidden state:** a payload shipped without its licence set; an archive
  over the crates.io 10 MiB limit under contract B; a payload silently
  substituted by filename fallback.
- **Positive control:** the exact-version pair runs a real guest under
  `--strict --verify-strict`.
- **Negative control:** four distinct refusals must each fail *before the guest
  starts* — absent helper (exit 69), version mismatch, ABI/build-ID mismatch,
  and a tampered manifest or DSO.
- **Command:** archive listing + the four negative probes.
- **Artifact:** `experiments/release-0.3-rc-payload-packages_<date>/`

---

## 5. Syscall coverage (`AC-SYS-*`)

### AC-SYS-01 — The pinned census is exhaustive and guarded

- **Source of truth:** the checked-in classification test at the RC SHA.
- **Denominator:** the pinned `syscalls` crate's x86_64 row count (373 at
  `1fadc037`, last `lsm_list_modules` = 461). The number must be **read**, not
  hardcoded into this contract.
- **Threshold:** the test passes; classes sum exactly to the denominator; no
  missing, extra, or cross-class duplicate rows.
- **Forbidden state:** a wildcard arm that absorbs unknown rows without failing.
- **Positive control:** the landed census
  (289 Determinized / 83 PassThrough / 1 Unsupported = 373) is reproduced.
- **Negative control:** **already demonstrated and must be re-run at the RC
  SHA** — adding a synthetic `audit_future_syscall=462` to the pinned crate made
  the test fail to compile (`rc=101`, `E0080`) at the
  `Sysno::count() == EXPECTED_X86_64_SYSNO_COUNT` assertion. A guard that does
  not break under this mutation is inert.
- **Command:** `cargo test -p hermit-detcore every_pinned_sysno_has_an_explicit_classification`
- **Artifact:** `experiments/release-0.3-rc-syscall-census_<date>/`

### AC-SYS-02 — The live-ABI gap is declared, not silently absent

- **Source of truth:** a diff of the pinned denominator against the Linux
  master row set at a named kernel SHA.
- **Denominator:** the live Linux common/64 row count (385 at
  `c0a27675`, last `rseq_slice_yield` = 471).
- **Threshold:** every row present live but absent from the pin is enumerated
  and carries an open tracking issue.
- **Forbidden state:** shipping with the gap undocumented. Currently **12** rows
  are transport-unsupported (`uprobe`, `uretprobe`, `mseal`, `setxattrat`,
  `getxattrat`, `listxattrat`, `removexattrat`, `open_tree_attr`,
  `file_getattr`, `file_setattr`, `listns`, `rseq_slice_yield`), tracked at
  <https://github.com/rrnewton/hermit/issues/1831>; `restart_syscall` is the
  sole pinned-unsupported row, tracked at
  <https://github.com/rrnewton/hermit/issues/404>. Also forbidden: relying on
  the classification-issue corpus as authority — it is stale (288 open rows,
  238 unique names, 50 duplicate-title groups, 0 closed, while 176 of those
  names are now Determinized and 61 PassThrough).
- **Positive control:** the enumeration is regenerated at the RC SHA and matches
  the committed `CENSUS_JSON` schema `release-0.3-syscall-audit-v1`.
- **Negative control:** removing a row from the tracking set makes the check
  fail rather than shrinking the reported gap.
- **Command:** census generator at the RC SHA + kernel SHA.
- **Artifact:** `experiments/release-0.3-rc-syscall-census_<date>/census.json`

### AC-SYS-03 — Dispatch binds every classified row

- **Source of truth:** the dispatch-plan authority consumed by every backend.
- **Denominator:** the 289 Determinized rows.
- **Threshold:** each has a named handler; no row reaches unsupported handling
  or non-strict passthrough by falling through a wildcard.
- **Forbidden state:** the currently-measured gap — the Determinized typed match
  reaches a wildcard that lands in unsupported handling and non-strict
  passthrough, with no 289-row handler-plan proof
  (<https://github.com/rrnewton/hermit/issues/1793#issuecomment-5211486551>).
  **This criterion is known-FAIL today.**
- **Positive control:** a handler-plan enumeration equal in size to the
  Determinized class.
- **Negative control:** deleting one handler makes the enumeration fail.
- **Command:** to be provided by `release-0.3-syscall-drafts`.
- **Artifact:** `experiments/release-0.3-rc-syscall-census_<date>/dispatch-plan.json`

---

## 6. SemVer cut (`AC-SEMVER-*`)

### AC-SEMVER-01 — Version is single-sourced and moves exactly once

- **Source of truth:** `hermit-cli/Cargo.toml` and `hermit --version`.
- **Denominator:** every crate in both workspaces (13 Hermit, 25 Reverie).
- **Threshold:** the bump is `0.2.0 -> 0.3.0`; `hermit --version` reports it;
  the embedded git SHA equals the RC SHA and carries no `-dirty` suffix.
- **Forbidden state:** a hardcoded version string in any `.rs` file — the
  package audit's negative grep found **none**, and that must stay true; a
  `-dirty` build stamp in a release binary; any bump performed before every
  other criterion here is `PASS`.
- **Positive control:** after the bump, `hermit --version` shows `0.3.0` and the
  RC SHA.
- **Negative control:** the grep for a hardcoded `0.3.0` in `.rs` sources
  returns nothing, proving single-sourcing survived the bump.
- **Command:** `grep -rn '0\.3\.0' --include=*.rs . | grep -v target/` plus
  `hermit --version`.
- **Artifact:** `experiments/release-0.3-rc-semver_<date>/`

### AC-SEMVER-02 — Breaking-change claim is measured, not assumed

- **Source of truth:** a public-API diff between the `0.2.0` point and the RC.
- **Denominator:** the publishable crates.
- **Threshold:** either a breaking change is demonstrated (justifying `0.2 ->
  0.3` as the 0.x breaking bump), or the bump is justified in writing on other
  grounds.
- **Forbidden state:** asserting per-crate breakage without running the diff.
  The package audit explicitly declined to guess this, and this contract keeps
  that discipline.
- **Positive control:** `cargo-semver-checks` (or equivalent) runs and reports.
- **Negative control:** the tool detects a deliberately introduced breaking
  change in a scratch branch.
- **Command:** `cargo semver-checks check-release` per publishable crate.
- **Artifact:** `experiments/release-0.3-rc-semver_<date>/api-diff.md`

---

## 7. Upstream pull request (`AC-UPSTREAM-01`)

- **Source of truth:** the PR on `facebookexperimental/hermit`.
- **Denominator:** one PR.
- **Threshold:** it exists, targets upstream `main`, carries the RC SHA, and its
  required checks are green.
- **Forbidden state:** opening it before the RC exists; **ignoring the still-open
  prior import** <https://github.com/facebookexperimental/hermit/pull/88>
  ("Hermit 0.2 release import") — that is a state collision that must be
  resolved or explicitly superseded, not silently duplicated; creating any
  *issue* on `facebookexperimental/*` (upstream issues sync into an internal
  tracker — forbidden by parent policy).
- **Positive control:** the PR's head SHA equals the RC SHA.
- **Negative control:** a PR whose head has drifted from the RC fails the same
  equality check.
- **Command:** `gh pr view <n> --repo facebookexperimental/hermit --json headRefOid,statusCheckRollup`
- **Artifact:** `ai_docs/release-0.3-upstream-pr.md`

---

## 8. fbsource import and parity (`AC-FBS-*`)

### AC-FBS-00 — The import source point is chosen

- **Source of truth:** a committed decision record.
- **Denominator:** one decision, from three shapes: (a) re-establish `stable` on
  both repos as a true validated ancestor of `main`, then import stable→stable;
  (b) import from a specific validated `main` commit on each side and retire the
  `stable` indirection; (c) wholesale main import.
- **Threshold:** the record names one and gives its reason.
- **Forbidden state:** letting (c) happen by default — it is precisely the
  "blind mega-import" the audit exists to avoid. Also forbidden: assuming
  `stable` is a validated frontier. Measured: `rrnewton/hermit` `stable` is
  **not** an ancestor of `main` (merge-base `f853cc0f8`, `stable` carries one
  CI-only commit, `main` has moved 832 commits past); Reverie's `stable` has not
  moved in ~2 weeks and is not an ancestor of upstream `main` either.
- **Artifact:** `ai_docs/release-0.3-fbsource-import-plan.md`

### AC-FBS-01 — Two diffs, Reverie first, each standing alone

- **Source of truth:** the diff stack.
- **Denominator:** two diffs.
- **Threshold:** diff 1 = `fbcode/hermetic_infra/reverie/**` (plus
  `common/**`), builds and tests green **before** diff 2 is evaluated; diff 2 =
  `fbcode/hermetic_infra/hermit/**`.
- **Forbidden state:** hermit landing first, or a single combined diff. The
  order is **forced by the build graph, not by convention**: `buck2 uquery
  deps(hermit-cli:hermit, 3)` contains the reverie targets and the reverse query
  is empty — a one-way edge with no back-edge. It also matches the landed
  precedent (D113553864 Reverie < D113553943 Hermit).
- **Positive control:** `buck2 build fbcode//hermetic_infra/reverie/...` and
  `buck2 test fbcode//hermetic_infra/reverie/...` both green at diff 1.
- **Negative control:** hermit is built against a half-imported reverie and
  fails, proving the ordering constraint is real rather than ceremonial.
- **Command:** the two buck2 invocations above, then the hermit pair.
- **Artifact:** `ai_docs/release-0.3-fbsource-import-plan.md` with diff IDs.

### AC-FBS-02 — The mod.rs codemod is carried, not reverted

- **Source of truth:** the import diff's rename mapping.
- **Denominator:** 23 renames = 46 file operations (16 hermit, 26 reverie,
  4 common), from `f256a94da424`.
- **Threshold:** the import either carries the upstream→internal rename mapping
  or replays the codemod as a third mechanical diff.
- **Forbidden state:** a blind import that silently reverts the codemod back to
  the `mod.rs` layout the monorepo has already codemodded away.
- **Positive control:** post-import, zero `mod.rs` files remain in the imported
  subtrees.
- **Negative control:** a trial import without the mapping collides on all 23
  files — that collision is the proof the mapping is required.
- **Artifact:** as AC-FBS-01.

### AC-FBS-03 — Test baselines are met, with contention excluded

- **Source of truth:** `buck2 test` results.
- **Denominator:** Reverie unittest set; Hermit test target set.
- **Threshold:** Reverie ≥ the 361/362 baseline with `Fail 0`; Hermit's heavy
  suite re-measured on a quiet box with `Fail 0`.
- **Forbidden state:** quoting the contended Hermit number as a baseline. The
  audit measured `Pass 591 / Fail 109 / Timeout 161` at load average 54/175/398
  against 316 cores — the 161 timeouts are a contention signature, not a product
  signature, and the same core surface re-run `--local-only -j 8` gave
  `Pass 180 / Fail 0 / Timeout 0`. Also forbidden: closing the **1-test
  discrepancy** (361 measured vs 362 cited) by assertion; it must be resolved or
  explicitly accepted in writing.
- **Positive control:** load average recorded alongside the result.
- **Negative control:** the core surface re-run under load reproduces timeouts,
  demonstrating the measurement is load-sensitive and the quiet-box requirement
  is not ceremonial.
- **Artifact:** `ai_docs/release-0.3-fbsource-parity.md`

### AC-FBS-04 — Matching-binary parity

- **Source of truth:** the same guest run under the fbsource-built binary and
  the OSS-built binary at the RC pair.
- **Denominator:** a named smoke set including a racy multi-process guest.
- **Threshold:** identical observable results.
- **Forbidden state:** using the in-binary version string to identify the import
  — a local buck build self-reports `fbsource: unknown, fbpkg: unknown`, so the
  **SCM commit is the only reliable version handle**; a smoke set of only
  single-threaded one-liners.
- **Positive control:**
  `hermit_verify run --hermit-arg=--strict -- /bin/bash -c 'for i in 1 2 3; do echo $i & done; wait'`
  reports success (two runs captured and compared).
- **Negative control:** a deliberately perturbed guest diverges, proving the
  comparison is live. Note `hermit_verify` is a **separate binary** internally
  and takes `--hermit-arg=`, not `--hermit-bin`.
- **Artifact:** `ai_docs/release-0.3-fbsource-parity.md`

---

## 9. Release-blocking preconditions (`AC-PRE-*`)

These are not release *quality* criteria; they are the conditions under which
any of the above can be measured at all.

### AC-PRE-01 — Parent `main` is green

- **Source of truth:** the last completed run of each dev-hermit workflow on
  `main`.
- **Denominator:** every dev-hermit workflow that runs on `main`, enumerated
  from `.github/workflows` at the re-read tip — not a hardcoded list.
- **Threshold:** all green at a re-read tip.
- **Forbidden state:** counting a *pending* tip as green (or as red). Measured:
  three consecutive `Dev-hermit operational tooling` main runs failed; the
  known failure `31142757501` was superseded by `2a911cff`; tip
  `c17f82ff` was pending at capture. Parent main is **not certified green**.
- **Positive control:** the enumeration is nonempty *and* each named workflow
  has a completed run at the re-read tip. A workflow with no completed run
  yields `NOT-RUN`, never green.
- **Negative control:** the same checker run against tip `f5815130` reports red
  — its `ci-hub bounded operations` shard failed when the release-worktree
  durability guard invoked `with-proxy` on a runner that lacked it. This proves
  the checker reads run conclusions rather than defaulting to green.
- **Artifact:** `ai_docs/release-0.3-preconditions.md`

### AC-PRE-02 — The RC head carries an authoritative check

- **Source of truth:** the authoritative check set per repo.
- **Denominator:** open PRs at capture, and the RC head itself.
- **Threshold:** the RC head has a *completed* run of every check in its
  repository's authoritative set, and every one is success.
- **Positive control:** the authoritative check names are enumerated from the
  repository's own merge-gate/branch-protection configuration rather than
  hardcoded, and the enumeration is nonempty. An empty enumeration is a checker
  failure, not a pass.
- **Forbidden state:** an RC head with **no authoritative check created at
  all**. This is the load-bearing operational finding: **93 of 107 open PRs
  (87%) had no authoritative result**, and **6 hermit PRs were ready-for-review
  with none while 0 ready PRs had one** — nothing in hermit was landable on
  evidence. A red count is not the problem; the absence of results is.
- **Negative control:** distinguishing gate red from product red must be
  preserved — 33 of 94 hermit PRs carried a failing *gate* check while only 2
  (#1766, #1767) carried a failing *product* check. Reporting "33 red" would be
  wrong by 16×.
- **Artifact:** `ai_docs/release-0.3-preconditions.md`

### AC-PRE-03 — Parent gitlinks match the RC pair

- **Source of truth:** `git ls-tree origin/main` in the parent.
- **Denominator:** the parent gitlink set at `origin/main` — `hermit`,
  `reverie`, `liteinst2`, `agent-utils` (4 gitlinks).
- **Threshold:** Hermit and LiteInst2 gitlinks equal the RC commits.
- **Forbidden state:** pinning an ancestor. Measured at snapshot: the parent
  pinned Hermit `b4e94ce4` (**69 commits behind** Hermit main) and LiteInst2
  `8bffae9d` (2 behind), while Reverie was exact.
- **Positive control:** `git ls-tree origin/main <path>` returns exactly the RC
  commit for Hermit and Reverie, and every remaining submodule's recorded
  commit resolves to a real object.
- **Negative control:** the same check evaluated against the 2026-08-07
  snapshot reports FAIL on the two stale gitlinks above, proving it compares
  rather than assumes.
- **Artifact:** `ai_docs/release-0.3-preconditions.md`

---

## 10. Decision procedure

The release is `GO` iff **every** criterion is `PASS` at one RC pair, with its
artifact committed and reachable from parent `main`.

| State | Meaning | Blocks the cut |
| --- | --- | --- |
| `PASS` | Threshold met, both controls fired, artifact committed | no |
| `FAIL` | Threshold not met, or a forbidden state observed | yes |
| `NOT-RUN` | No artifact at the RC pair | yes |
| `N/A` | Excluded by an AC-PKG-00 / AC-FBS-00 decision record | no |

`N/A` is reachable **only** through a committed decision record. No criterion
may be marked `N/A` by an implementer's judgement at evaluation time.

### Known state at authoring time

Nothing below is a gate result; it is the starting position, recorded so the
first evaluation is not mistaken for a regression.

| Criterion | Position | Basis |
| --- | --- | --- |
| AC-STRICT-01 | expected FAIL | zero published bitwise cells |
| AC-STRICT-03 | FAIL | `NOT-VALIDATED`, `qualifying_count 0` at `1fadc037` |
| AC-RUNNERUP-01 | NOT-RUN | baseline is stripped-comparator only |
| AC-UX-01 | FAIL | `make` enables third-party backends and can `sudo` install packages |
| AC-UX-03 | FAIL | KVM hung and orphaned a child |
| AC-PKG-00 | open | owner decision |
| AC-PKG-02 | FAIL | 1 of 5 crates packages; flagship name is not ours |
| AC-SYS-01 | PASS at `1fadc037` | census test green; mutation control fired |
| AC-SYS-02 | FAIL | 12 live rows absent from the pin |
| AC-SYS-03 | FAIL | wildcard reaches unsupported/passthrough |
| AC-SEMVER-01 | not started | all crates at 0.2.0, correctly |
| AC-UPSTREAM-01 | not started | upstream #88 still open |
| AC-FBS-00 | open | both `stable` branches stale |
| AC-PRE-01 | FAIL | parent main not certified green |
| AC-PRE-02 | FAIL | RC-grade authoritative coverage absent |
| AC-PRE-03 | FAIL | parent Hermit gitlink 69 commits behind |

---

## Provenance

Every measured number in this document comes from the eight completed
`release-0.3-audit-*` and `release-0.3-operational-health-snapshot` tasks, and
is reproduced here **with the caveats those audits attached**. The baselines
bind to Hermit `1fadc03779f2a246a9b5af5d4a93533511c837df` and Reverie
`dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` unless stated otherwise; all three
mains have since advanced. Per RRC-1, no number here may be used as a gate
result — each must be re-measured at the RC pair.

This document defines gates. It runs none of them and claims no passes.
