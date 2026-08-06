# validate.sh → product duplication audit (re-run @ hermit main `b64d893a`)

**Task:** `validate-sh-duplicates-product-functionality` (P0)
**Date:** 2026-08-05
**Bound to:** `rrnewton/hermit` main `b64d893ae9ea6404472eae9cb86102d91ec642ef`, `validate.sh` 4854 lines, primary checkout clean on `main`.
**Method:** local read of `validate.sh` + `hermit-cli/src/bin/hermit/{verify,run,record_start}.rs` + `AGENTS.md`. No egress (box-wide proxy 403), no fetch, no build, no mutation. Every line number below is at `b64d893a` — prior notes on this task cite line numbers from at least five earlier SHAs and have all drifted.

> **Why re-run:** every previous audit pass (2026-08-03 15:00 → 2026-08-05 06:24) predates the landing of the strict-verify verdict channel. Those notes report `--verify-strict`/`--verify-json`/`bitwise_parity` counts of **0/0/0** in `validate.sh`. At `b64d893a` they are **2/2/20**. The original three duplications are resolved; the live defect has moved.

---

## Denominator

**8 duplications found**, across three classes:

| Class | What | Count | Live at `b64d893a` | Disposition |
|---|---|---:|---:|---|
| A | Host-side reimplementation of the determinism comparison (the original P0 shape) | 3 | **0** | DELETE-THE-BASH — **all 3 done** |
| B | Wrong-contract delegation: calls the product, but bash decides the verdict/label under a weaker comparison than the label claims | 4 | **4** | DELETE-THE-BASH |
| C | Genuine product gap: expectation encoded in bash that no product code asserts | 1 | **1** | FIX-THE-PRODUCT |

**7 DELETE-THE-BASH · 1 FIX-THE-PRODUCT · 5 still live.**

No item in this audit is blocked on a missing product capability except **C1**. For every Class-A and Class-B item the shipped CLI already does strictly more than the bash — there was never a `--verify` gap that forced the workaround.

---

## The product contract (what bash should be deferring to)

`ComparisonSpec` in `hermit-cli/src/bin/hermit/verify.rs` is the single typed verdict authority:

- `LogCompareStrictness::Stripped` (verify.rs:83, the **default** for a bare `--verify`) — "normalizes away numeric values, addresses, tmp paths" (verify.rs:57-60). Asserts only *matched after normalizing known-variable content*.
- `LogCompareStrictness::Canonical` (verify.rs:90, selected by `--verify-strict`) — the parity mode.
- `ComparisonSpec::is_bitwise_parity()` (verify.rs:260-271) is the **single acceptance rule**: `compare_logs && full_trace && !strip_lines && canonicalize_addresses && exact_remainder && stripped_prefixes == PARITY_STRIPPED_PREFIXES && canonicalizations == PARITY_CANONICALIZATIONS && !ignore_lines && !skip_commit && !skip_detlog`. Its own doc comment states the rule: *"A consumer asking for parity must reject `Matched` under every weaker comparison; this predicate is that single acceptance rule."*
- Both `run` (run.rs:333/367/2779/2792) and `record start` (record_start.rs:232/218/465/478) accept `--verify-strict` and `--verify-json`.

**The two comparators genuinely disagree on real inputs** — this is not a theoretical distinction. `verify.rs:1012-1045`, test `stripped_matches_but_bitwise_diverges_on_numeric_only_log_difference`: two logs differing only in a numeric DETLOG value (stand-in for a virtual-time timestamp or raw syscall argument) → `Stripped` returns `Verdict::Matched` + `verified()==true`; `Canonical` on the identical inputs returns `Verdict::Diverged` + `verified()==false`. Guest outputs are held constant so the log comparison alone drives the flip.

**The repo's own normative definition** (`AGENTS.md:219`, :233-234):

> L2 = *Canonical full-observation repeat parity*, established by `hermit run --strict --verify --verify-strict --verify-json <path>` **requiring JSON `bitwise_parity: true`**.
> "Default `--verify` uses the lossy `Stripped` comparator and **cannot establish L2**."

---

## Class A — host-side comparison reimplementation: **RESOLVED (3/3)**

These are the three duplications the task was opened for. All are gone from `main`.

| # | Was | Now @ `b64d893a` | Owner in product |
|---|---|---|---|
| A1 | `hermit_determinism_check` — ran the guest twice, bash string-compared **stdout only** | **Removed.** Tombstone comment at validate.sh:2203-2208 records why. `hermit_verify_smoke` (2210-2214) is the product-backed check for the same echo workload. | `run --verify` |
| A2 | `hermit_record_replay_smoke` — `record start` (no `--verify`) + `replay --autopilot` + `cmp` stdout | **Migrated** (2216-2225) to `record start --verify`. | `record start --verify` |
| A3 | `rr_compatibility_probe` — the 139-row ratchet; verdict = `record==0 && replay==0 && cmp -s` stdout | **Migrated** (2765-2845) to `record start --verify --verify-strict --verify-json` (2805-2806), verdict = `rr_report_has_bitwise_parity` (2809). | `record start --verify --verify-strict` |

**A3 is the model migration and should be the template for all of Class B.** It does three things right:

1. **Consumes the typed field, not the exit code** — `rr_report_has_bitwise_parity` (2712-2720) requires `.bitwise_parity | type == "boolean"` **and** `== true`. The wrapper exit is explicitly demoted to diagnostic (comment 2709-2711, printed at 2812/2825), correctly, because a deterministic guest may itself exit nonzero.
2. **Brackets its own consumer** — `rr_report_consumer_self_test` (2726-2763) plants 7 producer-shaped fixtures: missing / `no_result` / `diverged` / **`verified:true` but `strip_lines:true`** / **`verified:true` but zero log counts** → all refused; `matched` + `bitwise_parity:true` → accepted; and `bitwise_parity:true` with `guest_exit_code:3` → **accepted** (proves the parity verdict survives a nonzero guest exit). Both directions, counted.
3. **Preserves the honest ratchet verbatim** — `RR_COMPAT_EXPECTED=139` (1072), `RR_COMPAT_PASSING_LABELS` (1146, 139 entries — count verified), canary fail-fast (2773-2776, 2819-2823), per-phase pgroup timeout `run_rr_compatibility_phase` (2677-2707).

Bash R/R orchestration is fully gone: **zero** live `replay --autopilot` sites remain (the only match, 2221, is inside A2's explanatory comment).

---

## Class B — wrong-contract delegation: **4 LIVE** (all DELETE-THE-BASH)

The disease moved one layer up. These sites *do* call the product, but bash assigns the assurance label and reads the process exit code, while the invocation selects the **lossy `Stripped`** comparator. The result is a claim the comparison does not support — the same "green measures a weaker property than the label states" hole as the original P0, now in the L2 lane instead of the R/R lane.

### B1 — `strict_compatibility_probe`: **170 rows labeled "L2" on a Stripped comparison** ← highest impact

- **Bash:** validate.sh:2902-2984. `local assurance=L2` is **hardcoded at 2915**, before any comparison runs. Invocation `run_args=(run --strict --verify --)` at **2919** (portable variant 2922, SaBRe 2929, e9patch 2941) — bare `--verify`, i.e. `Stripped`. Verdict is the probe process exit status (2967-2971 region), not a typed field.
- **Scale:** 170 `strict_compatibility_probe` call sites. Results printed as L2 at 3057 (`=== L2 compatibility: %s ===`), 3115/3116 and 3118/3119 (matrix/envelope headers), and the blocking summaries 3966/3969, 3987/3989, 3995 (`%s/%s passed L2`). `assurance="e9patch L2"` at 2931.
- **Why it is wrong:** `AGENTS.md:233-234` says in the repo's own words that this exact command "cannot establish L2." The 170-row envelope is a **blocking** gate (3995 "blocking"), so the strongest routinely-published compatibility number in the project is a Stripped result wearing an L2 label.
- **Disposition: DELETE-THE-BASH.** Add `--verify-strict --verify-json "$report"` to `run_args`; replace the exit-status verdict with `rr_report_has_bitwise_parity "$report"` (2712, already present and already bracketed). No product change needed.

### B2 — `_envelope_level`: L2/L3/L4 rows printed as "bitwise identical"

- **Bash:** `_envelope_level` (4116-4122) runs `"$HERMIT_BIN" "${HERMIT_RUN_ARGS[@]}" $flags` and returns its exit status. Called with `"--strict --verify"` for L2 at 4148, L3 at 4149 (`--detlog-heap --detlog-stack`), L4 stress ×20 at 4155, and again in `run_portable_envelope_levels` at 4264/4265/4267.
- **The claim:** line **4186** prints `L2  --strict --verify (bitwise identical)`. It is not bitwise — `strip_lines` is on. L3 (4187) and L4 (4188) inherit the same weakness, and L4 is *20 repetitions of a Stripped comparison*.
- These counters are serialized into `envelope.json` (4178-4182: `l2_pass`, `l3_pass`, `l4_pass`, `rr_pass`) and consumed downstream, carrying **no record of which comparator produced them** — a Proxy Binding failure: the value does not carry its condition.
- **Disposition: DELETE-THE-BASH.** Thread `--verify-strict --verify-json` through `_envelope_level`, gate on `bitwise_parity`, and emit the comparison spec into `envelope.json` alongside each count.

### B3 — `super_probe_command`: stress probes on the default comparator

- **Bash:** validate.sh:2544-2580. `ptrace-strict-verify` (2552-2554) and `ptrace-pipeline` (2556-2558) run `run --strict --verify` (Stripped, exit-status verdict). `ptrace-record-replay` (2562-2567) runs `record start --verify` and explicitly returns `$status` (2565/2567) — the exit-code-as-verdict conflation.
- **Disposition: DELETE-THE-BASH.** Same re-key. *(`kvm-verify` 2570 and `dbi-verify` 2574 are **not** in this count — KVM is output-only by design and correctly reports `bitwise_parity:false` per `AGENTS.md:241`. They must simply never be labeled L2.)*

### B4 — remaining `record start --verify` sites still keyed on exit status

- **Bash:** `hermit_record_replay_smoke` 2224; envelope R/R probe 4167 (feeds the `rr_pass` counter at 4169-4170); `run_privileged_envelope_record_replay` 4280.
- All three use bare `--verify` and take the process exit code as the verdict — the exact conflation A3 fixed one screen away. `record start --verify -- /bin/false` prints `Success: replay matched recording` and exits 1.
- **Disposition: DELETE-THE-BASH.** Re-key onto `--verify-strict --verify-json` + `rr_report_has_bitwise_parity`.

---

## Class C — genuine product gap: **1 LIVE** (FIX-THE-PRODUCT)

### C1 — `RR_COMPAT_KNOWN_FAILURES` is still inert; nothing asserts the known divergences

- **Bash:** `declare -Ar RR_COMPAT_KNOWN_FAILURES` at validate.sh:**1136-1142**, documenting five real toolchain divergences:
  - `g++` — "replay diverges (thread 13, ~event 132): C++ front-end header/.gch path resolution (readlink vs newfstatat) desyncs the event stream"
  - `ar` — "replay diverges (thread 11, ~event 3): archive workload teardown (execveat rm -rf) reorders against the recorded stream"
  - `strip`, `gprof`, `gcov` — "replay diverges at `replayer/mod.rs:776` after a clean record"
- **Verified inert at `b64d893a`:** the identifier appears at 1067, 1136, 1144 only — 1067 and 1144 are **comments**. **Zero dereferences.** The five rows are excluded purely by omission from `RR_COMPAT_PASSING_LABELS` (1146) and short-circuited at 2769-2772 (`RR_COMPAT_SKIPPED++`, `return 0`) — never recorded, never replayed.
- **One-sided ratchet:** a listed row that gets **fixed** is never promoted; a listed row whose **failure mode changes** (now segfaults, hangs, or fails at record instead of diverging at `mod.rs:776`) is invisible. The documented reasons are unverified by any executing code and can rot into fiction.
- **The contrast is in the same file:** sibling `COMPAT_SUMMARY_KNOWN_FAILURES` (1090) **is** dereferenced (2387, 2425, 3086) *and* has an xfail-strict unexpected-pass guard at **3080**: `WARN %s unexpectedly passed fail-closed --strict; drop it from COMPAT_SUMMARY_KNOWN_FAILURES`. The RR table has no equivalent.
- **Disposition: FIX-THE-PRODUCT.** A product-side xfail-strict R/R corpus test that partitions expected-pass from expected-divergence and **hard-fails when an expected-divergence starts passing** — converting a one-sided skip-list into a two-sided ratchet and moving the divergence knowledge next to the replayer code that owns it, versioned and typed.
- **Status:** PR #1596 (`codex/product-side-xfail-strict`, head `3e7a9ba3`) implements exactly this and is **open, not landed**. Task `product_side_xfail_strict` was closed while `origin/main` contained no `record_replay_xfail_strict` test — a phantom closure already flagged in this task's 2026-08-05 05:07 note. **Not verifiable this session (egress down).**

---

## Not duplications (checked and cleared)

- **Guest-workload comparisons.** Every `cmp` / `diff -u` / `sha256sum` / `md5sum` hit inside a `strict_compatibility_probe <label> bash -c '...'` command string (3199, 3373-3382, 3433-3451, 3518-3522, 3704, 3714, 3736, 3779-3801, 3864-3865, 2558) is **the coreutils program under test performing its own self-check**. Hermit determinism is then judged by the product verifier wrapping it. Legitimate — leave alone.
- validate.sh:515 `git diff --quiet` — dirty-tree gate, unrelated.
- 1155/1163/1164, 2302-2312 — label/category tables, not invocations.
- `kvm-verify` (2570) / `dbi-verify` (2574) — correct output-only fallback per `AGENTS.md:241`; only the *labeling* caveat applies.
- Corpus-runner orchestration (label selection, canary, tally, `run_rr_compatibility_phase` pgroup timeout, logging) — **KEEP-AS-HARNESS.** No product corpus-runner command exists; this is the honest ratchet the task explicitly says to preserve.

---

## Recommended order

1. **B1** — 170 rows, blocking gate, publishes the project's headline L2 compatibility number. Highest impact; mechanical (A3 is the template, and its consumer predicate + self-test already exist in-file).
2. **B2** — `envelope.json` is consumed downstream; emit the comparison spec with the counts so no future reader has to re-derive it.
3. **B3/B4** — small, same re-key.
4. **C1** — land PR #1596; re-open `product_side_xfail_strict` (phantom closure).

**Migration guard (applies to B1–B4):** re-keying from `Stripped`+exit-status to `bitwise_parity` is a **strict tightening**. Rows may flip green→red. Per the standing honest-ratchet rule, **every flip is a genuine product finding to file — never mask it by widening the comparison back.** The flip-set has never been measured; measuring it is part of the migration, not a precondition for it.

**Also unmeasured:** the 139-row R/R lane (A3) now runs under `--verify-strict`, but no run of all 139 rows under the strict contract is recorded in any ledger this session could reach. A3's correctness is established by source and by its bracketed self-test, not by a counted 139-row receipt.

---

## Provenance

Read-only. No fetch, no build, no branch, no commit, no PR mutation. GitHub egress was unavailable for the entire session (proxy 403 keyed to `agent_id: agent:claude_code`; `github.com` + `api.github.com` both refused), so all PR states cited (#1543, #1596) are **carried forward from prior task notes and NOT re-verified** — treat them as unverified claims. Everything bound to a `validate.sh` / `*.rs` / `AGENTS.md` line number was read directly at `b64d893a` this session.
